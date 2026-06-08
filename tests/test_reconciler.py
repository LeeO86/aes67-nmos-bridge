from __future__ import annotations

import pytest

from aes67_nmos_bridge.models import (
    BridgeConfig,
    DaemonState,
    DaemonStream,
    ReceiverConfig,
    SenderConfig,
)
from aes67_nmos_bridge.ownership import managed_name
from aes67_nmos_bridge.reconciler import ReconcileError, Reconciler


class FakeDaemon:
    def __init__(self, state: DaemonState):
        self.state = state
        self.calls: list[tuple[str, int, dict | None]] = []

    def get_state(self) -> DaemonState:
        return self.state

    def put_sender(self, daemon_id: int, payload: dict) -> None:
        self.calls.append(("put_sender", daemon_id, payload))

    def delete_sender(self, daemon_id: int) -> None:
        self.calls.append(("delete_sender", daemon_id, None))

    def put_receiver(self, daemon_id: int, payload: dict) -> None:
        self.calls.append(("put_receiver", daemon_id, payload))

    def delete_receiver(self, daemon_id: int) -> None:
        self.calls.append(("delete_receiver", daemon_id, None))


def test_creates_missing_configured_streams() -> None:
    config = BridgeConfig(
        senders=(SenderConfig("main", 1, "Main", (0, 1)),),
        receivers=(ReceiverConfig("return", 2, "Return", (2, 3), sdp=_sdp()),),
    )
    daemon = FakeDaemon(DaemonState())

    report = Reconciler(config, daemon).reconcile()

    assert [operation.action for operation in report.operations] == ["create", "create"]
    assert daemon.calls[0][0:2] == ("put_sender", 1)
    assert daemon.calls[0][2]["name"] == "NMOS[default]/sender/main Main"
    assert daemon.calls[1][0:2] == ("put_receiver", 2)
    assert daemon.calls[1][2]["use_sdp"] is True


def test_updates_drifted_managed_sender() -> None:
    config = BridgeConfig(senders=(SenderConfig("main", 1, "Main", (0, 1), codec="L24"),))
    existing = DaemonStream(
        "sender",
        1,
        {
            "id": 1,
            "name": managed_name("default", "sender", "main", "Main"),
            "enabled": True,
            "io": "Audio Device",
            "codec": "L16",
            "address": "",
            "max_samples_per_packet": 48,
            "ttl": 15,
            "payload_type": 98,
            "dscp": 34,
            "refclk_ptp_traceable": True,
            "map": [0, 1],
        },
    )
    daemon = FakeDaemon(DaemonState(senders=(existing,)))

    report = Reconciler(config, daemon).reconcile()

    assert [(op.action, op.reason) for op in report.operations] == [
        ("update", "daemon stream drifted")
    ]
    assert daemon.calls == [("put_sender", 1, report.operations[0].payload)]
    assert daemon.calls[0][2]["codec"] == "L24"


def test_deletes_orphaned_managed_streams_but_keeps_unmanaged() -> None:
    orphan = DaemonStream(
        "sender",
        7,
        {"id": 7, "name": managed_name("default", "sender", "old", "Old")},
    )
    unmanaged = DaemonStream("sender", 8, {"id": 8, "name": "Manual source"})
    daemon = FakeDaemon(DaemonState(senders=(orphan, unmanaged)))

    report = Reconciler(BridgeConfig(), daemon).reconcile()

    assert [(op.action, op.side, op.daemon_id) for op in report.operations] == [
        ("delete", "sender", 7)
    ]
    assert daemon.calls == [("delete_sender", 7, None)]


def test_refuses_to_overwrite_unmanaged_stream_in_configured_slot() -> None:
    config = BridgeConfig(senders=(SenderConfig("main", 1, "Main", (0, 1)),))
    unmanaged = DaemonStream("sender", 1, {"id": 1, "name": "Manual source"})
    daemon = FakeDaemon(DaemonState(senders=(unmanaged,)))

    with pytest.raises(ReconcileError, match="occupied by unmanaged stream"):
        Reconciler(config, daemon).plan()


def test_disabled_receiver_deletes_owned_sink() -> None:
    config = BridgeConfig(
        receivers=(ReceiverConfig("ret", 2, "Return", (0, 1), sdp=_sdp(), enabled=False),),
    )
    existing = DaemonStream(
        "receiver",
        2,
        {"id": 2, "name": managed_name("default", "receiver", "ret", "Return")},
    )
    daemon = FakeDaemon(DaemonState(receivers=(existing,)))

    report = Reconciler(config, daemon).reconcile()

    assert [(op.action, op.side, op.daemon_id) for op in report.operations] == [
        ("delete", "receiver", 2)
    ]
    assert daemon.calls == [("delete_receiver", 2, None)]


def test_disabled_receiver_does_not_create_sink() -> None:
    config = BridgeConfig(
        receivers=(ReceiverConfig("ret", 2, "Return", (0, 1), sdp=_sdp(), enabled=False),),
    )
    daemon = FakeDaemon(DaemonState())

    report = Reconciler(config, daemon).reconcile()

    assert report.operations == ()
    assert daemon.calls == []


def test_disabled_receiver_leaves_unmanaged_slot_untouched() -> None:
    config = BridgeConfig(
        receivers=(ReceiverConfig("ret", 2, "Return", (0, 1), sdp=_sdp(), enabled=False),),
    )
    unmanaged = DaemonStream("receiver", 2, {"id": 2, "name": "Manual sink"})
    daemon = FakeDaemon(DaemonState(receivers=(unmanaged,)))

    report = Reconciler(config, daemon).reconcile()

    assert report.operations == ()
    assert daemon.calls == []


def test_dry_run_does_not_call_daemon_mutations() -> None:
    config = BridgeConfig(senders=(SenderConfig("main", 1, "Main", (0, 1)),))
    daemon = FakeDaemon(DaemonState())

    report = Reconciler(config, daemon).reconcile(dry_run=True)

    assert report.changed is True
    assert report.dry_run is True
    assert daemon.calls == []


def _sdp() -> str:
    return (
        "v=0\n"
        "o=- 1 0 IN IP4 192.0.2.10\n"
        "s=Remote AES67 Source\n"
        "c=IN IP4 239.2.0.10/15\n"
        "t=0 0\n"
        "m=audio 5004 RTP/AVP 98\n"
        "a=rtpmap:98 L24/48000/2\n"
    )
