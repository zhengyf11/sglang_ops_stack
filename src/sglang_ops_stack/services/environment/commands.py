from sglang_ops_stack.remote.command_spec import CommandRisk, CommandSpec

CUDA_TEST_IMAGE = "nvidia/cuda:12.4.1-base-ubuntu22.04"


class HostCheckCommandBuilder:
    def ssh_smoke(self) -> CommandSpec:
        return CommandSpec(
            id="ssh.smoke",
            executable="id",
            args=("-u",),
            description="Verify SSH command execution",
        )

    def os_release(self) -> CommandSpec:
        return CommandSpec(
            id="os.release",
            executable="cat",
            args=("/etc/os-release",),
            description="Read OS release metadata",
        )

    def os_arch(self) -> CommandSpec:
        return CommandSpec(
            id="os.arch",
            executable="uname",
            args=("-m",),
            description="Read machine architecture",
        )

    def gpu_lspci(self) -> CommandSpec:
        return CommandSpec(
            id="gpu.lspci",
            executable="lspci",
            args=("-nn",),
            allowed_exit_codes=(0, 1),
            description="Detect NVIDIA PCI devices",
        )

    def driver_query(self) -> CommandSpec:
        return CommandSpec(
            id="driver.nvidia_smi",
            executable="nvidia-smi",
            args=("--query-gpu=driver_version,name", "--format=csv,noheader"),
            description="Query NVIDIA driver and GPU names",
        )

    def docker_version(self) -> CommandSpec:
        return CommandSpec(
            id="docker.version",
            executable="docker",
            args=("--version",),
            description="Check Docker client installation",
        )

    def docker_service_active(self) -> CommandSpec:
        return CommandSpec(
            id="docker.service_active",
            executable="systemctl",
            args=("is-active", "docker"),
            description="Check Docker daemon state",
        )

    def docker_runtime_info(self) -> CommandSpec:
        return CommandSpec(
            id="nvidia_runtime.docker_info",
            executable="docker",
            args=("info", "--format", "{{json .Runtimes}}"),
            description="Inspect Docker runtime configuration",
        )

    def nvidia_ctk_version(self) -> CommandSpec:
        return CommandSpec(
            id="nvidia_runtime.ctk_version",
            executable="nvidia-ctk",
            args=("--version",),
            description="Check NVIDIA Container Toolkit",
        )

    def container_gpu_test(self) -> CommandSpec:
        return CommandSpec(
            id="container.cuda_gpu_test",
            executable="docker",
            args=("run", "--rm", "--gpus", "all", CUDA_TEST_IMAGE, "nvidia-smi"),
            timeout_seconds=120,
            risk=CommandRisk.docker_run,
            description="Run CUDA test container to verify GPU visibility",
        )

    def install_docker(self) -> list[CommandSpec]:
        return [
            CommandSpec(
                id="docker.apt_update",
                executable="apt-get",
                args=("update",),
                sudo=True,
                timeout_seconds=120,
                risk=CommandRisk.package_install,
                description="Refresh apt package index before Docker installation",
            ),
            CommandSpec(
                id="docker.install",
                executable="apt-get",
                args=("install", "-y", "docker.io"),
                sudo=True,
                timeout_seconds=300,
                risk=CommandRisk.package_install,
                description="Install Docker package",
            ),
            CommandSpec(
                id="docker.enable_now",
                executable="systemctl",
                args=("enable", "--now", "docker"),
                sudo=True,
                timeout_seconds=60,
                risk=CommandRisk.service_change,
                description="Enable and start Docker service",
            ),
        ]

    def configure_nvidia_runtime(self) -> list[CommandSpec]:
        return [
            CommandSpec(
                id="nvidia_runtime.install_toolkit",
                executable="apt-get",
                args=("install", "-y", "nvidia-container-toolkit"),
                sudo=True,
                timeout_seconds=300,
                risk=CommandRisk.package_install,
                description="Install NVIDIA Container Toolkit package",
            ),
            CommandSpec(
                id="nvidia_runtime.configure",
                executable="nvidia-ctk",
                args=("runtime", "configure", "--runtime=docker"),
                sudo=True,
                timeout_seconds=60,
                risk=CommandRisk.service_change,
                description="Configure Docker NVIDIA runtime",
            ),
            CommandSpec(
                id="nvidia_runtime.restart_docker",
                executable="systemctl",
                args=("restart", "docker"),
                sudo=True,
                timeout_seconds=60,
                risk=CommandRisk.service_change,
                description="Restart Docker to apply NVIDIA runtime configuration",
            ),
        ]
