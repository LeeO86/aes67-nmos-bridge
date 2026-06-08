"""NMOS IS-04/IS-05 data model derived from the bridge's desired state.

This module turns the config-backed :class:`BridgeConfig` into the NMOS
resource graph (Node -> Device -> Source/Flow/Sender, Receiver) and into the
IS-05 connection representations (transport params, constraints, transport
file). It performs no I/O and never talks to the daemon, so it is fully
unit-testable.

Resource IDs are deterministic UUIDv5 values derived from the configured
namespace and ``nmos_id``, so they are stable across restarts and across hosts
that share the same config.
"""

from __future__ import annotations

import uuid
from typing import Any

from .models import BridgeConfig, ReceiverConfig, SenderConfig, StreamSide
from .sdp import parse_sdp_connection, sender_sdp

NODE_API_VERSION = "v1.3"
CONNECTION_API_VERSION = "v1.1"

TRANSPORT_RTP_MCAST = "urn:x-nmos:transport:rtp.mcast"
FORMAT_AUDIO = "urn:x-nmos:format:audio"
DEVICE_TYPE_GENERIC = "urn:x-nmos:device:generic"
SR_CTRL_TYPE = f"urn:x-nmos:control:sr-ctrl/{CONNECTION_API_VERSION}"

# Stable root namespace for all bridge-generated UUIDs.
_ROOT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "aes67-nmos-bridge.amwa.tv")


def stable_uuid(namespace: str, *parts: str) -> str:
    return str(uuid.uuid5(_ROOT_NAMESPACE, "/".join((namespace, *parts))))


def node_id(namespace: str) -> str:
    return stable_uuid(namespace, "node")


def device_id(namespace: str) -> str:
    return stable_uuid(namespace, "device")


def source_id(namespace: str, nmos_id: str) -> str:
    return stable_uuid(namespace, "source", nmos_id)


def flow_id(namespace: str, nmos_id: str) -> str:
    return stable_uuid(namespace, "flow", nmos_id)


def sender_id(namespace: str, nmos_id: str) -> str:
    return stable_uuid(namespace, "sender", nmos_id)


def receiver_id(namespace: str, nmos_id: str) -> str:
    return stable_uuid(namespace, "receiver", nmos_id)


def _media_type(codec: str) -> str:
    return f"audio/{codec}"


def _bit_depth(codec: str) -> int | None:
    digits = "".join(ch for ch in codec if ch.isdigit())
    return int(digits) if digits else None


def _channels(count: int) -> list[dict[str, str]]:
    # Symbols follow SMPTE ST 2110-30 / NMOS audio channel symbols where simple.
    return [
        {"label": f"Channel {index + 1}", "symbol": f"CH{index + 1:02d}"}
        for index in range(count)
    ]


class NmosModel:
    """Builds NMOS resources and connection representations from a config.

    A new instance is cheap; callers construct one per request from the current
    desired-state snapshot.
    """

    def __init__(self, config: BridgeConfig, *, base_href: str, version: str) -> None:
        self.config = config
        self.base_href = base_href.rstrip("/")
        self.version = version

    # -- identity helpers -------------------------------------------------

    @property
    def namespace(self) -> str:
        return self.config.namespace

    @property
    def node_id(self) -> str:
        return node_id(self.namespace)

    @property
    def device_id(self) -> str:
        return device_id(self.namespace)

    def connection_base(self) -> str:
        return f"{self.base_href}/x-nmos/connection/{CONNECTION_API_VERSION}"

    # -- IS-04 resources --------------------------------------------------

    def node(self) -> dict[str, Any]:
        label = self.config.node_label or f"aes67-nmos-bridge ({self.namespace})"
        return {
            "id": self.node_id,
            "version": self.version,
            "label": label,
            "description": "AES67 NMOS bridge for bondagit/aes67-linux-daemon",
            "tags": {},
            "href": f"{self.base_href}/",
            "caps": {},
            "api": {
                "versions": [NODE_API_VERSION],
                "endpoints": [
                    {
                        "host": _href_host(self.base_href),
                        "port": _href_port(self.base_href),
                        "protocol": "http",
                    }
                ],
            },
            "services": [],
            "clocks": [
                {
                    "name": "clk0",
                    "ref_type": "ptp",
                    "traceable": True,
                    "version": "IEEE1588-2008",
                    "gmid": "00-00-00-00-00-00-00-00",
                    "locked": True,
                }
            ],
            "interfaces": [{"name": "eth0", "chassis_id": None, "port_id": None}],
        }

    def device(self) -> dict[str, Any]:
        return {
            "id": self.device_id,
            "version": self.version,
            "label": self.config.node_label or f"aes67-nmos-bridge device ({self.namespace})",
            "description": "AES67 daemon control device",
            "tags": {},
            "type": DEVICE_TYPE_GENERIC,
            "node_id": self.node_id,
            "senders": [sender_id(self.namespace, s.nmos_id) for s in self.config.senders],
            "receivers": [receiver_id(self.namespace, r.nmos_id) for r in self.config.receivers],
            "controls": [
                {"href": f"{self.connection_base()}/", "type": SR_CTRL_TYPE},
            ],
        }

    def sources(self) -> list[dict[str, Any]]:
        return [self._source(sender) for sender in self.config.senders]

    def _source(self, sender: SenderConfig) -> dict[str, Any]:
        return {
            "id": source_id(self.namespace, sender.nmos_id),
            "version": self.version,
            "label": sender.label,
            "description": sender.description,
            "tags": {},
            "caps": {},
            "device_id": self.device_id,
            "parents": [],
            "clock_name": "clk0",
            "format": FORMAT_AUDIO,
            "channels": _channels(max(1, len(sender.map))),
        }

    def flows(self) -> list[dict[str, Any]]:
        return [self._flow(sender) for sender in self.config.senders]

    def _flow(self, sender: SenderConfig) -> dict[str, Any]:
        flow: dict[str, Any] = {
            "id": flow_id(self.namespace, sender.nmos_id),
            "version": self.version,
            "label": sender.label,
            "description": sender.description,
            "tags": {},
            "source_id": source_id(self.namespace, sender.nmos_id),
            "device_id": self.device_id,
            "parents": [],
            "format": FORMAT_AUDIO,
            "media_type": _media_type(sender.codec),
            "sample_rate": {"numerator": sender.sample_rate, "denominator": 1},
        }
        bit_depth = _bit_depth(sender.codec)
        if bit_depth is not None:
            flow["bit_depth"] = bit_depth
        return flow

    def senders(self) -> list[dict[str, Any]]:
        return [self._sender(sender) for sender in self.config.senders]

    def _sender(self, sender: SenderConfig) -> dict[str, Any]:
        sid = sender_id(self.namespace, sender.nmos_id)
        return {
            "id": sid,
            "version": self.version,
            "label": sender.label,
            "description": sender.description,
            "tags": {},
            "caps": {},
            "flow_id": flow_id(self.namespace, sender.nmos_id),
            "transport": TRANSPORT_RTP_MCAST,
            "device_id": self.device_id,
            "manifest_href": f"{self.connection_base()}/single/senders/{sid}/transportfile/",
            "interface_bindings": ["eth0"],
            "subscription": {"receiver_id": None, "active": bool(sender.enabled)},
        }

    def receivers(self) -> list[dict[str, Any]]:
        return [self._receiver(receiver) for receiver in self.config.receivers]

    def _receiver(self, receiver: ReceiverConfig) -> dict[str, Any]:
        rid = receiver_id(self.namespace, receiver.nmos_id)
        return {
            "id": rid,
            "version": self.version,
            "label": receiver.label,
            "description": receiver.description,
            "tags": {},
            "device_id": self.device_id,
            "transport": TRANSPORT_RTP_MCAST,
            "interface_bindings": ["eth0"],
            "subscription": {"sender_id": None, "active": bool(receiver.enabled)},
            "format": FORMAT_AUDIO,
            "caps": {"media_types": ["audio/L24", "audio/L16"]},
        }

    # -- IS-05 connection representations ---------------------------------

    def sender_transport_params(self, sender: SenderConfig) -> list[dict[str, Any]]:
        return [
            {
                "source_ip": "auto",
                "destination_ip": sender.address or "auto",
                "source_port": sender.rtp_port,
                "destination_port": sender.rtp_port,
                "rtp_enabled": bool(sender.enabled),
            }
        ]

    def receiver_transport_params(self, receiver: ReceiverConfig) -> list[dict[str, Any]]:
        parsed = parse_sdp_connection(receiver.sdp)
        return [
            {
                "source_ip": parsed.source_ip,
                "multicast_ip": parsed.multicast_ip,
                "interface_ip": "auto",
                "destination_port": parsed.destination_port
                if parsed.destination_port is not None
                else "auto",
                "rtp_enabled": bool(receiver.enabled),
            }
        ]

    def sender_constraints(self) -> list[dict[str, Any]]:
        return [
            {
                "source_ip": {},
                "destination_ip": {},
                "source_port": {},
                "destination_port": {},
                "rtp_enabled": {},
            }
        ]

    def receiver_constraints(self) -> list[dict[str, Any]]:
        return [
            {
                "source_ip": {},
                "multicast_ip": {},
                "interface_ip": {},
                "destination_port": {},
                "rtp_enabled": {},
            }
        ]

    def sender_transport_file(self, sender: SenderConfig) -> str:
        return sender_sdp(sender, session_id=_session_id(source_id(self.namespace, sender.nmos_id)))


def _session_id(resource_uuid: str) -> str:
    # Use the low 32 bits of the UUID as a stable numeric SDP session id.
    return str(int(resource_uuid.replace("-", ""), 16) % (2**31))


def _href_host(base_href: str) -> str:
    without_scheme = base_href.split("://", 1)[-1]
    host_port = without_scheme.split("/", 1)[0]
    if host_port.startswith("["):  # IPv6 literal
        return host_port[1 : host_port.index("]")]
    return host_port.rsplit(":", 1)[0] if ":" in host_port else host_port


def _href_port(base_href: str) -> int:
    without_scheme = base_href.split("://", 1)[-1]
    host_port = without_scheme.split("/", 1)[0]
    if host_port.startswith("[") and "]" in host_port:
        after = host_port[host_port.index("]") + 1 :]
        return int(after[1:]) if after.startswith(":") else 80
    return int(host_port.rsplit(":", 1)[1]) if ":" in host_port else 80


def side_resource_ids(config: BridgeConfig) -> dict[StreamSide, dict[str, str]]:
    """Map ``nmos_id`` -> resource UUID for each side (for fast lookup)."""

    return {
        "sender": {s.nmos_id: sender_id(config.namespace, s.nmos_id) for s in config.senders},
        "receiver": {
            r.nmos_id: receiver_id(config.namespace, r.nmos_id) for r in config.receivers
        },
    }
