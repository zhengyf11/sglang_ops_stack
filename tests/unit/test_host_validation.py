import pytest
from pydantic import ValidationError

from sglang_ops_stack.api.schemas.host import HostCreate


def test_host_create_validates_required_name() -> None:
    with pytest.raises(ValidationError):
        HostCreate(name="", ip="10.0.0.1")


def test_host_create_validates_port_range() -> None:
    with pytest.raises(ValidationError):
        HostCreate(name="gpu-1", ip="10.0.0.1", ssh_port=70000)


def test_host_create_rejects_password_extra_field() -> None:
    with pytest.raises(ValidationError):
        HostCreate(name="gpu-1", ip="10.0.0.1", password="secret")  # type: ignore[call-arg]


def test_host_create_rejects_whitespace_in_host() -> None:
    with pytest.raises(ValidationError):
        HostCreate(name="gpu-1", ip="bad host")
