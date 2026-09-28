from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static


class ErrorScreen(Screen):
    """Screen displayed when stream finding or downloading encounters an error."""

    BINDINGS = [
        Binding("enter", "retry", "Retry", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit_app", "Quit", show=False),
    ]

    def __init__(self, title: str, description: str, url: str):
        super().__init__()
        self.error_title = title
        self.error_description = description
        self.url = url

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static(self.error_title, classes="error-title")
            yield Static(self.error_description, classes="error-desc")

            yield Static(
                "Enter Retry          Esc Back          q Quit",
                classes="footer-nav",
            )

    def action_retry(self) -> None:
        self.app.start_finding(self.url)

    def action_back(self) -> None:
        self.app.show_input()

    def action_quit_app(self) -> None:
        self.app.exit()
