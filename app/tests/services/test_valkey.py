"""Unit tests for services.valkey URL/host resolution.

Covers the awsk-m2n fix: prod ECS tasks receive VALKEY_URL from Secrets
Manager (rediss:// + AUTH); local dev runs plaintext on
$VALKEY_HOST:$VALKEY_PORT. Both paths must produce a usable connection.
"""

from __future__ import annotations

import pytest
from arq.connections import RedisSettings

from services import valkey


# ── arq_redis_settings ────────────────────────────────────────────────────────


def test_arq_settings_parses_rediss_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """rediss:// → ssl=True + host/port/password populated from URL."""
    monkeypatch.setenv(
        "VALKEY_URL",
        "rediss://default:tok-abc123@valkey.aws.local:6380/0",
    )
    monkeypatch.setenv("VALKEY_HOST", "should-be-ignored")
    monkeypatch.setenv("VALKEY_PORT", "9999")

    s = valkey.arq_redis_settings()

    assert isinstance(s, RedisSettings)
    assert s.host == "valkey.aws.local"
    assert s.port == 6380
    assert s.ssl is True
    assert s.password == "tok-abc123"
    assert s.username == "default"
    assert s.database == 0


def test_arq_settings_parses_plain_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """redis:// (no TLS) honors the scheme — ssl stays False."""
    monkeypatch.setenv("VALKEY_URL", "redis://localhost:6379/0")

    s = valkey.arq_redis_settings()

    assert s.host == "localhost"
    assert s.port == 6379
    assert s.ssl is False


def test_arq_settings_fallback_to_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """No VALKEY_URL → constructs from VALKEY_HOST/VALKEY_PORT."""
    monkeypatch.delenv("VALKEY_URL", raising=False)
    monkeypatch.setenv("VALKEY_HOST", "ministack.local")
    monkeypatch.setenv("VALKEY_PORT", "16379")

    s = valkey.arq_redis_settings()

    assert s.host == "ministack.local"
    assert s.port == 16379
    assert s.ssl is False
    assert s.password is None


def test_arq_settings_defaults_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare environment → defaults to localhost:6379 plaintext (dev convenience)."""
    monkeypatch.delenv("VALKEY_URL", raising=False)
    monkeypatch.delenv("VALKEY_HOST", raising=False)
    monkeypatch.delenv("VALKEY_PORT", raising=False)

    s = valkey.arq_redis_settings()

    assert s.host == "localhost"
    assert s.port == 6379
    assert s.ssl is False


def test_arq_settings_empty_url_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty VALKEY_URL string is treated as unset, not a parse error."""
    monkeypatch.setenv("VALKEY_URL", "")
    monkeypatch.setenv("VALKEY_HOST", "fallback.local")
    monkeypatch.setenv("VALKEY_PORT", "6380")

    s = valkey.arq_redis_settings()

    assert s.host == "fallback.local"
    assert s.port == 6380


def test_arq_settings_url_whitespace_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only VALKEY_URL also falls back to HOST/PORT."""
    monkeypatch.setenv("VALKEY_URL", "   ")
    monkeypatch.delenv("VALKEY_HOST", raising=False)
    monkeypatch.delenv("VALKEY_PORT", raising=False)

    s = valkey.arq_redis_settings()

    assert s.host == "localhost"
    assert s.port == 6379


# ── make_redis_client ─────────────────────────────────────────────────────────


def test_make_redis_client_uses_url_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """from_url path: client picks up TLS + auth from the rediss:// URL."""
    monkeypatch.setenv("VALKEY_URL", "rediss://default:tok@valkey:6379/0")

    client = valkey.make_redis_client()

    pool_kwargs = client.connection_pool.connection_kwargs
    assert pool_kwargs.get("host") == "valkey"
    assert pool_kwargs.get("port") == 6379
    assert pool_kwargs.get("password") == "tok"
    # redis-py represents TLS via the connection_class, not a flag — just
    # assert the pool was constructed with an SSL connection class.
    from redis.asyncio.connection import SSLConnection

    assert client.connection_pool.connection_class is SSLConnection


def test_make_redis_client_falls_back_to_host_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without VALKEY_URL the constructor takes host/port directly."""
    monkeypatch.delenv("VALKEY_URL", raising=False)
    monkeypatch.setenv("VALKEY_HOST", "ministack.local")
    monkeypatch.setenv("VALKEY_PORT", "4566")

    client = valkey.make_redis_client(socket_timeout=2.0)

    pool_kwargs = client.connection_pool.connection_kwargs
    assert pool_kwargs.get("host") == "ministack.local"
    assert pool_kwargs.get("port") == 4566
    assert pool_kwargs.get("socket_timeout") == 2.0
