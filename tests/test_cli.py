"""python -m app.cli: one run without the GUI, exit code from the result."""
import sys

import pytest

from app import cli, store
from conftest import FakeEmby


def run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["app.cli", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def test_dry_by_default_and_live_with_flag(monkeypatch, fake_emby):
    s = store.load_settings()
    s.update(emby_url="http://e", api_key="k", movie_never_played_days=1)
    store.save_settings(s)
    fake = fake_emby(FakeEmby(items=[{"Id": "m", "Name": "m", "Type": "Movie", "Path": "/m",
                                      "DateCreated": "2020-01-01T00:00:00Z", "_lib": "lib-movies"}]))
    assert run_cli(monkeypatch, []) == 0
    assert fake.deleted == [] and store.list_runs()[0]["mode"] == "dry"
    assert run_cli(monkeypatch, ["--live"]) == 0
    assert fake.deleted == ["m"] and store.list_runs()[0]["mode"] == "live"


def test_failed_run_exits_1(monkeypatch):
    assert run_cli(monkeypatch, []) == 1     # no URL / key configured
