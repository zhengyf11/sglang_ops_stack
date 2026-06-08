from dataclasses import dataclass

from sglang_ops_stack.api.schemas.deployment import DockerConfig
from sglang_ops_stack.remote.command_spec import CommandRisk, CommandSpec


@dataclass(frozen=True)
class DockerCommandBuilder:
    image: str
    container_name: str
    port: int
    config: DockerConfig

    def pull(self) -> CommandSpec:
        return CommandSpec(
            id="deployment.docker_pull",
            executable="docker",
            args=("pull", self.image),
            timeout_seconds=900,
            risk=CommandRisk.service_change,
            description="Pull SGLang container image",
        )

    def rm_existing(self) -> CommandSpec:
        return CommandSpec(
            id="deployment.docker_rm_existing",
            executable="docker",
            args=("rm", "-f", self.container_name),
            timeout_seconds=120,
            allowed_exit_codes=(0, 1),
            risk=CommandRisk.docker_run,
            description="Remove existing container after explicit confirmation",
        )

    def run_detached_idle(self) -> CommandSpec:
        args: list[str] = ["run", "-d", "--name", self.container_name]
        if self.config.gpus:
            args.extend(("--gpus", self.config.gpus))
        if self.config.network:
            args.extend(("--network", self.config.network))
        if self.config.privileged:
            args.append("--privileged")
        if self.config.user:
            args.extend(("--user", self.config.user))
        if self.config.shm_size:
            args.extend(("--shm-size", self.config.shm_size))
        if self.config.network != "host":
            args.extend(("-p", f"{self.port}:{self.port}"))
        for volume in self.config.volumes:
            args.extend(("-v", f"{volume.host_path}:{volume.container_path}:{volume.mode}"))
        for key, value in self.config.env.items():
            args.extend(("-e", f"{key}={value}"))
        args.extend((self.image, "tail", "-f", "/dev/null"))
        return CommandSpec(
            id="deployment.docker_run_idle",
            executable="docker",
            args=tuple(args),
            timeout_seconds=300,
            risk=CommandRisk.docker_run,
            description="Create idle SGLang container",
        )

    def exec_detached(self, argv: tuple[str, ...]) -> CommandSpec:
        return CommandSpec(
            id="deployment.sglang_exec_start",
            executable="docker",
            args=("exec", "-d", self.container_name, *argv),
            timeout_seconds=120,
            risk=CommandRisk.service_change,
            description="Start SGLang inside container",
        )

    def ps(self) -> CommandSpec:
        return CommandSpec(
            id="deployment.docker_ps",
            executable="docker",
            args=("ps", "--filter", f"name=^{self.container_name}$", "--format", "{{.Names}}"),
            timeout_seconds=30,
            description="Check running container",
        )

    def inspect_running(self) -> CommandSpec:
        return CommandSpec(
            id="deployment.docker_inspect_running",
            executable="docker",
            args=("inspect", "-f", "{{.State.Running}}", self.container_name),
            timeout_seconds=30,
            allowed_exit_codes=(0, 1),
            description="Inspect container running state",
        )

    def logs_tail(self, lines: int = 100) -> CommandSpec:
        return CommandSpec(
            id="deployment.docker_logs",
            executable="docker",
            args=("logs", "--tail", str(lines), self.container_name),
            timeout_seconds=30,
            allowed_exit_codes=(0, 1),
            description="Read recent container logs",
        )

    def process_check(self) -> CommandSpec:
        return CommandSpec(
            id="deployment.sglang_process_check",
            executable="docker",
            args=("exec", self.container_name, "pgrep", "-af", "sglang|launch_server"),
            timeout_seconds=30,
            allowed_exit_codes=(0, 1),
            description="Check SGLang process inside container",
        )


def docker_risk_warnings(config: DockerConfig) -> list[str]:
    warnings: list[str] = []
    if config.privileged:
        warnings.append("Risk: --privileged grants broad host capabilities to the container.")
    if config.network == "host":
        warnings.append("Risk: --network host exposes the service directly on the host network.")
    if config.user in (None, "", "root", "0"):
        warnings.append("Risk: container process runs as root user by default.")
    if config.gpus == "all":
        warnings.append("Risk: --gpus all exposes all host GPUs to the container.")
    return warnings
