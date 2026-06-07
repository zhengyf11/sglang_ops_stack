from types import SimpleNamespace
from unittest.mock import MagicMock

from sglang_ops_stack.services import ssh_connect_check


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> "DummySession":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.closed = True


def test_task_wrapper_opens_independent_session_and_passes_settings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    session = DummySession()
    runner = MagicMock()
    monkeypatch.setattr(ssh_connect_check, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        ssh_connect_check,
        "get_settings",
        lambda: SimpleNamespace(ssh_connect_timeout=3.5, ssh_command_timeout=9.5),
    )
    monkeypatch.setattr(ssh_connect_check, "run_ssh_connect_check", runner)

    ssh_connect_check.run_ssh_connect_check_task(42, "super-secret")

    runner.assert_called_once_with(
        session,
        job_id=42,
        password="super-secret",
        connect_timeout=3.5,
        command_timeout=9.5,
    )
    assert session.closed is True
