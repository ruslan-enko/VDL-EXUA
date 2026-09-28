from pathlib import Path
from typing import Optional
from textual.app import App

from vdl.core.models import QualityOption, VideoInfo
from vdl.ui.screens.complete_screen import CompleteScreen
from vdl.ui.screens.download_screen import DownloadScreen
from vdl.ui.screens.error_screen import ErrorScreen
from vdl.ui.screens.finding_screen import FindingScreen
from vdl.ui.screens.input_screen import InputScreen
from vdl.ui.screens.quality_screen import QualityScreen


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

    def show_input(self) -> None:
        """Navigates to the initial input screen."""
        self.push_screen(InputScreen())

    def start_finding(self, url: str) -> None:
        """Navigates to the finding stream screen."""
        self.push_screen(FindingScreen(url))

    def show_quality_selection(self, video_info: VideoInfo) -> None:
        """Navigates to the quality selection screen."""
        self.push_screen(QualityScreen(video_info))

    def start_download(self, video_info: VideoInfo, quality: QualityOption) -> None:
        """Navigates to the download progress screen."""
        self.push_screen(DownloadScreen(video_info, quality))

    def show_complete(self, video_info: VideoInfo, file_path: Path) -> None:
        """Navigates to the complete screen."""
        self.push_screen(CompleteScreen(video_info, file_path))

    def show_error(self, title: str, description: str, url: str) -> None:
        """Navigates to the error screen."""
        self.push_screen(ErrorScreen(title, description, url))
