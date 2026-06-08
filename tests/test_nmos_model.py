from __future__ import annotations

from aes67_nmos_bridge.models import BridgeConfig, ReceiverConfig, SenderConfig
from aes67_nmos_bridge.nmos import (
    FORMAT_AUDIO,
    TRANSPORT_RTP_MCAST,
    NmosModel,
    receiver_id,
    sender_id,
    stable_uuid,
)
from aes67_nmos_bridge.sdp import parse_sdp_connection, sender_sdp

RECEIVER_SDP = (
    "v=0\n"
    "o=- 1 0 IN IP4 192.0.2.10\n"
    "s=Remote AES67 Source\n"
    "c=IN IP4 239.2.0.10/15\n"
    "t=0 0\n"
    "m=audio 5004 RTP/AVP 98\n"
    "a=rtpmap:98 L24/48000/2\n"
)


def _config() -> BridgeConfig:
    return BridgeConfig(
        namespace="truck-a",
        senders=(SenderConfig("program-main", 0, "Program Main", (0, 1), address="239.2.0.1"),),
        receivers=(ReceiverConfig("return-feed", 1, "Return Feed", (0, 1), sdp=RECEIVER_SDP),),
    )


def _model(config: BridgeConfig | None = None) -> NmosModel:
    return NmosModel(config or _config(), base_href="http://10.0.0.1:8090", version="1:2")


def test_uuids_are_deterministic_and_namespaced() -> None:
    assert sender_id("truck-a", "program-main") == sender_id("truck-a", "program-main")
    assert sender_id("truck-a", "program-main") != sender_id("truck-b", "program-main")
    assert sender_id("truck-a", "program-main") != receiver_id("truck-a", "program-main")
    # Pure UUIDv5 derivation, no randomness.
    assert stable_uuid("ns", "x") == stable_uuid("ns", "x")


def test_node_and_device_resources() -> None:
    model = _model()
    node = model.node()
    device = model.device()

    assert node["id"] == model.node_id
    assert node["api"]["versions"] == ["v1.3"]
    assert node["api"]["endpoints"][0]["host"] == "10.0.0.1"
    assert node["api"]["endpoints"][0]["port"] == 8090

    assert device["node_id"] == model.node_id
    assert device["controls"][0]["type"] == "urn:x-nmos:control:sr-ctrl/v1.1"
    assert device["controls"][0]["href"].endswith("/x-nmos/connection/v1.1/")
    assert sender_id("truck-a", "program-main") in device["senders"]
    assert receiver_id("truck-a", "program-main") not in device["senders"]


def test_source_flow_sender_graph_is_linked() -> None:
    model = _model()
    source = model.sources()[0]
    flow = model.flows()[0]
    sender = model.senders()[0]

    assert source["format"] == FORMAT_AUDIO
    assert len(source["channels"]) == 2
    assert flow["source_id"] == source["id"]
    assert flow["media_type"] == "audio/L24"
    assert flow["bit_depth"] == 24
    assert flow["sample_rate"] == {"numerator": 48000, "denominator": 1}
    assert sender["flow_id"] == flow["id"]
    assert sender["transport"] == TRANSPORT_RTP_MCAST
    assert sender["manifest_href"].endswith(f"/senders/{sender['id']}/transportfile/")


def test_sender_transport_params_from_config() -> None:
    params = _model().sender_transport_params(_config().senders[0])
    assert params == [
        {
            "source_ip": "auto",
            "destination_ip": "239.2.0.1",
            "source_port": 5004,
            "destination_port": 5004,
            "rtp_enabled": True,
        }
    ]


def test_receiver_transport_params_parsed_from_sdp() -> None:
    params = _model().receiver_transport_params(_config().receivers[0])
    assert params[0]["multicast_ip"] == "239.2.0.10"
    assert params[0]["destination_port"] == 5004
    assert params[0]["interface_ip"] == "auto"
    assert params[0]["rtp_enabled"] is True


def test_disabled_receiver_reports_rtp_disabled() -> None:
    config = BridgeConfig(
        namespace="truck-a",
        receivers=(
            ReceiverConfig("return-feed", 1, "Return", (0, 1), sdp=RECEIVER_SDP, enabled=False),
        ),
    )
    params = _model(config).receiver_transport_params(config.receivers[0])
    assert params[0]["rtp_enabled"] is False


def test_generated_sender_sdp_is_parseable() -> None:
    sdp = sender_sdp(_config().senders[0], session_id="42")
    parsed = parse_sdp_connection(sdp)
    assert parsed.multicast_ip == "239.2.0.1"
    assert parsed.destination_port == 5004
    assert "a=sendonly" in sdp
    assert "L24/48000/2" in sdp
