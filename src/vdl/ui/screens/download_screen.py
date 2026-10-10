import asyncio
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from vdl.core.downloader import Downloader, format_bytes
from vdl.core.models import DownloadProgress, QualityOption, VideoInfo


def render_progress_bar(percent: float, width: int = 32) -> str:
    """Renders a minimalist terminal progress bar with blocks."""
    pct = max(0.0, min(100.0, percent))
    filled = int(round((pct / 100.0) * width))
    unfilled = width - filled
    bar_str = "█" * filled + "░" * unfilled
    return f"{bar_str}  {int(pct)}%"


class DownloadScreen(Screen):
    """Real-time download progress screen."""

    BINDINGS = [
        Binding("escape", "cancel_download", "Cancel", show=False),
        Binding("ctrl+c", "cancel_download", "Cancel", show=False),
    ]

    def __init__(self, video_info: VideoInfo, quality: QualityOption):
        super().__init__()
        self.video_info = video_info
        self.quality = quality
        self.downloader: Downloader | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static("Downloading", classes="section-title")
            yield Static(self.video_info.title, classes="video-heading")

            with Vertical(classes="progress-container"):
                yield Static(
                    render_progress_bar(0.0),
                    id="progress-bar",
                    classes="progress-bar-text",
                )
                yield Static("0 B", id="stat-size", classes="progress-stats")
                yield Static("-- MB/s", id="stat-speed", classes="progress-stats")
                yield Static("--:-- remaining", id="stat-eta", classes="progress-stats")

            yield Static("", id="stat-status", classes="progress-status")

            yield Static("Esc cancel", classes="footer-nav")

    def on_mount(self) -> None:
        self.downloader = Downloader(
            video_info=self.video_info,
            quality=self.quality,
            on_progress=self._on_progress_update,
        )
        self.run_worker(self._run_download())

    def _on_progress_update(self, progress: DownloadProgress) -> None:
        self.app.call_safe(self._update_ui, progress)

    def _update_ui(self, progress: DownloadProgress) -> None:
        try:
            bar_widget = self.query_one("#progress-bar", Static)
            bar_widget.update(render_progress_bar(progress.percent))

            # Size text
            size_widget = self.query_one("#stat-size", Static)
            if progress.total_bytes and progress.total_bytes > progress.downloaded_bytes:
                size_str = f"{format_bytes(progress.downloaded_bytes)} / {format_bytes(progress.total_bytes)}"
            else:
                size_str = format_bytes(progress.downloaded_bytes)
            size_widget.update(size_str)

            # Speed text
            speed_widget = self.query_one("#stat-speed", Static)
            speed_widget.update(progress.speed_text)

            # ETA text
            eta_widget = self.query_one("#stat-eta", Static)
            eta_widget.update(f"{progress.eta_text} remaining")

            # Retry / stall status
            status_widget = self.query_one("#stat-status", Static)
            status_widget.update(progress.status_text or "")
        except Exception:
            pass

    async def _run_download(self) -> None:
        try:
            assert self.downloader is not None
            final_path = await self.downloader.start()
            self.app.call_safe(
                self.app.show_complete,
                self.video_info,
                final_path,
            )
        except asyncio.CancelledError:
            self.app.call_safe(self.app.show_input)
        except Exception as e:
            self.app.call_safe(
                self.app.show_error,
                "✕ Download error",
                str(e),
                self.video_info.page_url,
            )

    def action_cancel_download(self) -> None:
        if self.downloader:
            self.downloader.cancel()
        self.app.show_input()
