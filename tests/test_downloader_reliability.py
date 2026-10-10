"""Reliability tests: stall detection, retry, resume, and part assembly."""

import asyncio
import shutil
from pathlib import Path

import pytest

from vdl.core.downloader import Downloader, build_network_options
from vdl.core.models import QualityOption, VideoInfo

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not available"
)


def _info(url: str, duration: float | None = None) -> VideoInfo:
    return VideoInfo(
        title="Stall Test",
        page_url=url,
        stream_url=url,
        duration=duration,
    )


# ── flag building ────────────────────────────────────────────────


def test_network_options_for_http():
    opts = build_network_options("https://example.com/index.m3u8")
    assert "-reconnect" in opts
    assert "-timeout" in opts
    joined = " ".join(opts)
    assert "reconnect_at_eof" in joined
    assert "reconnect_delay_max" in joined


def test_network_options_avoid_reconnect_streamed():
    """`-reconnect_streamed` + `-reconnect_at_eof` makes ffmpeg loop forever
    on a playlist that has already ended."""
    assert "reconnect_streamed" not in " ".join(
        build_network_options("https://example.com/index.m3u8")
    )


def test_network_options_ignored_for_local_paths():
    assert build_network_options("/tmp/input.mp4") == []
    assert build_network_options("") == []





# ── happy path over real HTTP ────────────────────────────────────


@pytest.mark.asyncio
async def test_downloads_hls_over_http(hls_server, tmp_path: Path):
    server = hls_server()
    info = _info(f"{server.base_url}/index.m3u8", duration=20.0)
    quality = QualityOption(label="Original", url=f"{server.base_url}/index.m3u8")

    downloader = Downloader(
        video_info=info, quality=quality, output_dir=tmp_path / "out"
    )
    final_path = await downloader.start()

    assert final_path.exists()
    assert final_path.stat().st_size > 0
    assert final_path.suffix == ".mp4"
    # No leftover part files.
    assert list((tmp_path / "out").glob(".*part*")) == []


def _duration(path: Path) -> float:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


@pytest.mark.asyncio
async def test_recovers_from_midstream_stall_without_losing_data(
    hls_server, tmp_path: Path
):
    """The core scenario: a connection dies mid-stream and the download must
    still finish, with the final file covering the whole duration."""
    duration = 60
    server = hls_server(
        duration=duration, stall_from=6, stall_count=2, stall_seconds=20
    )
    url = f"{server.base_url}/index.m3u8"

    downloader = Downloader(
        video_info=_info(url, duration=float(duration)),
        quality=QualityOption(label="Original", url=url),
        output_dir=tmp_path / "out",
        stall_timeout=8.0,
        startup_timeout=20.0,
        max_attempts=4,
    )
    final_path = await downloader.start()

    assert final_path.exists()
    assert final_path.stat().st_size > 0
    # Resumed, so the result must span the stream rather than a prefix of it.
    final_duration = _duration(final_path)
    assert final_duration > duration * 0.85, (
        f"expected ~{duration}s, got {final_duration:.1f}s"
    )
    # Parts are cleaned up only after a successful remux.
    assert list((tmp_path / "out").glob(".*")) == []


# ── stall detection ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hanging_server_is_detected_and_fails_fast(hls_server, tmp_path: Path):
    """A server that accepts a request and never answers must not hang forever."""
    server = hls_server(stall_from=1, stall_count=99, stall_seconds=None)
    url = f"{server.base_url}/index.m3u8"
    downloader = Downloader(
        video_info=_info(url, duration=20.0),
        quality=QualityOption(label="Original", url=url),
        output_dir=tmp_path / "out",
        stall_timeout=3.0,
        startup_timeout=4.0,
        max_attempts=2,
    )

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(RuntimeError) as exc:
        await downloader.start()
    elapsed = loop.time() - started

    # Both attempts hit their timeout, then it gave up: bounded, not infinite.
    assert elapsed < 40, f"stall detection took {elapsed:.1f}s"
    message = str(exc.value)
    assert "Stalled" in message or "No data received" in message


@pytest.mark.asyncio
async def test_stall_error_reports_offending_limit(hls_server, tmp_path: Path):
    server = hls_server(stall_from=1, stall_seconds=None)
    url = f"{server.base_url}/index.m3u8"
    downloader = Downloader(
        video_info=_info(url),
        quality=QualityOption(label="Original", url=url),
        output_dir=tmp_path / "out",
        stall_timeout=2.0,
        startup_timeout=2.0,
        max_attempts=1,
    )
    with pytest.raises(RuntimeError, match="limit 2s"):
        await downloader.start()


# ── progress callbacks during retry ──────────────────────────────


@pytest.mark.asyncio
async def test_retry_reports_status_and_resets_attempt(hls_server, tmp_path: Path):
    server = hls_server(stall_from=1, stall_count=99, stall_seconds=None)
    url = f"{server.base_url}/index.m3u8"
    seen: list = []

    def record(progress):
        # Copy: the same object is mutated in place across the whole run.
        seen.append((progress.attempt, progress.status_text))

    downloader = Downloader(
        video_info=_info(url, duration=20.0),
        quality=QualityOption(label="Original", url=url),
        output_dir=tmp_path / "out",
        on_progress=record,
        stall_timeout=2.0,
        startup_timeout=2.0,
        max_attempts=2,
    )
    with pytest.raises(RuntimeError):
        await downloader.start()

    statuses = [s for _, s in seen if s]
    assert any("2/2" in s for s in statuses), statuses
    attempts = {a for a, _ in seen}
    assert attempts == {1, 2}


# ── part assembly ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concatenates_multiple_parts(tmp_path: Path):
    """Two separately-muxed TS parts must join into one playable MP4."""
    import subprocess

    parts = []
    for index, duration in ((1, 3), (2, 3)):
        part = tmp_path / f"chunk{index}.ts"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
                "-f", "lavfi", "-i", f"sine=frequency={440 * index}:duration={duration}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-g", "20", "-force_key_frames", "expr:gte(t,n_forced*2)",
                "-c:a", "aac", "-f", "mpegts", str(part),
            ],
            capture_output=True,
            check=True,
        )
        parts.append(part)

    target = tmp_path / "out"
    target.mkdir()

    downloader = Downloader(
        video_info=_info("local"), quality=QualityOption(label="x", url="local"),
        output_dir=target,
    )
    downloader._final_path = target / "joined.mp4"
    downloader._parts = list(parts)
    await downloader._assemble(shutil.which("ffmpeg"))

    joined = downloader._final_path
    assert joined.exists() and joined.stat().st_size > 0

    # Duration must cover both parts (≈6s), not just one.
    duration = _duration(joined)
    assert 5.0 <= duration <= 7.0, f"unexpected duration {duration}"

    # Parts consumed, merged TS removed.
    assert list(target.glob("*chunk*")) == []
    assert list(target.glob(".*")) == []


@pytest.mark.asyncio
async def test_truncated_part_does_not_discard_downloaded_data(tmp_path: Path):
    """A part killed mid-write is still usable: TS survives truncation, and the
    data before the break must survive the remux."""
    import subprocess

    url = None
    part = tmp_path / "part.ts"
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "warning",
            "-f", "lavfi", "-i", "testsrc=duration=20:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-g", "20", "-c:a", "aac", "-f", "mpegts", str(part),
        ]
    )
    import time as _time

    _time.sleep(2.0)
    proc.kill()
    proc.wait()

    assert part.stat().st_size > 0, "expected a non-empty truncated part"

    target = tmp_path / "out"
    target.mkdir()
    downloader = Downloader(
        video_info=_info("local"), quality=QualityOption(label="x", url="local"),
        output_dir=target,
    )
    downloader._final_path = target / "recovered.mp4"
    downloader._parts = [part]
    await downloader._assemble(shutil.which("ffmpeg"))

    assert downloader._final_path.exists()
    assert _duration(downloader._final_path) > 0.5


@pytest.mark.asyncio
async def test_assemble_keeps_parts_when_remux_fails(tmp_path: Path):
    parts = []
    for index in (1, 2):
        part = tmp_path / f"keep{index}.ts"
        part.write_bytes(b"not a real transport stream")
        parts.append(part)

    target = tmp_path / "out"
    target.mkdir()

    downloader = Downloader(
        video_info=_info("local"), quality=QualityOption(label="x", url="local"),
        output_dir=target,
    )
    downloader._final_path = target / "joined.mp4"
    downloader._parts = list(parts)
    with pytest.raises(RuntimeError, match="Parts kept"):
        await downloader._assemble(shutil.which("ffmpeg"))

    # Data is not thrown away when the join fails.
    assert all(p.exists() for p in parts)


@pytest.mark.asyncio
async def test_assemble_with_no_data_raises(tmp_path: Path):
    downloader = Downloader(
        video_info=_info("local"), quality=QualityOption(label="x", url="local"),
        output_dir=tmp_path,
    )
    downloader._final_path = tmp_path / "nothing.mp4"
    with pytest.raises(RuntimeError, match="no data"):
        await downloader._assemble(shutil.which("ffmpeg"))