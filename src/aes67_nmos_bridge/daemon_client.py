from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from .models import DaemonState


class DaemonClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class DaemonClient:
    base_url: str
    timeout_seconds: float = 5.0

    def get_state(self) -> DaemonState:
        payload = self._request_json("GET", "/api/streams")
        if not isinstance(payload, dict):
            raise DaemonClientError("daemon /api/streams response must be a JSON object")
        return DaemonState.from_streams_payload(payload)

    def put_sender(self, daemon_id: int, payload: dict) -> None:
        self._request_empty("PUT", f"/api/source/{daemon_id}", payload)

    def delete_sender(self, daemon_id: int) -> None:
        self._request_empty("DELETE", f"/api/source/{daemon_id}")

    def put_receiver(self, daemon_id: int, payload: dict) -> None:
        self._request_empty("PUT", f"/api/sink/{daemon_id}", payload)

    def delete_receiver(self, daemon_id: int) -> None:
        self._request_empty("DELETE", f"/api/sink/{daemon_id}")

    def _request_json(self, method: str, path: str) -> Any:
        body = self._request(method, path)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DaemonClientError(f"daemon returned invalid JSON for {path}: {exc}") from exc

    def _request_empty(self, method: str, path: str, payload: dict | None = None) -> None:
        self._request(method, path, payload)

    def _request(self, method: str, path: str, payload: dict | None = None) -> bytes:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(
            f"{self.base_url.rstrip('/')}{path}", data=data, headers=headers, method=method
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return response.read()
        except error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise DaemonClientError(
                f"daemon {method} {path} failed with HTTP {exc.code}: {message}"
            ) from exc
        except OSError as exc:
            raise DaemonClientError(f"daemon {method} {path} failed: {exc}") from exc
