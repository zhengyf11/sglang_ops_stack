from enum import StrEnum


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class JobType(StrEnum):
    ssh_connect_check = "ssh_connect_check"


class TargetType(StrEnum):
    host = "host"
