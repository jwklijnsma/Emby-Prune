# Web GUI

![Dashboard](https://github.com/jwklijnsma/Emby-Prune/blob/main/dash.png)

![Settings](https://github.com/jwklijnsma/Emby-Prune/blob/main/settings.png)

- **Dashboard** – dry run / run & delete buttons, live log, next scheduled run, last result
- **Review** – every stale item with reason, last watched (and by whom), added date, size; filter, sort, bulk **delete selected** or **protect**
- **History** – every run, browsable
- **Settings** – connection test, rules, libraries, keep tag, favourites, safety cap, schedule (cron with presets), notifications
- **Safety:** dry-run by default; max deletions per run (default 50); selected deletions re-check each item with Emby first and skip anything watched since the scan
- **Built-in scheduler** (no external cron needed)
- **Webhook notifications** – Discord, Slack, ntfy or generic JSON
- **Much faster scans** – one paged request per user instead of one request per item per user
- Optional **basic auth**, logs to `/config/logs`, run history in SQLite
