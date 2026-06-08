from __future__ import annotations

from pathlib import Path

from aes67_nmos_bridge.config import load_config
from aes67_nmos_bridge.models import BridgeConfig, ReceiverConfig, SenderConfig
from aes67_nmos_bridge.store import DesiredStateStore

SDP = (
    "v=0\no=- 1 0 IN IP4 192.0.2.10\ns=x\n"
    "c=IN IP4 239.2.0.10/15\nt=0 0\nm=audio 5004 RTP/AVP 98\n"
)


def _config() -> BridgeConfig:
    return BridgeConfig(
        senders=(SenderConfig("main", 0, "Main", (0, 1), address="239.2.0.1"),),
        receivers=(ReceiverConfig("ret", 1, "Return", (0, 1), sdp=SDP),),
    )


def test_update_sender_replaces_only_target() -> None:
    store = DesiredStateStore(_config())
    updated = store.update_sender("main", enabled=False, address="239.9.9.9")

    assert updated.enabled is False
    assert updated.address == "239.9.9.9"
    assert store.find_sender("main").enabled is False
    # Other config untouched.
    assert store.snapshot().receivers[0].nmos_id == "ret"


def test_update_receiver_changes_sdp() -> None:
    store = DesiredStateStore(_config())
    store.update_receiver("ret", sdp="v=0\nupdated\n", enabled=False)
    assert store.find_receiver("ret").sdp == "v=0\nupdated\n"
    assert store.find_receiver("ret").enabled is False


def test_version_advances_on_mutation() -> None:
    store = DesiredStateStore(_config())
    first = store.version_string()
    store.update_sender("main", enabled=False)
    assert store.version_string() != first
    # Format is secs:nanos
    secs, nanos = store.version_string().split(":")
    assert secs.isdigit() and nanos.isdigit()


def test_persistence_round_trips_through_config_loader(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = DesiredStateStore(_config(), path=path)
    store.update_receiver("ret", sdp="v=0\npersisted\n", enabled=False)
    store.update_sender("main", address="239.5.5.5")

    reloaded = load_config(path)
    assert reloaded.receivers[0].sdp == "v=0\npersisted\n"
    assert reloaded.receivers[0].enabled is False
    assert reloaded.senders[0].address == "239.5.5.5"
