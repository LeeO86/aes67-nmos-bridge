from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .models import BridgeConfig, ReconcileReport
from .reconciler import DaemonControl, Reconciler


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
    def __init__(self, config: BridgeConfig, daemon: DaemonControl):
        self.config = config
        self.reconciler = Reconciler(config, daemon)
        self.status = ServiceStatus()
        self._stop = threading.Event()

    def run_forever(self) -> None:
        server = self._make_server()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            while not self._stop.is_set():
                self.reconcile_once()
                self._stop.wait(self.config.reconcile_interval_seconds)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

    def stop(self) -> None:
        self._stop.set()

    def reconcile_once(self, dry_run: bool = False) -> ReconcileReport:
        try:
            report = self.reconciler.reconcile(dry_run=dry_run)
            self.status.mark_success(report)
            return report
        except Exception as exc:
            self.status.mark_error(exc)
            raise

    def _make_server(self) -> ThreadingHTTPServer:
        status = self.status

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in {"/healthz", "/readyz", "/status"}:
                    self.send_error(404)
                    return

                snapshot = status.snapshot()
                if self.path == "/readyz" and snapshot["last_success_epoch"] is None:
                    self.send_response(503)
                elif snapshot["ok"]:
                    self.send_response(200)
                else:
                    self.send_response(500)

                body = json.dumps(snapshot).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        return ThreadingHTTPServer((self.config.http_host, self.config.http_port), Handler)
