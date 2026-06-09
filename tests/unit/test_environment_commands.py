from sglang_ops_stack.remote.command_spec import CommandRisk, CommandSpec, CommandValidationError
from sglang_ops_stack.services.environment.commands import HostCheckCommandBuilder


def test_command_spec_builds_quoted_safe_command_line() -> None:
    spec = CommandSpec(
        id="docker.run.cuda_test",
        executable="docker",
        args=("run", "--rm", "--gpus", "all", "nvidia/cuda:12.4.1-base-ubuntu22.04", "nvidia-smi"),
        risk=CommandRisk.docker_run,
    )

    assert spec.command_line() == (
        "docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi"
    )


def test_command_spec_rejects_shell_control_executable() -> None:
    spec = CommandSpec(id="unsafe", executable="bash", args=("-c", "whoami"))

    try:
        spec.validate()
    except CommandValidationError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("unsafe command should be rejected")


def test_command_spec_quotes_arguments_with_spaces_without_shell_concatenation() -> None:
    spec = CommandSpec(id="echo.safe", executable="id", args=("hello world",))

    assert spec.command_line() == "id 'hello world'"


def test_command_spec_allows_controlled_stdin_without_rendering_content_in_summary() -> None:
    spec = CommandSpec(
        id="nvidia_runtime.write_repo_list",
        executable="tee",
        args=("/etc/apt/sources.list.d/nvidia-container-toolkit.list",),
        sudo=True,
        risk=CommandRisk.package_install,
        stdin=(
            "deb [signed-by=/usr/share/keyrings/"
            "nvidia-container-toolkit-keyring.gpg] https://example.invalid /\n"
        ),
    )

    assert spec.command_line() == (
        "sudo -n tee /etc/apt/sources.list.d/nvidia-container-toolkit.list"
    )
    assert spec.safe_summary()["stdin"] == "<fixed managed content>"


def test_host_check_command_builder_uses_stable_whitelisted_specs() -> None:
    builder = HostCheckCommandBuilder()
    specs = [
        builder.ssh_smoke(),
        builder.os_release(),
        builder.gpu_lspci(),
        builder.driver_query(),
        builder.docker_version(),
        builder.docker_service_active(),
        builder.nvidia_ctk_version(),
        builder.docker_runtime_info(),
        builder.container_gpu_test(),
    ]

    assert [spec.id for spec in specs] == [
        "ssh.smoke",
        "os.release",
        "gpu.lspci",
        "driver.nvidia_smi",
        "docker.version",
        "docker.service_active",
        "nvidia_runtime.ctk_version",
        "nvidia_runtime.docker_info",
        "container.cuda_gpu_test",
    ]
    assert all("sglang" not in spec.command_line().lower() for spec in specs)
    assert builder.install_docker()[0].sudo is True
    assert builder.configure_nvidia_runtime()[-1].id == "nvidia_runtime.restart_docker"


def test_nvidia_runtime_install_plan_is_complete_for_clean_ubuntu_lts() -> None:
    specs = HostCheckCommandBuilder().configure_nvidia_runtime()

    assert [spec.id for spec in specs] == [
        "nvidia_runtime.keyring_dir",
        "nvidia_runtime.download_gpg_key",
        "nvidia_runtime.install_gpg_keyring",
        "nvidia_runtime.write_repo_list",
        "nvidia_runtime.apt_update",
        "nvidia_runtime.install_toolkit",
        "nvidia_runtime.configure",
        "nvidia_runtime.restart_docker",
    ]
    assert all(spec.validate() is spec for spec in specs)
    repo_spec = next(spec for spec in specs if spec.id == "nvidia_runtime.write_repo_list")
    assert repo_spec.executable == "tee"
    assert repo_spec.stdin is not None
    assert "nvidia.github.io/libnvidia-container/stable/deb" in repo_spec.stdin
    assert "|" not in repo_spec.command_line()
    keyring_spec = next(spec for spec in specs if spec.id == "nvidia_runtime.install_gpg_keyring")
    assert "--yes" in keyring_spec.args
    assert "apt-get update" in specs[4].command_line()
