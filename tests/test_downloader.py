from pathlib import Path
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
