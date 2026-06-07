from datetime import UTC, datetime

import paramiko

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
            stdin, stdout, stderr = client.exec_command(
                command,
                timeout=timeout or self.command_timeout,
            )
            stdin.close()
            exit_code = stdout.channel.recv_exit_status()
            return CommandResult(
                exit_code=exit_code,
                stdout=stdout.read().decode(errors="replace"),
                stderr=stderr.read().decode(errors="replace"),
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
