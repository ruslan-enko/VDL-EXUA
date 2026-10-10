import asyncio
from pathlib import Path
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from vdl.core.downloader import Downloader, format_bytes
from vdl.core.finder import load_episode_video, season_label
from vdl.core.models import DownloadProgress, EpisodeOption, VideoInfo
from vdl.ui.screens.download_screen import render_progress_bar


class SeasonDownloadScreen(Screen):
    """Batch download of a whole season, episode by episode."""

    BINDINGS = [
        Binding("escape", "cancel_download", "Cancel", show=False),
        Binding("ctrl+c", "cancel_download", "Cancel", show=False),
    ]

    def __init__(
        self,
        series_info: VideoInfo,
        season_episodes: list[EpisodeOption],
        quality_label: str,
    ):
        super().__init__()
        self.series_info = series_info
        self.season_episodes = season_episodes
        self.quality_label = quality_label
        self.output_dir = Path.home() / "Downloads"
        self._cancelled = False
        self._current_downloader: Downloader | None = None
        self._done = 0
        self._failed: list[str] = []
        self._saved: list[Path] = []

    def _season_title(self) -> str:
        base = self.series_info.base_title or self.series_info.title
        return f"{base} — {season_label(self.season_episodes)}"

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("vdl", classes="brand")
            yield Static("Downloading season", classes="section-title")
            yield Static(self._season_title(), classes="video-heading")
            yield Static(
                f"Quality {self.quality_label}  •  0/{len(self.season_episodes)}",
                id="season-counter",
                classes="progress-stats",
            )
            yield Static("", id="episode-name", classes="progress-stats")

            with Vertical(classes="progress-container"):
                yield Static(
                    render_progress_bar(0.0),
                    id="progress-bar",
                    classes="progress-bar-text",
                )
                yield Static("0 B", id="stat-size", classes="progress-stats")
                yield Static("-- MB/s", id="stat-speed", classes="progress-stats")
                yield Static("--:-- remaining", id="stat-eta", classes="progress-stats")

            yield Static("", id="stat-status", classes="progress-status")

            yield Static("Esc cancel season", classes="footer-nav")

    def on_mount(self) -> None:
        self.run_worker(self._run_queue())

    def _on_file_progress(self, total: int, done_before: int, progress: DownloadProgress) -> None:
        self.app.call_safe(self._update_ui, total, done_before, progress)

    def _update_ui(self, total: int, done_before: int, progress: DownloadProgress) -> None:
        try:
            overall = (done_before + progress.percent / 100.0) / total * 100.0 if total else 0.0
            self.query_one("#progress-bar", Static).update(render_progress_bar(overall))
            size_w = self.query_one("#stat-size", Static)
            if progress.total_bytes and progress.total_bytes > progress.downloaded_bytes:
                size_w.update(
                    f"{format_bytes(progress.downloaded_bytes)} / {format_bytes(progress.total_bytes)}"
                )
            else:
                size_w.update(format_bytes(progress.downloaded_bytes))
            self.query_one("#stat-speed", Static).update(progress.speed_text)
            self.query_one("#stat-eta", Static).update(f"{progress.eta_text} remaining")
            self.query_one("#stat-status", Static).update(progress.status_text or "")
        except Exception:
            pass

    def _set_counter(self, done: int, current_label: str = "") -> None:
        try:
            total = len(self.season_episodes)
            self.query_one("#season-counter", Static).update(
                f"Quality {self.quality_label}  •  {done}/{total}"
            )
            if current_label:
                self.query_one("#episode-name", Static).update(f"› {current_label}")
        except Exception:
            pass

    async def _run_queue(self) -> None:
        total = len(self.season_episodes)
        try:
            for idx, episode in enumerate(self.season_episodes):
                if self._cancelled:
                    raise asyncio.CancelledError("Download cancelled by user.")

                self.app.call_safe(self._set_counter, idx, episode.label)

                # Resolve per-episode stream + qualities.
                try:
                    info = await load_episode_video(self.series_info, episode)
                except Exception as e:
                    self._failed.append(f"{episode.label}: {e}")
                    continue

                quality = next(
                    (q for q in info.qualities if q.label == self.quality_label),
                    info.qualities[0] if info.qualities else None,
                )
                if quality is None:
                    self._failed.append(f"{episode.label}: no stream")
                    continue

                self._current_downloader = Downloader(
                    video_info=info,
                    quality=quality,
                    output_dir=self.output_dir,
                    on_progress=lambda p, t=total, d=idx: self._on_file_progress(t, d, p),
                )
                try:
                    final_path = await self._current_downloader.start()
                    self._saved.append(final_path)
                    self._done += 1
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._failed.append(f"{episode.label}: {e}")
                    continue
                finally:
                    self._current_downloader = None

            if self._cancelled:
                raise asyncio.CancelledError("Download cancelled by user.")

            self.app.call_safe(
                self.app.show_season_complete,
                self._season_title(),
                self.quality_label,
                self._saved,
                self._failed,
                self.output_dir,
                self.series_info,
            )
        except asyncio.CancelledError:
            self.app.call_safe(self.app.show_input)
        except Exception as e:
            self.app.call_safe(
                self.app.show_error,
                "✕ Season download error",
                str(e),
                self.series_info.page_url,
            )

    def action_cancel_download(self) -> None:
        self._cancelled = True
        if self._current_downloader:
            self._current_downloader.cancel()
        self.app.show_input()
