# Pruning rules

| | Watched by someone | Never watched |
|---|---|---|
| **Movies** | deleted when the latest play by *any* user is older than `movie_days` (120) | deleted when added more than `movie_never_played_days` (120) ago |
| **TV episodes** | deleted when the latest play by *any* user is older than `tv_days` (90) | deleted when added more than `tv_never_played_days` (180) ago |

Set any value to `0` to switch that rule off. Items are removed through the Emby API (`DELETE /Items/{id}`), so Emby cleans up both library and files.

**Never deleted:**
- libraries you untick in Settings
- items or whole series carrying the keep tag (default `keep`) in Emby
- items or series favourited by any user (toggle)
- items / series you add to the protected list from the Review page
- virtual "missing" episodes (no file)
