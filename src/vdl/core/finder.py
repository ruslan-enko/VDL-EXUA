import re
from typing import Callable, List, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import httpx
import m3u8

from vdl.core.metadata import derive_video_title
from vdl.core.models import QualityOption, VideoInfo


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
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise NetworkError(f"HTTP {resp.status_code} error loading page.")
            html_content = resp.text
    except httpx.RequestError as e:
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
    # 2a. Check main page scripts & HTML for direct m3u8
    direct_streams = _extract_m3u8_links(html_content)
    if direct_streams:
        notify("player_found", "Embedded player")
        notify("stream_found", "Stream found in page")
        return await _parse_stream_url(
            stream_url=direct_streams[0],
            page_url=url,
            page_title=page_title,
            referer=url,
            notify=notify,
        )

    # 2b. Check iframes
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
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            for ifr_url in iframes:
                try:
                    ifr_resp = await client.get(ifr_url, headers={"Referer": url})
                    if ifr_resp.status_code == 200:
                        ifr_streams = _extract_m3u8_links(ifr_resp.text)
                        if ifr_streams:
                            notify("stream_found", "HLS stream found in player")
                            return await _parse_stream_url(
                                stream_url=ifr_streams[0],
                                page_url=url,
                                page_title=page_title,
                                referer=ifr_url,
                                notify=notify,
                            )
                except Exception:
                    continue

    raise StreamNotFoundError(
        "The page does not appear to contain a supported HLS video."
    )


async def _parse_stream_url(
    stream_url: str,
    page_url: str,
    page_title: Optional[str],
    referer: Optional[str],
    notify: Callable[[str, str], None],
) -> VideoInfo:
    """Parses master m3u8 playlist, extracts qualities and calculates duration."""
    qualities: List[QualityOption] = []
    duration: Optional[float] = None

    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=12.0) as client:
            resp = await client.get(stream_url)
            if resp.status_code == 200:
                parsed = m3u8.loads(resp.text)
                
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
    )
