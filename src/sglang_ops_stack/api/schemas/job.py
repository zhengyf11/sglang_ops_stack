from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    status: str
    target_type: str
    target_id: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class JobLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    level: str
    message: str
    created_at: datetime
