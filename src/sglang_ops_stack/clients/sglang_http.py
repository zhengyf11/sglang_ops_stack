import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SGLangHTTPResponse:
    ok: bool
    status_code: int | None
    payload: dict[str, Any] | None
    error: str | None = None


class SGLangHTTPClient:
    def __init__(self, base_url: str, timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def health(self) -> SGLangHTTPResponse:
        return self.get_json_or_text("/health")

    def model_info(self) -> SGLangHTTPResponse:
        return self.get_json_or_text("/model_info")

    def server_info(self) -> SGLangHTTPResponse:
        return self.get_json_or_text("/server_info")

    def get_load(self) -> SGLangHTTPResponse:
        return self.get_json_or_text("/get_load")

    def health_generate(self) -> SGLangHTTPResponse:
        return SGLangHTTPResponse(ok=False, status_code=None, payload=None, error="not_implemented")

    def metrics(self) -> SGLangHTTPResponse:
        return self.get_json_or_text("/metrics")

    def get_json_or_text(self, path: str) -> SGLangHTTPResponse:
        request = Request(f"{self.base_url}{path}", method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                status_code = int(response.status)
                body = response.read().decode(errors="replace")
        except HTTPError as exc:
            return SGLangHTTPResponse(False, int(exc.code), None, str(exc))
        except URLError as exc:
            return SGLangHTTPResponse(False, None, None, str(exc.reason))
        except TimeoutError as exc:
            return SGLangHTTPResponse(False, None, None, str(exc))
        payload: dict[str, Any]
        try:
            loaded = json.loads(body) if body else {}
            payload = loaded if isinstance(loaded, dict) else {"value": loaded}
        except json.JSONDecodeError:
            payload = {"text": body}
        return SGLangHTTPResponse(200 <= status_code < 300, status_code, payload)
