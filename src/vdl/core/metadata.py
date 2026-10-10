import re
from typing import Optional
from urllib.parse import urlparse, unquote


FLUFF_PATTERNS = [
    r"(?i)\bдивитись\s+онлайн\b",
    r"(?i)\bдивитися\s+онлайн\b",
    r"(?i)\bсмотреть\s+онлайн\b",
    r"(?i)\bдивитись\b",
    r"(?i)\bдивитися\b",
    r"(?i)\bукраїнською\b",
    r"(?i)\bна\s+українській\b",
    r"(?i)\bмовою\b",
    r"(?i)\bв\s+озвучці\b",
    r"(?i)\bу\s+високій\s+якості\b",
    r"(?i)\bв\s+високій\s+якості\b",
    r"(?i)\bвисокій\s+якості\b",
    r"(?i)\bв\s+хорошій\s+якості\b",
    r"(?i)\bв\s+хорошем\s+качестве\b",
    r"(?i)\bбезкоштовно\b",
    r"(?i)\bбезплатно\b",
    r"(?i)\bбесплатно\b",
    r"(?i)\bвсі\s+серії\b",
    r"(?i)\bвсе\s+серии\b",
    r"(?i)\bонлайн\b",
    r"(?i)\bhd\s*1080p?\b",
    r"(?i)\b1080p\b",
    r"(?i)\b720p\b",
    r"(?i)\bHD\b",
    r"(?i)\bтрейлер\b",
]

SITE_SUFFIX_PATTERNS = [
    r"(?i)\s*[-–—|]\s*(uafix|uaflix|uaserials|hdrezka|rezka|kinogo|uakino|eneyida|gidonline|filmix|sweet\.tv|megogo).*$",
    r"(?i)\s*[-–—|]\s*дивитися.*$",
    r"(?i)\s*[-–—|]\s*смотреть.*$",
]


def extract_season_episode(text: str) -> Optional[str]:
    """Finds season and episode indicators like S01E07 or season-01-episode-07."""
    # Match S01E07 or s1e7
    m = re.search(r"(?i)\b[sS](\d{1,2})[eE](\d{1,2})\b", text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        return f"S{s:02d}E{e:02d}"

    # Match season-01-episode-07 or season/1/episode/7
    m = re.search(r"(?i)season[-_ /]?0*(\d+)[-_ /]?episode[-_ /]?0*(\d+)", text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        return f"S{s:02d}E{e:02d}"

    # Match 1 сезон 7 серія
    m = re.search(r"(?i)(\d+)\s*сезон[^\d]+(\d+)\s*сері", text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        return f"S{s:02d}E{e:02d}"

    # Match 7 серія (single episode without season)
    m = re.search(r"(?i)\b(\d+)\s*(?:серія|серію|серия|серию|ep|episode)\b", text)
    if m:
        e = int(m.group(1))
        return f"E{e:02d}"

    return None


def clean_title(title: str) -> str:
    """Cleans HTML page title or raw title string from SEO fluff and site names."""
    if not title:
        return ""

    result = title.strip()

    # Prefer quoted serial name: Мультсеріал «Назва» 1-10 сезон ... → Назва
    m = re.search(r"«([^»]{2,}?)»", result)
    if m:
        result = m.group(1).strip()
    else:
        m2 = re.search(r'"([^"]{2,}?)"', result)
        # Only use double-quoted part when outer text looks like SEO fluff.
        if m2 and re.search(r"(?i)(серіал|сериал|мульт|фільм|фильм|дивити|смотреть|сезон)", result):
            result = m2.group(1).strip()

    # Remove site suffix
    for pattern in SITE_SUFFIX_PATTERNS:
        result = re.sub(pattern, "", result)

    # Remove fluff words
    for pattern in FLUFF_PATTERNS:
        result = re.sub(pattern, "", result)

    # Remove leading media-type words: Серіал / Мультсеріал / Фільм ...
    # (both Ukrainian and Russian spellings).
    result = re.sub(
        r"(?i)^\s*(мультсеріал|мультсериал|мультфільм|мультфильм|серіал|сериал|фільм|фильм|мульт|аніме|аниме|кіно|кино)\s+",
        "",
        result,
    )
    # Remove season mentions: "1-10 сезон", "1, 2, 3 сезон", "1 сезон"
    result = re.sub(r"(?i)\b\d+\s*[-–—]\s*\d+\s*сезон\w*", "", result)
    result = re.sub(r"(?i)\b\d+(?:\s*,\s*\d+)+\s*сезон\w*", "", result)
    result = re.sub(r"(?i)\b\d+\s*сезон\w*", "", result)

    # Clean up punctuation and whitespace
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"\s*[-–—]\s*$", "", result)
    result = re.sub(r"^\s*[-–—]\s*", "", result)
    # Drop dangling prepositions left after fluff removal ("... в HD" → "... в").
    result = re.sub(r"(?i)\s+\b[вузіз]\b\s*$", "", result)
    result = result.strip(" «»\"'").strip()
    return result.strip()


def derive_video_title(
    page_title: Optional[str] = None,
    page_url: Optional[str] = None,
    stream_url: Optional[str] = None,
) -> str:
    """Derives a clean, human-friendly title for the video.
    
    Example:
        Lanterns — S01E07
    """
    se_code = None

    # Check for SxxExx in stream URL (e.g. lanterns.s01e07.ua.mvo_67431)
    stream_basename = ""
    if stream_url:
        parsed_stream = urlparse(stream_url)
        path = unquote(parsed_stream.path)
        parts = [p for p in path.split("/") if p and not p.endswith(".m3u8") and p not in ("hls", "vid", "new1")]
        if parts:
            stream_basename = parts[-1]
            se_code = extract_season_episode(stream_basename)

    # Check in page URL if not found in stream
    if not se_code and page_url:
        se_code = extract_season_episode(page_url)

    # Check in page title
    if not se_code and page_title:
        se_code = extract_season_episode(page_title)

    # Now get base title
    base_title = ""

    # Check stream basename first for clean latin show name
    if stream_basename:
        # Skip opaque CDN tokens (ashdi style: DK2XiHKMjuddnhH+BI8=) —
        # they carry no title info; fall through to page title instead.
        is_token = (
            "+" in stream_basename
            or "=" in stream_basename
            or re.fullmatch(r"[A-Za-z0-9]{8,}", stream_basename) is not None
        )
        if not is_token:
            # e.g. lanterns.s01e07.ua.mvo_67431 -> Lanterns
            clean_base = re.sub(r"(?i)[._-][sS]\d+[eE]\d+.*", "", stream_basename)
            clean_base = re.sub(r"[_.-]+", " ", clean_base).strip()
            if clean_base and len(clean_base) > 2:
                base_title = clean_base.capitalize()

    # If base_title not good, try cleaned page title
    if not base_title and page_title:
        cleaned = clean_title(page_title)
        # If se_code is already detected, remove episode mentions from base
        if se_code:
            cleaned = re.sub(r"(?i)\b\d+\s*(?:сезон|сері[яї]|сери[яи])\b", "", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
        if cleaned:
            base_title = cleaned

    # Fallback to URL path slug
    if not base_title and page_url:
        parsed = urlparse(page_url)
        slug_parts = [p for p in parsed.path.strip("/").split("/") if p]
        for part in slug_parts:
            if not part.startswith("season-") and not part.startswith("episode-") and part not in ("serials", "movies", "film"):
                base_title = part.replace("-", " ").replace("_", " ").title()
                break

    if not base_title:
        base_title = "Video"

    if se_code:
        return f"{base_title} — {se_code}"
    return base_title


def sanitize_filename(name: str) -> str:
    """Removes invalid filesystem characters and ensures safe .mp4 filename."""
    # Replace illegal filename characters: / \ : * ? " < > |
    sanitized = re.sub(r'[\\/*?:"<>|]', "-", name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .-_")
    if not sanitized:
        sanitized = "video"
    if not sanitized.lower().endswith(".mp4"):
        sanitized += ".mp4"
    return sanitized
