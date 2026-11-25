"""The pure rule helpers in app.pruner."""
from datetime import datetime, timedelta, timezone

import pytest

from app.pruner import evaluate, human_size, item_size, item_tags, label_for, parse_iso

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
CFG = {"movies_enabled": True, "tv_enabled": True, "movie_days": 120, "movie_never_played_days": 200,
       "tv_days": 90, "tv_never_played_days": 180}


def ago(days):
    return NOW - timedelta(days=days)


@pytest.mark.parametrize("raw,expected", [
    ("2026-01-02T03:04:05Z", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
    ("2026-01-02T03:04:05.0000000Z", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
    ("2026-01-02T03:04:05", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),   # naive -> UTC
    ("2026-01-02T05:04:05+02:00", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
    ("", None), (None, None), ("not a date", None),
])
def test_parse_iso(raw, expected):
    assert parse_iso(raw) == expected


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"), (None, "0 B"), (512, "512 B"), (1536, "1.5 KB"), (5 * 1024**3, "5.0 GB"),
    (3 * 1024**5, "3072.0 TB"),   # stops at TB
])
def test_human_size(n, expected):
    assert human_size(n) == expected


def test_item_tags_merges_and_normalises():
    item = {"Tags": ["Keep ", "4K"], "TagItems": [{"Name": "KEEP"}, {"Name": ""}, {}]}
    assert item_tags(item) == {"keep", "4k"}
    assert item_tags({}) == set()


def test_item_size_prefers_size_then_media_sources():
    assert item_size({"Size": "1000", "MediaSources": [{"Size": 5}]}) == 1000
    assert item_size({"MediaSources": [{"Size": 300}, {"Size": "200"}, {}]}) == 500
    assert item_size({}) == 0


@pytest.mark.parametrize("item,expected", [
    ({"Name": "Ep", "Type": "Episode", "SeriesName": "Show", "ParentIndexNumber": 1, "IndexNumber": 2},
     "Show S01E02 - Ep"),
    ({"Name": "Special", "Type": "Episode", "SeriesName": "Show"}, "Show - Special"),
    ({"Name": "Film", "Type": "Movie", "ProductionYear": 1999}, "Film (1999)"),
    ({"Name": "Film", "Type": "Movie"}, "Film"),
    ({}, "Unknown"),
])
def test_label_for(item, expected):
    assert label_for(item) == expected


def test_movie_played_recently_is_kept():
    assert evaluate("Movie", ago(500), ago(119), CFG, NOW) is None


def test_movie_played_long_ago_is_stale():
    reason = evaluate("Movie", ago(500), ago(121), CFG, NOW)
    assert reason == "Last watched 121 days ago (limit 120d)"


def test_movie_never_played_uses_added_date():
    assert evaluate("Movie", ago(199), None, CFG, NOW) is None
    assert evaluate("Movie", ago(201), None, CFG, NOW) == "Never watched, added 201 days ago (limit 200d)"
    assert evaluate("Movie", None, None, CFG, NOW) is None   # no added date -> never stale


def test_episode_uses_tv_limits():
    assert evaluate("Episode", ago(500), ago(91), CFG, NOW).startswith("Last watched 91 days")
    assert evaluate("Episode", ago(181), None, CFG, NOW).startswith("Never watched, added 181")


def test_zero_days_disables_a_rule():
    cfg = {**CFG, "movie_days": 0, "movie_never_played_days": 0}
    assert evaluate("Movie", ago(9999), ago(9999), cfg, NOW) is None
    assert evaluate("Movie", ago(9999), None, cfg, NOW) is None


def test_a_played_item_never_falls_back_to_the_never_played_rule():
    # watched recently but added long ago: the played rule decides, so it is kept
    assert evaluate("Movie", ago(9999), ago(1), CFG, NOW) is None


def test_disabled_types_and_unknown_types_are_never_stale():
    assert evaluate("Movie", ago(999), None, {**CFG, "movies_enabled": False}, NOW) is None
    assert evaluate("Episode", ago(999), None, {**CFG, "tv_enabled": False}, NOW) is None
    assert evaluate("Series", ago(999), None, CFG, NOW) is None
    assert evaluate("Audio", ago(999), None, CFG, NOW) is None
