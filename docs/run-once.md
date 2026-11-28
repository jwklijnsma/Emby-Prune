# Run-once mode

Prefer the original "container runs, then exits" style?

```bash
docker run --rm -v ./config:/config ghcr.io/jwklijnsma/emby-prune python -m app.cli          # follows the DRY_RUN setting
docker run --rm -v ./config:/config ghcr.io/jwklijnsma/emby-prune python -m app.cli --live   # deletes
```

Exit code is non-zero when the run fails.
