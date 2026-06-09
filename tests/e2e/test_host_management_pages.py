from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from sglang_ops_stack.api.schemas.host import HostCreate
from sglang_ops_stack.services import host_service, job_service


def test_host_management_pages(client: TestClient) -> None:
    assert client.get("/hosts").status_code == 200

    create = client.post(
        "/hosts",
        data={"name": "gpu-1", "ip": "10.0.0.1", "ssh_port": "22", "ssh_user": "root"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    detail_url = create.headers["location"]
    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "gpu-1" in detail.text
    assert 'type="password"' in detail.text
    assert 'value="' not in detail.text.split('type="password"')[1].split(">", 1)[0]

    edit_url = f"{detail_url}/edit"
    assert client.get(edit_url).status_code == 200
    updated = client.post(
        detail_url,
        data={"name": "gpu-2", "ip": "10.0.0.2", "ssh_port": "2222", "ssh_user": "ubuntu"},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    assert "gpu-2" in client.get(detail_url).text


def test_job_page_does_not_render_plain_password(client: TestClient, db_session: Session) -> None:
    host = host_service.create_host(db_session, HostCreate(name="gpu", ip="10.0.0.1"))
    job = job_service.create_job(db_session, target_id=host.id)
    job_service.add_log(
        db_session,
        job_id=job.id,
        level="error",
        message="failed password=super-secret",
        secrets=["super-secret"],
    )
    job_service.mark_failed(
        db_session,
        job,
        error_code="auth_failed",
        error_message="password=super-secret",
        secrets=["super-secret"],
    )

    page = client.get(f"/jobs/{job.id}")
    assert page.status_code == 200
    assert "super-secret" not in page.text
    assert "***" in page.text
