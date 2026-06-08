#include <catch2/catch_test_macros.hpp>

#include "bridge/config.h"

using namespace bridge;

namespace {
web::json::value parse(const char* json) {
    return web::json::value::parse(utility::conversions::to_string_t(json));
}
}  // namespace

TEST_CASE("parse_bridge_config applies defaults") {
    const auto settings = parse(R"({
        "senders": [{"nmos_id": "main", "daemon_id": 0, "label": "Main", "map": [0, 1]}],
        "receivers": [{"nmos_id": "ret", "daemon_id": 1, "label": "Return", "map": [2, 3]}]
    })");

    const auto config = parse_bridge_config(settings);
    CHECK(config.ns == "default");
    CHECK(config.daemon_base_url == "http://127.0.0.1:8080");
    REQUIRE(config.senders.size() == 1);
    CHECK(config.senders[0].codec == "L24");
    CHECK(config.senders[0].rtp_port == 5004);
    REQUIRE(config.receivers.size() == 1);
    CHECK(config.receivers[0].delay == 576);
}

TEST_CASE("parse_bridge_config rejects duplicate daemon ids") {
    const auto settings = parse(R"({
        "senders": [
            {"nmos_id": "a", "daemon_id": 0, "label": "A", "map": [0]},
            {"nmos_id": "b", "daemon_id": 0, "label": "B", "map": [1]}
        ]
    })");
    CHECK_THROWS_AS(parse_bridge_config(settings), std::runtime_error);
}

TEST_CASE("parse_bridge_config rejects empty channel map") {
    const auto settings = parse(R"({
        "senders": [{"nmos_id": "a", "daemon_id": 0, "label": "A", "map": []}]
    })");
    CHECK_THROWS_AS(parse_bridge_config(settings), std::runtime_error);
}

TEST_CASE("parse_bridge_config rejects out-of-range daemon id") {
    const auto settings = parse(R"({
        "receivers": [{"nmos_id": "a", "daemon_id": 64, "label": "A", "map": [0]}]
    })");
    CHECK_THROWS_AS(parse_bridge_config(settings), std::runtime_error);
}
