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
class VideoInfo:
    """Represents discovered video stream and its metadata."""
    title: str
    page_url: str
    stream_url: str
    qualities: List[QualityOption] = field(default_factory=list)
    referer: Optional[str] = None
    duration: Optional[float] = None  # in seconds


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
