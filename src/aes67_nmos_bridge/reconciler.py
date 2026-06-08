from __future__ import annotations

from typing import Protocol

from .models import (
    BridgeConfig,
    DaemonState,
    DaemonStream,
    PlannedOperation,
    ReceiverConfig,
    ReconcileReport,
    SenderConfig,
    StreamSide,
)
from .ownership import managed_name, parse_managed_name


class ReconcileError(RuntimeError):
    pass


class DaemonControl(Protocol):
    def get_state(self) -> DaemonState: ...

    def put_sender(self, daemon_id: int, payload: dict) -> None: ...

    def delete_sender(self, daemon_id: int) -> None: ...

    def put_receiver(self, daemon_id: int, payload: dict) -> None: ...

    def delete_receiver(self, daemon_id: int) -> None: ...


class Reconciler:
    def __init__(self, config: BridgeConfig, daemon: DaemonControl):
        self.config = config
        self.daemon = daemon

    def plan(self, state: DaemonState | None = None) -> tuple[PlannedOperation, ...]:
        state = state if state is not None else self.daemon.get_state()
        operations: list[PlannedOperation] = []
        operations.extend(
            self._plan_side(
                side="sender",
                desired={sender.daemon_id: sender for sender in self.config.senders},
                existing={stream.id: stream for stream in state.senders},
            )
        )
        operations.extend(
            self._plan_side(
                side="receiver",
                desired={receiver.daemon_id: receiver for receiver in self.config.receivers},
                existing={stream.id: stream for stream in state.receivers},
            )
        )
        return tuple(operations)

    def reconcile(self, dry_run: bool = False) -> ReconcileReport:
        operations = self.plan()
        if not dry_run:
            for operation in operations:
                self._apply(operation)
        return ReconcileReport(operations=operations, dry_run=dry_run)

    def _plan_side(
        self,
        side: StreamSide,
        desired: dict[int, SenderConfig] | dict[int, ReceiverConfig],
        existing: dict[int, DaemonStream],
    ) -> tuple[PlannedOperation, ...]:
        operations: list[PlannedOperation] = []
        desired_nmos_ids = {stream.nmos_id for stream in desired.values()}

        for daemon_id, desired_stream in desired.items():
            existing_stream = existing.get(daemon_id)
            desired_payload = self._payload_for(side, desired_stream)
            if existing_stream is None:
                operations.append(
                    PlannedOperation(
                        "create",
                        side,
                        daemon_id,
                        "configured stream missing",
                        desired_payload,
                    )
                )
                continue

            ownership = parse_managed_name(existing_stream.name)
            if ownership is None:
                raise ReconcileError(
                    f"{side} daemon_id {daemon_id} is occupied by unmanaged stream "
                    f"{existing_stream.name!r}"
                )
            if ownership.namespace != self.config.namespace:
                raise ReconcileError(
                    f"{side} daemon_id {daemon_id} is owned by NMOS namespace "
                    f"{ownership.namespace!r}, expected {self.config.namespace!r}"
                )

            if _payload_drifted(existing_stream.payload, desired_payload):
                operations.append(
                    PlannedOperation(
                        "update",
                        side,
                        daemon_id,
                        "daemon stream drifted",
                        desired_payload,
                    )
                )

        for daemon_id, existing_stream in existing.items():
            ownership = parse_managed_name(existing_stream.name)
            if (
                ownership is not None
                and ownership.namespace == self.config.namespace
                and ownership.side == side
                and (ownership.nmos_id not in desired_nmos_ids or daemon_id not in desired)
            ):
                operations.append(
                    PlannedOperation(
                        "delete",
                        side,
                        daemon_id,
                        "managed stream not in bridge config",
                    )
                )

        return tuple(operations)

    def _payload_for(
        self, side: StreamSide, stream: SenderConfig | ReceiverConfig
    ) -> dict[str, object]:
        if side == "sender":
            sender = stream
            assert isinstance(sender, SenderConfig)
            return {
                "enabled": sender.enabled,
                "name": managed_name(self.config.namespace, side, sender.nmos_id, sender.label),
                "io": sender.io,
                "codec": sender.codec,
                "address": sender.address,
                "max_samples_per_packet": sender.max_samples_per_packet,
                "ttl": sender.ttl,
                "payload_type": sender.payload_type,
                "dscp": sender.dscp,
                "refclk_ptp_traceable": sender.refclk_ptp_traceable,
                "map": list(sender.map),
            }

        receiver = stream
        assert isinstance(receiver, ReceiverConfig)
        return {
            "name": managed_name(self.config.namespace, side, receiver.nmos_id, receiver.label),
            "io": receiver.io,
            "delay": receiver.delay,
            "use_sdp": True,
            "source": receiver.source,
            "sdp": receiver.sdp,
            "ignore_refclk_gmid": receiver.ignore_refclk_gmid,
            "map": list(receiver.map),
        }

    def _apply(self, operation: PlannedOperation) -> None:
        if operation.side == "sender":
            if operation.action in {"create", "update"}:
                assert operation.payload is not None
                self.daemon.put_sender(operation.daemon_id, operation.payload)
            else:
                self.daemon.delete_sender(operation.daemon_id)
            return

        if operation.action in {"create", "update"}:
            assert operation.payload is not None
            self.daemon.put_receiver(operation.daemon_id, operation.payload)
        else:
            self.daemon.delete_receiver(operation.daemon_id)


def _payload_drifted(existing: dict, desired: dict[str, object]) -> bool:
    for key, desired_value in desired.items():
        existing_value = existing.get(key)
        if isinstance(desired_value, list):
            existing_value = list(existing_value or [])
        if existing_value != desired_value:
            return True
    return False
