import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK = "***"
SENSITIVE_KEYWORDS = ("password", "passwd", "pwd", "token", "api_key", "apikey", "key", "secret")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret)\s*=\s*([^\s&;,'\"]+)"
)
_JSON_SECRET_RE = re.compile(
    r"(?i)(['\"]?(?:password|passwd|pwd|token|api[_-]?key|secret)['\"]?\s*:\s*['\"])([^'\"]+)(['\"])"
)
_BEARER_RE = re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)([^\s]+)")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_RE = re.compile(r"https?://[^\s'\")<>]+")


def _has_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(token in normalized for token in SENSITIVE_KEYWORDS)


def _mask_url_tokens(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        parts = urlsplit(raw_url)
        if not parts.query:
            return raw_url
        query = urlencode(
            [
                (key, MASK if _has_sensitive_key(key) else value)
                for key, value in parse_qsl(parts.query)
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    return _URL_RE.sub(repl, text)


def mask_secret(text: str | None, secrets: Sequence[str] = ()) -> str:
    if text is None:
        return ""
    masked = text
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, MASK)
    masked = _PRIVATE_KEY_RE.sub(MASK, masked)
    masked = _BEARER_RE.sub(r"\1***", masked)
    masked = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={MASK}", masked)
    masked = _JSON_SECRET_RE.sub(lambda match: f"{match.group(1)}{MASK}{match.group(3)}", masked)
    return _mask_url_tokens(masked)


def mask_sensitive_data(value: Any, secrets: Sequence[str] = ()) -> Any:
    if isinstance(value, str):
        return mask_secret(value, secrets)
    if isinstance(value, Mapping):
        return {
            key: MASK if _has_sensitive_key(key) else mask_sensitive_data(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive_data(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_sensitive_data(item, secrets) for item in value)
    return value
