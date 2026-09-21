# syntax=docker/dockerfile:1.7
# Alpha Direct Finance — Django backend
# Multi-stage build: builder produces wheels, runtime image stays slim.

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

# Which commit this image is. Passed by the deploy script; the health endpoint
# reports it so a stale production can be SEEN rather than assumed current.
ARG GIT_SHA=unknown
ARG BUILD_AT=unknown
ENV OMNI_BUILD_SHA=$GIT_SHA \
    OMNI_BUILD_AT=$BUILD_AT

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=alpha_finance.settings \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        libgomp1 \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 django \
    && useradd  --system --uid 1000 --gid django --home-dir /app --shell /usr/sbin/nologin django

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Headless Chromium for the Underwriting document renderer (server-side A4
# PDF for emailed/stored copies). Installed world-readable so the runtime
# `django` user can launch it. NON-FATAL: if the browser download fails the
# image still builds — the renderer degrades to HTTP 503 and the tool's
# in-browser "Save PDF" (window.print) keeps working. (CFO handover 2026-07-08)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN (playwright install --with-deps chromium \
      && chmod -R a+rX /ms-playwright) \
    || echo "[build] chromium install skipped — server-side PDF render will 503; browser Save-PDF still works"

COPY --chown=django:django . /app

RUN mkdir -p /app/staticfiles /app/media \
    && chown -R django:django /app/staticfiles /app/media

RUN chmod +x /app/docker/entrypoint.sh

USER django

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ > /dev/null || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "alpha_finance.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--max-requests", "2000", \
     "--max-requests-jitter", "200", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
