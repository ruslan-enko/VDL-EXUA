from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static

from vdl.core.finder import (
    episodes_of_same_season,
    load_episode_video,
    FinderError,
)
from vdl.core.models import EpisodeOption, VideoInfo


class EpisodeScreen(Screen):
    """Serial episode selection: season / dub / episode list."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("left", "prev_season", "Prev season", show=False),
        Binding("right", "next_season", "Next season", show=False),
        Binding("h", "prev_season", "Prev season", show=False),
        Binding("l", "next_season", "Next season", show=False),
        Binding("enter", "confirm", "Continue", show=False),
        Binding("s", "season", "Season", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit_app", "Quit", show=False),
    ]

    selected_index = reactive(0)

    def __init__(self, series_info: VideoInfo):
        super().__init__()
        self.series_info = series_info
        self.episodes = series_info.episodes
        self._loading = False

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            base = self.series_info.base_title or self.series_info.title
            yield Static(base, classes="video-heading")
            count = len(self.episodes)
            yield Static(f"Episodes ({count})", classes="section-title")

            with Vertical(id="episode-list", classes="quality-container"):
                for i, ep in enumerate(self.episodes):
                    prefix = "› " if i == 0 else "  "
                    cls = "quality-item quality-item-selected" if i == 0 else "quality-item"
                    yield Static(f"{prefix}{ep.label}", id=f"episode-{i}", classes=cls)

            yield Static(
                "↑↓ Episode   ←→ Season   Enter 1 ep   S Season   Esc Back",
                classes="footer-nav",
            )

    def watch_selected_index(self, old_val: int, new_val: int) -> None:
        try:
            old_item = self.query_one(f"#episode-{old_val}", Static)
            old_item.update(f"  {self.episodes[old_val].label}")
            old_item.remove_class("quality-item-selected")

            new_item = self.query_one(f"#episode-{new_val}", Static)
            new_item.update(f"› {self.episodes[new_val].label}")
            new_item.add_class("quality-item-selected")
            new_item.scroll_visible()
        except Exception:
            pass

    def _season_starts(self) -> list[int]:
        starts: list[int] = []
        last_season = None
        for i, ep in enumerate(self.episodes):
            key = ep.season_num if ep.season_num is not None else ep.season
            if key != last_season:
                starts.append(i)
                last_season = key
        return starts

    def _jump_season(self, direction: int) -> None:
        if not self.episodes:
            return
        starts = self._season_starts()
        if not starts:
            return
        cur = self.selected_index
        if direction > 0:
            nxt = next((s for s in starts if s > cur), starts[0])
            self.selected_index = nxt
        else:
            prev = [s for s in starts if s < cur]
            self.selected_index = prev[-1] if prev else starts[-1]

    def action_cursor_up(self) -> None:
        if self.episodes and not self._loading:
            self.selected_index = (self.selected_index - 1) % len(self.episodes)

    def action_cursor_down(self) -> None:
        if self.episodes and not self._loading:
            self.selected_index = (self.selected_index + 1) % len(self.episodes)

    def action_prev_season(self) -> None:
        if not self._loading:
            self._jump_season(-1)

    def action_next_season(self) -> None:
        if not self._loading:
            self._jump_season(1)

    def action_confirm(self) -> None:
        if not self.episodes or self._loading:
            return
        self._loading = True
        chosen: EpisodeOption = self.episodes[self.selected_index]
        try:
            footer = self.query_one(".footer-nav", Static)
            footer.update("Loading episode stream...")
        except Exception:
            pass
        self.run_worker(self._resolve_episode(chosen))

    def action_season(self) -> None:
        """Downloads the whole season of the highlighted episode."""
        if not self.episodes or self._loading:
            return
        self._loading = True
        chosen: EpisodeOption = self.episodes[self.selected_index]
        try:
            footer = self.query_one(".footer-nav", Static)
            footer.update("Loading season...")
        except Exception:
            pass
        self.run_worker(self._resolve_season(chosen))

    async def _resolve_episode(self, episode: EpisodeOption) -> None:
        try:
            info = await load_episode_video(self.series_info, episode)
            self.app.call_safe(self.app.show_quality_selection, info, self.series_info)
        except FinderError as e:
            self.app.call_safe(
                self.app.show_error,
                "✕ Could not load episode",
                str(e),
                self.series_info.page_url,
            )
        except Exception as e:
            self.app.call_safe(
                self.app.show_error,
                "✕ An unexpected error occurred",
                str(e),
                self.series_info.page_url,
            )
        finally:
            self._loading = False

    async def _resolve_season(self, episode: EpisodeOption) -> None:
        try:
            season_eps = episodes_of_same_season(self.episodes, episode)
            if not season_eps:
                raise FinderError("No episodes found for this season.")
            # Qualities are shown from the first episode; the label chosen
            # here is then matched per-episode during batch download.
            first_info = await load_episode_video(self.series_info, season_eps[0])
            self.app.call_safe(
                self.app.show_quality_selection,
                first_info,
                self.series_info,
                season_eps,
            )
        except FinderError as e:
            self.app.call_safe(
                self.app.show_error,
                "✕ Could not load season",
                str(e),
                self.series_info.page_url,
            )
        except Exception as e:
            self.app.call_safe(
                self.app.show_error,
                "✕ An unexpected error occurred",
                str(e),
                self.series_info.page_url,
            )
        finally:
            self._loading = False

    def action_back(self) -> None:
        self.app.show_input()

    def action_quit_app(self) -> None:
        self.app.exit()
