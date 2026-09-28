from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static

from vdl.core.models import VideoInfo


class QualityScreen(Screen):
    """Quality selection screen with arrow key navigation."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("enter", "confirm", "Download", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit_app", "Quit", show=False),
    ]

    selected_index = reactive(0)

    def __init__(self, video_info: VideoInfo):
        super().__init__()
        self.video_info = video_info
        self.qualities = video_info.qualities

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static(self.video_info.title, classes="video-heading")
            yield Static("Quality", classes="section-title")
            
            with Vertical(id="quality-list", classes="quality-container"):
                for i, q in enumerate(self.qualities):
                    prefix = "› " if i == 0 else "  "
                    cls = "quality-item quality-item-selected" if i == 0 else "quality-item"
                    yield Static(f"{prefix}{q.label}", id=f"quality-{i}", classes=cls)
            
            yield Static(
                "↑↓ Select          Enter Download          Esc Back",
                classes="footer-nav",
            )

    def watch_selected_index(self, old_val: int, new_val: int) -> None:
        try:
            old_item = self.query_one(f"#quality-{old_val}", Static)
            old_item.update(f"  {self.qualities[old_val].label}")
            old_item.remove_class("quality-item-selected")

            new_item = self.query_one(f"#quality-{new_val}", Static)
            new_item.update(f"› {self.qualities[new_val].label}")
            new_item.add_class("quality-item-selected")
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        if self.qualities:
            self.selected_index = (self.selected_index - 1) % len(self.qualities)

    def action_cursor_down(self) -> None:
        if self.qualities:
            self.selected_index = (self.selected_index + 1) % len(self.qualities)

    def action_confirm(self) -> None:
        if self.qualities:
            chosen = self.qualities[self.selected_index]
            self.app.start_download(self.video_info, chosen)

    def action_back(self) -> None:
        self.app.show_input()

    def action_quit_app(self) -> None:
        self.app.exit()
