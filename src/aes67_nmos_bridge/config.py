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
        enabled=bool(raw.get("enabled", True)),
        io=str(raw.get("io", SenderConfig.io)),
        codec=str(raw.get("codec", SenderConfig.codec)),
        address=str(raw.get("address", SenderConfig.address)),
        max_samples_per_packet=int(
            raw.get("max_samples_per_packet", SenderConfig.max_samples_per_packet)
        ),
        ttl=int(raw.get("ttl", SenderConfig.ttl)),
        payload_type=int(raw.get("payload_type", SenderConfig.payload_type)),
        dscp=int(raw.get("dscp", SenderConfig.dscp)),
        refclk_ptp_traceable=bool(
            raw.get("refclk_ptp_traceable", SenderConfig.refclk_ptp_traceable)
        ),
    )


def _parse_receiver(raw: dict[str, Any]) -> ReceiverConfig:
    return ReceiverConfig(
        nmos_id=str(_required(raw, "nmos_id")),
        daemon_id=int(_required(raw, "daemon_id")),
        label=str(_required(raw, "label")),
        map=tuple(int(value) for value in _required(raw, "map")),
        sdp=str(_required(raw, "sdp")),
        io=str(raw.get("io", ReceiverConfig.io)),
        delay=int(raw.get("delay", ReceiverConfig.delay)),
        source=str(raw.get("source", ReceiverConfig.source)),
        ignore_refclk_gmid=bool(raw.get("ignore_refclk_gmid", ReceiverConfig.ignore_refclk_gmid)),
    )


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
