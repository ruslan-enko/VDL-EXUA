import pytest
from vdl.core.metadata import (
    clean_title,
    derive_video_title,
    extract_season_episode,
    sanitize_filename,
)


def test_extract_season_episode():
    assert extract_season_episode("Lanterns.S01E07.1080p") == "S01E07"
    assert extract_season_episode("season-01-episode-07") == "S01E07"
    assert extract_season_episode("1 сезон 7 серія") == "S01E07"
    assert extract_season_episode("7 серія") == "E07"
    assert extract_season_episode("Random Movie") is None


def test_clean_title():
    raw = "Ліхтарі 7 серія дивитись онлайн українською - Uafix"
    cleaned = clean_title(raw)
    assert "дивитись онлайн" not in cleaned
    assert "Uafix" not in cleaned
    assert "Ліхтарі" in cleaned


def test_derive_video_title_with_stream_and_page():
    title = derive_video_title(
        page_title="Ліхтарі 7 серія дивитись онлайн українською",
        page_url="https://uafix.net/serials/lihtari/season-01-episode-07/",
        stream_url="https://zetvideo.net/vid/1/new1/lanterns.s01e07.ua.mvo_67431/hls/index.m3u8",
    )
    assert title == "Lanterns — S01E07"


def test_sanitize_filename():
    assert sanitize_filename("Lanterns — S01E07") == "Lanterns — S01E07.mp4"
    assert sanitize_filename("Test/Movie: Special?*") == "Test-Movie- Special.mp4"
