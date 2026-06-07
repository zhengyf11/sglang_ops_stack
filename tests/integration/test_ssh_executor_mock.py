from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from sglang_ops_stack.remote.executor import SSHCommandTimeoutError, SSHExecutor


def test_ssh_executor_passes_connection_options_to_paramiko() -> None:
    channel = MagicMock()
    channel.recv_ready.side_effect = [True, False, False]
    channel.recv_stderr_ready.return_value = False
    channel.recv.return_value = b"ok"
    channel.exit_status_ready.return_value = True
    channel.recv_exit_status.return_value = 0
    stdout = MagicMock()
    stderr = MagicMock()
    stdin = MagicMock()
    stdout.channel = channel
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


def test_ssh_executor_command_timeout_closes_channel_and_client() -> None:
    channel = MagicMock()
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = False
    stdout = MagicMock()
    stderr = MagicMock()
    stdin = MagicMock()
    stdout.channel = channel
    client = MagicMock()
    client.exec_command.return_value = (stdin, stdout, stderr)

    with (
        patch("sglang_ops_stack.remote.executor.paramiko.SSHClient", return_value=client),
        pytest.raises(SSHCommandTimeoutError, match="timed out after 0s"),
    ):
        SSHExecutor(connect_timeout=5, command_timeout=7).run(
            host="10.0.0.1",
            port=2222,
            username="root",
            password="secret",
            command="sleep 999",
            timeout=0,
        )

    channel.close.assert_called_once()
    assert client.close.call_count >= 1
