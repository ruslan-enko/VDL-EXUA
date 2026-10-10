from pathlib import Path
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from vdl.core.models import VideoInfo


class CompleteScreen(Screen):
    """Screen displayed upon successful download completion."""

    BINDINGS = [
        Binding("enter", "download_another", "Another", show=False),
        Binding("b", "go_back", "Back", show=False),
        Binding("backspace", "go_back", "Back", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("escape", "quit_app", "Quit", show=False),
    ]

    def __init__(self, video_info: VideoInfo, file_path: Path):
        super().__init__()
        self.video_info = video_info
        self.file_path = file_path

    def _has_series(self) -> bool:
        return bool(self.video_info.episodes)

    def _series_info(self) -> VideoInfo:
        base = self.video_info.base_title or self.video_info.title
        return VideoInfo(
            title=base,
            page_url=self.video_info.page_url,
            stream_url=self.video_info.page_url,
            qualities=[],
            referer=self.video_info.referer,
            episodes=self.video_info.episodes,
            base_title=base,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static("✓ Download complete", classes="success-title")
            
            yield Static(self.file_path.name, classes="file-info")
            
            # Show path relative to home if in user dir
            try:
                display_folder = f"~/{self.file_path.parent.relative_to(Path.home())}"
            except Exception:
                display_folder = str(self.file_path.parent)

            yield Static(display_folder, classes="folder-info")

            if self._has_series():
                yield Static(
                    "Enter New URL    B Back to episodes    q Quit",
                    classes="footer-nav",
                )
            else:
                yield Static(
                    "Press Enter to download another\nq        Quit",
                    classes="footer-nav",
                )

    def action_download_another(self) -> None:
        self.app.show_input()

    def action_go_back(self) -> None:
        if self._has_series():
            self.app.show_episode_selection(self._series_info())
        else:
            self.app.show_input()

    def action_quit_app(self) -> None:
        self.app.exit()


class SeasonCompleteScreen(Screen):
    """Result screen for a batch season download."""

    BINDINGS = [
        Binding("enter", "download_another", "Another", show=False),
        Binding("b", "go_back", "Back", show=False),
        Binding("backspace", "go_back", "Back", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("escape", "quit_app", "Quit", show=False),
    ]

    def __init__(
        self,
        season_title: str,
        quality_label: str,
        saved: list[Path],
        failed: list[str],
        output_dir: Path,
        series_info: VideoInfo | None = None,
    ):
        super().__init__()
        self.season_title = season_title
        self.quality_label = quality_label
        self.saved = saved
        self.failed = failed
        self.output_dir = output_dir
        self.series_info = series_info

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            if self.failed and not self.saved:
                yield Static("✕ Season download failed", classes="error-title")
            else:
                yield Static("✓ Season complete", classes="success-title")

            yield Static(self.season_title, classes="file-info")
            yield Static(
                f"{len(self.saved)} saved • {self.quality_label}",
                classes="progress-stats",
            )
            try:
                display_folder = f"~/{self.output_dir.relative_to(Path.home())}"
            except Exception:
                display_folder = str(self.output_dir)
            yield Static(display_folder, classes="folder-info")

            if self.failed:
                shown = "; ".join(self.failed[:3])
                if len(self.failed) > 3:
                    shown += f" (+{len(self.failed) - 3} more)"
                yield Static(f"Failed: {shown}", classes="error-desc")

            if self.series_info is not None and self.series_info.episodes:
                yield Static(
                    "Enter New URL    B Back to episodes    q Quit",
                    classes="footer-nav",
                )
            else:
                yield Static(
                    "Press Enter to download another\nq        Quit",
                    classes="footer-nav",
                )

    def action_download_another(self) -> None:
        self.app.show_input()

    def action_go_back(self) -> None:
        if self.series_info is not None and self.series_info.episodes:
            self.app.show_episode_selection(self.series_info)
        else:
            self.app.show_input()

    def action_quit_app(self) -> None:
        self.app.exit()
