# Enbek Screen — production image (single process: the analysis job runner is in-process)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home /app app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts/smoke_test.py scripts/backup_db.py ./scripts/

# SQLite database and generated secret live here; mount a volume to keep them.
RUN mkdir -p /app/instance && chown -R app:app /app/instance
VOLUME ["/app/instance"]

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["waitress-serve", "--host=0.0.0.0", "--port=8000", "--threads=8", "--call", "app:create_app"]
