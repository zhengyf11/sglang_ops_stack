import shlex
from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class CommandRisk(StrEnum):
    read_only = "read_only"
    service_change = "service_change"
    package_install = "package_install"
    docker_run = "docker_run"


class CommandValidationError(ValueError):
    pass


_ALLOWED_EXECUTABLES = {
    "apt-get",
    "cat",
    "curl",
    "docker",
    "dpkg-query",
    "gpg",
    "id",
    "install",
    "lspci",
    "nvidia-ctk",
    "nvidia-smi",
    "python3",
    "systemctl",
    "tee",
    "uname",
}
_FORBIDDEN_TOKENS = {";", "&&", "||", "|", ">", "<", "`", "$"}


@dataclass(frozen=True)
class CommandSpec:
    id: str
    executable: str
    args: tuple[str, ...] = ()
    sudo: bool = False
    timeout_seconds: int = 30
    allowed_exit_codes: tuple[int, ...] = (0,)
    redact_patterns: tuple[str, ...] = ()
    risk: CommandRisk = CommandRisk.read_only
    description: str = ""
    stdin: str | None = None

    def validate(self) -> Self:
        if self.executable not in _ALLOWED_EXECUTABLES:
            raise CommandValidationError(f"executable {self.executable!r} is not allowed")
        if not self.id or any(token in self.id for token in _FORBIDDEN_TOKENS):
            raise CommandValidationError("command id is invalid")
        for value in (self.executable, *self.args):
            if "\x00" in value or "\n" in value or "\r" in value:
                raise CommandValidationError("command values must be single-line strings")
        if self.stdin is not None and "\x00" in self.stdin:
            raise CommandValidationError("stdin must not contain NUL bytes")
        if self.sudo and self.risk == CommandRisk.read_only:
            raise CommandValidationError("sudo commands must declare an elevated risk")
        return self

    def argv(self) -> tuple[str, ...]:
        self.validate()
        command = (self.executable, *self.args)
        if self.sudo:
            return ("sudo", "-n", *command)
        return command

    def command_line(self) -> str:
        return " ".join(shlex.quote(part) for part in self.argv())

    def safe_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "id": self.id,
            "command": self.command_line(),
            "sudo": self.sudo,
            "risk": self.risk.value,
            "description": self.description,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.stdin is not None:
            summary["stdin"] = "<fixed managed content>"
        return summary
