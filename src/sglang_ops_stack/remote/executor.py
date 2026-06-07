import time
from datetime import UTC, datetime
from typing import Any

import paramiko

from sglang_ops_stack.remote.command_spec import CommandSpec
from sglang_ops_stack.remote.result import CommandResult


class SSHExecutionError(RuntimeError):
    error_code = "ssh_error"


class SSHAuthError(SSHExecutionError):
    error_code = "auth_failed"


class SSHConnectTimeoutError(SSHExecutionError):
    error_code = "connect_timeout"


class SSHCommandTimeoutError(SSHExecutionError):
    error_code = "command_timeout"


class SSHExecutor:
    def __init__(self, connect_timeout: float = 10.0, command_timeout: float = 30.0) -> None:
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout

    def execute_spec(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        spec: CommandSpec,
    ) -> CommandResult:
        spec.validate()
        return self.run(
            host=host,
            port=port,
            username=username,
            password=password,
            command=spec.command_line(),
            timeout=spec.timeout_seconds,
        )

    def run(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        command: str,
        timeout: float | None = None,
    ) -> CommandResult:
        started_at = datetime.now(UTC)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            command_timeout = self.command_timeout if timeout is None else timeout
            stdin, stdout, _stderr = client.exec_command(
                command,
                timeout=command_timeout,
            )
            stdin.close()
            channel = stdout.channel
            stdout_bytes, stderr_bytes, exit_code = self._read_channel(
                channel,
                timeout=command_timeout,
                client=client,
            )
            return CommandResult(
                exit_code=exit_code,
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
                timed_out=False,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        except paramiko.AuthenticationException as exc:
            raise SSHAuthError("SSH authentication failed") from exc
        except TimeoutError as exc:
            raise SSHConnectTimeoutError("SSH connection timed out") from exc
        except paramiko.SSHException as exc:
            message = str(exc) or "SSH execution failed"
            if "timed out" in message.lower():
                raise SSHCommandTimeoutError("SSH command timed out") from exc
            raise SSHExecutionError(message) from exc
        finally:
            client.close()

    def _read_channel(
        self,
        channel: Any,
        *,
        timeout: float,
        client: paramiko.SSHClient,
    ) -> tuple[bytes, bytes, int]:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        deadline = time.monotonic() + timeout

        while True:
            while channel.recv_ready():
                stdout_chunks.append(channel.recv(32768))
            while channel.recv_stderr_ready():
                stderr_chunks.append(channel.recv_stderr(32768))

            if channel.exit_status_ready():
                exit_code = channel.recv_exit_status()
                while channel.recv_ready():
                    stdout_chunks.append(channel.recv(32768))
                while channel.recv_stderr_ready():
                    stderr_chunks.append(channel.recv_stderr(32768))
                return b"".join(stdout_chunks), b"".join(stderr_chunks), exit_code

            if time.monotonic() >= deadline:
                channel.close()
                client.close()
                raise SSHCommandTimeoutError(f"SSH command timed out after {timeout:g}s")

            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
