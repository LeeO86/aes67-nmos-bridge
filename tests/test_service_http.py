from __future__ import annotations

import json
import urllib.request
from urllib.error import HTTPError

from aes67_nmos_bridge.models import BridgeConfig, DaemonState, ReceiverConfig, SenderConfig
from aes67_nmos_bridge.nmos import receiver_id
from aes67_nmos_bridge.service import BridgeService
from aes67_nmos_bridge.store import DesiredStateStore

SDP = (
    "v=0\no=- 1 0 IN IP4 192.0.2.10\ns=x\n"
    "c=IN IP4 239.2.0.10/15\nt=0 0\nm=audio 5004 RTP/AVP 98\n"
)
NEW_SDP = (
    "v=0\no=- 2 0 IN IP4 192.0.2.20\ns=y\n"
    "c=IN IP4 239.9.9.9/15\nt=0 0\nm=audio 5006 RTP/AVP 98\n"
)


class FakeDaemon:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict | None]] = []

    def get_state(self) -> DaemonState:
        return DaemonState()

    def put_sender(self, daemon_id: int, payload: dict) -> None:
        self.calls.append(("put_sender", daemon_id, payload))

    def delete_sender(self, daemon_id: int) -> None:
        self.calls.append(("delete_sender", daemon_id, None))

    def put_receiver(self, daemon_id: int, payload: dict) -> None:
        self.calls.append(("put_receiver", daemon_id, payload))

    def delete_receiver(self, daemon_id: int) -> None:
        self.calls.append(("delete_receiver", daemon_id, None))


def _service() -> tuple[BridgeService, FakeDaemon]:
    config = BridgeConfig(
        http_host="127.0.0.1",
        http_port=0,
        senders=(SenderConfig("main", 0, "Main", (0, 1), address="239.2.0.1"),),
        receivers=(ReceiverConfig("ret", 1, "Return", (0, 1), sdp=SDP),),
    )
    daemon = FakeDaemon()
    return BridgeService(DesiredStateStore(config), daemon), daemon


def _request(port: int, method: str, path: str, body: bytes | None = None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def test_http_server_serves_health_and_nmos() -> None:
    import threading

    service, daemon = _service()
    server = service._make_server()
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # readyz before any reconcile -> 503
        status, _ = _request(port, "GET", "/readyz")
        assert status == 503

        service.reconcile_once()
        status, body = _request(port, "GET", "/healthz")
        assert status == 200
        assert json.loads(body)["ok"] is True

        # IS-04 node self over HTTP
        status, body = _request(port, "GET", "/x-nmos/node/v1.3/self/")
        assert status == 200
        assert json.loads(body)["api"]["versions"] == ["v1.3"]

        # IS-05 immediate activation over HTTP pushes to the daemon
        rid = receiver_id("default", "ret")
        patch = json.dumps(
            {
                "transport_file": {"data": NEW_SDP, "type": "application/sdp"},
                "activation": {"mode": "activate_immediate"},
            }
        ).encode("utf-8")
        status, body = _request(
            port, "PATCH", f"/x-nmos/connection/v1.1/single/receivers/{rid}/staged/", patch
        )
        assert status == 200
        assert service.store.find_receiver("ret").sdp == NEW_SDP
        put_calls = [call for call in daemon.calls if call[0] == "put_receiver"]
        assert put_calls and put_calls[-1][2]["sdp"] == NEW_SDP

        status, _ = _request(port, "GET", "/x-nmos/does/not/exist")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
