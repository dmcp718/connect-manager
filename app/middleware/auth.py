"""
Authentication Middleware
Protects routes that require authentication
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

from services import auth


# Routes that don't require authentication
PUBLIC_PATHS = [
    "/login",
    "/api/auth/login",
    "/api/auth/register",
    "/static",
    "/favicon.ico",
    "/health",
    "/ready",
    "/metrics",
]

# Cookie name for auth token
COOKIE_NAME = "access_token"


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that protects routes requiring authentication.
    Redirects unauthenticated users to login page.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths
        if self._is_public_path(path):
            return await call_next(request)

        # Check for authentication
        token = self._get_token(request)

        if not token:
            return self._unauthorized_response(request)

        # Verify token
        payload = auth.decode_token(token)
        if not payload:
            return self._unauthorized_response(request)

        # Attach user info to request state for route handlers
        request.state.user_id = payload["sub"]
        request.state.user_email = payload["email"]
        request.state.session_id = payload["jti"]
        request.state.is_admin = payload.get("is_admin", False)

        return await call_next(request)

    def _is_public_path(self, path: str) -> bool:
        """Check if path is public (doesn't require auth)."""
        for public_path in PUBLIC_PATHS:
            if path == public_path or path.startswith(public_path + "/"):
                return True
            # Handle exact matches and prefix matches
            if path.startswith(public_path):
                return True
        return False

    def _get_token(self, request: Request) -> str | None:
        """Extract token from cookie or Authorization header."""
        # Try cookie first
        token = request.cookies.get(COOKIE_NAME)
        if token:
            return token

        # Fall back to Authorization header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]

        return None

    def _unauthorized_response(self, request: Request):
        """Return appropriate unauthorized response."""
        # For API requests, return JSON
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
            )

        # For page requests, redirect to login
        return RedirectResponse(url="/login", status_code=302)
