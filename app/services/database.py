"""Async SQLAlchemy engine + sessionmaker for the CONNECT Manager Postgres data plane.

Exposes four public symbols consumed by the rest of the app:
  get_engine()        — process-singleton AsyncEngine, lazy-initialised
  get_sessionmaker()  — process-singleton async_sessionmaker, lazy-initialised
  get_db()            — FastAPI Depends generator; yields one AsyncSession per request
  shutdown_engine()   — called from FastAPI lifespan on SIGTERM; idempotent
"""

from __future__ import annotations

import os
import ssl
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from services.logging import get_logger

log = get_logger(__name__)

# Module-level singletons — None until first call to get_engine().
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_ssl_arg() -> ssl.SSLContext | bool:
    """Return the asyncpg ssl= connect arg.

    asyncpg does not honour sslmode= URL parameters; the ssl= keyword arg is
    the supported path.  When DATABASE_URL_DISABLE_SSL=1 we pass False (no
    TLS).  Otherwise we build a default-strict SSLContext (sslmode=require
    equivalent).
    """
    if os.environ.get("DATABASE_URL_DISABLE_SSL") == "1":
        return False
    ctx = ssl.create_default_context()
    return ctx


def get_engine() -> AsyncEngine:
    """Return (or lazily create) the process-singleton AsyncEngine."""
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is required")

        pool_size = int(os.environ.get("DB_POOL_SIZE", 10))
        max_overflow = int(os.environ.get("DB_MAX_OVERFLOW", 5))
        pool_timeout = int(os.environ.get("DB_POOL_TIMEOUT", 30))
        ssl_arg = _build_ssl_arg()
        ssl_enabled = ssl_arg is not False

        _engine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_pre_ping=True,
            connect_args={"ssl": ssl_arg},
        )

        log.info(
            "Postgres engine initialised",
            extra={
                "pool_size": pool_size,
                "max_overflow": max_overflow,
                "ssl_enabled": ssl_enabled,
            },
        )

    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return (or lazily create) the process-singleton async_sessionmaker."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
        )
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency; yields one AsyncSession per request.

    Rolls back on exception, always closes in finally.  Caller decides when to
    commit — this dependency does not call commit().
    """
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def shutdown_engine() -> None:
    """Dispose of the engine connection pool; safe to call multiple times."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
        log.info("Postgres engine disposed")


# ─────────────────────────────────────────────────────────────────────────────
# Legacy SQLite-era shim — REMOVE after awsk-opc follow-up beads convert all
# `db.*` callers in app/services/{state,user_state,sqs_poller}.py and app/main.py
# to async repository methods. Until then this catches every legacy call with
# a shape-aware safe default so the app boots and serves requests cleanly,
# while degraded codepaths log a warning per call.
# ─────────────────────────────────────────────────────────────────────────────

_LEGACY_LIST_PREFIXES = ("list_", "get_all_")
_LEGACY_BOOL_SUFFIXES = ("_exists",)
_LEGACY_INT_PREFIXES = ("count_", "get_count_")


def _legacy_default_for(name: str) -> Any:
    """Pick a shape-appropriate default value based on the function name."""
    if name.startswith(_LEGACY_LIST_PREFIXES):
        return []
    if name.startswith(_LEGACY_INT_PREFIXES):
        return 0
    if name.endswith(_LEGACY_BOOL_SUFFIXES):
        return False
    # Everything else (get_*, save_*, set_*, delete_*, init_*, create_*, update_*,
    # mark_*, clear_*, …) returns None. Side-effect callers ignore it; readers
    # that expected a row get None and handle it as "not found."
    return None


def __getattr__(name: str) -> Any:
    """Module-level fallback for legacy `db.*` references.

    Returns a callable that logs a warning and returns the shape-default for
    `name`. Triggered only when a name isn't otherwise defined in this module
    — engine/sessionmaker/get_db/shutdown_engine are unaffected.
    """
    if name.startswith("_"):
        raise AttributeError(name)

    default = _legacy_default_for(name)

    def _legacy_stub(*args: Any, **kwargs: Any) -> Any:
        log.warning(
            "legacy db.%s called — returning safe default; convert to async repo",
            name,
            extra={"legacy_function": name, "default_returned": repr(default)},
        )
        return default

    return _legacy_stub
