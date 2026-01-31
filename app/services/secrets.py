"""
Secure Secret Storage
- Docker/Container: File-based storage in DATA_DIR
- Local development: System keyring (macOS Keychain, Windows Credential Locker, Linux Secret Service)
"""

import json
import os
from pathlib import Path
from typing import Optional

# Check if running in container mode (DATA_DIR set)
_data_dir = os.getenv("DATA_DIR")
_container_mode = bool(_data_dir)

if not _container_mode:
    import keyring

# Service name / file name
SERVICE_NAME = "lucidlink-labs"
SECRETS_FILE = Path(_data_dir) / "secrets.json" if _data_dir else None

# Secret keys
KEY_LL_TOKEN = "lucidlink_api_token"
KEY_AWS_ACCESS_KEY = "aws_access_key"
KEY_AWS_SECRET_KEY = "aws_secret_key"


def _load_secrets() -> dict:
    """Load secrets from file (container mode)."""
    if SECRETS_FILE and SECRETS_FILE.exists():
        try:
            return json.loads(SECRETS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_secrets(secrets: dict) -> None:
    """Save secrets to file (container mode)."""
    if SECRETS_FILE:
        SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRETS_FILE.write_text(json.dumps(secrets))
        # Restrict file permissions
        os.chmod(SECRETS_FILE, 0o600)


def set_secret(key: str, value: str) -> None:
    """Store a secret."""
    if not value:
        return

    if _container_mode:
        secrets = _load_secrets()
        secrets[key] = value
        _save_secrets(secrets)
    else:
        keyring.set_password(SERVICE_NAME, key, value)


def get_secret(key: str) -> Optional[str]:
    """Retrieve a secret."""
    # Check environment variables first
    env_key = f"LL_{key.upper()}"
    env_value = os.getenv(env_key)
    if env_value:
        return env_value

    try:
        if _container_mode:
            secrets = _load_secrets()
            return secrets.get(key)
        else:
            return keyring.get_password(SERVICE_NAME, key)
    except Exception:
        return None


def delete_secret(key: str) -> bool:
    """Delete a secret."""
    try:
        if _container_mode:
            secrets = _load_secrets()
            if key in secrets:
                del secrets[key]
                _save_secrets(secrets)
                return True
            return False
        else:
            keyring.delete_password(SERVICE_NAME, key)
            return True
    except Exception:
        return False


# Convenience functions for specific secrets

def set_lucidlink_token(token: str) -> None:
    """Store the LucidLink API token."""
    set_secret(KEY_LL_TOKEN, token)


def get_lucidlink_token() -> Optional[str]:
    """Retrieve the LucidLink API token."""
    return get_secret(KEY_LL_TOKEN)


def delete_lucidlink_token() -> bool:
    """Delete the LucidLink API token."""
    return delete_secret(KEY_LL_TOKEN)


def set_aws_credentials(access_key: str, secret_key: str) -> None:
    """Store AWS credentials."""
    if access_key:
        set_secret(KEY_AWS_ACCESS_KEY, access_key)
    if secret_key:
        set_secret(KEY_AWS_SECRET_KEY, secret_key)


def get_aws_credentials() -> tuple[Optional[str], Optional[str]]:
    """Retrieve AWS credentials."""
    return (
        get_secret(KEY_AWS_ACCESS_KEY),
        get_secret(KEY_AWS_SECRET_KEY),
    )


def delete_aws_credentials() -> None:
    """Delete AWS credentials."""
    delete_secret(KEY_AWS_ACCESS_KEY)
    delete_secret(KEY_AWS_SECRET_KEY)


def clear_all_secrets() -> None:
    """Clear all stored secrets."""
    delete_lucidlink_token()
    delete_aws_credentials()
