from __future__ import annotations

import pytest

from aes67_nmos_bridge.config import ConfigError, parse_config
from aes67_nmos_bridge.ownership import managed_name, parse_managed_name


def test_parses_valid_config_defaults() -> None:
    config = parse_config(
        {
            "senders": [{"nmos_id": "main", "daemon_id": 0, "label": "Main", "map": [0, 1]}],
            "receivers": [
                {
                    "nmos_id": "return",
                    "daemon_id": 1,
                    "label": "Return",
                    "map": [2, 3],
                    "sdp": "v=0\n",
                }
            ],
        }
    )

    assert config.daemon_base_url == "http://127.0.0.1:8080"
    assert config.senders[0].codec == "L24"
    assert config.receivers[0].delay == 576


def test_rejects_duplicate_ids_per_side() -> None:
    with pytest.raises(ConfigError, match="duplicate sender daemon_id"):
        parse_config(
            {
                "senders": [
                    {"nmos_id": "a", "daemon_id": 0, "label": "A", "map": [0]},
                    {"nmos_id": "b", "daemon_id": 0, "label": "B", "map": [1]},
                ]
            }
        )


def test_rejects_empty_channel_map() -> None:
    with pytest.raises(ConfigError, match="map must not be empty"):
        parse_config({"senders": [{"nmos_id": "a", "daemon_id": 0, "label": "A", "map": []}]})


def test_managed_name_round_trip() -> None:
    name = managed_name("truck-a", "receiver", "return", "Return Feed")

    ownership = parse_managed_name(name)

    assert ownership is not None
    assert ownership.namespace == "truck-a"
    assert ownership.side == "receiver"
    assert ownership.nmos_id == "return"


def test_ignores_malformed_or_unmanaged_names() -> None:
    assert parse_managed_name("Manual source") is None
    assert parse_managed_name("NMOS[]/sender/main Main") is None
    assert parse_managed_name("NMOS[default]/invalid/main Main") is None
