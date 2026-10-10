import asyncio
import json
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import httpx
import m3u8

from vdl.core.metadata import clean_title, derive_video_title
from vdl.core.models import AudioTrack, EpisodeOption, QualityOption, VideoInfo


class FinderError(Exception):
    """Base exception for stream finder errors."""
    pass


class StreamNotFoundError(FinderError):
    """Raised when no supported video stream is found."""
    pass


class NetworkError(FinderError):
    """Raised when a network or HTTP error occurs."""
    pass


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
}

M3U8_REGEX = re.compile(
    r"""(?xi)
    (?:file|source|src|url|hls|playlist)\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']
    |
    ["'](https?://[^"']+\.m3u8[^"']*)["']
    |
    (https?://[^\s"'<>]+\.m3u8[^\s"'<>]*)
    """
)


def _is_cloudflare_challenge(text: str) -> bool:
    """Detects Cloudflare bot-challenge pages (uakino.best uses them)."""
    if not text:
        return False
    snippet = text[:4000]
    return ("Just a moment..." in snippet) and (
        "challenges.cloudflare.com" in snippet or "cf-challenge" in snippet
    )


async def _curl_fetch_text(
    url: str,
    referer: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """Fetches a URL via system curl (TLS fingerprint passes Cloudflare).

    Used as a fallback when httpx gets a Cloudflare challenge page.
    macOS curl uses SecureTransport which uakino.best does not challenge.
    """
    curl_bin = shutil.which("curl")
    if not curl_bin:
        raise NetworkError("curl binary not found for Cloudflare fallback.")
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    cmd = [curl_bin, "-sL", "--max-time", "25", "--compressed"]
    cmd += ["-A", headers.get("User-Agent", "")]
    for key in ("Accept", "Accept-Language", "Referer", "X-Requested-With"):
        if key in headers:
            cmd += ["-H", f"{key}: {headers[key]}"]
    cmd.append(url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as e:
        raise NetworkError(f"Network error (curl fallback): {e}") from e
    if proc.returncode != 0:
        raise NetworkError("Network error (curl fallback failed).")
    try:
        return out.decode("utf-8", errors="replace")
    except Exception as e:
        raise NetworkError(f"Network error (decode): {e}") from e


async def _fetch_text(
    url: str,
    referer: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> str:
    """Fetches page text, falling back to curl on Cloudflare challenges."""
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400 and resp.status_code != 403:
                raise NetworkError(f"HTTP {resp.status_code} error loading page.")
            text = resp.text
            if resp.status_code != 403 or not _is_cloudflare_challenge(text):
                if resp.status_code >= 400:
                    raise NetworkError(f"HTTP {resp.status_code} error loading page.")
                return text
            # 403 + CF challenge → fall through to curl
    except httpx.RequestError as e:
        # Connection-level failure — try curl before giving up only when
        # curl exists; otherwise report the original error.
        if not shutil.which("curl"):
            raise NetworkError(f"Network error: {e}") from e
    # Fallback: system curl (different TLS fingerprint, passes Cloudflare).
    text = await _curl_fetch_text(url, referer=referer, extra_headers=extra_headers)
    if _is_cloudflare_challenge(text):
        raise NetworkError("HTTP 403 error loading page.")
    return text


def _extract_m3u8_links(text: str) -> List[str]:
    """Finds all .m3u8 URLs within text or HTML/JS."""
    links = []
    seen = set()
    for match in M3U8_REGEX.finditer(text):
        for group in match.groups():
            if group and group not in seen:
                # clean backslashes if escaped in JSON (\/)
                clean_url = group.replace("\\/", "/").strip()
                if clean_url.startswith("http://") or clean_url.startswith("https://"):
                    links.append(clean_url)
                    seen.add(clean_url)
    return links


# ── Playerjs serial playlist (seasons / dubs / episodes) ──────────────

# Matches: file: '[{...}]'  or  file:"[{...}]"  (single-quoted JSON is the norm)
_PLAYERJS_FILE_RE = re.compile(
    r"""file\s*:\s*'(?P<single>(?:\\.|[^'\\])*)'"""
    r"""|file\s*:\s*"(?P<double>(?:\\.|[^"\\])*)\"""",
    re.DOTALL,
)

_SEASON_NUM_RE = re.compile(r"(?i)(?:сезон|season|\bs)\s*0*(\d{1,3})")
_EPISODE_NUM_RE = re.compile(r"(?i)(?:сері[яїюй]|сери[яию]|episode|ep|\be)\s*0*(\d{1,4})")


def _parse_num(text: Optional[str], pattern: re.Pattern) -> Optional[int]:
    if not text:
        return None
    m = pattern.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    # Fallback: leading number like "1 сезон" / "2 серія" is already covered,
    # but also catch bare "12" titles.
    m2 = re.search(r"(\d{1,4})", text)
    if m2:
        try:
            return int(m2.group(1))
        except ValueError:
            return None
    return None


def _flatten_playlist_nodes(
    nodes: List[Dict[str, Any]],
    trail: List[str],
) -> List[EpisodeOption]:
    """Recursively flattens Playerjs file/folder JSON into episode list."""
    episodes: List[EpisodeOption] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title = str(node.get("title", "") or "").strip()
        file_val = node.get("file")
        folder = node.get("folder")

        if isinstance(folder, list) and folder:
            next_trail = trail + [title] if title else trail
            episodes.extend(_flatten_playlist_nodes(folder, next_trail))
        elif isinstance(file_val, str) and file_val:
            cleaned = file_val.replace("\\/", "/").strip()
            # Playerjs can pack several qualities comma-separated, optionally
            # prefixed like "[480p]https://...". Pick the first .m3u8 part.
            candidates = [c.strip().strip("'\"") for c in cleaned.split(",")]
            m3u8_url = None
            for c in candidates:
                # Strip "[720p]"-style quality prefix if present.
                no_tag = re.sub(r"^\[[^\]]*\]", "", c).strip()
                if no_tag.startswith("//"):
                    no_tag = "https:" + no_tag
                if ".m3u8" in no_tag and no_tag.startswith("http"):
                    m3u8_url = no_tag
                    break
            if not m3u8_url:
                # Single mp4 / non-HLS file — skip (finder is HLS-only).
                continue
            full_path = trail + ([title] if title else [])
            if len(full_path) >= 3:
                season, voice = full_path[0], " / ".join(full_path[1:-1])
                episode = full_path[-1]
            elif len(full_path) == 2:
                season, voice, episode = full_path[0], None, full_path[1]
            elif len(full_path) == 1:
                season, voice, episode = None, None, full_path[0]
            else:
                season, voice, episode = None, None, "Episode"

            label_parts = [p for p in [season, voice, episode] if p]
            label = " / ".join(label_parts) if label_parts else "Episode"
            episodes.append(
                EpisodeOption(
                    label=label,
                    file=m3u8_url,
                    season=season,
                    voice=voice,
                    episode=episode,
                    season_num=_parse_num(season, _SEASON_NUM_RE),
                    episode_num=_parse_num(episode, _EPISODE_NUM_RE),
                    poster=node.get("poster"),
                    episode_id=str(node.get("id")) if node.get("id") is not None else None,
                )
            )
        # else: folder empty and no file — skip
    return episodes


def parse_playerjs_playlist(text: str) -> List[EpisodeOption]:
    """Extracts season/dub/episode list from Playerjs embed HTML/JS.

    Looks for `file: '[{...}]'` blocks, JSON-decodes them and flattens
    nested folder structure into a flat episode list. Returns [] when
    no serial playlist is present.
    """
    episodes: List[EpisodeOption] = []
    seen_files = set()
    for match in _PLAYERJS_FILE_RE.finditer(text):
        raw = match.group("single") if match.group("single") is not None else match.group("double")
        if not raw:
            continue
        raw = raw.strip()
        if not raw.startswith("["):
            continue
        # Unescape JS-escaped single quotes inside single-quoted string.
        raw = raw.replace("\\'", "'")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        for ep in _flatten_playlist_nodes(data, []):
            if ep.file not in seen_files:
                episodes.append(ep)
                seen_files.add(ep.file)
    return episodes


def _extract_js_array(text: str, marker: str) -> Optional[str]:
    """Extracts a balanced `[...]` block following a JS marker like `seasons:`."""
    idx = text.find(marker)
    if idx < 0:
        return None
    start = text.find("[", idx + len(marker))
    if start < 0:
        return None
    depth = 0
    in_str: Optional[str] = None
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def parse_venom_playlist(text: str) -> List[EpisodeOption]:
    """Extracts season/episode/dub list from a Venom player embed page.

    Venom (api.ortified.ws and mirrors) embeds
    `playlist: { seasons: [{ season: N, episodes: [{ episode, hls,
    audio: { names: [...] }, title, id, poster }] }] }`.
    One entry is produced per (episode × dub) so the dub can be chosen
    together with the episode; entries sharing a file differ by voice.
    Returns [] when no Venom playlist is present.
    """
    raw = _extract_js_array(text, "seasons:")
    if not raw:
        return []
    try:
        seasons = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(seasons, list):
        return []

    episodes: List[EpisodeOption] = []
    for s in seasons:
        if not isinstance(s, dict):
            continue
        try:
            season_num = int(s.get("season"))
        except (TypeError, ValueError):
            continue
        season_name = f"{season_num} сезон"
        for e in s.get("episodes") or []:
            if not isinstance(e, dict):
                continue
            hls = str(e.get("hls") or "").replace("\\/", "/").strip()
            if ".m3u8" not in hls or not hls.startswith("http"):
                continue
            try:
                ep_num = int(str(e.get("episode")))
            except (TypeError, ValueError):
                continue
            ep_name = f"{ep_num} серія"
            audio = e.get("audio") or {}
            names = audio.get("names") or []
            if not isinstance(names, list) or not names:
                names = [None]
            for name in names:
                voice = str(name).strip() if name else None
                parts = [season_name] + ([voice] if voice else []) + [ep_name]
                episodes.append(
                    EpisodeOption(
                        label=" / ".join(parts),
                        file=hls,
                        season=season_name,
                        voice=voice,
                        episode=ep_name,
                        season_num=season_num,
                        episode_num=ep_num,
                        poster=e.get("poster"),
                        episode_id=str(e.get("id")) if e.get("id") is not None else None,
                    )
                )

    episodes.sort(key=lambda x: (x.season_num or 0, x.episode_num or 0, x.voice or ""))
    deduped: List[EpisodeOption] = []
    seen = set()
    for ep in episodes:
        key = (ep.file, ep.voice)
        if key not in seen:
            deduped.append(ep)
            seen.add(key)
    return deduped


# ── DLE playlists (uakino.best style) ─────────────────────────────────
# Main page contains <div class="playlists-ajax" data-xfname="playlist"
# data-news_id="9548"> with no iframe. The actual episode list is loaded via
# /engine/ajax/playlists.php?news_id=..&xfield=.. which returns JSON
# {success, response: "<div class=playlists-player>..."} where each
# <li data-file="//ashdi.vip/vod/.." data-voice="ТЕТ">Серія 1</li>
# points at an intermediate player page holding the real .m3u8.

_DLE_AJAX_RE = re.compile(
    r"""class=["'][^"']*playlists-ajax[^"']*["'][^>]*?"""
    r"""data-xfname=["'](?P<xfield>[^"']+)["'][^>]*?"""
    r"""data-news_id=["'](?P<news_id>\d+)["']"""
    r"""|class=["'][^"']*playlists-ajax[^"']*["'][^>]*?"""
    r"""data-news_id=["'](?P<news_id2>\d+)["'][^>]*?"""
    r"""data-xfname=["'](?P<xfield2>[^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)

_DLE_SEASON_URL_RE = re.compile(r"(?i)(\d{1,3})\s*[-_]\s*sezon\b")
_DLE_SEASON_TITLE_RE = re.compile(r"(?i)(\d{1,3})\s*сезон")


def _extract_dle_ajax_params(html: str) -> List[Tuple[str, str]]:
    """Finds (news_id, xfield) pairs from playlists-ajax placeholders."""
    params: List[Tuple[str, str]] = []
    seen = set()
    for m in _DLE_AJAX_RE.finditer(html):
        news_id = m.group("news_id") or m.group("news_id2")
        xfield = m.group("xfield") or m.group("xfield2")
        if news_id and xfield and (news_id, xfield) not in seen:
            params.append((news_id, xfield))
            seen.add((news_id, xfield))
    return params


def _infer_dle_season(
    page_title: Optional[str], page_url: Optional[str]
) -> Tuple[Optional[str], Optional[int]]:
    """Infers ('N сезон', N) from page title/URL; each uakino page = 1 season."""
    for text in (page_title or "", page_url or ""):
        m = _DLE_SEASON_TITLE_RE.search(text)
        if m:
            try:
                num = int(m.group(1))
                return f"{num} сезон", num
            except ValueError:
                pass
        m2 = _DLE_SEASON_URL_RE.search(text)
        if m2:
            try:
                num = int(m2.group(1))
                return f"{num} сезон", num
            except ValueError:
                pass
    # Generic season patterns (e.g. season-01, /s1/) as last resort.
    for text in (page_url or "", page_title or ""):
        m = _SEASON_NUM_RE.search(text)
        if m:
            try:
                num = int(m.group(1))
                return f"{num} сезон", num
            except ValueError:
                pass
    return None, None


def parse_dle_playlist_html(
    playlist_html: str,
    season: Optional[str] = None,
    season_num: Optional[int] = None,
) -> List[EpisodeOption]:
    """Parses playlists.php response HTML into episode options.

    `file` holds the intermediate player URL (e.g. //ashdi.vip/vod/..),
    resolved to a direct .m3u8 lazily on episode selection.
    """
    soup = BeautifulSoup(playlist_html, "html.parser")
    items = soup.select(".playlists-videos li[data-file]")
    episodes: List[EpisodeOption] = []
    seen = set()
    voice_order: Dict[str, int] = {}
    for li in items:
        raw_file = (li.get("data-file") or "").strip()
        if not raw_file:
            continue
        if raw_file.startswith("//"):
            raw_file = "https:" + raw_file
        if not raw_file.startswith(("http://", "https://")):
            continue
        voice = (li.get("data-voice") or "").strip() or None
        ep_name = li.get_text(strip=True) or "Серія"
        ep_num = _parse_num(ep_name, _EPISODE_NUM_RE)
        parts = [p for p in [season, voice, ep_name] if p]
        label = " / ".join(parts) if parts else ep_name
        key = (raw_file, voice)
        if key in seen:
            continue
        seen.add(key)
        if voice and voice not in voice_order:
            voice_order[voice] = len(voice_order)
        episodes.append(
            EpisodeOption(
                label=label,
                file=raw_file,
                season=season,
                voice=voice,
                episode=ep_name,
                season_num=season_num,
                episode_num=ep_num,
                episode_id=str(li.get("data-id")) if li.get("data-id") else None,
            )
        )
    # Keep dub groups in page order, episodes numeric within each dub.
    episodes.sort(
        key=lambda x: (voice_order.get(x.voice or "", 0), x.episode_num or 0)
    )
    return episodes


async def _fetch_dle_episodes(
    page_url: str,
    html_content: str,
    page_title: Optional[str],
) -> List[EpisodeOption]:
    """Loads DLE playlists.php episode lists for a page. Returns [] if none."""
    params = _extract_dle_ajax_params(html_content)
    if not params:
        return []
    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else page_url
    season, season_num = _infer_dle_season(page_title, page_url)
    all_episodes: List[EpisodeOption] = []
    for news_id, xfield in params:
        ajax_url = f"{base}/engine/ajax/playlists.php?news_id={news_id}&xfield={xfield}"
        try:
            raw = await _fetch_text(
                ajax_url,
                referer=page_url,
                extra_headers={"X-Requested-With": "XMLHttpRequest"},
            )
        except FinderError:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        response_html = data.get("response") if isinstance(data, dict) else None
        if not data.get("success") or not response_html:
            continue
        all_episodes.extend(
            parse_dle_playlist_html(response_html, season=season, season_num=season_num)
        )
    # Dedup across multiple ajax blocks, preserve order.
    deduped: List[EpisodeOption] = []
    seen = set()
    for ep in all_episodes:
        key = (ep.file, ep.voice)
        if key not in seen:
            deduped.append(ep)
            seen.add(key)
    return deduped


async def _resolve_intermediate_file(
    file_url: str, referer: Optional[str]
) -> Tuple[str, Optional[str]]:
    """Resolves intermediate player pages (ashdi.vip/vod/..) to direct .m3u8.

    Returns (stream_url, referer_for_stream). Passes through URLs that
    already point at .m3u8.
    """
    if ".m3u8" in file_url.lower():
        return file_url, referer
    html = await _fetch_text(file_url, referer=referer)
    links = _extract_m3u8_links(html)
    if not links:
        raise StreamNotFoundError("Player page has no supported HLS stream.")
    return links[0], file_url


def build_episode_title(base_title: str, episode: EpisodeOption) -> str:
    """Builds 'Serial — S01E02 • ТЕТ' style title for a chosen episode."""
    base = (base_title or "").strip() or "Video"
    suffix = ""
    if episode.season_num is not None and episode.episode_num is not None:
        suffix = f"S{episode.season_num:02d}E{episode.episode_num:02d}"
    elif episode.episode_num is not None:
        suffix = f"E{episode.episode_num:02d}"
    elif episode.episode:
        suffix = episode.episode
    else:
        suffix = episode.label
    title = f"{base} — {suffix}" if suffix else base
    if episode.voice and episode.season_num is not None:
        # Keep dub info when present (e.g. ТЕТ / QTV / 1+1).
        title += f" • {episode.voice}"
    return title


def season_key(episode: EpisodeOption) -> str:
    """Stable grouping key for a season (number preferred, label fallback)."""
    if episode.season_num is not None:
        return f"s{episode.season_num}"
    return f"l:{episode.season or 'Season'}"


def season_label(episodes: List[EpisodeOption]) -> str:
    """Human label for a group of same-season episodes, e.g. '1 сезон'."""
    if not episodes:
        return "Season"
    first = episodes[0]
    if first.season:
        return first.season
    if first.season_num is not None:
        return f"{first.season_num} сезон"
    return "Season"


def group_episodes_by_season(
    episodes: List[EpisodeOption],
) -> List[tuple[str, List[EpisodeOption]]]:
    """Groups flat episode list into [(season_label, episodes)] in order."""
    groups: Dict[str, List[EpisodeOption]] = {}
    order: List[str] = []
    labels: Dict[str, str] = {}
    for ep in episodes:
        key = season_key(ep)
        if key not in groups:
            groups[key] = []
            order.append(key)
            labels[key] = ep.season or (
                f"{ep.season_num} сезон" if ep.season_num is not None else "Season"
            )
    for ep in episodes:
        groups[season_key(ep)].append(ep)
    return [(labels[k], groups[k]) for k in order]


def episodes_of_same_season(
    episodes: List[EpisodeOption], reference: EpisodeOption
) -> List[EpisodeOption]:
    """Returns episodes of the same season (and dub, when set) as reference."""
    key = season_key(reference)
    ref_voice = reference.voice.strip().lower() if reference.voice else None
    result = []
    for e in episodes:
        if season_key(e) != key:
            continue
        if ref_voice is not None:
            ev = e.voice.strip().lower() if e.voice else None
            if ev != ref_voice:
                continue
        result.append(e)
    return result


async def find_video(
    url: str,
    on_step: Optional[Callable[[str, str], None]] = None,
) -> VideoInfo:
    """Finds HLS streams from a given URL with real-time step notifications.
    
    Steps reported via on_step(step_name, detail):
      - "page_found": page title / status
      - "player_found": player info / host
      - "stream_found": stream url
      - "qualities_ready": number of quality options
    """
    def notify(step: str, detail: str = ""):
        if on_step:
            on_step(step, detail)

    parsed_input = urlparse(url)
    if not parsed_input.scheme:
        url = "https://" + url

    # Case 0: Direct m3u8 link provided
    if ".m3u8" in url.lower():
        notify("page_found", "Direct stream")
        notify("player_found", "HLS Stream")
        notify("stream_found", "Found .m3u8 link")
        return await _parse_stream_url(
            stream_url=url,
            page_url=url,
            page_title=None,
            referer=None,
            notify=notify,
        )

    # Step 1: Fetch page
    page_title = None
    html_content = ""
    try:
        html_content = await _fetch_text(url)
    except FinderError:
        raise
    except Exception as e:
        raise NetworkError(f"Network error: {e}") from e

    # Extract page title
    soup = BeautifulSoup(html_content, "html.parser")
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        page_title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        page_title = soup.title.string.strip()

    notify("page_found", page_title or "Page loaded")

    # Step 2: Search for video player and streams
    # 2a. Check main page for serial playlists (seasons / episodes / dubs)
    main_playlist = parse_playerjs_playlist(html_content)
    if len(main_playlist) < 2:
        venom_main = parse_venom_playlist(html_content)
        if len(venom_main) >= 2:
            main_playlist = venom_main
    if len(main_playlist) >= 2:
        return await _build_series_info(
            page_url=url,
            page_title=page_title,
            referer=url,
            episodes=main_playlist,
            notify=notify,
        )

    # 2a2. DLE playlists (uakino.best): episodes load via playlists.php AJAX
    # into intermediate ashdi.vip player pages (Cloudflare-protected).
    try:
        dle_episodes = await _fetch_dle_episodes(url, html_content, page_title)
    except FinderError:
        dle_episodes = []
    if len(dle_episodes) >= 2:
        return await _build_series_info(
            page_url=url,
            page_title=page_title,
            referer=url,
            episodes=dle_episodes,
            notify=notify,
        )
    if len(dle_episodes) == 1:
        # Single-video DLE page (e.g. one-episode playlist): resolve the
        # intermediate player page to a direct stream like a single video.
        try:
            single_url, single_referer = await _resolve_intermediate_file(
                dle_episodes[0].file, url
            )
            notify("player_found", "Embedded player")
            notify("stream_found", "Stream found in page")
            return await _parse_stream_url(
                stream_url=single_url,
                page_url=url,
                page_title=page_title,
                referer=single_referer,
                notify=notify,
            )
        except FinderError:
            pass

    # 2b. Check main page scripts & HTML for direct m3u8
    direct_streams = _extract_m3u8_links(html_content)
    if direct_streams:
        # Single-video Playerjs embed (one file) also lands here — but if the
        # page actually had a 1-item playlist JSON, prefer episode title.
        if len(main_playlist) == 1:
            info = await _parse_stream_url(
                stream_url=main_playlist[0].file,
                page_url=url,
                page_title=page_title,
                referer=url,
                notify=notify,
            )
            info.episodes = []
            return info
        notify("player_found", "Embedded player")
        notify("stream_found", "Stream found in page")
        return await _parse_stream_url(
            stream_url=direct_streams[0],
            page_url=url,
            page_title=page_title,
            referer=url,
            notify=notify,
        )

    # 2c. Check iframes
    iframes = []
    for ifr in soup.find_all(["iframe", "embed"]):
        src = ifr.get("src") or ifr.get("data-src")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = str(httpx.URL(url).join(src))
            iframes.append(src)

    if not iframes:
        # Also check div/player containers with data-src or data-frame
        for tag in soup.find_all(attrs={"data-src": True}):
            src = tag["data-src"]
            if "http" in src or src.startswith("/"):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = str(httpx.URL(url).join(src))
                iframes.append(src)

    if iframes:
        notify("player_found", f"Player found ({len(iframes)} source{'s' if len(iframes) > 1 else ''})")
        # Try iframes sequentially
        for ifr_url in iframes:
            try:
                ifr_text = await _fetch_text(ifr_url, referer=url)
                # Serial playlists first — they contain ALL episodes,
                # while generic m3u8 regex would only grab the first.
                ifr_playlist = parse_playerjs_playlist(ifr_text)
                if len(ifr_playlist) < 2:
                    venom_playlist = parse_venom_playlist(ifr_text)
                    if len(venom_playlist) >= 2:
                        ifr_playlist = venom_playlist
                if len(ifr_playlist) >= 2:
                    return await _build_series_info(
                        page_url=url,
                        page_title=page_title,
                        referer=ifr_url,
                        episodes=ifr_playlist,
                        notify=notify,
                    )
                ifr_streams = _extract_m3u8_links(ifr_text)
                if ifr_streams:
                    if len(ifr_playlist) == 1:
                        info = await _parse_stream_url(
                            stream_url=ifr_playlist[0].file,
                            page_url=url,
                            page_title=page_title,
                            referer=ifr_url,
                            notify=notify,
                        )
                        info.episodes = []
                        return info
                    notify("stream_found", "HLS stream found in player")
                    return await _parse_stream_url(
                        stream_url=ifr_streams[0],
                        page_url=url,
                        page_title=page_title,
                        referer=ifr_url,
                        notify=notify,
                    )
            except FinderError:
                continue
            except Exception:
                continue

    raise StreamNotFoundError(
        "The page does not appear to contain a supported HLS video."
    )


async def _build_series_info(
    page_url: str,
    page_title: Optional[str],
    referer: Optional[str],
    episodes: List[EpisodeOption],
    notify: Callable[[str, str], None],
) -> VideoInfo:
    """Builds a series-level VideoInfo and pre-loads first-episode qualities."""
    base_title = derive_video_title(
        page_title=page_title,
        page_url=page_url,
        stream_url=None,
    )
    if not base_title or base_title == "Video":
        cleaned = clean_title(page_title or "")
        base_title = cleaned or base_title

    notify("player_found", f"Serial: {len(episodes)} episodes")
    notify("stream_found", f"Found {len(episodes)} episodes")

    # Parse qualities for the first episode so single-call callers
    # (and tests) still get a usable stream immediately.
    first = episodes[0]
    # DLE (uakino) episodes point at intermediate player pages — resolve
    # the first one now and cache the direct .m3u8 on the option.
    first_referer = referer
    first_file = first.file
    if ".m3u8" not in first_file.lower():
        try:
            first_file, first_referer = await _resolve_intermediate_file(
                first_file, referer
            )
            first.file = first_file
        except FinderError:
            pass
    info = await _parse_stream_url(
        stream_url=first_file,
        page_url=page_url,
        page_title=page_title,
        referer=first_referer,
        notify=notify,
    )
    info.title = base_title
    info.base_title = base_title
    info.episodes = episodes
    # Keep stream_url pointing at first episode until user picks another.
    return info


async def load_episode_video(
    base_info: VideoInfo,
    episode: EpisodeOption,
    on_step: Optional[Callable[[str, str], None]] = None,
) -> VideoInfo:
    """Resolves qualities for a chosen season/dub/episode."""
    def notify(step: str, detail: str = ""):
        if on_step:
            on_step(step, detail)

    notify("page_found", base_info.base_title or base_info.title)
    notify("player_found", episode.label)
    notify("stream_found", episode.label)
    # Intermediate player pages (uakino/ashdi) resolve to .m3u8 on demand.
    stream_url = episode.file
    episode_referer = base_info.referer
    if ".m3u8" not in stream_url.lower():
        stream_url, episode_referer = await _resolve_intermediate_file(
            stream_url, base_info.referer
        )
        episode.file = stream_url
    info = await _parse_stream_url(
        stream_url=stream_url,
        page_url=base_info.page_url,
        page_title=base_info.base_title or base_info.title,
        referer=episode_referer,
        notify=notify,
    )
    info.episodes = base_info.episodes
    info.base_title = base_info.base_title or base_info.title
    info.title = build_episode_title(info.base_title, episode)
    info.selected_audio = _match_audio(info.audio_tracks, episode.voice)
    return info


def _match_audio(
    tracks: List[AudioTrack], voice: Optional[str]
) -> Optional[AudioTrack]:
    """Picks the dub matching the chosen episode voice, else default/first."""
    if not tracks:
        return None
    if voice:
        wanted = voice.strip().lower()
        for t in tracks:
            if t.name.strip().lower() == wanted:
                return t
    for t in tracks:
        if t.is_default:
            return t
    return tracks[0]


async def _parse_stream_url(
    stream_url: str,
    page_url: str,
    page_title: Optional[str],
    referer: Optional[str],
    notify: Callable[[str, str], None],
) -> VideoInfo:
    """Parses master m3u8 playlist, extracts qualities and calculates duration."""
    qualities: List[QualityOption] = []
    audio_tracks: List[AudioTrack] = []
    duration: Optional[float] = None

    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=12.0) as client:
            resp = await client.get(stream_url)
            if resp.status_code == 200:
                parsed = m3u8.loads(resp.text)

                # Audio renditions (dub voices), e.g. Venom masters with
                # EXT-X-MEDIA TYPE=AUDIO. Failover groups repeat the same
                # names — keep the first URI per name.
                try:
                    seen_audio = set()
                    for md in parsed.media or []:
                        if str(getattr(md, "type", "") or "").upper() != "AUDIO":
                            continue
                        name = str(getattr(md, "name", "") or "").strip()
                        if not name or name in seen_audio:
                            continue
                        seen_audio.add(name)
                        uri = getattr(md, "uri", "") or ""
                        if uri and not uri.startswith(("http://", "https://")):
                            uri = str(httpx.URL(stream_url).join(uri))
                        audio_tracks.append(
                            AudioTrack(
                                name=name,
                                uri=uri or None,
                                language=getattr(md, "language", None),
                                group_id=getattr(md, "group_id", None),
                                is_default=str(getattr(md, "default", "") or "").upper() == "YES",
                            )
                        )
                except Exception:
                    pass
                
                # Check for master playlist with variant streams
                if parsed.is_variant and parsed.playlists:
                    # Sort playlists by resolution height descending
                    sorted_playlists = sorted(
                        parsed.playlists,
                        key=lambda p: (
                            p.stream_info.resolution[1] if p.stream_info.resolution else 0,
                            p.stream_info.bandwidth or 0,
                        ),
                        reverse=True,
                    )

                    seen_labels = set()
                    for p in sorted_playlists:
                        uri = p.uri
                        if not (uri.startswith("http://") or uri.startswith("https://")):
                            uri = str(httpx.URL(stream_url).join(uri))

                        h = p.stream_info.resolution[1] if p.stream_info.resolution else None
                        w = p.stream_info.resolution[0] if p.stream_info.resolution else None
                        
                        # Determine standard label
                        label = None
                        if w and w >= 1900 or (h and h >= 900) or "/1080/" in uri:
                            label = "1080p"
                        elif w and w >= 1200 or (h and h >= 600) or "/720/" in uri:
                            label = "720p"
                        elif w and w >= 800 or (h and h >= 400) or "/480/" in uri:
                            label = "480p"
                        elif w and w >= 600 or (h and h >= 300) or "/360/" in uri:
                            label = "360p"
                        elif h:
                            label = f"{h}p"
                        else:
                            label = "Auto" if not qualities else f"Quality {len(qualities)+1}"
                        
                        if label in seen_labels:
                            continue
                        seen_labels.add(label)

                        qualities.append(
                            QualityOption(
                                label=label,
                                url=uri,
                                resolution=f"{w}x{h}" if (w and h) else None,
                                width=w,
                                height=h,
                                bandwidth=p.stream_info.bandwidth,
                            )
                        )

                    # Fetch top quality playlist to get duration from segments
                    if qualities:
                        try:
                            top_resp = await client.get(qualities[0].url)
                            if top_resp.status_code == 200:
                                top_parsed = m3u8.loads(top_resp.text)
                                seg_durations = [s.duration for s in top_parsed.segments if s.duration is not None]
                                if seg_durations:
                                    duration = sum(seg_durations)
                        except Exception:
                            pass

                # If single media playlist without variants
                elif parsed.segments:
                    seg_durations = [s.duration for s in parsed.segments if s.duration is not None]
                    if seg_durations:
                        duration = sum(seg_durations)
                    qualities.append(
                        QualityOption(
                            label="Original",
                            url=stream_url,
                            bandwidth=None,
                        )
                    )

    except Exception:
        # Fallback if m3u8 fetch failed
        pass

    # Fallback if no qualities were parsed
    if not qualities:
        qualities.append(
            QualityOption(
                label="Original",
                url=stream_url,
            )
        )

    notify("qualities_ready", f"{len(qualities)} available")

    title = derive_video_title(
        page_title=page_title,
        page_url=page_url,
        stream_url=stream_url,
    )

    return VideoInfo(
        title=title,
        page_url=page_url,
        stream_url=stream_url,
        qualities=qualities,
        referer=referer,
        duration=duration,
        audio_tracks=audio_tracks,
    )
