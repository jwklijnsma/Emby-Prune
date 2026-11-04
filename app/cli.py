"""Run once without the web GUI (for an external scheduler, like the original script).

    docker run --rm -v ./config:/config emby-prune python -m app.cli [--live]

Uses the GUI's saved settings, falling back to the env vars. Without --live the
DRY_RUN setting decides.
"""
import logging
import sys
import time

from . import store
from .pruner import Runner


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    store.init_db()
    cfg = store.load_settings()
    mode = "live" if "--live" in sys.argv or not cfg["dry_run"] else "dry"
    r = Runner()
    run_id = r.start(mode, "cli")
    while r.running:
        time.sleep(0.5)
    run = store.get_run(run_id)
    sys.exit(0 if run["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
