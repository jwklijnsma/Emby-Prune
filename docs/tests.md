# Tests

```bash
pip install -r requirements.txt pytest pytest-cov
python -m pytest --cov=app tests
```

The suite runs offline against a fake Emby server (no real deletes): the stale
rules, any-user watch aggregation, keep tag / favourites / protected list,
dry vs live runs and the safety cap, re-checked deletes, webhooks, the
settings/history store and the REST API. CI runs it on every PR (`tests.yml`).
