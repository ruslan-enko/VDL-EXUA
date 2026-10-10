from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static

from vdl.core.finder import season_label
from vdl.core.models import EpisodeOption, VideoInfo


def plural_episodes(n: int) -> str:
    """Ukrainian plural: 1 серія / 3 серії / 5 серій."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} серія"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} серії"
    return f"{n} серій"


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

    def __init__(
        self,
        video_info: VideoInfo,
        series_info: VideoInfo | None = None,
        season_episodes: list[EpisodeOption] | None = None,
    ):
        super().__init__()
        self.video_info = video_info
        self.qualities = video_info.qualities
        # When we came from episode selection, Esc should return there.
        self.series_info = series_info or (video_info if video_info.episodes else None)
        self.season_episodes = season_episodes

    @property
    def is_season_mode(self) -> bool:
        return bool(self.season_episodes and len(self.season_episodes) >= 1)

    def _heading(self) -> str:
        if self.is_season_mode:
            assert self.season_episodes is not None
            base = self.series_info.base_title if self.series_info and self.series_info.base_title else self.video_info.title
            return f"{base} — {season_label(self.season_episodes)} ({plural_episodes(len(self.season_episodes))})"
        return self.video_info.title

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static(self._heading(), classes="video-heading")
            if self.is_season_mode:
                assert self.season_episodes is not None
                yield Static(f"Quality — for all {len(self.season_episodes)} episodes", classes="section-title")
            else:
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
        if not self.qualities:
            return
        chosen = self.qualities[self.selected_index]
        if self.is_season_mode:
            assert self.season_episodes is not None
            assert self.series_info is not None
            self.app.start_season_download(
                self.series_info, self.season_episodes, chosen.label
            )
        else:
            self.app.start_download(self.video_info, chosen)

    def action_back(self) -> None:
        if self.series_info and self.series_info.episodes:
            self.app.show_episode_selection(self.series_info)
        else:
            self.app.show_input()

    def action_quit_app(self) -> None:
        self.app.exit()
