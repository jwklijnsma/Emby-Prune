"""The thin Emby REST client: auth, paging, delete."""
import pytest

from app.emby import EmbyClient


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_requires_url_and_key():
    with pytest.raises(ValueError):
        EmbyClient("", "k")
    with pytest.raises(ValueError):
        EmbyClient("http://e", "")


def test_auth_both_ways_and_trailing_slash():
    c = EmbyClient("http://emby:8096/", "KEY")
    assert c.url == "http://emby:8096"
    assert c.s.params == {"api_key": "KEY"} and c.s.headers["X-Emby-Token"] == "KEY"


def test_iter_items_pages_until_total(monkeypatch):
    c = EmbyClient("http://e", "k")
    pages = []

    def get(url, params, timeout):
        pages.append(params["StartIndex"])
        start, limit = params["StartIndex"], params["Limit"]
        items = [{"Id": str(i)} for i in range(start, min(start + limit, 5))]
        return FakeResp({"Items": items, "TotalRecordCount": 5})

    monkeypatch.setattr(c.s, "get", get)
    assert [i["Id"] for i in c.iter_items("/Items", page_size=2, Recursive="true")] == list("01234")
    assert pages == [0, 2, 4]


def test_iter_items_stops_on_empty_page(monkeypatch):
    c = EmbyClient("http://e", "k")
    monkeypatch.setattr(c.s, "get", lambda *a, **k: FakeResp({"Items": [], "TotalRecordCount": 99}))
    assert list(c.iter_items("/Items")) == []


def test_libraries_accepts_both_shapes(monkeypatch):
    c = EmbyClient("http://e", "k")
    monkeypatch.setattr(c.s, "get", lambda *a, **k: FakeResp({"Items": [{"Id": "1"}]}))
    assert c.libraries() == [{"Id": "1"}]
    monkeypatch.setattr(c.s, "get", lambda *a, **k: FakeResp([{"Id": "2"}]))
    assert c.libraries() == [{"Id": "2"}]


def test_errors_raise(monkeypatch):
    c = EmbyClient("http://e", "k")
    monkeypatch.setattr(c.s, "get", lambda *a, **k: FakeResp({}, 401))
    with pytest.raises(RuntimeError):
        c.users()


def test_delete_returns_status(monkeypatch):
    c = EmbyClient("http://e", "k")
    seen = {}
    monkeypatch.setattr(c.s, "delete", lambda url, timeout: seen.update(url=url) or FakeResp(None, 204))
    assert c.delete_item("abc") == 204 and seen["url"] == "http://e/Items/abc"
