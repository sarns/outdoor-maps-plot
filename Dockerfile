# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.0 AS uv

FROM python:3.13-slim AS builder

COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.13-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_HOST=0.0.0.0 \
    OMP_PORT=8000 \
    OMP_JOB_ROOT=/tmp/poster-jobs \
    OMP_CACHE_ROOT=/var/cache/outdoor-maps-plot \
    TMPDIR=/tmp/poster-jobs

RUN groupadd --gid 10001 poster \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent poster \
    && mkdir -p /app /tmp/poster-jobs /var/cache/outdoor-maps-plot \
    && chown -R poster:poster /app /tmp/poster-jobs /var/cache/outdoor-maps-plot

COPY --from=builder --chown=poster:poster /app/.venv /app/.venv
COPY --from=builder --chown=poster:poster /app/LICENSE /app/THIRD_PARTY_NOTICES.md /app/

USER 10001:10001
WORKDIR /app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

CMD ["outdoor-maps-web"]
