"""Settings (JSON) and run history (SQLite) persisted under CONFIG_DIR."""
import json
import os
import sqlite3
import threading

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
DB_FILE = os.path.join(CONFIG_DIR, "emby-prune.db")
_lock = threading.Lock()


def _env_bool(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    v = os.environ.get(name)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


def defaults():
    """Env vars from the original script seed the first-run settings."""
    movie_days = _env_int("MOVIE_PRUNE_DAYS", _env_int("PRUNE_DAYS", 120))
    return {
        "emby_url": os.environ.get("EMBY_URL", ""),
        "api_key": os.environ.get("EMBY_API_KEY", ""),
        "movies_enabled": _env_bool("MOVIES_ENABLED", True),
        "movie_days": movie_days,
        "movie_never_played_days": _env_int("MOVIE_NEVER_PLAYED_DAYS", movie_days),
        "tv_enabled": _env_bool("TV_ENABLED", True),
        "tv_days": _env_int("TV_PRUNE_DAYS", 90),
        "tv_never_played_days": _env_int("TV_NEVER_PLAYED_DAYS", 180),
        "dry_run": _env_bool("DRY_RUN", True),
        "schedule_enabled": _env_bool("SCHEDULE_ENABLED", False),
        "schedule_cron": os.environ.get("SCHEDULE_CRON", "0 3 * * 1"),
        "excluded_libraries": [],
        "keep_tag": os.environ.get("KEEP_TAG", "keep"),
        "protect_favorites": _env_bool("PROTECT_FAVORITES", True),
        "max_deletes": _env_int("MAX_DELETES", 50),
        "protected_items": {},  # item/series id -> label
        "webhook_url": os.environ.get("WEBHOOK_URL", ""),
        "notify_dry_runs": _env_bool("NOTIFY_DRY_RUNS", False),
    }


def load_settings():
    s = defaults()
    with _lock:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                s.update(json.load(f))
    return s


def save_settings(s):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with _lock:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
        os.chmod(SETTINGS_FILE, 0o600)


# ---------------------------------------------------------------- history db
def db():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started TEXT, finished TEXT, trigger TEXT, mode TEXT,
                status TEXT, candidates INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
                freed INTEGER DEFAULT 0, error TEXT, log TEXT
            );
            CREATE TABLE IF NOT EXISTS items (
                run_id INTEGER, item_id TEXT, series_id TEXT, label TEXT, type TEXT,
                library TEXT, path TEXT, reason TEXT, plays INTEGER, users TEXT,
                last_played TEXT, created TEXT, size INTEGER, action TEXT,
                PRIMARY KEY (run_id, item_id)
            );
            """
        )


def create_run(started, trigger, mode):
    with db() as con:
        cur = con.execute(
            "INSERT INTO runs (started, trigger, mode, status) VALUES (?,?,?,?)",
            (started, trigger, mode, "running"),
        )
        return cur.lastrowid


def finish_run(run_id, **fields):
    keys = ", ".join(f"{k}=?" for k in fields)
    with db() as con:
        con.execute(f"UPDATE runs SET {keys} WHERE id=?", (*fields.values(), run_id))


def add_items(run_id, items):
    with db() as con:
        con.executemany(
            """INSERT OR REPLACE INTO items (run_id,item_id,series_id,label,type,library,path,
               reason,plays,users,last_played,created,size,action)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (run_id, i["id"], i.get("series_id"), i["label"], i["type"], i.get("library"),
                 i.get("path"), i["reason"], i.get("plays", 0), ", ".join(i.get("users", [])),
                 i.get("last_played"), i.get("created"), i.get("size", 0), i.get("action", ""))
                for i in items
            ],
        )


def set_item_action(run_id, item_id, action):
    with db() as con:
        con.execute("UPDATE items SET action=? WHERE run_id=? AND item_id=?", (action, run_id, item_id))


def list_runs(limit=100):
    with db() as con:
        rows = con.execute(
            "SELECT id,started,finished,trigger,mode,status,candidates,deleted,freed,error "
            "FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id):
    with db() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        items = con.execute(
            "SELECT * FROM items WHERE run_id=? ORDER BY type, label", (run_id,)
        ).fetchall()
    out = dict(r)
    out["items"] = [dict(i) for i in items]
    return out


def latest_run_id():
    with db() as con:
        r = con.execute("SELECT id FROM runs WHERE status!='running' ORDER BY id DESC LIMIT 1").fetchone()
    return r["id"] if r else None


def recount_run(run_id):
    with db() as con:
        r = con.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) s FROM items WHERE run_id=? AND action='deleted'",
            (run_id,),
        ).fetchone()
        con.execute("UPDATE runs SET deleted=?, freed=? WHERE id=?", (r["n"], r["s"], run_id))
