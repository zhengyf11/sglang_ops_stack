from sglang_ops_stack.api.schemas.deployment import SGLangConfig


def build_sglang_argv(config: SGLangConfig) -> tuple[str, ...]:
    model_path = config.model_path
    if not model_path:
        raise ValueError("model_path is required")
    if config.launch_style == "python_module":
        args: list[str] = ["python3", "-m", "sglang.launch_server", "--model-path", model_path]
    else:
        args = ["sglang", "serve", model_path]
    args.extend(("--host", config.host, "--port", str(config.port)))
    args.extend(("--tp-size", str(config.tp_size)))
    args.extend(("--dp-size", str(config.dp_size)))
    args.extend(("--pp-size", str(config.pp_size)))
    if config.served_model_name:
        args.extend(("--served-model-name", config.served_model_name))
    if config.mem_fraction_static is not None:
        args.extend(("--mem-fraction-static", str(config.mem_fraction_static)))
    if config.trust_remote_code:
        args.append("--trust-remote-code")
    if config.enable_cache_report:
        args.append("--enable-cache-report")
    if config.enable_metrics:
        args.append("--enable-metrics")
    for key in sorted(config.extra_args):
        args.extend((f"--{key.replace('_', '-')}", str(config.extra_args[key])))
    return tuple(args)
