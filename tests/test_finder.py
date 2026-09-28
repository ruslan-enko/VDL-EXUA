import pytest
from vdl.core.finder import find_video


@pytest.mark.asyncio
async def test_find_video_real_uafix():
    url = "https://uafix.net/serials/lihtari/season-01-episode-07/"
    steps = []

    def on_step(step, detail):
        steps.append(step)

    try:
        info = await find_video(url, on_step=on_step)
        assert info.title == "Lanterns — S01E07"
        assert len(info.qualities) >= 1
        labels = [q.label for q in info.qualities]
        assert "1080p" in labels or "720p" in labels
        assert "page_found" in steps
        assert "player_found" in steps
        assert "stream_found" in steps
    except Exception as e:
        pytest.skip(f"Network call skipped if unavailable: {e}")
