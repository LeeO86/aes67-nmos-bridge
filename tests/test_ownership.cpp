#include <catch2/catch_test_macros.hpp>

#include "bridge/ownership.h"

using namespace bridge;

TEST_CASE("managed_name round-trips through parse_managed_name") {
    const auto name = managed_name("truck-a", Side::receiver, "return", "Return Feed");
    REQUIRE(name == "NMOS[truck-a]/receiver/return Return Feed");

    const auto ownership = parse_managed_name(name);
    REQUIRE(ownership.has_value());
    CHECK(ownership->ns == "truck-a");
    CHECK(ownership->side == Side::receiver);
    CHECK(ownership->nmos_id == "return");
}

TEST_CASE("managed_name works without a label") {
    const auto name = managed_name("default", Side::sender, "main", "");
    CHECK(name == "NMOS[default]/sender/main");
    const auto ownership = parse_managed_name(name);
    REQUIRE(ownership.has_value());
    CHECK(ownership->nmos_id == "main");
}

TEST_CASE("parse_managed_name rejects malformed or unmanaged names") {
    CHECK_FALSE(parse_managed_name("Manual source").has_value());
    CHECK_FALSE(parse_managed_name("NMOS[]/sender/main Main").has_value());
    CHECK_FALSE(parse_managed_name("NMOS[default]/invalid/main Main").has_value());
    CHECK_FALSE(parse_managed_name("NMOS[default]/sender/ NoId").has_value());
    CHECK_FALSE(parse_managed_name("NMOS[default]sender/main").has_value());
}
