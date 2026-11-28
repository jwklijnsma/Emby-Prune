# API

The GUI is a thin client over a JSON API (same basic auth): `GET /api/status`, `POST /api/run {"mode":"dry"|"live"}`, `POST /api/cancel`, `GET /api/runs`, `GET /api/runs/<id>`, `POST /api/runs/<id>/delete {"ids":[…]}`, `GET/POST /api/settings`, `POST /api/protect`, `GET /healthz`.
