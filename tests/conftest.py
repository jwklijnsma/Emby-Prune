"""Shared fixtures: a temp CONFIG_DIR per test and a fake Emby server.

CONFIG_DIR is set before any app module is imported, because app.main creates
its log folder and database at import time.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CONFIG_DIR", tempfile.mkdtemp(prefix="emby-prune-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
import requests  # noqa: E402

from app import store  # noqa: E402


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    """Every test gets its own settings file and database."""
    monkeypatch.setattr(store, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(store, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(store, "DB_FILE", str(tmp_path / "emby-prune.db"))
    for var in ("EMBY_URL", "EMBY_API_KEY", "PRUNE_DAYS", "MOVIE_PRUNE_DAYS", "MOVIE_NEVER_PLAYED_DAYS",
                "TV_PRUNE_DAYS", "TV_NEVER_PLAYED_DAYS", "DRY_RUN", "MAX_DELETES", "KEEP_TAG",
                "MOVIES_ENABLED", "TV_ENABLED", "PROTECT_FAVORITES"):
        monkeypatch.delenv(var, raising=False)
    store.init_db()
    return tmp_path


class FakeEmby:
    """Stands in for app.emby.EmbyClient. Items live in `self.items` (id -> item);
    per-user watch data in `self.userdata[(user_id, item_id)]`."""

    def __init__(self, items=(), users=None, libraries=None, userdata=None, series=()):
        self.items = {i["Id"]: i for i in items}
        self.series = list(series)
        self.users_ = users if users is not None else [{"Id": "u1", "Name": "alice"}, {"Id": "u2", "Name": "bob"}]
        self.libraries_ = libraries or [{"Id": "lib-movies", "Name": "Movies"},
                                        {"Id": "lib-tv", "Name": "TV"}]
        self.userdata = dict(userdata or {})
        self.deleted = []
        self.delete_status = 204
        self.gone = set()

    def system_info(self):
        return {"ServerName": "test-emby", "Version": "4.9"}

    def users(self):
        return self.users_

    def libraries(self):
        return self.libraries_

    def item_for_user(self, user_id, item_id):
        if item_id in self.gone:
            resp = requests.Response()
            resp.status_code = 404
            raise requests.HTTPError("404", response=resp)
        return {"Id": item_id, "UserData": self.userdata.get((user_id, item_id), {})}

    def iter_items(self, path, page_size=500, **params):
        if path.startswith("/Users/"):
            uid = path.split("/")[2]
            for iid, item in self.items.items():
                yield {**item, "UserData": self.userdata.get((uid, iid), {})}
            for s in self.series:
                yield {**s, "UserData": self.userdata.get((uid, s["Id"]), {})}
            return
        if params.get("IncludeItemTypes") == "Series":
            yield from self.series
            return
        types = set((params.get("IncludeItemTypes") or "").split(","))
        for item in self.items.values():
            if item.get("_lib") == params.get("ParentId") and item.get("Type") in types:
                yield item

    def delete_item(self, item_id):
        self.deleted.append(item_id)
        return self.delete_status


@pytest.fixture
def fake_emby(monkeypatch):
    """Install a FakeEmby as the client the pruner builds; returns a setter."""
    holder = {}

    def install(fake):
        holder["fake"] = fake
        monkeypatch.setattr("app.pruner.EmbyClient", lambda *a, **k: fake)
        return fake

    return install
