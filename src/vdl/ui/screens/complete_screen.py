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
        Binding("q", "quit_app", "Quit", show=False),
        Binding("escape", "quit_app", "Quit", show=False),
    ]

    def __init__(self, video_info: VideoInfo, file_path: Path):
        super().__init__()
        self.video_info = video_info
        self.file_path = file_path

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

            yield Static(
                "Press Enter to download another\nq        Quit",
                classes="footer-nav",
            )

    def action_download_another(self) -> None:
        self.app.show_input()

    def action_quit_app(self) -> None:
        self.app.exit()
