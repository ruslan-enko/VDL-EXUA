from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from vdl.core.finder import find_video, FinderError
from vdl.core.models import VideoInfo


class FindingScreen(Screen):
    """Screen showing progressive video stream discovery."""

    BINDINGS = [
        Binding("escape", "cancel", "Back", show=False),
        Binding("q", "cancel", "Quit", show=False),
    ]

    def __init__(self, target_url: str):
        super().__init__()
        self.target_url = target_url
        self._video_info: VideoInfo | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static("Finding video...", classes="section-title")
            
            yield Static("○ Connecting to page...", id="step-page", classes="step-row step-active")
            yield Static("", id="step-player", classes="step-row")
            yield Static("", id="step-stream", classes="step-row")
            yield Static("", id="step-qualities", classes="step-row")
            
            yield Static("Esc back", classes="footer-nav")

    def on_mount(self) -> None:
        self.run_worker(self._do_find())

    async def _do_find(self) -> None:
        def on_step(step: str, detail: str) -> None:
            self.app.call_from_thread(self._handle_step, step, detail)

        try:
            info = await find_video(self.target_url, on_step=on_step)
            self._video_info = info
            # Transition to quality selection
            self.app.call_from_thread(self.app.show_quality_selection, info)
        except FinderError as e:
            self.app.call_from_thread(
                self.app.show_error,
                "✕ Could not find a video stream",
                str(e),
                self.target_url,
            )
        except Exception as e:
            self.app.call_from_thread(
                self.app.show_error,
                "✕ An unexpected error occurred",
                str(e),
                self.target_url,
            )

    def _handle_step(self, step: str, detail: str) -> None:
        try:
            if step == "page_found":
                step_page = self.query_one("#step-page", Static)
                step_page.update("✓ Page found")
                step_page.remove_class("step-active")
                step_page.add_class("step-done")

                step_player = self.query_one("#step-player", Static)
                step_player.update("○ Video player found")
                step_player.add_class("step-active")

            elif step == "player_found":
                step_player = self.query_one("#step-player", Static)
                step_player.update("✓ Video player found")
                step_player.remove_class("step-active")
                step_player.add_class("step-done")

                step_stream = self.query_one("#step-stream", Static)
                step_stream.update("○ HLS stream found")
                step_stream.add_class("step-active")

            elif step == "stream_found":
                step_stream = self.query_one("#step-stream", Static)
                step_stream.update("✓ HLS stream found")
                step_stream.remove_class("step-active")
                step_stream.add_class("step-done")

            elif step == "qualities_ready":
                step_qual = self.query_one("#step-qualities", Static)
                step_qual.update("✓ Qualities ready")
                step_qual.add_class("step-done")
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.app.show_input()
