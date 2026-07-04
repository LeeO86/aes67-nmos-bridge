#include <catch2/catch_test_macros.hpp>

#include <limits>

#include <nmos/json_fields.h>
#include <nmos/settings.h>

#include "bridge/config.h"
#include "nmos/nmos_resources.h"

using namespace bridge;

TEST_CASE("static NMOS registration config maps to nmos-cpp settings") {
    BridgeConfig config;
    config.nmos_registration.mode = "static";
    config.nmos_registration.address = "172.24.94.8:80";
    config.nmos_registration.version = "v1.2";
    web::json::value settings = web::json::value::object();

    apply_registration_settings(settings, config);

    CHECK(nmos::fields::registry_address(settings) == U("172.24.94.8"));
    CHECK(nmos::fields::registration_port(settings) == 80);
    CHECK(nmos::fields::registry_version(settings) == U("v1.2"));
    CHECK(nmos::fields::highest_pri(settings) == (std::numeric_limits<int>::max)());
}

TEST_CASE("dns-sd NMOS registration config maps to unicast DNS-SD settings") {
    BridgeConfig config;
    config.nmos_registration.mode = "dns-sd";
    config.nmos_registration.domain = "media.int";
    web::json::value settings = web::json::value::object();

    apply_registration_settings(settings, config);

    CHECK(nmos::fields::dns_sd_browse_mode(settings) == 1);
    CHECK(nmos::fields::domain(settings) == U("media.int"));
}

TEST_CASE("mdns NMOS registration config maps to Bonjour settings") {
    BridgeConfig config;
    config.nmos_registration.mode = "mdns";
    web::json::value settings = web::json::value::object();

    apply_registration_settings(settings, config);

    CHECK(nmos::fields::dns_sd_browse_mode(settings) == 2);
    CHECK(nmos::fields::domain(settings) == U("local."));
}

TEST_CASE("registryless NMOS registration config disables discovery") {
    BridgeConfig config;
    config.nmos_registration.mode = "registryless";
    web::json::value settings = web::json::value::object();

    apply_registration_settings(settings, config);

    CHECK(nmos::fields::highest_pri(settings) == (std::numeric_limits<int>::max)());
}
