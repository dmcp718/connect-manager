"""Bridge to app/services/aws.py.

Adds ../app to sys.path so the bootstrap TUI can reuse the same boto3 client
factory the web app uses. boto3's default credential chain handles ECS Task
Role, env-var creds (ministack), and SSO/profile resolution transparently.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _ensure_app_on_path() -> None:
    """Insert ../app on sys.path if not already there."""
    app_dir = Path(__file__).resolve().parents[2] / "app"
    p = str(app_dir)
    if p not in sys.path:
        sys.path.insert(0, p)


_ensure_app_on_path()

# Imported lazily — `services.aws` requires boto3 which is in our deps.
from services.aws import get_client, get_resource  # noqa: E402


def boto3_client(service_name: str, **kwargs: Any) -> Any:
    """Construct a boto3 client via the app's helper. Default credential
    chain only — never pass aws_access_key_id / aws_secret_access_key here.
    """
    return get_client(service_name, **kwargs)


def boto3_resource(service_name: str, **kwargs: Any) -> Any:
    return get_resource(service_name, **kwargs)
