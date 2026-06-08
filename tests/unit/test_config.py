from sglang_ops_stack.config import Settings


def test_settings_defaults_keep_development_startup_and_docs_enabled() -> None:
    settings = Settings()

    assert settings.environment == "development"
    assert settings.docs_enabled is True
    assert settings.app_name == "sglang_ops_stack"


def test_security_headers_setting_accepts_env_style_csv() -> None:
    settings = Settings(security_headers="x-frame-options,x-content-type-options")

    assert settings.security_headers == ("x-frame-options", "x-content-type-options")
