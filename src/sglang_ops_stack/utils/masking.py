import re
from collections.abc import Sequence

MASK = "***"
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret)\s*=\s*([^\s&;,'\"]+)"
)
_BEARER_RE = re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)([^\s]+)")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


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
    return masked
