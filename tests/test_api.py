"""The Flask API and the cron conversion in app.main."""
import base64

import pytest

from app import main, store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "WEB_USER", "")
    monkeypatch.setattr(main, "WEB_PASS", "")
    main.app.config["TESTING"] = True
    return main.app.test_client()


@pytest.mark.parametrize("expr,dow", [
    ("0 3 * * 1", "mon"), ("0 3 * * 0", "sun"), ("0 3 * * 7", "sun"),
    ("0 3 * * 1-5", "mon-fri"), ("0 3 * * 0,6", "sun,sat"), ("0 3 * * *", "*"), ("0 3 * * */2", "*/2"),
])
def test_cron_weekdays_follow_cron_not_apscheduler(expr, dow):
    trig = main.cron_trigger(expr)
    field = next(f for f in trig.fields if f.name == "day_of_week")
    assert str(field) == dow


def test_cron_needs_five_fields():
    with pytest.raises(ValueError, match="5 fields"):
        main.cron_trigger("0 3 * *")


def test_settings_mask_the_api_key(client):
    s = store.load_settings()
    s["api_key"] = "secret"
    store.save_settings(s)
    body = client.get("/api/settings").get_json()
    assert body["api_key"] == main.MASK
    # posting the mask back must not overwrite the real key
    r = client.post("/api/settings", json={"api_key": main.MASK, "tv_days": 30})
    assert r.status_code == 200
    assert store.load_settings()["api_key"] == "secret"
    assert store.load_settings()["tv_days"] == 30


def test_settings_validation(client):
    r = client.post("/api/settings", json={"movie_days": -1, "tv_days": "x", "emby_url": "emby:8096",
                                           "schedule_cron": "nope"})
    assert r.status_code == 400
    errors = " ".join(r.get_json()["errors"])
    assert "movie_days" in errors and "tv_days" in errors and "http://" in errors and "cron" in errors
    assert store.load_settings()["movie_days"] != -1     # nothing saved


def test_schedule_is_applied(client):
    r = client.post("/api/settings", json={"schedule_enabled": True, "schedule_cron": "15 4 * * 2"})
    assert r.status_code == 200
    job = main.scheduler.get_job("prune")
    assert job is not None
    client.post("/api/settings", json={"schedule_enabled": False})
    assert main.scheduler.get_job("prune") is None


def test_run_mode_is_validated(client, monkeypatch):
    assert client.post("/api/run", json={"mode": "delete-everything"}).status_code == 400
    monkeypatch.setattr(main.runner, "start", lambda mode, trigger: None)
    assert client.post("/api/run", json={"mode": "dry"}).status_code == 409
    monkeypatch.setattr(main.runner, "start", lambda mode, trigger: 42)
    assert client.post("/api/run", json={"mode": "live"}).get_json() == {"ok": True, "run_id": 42}


def test_runs_and_detail(client):
    rid = store.create_run("t", "manual", "dry")
    assert client.get("/api/runs").get_json()[0]["id"] == rid
    assert client.get(f"/api/runs/{rid}").status_code == 200
    assert client.get("/api/runs/999").status_code == 404


def test_delete_selected_checks_input(client):
    rid = store.create_run("t", "manual", "dry")
    assert client.post(f"/api/runs/{rid}/delete", json={"ids": []}).status_code == 400
    assert client.post("/api/runs/999/delete", json={"ids": ["x"]}).status_code == 404


def test_protect_and_unprotect(client):
    r = client.post("/api/protect", json={"items": [{"id": "abc", "label": "Film"}, {"label": "no id"}]})
    assert r.get_json()["protected_items"] == {"abc": "Film"}
    assert store.load_settings()["protected_items"] == {"abc": "Film"}
    client.delete("/api/protect/abc")
    assert store.load_settings()["protected_items"] == {}


def test_basic_auth_when_configured(client, monkeypatch):
    monkeypatch.setattr(main, "WEB_USER", "admin")
    monkeypatch.setattr(main, "WEB_PASS", "pw")
    assert client.get("/api/status").status_code == 401
    bad = base64.b64encode(b"admin:nope").decode()
    assert client.get("/api/status", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    good = base64.b64encode(b"admin:pw").decode()
    r = client.get("/api/status", headers={"Authorization": f"Basic {good}"})
    assert r.status_code == 200 and r.get_json()["auth"] is True


def test_health_needs_no_auth(client, monkeypatch):
    monkeypatch.setattr(main, "WEB_USER", "admin")
    monkeypatch.setattr(main, "WEB_PASS", "pw")
    assert client.get("/healthz").status_code == 200


def test_test_connection_reports_errors(client):
    r = client.post("/api/test", json={"emby_url": "", "api_key": ""})
    assert r.status_code == 400 and "must be configured" in r.get_json()["error"]
