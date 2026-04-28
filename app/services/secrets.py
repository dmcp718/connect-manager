"""
Secure Secret Storage
- Docker/Container: Encrypted file-based storage in DATA_DIR (Fernet AES-128)
- Local development: System keyring (macOS Keychain, Windows Credential Locker, Linux Secret Service)
"""

import base64
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Optional, Tuple

# Check if running in container mode (DATA_DIR set)
_data_dir = os.getenv("DATA_DIR")
_container_mode = bool(_data_dir)

if _container_mode:
    from cryptography.fernet import Fernet, InvalidToken
else:
    import keyring

# Service name / file name
SERVICE_NAME = "lucidlink-labs"
SECRETS_FILE = Path(_data_dir) / "secrets.enc" if _data_dir else None
_LEGACY_SECRETS_FILE = Path(_data_dir) / "secrets.json" if _data_dir else None


def _get_fernet_key() -> bytes:
    """Derive a Fernet key from JWT_SECRET_KEY using PBKDF2."""
    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    if not jwt_secret:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable required for secrets encryption"
        )

    # Use PBKDF2 to derive a 32-byte key, then base64 encode for Fernet
    # Salt is fixed (app-specific) since we need deterministic key derivation
    salt = b"lucidlink-labs-secrets-v1"
    key = hashlib.pbkdf2_hmac(
        "sha256", jwt_secret.encode(), salt, iterations=100000, dklen=32
    )
    return base64.urlsafe_b64encode(key)


def _get_fernet() -> "Fernet":
    """Get a Fernet instance for encryption/decryption."""
    return Fernet(_get_fernet_key())


# Secret keys
KEY_LL_TOKEN = "lucidlink_api_token"
KEY_AWS_ACCESS_KEY = "aws_access_key"
KEY_AWS_SECRET_KEY = "aws_secret_key"


def _migrate_legacy_secrets() -> Optional[dict]:
    """Check for and migrate plaintext secrets.json to encrypted format."""
    if not _LEGACY_SECRETS_FILE or not _LEGACY_SECRETS_FILE.exists():
        return None

    try:
        # Read plaintext secrets
        plaintext = _LEGACY_SECRETS_FILE.read_text()
        secrets = json.loads(plaintext)

        # Save in encrypted format
        _save_secrets(secrets)

        # Remove legacy file
        _LEGACY_SECRETS_FILE.unlink()

        return secrets
    except Exception:
        return None


def _load_secrets() -> dict:
    """Load secrets from encrypted file (container mode)."""
    # Check for legacy plaintext file and migrate
    migrated = _migrate_legacy_secrets()
    if migrated is not None:
        return migrated

    if SECRETS_FILE and SECRETS_FILE.exists():
        try:
            fernet = _get_fernet()
            encrypted_data = SECRETS_FILE.read_bytes()
            decrypted = fernet.decrypt(encrypted_data)
            return json.loads(decrypted.decode())
        except InvalidToken:
            # Corrupted or wrong key - return empty
            return {}
        except Exception:
            return {}
    return {}


def _save_secrets(secrets: dict) -> None:
    """Save secrets to encrypted file (container mode)."""
    if SECRETS_FILE:
        SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        fernet = _get_fernet()
        plaintext = json.dumps(secrets).encode()
        encrypted = fernet.encrypt(plaintext)
        SECRETS_FILE.write_bytes(encrypted)
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


# ============== Named Credentials (for multi-config support) ==============


def generate_credentials_key() -> str:
    """Generate a unique key for storing credentials."""
    return uuid.uuid4().hex[:8]


def set_named_credentials(key: str, access_key: str, secret_key: str) -> None:
    """Store AWS credentials with a unique key."""
    if not key or not access_key or not secret_key:
        return
    cred_data = json.dumps(
        {
            "access_key": access_key,
            "secret_key": secret_key,
        }
    )
    set_secret(f"aws_creds_{key}", cred_data)


def get_named_credentials(key: str) -> Tuple[Optional[str], Optional[str]]:
    """Retrieve AWS credentials by key."""
    if not key:
        return None, None
    try:
        cred_data = get_secret(f"aws_creds_{key}")
        if cred_data:
            data = json.loads(cred_data)
            return data.get("access_key"), data.get("secret_key")
    except (json.JSONDecodeError, TypeError):
        pass
    return None, None


def delete_named_credentials(key: str) -> bool:
    """Delete AWS credentials by key."""
    if not key:
        return False
    return delete_secret(f"aws_creds_{key}")


# ============== User-Specific Tokens (for multi-user support) ==============


def set_user_token(user_id: str, token: str) -> None:
    """Store a LucidLink API token for a specific user."""
    if not user_id or not token:
        return
    set_secret(f"ll_token_{user_id}", token)


def get_user_token(user_id: str) -> Optional[str]:
    """Retrieve a LucidLink API token for a specific user."""
    if not user_id:
        return None
    return get_secret(f"ll_token_{user_id}")


def delete_user_token(user_id: str) -> bool:
    """Delete a LucidLink API token for a specific user."""
    if not user_id:
        return False
    return delete_secret(f"ll_token_{user_id}")
