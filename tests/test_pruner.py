"""Runner.scan and the dry/live/delete-selected jobs, against a fake Emby."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import pruner, store
from app.pruner import Runner
from conftest import FakeEmby

NOW = datetime.now(timezone.utc)


def iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def movie(iid, added=500, lib="lib-movies", **kw):
    return {"Id": iid, "Name": iid, "Type": "Movie", "Path": f"/m/{iid}.mkv", "DateCreated": iso(added),
            "_lib": lib, "Size": 1000, **kw}


def episode(iid, series="s1", added=500, **kw):
    return {"Id": iid, "Name": iid, "Type": "Episode", "Path": f"/tv/{iid}.mkv", "DateCreated": iso(added),
            "SeriesId": series, "SeriesName": "Show", "ParentIndexNumber": 1, "IndexNumber": 1,
            "_lib": "lib-tv", "Size": 500, **kw}


def played(days):
    return {"LastPlayedDate": iso(days), "PlayCount": 1}


def cfg(**over):
    c = store.load_settings()
    c.update(movie_days=100, movie_never_played_days=200, tv_days=100, tv_never_played_days=200,
             keep_tag="keep", protect_favorites=True, max_deletes=50, excluded_libraries=[],
             protected_items={}, emby_url="http://emby", api_key="k")
    c.update(over)
    return c


def ids(cands):
    return sorted(c["id"] for c in cands)


def test_any_user_watching_keeps_an_item():
    fake = FakeEmby(items=[movie("m1"), movie("m2")],
                    userdata={("u1", "m1"): played(300),     # alice: long ago
                              ("u2", "m1"): played(5),       # bob: recently -> keep
                              ("u1", "m2"): played(300)})
    out = Runner().scan(fake, cfg())
    assert ids(out) == ["m2"]
    c = out[0]
    assert c["users"] == ["alice"] and c["plays"] == 1 and c["reason"].startswith("Last watched 300")


def test_never_played_items_go_after_the_added_limit():
    fake = FakeEmby(items=[movie("old", added=250), movie("new", added=10)])
    assert ids(Runner().scan(fake, cfg())) == ["old"]


def test_keep_tag_on_item_or_series_protects():
    fake = FakeEmby(items=[movie("tagged", Tags=["Keep"]), episode("e1", series="kept-show"),
                           episode("e2", series="other")],
                    series=[{"Id": "kept-show", "Type": "Series", "Tags": ["keep"]},
                            {"Id": "other", "Type": "Series", "Tags": []}])
    assert ids(Runner().scan(fake, cfg())) == ["e2"]


def test_favourites_protect_item_and_whole_series():
    fake = FakeEmby(items=[movie("fav"), episode("e1", series="favshow"), movie("plain")],
                    series=[{"Id": "favshow", "Type": "Series"}],
                    userdata={("u2", "fav"): {"IsFavorite": True},
                              ("u1", "favshow"): {"IsFavorite": True}})
    assert ids(Runner().scan(fake, cfg())) == ["plain"]
    assert ids(Runner().scan(fake, cfg(protect_favorites=False))) == ["e1", "fav", "plain"]


def test_protected_list_and_excluded_libraries():
    fake = FakeEmby(items=[movie("m1"), movie("m2"), episode("e1", series="s9")])
    out = Runner().scan(fake, cfg(protected_items={"m1": "M1", "s9": "Show"}))
    assert ids(out) == ["m2"]
    assert ids(Runner().scan(fake, cfg(excluded_libraries=["lib-movies"]))) == ["e1"]


def test_virtual_items_and_disabled_types_are_skipped():
    fake = FakeEmby(items=[movie("nopath", Path=""), movie("m"), episode("e")])
    assert ids(Runner().scan(fake, cfg())) == ["e", "m"]
    assert ids(Runner().scan(fake, cfg(tv_enabled=False))) == ["m"]
    assert Runner().scan(fake, cfg(tv_enabled=False, movies_enabled=False)) == []


def test_no_users_aborts():
    with pytest.raises(RuntimeError, match="No users"):
        Runner().scan(FakeEmby(users=[]), cfg())


def test_cancel_stops_a_scan():
    r = Runner()
    r.cancel_event.set()
    with pytest.raises(pruner.Cancelled):
        r.scan(FakeEmby(items=[movie("m")]), cfg())


def wait_done(r, timeout=10):
    end = time.time() + timeout
    while r.running and time.time() < end:
        time.sleep(0.02)
    assert not r.running


def test_dry_run_deletes_nothing(fake_emby):
    store.save_settings(cfg())
    fake = fake_emby(FakeEmby(items=[movie("m1"), movie("m2")]))
    r = Runner()
    rid = r.start("dry")
    assert r.start("dry") is None            # one job at a time
    wait_done(r)
    run = store.get_run(rid)
    assert fake.deleted == []
    assert run["status"] == "ok" and run["candidates"] == 2 and run["deleted"] == 0
    assert {i["action"] for i in run["items"]} == {"dry-run"}


def test_live_run_respects_the_safety_cap(fake_emby):
    store.save_settings(cfg(max_deletes=2))
    fake = fake_emby(FakeEmby(items=[movie(f"m{n}") for n in range(5)]))
    r = Runner()
    rid = r.start("live")
    wait_done(r)
    run = store.get_run(rid)
    assert len(fake.deleted) == 2
    assert (run["deleted"], run["freed"]) == (2, 2000)
    assert sum(i["action"] == "skipped (max per run)" for i in run["items"]) == 3
    assert "Safety cap reached" in run["log"]


def test_failed_deletes_are_recorded(fake_emby):
    store.save_settings(cfg())
    fake = fake_emby(FakeEmby(items=[movie("m1")]))
    fake.delete_status = 500
    r = Runner()
    rid = r.start("live")
    wait_done(r)
    run = store.get_run(rid)
    assert run["deleted"] == 0 and run["items"][0]["action"] == "failed (HTTP 500)"


def test_errors_end_the_run_cleanly():
    store.save_settings(cfg(emby_url="", api_key=""))   # the real EmbyClient refuses this
    r = Runner()
    rid = r.start("dry")
    wait_done(r)
    run = store.get_run(rid)
    assert run["status"] == "error" and "must be configured" in run["error"]
    assert r.lock.acquire(blocking=False)      # lock released for the next run


def test_delete_selected_rechecks_each_item(fake_emby):
    store.save_settings(cfg())
    fake = fake_emby(FakeEmby(items=[movie(i) for i in ("stale", "watched", "gone", "prot", "done")]))
    rid = store.create_run("t", "manual", "dry")
    store.add_items(rid, [
        {"id": i, "label": i, "type": "Movie", "reason": "r", "size": 1000, "created": iso(500),
         "action": "deleted" if i == "done" else "dry-run"}
        for i in ("stale", "watched", "gone", "prot", "done")])
    s = cfg()
    s["protected_items"] = {"prot": "Prot"}
    store.save_settings(s)
    fake.userdata[("u1", "watched")] = played(1)        # watched since the scan
    fake.gone.add("gone")
    r = Runner()
    r.delete_selected(rid, ["stale", "watched", "gone", "prot", "done", "not-in-run"])
    wait_done(r)
    actions = {i["item_id"]: i["action"] for i in store.get_run(rid)["items"]}
    assert fake.deleted == ["stale"]
    assert actions == {"stale": "deleted", "watched": "skipped (no longer stale)", "gone": "gone",
                       "prot": "skipped (protected)", "done": "deleted"}
    run = store.get_run(rid)
    assert (run["deleted"], run["freed"]) == (2, 2000)


@pytest.mark.parametrize("url,expect", [
    ("https://discord.com/api/webhooks/1/x", "json:content"),
    ("https://ntfy.sh/topic", "data"),
    ("https://hooks.slack.com/services/x", "json:text"),
    ("https://example.com/hook", "data"),
])
def test_webhook_formats(monkeypatch, url, expect):
    sent = {}
    monkeypatch.setattr(pruner.requests, "post", lambda u, **kw: sent.update(kw, url=u))
    Runner()._notify({"webhook_url": url}, 7, "live", "schedule",
                     {"status": "ok", "candidates": 3, "deleted": 2, "freed": 2048, "error": None})
    assert sent["url"] == url
    kind, _, key = expect.partition(":")
    payload = sent.get(kind)
    text = payload[key] if key else (payload.decode() if isinstance(payload, bytes) else payload)
    assert "run #7" in text and "2 deleted" in text and "2.0 KB" in text


def test_dry_runs_notify_only_when_asked(monkeypatch):
    calls = []
    monkeypatch.setattr(pruner.requests, "post", lambda *a, **k: calls.append(a))
    res = {"status": "ok", "candidates": 0, "deleted": 0, "freed": 0}
    Runner()._notify({"webhook_url": "https://x/hook"}, 1, "dry", "manual", res)
    Runner()._notify({"webhook_url": ""}, 1, "live", "manual", res)
    assert calls == []
    Runner()._notify({"webhook_url": "https://x/hook", "notify_dry_runs": True}, 1, "dry", "manual", res)
    assert len(calls) == 1


def test_webhook_failure_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(pruner.requests, "post", boom)
    Runner()._notify({"webhook_url": "https://x"}, 1, "live", "manual",
                     {"status": "ok", "candidates": 0, "deleted": 0, "freed": 0})


def test_cancelled_live_run(fake_emby):
    store.save_settings(cfg())
    fake = fake_emby(FakeEmby(items=[movie(f"m{n}") for n in range(3)]))
    r = Runner()
    orig = fake.delete_item

    def delete_then_cancel(iid):
        r.cancel()
        return orig(iid)

    fake.delete_item = delete_then_cancel
    rid = r.start("live")
    wait_done(r)
    assert store.get_run(rid)["status"] == "cancelled"
    assert len(fake.deleted) == 1
