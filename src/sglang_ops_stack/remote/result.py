from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    started_at: datetime
    finished_at: datetime
