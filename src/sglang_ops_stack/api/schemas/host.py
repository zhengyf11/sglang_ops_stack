from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HostBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    ip: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", min_length=1, max_length=120)
    auth_type: str = Field(default="password", pattern="^password$")
    tags: str | None = Field(default=None, max_length=512)
    note: str | None = None

    @field_validator("ip")
    @classmethod
    def validate_ip_like(cls, value: str) -> str:
        if any(ch.isspace() for ch in value):
            raise ValueError("ip/host must not contain whitespace")
        return value


class HostCreate(HostBase):
    pass


class HostUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    ip: str | None = Field(default=None, min_length=1, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, min_length=1, max_length=120)
    auth_type: str | None = Field(default=None, pattern="^password$")
    tags: str | None = Field(default=None, max_length=512)
    note: str | None = None

    @field_validator("ip")
    @classmethod
    def validate_optional_ip_like(cls, value: str | None) -> str | None:
        if value is not None and any(ch.isspace() for ch in value):
            raise ValueError("ip/host must not contain whitespace")
        return value


class HostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ip: str
    ssh_port: int
    ssh_user: str
    auth_type: str
    tags: str | None
    note: str | None
    os_info: str | None
    gpu_info: str | None
    last_check_status: str | None
    environment_status: str | None
    last_check_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SSHCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    password: str = Field(min_length=1)
