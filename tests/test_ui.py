from pathlib import Path
import pytest
from vdl.core.models import QualityOption, VideoInfo
from vdl.ui.app import VDLApp
from vdl.ui.screens.complete_screen import CompleteScreen
from vdl.ui.screens.download_screen import DownloadScreen, render_progress_bar
from vdl.ui.screens.error_screen import ErrorScreen
from vdl.ui.screens.finding_screen import FindingScreen
from vdl.ui.screens.input_screen import InputScreen
from vdl.ui.screens.quality_screen import QualityScreen


@pytest.mark.asyncio
async def test_input_screen_mount():
    app = VDLApp()
    async with app.run_test() as pilot:
        assert isinstance(app.screen, InputScreen)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_quality_screen_navigation():
    video_info = VideoInfo(
        title="Lanterns — S01E07",
        page_url="https://uafix.net/serials/lihtari/season-01-episode-07/",
        stream_url="https://zetvideo.net/stream.m3u8",
        qualities=[
            QualityOption(label="1080p", url="https://zetvideo.net/1080.m3u8"),
            QualityOption(label="720p", url="https://zetvideo.net/720.m3u8"),
            QualityOption(label="480p", url="https://zetvideo.net/480.m3u8"),
        ],
    )

    app = VDLApp()
    async with app.run_test() as pilot:
        app.show_quality_selection(video_info)
        await pilot.pause()
        assert isinstance(app.screen, QualityScreen)
        assert app.screen.selected_index == 0

        # Press down
        await pilot.press("down")
        await pilot.pause()
        assert app.screen.selected_index == 1

        # Press up
        await pilot.press("up")
        await pilot.pause()
        assert app.screen.selected_index == 0


def test_render_progress_bar():
    bar_0 = render_progress_bar(0.0, width=10)
    assert bar_0 == "░░░░░░░░░░  0%"

    bar_50 = render_progress_bar(50.0, width=10)
    assert bar_50 == "█████░░░░░  50%"

    bar_100 = render_progress_bar(100.0, width=10)
    assert bar_100 == "██████████  100%"


@pytest.mark.asyncio
async def test_complete_and_error_screens():
    video_info = VideoInfo(
        title="Test Movie",
        page_url="https://example.com",
        stream_url="https://example.com/test.m3u8",
    )

    app = VDLApp()
    async with app.run_test() as pilot:
        app.show_complete(video_info, Path("/tmp/Test Movie.mp4"))
        await pilot.pause()
        assert isinstance(app.screen, CompleteScreen)

        app.show_error("Test Title", "Test Desc", "https://example.com")
        await pilot.pause()
        assert isinstance(app.screen, ErrorScreen)
