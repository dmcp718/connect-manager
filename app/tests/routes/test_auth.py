"""End-to-end route tests for /api/auth/* endpoints.

Builds a minimal FastAPI app and overrides the get_db dependency with a
per-test AsyncSession backed by the shared postgres_container fixture.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base
from routes.auth import router
from services.database import get_db

if os.environ.get("PYTEST_FAST"):
    pytest.skip("PYTEST_FAST set — skipping DB tests", allow_module_level=True)


# ── Per-test DB session ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session(postgres_container: str) -> AsyncIterator[AsyncSession]:
    """Fresh session per test."""
    engine: AsyncEngine = create_async_engine(
        postgres_container,
        connect_args={"ssl": False},
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as session:
        yield session
        await session.rollback()
        for tbl in ("user_sessions", "users"):
            await session.execute(Base.metadata.tables[tbl].delete())  # type: ignore[arg-type]
        await session.commit()
    await engine.dispose()


# ── Minimal app with dependency override ─────────────────────────────────────


def build_app(session: AsyncSession) -> FastAPI:
    """Return a FastAPI instance whose get_db yields the provided session."""
    app = FastAPI()
    app.include_router(router)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    return app


# ── Tests ─────────────────────────────────────────────────────────────────────


# ActivityLogger.log calls db.create_activity_log which is a legacy SQLite-era
# API that no longer exists on this branch (tracked in awsk-opc).  Patch it
# out for route tests so the breakage doesn't mask our auth logic coverage.
@pytest.fixture(autouse=True)
def _stub_activity_logger() -> Iterator[None]:
    with patch("services.activity_logger.ActivityLogger.log", return_value=0):
        yield


_EMAIL = "routetest@example.com"
_PASSWORD = "RoutePass1"
_DISPLAY = "Route User"


@pytest.mark.asyncio
async def test_register_login_protected_endpoint(db_session: AsyncSession) -> None:
    """Full happy-path: register → login → cookie set → me endpoint returns user info."""
    app = build_app(db_session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        # httpx follows cookies automatically within a session
        cookies={},
    ) as client:
        # 1. Register
        reg_resp = await client.post(
            "/api/auth/register",
            data={"email": _EMAIL, "password": _PASSWORD, "display_name": _DISPLAY},
        )
        assert reg_resp.status_code == 200, reg_resp.text
        body = reg_resp.json()
        assert body["status"] == "success"
        assert "user_id" in body

        # Cookie must have been set by the register endpoint.
        token_cookie = reg_resp.cookies.get("access_token")
        assert token_cookie, "access_token cookie missing after register"

        # 2. Call /me with the cookie
        me_resp = await client.get(
            "/api/auth/me",
            cookies={"access_token": token_cookie},
        )
        assert me_resp.status_code == 200, me_resp.text
        me_body = me_resp.json()
        assert me_body["email"] == _EMAIL
        assert me_body["display_name"] == _DISPLAY


@pytest.mark.asyncio
async def test_login_sets_cookie_and_logout_clears_session(
    db_session: AsyncSession,
) -> None:
    """Login sets cookie; logout revokes session so the cookie is useless afterwards."""
    app = build_app(db_session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Register first
        await client.post(
            "/api/auth/register",
            data={"email": _EMAIL, "password": _PASSWORD},
        )

        # Login
        login_resp = await client.post(
            "/api/auth/login",
            data={"email": _EMAIL, "password": _PASSWORD},
        )
        assert login_resp.status_code == 200, login_resp.text
        token = login_resp.cookies.get("access_token")
        assert token

        # /me succeeds with valid cookie
        me_ok = await client.get("/api/auth/me", cookies={"access_token": token})
        assert me_ok.status_code == 200

        # Logout
        logout_resp = await client.post(
            "/api/auth/logout",
            cookies={"access_token": token},
        )
        assert logout_resp.status_code == 200

        # /me must now return 401 (session revoked)
        me_after = await client.get("/api/auth/me", cookies={"access_token": token})
        assert me_after.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(db_session: AsyncSession) -> None:
    """POST /login with wrong password returns 401."""
    app = build_app(db_session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/auth/register",
            data={"email": _EMAIL, "password": _PASSWORD},
        )
        resp = await client.post(
            "/api/auth/login",
            data={"email": _EMAIL, "password": "WrongPassword9"},
        )
        assert resp.status_code == 401
