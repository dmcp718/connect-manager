# syntax=docker/dockerfile:1.6
#
# CONNECT Manager image — multi-stage build with `web` and `worker` targets
# consumed by:
#   - .github/workflows/aws-fargate.yml `build-push` job (--target web/worker)
#   - scripts/build-and-push-images.sh for Path B operators (no GitHub Actions)
#   - The ECS migrate task definition reuses the web image and overrides the
#     command to `alembic upgrade head` (one-shot, run via aws ecs run-task on
#     deploy per CLAUDE.md rule #6).
#
# Targets share a common `deps` stage so the dependency layer caches across
# both images. Final images are minimal — no build toolchain, no docs.

# ── Dep resolution ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS deps

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Build toolchain only in this stage; the runtime stages don't need it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned for reproducibility — bump deliberately).
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies into a virtualenv we'll copy into the runtime stages.
# `--python /usr/local/bin/python3.12` pins to the slim image's interpreter
# so the venv symlinks point at /usr/local/bin/python3.12 (which exists in
# the runtime stages — the default uv-managed Python at /usr/bin/python3
# does not).
COPY app/pyproject.toml app/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
        --python /usr/local/bin/python3.12

# ── Common runtime base ──────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# libpq is needed at runtime for asyncpg's underlying libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bring in the resolved virtualenv from the deps stage.
COPY --from=deps /app/.venv /app/.venv

# Application code. `app/` becomes the import root; alembic config lives at
# /migrations so the migrate task can run `alembic -c /migrations/alembic.ini
# upgrade head` regardless of working directory.
COPY app/ /app/
COPY VERSION /app/VERSION
COPY alembic.ini /migrations/alembic.ini
COPY alembic/ /migrations/alembic/

# Non-root runtime user — defence in depth even though Fargate runs the
# container in its own kernel-isolated VM.
RUN groupadd --system connect && \
    useradd --system --gid connect --home-dir /app --no-create-home connect && \
    chown -R connect:connect /app /migrations
USER connect

# ── Web target ───────────────────────────────────────────────────────────────
FROM base AS web

EXPOSE 8000

# uvicorn binds the FastAPI app from app/main.py:app. The lifespan handler
# wires job_queue + ActivityLogger persistence + metrics sampler.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]

# ── Worker target ────────────────────────────────────────────────────────────
FROM base AS worker

# ARQ picks up WorkerSettings from services.worker:WorkerSettings — the cron
# jobs (sqs_poll_cron, cleanup_old_events, timeout_stale_jobs,
# cleanup_old_activity_logs) are registered there. job_completion_wait
# graceful-drain is configured via env (ARQ_JOB_COMPLETION_WAIT).
CMD ["arq", "services.worker.WorkerSettings"]
