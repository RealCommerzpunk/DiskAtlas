# DiskAtlas-Server (Dashboard + API + Ingest) – z. B. für Unraid.
# Festplatten-Scans laufen per Agent auf den jeweiligen Rechnern und senden an diesen Server.
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/RealCommerzpunk/DiskAtlas" \
      org.opencontainers.image.description="DiskAtlas – Festplatteninventar (Weboberfläche, API, Datenbank)" \
      org.opencontainers.image.licenses="MIT" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/RealCommerzpunk/DiskAtlas/main/src/diskatlas/web/static/icon_256.png" \
      net.unraid.docker.webui="http://[IP]:[PORT:8765]/"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISKATLAS_DATABASE_URL=sqlite:////data/diskatlas.db \
    DISKATLAS_HOST=0.0.0.0 \
    DISKATLAS_PORT=8765

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]" && mkdir -p /data && chown 99:100 /data

# Unraid-Standard: nobody (99) / users (100)
USER 99:100
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['DISKATLAS_PORT'] + '/api/v1/health')"
CMD ["diskatlas", "serve"]
