from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StreamSide = Literal["sender", "receiver"]


@dataclass(frozen=True)
class SenderConfig:
    nmos_id: str
    daemon_id: int
    label: str
    map: tuple[int, ...]
    enabled: bool = True
    io: str = "Audio Device"
    codec: str = "L24"
    address: str = ""
    max_samples_per_packet: int = 48
    ttl: int = 15
    payload_type: int = 98
    dscp: int = 34
    refclk_ptp_traceable: bool = True


@dataclass(frozen=True)
class ReceiverConfig:
    nmos_id: str
    daemon_id: int
    label: str
    map: tuple[int, ...]
    sdp: str
    io: str = "Audio Device"
    delay: int = 576
    source: str = ""
    ignore_refclk_gmid: bool = False


@dataclass(frozen=True)
class BridgeConfig:
    daemon_base_url: str = "http://127.0.0.1:8080"
    namespace: str = "default"
    reconcile_interval_seconds: float = 5.0
    http_host: str = "127.0.0.1"
    http_port: int = 8090
    senders: tuple[SenderConfig, ...] = field(default_factory=tuple)
    receivers: tuple[ReceiverConfig, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DaemonStream:
    side: StreamSide
    id: int
    payload: dict[str, Any]

    @property
    def name(self) -> str:
        value = self.payload.get("name", "")
        return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class DaemonState:
    senders: tuple[DaemonStream, ...] = field(default_factory=tuple)
    receivers: tuple[DaemonStream, ...] = field(default_factory=tuple)

    @classmethod
    def from_streams_payload(cls, payload: dict[str, Any]) -> DaemonState:
        return cls(
            senders=tuple(
                DaemonStream("sender", int(source["id"]), dict(source))
                for source in payload.get("sources", [])
            ),
            receivers=tuple(
                DaemonStream("receiver", int(sink["id"]), dict(sink))
                for sink in payload.get("sinks", [])
            ),
        )


@dataclass(frozen=True)
class PlannedOperation:
    action: Literal["create", "update", "delete"]
    side: StreamSide
    daemon_id: int
    reason: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReconcileReport:
    operations: tuple[PlannedOperation, ...]
    dry_run: bool

    @property
    def changed(self) -> bool:
        return bool(self.operations)
