from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .api import NmosApi
from .models import BridgeConfig, ReconcileReport
from .reconciler import DaemonControl, Reconciler
from .store import DesiredStateStore

_HEALTH_PATHS = {"/healthz", "/readyz", "/status"}


@dataclass
class ServiceStatus:
    last_success_epoch: float | None = None
    last_error: str | None = None
    last_report: ReconcileReport | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def mark_success(self, report: ReconcileReport) -> None:
        with self._lock:
            self.last_success_epoch = time.time()
            self.last_error = None
            self.last_report = report

    def mark_error(self, error: Exception) -> None:
        with self._lock:
            self.last_error = str(error)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": self.last_error is None,
                "last_success_epoch": self.last_success_epoch,
                "last_error": self.last_error,
                "last_operations": [
                    {
                        "action": operation.action,
                        "side": operation.side,
                        "daemon_id": operation.daemon_id,
                        "reason": operation.reason,
                    }
                    for operation in (self.last_report.operations if self.last_report else ())
                ],
            }


class BridgeService:
    def __init__(self, config: BridgeConfig | DesiredStateStore, daemon: DaemonControl):
        self.store = config if isinstance(config, DesiredStateStore) else DesiredStateStore(config)
        self.daemon = daemon
        self.status = ServiceStatus()
        self.api = NmosApi(self.store, reconcile_cb=self._reconcile_for_api)
        self._stop = threading.Event()

    @property
    def config(self) -> BridgeConfig:
        return self.store.snapshot()

    def run_forever(self) -> None:
        server = self._make_server()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            while not self._stop.is_set():
                # Keep the loop alive on transient daemon errors; status records it.
                with contextlib.suppress(Exception):
                    self.reconcile_once()
                self._stop.wait(self.store.snapshot().reconcile_interval_seconds)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

    def stop(self) -> None:
        self._stop.set()

    def reconcile_once(self, dry_run: bool = False) -> ReconcileReport:
        try:
            reconciler = Reconciler(self.store.snapshot(), self.daemon)
            report = reconciler.reconcile(dry_run=dry_run)
            self.status.mark_success(report)
            return report
        except Exception as exc:
            self.status.mark_error(exc)
            raise

    def _reconcile_for_api(self) -> None:
        # Used as the IS-05 immediate-activation callback. The desired state has
        # already been updated in the store; push it to the daemon now.
        self.reconcile_once()

    def _make_server(self) -> ThreadingHTTPServer:
        status = self.status
        api = self.api
        bind_host = self.store.snapshot().http_host
        bind_port = self.store.snapshot().http_port

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path in _HEALTH_PATHS:
                    self._handle_health()
                    return
                self._handle_api("GET", None)

            def do_PATCH(self) -> None:  # noqa: N802
                body = self._read_body()
                self._handle_api("PATCH", body)

            def _handle_health(self) -> None:
                snapshot = status.snapshot()
                if self.path == "/readyz" and snapshot["last_success_epoch"] is None:
                    code = 503
                elif snapshot["ok"]:
                    code = 200
                else:
                    code = 500
                self._write_json(code, snapshot)

            def _handle_api(self, method: str, body: bytes | None) -> None:
                response = api.dispatch(method, self.path.split("?", 1)[0], body)
                self.send_response(response.status)
                payload = response.encoded()
                self.send_header("Content-Type", response.content_type)
                self.send_header("Content-Length", str(len(payload)))
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(payload)

            def _read_body(self) -> bytes | None:
                length = self.headers.get("Content-Length")
                if length is None:
                    return None
                try:
                    return self.rfile.read(int(length))
                except (TypeError, ValueError):
                    return None

            def _write_json(self, code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        return ThreadingHTTPServer((bind_host, bind_port), Handler)
