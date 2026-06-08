from sglang_ops_stack.config import Settings
from sglang_ops_stack.core.security import build_security_config


def test_security_config_keeps_development_startup_permissive() -> None:
    config = build_security_config(Settings(environment="development"))

    assert config.environment == "development"
    assert config.require_https is False
    assert config.headers["x-content-type-options"] == "nosniff"


def test_security_config_does_not_expose_secrets() -> None:
    config = build_security_config(Settings())

    assert "database" not in repr(config).lower()
    assert "secret" not in repr(config).lower()
