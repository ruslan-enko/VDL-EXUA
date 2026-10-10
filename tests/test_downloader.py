from pathlib import Path
import pytest
from vdl.core.downloader import format_bytes, format_eta, get_unique_filepath


def test_format_bytes():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024 * 512) == "512.0 KB"
    assert format_bytes(int(1024 * 1024 * 8.4)) == "8.40 MB"
    assert format_bytes(int(1024 * 1024 * 1024 * 1.82)) == "1.82 GB"


def test_format_eta():
    assert format_eta(45) == "00:45"
    assert format_eta(151) == "02:31"
    assert format_eta(3665) == "01:01:05"


def test_unique_filepath(tmp_path: Path):
    file1 = tmp_path / "video.mp4"
    file1.touch()

    path2 = get_unique_filepath(tmp_path, "video.mp4")
    assert path2.name == "video (1).mp4"
    path2.touch()

    path3 = get_unique_filepath(tmp_path, "video.mp4")
    assert path3.name == "video (2).mp4"


def test_downloader_temp_format(tmp_path: Path):
    from vdl.core.downloader import Downloader
    from vdl.core.models import QualityOption, VideoInfo

    v_info = VideoInfo(
        title="American hostage — S01E01",
        page_url="https://example.com",
        stream_url="https://example.com/index.m3u8",
    )
    quality = QualityOption(label="1080p", url="https://example.com/1080.m3u8")
    downloader = Downloader(video_info=v_info, quality=quality, output_dir=tmp_path)
    assert downloader._temp_path is None


@pytest.mark.asyncio
async def test_downloader_execution_with_local_input(tmp_path: Path):
    import subprocess
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")

    input_file = tmp_path / "input.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1", "-c:v", "libx264", str(input_file)],
        capture_output=True,
        check=True,
    )

    from vdl.core.downloader import Downloader
    from vdl.core.models import QualityOption, VideoInfo

    v_info = VideoInfo(
        title="American hostage — S01E01",
        page_url="https://example.com",
        stream_url=str(input_file),
    )
    quality = QualityOption(label="360p", url=str(input_file))
    downloader = Downloader(video_info=v_info, quality=quality, output_dir=tmp_path)
    final_file = await downloader.start()
    assert final_file.exists()
    assert final_file.name == "American hostage — S01E01.mp4"
