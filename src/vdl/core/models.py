from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class QualityOption:
    """Represents an available stream quality level."""
    label: str               # e.g. "1080p", "720p", "480p", "360p", "Original"
    url: str                 # Stream URL (.m3u8)
    resolution: Optional[str] = None  # e.g. "1920x1080"
    width: Optional[int] = None
    height: Optional[int] = None
    bandwidth: Optional[int] = None   # in bits per second


@dataclass
class AudioTrack:
    """Single audio rendition inside an HLS master playlist (dub voice)."""
    name: str                  # e.g. "Рус. Дублированный", "Eng.Original"
    uri: Optional[str] = None  # rendition playlist URL (.m3u8), absolute
    language: Optional[str] = None
    group_id: Optional[str] = None
    is_default: bool = False


@dataclass
class EpisodeOption:
    """Single episode/voice variant inside a Playerjs serial playlist."""
    label: str               # e.g. "1 сезон / ТЕТ / 2 серія"
    file: str                # Stream URL (.m3u8)
    season: Optional[str] = None   # e.g. "1 сезон"
    voice: Optional[str] = None    # e.g. "ТЕТ"
    episode: Optional[str] = None  # e.g. "2 серія"
    season_num: Optional[int] = None
    episode_num: Optional[int] = None
    poster: Optional[str] = None
    episode_id: Optional[str] = None


@dataclass
class VideoInfo:
    """Represents discovered video stream and its metadata."""
    title: str
    page_url: str
    stream_url: str
    qualities: List[QualityOption] = field(default_factory=list)
    referer: Optional[str] = None
    duration: Optional[float] = None  # in seconds
    episodes: List[EpisodeOption] = field(default_factory=list)
    base_title: Optional[str] = None  # clean serial title without SxxExx
    audio_tracks: List[AudioTrack] = field(default_factory=list)
    selected_audio: Optional[AudioTrack] = None  # chosen dub for download


@dataclass
class DownloadProgress:
    """Real-time progress of ffmpeg download."""
    percent: float = 0.0              # 0.0 to 100.0
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    speed_text: str = "-- MB/s"
    eta_text: str = "--:--"
    out_time_seconds: float = 0.0
    is_complete: bool = False
    error: Optional[str] = None
    attempt: int = 1                  # 1-based ffmpeg attempt counter
    max_attempts: int = 1
    status_text: Optional[str] = None  # e.g. "Reconnecting…" while retrying
