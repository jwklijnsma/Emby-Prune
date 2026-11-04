"""Thin Emby REST API client."""
import requests


class EmbyClient:
    def __init__(self, url: str, api_key: str, timeout: int = 60):
        if not url or not api_key:
            raise ValueError("Emby URL and API key must be configured")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.s = requests.Session()
        # Same auth style as the original script (query param), plus the header.
        self.s.params = {"api_key": api_key}
        self.s.headers.update({"X-Emby-Token": api_key, "Accept": "application/json"})

    def _get(self, path, **params):
        r = self.s.get(self.url + path, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def system_info(self):
        return self._get("/System/Info")

    def users(self):
        return self._get("/Users")

    def libraries(self):
        data = self._get("/Library/MediaFolders")
        return data.get("Items", []) if isinstance(data, dict) else data

    def item_for_user(self, user_id, item_id):
        return self._get(f"/Users/{user_id}/Items/{item_id}")

    def iter_items(self, path, page_size=500, **params):
        """Page through an Items endpoint, yielding each item."""
        start = 0
        while True:
            data = self._get(path, StartIndex=start, Limit=page_size, **params)
            items = data.get("Items", [])
            yield from items
            start += len(items)
            if not items or start >= data.get("TotalRecordCount", 0):
                break

    def delete_item(self, item_id) -> int:
        r = self.s.delete(f"{self.url}/Items/{item_id}", timeout=self.timeout)
        return r.status_code
