import asyncio
import os
from collections import deque
from pathlib import Path
import shutil
import signal
import time
from typing import Callable, List, Optional

from vdl.core.finder import DEFAULT_HEADERS
from vdl.core.metadata import sanitize_filename
from vdl.core.models import DownloadProgress, QualityOption, VideoInfo

# How often the stall watchdog wakes up to check output-file growth (seconds).
WATCHDOG_TICK = 1.0
# Seconds without any output growth before the attempt is considered hung.
DEFAULT_STALL_TIMEOUT = 30.0
# Seconds allowed before an attempt writes anything at all. Generous, because
# a resume (-ss) has to walk to the seek point before ffmpeg emits data.
DEFAULT_STARTUP_TIMEOUT = 120.0
# Total ffmpeg attempts before giving up (1 = no retries).
DEFAULT_MAX_ATTEMPTS = 3
# Seconds to wait before restarting a failed attempt.
RETRY_DELAY = 2.0
# Re-seek this far back so a retry does not leave a gap at the join point.
RESUME_OVERLAP = 1.0
# Number of stderr lines kept for diagnostics.
STDERR_TAIL_LINES = 40


def format_bytes(num_bytes: int) -> str:
    """Formats bytes to human-readable string (e.g., 1.42 GB, 8.4 MB)."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_eta(seconds: float) -> str:
    """Formats seconds into MM:SS or HH:MM:SS."""
    sec = int(max(0, seconds))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_unique_filepath(directory: Path, filename: str) -> Path:
    """Ensures a non-colliding file path in the directory."""
    path = directory / filename
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def build_network_options(url: str) -> List[str]:
    """Returns reconnect/timeout flags for an HTTP(S) input, else [].

    HLS input has no socket timeout by default: when the CDN accepts a
    segment connection and then goes silent, ffmpeg waits forever. These
    options make it retry instead of hanging.

    `-reconnect_streamed` is deliberately NOT set. Combined with
    `-reconnect_at_eof` on a finished playlist, ffmpeg keeps reconnecting at
    the end of the stream and never terminates — verified to hang 100% of the
    time against a local HLS server with `#EXT-X-ENDLIST`.
    """
    if not url.startswith(("http://", "https://")):
        return []
    return [
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_delay_max", "5",
        "-timeout", "15000000",
    ]





class _AttemptFailure(Exception):
    """A single ffmpeg attempt failed but the download may still be resumable."""

    def __init__(self, message: str, out_time: float = 0.0):
        super().__init__(message)
        self.out_time = out_time


class _StallError(_AttemptFailure):
    """ffmpeg produced no output for longer than the stall timeout."""


class Downloader:
    """Manages an async ffmpeg download process with real-time progress parsing.

    Survives hung or dropped connections: attempts that stall or exit with an
    error are retried with an input seek past whatever was already written, and
    the resulting parts are concatenated into the final file.
    """

    def __init__(
        self,
        video_info: VideoInfo,
        quality: QualityOption,
        output_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[DownloadProgress], None]] = None,
        stall_timeout: float = DEFAULT_STALL_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ):
        self.video_info = video_info
        self.quality = quality
        self.output_dir = output_dir or (Path.home() / "Downloads")
        self.on_progress = on_progress
        self.stall_timeout = stall_timeout
        self.max_attempts = max(1, max_attempts)
        self.startup_timeout = startup_timeout

        self._process: Optional[asyncio.subprocess.Process] = None
        self._is_cancelled = False
        self._temp_path: Optional[Path] = None
        self._final_path: Optional[Path] = None
        self._parts: List[Path] = []
        self._log_tail: deque = deque(maxlen=STDERR_TAIL_LINES)
        self._run_start = 0.0

    @property
    def final_path(self) -> Optional[Path]:
        return self._final_path

    @property
    def log_tail(self) -> List[str]:
        """Last ffmpeg stderr lines, for diagnostics."""
        return list(self._log_tail)

    async def start(self) -> Path:
        """Downloads the stream and returns the final file Path."""
        ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        if not os.path.exists(ffmpeg_bin):
            raise RuntimeError("ffmpeg is not installed or not found in PATH.")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = sanitize_filename(self.video_info.title)
        self._final_path = get_unique_filepath(self.output_dir, filename)
        self._temp_path = self._final_path.with_name(f".{self._final_path.name}.part")

        progress = DownloadProgress(max_attempts=self.max_attempts)
        self._run_start = time.time()

        # Absolute position in the source timeline already downloaded.
        resume_from = 0.0
        attempt = 0
        self._speed_bytes = 0
        self._speed_time = time.time()
        self._speed_text = "-- MB/s"
        self._bytes_before_attempt = 0

        while True:
            attempt += 1
            part_path = self._part_path(attempt)
            try:
                part_path.unlink()
            except OSError:
                pass
            self._parts.append(part_path)

            progress.attempt = attempt
            progress.status_text = None
            if self.on_progress:
                self.on_progress(progress)

            try:
                await self._run_attempt(ffmpeg_bin, part_path, resume_from, progress)
                break
            except _AttemptFailure as e:
                if self._is_cancelled:
                    self._cleanup_parts()
                    raise asyncio.CancelledError("Download cancelled by user.")

                # Drop the part if nothing usable landed on disk.
                if not self._has_content(part_path):
                    self._forget_part(part_path)

                if attempt >= self.max_attempts:
                    self._cleanup_parts()
                    raise RuntimeError(
                        f"{e}\n(after {attempt} attempts, {len(self._parts)} part(s) kept "
                        f"in {self.output_dir})"
                    )

                resume_from = max(0.0, resume_from + e.out_time - RESUME_OVERLAP)
                self._bytes_before_attempt += self._file_size(part_path)
                progress.status_text = (
                    f"Reconnecting… ({attempt + 1}/{self.max_attempts})"
                )
                if self.on_progress:
                    self.on_progress(progress)
                await asyncio.sleep(RETRY_DELAY)
                if self._is_cancelled:
                    self._cleanup_parts()
                    raise asyncio.CancelledError("Download cancelled by user.")

        await self._assemble(ffmpeg_bin)

        progress.percent = 100.0
        progress.is_complete = True
        progress.status_text = None
        progress.eta_text = "00:00"
        if self.on_progress:
            self.on_progress(progress)

        return self._final_path

    # ── attempt loop internals ──────────────────────────────────────

    def _part_path(self, index: int) -> Path:
        """Part file for attempt `index`; attempt 1 keeps the legacy .part name."""
        assert self._final_path is not None
        if index == 1:
            assert self._temp_path is not None
            return self._temp_path
        return self._final_path.with_name(f".{self._final_path.name}.part{index}")

    @staticmethod
    def _has_content(path: Path) -> bool:
        try:
            return path.stat().st_size > 0
        except OSError:
            return False

    def _forget_part(self, path: Path) -> None:
        if path in self._parts:
            self._parts.remove(path)
        try:
            path.unlink()
        except OSError:
            pass

    def _build_cmd(
        self, ffmpeg_bin: str, out_path: Path, seek_seconds: float
    ) -> List[str]:
        """Builds the ffmpeg argv for one attempt."""
        headers_str = ""
        if self.quality.url.startswith(("http://", "https://")):
            if self.video_info.referer:
                headers_str += f"Referer: {self.video_info.referer}\r\n"
            headers_str += f"User-Agent: {DEFAULT_HEADERS['User-Agent']}\r\n"

        selected = getattr(self.video_info, "selected_audio", None)
        audio_url = None
        if selected is not None and getattr(selected, "uri", None):
            audio_url = selected.uri

        cmd = [ffmpeg_bin, "-y"]

        def add_input_opts(url: str, with_seek: bool) -> None:
            if headers_str:
                cmd.extend(["-headers", headers_str])
            cmd.extend(build_network_options(url))
            if with_seek and seek_seconds > 0:
                cmd.extend(["-ss", f"{seek_seconds:.3f}"])

        add_input_opts(self.quality.url, with_seek=True)
        cmd.extend(["-i", self.quality.url])
        if audio_url:
            # Video-only variant + separate dub rendition: mux chosen video
            # quality with the chosen audio track.
            add_input_opts(audio_url, with_seek=False)
            cmd.extend(["-i", audio_url])

        if audio_url:
            cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
        cmd.extend([
            "-c", "copy",
            "-progress", "pipe:1",
            "-nostats",
            # Without this ffmpeg emits a line per segment into a pipe we
            # never drain mid-run; once the 64 KB pipe buffer fills, ffmpeg
            # blocks on write() and the download silently stops.
            "-loglevel", "warning",
            # Parts are MPEG-TS, not MP4: a killed attempt leaves a truncated
            # TS that is still concatenable, whereas a truncated MP4 has no
            # moov atom and cannot be reopened at all.
            "-f", "mpegts",
            str(out_path),
        ])
        return cmd

    async def _run_attempt(
        self,
        ffmpeg_bin: str,
        out_path: Path,
        seek_seconds: float,
        progress: DownloadProgress,
    ) -> None:
        """Runs one ffmpeg attempt, raising _AttemptFailure on stall or error."""
        cmd = self._build_cmd(ffmpeg_bin, out_path, seek_seconds)
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_tail: deque = deque(maxlen=STDERR_TAIL_LINES)

        async def drain_stderr() -> None:
            assert self._process is not None and self._process.stderr is not None
            while True:
                raw = await self._process.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    stderr_tail.append(line)

        stdout_queue: asyncio.Queue = asyncio.Queue()

        async def pump_stdout() -> None:
            assert self._process is not None and self._process.stdout is not None
            while True:
                raw = await self._process.stdout.readline()
                if not raw:
                    break
                stdout_queue.put_nowait(
                    raw.decode("utf-8", errors="replace").strip()
                )
            stdout_queue.put_nowait(None)  # EOF sentinel

        pumps = [
            asyncio.create_task(drain_stderr()),
            asyncio.create_task(pump_stdout()),
        ]

        total_duration = self.video_info.duration
        last_size = 0
        last_progress_at = time.monotonic()
        saw_output = False
        # Media seconds muxed during THIS attempt, excluding the seek offset.
        # This is what a retry has to skip forward by.
        attempt_out_time = 0.0

        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        stdout_queue.get(), timeout=WATCHDOG_TICK
                    )
                except asyncio.TimeoutError:
                    line = None
                    tick_only = True
                else:
                    tick_only = False

                if line is None and not tick_only:
                    break  # stdout EOF

                if not tick_only and line:
                    before = attempt_out_time
                    attempt_out_time = self._handle_progress_line(
                        line, seek_seconds, total_duration, progress,
                        attempt_out_time,
                    )
                    # ffmpeg advancing the media clock means real progress even
                    # when the muxer has not flushed bytes to disk yet — which is
                    # the normal case for MP4, where writes land in bursts.
                    if attempt_out_time > before:
                        saw_output = True
                        last_progress_at = time.monotonic()

                size = self._file_size(out_path)
                if size > last_size:
                    last_size = size
                    last_progress_at = time.monotonic()
                    saw_output = True

                idle_for = time.monotonic() - last_progress_at
                # A stalled connection is detected by output growth stopping.
                # Before the first byte lands, use the looser startup budget,
                # since an input seek can take a while to reach its target.
                limit = self.stall_timeout if saw_output else self.startup_timeout
                if idle_for > limit:
                    waited = int(idle_for)
                    self._kill_process()
                    what = "Stalled" if saw_output else "No data received"
                    raise _StallError(
                        f"{what}: output unchanged for {waited}s "
                        f"(limit {int(limit)}s).",
                        attempt_out_time,
                    )
        finally:
            for task in pumps:
                task.cancel()

        if self._is_cancelled:
            raise asyncio.CancelledError("Download cancelled by user.")

        try:
            await asyncio.wait_for(self._process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self._kill_process()
            raise _AttemptFailure(
                "FFmpeg did not exit after closing its output stream.",
                attempt_out_time,
            )

        self._log_tail = stderr_tail

        if self._process.returncode != 0:
            err_text = "\n".join(stderr_tail).strip()
            if len(err_text) > 1000:
                err_text = err_text[-1000:]
            raise _AttemptFailure(
                f"FFmpeg error (code {self._process.returncode}): "
                f"{err_text or 'no output'}",
                attempt_out_time,
            )

        if not self._has_content(out_path):
            raise _AttemptFailure(
                "FFmpeg produced an empty file.", attempt_out_time
            )

    def _handle_progress_line(
        self,
        line: str,
        seek_seconds: float,
        total_duration: Optional[float],
        progress: DownloadProgress,
        attempt_out_time: float,
    ) -> float:
        """Applies one `-progress` key=value line.

        Returns the media seconds muxed so far during this attempt, excluding
        the seek offset, which is what a retry needs to resume from.
        """
        if "=" not in line:
            return attempt_out_time
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()

        if key == "total_size" and val.isdigit():
            attempt_bytes = int(val)
            # Carried-over bytes from earlier attempts, so the size readout
            # keeps growing across a resume instead of resetting to zero.
            cumulative = self._bytes_before_attempt + attempt_bytes
            progress.downloaded_bytes = cumulative

            now = time.time()
            delta_t = now - self._speed_time
            if delta_t >= 0.5:
                delta_bytes = cumulative - self._speed_bytes
                self._speed_text = (
                    f"{format_bytes(int(delta_bytes / delta_t))}/s"
                    if delta_t > 0
                    else self._speed_text
                )
                self._speed_time = now
                self._speed_bytes = cumulative
            progress.speed_text = self._speed_text
            return attempt_out_time

        if key == "out_time_us" and val.isdigit():
            attempt_out_time = int(val) / 1_000_000.0
            # ffmpeg reports time relative to the seek point; add the offset
            # back so percent and ETA stay correct across resumed attempts.
            out_time_sec = seek_seconds + attempt_out_time
            progress.out_time_seconds = out_time_sec

            if total_duration and total_duration > 0:
                pct = min(99.0, (out_time_sec / total_duration) * 100.0)
                progress.percent = pct

                elapsed = time.time() - self._run_start
                if pct > 1.0 and elapsed > 0:
                    total_est_sec = elapsed / (pct / 100.0)
                    progress.eta_text = format_eta(max(0.0, total_est_sec - elapsed))
                    progress.total_bytes = int(
                        progress.downloaded_bytes / (pct / 100.0)
                    )
            return attempt_out_time

        if key == "progress":
            if val == "end":
                progress.percent = 100.0
                progress.eta_text = "00:00"
            if self.on_progress:
                self.on_progress(progress)
            return attempt_out_time

        return attempt_out_time

    @staticmethod
    def _file_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _kill_process(self) -> None:
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    async def _assemble(self, ffmpeg_bin: str) -> None:
        """Joins downloaded TS parts into the final MP4.

        Parts are byte-concatenated and remuxed once, so a truncated part only
        costs the packets ffmpeg could not read — not the whole file.
        """
        assert self._final_path is not None
        usable = [p for p in self._parts if self._has_content(p)]
        self._parts = usable

        if not usable:
            self._cleanup_parts()
            raise RuntimeError("Download produced no data.")

        merged = self._final_path.with_name(f".{self._final_path.name}.merged.ts")
        try:
            with open(merged, "wb") as out:
                for part in usable:
                    with open(part, "rb") as src:
                        while chunk := src.read(1 << 20):
                            out.write(chunk)

            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin, "-y",
                "-fflags", "+genpts",
                "-i", str(merged),
                "-c", "copy",
                "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart",
                "-loglevel", "warning",
                str(self._final_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0 or not self._has_content(self._final_path):
                tail = (err or b"").decode("utf-8", errors="replace").strip()[-500:]
                # Parts are deliberately left on disk: they hold the only copy
                # of the data, and the user may want to join them by hand.
                raise RuntimeError(
                    f"Could not remux {len(usable)} downloaded parts: "
                    f"{tail or 'ffmpeg produced no output'}\n"
                    f"Parts kept in {self.output_dir}."
                )
        except BaseException:
            # Remux failed: parts hold the only copy of the data. Leave them.
            raise
        finally:
            try:
                merged.unlink()
            except OSError:
                pass

        self._cleanup_parts()

    def cancel(self):
        """Gracefully terminates ffmpeg process and removes temporary files."""
        self._is_cancelled = True
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
            except (ProcessLookupError, OSError):
                pass
        self._cleanup_parts()

    def _cleanup_parts(self):
        for path in list(self._parts) + (
            [self._temp_path] if self._temp_path else []
        ):
            try:
                path.unlink()
            except OSError:
                pass
        self._parts.clear()