from pathlib import Path
import threading
from typing import Any, Callable, Optional
from textual.app import App

from vdl.core.models import EpisodeOption, QualityOption, VideoInfo
from vdl.ui.screens.complete_screen import CompleteScreen, SeasonCompleteScreen
from vdl.ui.screens.download_screen import DownloadScreen
from vdl.ui.screens.episode_screen import EpisodeScreen
from vdl.ui.screens.error_screen import ErrorScreen
from vdl.ui.screens.finding_screen import FindingScreen
from vdl.ui.screens.input_screen import InputScreen
from vdl.ui.screens.quality_screen import QualityScreen
from vdl.ui.screens.season_download_screen import SeasonDownloadScreen


class VDLApp(App):
    """Main Textual application for vdl."""

    CSS_PATH = "styles.tcss"

    def __init__(self, initial_url: Optional[str] = None):
        super().__init__()
        self.initial_url = initial_url

    def on_mount(self) -> None:
        if self.initial_url:
            self.push_screen(FindingScreen(self.initial_url))
        else:
            self.push_screen(InputScreen())

    def call_safe(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Invokes callback on the app thread safely, whether called from main thread or a worker thread."""
        if threading.get_ident() == getattr(self, "_thread_id", None):
            callback(*args, **kwargs)
        else:
            self.call_from_thread(callback, *args, **kwargs)

    def show_input(self) -> None:
        """Navigates to the initial input screen."""
        self.push_screen(InputScreen())

    def start_finding(self, url: str) -> None:
        """Navigates to the finding stream screen."""
        self.push_screen(FindingScreen(url))

    def show_quality_selection(
        self,
        video_info: VideoInfo,
        series_info: VideoInfo | None = None,
        season_episodes: list[EpisodeOption] | None = None,
    ) -> None:
        """Navigates to the quality selection screen."""
        self.push_screen(QualityScreen(video_info, series_info, season_episodes))

    def show_episode_selection(self, series_info: VideoInfo) -> None:
        """Navigates to the season/episode selection screen."""
        self.push_screen(EpisodeScreen(series_info))

    def start_download(self, video_info: VideoInfo, quality: QualityOption) -> None:
        """Navigates to the download progress screen."""
        self.push_screen(DownloadScreen(video_info, quality))

    def start_season_download(
        self,
        series_info: VideoInfo,
        season_episodes: list[EpisodeOption],
        quality_label: str,
    ) -> None:
        """Navigates to the batch season download screen."""
        self.push_screen(SeasonDownloadScreen(series_info, season_episodes, quality_label))

    def show_complete(self, video_info: VideoInfo, file_path: Path) -> None:
        """Navigates to the complete screen."""
        self.push_screen(CompleteScreen(video_info, file_path))

    def show_season_complete(
        self,
        season_title: str,
        quality_label: str,
        saved: list[Path],
        failed: list[str],
        output_dir: Path,
        series_info: VideoInfo | None = None,
    ) -> None:
        """Navigates to the season batch result screen."""
        self.push_screen(
            SeasonCompleteScreen(
                season_title, quality_label, saved, failed, output_dir, series_info
            )
        )

    def show_error(self, title: str, description: str, url: str) -> None:
        """Navigates to the error screen."""
        self.push_screen(ErrorScreen(title, description, url))
