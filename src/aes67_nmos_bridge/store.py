"""Desired-state store: the bridge's source of truth for NMOS-managed streams.

The store holds the current :class:`BridgeConfig`, which is the authoritative
desired state that the reconciler pushes onto the AES67 daemon. Mutations made
through the NMOS IS-05 Connection API (for example, activating a receiver with a
new transport file) go through this store so that:

* the change becomes part of the desired state (source-of-truth behaviour), and
* it is optionally persisted to disk so it survives a restart.

The store is thread-safe. It is intentionally small today (file-backed JSON),
but its API is the seam where future config-reload / external-API backends plug
in without the reconciler or NMOS layer needing to change.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from .config import config_to_dict
from .models import BridgeConfig, ReceiverConfig, SenderConfig


class DesiredStateStore:
    def __init__(self, config: BridgeConfig, path: str | Path | None = None) -> None:
        self._config = config
        self._path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._version_ns = time.time_ns()

    def snapshot(self) -> BridgeConfig:
        with self._lock:
            return self._config

    def version_string(self) -> str:
        """Return the current desired-state version as an NMOS ``secs:nanos`` tag.

        The value only changes when the desired state changes, which keeps NMOS
        resource ``version`` fields stable between unrelated reads.
        """

        with self._lock:
            return f"{self._version_ns // 1_000_000_000}:{self._version_ns % 1_000_000_000}"

    def find_sender(self, nmos_id: str) -> SenderConfig | None:
        with self._lock:
            return next((s for s in self._config.senders if s.nmos_id == nmos_id), None)

    def find_receiver(self, nmos_id: str) -> ReceiverConfig | None:
        with self._lock:
            return next((r for r in self._config.receivers if r.nmos_id == nmos_id), None)

    def replace_config(self, config: BridgeConfig) -> BridgeConfig:
        with self._lock:
            self._config = config
            self._bump_version()
            self._persist()
            return self._config

    def update_sender(self, nmos_id: str, **changes: object) -> SenderConfig:
        with self._lock:
            senders = list(self._config.senders)
            for index, sender in enumerate(senders):
                if sender.nmos_id == nmos_id:
                    updated = replace(sender, **changes)
                    senders[index] = updated
                    self._config = replace(self._config, senders=tuple(senders))
                    self._bump_version()
                    self._persist()
                    return updated
            raise KeyError(f"unknown sender nmos_id: {nmos_id}")

    def update_receiver(self, nmos_id: str, **changes: object) -> ReceiverConfig:
        with self._lock:
            receivers = list(self._config.receivers)
            for index, receiver in enumerate(receivers):
                if receiver.nmos_id == nmos_id:
                    updated = replace(receiver, **changes)
                    receivers[index] = updated
                    self._config = replace(self._config, receivers=tuple(receivers))
                    self._bump_version()
                    self._persist()
                    return updated
            raise KeyError(f"unknown receiver nmos_id: {nmos_id}")

    def _bump_version(self) -> None:
        # Ensure the version is strictly monotonic even for sub-tick updates.
        self._version_ns = max(time.time_ns(), self._version_ns + 1)

    def _persist(self) -> None:
        if self._path is None:
            return
        payload = json.dumps(config_to_dict(self._config), indent=2, sort_keys=True)
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise
