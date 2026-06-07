from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from sglang_ops_stack.remote.executor import SSHExecutor


def test_ssh_executor_passes_connection_options_to_paramiko() -> None:
    stdout = MagicMock()
    stderr = MagicMock()
    stdin = MagicMock()
    stdout.channel.recv_exit_status.return_value = 0
    stdout.read.return_value = b"ok"
    stderr.read.return_value = b""
    client = MagicMock()
    client.exec_command.return_value = (stdin, stdout, stderr)

    with patch("sglang_ops_stack.remote.executor.paramiko.SSHClient", return_value=client):
        result = SSHExecutor(connect_timeout=5, command_timeout=7).run(
            host="10.0.0.1",
            port=2222,
            username="root",
            password="secret",
            command="whoami",
        )

    client.connect.assert_called_once_with(
        hostname="10.0.0.1",
        port=2222,
        username="root",
        password="secret",
        timeout=5,
        auth_timeout=5,
        banner_timeout=5,
        look_for_keys=False,
        allow_agent=False,
    )
    client.exec_command.assert_called_once_with("whoami", timeout=7)
    assert result.exit_code == 0
    assert result.stdout == "ok"
    assert result.started_at <= datetime.now(UTC)
