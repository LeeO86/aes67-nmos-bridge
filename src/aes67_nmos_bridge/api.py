"""Native NMOS IS-04 Node API + IS-05 Connection API surface.

This is a small, dependency-free dispatcher that maps NMOS HTTP paths onto the
desired-state store and :class:`NmosModel`. It returns plain
:class:`HttpResponse` values so it can be unit tested without sockets, then
wired into the stdlib HTTP server in :mod:`aes67_nmos_bridge.service`.

Implemented:

* IS-04 Node API (read-only): self, devices, sources, flows, senders, receivers.
* IS-05 Connection API (read): constraints, staged, active, transporttype,
  transportfile (senders).
* IS-05 Connection API (write): PATCH ``/staged`` with **immediate** activation
  for senders and receivers, which updates the desired-state store (source of
  truth) and triggers a daemon reconcile.

Not implemented yet (returns 501): scheduled activations and bulk operations.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .models import BridgeConfig, ReceiverConfig, SenderConfig, StreamSide
from .nmos import (
    CONNECTION_API_VERSION,
    NODE_API_VERSION,
    TRANSPORT_RTP_MCAST,
    NmosModel,
    receiver_id,
    sender_id,
)
from .store import DesiredStateStore

_IMMEDIATE = "activate_immediate"
_SCHEDULED_MODES = {"activate_scheduled_absolute", "activate_scheduled_relative"}
_NULL_ACTIVATION = {"mode": None, "requested_time": None, "activation_time": None}


@dataclass
class HttpResponse:
    status: int
    body: Any = None
    content_type: str = "application/json"
    raw: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def encoded(self) -> bytes:
        if self.raw is not None:
            return self.raw
        if self.body is None:
            return b""
        return json.dumps(self.body).encode("utf-8")


@dataclass
class _ConnectionState:
    """In-memory IS-05 connection state not captured by the desired config.

    Transport params / master_enable are derived from the desired config (the
    source of truth). This only holds connection-layer extras: the staged
    overlay (for stage-without-activate), the activation metadata to report on
    the active endpoint, and the subscription id.
    """

    staged_overlay: dict[str, Any] | None = None
    active_activation: dict[str, Any] = field(default_factory=lambda: dict(_NULL_ACTIVATION))
    subscription_id: str | None = None


class NmosApi:
    def __init__(
        self,
        store: DesiredStateStore,
        *,
        reconcile_cb: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._reconcile_cb = reconcile_cb
        self._clock = clock
        self._lock = threading.RLock()
        self._state: dict[tuple[StreamSide, str], _ConnectionState] = {}

    # -- public entry point ----------------------------------------------

    def dispatch(self, method: str, path: str, body: bytes | None = None) -> HttpResponse:
        segments = [segment for segment in path.split("/") if segment != ""]
        try:
            if method == "GET":
                return self._dispatch_get(segments)
            if method == "PATCH":
                return self._dispatch_patch(segments, body)
            return _error(405, "method not allowed")
        except _ApiError as exc:
            return _error(exc.status, exc.message)

    # -- model helpers ----------------------------------------------------

    def _model(self, config: BridgeConfig) -> NmosModel:
        return NmosModel(
            config,
            base_href=_base_href(config),
            version=self._store.version_string(),
        )

    def _resource_lookup(self, config: BridgeConfig) -> dict[StreamSide, dict[str, str]]:
        return {
            "sender": {sender_id(config.namespace, s.nmos_id): s.nmos_id for s in config.senders},
            "receiver": {
                receiver_id(config.namespace, r.nmos_id): r.nmos_id for r in config.receivers
            },
        }

    def _state_for(self, side: StreamSide, nmos_id: str) -> _ConnectionState:
        key = (side, nmos_id)
        state = self._state.get(key)
        if state is None:
            state = _ConnectionState()
            self._state[key] = state
        return state

    # -- GET routing ------------------------------------------------------

    def _dispatch_get(self, segments: list[str]) -> HttpResponse:
        if not segments:
            return _json(["x-nmos/"])
        if segments == ["x-nmos"]:
            return _json(["node/", "connection/"])
        if segments[0] != "x-nmos":
            return _error(404, "not found")

        if segments[1:2] == ["node"]:
            return self._get_node(segments[2:])
        if segments[1:2] == ["connection"]:
            return self._get_connection(segments[2:])
        return _error(404, "not found")

    def _get_node(self, rest: list[str]) -> HttpResponse:
        if rest == []:
            return _json([f"{NODE_API_VERSION}/"])
        if rest[0] != NODE_API_VERSION:
            return _error(404, "unsupported node api version")
        rest = rest[1:]
        config = self._store.snapshot()
        model = self._model(config)

        if rest == []:
            return _json(
                ["self/", "devices/", "sources/", "flows/", "senders/", "receivers/"]
            )

        collection = rest[0]
        ident = rest[1] if len(rest) > 1 else None

        if collection == "self":
            return _json(model.node())

        builders: dict[str, Callable[[], list[dict[str, Any]]]] = {
            "devices": lambda: [model.device()],
            "sources": model.sources,
            "flows": model.flows,
            "senders": model.senders,
            "receivers": model.receivers,
        }
        if collection not in builders:
            return _error(404, "not found")
        resources = builders[collection]()
        if ident is None:
            return _json(resources)
        for resource in resources:
            if resource["id"] == ident:
                return _json(resource)
        return _error(404, f"{collection} {ident} not found")

    def _get_connection(self, rest: list[str]) -> HttpResponse:
        if rest == []:
            return _json([f"{CONNECTION_API_VERSION}/"])
        if rest[0] != CONNECTION_API_VERSION:
            return _error(404, "unsupported connection api version")
        rest = rest[1:]

        if rest == []:
            return _json(["bulk/", "single/"])
        if rest == ["single"]:
            return _json(["senders/", "receivers/"])
        if rest == ["bulk"]:
            return _json(["senders/", "receivers/"])
        if rest[0] != "single":
            return _error(404, "not found")

        rest = rest[1:]
        if not rest or rest[0] not in {"senders", "receivers"}:
            return _error(404, "not found")
        side: StreamSide = "sender" if rest[0] == "senders" else "receiver"
        rest = rest[1:]

        config = self._store.snapshot()
        lookup = self._resource_lookup(config)[side]

        if rest == []:
            return _json([f"{rid}/" for rid in lookup])

        resource_uuid = rest[0]
        nmos_id = lookup.get(resource_uuid)
        if nmos_id is None:
            return _error(404, f"{side} {resource_uuid} not found")
        sub = rest[1] if len(rest) > 1 else None

        if sub is None:
            endpoints = ["constraints/", "staged/", "active/", "transporttype/"]
            if side == "sender":
                endpoints.append("transportfile/")
            return _json(endpoints)
        return self._get_connection_leaf(side, nmos_id, sub, config)

    def _get_connection_leaf(
        self, side: StreamSide, nmos_id: str, sub: str, config: BridgeConfig
    ) -> HttpResponse:
        model = self._model(config)
        if sub == "transporttype":
            return _json(TRANSPORT_RTP_MCAST)
        if sub == "constraints":
            return _json(
                model.sender_constraints() if side == "sender" else model.receiver_constraints()
            )
        if sub == "transportfile":
            if side != "sender":
                return _error(404, "receivers have no transportfile endpoint")
            sender = self._require_sender(config, nmos_id)
            return HttpResponse(
                status=200,
                raw=model.sender_transport_file(sender).encode("utf-8"),
                content_type="application/sdp",
            )
        if sub in {"staged", "active"}:
            with self._lock:
                return _json(self._connection_repr(side, nmos_id, config, staged=(sub == "staged")))
        return _error(404, "not found")

    # -- connection representation ---------------------------------------

    def _connection_repr(
        self, side: StreamSide, nmos_id: str, config: BridgeConfig, *, staged: bool
    ) -> dict[str, Any]:
        state = self._state_for(side, nmos_id)
        if staged and state.staged_overlay is not None:
            overlay = json.loads(json.dumps(state.staged_overlay))
            overlay["activation"] = dict(_NULL_ACTIVATION)
            return overlay

        model = self._model(config)
        if side == "sender":
            sender = self._require_sender(config, nmos_id)
            params = model.sender_transport_params(sender)
            master_enable = bool(sender.enabled)
            sub_key = "receiver_id"
        else:
            receiver = self._require_receiver(config, nmos_id)
            params = model.receiver_transport_params(receiver)
            master_enable = bool(receiver.enabled)
            sub_key = "sender_id"

        repr_obj: dict[str, Any] = {
            sub_key: state.subscription_id,
            "master_enable": master_enable,
            "activation": dict(_NULL_ACTIVATION) if staged else dict(state.active_activation),
            "transport_params": params,
        }
        if side == "receiver":
            receiver = self._require_receiver(config, nmos_id)
            repr_obj["transport_file"] = {
                "data": receiver.sdp,
                "type": "application/sdp" if receiver.sdp else None,
            }
        return repr_obj

    # -- PATCH routing ----------------------------------------------------

    def _dispatch_patch(self, segments: list[str], body: bytes | None) -> HttpResponse:
        expected_prefix = ["x-nmos", "connection", CONNECTION_API_VERSION, "single"]
        if segments[: len(expected_prefix)] != expected_prefix:
            return _error(404, "not found")
        rest = segments[len(expected_prefix) :]
        if len(rest) != 3 or rest[0] not in {"senders", "receivers"} or rest[2] != "staged":
            return _error(404, "not found")

        side: StreamSide = "sender" if rest[0] == "senders" else "receiver"
        resource_uuid = rest[1]

        config = self._store.snapshot()
        lookup = self._resource_lookup(config)[side]
        nmos_id = lookup.get(resource_uuid)
        if nmos_id is None:
            return _error(404, f"{side} {resource_uuid} not found")

        patch = _parse_json_object(body)
        with self._lock:
            return self._patch_staged(side, nmos_id, patch)

    def _patch_staged(
        self, side: StreamSide, nmos_id: str, patch: dict[str, Any]
    ) -> HttpResponse:
        config = self._store.snapshot()
        activation = patch.get("activation") or {}
        if not isinstance(activation, dict):
            return _error(400, "activation must be an object")
        mode = activation.get("mode")
        if mode is not None and mode != _IMMEDIATE and mode not in _SCHEDULED_MODES:
            return _error(400, f"invalid activation mode: {mode!r}")
        if mode in _SCHEDULED_MODES:
            return _error(501, "scheduled activation is not implemented")

        base = self._connection_repr(side, nmos_id, config, staged=True)
        merged = _merge_staged(base, patch, side)

        if mode != _IMMEDIATE:
            state = self._state_for(side, nmos_id)
            state.staged_overlay = merged
            response = json.loads(json.dumps(merged))
            response["activation"] = dict(_NULL_ACTIVATION)
            return _json(response)

        # Immediate activation: apply to desired state and reconcile.
        self._apply_to_config(side, nmos_id, merged)
        state = self._state_for(side, nmos_id)
        state.staged_overlay = None
        sub_key = "receiver_id" if side == "sender" else "sender_id"
        if sub_key in patch:
            state.subscription_id = patch[sub_key]
        activation_time = _tai_now(self._clock)
        state.active_activation = {
            "mode": _IMMEDIATE,
            "requested_time": None,
            "activation_time": activation_time,
        }

        reconcile_error: str | None = None
        if self._reconcile_cb is not None:
            try:
                self._reconcile_cb()
            except Exception as exc:  # noqa: BLE001 - surfaced to the controller
                reconcile_error = str(exc)

        config = self._store.snapshot()
        response = self._connection_repr(side, nmos_id, config, staged=False)
        response["activation"] = {
            "mode": _IMMEDIATE,
            "requested_time": None,
            "activation_time": activation_time,
        }
        if reconcile_error is not None:
            return _error(500, f"activation staged but daemon reconcile failed: {reconcile_error}")
        return _json(response)

    def _apply_to_config(self, side: StreamSide, nmos_id: str, staged: dict[str, Any]) -> None:
        params = staged.get("transport_params") or [{}]
        leg = params[0] if params and isinstance(params[0], dict) else {}
        master_enable = staged.get("master_enable")

        if side == "sender":
            changes: dict[str, object] = {}
            if master_enable is not None:
                changes["enabled"] = bool(master_enable)
            destination_ip = leg.get("destination_ip")
            if isinstance(destination_ip, str) and destination_ip not in {"", "auto"}:
                changes["address"] = destination_ip
            port = leg.get("destination_port")
            if isinstance(port, int):
                changes["rtp_port"] = port
            if changes:
                self._store.update_sender(nmos_id, **changes)
            return

        changes = {}
        if master_enable is not None:
            changes["enabled"] = bool(master_enable)
        transport_file = staged.get("transport_file")
        if isinstance(transport_file, dict):
            data = transport_file.get("data")
            if isinstance(data, str) and data.strip():
                changes["sdp"] = data
        if changes:
            self._store.update_receiver(nmos_id, **changes)

    # -- small typed lookups ---------------------------------------------

    def _require_sender(self, config: BridgeConfig, nmos_id: str) -> SenderConfig:
        for sender in config.senders:
            if sender.nmos_id == nmos_id:
                return sender
        raise _ApiError(404, f"sender {nmos_id} not found")

    def _require_receiver(self, config: BridgeConfig, nmos_id: str) -> ReceiverConfig:
        for receiver in config.receivers:
            if receiver.nmos_id == nmos_id:
                return receiver
        raise _ApiError(404, f"receiver {nmos_id} not found")


class _ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _merge_staged(base: dict[str, Any], patch: dict[str, Any], side: StreamSide) -> dict[str, Any]:
    merged = json.loads(json.dumps(base))
    sub_key = "receiver_id" if side == "sender" else "sender_id"
    if sub_key in patch:
        merged[sub_key] = patch[sub_key]
    if "master_enable" in patch:
        merged["master_enable"] = bool(patch["master_enable"])
    if side == "receiver" and "transport_file" in patch:
        merged["transport_file"] = patch["transport_file"]
    if "transport_params" in patch and isinstance(patch["transport_params"], list):
        legs = merged.get("transport_params") or []
        for index, leg_patch in enumerate(patch["transport_params"]):
            if not isinstance(leg_patch, dict):
                continue
            if index < len(legs):
                legs[index].update(leg_patch)
            else:
                legs.append(dict(leg_patch))
        merged["transport_params"] = legs
    return merged


def _parse_json_object(body: bytes | None) -> dict[str, Any]:
    if not body:
        raise _ApiError(400, "request body required")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _ApiError(400, f"invalid JSON body: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _ApiError(400, "request body must be a JSON object")
    return parsed


def _tai_now(clock: Callable[[], float]) -> str:
    # Approximate TAI as UTC + 37s leap-second offset (sufficient for staging).
    now = clock() + 37.0
    seconds = int(now)
    nanos = int((now - seconds) * 1_000_000_000)
    return f"{seconds}:{nanos}"


def _base_href(config: BridgeConfig) -> str:
    host = config.advertised_host
    if not host:
        host = config.http_host
    if host in {"", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{config.http_port}"


def _json(body: Any) -> HttpResponse:
    return HttpResponse(status=200, body=body)


def _error(status: int, message: str) -> HttpResponse:
    return HttpResponse(status=status, body={"code": status, "error": message, "debug": None})
