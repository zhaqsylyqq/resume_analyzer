# Enbek Screen — production image (single process: the analysis job runner is in-process)
#
# CI-03: two stages. The builder installs the locked dependencies into a virtualenv;
# the runtime stage gets only that virtualenv and the app code — no pip, no build cache.
# The base image is pinned by digest; Dependabot proposes digest updates (CI-04).
ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# --- builder -------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# A virtualenv without its own pip: the builder's pip installs into it.
RUN python -m venv --without-pip /opt/venv

# QW-03: exact versions with hashes; pytest and other dev tools are not in the image
COPY requirements.lock /tmp/requirements.lock
RUN pip --python /opt/venv/bin/python install --require-hashes --no-deps -r /tmp/requirements.lock

# --- runtime -------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="hrscreen" \
      org.opencontainers.image.description="HR resume screening (RU/KK)" \
      org.opencontainers.image.source="https://github.com/zhaqsylyqq/resume_analyzer" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Security fixes released after the base image was built (Trivy found one in zlib),
# then pip and setuptools leave the image: the app never installs packages at runtime.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y -q pip setuptools wheel \
 && groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY scripts/smoke_test.py scripts/backup_db.py scripts/backup_offsite.py scripts/restore_check.py ./scripts/

# SQLite database and generated secret live here; mount a volume to keep them.
RUN mkdir -p /app/instance && chown -R app:app /app/instance
VOLUME ["/app/instance"]

USER 10001:10001
EXPOSE 8000
# APP-08: on SIGTERM the app finishes resumes in analysis (SHUTDOWN_TIMEOUT, default 25 s)
# and exits; give it time with `docker stop -t 30` (compose: stop_grace_period).
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

CMD ["waitress-serve", "--host=0.0.0.0", "--port=8000", "--threads=8", "--call", "app:create_server_app"]
