from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sglang_ops_stack.config import Settings, get_settings
from sglang_ops_stack.db.models.user import User

_ALGORITHM = "sha256"
_DEFAULT_TOKEN_TTL_MINUTES = 60


def hash_password(password: str, *, iterations: int = 260_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, raw_iterations, salt, expected = password_hash.split("$", 3)
        iterations = int(raw_iterations)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode(), salt.encode(), iterations).hex()
    return hmac.compare_digest(digest, expected)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _sign(message: str, secret_key: str) -> str:
    digest = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).digest()
    return _b64url_encode(digest)


def create_access_token(user: User, settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    expires_at = datetime.now(UTC) + timedelta(
        minutes=active_settings.access_token_expire_minutes or _DEFAULT_TOKEN_TTL_MINUTES
    )
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "exp": int(expires_at.timestamp()),
    }
    header = {"typ": "JWT", "alg": "HS256"}
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode()),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode()),
        ]
    )
    signature = _sign(signing_input, active_settings.auth_secret_key)
    return f"{signing_input}.{signature}"


def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any] | None:
    active_settings = settings or get_settings()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    signing_input = ".".join(parts[:2])
    if not hmac.compare_digest(_sign(signing_input, active_settings.auth_secret_key), parts[2]):
        return None
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(datetime.now(UTC).timestamp()):
        return None
    return payload if isinstance(payload, dict) else None
