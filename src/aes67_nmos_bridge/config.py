from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BridgeConfig, ReceiverConfig, SenderConfig


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> BridgeConfig:
    with Path(path).open(encoding="utf-8") as file:
        raw = json.load(file)

    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a JSON object")

    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> BridgeConfig:
    senders = tuple(_parse_sender(item) for item in raw.get("senders", []))
    receivers = tuple(_parse_receiver(item) for item in raw.get("receivers", []))
    config = BridgeConfig(
        daemon_base_url=str(raw.get("daemon_base_url", BridgeConfig.daemon_base_url)).rstrip("/"),
        namespace=str(raw.get("namespace", BridgeConfig.namespace)),
        reconcile_interval_seconds=float(
            raw.get("reconcile_interval_seconds", BridgeConfig.reconcile_interval_seconds)
        ),
        http_host=str(raw.get("http_host", BridgeConfig.http_host)),
        http_port=int(raw.get("http_port", BridgeConfig.http_port)),
        advertised_host=str(raw.get("advertised_host", BridgeConfig.advertised_host)),
        node_label=str(raw.get("node_label", BridgeConfig.node_label)),
        senders=senders,
        receivers=receivers,
    )
    _validate_config(config)
    return config


def _parse_sender(raw: dict[str, Any]) -> SenderConfig:
    return SenderConfig(
        nmos_id=str(_required(raw, "nmos_id")),
        daemon_id=int(_required(raw, "daemon_id")),
        label=str(_required(raw, "label")),
        map=tuple(int(value) for value in _required(raw, "map")),
        enabled=bool(raw.get("enabled", SenderConfig.enabled)),
        io=str(raw.get("io", SenderConfig.io)),
        codec=str(raw.get("codec", SenderConfig.codec)),
        address=str(raw.get("address", SenderConfig.address)),
        rtp_port=int(raw.get("rtp_port", SenderConfig.rtp_port)),
        sample_rate=int(raw.get("sample_rate", SenderConfig.sample_rate)),
        max_samples_per_packet=int(
            raw.get("max_samples_per_packet", SenderConfig.max_samples_per_packet)
        ),
        ttl=int(raw.get("ttl", SenderConfig.ttl)),
        payload_type=int(raw.get("payload_type", SenderConfig.payload_type)),
        dscp=int(raw.get("dscp", SenderConfig.dscp)),
        refclk_ptp_traceable=bool(
            raw.get("refclk_ptp_traceable", SenderConfig.refclk_ptp_traceable)
        ),
        description=str(raw.get("description", SenderConfig.description)),
    )


def _parse_receiver(raw: dict[str, Any]) -> ReceiverConfig:
    return ReceiverConfig(
        nmos_id=str(_required(raw, "nmos_id")),
        daemon_id=int(_required(raw, "daemon_id")),
        label=str(_required(raw, "label")),
        map=tuple(int(value) for value in _required(raw, "map")),
        sdp=str(_required(raw, "sdp")),
        enabled=bool(raw.get("enabled", ReceiverConfig.enabled)),
        io=str(raw.get("io", ReceiverConfig.io)),
        delay=int(raw.get("delay", ReceiverConfig.delay)),
        source=str(raw.get("source", ReceiverConfig.source)),
        sample_rate=int(raw.get("sample_rate", ReceiverConfig.sample_rate)),
        ignore_refclk_gmid=bool(raw.get("ignore_refclk_gmid", ReceiverConfig.ignore_refclk_gmid)),
        description=str(raw.get("description", ReceiverConfig.description)),
    )


def config_to_dict(config: BridgeConfig) -> dict[str, Any]:
    """Serialise a :class:`BridgeConfig` back to a JSON-compatible dict.

    The result round-trips through :func:`parse_config`, so the desired-state
    store can persist mutations made through the NMOS API.
    """

    return {
        "daemon_base_url": config.daemon_base_url,
        "namespace": config.namespace,
        "reconcile_interval_seconds": config.reconcile_interval_seconds,
        "http_host": config.http_host,
        "http_port": config.http_port,
        "advertised_host": config.advertised_host,
        "node_label": config.node_label,
        "senders": [
            {
                "nmos_id": sender.nmos_id,
                "daemon_id": sender.daemon_id,
                "label": sender.label,
                "map": list(sender.map),
                "enabled": sender.enabled,
                "io": sender.io,
                "codec": sender.codec,
                "address": sender.address,
                "rtp_port": sender.rtp_port,
                "sample_rate": sender.sample_rate,
                "max_samples_per_packet": sender.max_samples_per_packet,
                "ttl": sender.ttl,
                "payload_type": sender.payload_type,
                "dscp": sender.dscp,
                "refclk_ptp_traceable": sender.refclk_ptp_traceable,
                "description": sender.description,
            }
            for sender in config.senders
        ],
        "receivers": [
            {
                "nmos_id": receiver.nmos_id,
                "daemon_id": receiver.daemon_id,
                "label": receiver.label,
                "map": list(receiver.map),
                "sdp": receiver.sdp,
                "enabled": receiver.enabled,
                "io": receiver.io,
                "delay": receiver.delay,
                "source": receiver.source,
                "sample_rate": receiver.sample_rate,
                "ignore_refclk_gmid": receiver.ignore_refclk_gmid,
                "description": receiver.description,
            }
            for receiver in config.receivers
        ],
    }


def _required(raw: dict[str, Any], key: str) -> Any:
    if key not in raw:
        raise ConfigError(f"missing required key: {key}")
    return raw[key]


def _validate_config(config: BridgeConfig) -> None:
    if not config.namespace:
        raise ConfigError("namespace must not be empty")
    if config.reconcile_interval_seconds <= 0:
        raise ConfigError("reconcile_interval_seconds must be greater than zero")

    for side, streams in (("sender", config.senders), ("receiver", config.receivers)):
        daemon_ids: set[int] = set()
        nmos_ids: set[str] = set()
        for stream in streams:
            if stream.daemon_id < 0 or stream.daemon_id > 63:
                raise ConfigError(f"{side} {stream.nmos_id} daemon_id must be in range 0..63")
            if not stream.map:
                raise ConfigError(f"{side} {stream.nmos_id} map must not be empty")
            if stream.daemon_id in daemon_ids:
                raise ConfigError(f"duplicate {side} daemon_id: {stream.daemon_id}")
            if stream.nmos_id in nmos_ids:
                raise ConfigError(f"duplicate {side} nmos_id: {stream.nmos_id}")
            daemon_ids.add(stream.daemon_id)
            nmos_ids.add(stream.nmos_id)
