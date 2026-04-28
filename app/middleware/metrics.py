"""Prometheus HTTP request instrumentation middleware.

Wraps every request to populate the SPEC §8.1 metrics:

  - ``connect_http_requests_total{method,path,status}`` (counter)
  - ``connect_http_request_duration_seconds{method,path}`` (histogram)

The ``path`` label is the FastAPI/Starlette **route template** (e.g.
``/api/datastores/{datastore_id}``) rather than the rendered URL, which keeps
label cardinality bounded. Requests that don't match a known route fall back
to the literal request path so 404 hits don't silently disappear from the
counter.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match
from starlette.types import ASGIApp

from routes.health import http_request_duration_seconds, http_requests_total


def _resolve_route_template(request: Request) -> str:
    """Return the matched route's path template, or the raw path on miss."""
    router = request.scope.get("app")
    if router is None:
        return request.url.path

    routes = getattr(router, "routes", None) or []
    for route in routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            template = getattr(route, "path", None)
            if isinstance(template, str) and template:
                return template
    return request.url.path


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record per-request counter and histogram observations."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Don't recurse: the /metrics endpoint itself is excluded from the
        # counter so a Prometheus scrape doesn't inflate its own request rate.
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        path_template = _resolve_route_template(request)
        start = time.perf_counter()
        status_code: int = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            elapsed = time.perf_counter() - start
            http_requests_total.labels(
                method=method,
                path=path_template,
                status=str(status_code),
            ).inc()
            http_request_duration_seconds.labels(
                method=method,
                path=path_template,
            ).observe(elapsed)
