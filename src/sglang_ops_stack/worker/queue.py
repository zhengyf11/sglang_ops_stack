import base64
import hashlib
import hmac
import secrets
from typing import Any, Literal, Protocol, TypedDict, cast

from sglang_ops_stack.config import Settings, get_settings

try:
    from celery import Celery  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised when optional worker deps absent
    Celery = None


class AsyncTask(Protocol):
    def delay(self, payload: dict[str, Any]) -> object: ...


class JobPayload(TypedDict, total=False):
    job_type: str
    job_id: int
    encrypted_password: str
    operation: str
    confirm_remove_existing: bool
    requested_config: dict[str, Any]
    confirm_high_risk: bool


settings = get_settings()
celery_app = (
    Celery("sglang_ops_stack", broker=settings.redis_url, backend=settings.redis_url)
    if Celery is not None
    else None
)

if celery_app is not None:
    celery_app.conf.update(
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_time_limit=60 * 60 * 6,
    )


def should_use_worker_queue(active_settings: Settings) -> bool:
    return active_settings.task_queue_mode == "celery"


if celery_app is not None:

    @celery_app.task(name="sglang_ops_stack.dispatch_job")  # type: ignore[untyped-decorator]
    def dispatch_job(payload: dict[str, Any]) -> None:
        _dispatch_job(payload)

else:

    class _MissingCeleryTask:
        def delay(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("Celery is not installed")

    dispatch_job = _MissingCeleryTask()


def enqueue_job(
    active_settings: Settings,
    job_type: str,
    job_id: int,
    password: str,
    **options: Any,
) -> bool:
    if not should_use_worker_queue(active_settings):
        return False
    payload: dict[str, Any] = {
        "job_type": job_type,
        "job_id": job_id,
        "encrypted_password": encrypt_password(password, active_settings),
    }
    payload.update(options)
    task = cast(AsyncTask, dispatch_job)
    task.delay(dict(payload))
    return True


def encrypt_password(password: str, active_settings: Settings | None = None) -> str:
    active_settings = active_settings or get_settings()
    nonce = secrets.token_bytes(16)
    plaintext = password.encode("utf-8")
    ciphertext = _xor_stream(plaintext, _secret_key(active_settings), nonce)
    mac = hmac.new(_secret_key(active_settings), nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + mac + ciphertext).decode("ascii")


def decrypt_password(token: str, active_settings: Settings | None = None) -> str:
    active_settings = active_settings or get_settings()
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    if len(raw) < 48:
        raise ValueError("encrypted credential payload is malformed")
    nonce = raw[:16]
    mac = raw[16:48]
    ciphertext = raw[48:]
    expected = hmac.new(_secret_key(active_settings), nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("encrypted credential payload failed integrity check")
    return _xor_stream(ciphertext, _secret_key(active_settings), nonce).decode("utf-8")


def _secret_key(active_settings: Settings) -> bytes:
    material = active_settings.secret_key or active_settings.auth_secret_key
    return hashlib.sha256(material.encode("utf-8")).digest()


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        output.extend(block)
        counter += 1
    return bytes(value ^ mask for value, mask in zip(data, output, strict=False))


def _dispatch_job(payload: dict[str, Any]) -> None:
    job_type = str(payload["job_type"])
    job_id = int(payload["job_id"])
    try:
        _dispatch_job_unchecked(payload, job_type, job_id)
    except Exception as exc:
        _mark_job_failed(job_id, exc)
        raise


def _dispatch_job_unchecked(payload: dict[str, Any], job_type: str, job_id: int) -> None:
    from sglang_ops_stack.domain_enums import JobType
    from sglang_ops_stack.jobs.deployment_jobs import run_deployment_task
    from sglang_ops_stack.jobs.operation_jobs import run_operation_task
    from sglang_ops_stack.jobs.redeploy_jobs import run_redeploy_task
    from sglang_ops_stack.services.environment.runner import (
        confirm_environment_install_task,
        run_environment_check_task,
    )
    from sglang_ops_stack.services.ssh_connect_check import run_ssh_connect_check_task

    password = decrypt_password(str(payload["encrypted_password"]))
    if job_type == JobType.ssh_connect_check.value:
        run_ssh_connect_check_task(job_id, password)
        return
    if job_type == JobType.environment_check.value:
        run_environment_check_task(job_id, password)
        return
    if job_type == "environment_install":
        confirm_environment_install_task(job_id, password, True)
        return
    if job_type == JobType.deployment.value:
        run_deployment_task(job_id, password, bool(payload.get("confirm_remove_existing", False)))
        return
    if job_type in {"restart", "stop", "start"}:
        operation = cast(Literal["restart", "stop", "start"], payload.get("operation", job_type))
        run_operation_task(job_id, operation, password)
        return
    if job_type == JobType.redeployment.value:
        requested_config = payload.get("requested_config")
        if not isinstance(requested_config, dict):
            requested_config = {}
        run_redeploy_task(
            job_id,
            password,
            requested_config,
            bool(payload.get("confirm_high_risk", False)),
        )
        return
    raise ValueError(f"unsupported worker job type: {job_type}")


def _mark_job_failed(job_id: int, exc: Exception) -> None:
    from sglang_ops_stack.db.session import SessionLocal
    from sglang_ops_stack.services import job_service

    with SessionLocal() as db:
        job = job_service.get_job(db, job_id)
        if job is None:
            return
        if job.status in {"succeeded", "failed"}:
            return
        message = str(exc) or exc.__class__.__name__
        job_service.mark_failed(
            db,
            job,
            error_code="WORKER_TASK_FAILED",
            error_message=message,
        )
