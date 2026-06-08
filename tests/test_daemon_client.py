from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from aes67_nmos_bridge.daemon_client import DaemonClient


def test_daemon_client_maps_rest_endpoints() -> None:
    records: list[tuple[str, str, dict[str, Any] | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            records.append(("GET", self.path, None))
            self._send_json(
                {
                    "sources": [{"id": 1, "name": "Source"}],
                    "sinks": [{"id": 2, "name": "Sink"}],
                }
            )

        def do_PUT(self) -> None:  # noqa: N802
            records.append(("PUT", self.path, self._read_json()))
            self._send_json({})

        def do_DELETE(self) -> None:  # noqa: N802
            records.append(("DELETE", self.path, None))
            self._send_json({})

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers["Content-Length"])
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _send_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = DaemonClient(f"http://127.0.0.1:{server.server_port}")
        state = client.get_state()
        client.put_sender(1, {"name": "Sender"})
        client.put_receiver(2, {"name": "Receiver"})
        client.delete_sender(3)
        client.delete_receiver(4)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert state.senders[0].id == 1
    assert state.receivers[0].id == 2
    assert records == [
        ("GET", "/api/streams", None),
        ("PUT", "/api/source/1", {"name": "Sender"}),
        ("PUT", "/api/sink/2", {"name": "Receiver"}),
        ("DELETE", "/api/source/3", None),
        ("DELETE", "/api/sink/4", None),
    ]
