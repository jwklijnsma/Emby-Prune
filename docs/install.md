# Install

Prebuilt image (amd64 + arm64): `ghcr.io/jwklijnsma/emby-prune:latest`

```bash
git clone https://github.com/jwklijnsma/emby-prune && cd emby-prune
# edit docker-compose.yml: TZ, WEB_USERNAME / WEB_PASSWORD
docker compose up -d            # uses the GHCR image
# or build locally:  docker compose up -d --build
```

Open `http://<host>:8585`, go to **Settings**, enter your Emby URL (e.g. `http://192.168.1.10:8096/emby`) and an API key (Emby Dashboard → Advanced → API Keys), click **Test connection**, pick libraries, save.

Then run a **Dry run**, check the **Review** tab, and only then either delete selected items, use **Run & delete**, or untick "Dry-run scheduled runs" to let the schedule delete.

Unraid: use `ghcr.io/jwklijnsma/emby-prune:latest` as the repository, map `/config` to `/mnt/user/appdata/emby-prune` and port 8585.
