from dataclasses import dataclass

from sglang_ops_stack.config import Settings


@dataclass(frozen=True)
class SecurityConfig:
    environment: str
    require_https: bool
    headers: dict[str, str]


def build_security_config(settings: Settings) -> SecurityConfig:
    available_headers = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
    }
    headers = {
        name: value
        for name, value in available_headers.items()
        if name in settings.security_headers
    }
    return SecurityConfig(
        environment=settings.environment,
        require_https=settings.require_https,
        headers=headers,
    )
