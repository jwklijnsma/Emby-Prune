"""Emby Prune — web GUI, REST API and scheduler."""
import hmac
import logging
import os
import re
import sys
from functools import wraps
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, Response, jsonify, render_template, request

from . import store
from .emby import EmbyClient
from .pruner import Runner

MASK = "••••••••"
TZ = ZoneInfo(os.environ.get("TZ", "UTC"))
WEB_USER = os.environ.get("WEB_USERNAME", "")
WEB_PASS = os.environ.get("WEB_PASSWORD", "")

# ---------------------------------------------------------------- logging
os.makedirs(os.path.join(store.CONFIG_DIR, "logs"), exist_ok=True)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
root = logging.getLogger()
root.setLevel(logging.INFO)
for h in (logging.StreamHandler(sys.stdout),
          RotatingFileHandler(os.path.join(store.CONFIG_DIR, "logs", "emby-prune.log"),
                              maxBytes=2_000_000, backupCount=5)):
    h.setFormatter(fmt)
    root.addHandler(h)

store.init_db()
runner = Runner()
scheduler = BackgroundScheduler(timezone=TZ)
scheduler.start()
app = Flask(__name__)


_DOW = ["sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def cron_trigger(expr):
    """Standard 5-field cron -> APScheduler trigger.

    APScheduler 3 numbers weekdays 0=Monday, unlike cron (0/7=Sunday, 1=Monday),
    so numeric weekdays are converted to names first.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)}")
    minute, hour, day, month, dow = fields
    dow = re.sub(r"(?<![/\d])([0-7])(?!\d)", lambda m: _DOW[int(m.group(1))], dow)
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow, timezone=TZ)


def apply_schedule(cfg=None):
    cfg = cfg or store.load_settings()
    if scheduler.get_job("prune"):
        scheduler.remove_job("prune")
    if cfg.get("schedule_enabled"):
        scheduler.add_job(
            lambda: runner.start("dry" if store.load_settings()["dry_run"] else "live", "schedule"),
            cron_trigger(cfg["schedule_cron"]),
            id="prune", misfire_grace_time=3600, coalesce=True,
        )


try:
    apply_schedule()
except Exception as e:  # noqa: BLE001
    logging.error("Invalid schedule in settings: %s", e)


# ---------------------------------------------------------------- auth
def auth(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if WEB_USER and WEB_PASS:
            a_ = request.authorization
            if not (a_ and hmac.compare_digest(a_.username or "", WEB_USER)
                    and hmac.compare_digest(a_.password or "", WEB_PASS)):
                return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Emby Prune"'})
        return fn(*a, **kw)
    return wrapper


def public_settings(cfg):
    out = dict(cfg)
    out["api_key"] = MASK if cfg.get("api_key") else ""
    return out


# ---------------------------------------------------------------- pages
@app.get("/")
@auth
def index():
    return render_template("index.html")


@app.get("/healthz")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- api
@app.get("/api/settings")
@auth
def get_settings():
    return jsonify(public_settings(store.load_settings()))


@app.post("/api/settings")
@auth
def post_settings():
    cfg = store.load_settings()
    body = request.get_json(force=True)
    errors = []
    for k in ("emby_url", "keep_tag", "schedule_cron", "webhook_url"):
        if k in body:
            cfg[k] = str(body[k]).strip()
    if "api_key" in body and body["api_key"] != MASK:
        cfg["api_key"] = str(body["api_key"]).strip()
    for k in ("movie_days", "movie_never_played_days", "tv_days", "tv_never_played_days", "max_deletes"):
        if k in body:
            try:
                v = int(body[k])
                if v < 0:
                    raise ValueError
                cfg[k] = v
            except (TypeError, ValueError):
                errors.append(f"{k} must be a whole number ≥ 0")
    for k in ("movies_enabled", "tv_enabled", "dry_run", "schedule_enabled", "protect_favorites", "notify_dry_runs"):
        if k in body:
            cfg[k] = bool(body[k])
    if "excluded_libraries" in body:
        cfg["excluded_libraries"] = [str(x) for x in body["excluded_libraries"]]
    if cfg["emby_url"] and not cfg["emby_url"].startswith(("http://", "https://")):
        errors.append("Emby URL must start with http:// or https://")
    try:
        cron_trigger(cfg["schedule_cron"])
    except Exception as e:  # noqa: BLE001
        errors.append(f"Invalid cron expression: {e}")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    store.save_settings(cfg)
    apply_schedule(cfg)
    return jsonify({"ok": True, "settings": public_settings(cfg)})


@app.post("/api/test")
@auth
def test_connection():
    cfg = store.load_settings()
    body = request.get_json(silent=True) or {}
    url = body.get("emby_url") or cfg["emby_url"]
    key = body.get("api_key") if body.get("api_key") not in (None, "", MASK) else cfg["api_key"]
    try:
        c = EmbyClient(url, key, timeout=15)
        info = c.system_info()
        users = c.users()
        libs = c.libraries()
        return jsonify({"ok": True, "server": info.get("ServerName"), "version": info.get("Version"),
                        "users": len(users),
                        "libraries": [{"id": l["Id"], "name": l["Name"], "type": l.get("CollectionType")} for l in libs]})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 400


@app.get("/api/libraries")
@auth
def libraries():
    cfg = store.load_settings()
    try:
        libs = EmbyClient(cfg["emby_url"], cfg["api_key"], timeout=15).libraries()
        return jsonify([{"id": l["Id"], "name": l["Name"], "type": l.get("CollectionType")} for l in libs])
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400


@app.post("/api/run")
@auth
def run():
    mode = (request.get_json(silent=True) or {}).get("mode", "dry")
    if mode not in ("dry", "live"):
        return jsonify({"ok": False, "error": "mode must be dry or live"}), 400
    run_id = runner.start(mode, "manual")
    if run_id is None:
        return jsonify({"ok": False, "error": "A run is already in progress"}), 409
    return jsonify({"ok": True, "run_id": run_id})


@app.post("/api/cancel")
@auth
def cancel():
    runner.cancel()
    return jsonify({"ok": True})


@app.get("/api/status")
@auth
def status():
    job = scheduler.get_job("prune")
    runs = store.list_runs(1)
    return jsonify({
        "running": runner.running,
        "current_run": runner.current_run,
        "log": runner.lines[-400:] if (runner.lines or runner.running) else
               ((store.get_run(runs[0]["id"]) or {}).get("log") or "").splitlines()[-400:] if runs else [],
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "last_run": runs[0] if runs else None,
        "latest_finished": store.latest_run_id(),
        "auth": bool(WEB_USER and WEB_PASS),
    })


@app.get("/api/runs")
@auth
def runs():
    return jsonify(store.list_runs(200))


@app.get("/api/runs/<int:run_id>")
@auth
def run_detail(run_id):
    r = store.get_run(run_id)
    return (jsonify(r), 200) if r else (jsonify({"error": "not found"}), 404)


@app.post("/api/runs/<int:run_id>/delete")
@auth
def delete_selected(run_id):
    ids = (request.get_json(force=True) or {}).get("ids", [])
    if not ids:
        return jsonify({"ok": False, "error": "Nothing selected"}), 400
    if not store.get_run(run_id):
        return jsonify({"ok": False, "error": "Run not found"}), 404
    if runner.delete_selected(run_id, ids) is None:
        return jsonify({"ok": False, "error": "A run is already in progress"}), 409
    return jsonify({"ok": True})


@app.post("/api/protect")
@auth
def protect():
    body = request.get_json(force=True) or {}
    cfg = store.load_settings()
    for entry in body.get("items", []):
        if entry.get("id"):
            cfg["protected_items"][str(entry["id"])] = str(entry.get("label") or entry["id"])
    store.save_settings(cfg)
    return jsonify({"ok": True, "protected_items": cfg["protected_items"]})


@app.delete("/api/protect/<item_id>")
@auth
def unprotect(item_id):
    cfg = store.load_settings()
    cfg["protected_items"].pop(item_id, None)
    store.save_settings(cfg)
    return jsonify({"ok": True, "protected_items": cfg["protected_items"]})


def serve():
    from waitress import serve as w
    port = int(os.environ.get("WEB_PORT", "8585"))
    logging.info("Emby Prune web GUI on port %s (TZ=%s, auth=%s)", port, TZ, bool(WEB_USER and WEB_PASS))
    w(app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    serve()
