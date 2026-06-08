from __future__ import annotations

import json

from aes67_nmos_bridge.api import NmosApi
from aes67_nmos_bridge.models import BridgeConfig, ReceiverConfig, SenderConfig
from aes67_nmos_bridge.nmos import receiver_id, sender_id
from aes67_nmos_bridge.store import DesiredStateStore

SDP = (
    "v=0\n"
    "o=- 1 0 IN IP4 192.0.2.10\n"
    "s=Remote\n"
    "c=IN IP4 239.2.0.10/15\n"
    "t=0 0\n"
    "m=audio 5004 RTP/AVP 98\n"
    "a=rtpmap:98 L24/48000/2\n"
)

NEW_SDP = (
    "v=0\n"
    "o=- 2 0 IN IP4 192.0.2.20\n"
    "s=New\n"
    "c=IN IP4 239.9.9.9/15\n"
    "t=0 0\n"
    "m=audio 5006 RTP/AVP 98\n"
    "a=rtpmap:98 L24/48000/2\n"
)


def _api() -> tuple[NmosApi, DesiredStateStore, list[str]]:
    config = BridgeConfig(
        senders=(SenderConfig("main", 0, "Main", (0, 1), address="239.2.0.1"),),
        receivers=(ReceiverConfig("ret", 1, "Return", (0, 1), sdp=SDP),),
    )
    store = DesiredStateStore(config)
    calls: list[str] = []
    api = NmosApi(store, reconcile_cb=lambda: calls.append("reconcile"), clock=lambda: 1000.0)
    return api, store, calls


def _body(response) -> object:
    return json.loads(response.encoded().decode("utf-8"))


def _sid() -> str:
    return sender_id("default", "main")


def _rid() -> str:
    return receiver_id("default", "ret")


# -- IS-04 read -----------------------------------------------------------


def test_node_api_index_endpoints() -> None:
    api, _, _ = _api()
    assert _body(api.dispatch("GET", "/")) == ["x-nmos/"]
    assert _body(api.dispatch("GET", "/x-nmos/")) == ["node/", "connection/"]
    assert "self/" in _body(api.dispatch("GET", "/x-nmos/node/v1.3/"))


def test_node_self_and_collections() -> None:
    api, _, _ = _api()
    node = _body(api.dispatch("GET", "/x-nmos/node/v1.3/self/"))
    assert node["id"] != _sid()
    assert node["api"]["versions"] == ["v1.3"]
    senders = _body(api.dispatch("GET", "/x-nmos/node/v1.3/senders/"))
    assert senders[0]["id"] == _sid()
    one = _body(api.dispatch("GET", f"/x-nmos/node/v1.3/senders/{_sid()}/"))
    assert one["id"] == _sid()


def test_node_unknown_resource_is_404() -> None:
    api, _, _ = _api()
    assert api.dispatch("GET", "/x-nmos/node/v1.3/senders/does-not-exist/").status == 404
    assert api.dispatch("GET", "/x-nmos/node/v9.9/").status == 404


# -- IS-05 read -----------------------------------------------------------


def test_connection_index_and_transporttype() -> None:
    api, _, _ = _api()
    assert _body(api.dispatch("GET", "/x-nmos/connection/v1.1/")) == ["bulk/", "single/"]
    assert _body(api.dispatch("GET", "/x-nmos/connection/v1.1/single/")) == [
        "senders/",
        "receivers/",
    ]
    tt = api.dispatch("GET", f"/x-nmos/connection/v1.1/single/senders/{_sid()}/transporttype/")
    assert _body(tt) == "urn:x-nmos:transport:rtp.mcast"


def test_sender_active_and_transportfile() -> None:
    api, _, _ = _api()
    active = _body(api.dispatch("GET", f"/x-nmos/connection/v1.1/single/senders/{_sid()}/active/"))
    assert active["transport_params"][0]["destination_ip"] == "239.2.0.1"
    assert active["master_enable"] is True

    tf = api.dispatch("GET", f"/x-nmos/connection/v1.1/single/senders/{_sid()}/transportfile/")
    assert tf.status == 200
    assert tf.content_type == "application/sdp"
    assert b"a=sendonly" in tf.encoded()


def test_receiver_has_no_transportfile_endpoint() -> None:
    api, _, _ = _api()
    listing = _body(api.dispatch("GET", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/"))
    assert "transportfile/" not in listing
    assert (
        api.dispatch(
            "GET", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/transportfile/"
        ).status
        == 404
    )


def test_receiver_active_includes_transport_file() -> None:
    api, _, _ = _api()
    active = _body(
        api.dispatch("GET", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/active/")
    )
    assert active["transport_file"]["data"] == SDP
    assert active["transport_params"][0]["multicast_ip"] == "239.2.0.10"


# -- IS-05 PATCH / activation --------------------------------------------


def test_receiver_immediate_activation_updates_desired_and_reconciles() -> None:
    api, store, calls = _api()
    patch = json.dumps(
        {
            "sender_id": "11111111-1111-1111-1111-111111111111",
            "master_enable": True,
            "transport_file": {"data": NEW_SDP, "type": "application/sdp"},
            "activation": {"mode": "activate_immediate", "requested_time": None},
        }
    ).encode("utf-8")

    response = api.dispatch(
        "PATCH", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/staged/", patch
    )
    assert response.status == 200
    body = _body(response)
    assert body["activation"]["mode"] == "activate_immediate"
    assert body["activation"]["activation_time"] is not None

    # Source of truth updated and reconcile triggered.
    assert store.find_receiver("ret").sdp == NEW_SDP
    assert calls == ["reconcile"]

    # Staged returns to null activation; active reflects the activation.
    staged = _body(
        api.dispatch("GET", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/staged/")
    )
    assert staged["activation"]["mode"] is None
    assert staged["transport_params"][0]["multicast_ip"] == "239.9.9.9"
    active = _body(
        api.dispatch("GET", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/active/")
    )
    assert active["activation"]["mode"] == "activate_immediate"
    assert active["sender_id"] == "11111111-1111-1111-1111-111111111111"


def test_receiver_master_enable_false_disables_in_config() -> None:
    api, store, calls = _api()
    patch = json.dumps(
        {"master_enable": False, "activation": {"mode": "activate_immediate"}}
    ).encode("utf-8")
    response = api.dispatch(
        "PATCH", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/staged/", patch
    )
    assert response.status == 200
    assert store.find_receiver("ret").enabled is False
    assert calls == ["reconcile"]


def test_sender_immediate_activation_updates_address() -> None:
    api, store, calls = _api()
    patch = json.dumps(
        {
            "master_enable": True,
            "transport_params": [{"destination_ip": "239.7.7.7"}],
            "activation": {"mode": "activate_immediate"},
        }
    ).encode("utf-8")
    response = api.dispatch(
        "PATCH", f"/x-nmos/connection/v1.1/single/senders/{_sid()}/staged/", patch
    )
    assert response.status == 200
    assert store.find_sender("main").address == "239.7.7.7"
    assert calls == ["reconcile"]


def test_stage_without_activation_does_not_touch_config() -> None:
    api, store, calls = _api()
    patch = json.dumps({"transport_params": [{"destination_ip": "239.8.8.8"}]}).encode("utf-8")
    response = api.dispatch(
        "PATCH", f"/x-nmos/connection/v1.1/single/senders/{_sid()}/staged/", patch
    )
    assert response.status == 200
    # Config untouched, no reconcile, but staged overlay reflects the change.
    assert store.find_sender("main").address == "239.2.0.1"
    assert calls == []
    staged = _body(api.dispatch("GET", f"/x-nmos/connection/v1.1/single/senders/{_sid()}/staged/"))
    assert staged["transport_params"][0]["destination_ip"] == "239.8.8.8"


def test_scheduled_activation_is_not_implemented() -> None:
    api, _, _ = _api()
    patch = json.dumps(
        {"activation": {"mode": "activate_scheduled_relative", "requested_time": "0:0"}}
    ).encode("utf-8")
    response = api.dispatch(
        "PATCH", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/staged/", patch
    )
    assert response.status == 501


def test_patch_unknown_resource_is_404() -> None:
    api, _, _ = _api()
    patch = json.dumps({"activation": {"mode": "activate_immediate"}}).encode("utf-8")
    response = api.dispatch(
        "PATCH", "/x-nmos/connection/v1.1/single/receivers/nope/staged/", patch
    )
    assert response.status == 404


def test_patch_invalid_json_is_400() -> None:
    api, _, _ = _api()
    response = api.dispatch(
        "PATCH", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/staged/", b"not json"
    )
    assert response.status == 400


def test_reconcile_failure_surfaces_500_but_keeps_desired_state() -> None:
    config = BridgeConfig(
        receivers=(ReceiverConfig("ret", 1, "Return", (0, 1), sdp=SDP),),
    )
    store = DesiredStateStore(config)

    def boom() -> None:
        raise RuntimeError("daemon offline")

    api = NmosApi(store, reconcile_cb=boom)
    patch = json.dumps(
        {"transport_file": {"data": NEW_SDP, "type": "application/sdp"},
         "activation": {"mode": "activate_immediate"}}
    ).encode("utf-8")
    response = api.dispatch(
        "PATCH", f"/x-nmos/connection/v1.1/single/receivers/{_rid()}/staged/", patch
    )
    assert response.status == 500
    # Desired state still updated (daemon catches up on next loop).
    assert store.find_receiver("ret").sdp == NEW_SDP


def test_method_not_allowed() -> None:
    api, _, _ = _api()
    assert api.dispatch("DELETE", "/x-nmos/node/v1.3/self/").status == 405
