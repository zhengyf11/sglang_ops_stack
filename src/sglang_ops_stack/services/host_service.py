from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate, HostUpdate
from sglang_ops_stack.db.models.host import Host


def list_hosts(db: Session) -> Sequence[Host]:
    return db.scalars(select(Host).order_by(Host.id)).all()


def get_host(db: Session, host_id: int) -> Host | None:
    return db.get(Host, host_id)


def create_host(db: Session, payload: HostCreate) -> Host:
    host = Host(**payload.model_dump())
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


def update_host(db: Session, host: Host, payload: HostUpdate) -> Host:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(host, key, value)
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


def delete_host(db: Session, host: Host) -> None:
    db.delete(host)
    db.commit()
