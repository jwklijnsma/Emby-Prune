FROM python:3.15-alpine

ENV PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config \
    WEB_PORT=8585 \
    TZ=UTC

WORKDIR /opt/emby-prune
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && adduser -D -u 1000 prune && mkdir -p /config && chown prune /config

COPY app ./app

USER prune
VOLUME /config
EXPOSE 8585
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:${WEB_PORT}/healthz || exit 1
CMD ["python", "-m", "app.main"]
