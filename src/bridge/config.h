#pragma once

#include <cpprest/json.h>

#include <string>
#include <vector>

namespace bridge {

struct SenderConfig {
    std::string nmos_id;
    int daemon_id = 0;
    std::string label;
    std::vector<int> map;
    std::string description;
    std::string io = "Audio Device";
    std::string codec = "L24";
    std::string address;  // multicast destination, may be empty for "auto"
    int rtp_port = 5004;
    int sample_rate = 48000;
    int max_samples_per_packet = 48;
    int ttl = 15;
    int payload_type = 98;
    int dscp = 34;
    bool refclk_ptp_traceable = true;
};

struct ReceiverConfig {
    std::string nmos_id;
    int daemon_id = 0;
    std::string label;
    std::vector<int> map;
    std::string description;
    std::string io = "Audio Device";
    int delay = 576;
    std::string source;
    int sample_rate = 48000;
    bool ignore_refclk_gmid = false;
};

struct NmosRegistrationConfig {
    std::string mode;
    std::string address;
    int port = 0;
    std::string version;
    std::string domain;
};

struct BridgeConfig {
    std::string daemon_base_url = "http://127.0.0.1:8080";
    std::string ns = "default";
    double reconcile_interval_seconds = 5.0;
    std::string daemon_interface_name;
    std::vector<std::string> nmos_api_address_cidrs;
    NmosRegistrationConfig nmos_registration;
    std::vector<SenderConfig> senders;
    std::vector<ReceiverConfig> receivers;
};

// Parse and validate the bridge-specific portion of the shared JSON settings.
// Throws std::runtime_error with a descriptive message on invalid configuration.
BridgeConfig parse_bridge_config(const web::json::value& settings);

}  // namespace bridge
