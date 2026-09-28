import asyncio
import os
from pathlib import Path
import shutil
import signal
import time
from typing import Callable, Optional

from vdl.core.finder import DEFAULT_HEADERS
from vdl.core.metadata import sanitize_filename
from vdl.core.models import DownloadProgress, QualityOption, VideoInfo


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


class Downloader:
    """Manages an async ffmpeg download process with real-time progress parsing."""

    def __init__(
        self,
        video_info: VideoInfo,
        quality: QualityOption,
        output_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[DownloadProgress], None]] = None,
    ):
        self.video_info = video_info
        self.quality = quality
        self.output_dir = output_dir or (Path.home() / "Downloads")
        self.on_progress = on_progress

        self._process: Optional[asyncio.subprocess.Process] = None
        self._is_cancelled = False
        self._temp_path: Optional[Path] = None
        self._final_path: Optional[Path] = None

    @property
    def final_path(self) -> Optional[Path]:
        return self._final_path

    async def start(self) -> Path:
        """Starts the download and returns the final file Path when finished."""
        ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        if not os.path.exists(ffmpeg_bin):
            raise RuntimeError("ffmpeg is not installed or not found in PATH.")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = sanitize_filename(self.video_info.title)
        self._final_path = get_unique_filepath(self.output_dir, filename)
        self._temp_path = self.output_dir / f".{self._final_path.name}.part"

        # Build ffmpeg command
        headers_str = ""
        if self.video_info.referer:
            headers_str += f"Referer: {self.video_info.referer}\r\n"
        headers_str += f"User-Agent: {DEFAULT_HEADERS['User-Agent']}\r\n"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-headers", headers_str,
            "-i", self.quality.url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-progress", "pipe:1",
            "-nostats",
            str(self._temp_path),
        ]

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        start_time = time.time()
        last_calc_time = start_time
        last_bytes = 0
        current_speed_str = "-- MB/s"
        total_duration = self.video_info.duration

        progress_obj = DownloadProgress()

        try:
            assert self._process.stdout is not None
            while True:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                if "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip()

                    if key == "total_size" and val.isdigit():
                        downloaded_bytes = int(val)
                        progress_obj.downloaded_bytes = downloaded_bytes

                        # Compute speed periodically
                        now = time.time()
                        delta_t = now - last_calc_time
                        if delta_t >= 0.5:
                            delta_bytes = downloaded_bytes - last_bytes
                            speed_bps = delta_bytes / delta_t if delta_t > 0 else 0
                            current_speed_str = f"{format_bytes(int(speed_bps))}/s"
                            last_calc_time = now
                            last_bytes = downloaded_bytes
                        progress_obj.speed_text = current_speed_str

                    elif key == "out_time_us" and val.isdigit():
                        out_time_us = int(val)
                        out_time_sec = out_time_us / 1_000_000.0
                        progress_obj.out_time_seconds = out_time_sec

                        if total_duration and total_duration > 0:
                            pct = min(99.0, (out_time_sec / total_duration) * 100.0)
                            progress_obj.percent = pct

                            elapsed = time.time() - start_time
                            if pct > 1.0 and elapsed > 0:
                                total_est_sec = elapsed / (pct / 100.0)
                                remaining_sec = max(0, total_est_sec - elapsed)
                                progress_obj.eta_text = format_eta(remaining_sec)
                                
                                est_total_b = int(progress_obj.downloaded_bytes / (pct / 100.0))
                                progress_obj.total_bytes = est_total_b

                    elif key == "progress" and val == "end":
                        progress_obj.percent = 100.0
                        progress_obj.eta_text = "00:00"
                        if self.on_progress:
                            self.on_progress(progress_obj)
                        break

                if self.on_progress:
                    self.on_progress(progress_obj)

            await self._process.wait()

            if self._is_cancelled:
                self._cleanup_temp()
                raise asyncio.CancelledError("Download cancelled by user.")

            if self._process.returncode != 0:
                self._cleanup_temp()
                stderr_data = b""
                if self._process.stderr:
                    stderr_data = await self._process.stderr.read()
                err_msg = stderr_data.decode("utf-8", errors="replace")[-300:]
                raise RuntimeError(f"FFmpeg error (code {self._process.returncode}): {err_msg}")

            # Atomic move from temp to final path
            if self._temp_path.exists():
                self._temp_path.rename(self._final_path)

            progress_obj.percent = 100.0
            progress_obj.is_complete = True
            if self.on_progress:
                self.on_progress(progress_obj)

            return self._final_path

        except asyncio.CancelledError:
            self._cleanup_temp()
            raise
        except Exception:
            self._cleanup_temp()
            raise

    def cancel(self):
        """Gracefully terminates ffmpeg process and removes temporary files."""
        self._is_cancelled = True
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
        self._cleanup_temp()

    def _cleanup_temp(self):
        if self._temp_path and self._temp_path.exists():
            try:
                self._temp_path.unlink()
            except OSError:
                pass
