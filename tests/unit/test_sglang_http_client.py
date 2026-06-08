from typing import Any

from sglang_ops_stack.clients import sglang_http
from sglang_ops_stack.clients.sglang_http import SGLangHTTPClient


class _FakeResponse:
    status = 200

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return b'{"status":"ok"}'


def test_sglang_http_client_reads_health_json(monkeypatch: Any) -> None:
    requested: list[str] = []

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        requested.append(request.full_url)
        assert timeout == 5.0
        return _FakeResponse()

    monkeypatch.setattr(sglang_http, "urlopen", fake_urlopen)

    response = SGLangHTTPClient("http://127.0.0.1:30000").health()

    assert requested == ["http://127.0.0.1:30000/health"]
    assert response.ok is True
    assert response.status_code == 200
    assert response.payload == {"status": "ok"}
