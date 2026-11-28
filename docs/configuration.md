# Environment variables

All optional. The Emby/rule variables only seed the first-run settings; after that the GUI's `settings.json` is used.

| Variable | Default | |
|---|---|---|
| `TZ` | `UTC` | Time zone for the schedule |
| `WEB_PORT` | `8585` | GUI port |
| `WEB_USERNAME` / `WEB_PASSWORD` | – | Enable basic auth on the GUI |
| `EMBY_URL` / `EMBY_API_KEY` | – | Seed connection |
| `MOVIE_PRUNE_DAYS` (or `PRUNE_DAYS`) | 120 | |
| `MOVIE_NEVER_PLAYED_DAYS` | = movie days | |
| `TV_PRUNE_DAYS` | 90 | |
| `TV_NEVER_PLAYED_DAYS` | 180 | |
| `DRY_RUN` | `true` | Applies to scheduled runs |
| `MAX_DELETES` | 50 | 0 = unlimited |
| `KEEP_TAG` | `keep` | |
| `SCHEDULE_ENABLED` / `SCHEDULE_CRON` | `false` / `0 3 * * 1` | |
| `WEBHOOK_URL` | – | |
