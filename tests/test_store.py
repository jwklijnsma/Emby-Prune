"""Settings and run history persistence (app.store)."""
import json
import os
import stat

from app import store


def test_defaults_come_from_env(monkeypatch):
    monkeypatch.setenv("PRUNE_DAYS", "30")          # legacy name still seeds movie_days
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("MAX_DELETES", "not-a-number")
    d = store.defaults()
    assert d["movie_days"] == 30
    assert d["movie_never_played_days"] == 30       # follows movie_days unless set
    assert d["dry_run"] is False
    assert d["max_deletes"] == 50                   # bad value -> default
    monkeypatch.setenv("MOVIE_PRUNE_DAYS", "45")
    assert store.defaults()["movie_days"] == 45     # specific name wins


def test_dry_run_is_the_safe_default():
    assert store.defaults()["dry_run"] is True


def test_save_and_load_roundtrip(config_dir):
    cfg = store.load_settings()
    cfg.update(emby_url="http://emby:8096", api_key="secret", tv_days=7)
    store.save_settings(cfg)
    assert store.load_settings()["tv_days"] == 7
    path = config_dir / "settings.json"
    assert json.loads(path.read_text())["api_key"] == "secret"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600   # holds the API key
    assert not (config_dir / "settings.json.tmp").exists()


def test_saved_settings_keep_new_default_keys(config_dir):
    (config_dir / "settings.json").write_text(json.dumps({"tv_days": 3}))
    cfg = store.load_settings()
    assert cfg["tv_days"] == 3 and "max_deletes" in cfg


def test_run_history_roundtrip():
    rid = store.create_run("2026-11-01T00:00:00", "manual", "dry")
    assert store.latest_run_id() is None              # still running
    store.add_items(rid, [
        {"id": "b", "label": "B movie", "type": "Movie", "reason": "old", "size": 100,
         "users": ["bob", "alice"], "action": "deleted"},
        {"id": "a", "label": "A show", "type": "Episode", "reason": "old", "size": 50, "action": "dry-run"},
    ])
    store.finish_run(rid, status="ok", candidates=2, finished="2026-11-01T00:01:00")
    run = store.get_run(rid)
    assert run["status"] == "ok" and run["candidates"] == 2
    assert [i["item_id"] for i in run["items"]] == ["a", "b"]   # ordered by type, label
    assert run["items"][1]["users"] == "bob, alice"
    assert store.latest_run_id() == rid
    assert store.list_runs()[0]["id"] == rid
    assert store.get_run(9999) is None


def test_set_action_and_recount():
    rid = store.create_run("t", "manual", "live")
    store.add_items(rid, [{"id": i, "label": i, "type": "Movie", "reason": "r", "size": 10 * n}
                          for n, i in enumerate(("x", "y", "z"), start=1)])
    store.set_item_action(rid, "x", "deleted")
    store.set_item_action(rid, "z", "deleted")
    store.recount_run(rid)
    run = store.get_run(rid)
    assert (run["deleted"], run["freed"]) == (2, 40)
