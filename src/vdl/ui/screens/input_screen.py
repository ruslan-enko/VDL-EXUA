import shutil
import subprocess
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input, Static


def get_clipboard_url() -> str:
    """Attempts to read URL from macOS clipboard if present."""
    if shutil.which("pbpaste"):
        try:
            res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=1.0)
            text = res.stdout.strip()
            if text.startswith("http://") or text.startswith("https://"):
                return text
        except Exception:
            pass
    return ""


class InputScreen(Screen):
    """Initial screen: prompts the user to paste a video page URL."""

    BINDINGS = [
        Binding("escape", "quit_app", "Quit", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static("Paste video page URL", classes="section-title")
            
            initial_url = get_clipboard_url()
            yield Input(
                value=initial_url,
                placeholder="https://...",
                id="url-input",
            )
            
            yield Static(
                "Enter continue                    Esc quit",
                classes="footer-nav",
            )

    def on_mount(self) -> None:
        url_input = self.query_one("#url-input", Input)
        url_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw_url = event.value.strip()
        if raw_url:
            self.app.start_finding(raw_url)

    def action_quit_app(self) -> None:
        self.app.exit()
