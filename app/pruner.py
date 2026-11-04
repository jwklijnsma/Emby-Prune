"""Pruning engine — the forum script's any-user watch logic, with extras.

Rules (a value of 0 days disables that rule):
  Movies   played      -> stale when the most recent play by ANY user is older than movie_days
           never played -> stale when added longer ago than movie_never_played_days
  Episodes played      -> stale when the most recent play by ANY user is older than tv_days
           never played -> stale when added longer ago than tv_never_played_days

Never touched: excluded libraries, items/series tagged with keep_tag, items/series
favourited by any user (if enabled), and items/series on the protected list.
"""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

import requests

from . import store
from .emby import EmbyClient

log = logging.getLogger("emby-prune")


class Cancelled(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def item_tags(item):
    tags = list(item.get("Tags") or [])
    tags += [t.get("Name", "") for t in item.get("TagItems") or []]
    return {t.strip().lower() for t in tags if t}


def item_size(item):
    if item.get("Size"):
        return int(item["Size"])
    return sum(int(ms.get("Size") or 0) for ms in item.get("MediaSources") or [])


def label_for(item):
    name = item.get("Name", "Unknown")
    if item.get("Type") == "Episode" and item.get("SeriesName"):
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        if s is not None and e is not None:
            return f"{item['SeriesName']} S{s:02d}E{e:02d} - {name}"
        return f"{item['SeriesName']} - {name}"
    year = item.get("ProductionYear")
    return f"{name} ({year})" if year else name


def evaluate(item_type, created, last_played, cfg, now):
    """Return a reason string when the item is stale, else None."""
    if item_type == "Movie":
        if not cfg["movies_enabled"]:
            return None
        played_days, never_days = cfg["movie_days"], cfg["movie_never_played_days"]
    elif item_type == "Episode":
        if not cfg["tv_enabled"]:
            return None
        played_days, never_days = cfg["tv_days"], cfg["tv_never_played_days"]
    else:
        return None

    if last_played:
        if played_days > 0 and last_played < now - timedelta(days=played_days):
            ago = (now - last_played).days
            return f"Last watched {ago} days ago (limit {played_days}d)"
    elif never_days > 0 and created and created < now - timedelta(days=never_days):
        ago = (now - created).days
        return f"Never watched, added {ago} days ago (limit {never_days}d)"
    return None


class Runner:
    """Runs one job at a time in a background thread and keeps a live log."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.running = False
        self.current_run = None
        self.lines = []

    # ------------------------------------------------------------- logging
    def log(self, msg):
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{stamp}  {msg}"
        self.lines.append(line)
        if len(self.lines) > 5000:
            self.lines = self.lines[-4000:]
        log.info(msg)

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    # ------------------------------------------------------------- public
    def start(self, mode, trigger="manual"):
        """mode: 'dry' or 'live'. Returns run id or None if busy."""
        if not self.lock.acquire(blocking=False):
            return None
        self.running = True
        self.cancel_event.clear()
        self.lines = []
        run_id = store.create_run(utcnow().isoformat(), trigger, mode)
        self.current_run = run_id
        threading.Thread(target=self._job, args=(run_id, mode, trigger), daemon=True).start()
        return run_id

    def cancel(self):
        self.cancel_event.set()

    def delete_selected(self, run_id, item_ids):
        """Delete chosen items from an earlier scan, re-checking each one first."""
        if not self.lock.acquire(blocking=False):
            return None
        self.running = True
        self.cancel_event.clear()
        self.lines = []
        self.current_run = run_id
        threading.Thread(target=self._delete_job, args=(run_id, item_ids), daemon=True).start()
        return run_id

    # ------------------------------------------------------------- jobs
    def _job(self, run_id, mode, trigger):
        cfg = store.load_settings()
        result = dict(status="ok", candidates=0, deleted=0, freed=0, error=None)
        try:
            label = "DRY RUN — nothing will be deleted" if mode == "dry" else "LIVE RUN — stale items will be deleted"
            self.log(f"Run #{run_id} started ({trigger}): {label}")
            client = EmbyClient(cfg["emby_url"], cfg["api_key"])
            candidates = self.scan(client, cfg)
            result["candidates"] = len(candidates)
            for c in candidates:
                c["action"] = "dry-run" if mode == "dry" else "pending"

            if mode == "live" and candidates:
                cap = cfg["max_deletes"]
                for n, c in enumerate(candidates):
                    if cap and n >= cap:
                        c["action"] = "skipped (max per run)"
                        continue
                    self.check_cancel()
                    c["action"] = self._delete(client, c)
                    if c["action"] == "deleted":
                        result["deleted"] += 1
                        result["freed"] += c.get("size", 0)
                if cap and len(candidates) > cap:
                    self.log(f"Safety cap reached: deleted at most {cap}; "
                             f"{len(candidates) - cap} left for the next run")
            store.add_items(run_id, candidates)
            self.log(f"Done. Candidates: {result['candidates']}, deleted: {result['deleted']}, "
                     f"freed: {human_size(result['freed'])}")
        except Cancelled:
            result["status"] = "cancelled"
            self.log("Run cancelled.")
        except Exception as e:  # noqa: BLE001
            result["status"] = "error"
            result["error"] = str(e)
            self.log(f"ERROR: {e}")
            log.exception("run failed")
        finally:
            store.finish_run(run_id, finished=utcnow().isoformat(), log="\n".join(self.lines), **result)
            self._notify(cfg, run_id, mode, trigger, result)
            self.running = False
            self.lock.release()

    def _delete_job(self, run_id, item_ids):
        cfg = store.load_settings()
        try:
            run = store.get_run(run_id)
            wanted = {i["item_id"]: i for i in run["items"] if i["item_id"] in set(item_ids)}
            self.log(f"Deleting {len(wanted)} selected item(s) from scan #{run_id} (re-checking each first)")
            client = EmbyClient(cfg["emby_url"], cfg["api_key"])
            users = client.users()
            now = utcnow()
            for iid, row in wanted.items():
                self.check_cancel()
                if row["action"] == "deleted":
                    continue
                if iid in cfg["protected_items"] or (row["series_id"] and row["series_id"] in cfg["protected_items"]):
                    store.set_item_action(run_id, iid, "skipped (protected)")
                    self.log(f"[SKIP] {row['label']}: protected")
                    continue
                last = None
                try:
                    for u in users:
                        ud = (client.item_for_user(u["Id"], iid).get("UserData") or {})
                        lp = parse_iso(ud.get("LastPlayedDate"))
                        if lp and (last is None or lp > last):
                            last = lp
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        store.set_item_action(run_id, iid, "gone")
                        self.log(f"[GONE] {row['label']}: no longer in Emby")
                        continue
                    raise
                if not evaluate(row["type"], parse_iso(row["created"]), last,
                                {**cfg, "movies_enabled": True, "tv_enabled": True}, now):
                    store.set_item_action(run_id, iid, "skipped (no longer stale)")
                    self.log(f"[SKIP] {row['label']}: watched since the scan / no longer stale")
                    continue
                action = self._delete(client, {"id": iid, "label": row["label"]})
                store.set_item_action(run_id, iid, action)
            store.recount_run(run_id)
            self.log("Selected deletions finished.")
        except Cancelled:
            store.recount_run(run_id)
            self.log("Cancelled.")
        except Exception as e:  # noqa: BLE001
            self.log(f"ERROR: {e}")
            log.exception("delete job failed")
        finally:
            self.running = False
            self.lock.release()

    def _delete(self, client, c):
        try:
            code = client.delete_item(c["id"])
        except Exception as e:  # noqa: BLE001
            self.log(f"[FAIL] {c['label']}: {e}")
            return "failed"
        if code in (200, 204):
            self.log(f"[DELETED] {c['label']}")
            return "deleted"
        self.log(f"[FAIL] {c['label']}: HTTP {code}")
        return f"failed (HTTP {code})"

    # ------------------------------------------------------------- scan
    def scan(self, client, cfg):
        now = utcnow()
        keep_tag = (cfg.get("keep_tag") or "").strip().lower()
        protected = set(cfg.get("protected_items") or {})
        excluded = set(cfg.get("excluded_libraries") or [])

        info = client.system_info()
        self.log(f"Connected to {info.get('ServerName', 'Emby')} (v{info.get('Version', '?')})")
        users = client.users()
        self.log(f"{len(users)} user(s): {', '.join(u.get('Name', '?') for u in users)}")
        if not users:
            raise RuntimeError("No users found — aborting")

        libs = client.libraries()
        active = [l for l in libs if l["Id"] not in excluded]
        skipped = [l["Name"] for l in libs if l["Id"] in excluded]
        self.log(f"Libraries scanned: {', '.join(l['Name'] for l in active) or '(none)'}")
        if skipped:
            self.log(f"Libraries excluded: {', '.join(skipped)}")

        # 1) Aggregate watch data across ALL users (one paged query per user,
        #    instead of one request per item per user like the original script).
        agg = {}
        for u in users:
            self.check_cancel()
            n = 0
            for it in client.iter_items(
                f"/Users/{u['Id']}/Items", Recursive="true",
                IncludeItemTypes="Movie,Episode,Series", EnableImages="false",
                EnableUserData="true",
            ):
                ud = it.get("UserData") or {}
                lp = parse_iso(ud.get("LastPlayedDate"))
                pc = ud.get("PlayCount") or 0
                fav = bool(ud.get("IsFavorite"))
                if not (lp or pc or fav):
                    continue
                a = agg.setdefault(it["Id"], {"last": None, "plays": 0, "fav": False, "users": []})
                if lp and (a["last"] is None or lp > a["last"]):
                    a["last"] = lp
                a["plays"] += pc
                a["fav"] = a["fav"] or fav
                if lp or pc:
                    a["users"].append(u.get("Name", "?"))
                    n += 1
            self.log(f"  {u.get('Name')}: {n} watched item(s)")

        # 2) Series carrying the keep tag protect all their episodes.
        keep_series = set()
        if keep_tag and cfg["tv_enabled"]:
            for s in client.iter_items("/Items", Recursive="true", IncludeItemTypes="Series", Fields="Tags"):
                if keep_tag in item_tags(s):
                    keep_series.add(s["Id"])
            if keep_series:
                self.log(f"{len(keep_series)} series tagged '{keep_tag}' are protected")

        types = [t for t, on in (("Movie", cfg["movies_enabled"]), ("Episode", cfg["tv_enabled"])) if on]
        if not types:
            self.log("Movies and TV are both disabled — nothing to do.")
            return []

        # 3) Walk every included library and apply the rules.
        candidates, seen = [], set()
        counts = {"scanned": 0, "tag": 0, "fav": 0, "protected": 0}
        for lib in active:
            for it in client.iter_items(
                "/Items", ParentId=lib["Id"], Recursive="true", IncludeItemTypes=",".join(types),
                Fields="Path,DateCreated,Tags,MediaSources,SeriesId,SeriesName,"
                       "ParentIndexNumber,IndexNumber,ProductionYear",
                EnableImages="false",
            ):
                self.check_cancel()
                iid = it["Id"]
                if iid in seen or not it.get("Path"):  # skip duplicates and virtual/missing episodes
                    continue
                seen.add(iid)
                counts["scanned"] += 1
                sid = it.get("SeriesId")
                ud = agg.get(iid, {})

                reason = evaluate(it.get("Type"), parse_iso(it.get("DateCreated")), ud.get("last"), cfg, now)
                if not reason:
                    continue
                if keep_tag and (keep_tag in item_tags(it) or sid in keep_series):
                    counts["tag"] += 1
                    continue
                if cfg["protect_favorites"] and (ud.get("fav") or agg.get(sid, {}).get("fav")):
                    counts["fav"] += 1
                    continue
                if iid in protected or (sid and sid in protected):
                    counts["protected"] += 1
                    continue

                c = {
                    "id": iid, "series_id": sid, "type": it.get("Type"), "label": label_for(it),
                    "library": lib.get("Name"), "path": it.get("Path"), "reason": reason,
                    "plays": ud.get("plays", 0), "users": sorted(set(ud.get("users", []))),
                    "last_played": ud["last"].isoformat() if ud.get("last") else None,
                    "created": it.get("DateCreated"), "size": item_size(it),
                }
                candidates.append(c)
                self.log(f"[STALE] {c['label']} — {reason}")

        candidates.sort(key=lambda c: (c["type"], c["label"].lower()))
        total = sum(c["size"] for c in candidates)
        self.log(f"Scanned {counts['scanned']} items; {len(candidates)} stale ({human_size(total)}). "
                 f"Kept by tag: {counts['tag']}, favourite: {counts['fav']}, protected list: {counts['protected']}")
        return candidates

    # ------------------------------------------------------------- notify
    def _notify(self, cfg, run_id, mode, trigger, result):
        url = (cfg.get("webhook_url") or "").strip()
        if not url or (mode == "dry" and not cfg.get("notify_dry_runs")):
            return
        text = (f"Emby Prune run #{run_id} ({'dry run' if mode == 'dry' else 'live'}, {trigger}): "
                f"{result['status']} — {result['candidates']} stale, {result['deleted']} deleted, "
                f"{human_size(result['freed'])} freed")
        if result.get("error"):
            text += f"\nError: {result['error']}"
        try:
            if "discord.com/api/webhooks" in url:
                requests.post(url, json={"content": text}, timeout=15)
            elif "ntfy" in url:
                requests.post(url, data=text.encode(), headers={"Title": "Emby Prune"}, timeout=15)
            elif "hooks.slack.com" in url:
                requests.post(url, json={"text": text}, timeout=15)
            else:
                requests.post(url, data=json.dumps({"text": text, "run_id": run_id, **result}),
                              headers={"Content-Type": "application/json"}, timeout=15)
        except Exception as e:  # noqa: BLE001
            log.warning("webhook failed: %s", e)
