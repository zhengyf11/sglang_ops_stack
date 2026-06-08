from enum import StrEnum


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting_confirmation = "waiting_confirmation"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class JobType(StrEnum):
    ssh_connect_check = "ssh_connect_check"
    environment_check = "environment_check"
    deployment = "deployment"
    deployment_operation = "deployment_operation"
    redeployment = "redeployment"


class TargetType(StrEnum):
    host = "host"
    deployment = "deployment"
