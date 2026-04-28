"""Valkey connection-string resolver.

Production tasks receive `VALKEY_URL` from Secrets Manager (a
`rediss://default:<auth-token>@<host>:6379` URL with TLS + AUTH),
populated by `terraform/modules/elasticache`. Local dev / ministack
runs Valkey plaintext on `localhost:6379` and exposes it via the
`VALKEY_HOST` / `VALKEY_PORT` env vars per `local/docker-compose.local.yml`.

Both the ARQ side (`arq.connections.RedisSettings`) and the raw
`redis.asyncio.Redis` side need to handle either form, so this module
centralizes the resolution. Read `VALKEY_URL` first; fall back to
`VALKEY_HOST` / `VALKEY_PORT` only when the URL is unset.

Without this helper, every consumer that called `os.getenv("VALKEY_HOST",
"localhost")` directly would silently default to `localhost:6379` in
production and fail to reach ElastiCache (no TLS, no AUTH). See awsk-m2n.
"""

from __future__ import annotations

import os
from typing import Any

import redis.asyncio as redis
from arq.connections import RedisSettings


def _env_url() -> str | None:
    url = os.environ.get("VALKEY_URL", "").strip()
    return url or None


def arq_redis_settings() -> RedisSettings:
    """Return ARQ `RedisSettings` derived from `VALKEY_URL` if present,
    else from `VALKEY_HOST` / `VALKEY_PORT`.

    `RedisSettings.from_dsn` parses `rediss://` schemes correctly and sets
    `ssl=True`, `username`, `password`, `host`, `port`, `database`.
    """
    url = _env_url()
    if url:
        return RedisSettings.from_dsn(url)
    return RedisSettings(
        host=os.getenv("VALKEY_HOST", "localhost"),
        port=int(os.getenv("VALKEY_PORT", 6379)),
    )


def make_redis_client(**override: Any) -> redis.Redis:
    """Construct a `redis.asyncio.Redis` from `VALKEY_URL` or HOST/PORT.

    Override kwargs (e.g. `socket_timeout`) are passed through to either
    `Redis.from_url` or the `Redis(...)` constructor, mirroring whichever
    code path was taken.
    """
    url = _env_url()
    if url:
        return redis.Redis.from_url(url, **override)
    return redis.Redis(
        host=os.getenv("VALKEY_HOST", "localhost"),
        port=int(os.getenv("VALKEY_PORT", 6379)),
        **override,
    )
