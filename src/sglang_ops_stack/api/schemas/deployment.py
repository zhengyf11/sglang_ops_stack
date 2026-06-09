import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sglang_ops_stack.utils.masking import mask_secret

_CONTAINER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$")
_IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,511}$")
_ALLOWED_DOCKER_VOLUME_MODES = {"ro", "rw"}
_SHELL_META_RE = re.compile(r"[;&|$`]")
_ALLOWED_SGLANG_EXTRA_ARGS = {
    "context_length",
    "max_running_requests",
    "schedule_policy",
    "chunked_prefill_size",
}
_ALLOWED_SGLANG_ADVANCED = {"trust_remote_code", "enable_cache_report", "enable_metrics"}


class DockerVolume(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    host_path: str = Field(min_length=1)
    container_path: str = Field(min_length=1)
    mode: str = Field(default="ro")

    @field_validator("host_path", "container_path")
    @classmethod
    def reject_volume_shorthand(cls, value: str) -> str:
        if (
            value.startswith("-v")
            or "\x00" in value
            or "\n" in value
            or "\r" in value
            or _SHELL_META_RE.search(value)
        ):
            raise ValueError(
                "volume paths must be structured single-line values without shell metacharacters"
            )
        return value

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in _ALLOWED_DOCKER_VOLUME_MODES:
            raise ValueError("volume mode must be ro or rw")
        return value


class DockerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    volumes: list[DockerVolume] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    gpus: str | None = Field(default="all")
    network: str | None = Field(default="host")
    privileged: bool = False
    user: str | None = None
    ipc: str | None = None
    shm_size: str | None = None
    remove_existing: bool = False

    @model_validator(mode="after")
    def reject_raw_volume_env_flags(self) -> "DockerConfig":
        for key, value in self.env.items():
            if key.startswith("-") or "\n" in key or "\n" in value:
                raise ValueError("environment variables must be structured key/value strings")
        return self

    @field_validator("gpus", "network", "user", "ipc", "shm_size")
    @classmethod
    def reject_shell_payload_config_fields(cls, value: str | None) -> str | None:
        if value is not None and _SHELL_META_RE.search(value):
            raise ValueError("docker config string fields must not contain shell metacharacters")
        return value


class SGLangConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model_path: str | None = None
    served_model_name: str | None = None
    host: str = "0.0.0.0"
    port: int = Field(default=30000, ge=1, le=65535)
    tp_size: int = Field(default=1, ge=1)
    dp_size: int = Field(default=1, ge=1)
    pp_size: int = Field(default=1, ge=1)
    mem_fraction_static: float | None = Field(default=None, gt=0, le=1)
    trust_remote_code: bool = False
    enable_cache_report: bool = False
    enable_metrics: bool = True
    launch_style: str = Field(default="sglang", pattern="^(sglang|python_module)$")
    extra_args: dict[str, str | int | float] = Field(default_factory=dict)

    @field_validator("extra_args")
    @classmethod
    def validate_extra_args(
        cls, value: dict[str, str | int | float]
    ) -> dict[str, str | int | float]:
        unknown = set(value) - _ALLOWED_SGLANG_EXTRA_ARGS
        advanced = set(value) & _ALLOWED_SGLANG_ADVANCED
        if unknown or advanced:
            raise ValueError("SGLang extra_args contains non-whitelisted parameters")
        return value


class DeploymentBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    host_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    container_name: str = Field(min_length=2, max_length=128, pattern=_CONTAINER_RE.pattern)
    image: str = Field(min_length=1, max_length=512, pattern=_IMAGE_RE.pattern)
    model_path: str = Field(min_length=1)
    served_model_name: str | None = Field(default=None, max_length=255)
    port: int = Field(default=30000, ge=1, le=65535)
    tp_size: int = Field(default=1, ge=1)
    dp_size: int = Field(default=1, ge=1)
    pp_size: int = Field(default=1, ge=1)
    mem_fraction_static: float | None = Field(default=None, gt=0, le=1)
    docker_config: DockerConfig = Field(default_factory=DockerConfig)
    sglang_config: SGLangConfig = Field(default_factory=SGLangConfig)
    change_summary: str | None = None
    created_by: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def sync_top_level_sglang_fields(self) -> "DeploymentBase":
        if self.sglang_config.model_path and self.sglang_config.model_path != self.model_path:
            raise ValueError("sglang_config.model_path must match model_path when provided")
        self.sglang_config.model_path = self.model_path
        self.sglang_config.served_model_name = self.served_model_name
        self.sglang_config.port = self.port
        self.sglang_config.tp_size = self.tp_size
        self.sglang_config.dp_size = self.dp_size
        self.sglang_config.pp_size = self.pp_size
        self.sglang_config.mem_fraction_static = self.mem_fraction_static
        return self


class DeploymentCreate(DeploymentBase):
    pass


class DeploymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    host_id: int
    name: str
    container_name: str
    image: str
    model_path: str
    served_model_name: str | None
    bind_host: str
    port: int
    tp_size: int
    dp_size: int
    pp_size: int
    mem_fraction_static: float | None
    docker_config: dict[str, Any]
    sglang_config: dict[str, Any]
    status: str
    service_url: str | None
    metrics_url: str | None
    last_command_preview: str | None
    last_health_status: dict[str, Any] | None
    current_revision_id: int | None
    current_version: int
    last_job_id: int | None
    last_error_code: str | None
    last_error_message: str | None


class DeployRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    password: str = Field(min_length=1)
    confirm_remove_existing: bool = False


class DeploymentPreview(BaseModel):
    command_preview: str
    risk_warnings: list[str]


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    password: str = Field(min_length=1)


class OperationResponse(BaseModel):
    job_id: int
    status: str
    operation: Literal["restart", "stop", "start"]


class DeploymentLogsResponse(BaseModel):
    deployment_id: int
    tail: int
    logs: str


class DeploymentDiffItem(BaseModel):
    field: str
    old_value: Any
    new_value: Any
    risk: str | None = None


class RedeployPlan(BaseModel):
    command_preview: str
    risk_warnings: list[str]
    diff: list[DeploymentDiffItem]
    high_risk: bool
    requires_confirmation: bool


class RedeployRequest(DeploymentCreate):
    password: str = Field(min_length=1)
    confirm_high_risk: bool = False


class RedeployResponse(BaseModel):
    job_id: int
    status: str
    high_risk: bool


def _collect_config_secrets(value: Any) -> list[str]:
    secrets: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower()
            if isinstance(item, str) and (
                key_lower in {"model_path", "host_path", "container_path"}
                or any(token in key_lower for token in ("password", "token", "key", "secret"))
            ):
                secrets.append(item)
            secrets.extend(_collect_config_secrets(item))
    elif isinstance(value, list):
        for item in value:
            secrets.extend(_collect_config_secrets(item))
    return [secret for secret in secrets if secret]


def _mask_config_value(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return mask_secret(value, secrets)
    if isinstance(value, list):
        return [_mask_config_value(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _mask_config_value(item, secrets) for key, item in value.items()}
    return value


class DeploymentRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    deployment_id: int
    revision_no: int
    config_snapshot: dict[str, Any]
    command_preview: str
    change_summary: str | None
    created_by: str | None

    @model_validator(mode="after")
    def redact_snapshot(self) -> "DeploymentRevisionRead":
        secrets = _collect_config_secrets(self.config_snapshot)
        self.config_snapshot = _mask_config_value(self.config_snapshot, secrets)
        self.command_preview = mask_secret(self.command_preview, secrets)
        return self
