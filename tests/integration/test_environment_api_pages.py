from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate
from sglang_ops_stack.services import host_service, job_service


def test_start_environment_check_api_creates_waiting_job_without_storing_password(
    client: TestClient,
    db_session: Session,
) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))

    response = client.post(
        f"/api/hosts/{host.id}/environment/check",
        json={"password": "super-secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    job = job_service.get_job(db_session, payload["job_id"])
    assert job is not None
    assert job.type == "environment_check"
    assert "super-secret" not in str(job.__dict__)


def test_confirm_install_requires_explicit_confirmation(
    client: TestClient,
    db_session: Session,
) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))
    job = job_service.create_job(db_session, target_id=host.id, job_type="environment_check")
    job_service.mark_waiting_confirmation(
        db_session,
        job,
        result={"install_plan": [{"id": "docker.install"}], "steps": []},
    )

    response = client.post(
        f"/api/jobs/{job.id}/confirm-install",
        json={"password": "super-secret", "confirmed": False},
    )

    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"].lower()


def test_retry_environment_check_endpoint_resets_failed_job(
    client: TestClient,
    db_session: Session,
) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))
    job = job_service.create_job(db_session, target_id=host.id, job_type="environment_check")
    job_service.mark_failed(
        db_session,
        job,
        error_code="ENV_DOCKER_MISSING",
        error_message="password=super-secret docker missing",
        secrets=["super-secret"],
    )

    response = client.post(
        f"/api/jobs/{job.id}/retry",
        json={"password": "super-secret"},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == job.id
    refreshed = job_service.get_job(db_session, job.id)
    assert refreshed is not None and refreshed.status == "pending"
    assert "super-secret" not in (refreshed.error_message or "")


def test_host_detail_page_has_environment_check_entry(
    client: TestClient,
    db_session: Session,
) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))

    page = client.get(f"/hosts/{host.id}")

    assert page.status_code == 200
    assert "Environment check" in page.text
    assert f"/hosts/{host.id}/environment/check" in page.text


def test_job_page_renders_environment_steps_and_confirmation_form(
    client: TestClient,
    db_session: Session,
) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))
    job = job_service.create_job(db_session, target_id=host.id, job_type="environment_check")
    job_service.mark_waiting_confirmation(
        db_session,
        job,
        result={
            "steps": [
                {"id": "ssh", "name": "SSH", "status": "passed", "summary": "ok"},
                {
                    "id": "docker",
                    "name": "Docker",
                    "status": "waiting_confirmation",
                    "error_code": "ENV_DOCKER_MISSING",
                    "summary": "Docker must be installed",
                },
            ],
            "install_plan": [
                {
                    "id": "docker.install",
                    "description": "Install Docker packages",
                    "risk": "package_install",
                }
            ],
        },
    )

    page = client.get(f"/jobs/{job.id}")

    assert page.status_code == 200
    assert "Environment steps" in page.text
    assert "ENV_DOCKER_MISSING" in page.text
    assert "Confirm installation" in page.text
    assert "package_install" in page.text
