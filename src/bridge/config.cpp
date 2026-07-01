#include "bridge/config.h"

#include <set>
#include <stdexcept>

namespace bridge {

namespace {

using web::json::value;

std::string require_string(const value& obj, const std::string& key) {
    if (!obj.has_field(utility::conversions::to_string_t(key))) {
        throw std::runtime_error("missing required key: " + key);
    }
    return utility::conversions::to_utf8string(obj.at(utility::conversions::to_string_t(key)).as_string());
}

std::string opt_string(const value& obj, const std::string& key, const std::string& fallback) {
    const auto k = utility::conversions::to_string_t(key);
    if (!obj.has_field(k) || obj.at(k).is_null()) return fallback;
    return utility::conversions::to_utf8string(obj.at(k).as_string());
}

int opt_int(const value& obj, const std::string& key, int fallback) {
    const auto k = utility::conversions::to_string_t(key);
    if (!obj.has_field(k) || obj.at(k).is_null()) return fallback;
    return obj.at(k).as_integer();
}

bool opt_bool(const value& obj, const std::string& key, bool fallback) {
    const auto k = utility::conversions::to_string_t(key);
    if (!obj.has_field(k) || obj.at(k).is_null()) return fallback;
    return obj.at(k).as_bool();
}

int require_int(const value& obj, const std::string& key) {
    if (!obj.has_field(utility::conversions::to_string_t(key))) {
        throw std::runtime_error("missing required key: " + key);
    }
    return obj.at(utility::conversions::to_string_t(key)).as_integer();
}

std::vector<int> require_map(const value& obj) {
    const auto k = utility::conversions::to_string_t("map");
    if (!obj.has_field(k)) {
        throw std::runtime_error("missing required key: map");
    }
    std::vector<int> result;
    for (const auto& element : obj.at(k).as_array()) {
        result.push_back(element.as_integer());
    }
    return result;
}

SenderConfig parse_sender(const value& obj) {
    SenderConfig sender;
    sender.nmos_id = require_string(obj, "nmos_id");
    sender.daemon_id = require_int(obj, "daemon_id");
    sender.label = require_string(obj, "label");
    sender.map = require_map(obj);
    sender.description = opt_string(obj, "description", "");
    sender.io = opt_string(obj, "io", sender.io);
    sender.codec = opt_string(obj, "codec", sender.codec);
    sender.address = opt_string(obj, "address", "");
    sender.rtp_port = opt_int(obj, "rtp_port", sender.rtp_port);
    sender.sample_rate = opt_int(obj, "sample_rate", sender.sample_rate);
    sender.max_samples_per_packet = opt_int(obj, "max_samples_per_packet", sender.max_samples_per_packet);
    sender.ttl = opt_int(obj, "ttl", sender.ttl);
    sender.payload_type = opt_int(obj, "payload_type", sender.payload_type);
    sender.dscp = opt_int(obj, "dscp", sender.dscp);
    sender.refclk_ptp_traceable = opt_bool(obj, "refclk_ptp_traceable", sender.refclk_ptp_traceable);
    return sender;
}

ReceiverConfig parse_receiver(const value& obj) {
    ReceiverConfig receiver;
    receiver.nmos_id = require_string(obj, "nmos_id");
    receiver.daemon_id = require_int(obj, "daemon_id");
    receiver.label = require_string(obj, "label");
    receiver.map = require_map(obj);
    receiver.description = opt_string(obj, "description", "");
    receiver.io = opt_string(obj, "io", receiver.io);
    receiver.delay = opt_int(obj, "delay", receiver.delay);
    receiver.source = opt_string(obj, "source", "");
    receiver.sample_rate = opt_int(obj, "sample_rate", receiver.sample_rate);
    receiver.ignore_refclk_gmid = opt_bool(obj, "ignore_refclk_gmid", receiver.ignore_refclk_gmid);
    return receiver;
}

NmosRegistrationConfig parse_nmos_registration(const value& obj) {
    NmosRegistrationConfig registration;
    registration.mode = opt_string(obj, "mode", "");
    registration.address = opt_string(obj, "address", "");
    registration.port = opt_int(obj, "port", 0);
    registration.version = opt_string(obj, "version", "");
    registration.domain = opt_string(obj, "domain", "");
    return registration;
}

template <typename T>
void check_unique(const std::vector<T>& streams, const std::string& side) {
    std::set<int> daemon_ids;
    std::set<std::string> nmos_ids;
    for (const auto& stream : streams) {
        if (stream.daemon_id < 0 || stream.daemon_id > 63) {
            throw std::runtime_error(side + " " + stream.nmos_id + " daemon_id must be in range 0..63");
        }
        if (stream.map.empty()) {
            throw std::runtime_error(side + " " + stream.nmos_id + " map must not be empty");
        }
        if (!daemon_ids.insert(stream.daemon_id).second) {
            throw std::runtime_error("duplicate " + side + " daemon_id: " + std::to_string(stream.daemon_id));
        }
        if (!nmos_ids.insert(stream.nmos_id).second) {
            throw std::runtime_error("duplicate " + side + " nmos_id: " + stream.nmos_id);
        }
    }
}

}  // namespace

BridgeConfig parse_bridge_config(const value& settings) {
    BridgeConfig config;
    config.daemon_base_url = opt_string(settings, "daemon_base_url", config.daemon_base_url);
    while (!config.daemon_base_url.empty() && config.daemon_base_url.back() == '/') {
        config.daemon_base_url.pop_back();
    }
    config.ns = opt_string(settings, "namespace", config.ns);
    config.daemon_interface_name =
        opt_string(settings, "daemon_interface_name", config.daemon_interface_name);

    const auto interval_key = utility::conversions::to_string_t("reconcile_interval_seconds");
    if (settings.has_field(interval_key) && !settings.at(interval_key).is_null()) {
        config.reconcile_interval_seconds = settings.at(interval_key).as_double();
    }

    const auto cidrs_key = utility::conversions::to_string_t("nmos_api_address_cidrs");
    if (settings.has_field(cidrs_key) && !settings.at(cidrs_key).is_null()) {
        for (const auto& element : settings.at(cidrs_key).as_array()) {
            config.nmos_api_address_cidrs.push_back(
                utility::conversions::to_utf8string(element.as_string()));
        }
    }

    const auto registration_key = utility::conversions::to_string_t("nmos_registration");
    if (settings.has_field(registration_key) && !settings.at(registration_key).is_null()) {
        config.nmos_registration = parse_nmos_registration(settings.at(registration_key));
    }

    const auto senders_key = utility::conversions::to_string_t("senders");
    if (settings.has_field(senders_key)) {
        for (const auto& element : settings.at(senders_key).as_array()) {
            config.senders.push_back(parse_sender(element));
        }
    }
    const auto receivers_key = utility::conversions::to_string_t("receivers");
    if (settings.has_field(receivers_key)) {
        for (const auto& element : settings.at(receivers_key).as_array()) {
            config.receivers.push_back(parse_receiver(element));
        }
    }

    if (config.ns.empty()) {
        throw std::runtime_error("namespace must not be empty");
    }
    if (config.reconcile_interval_seconds <= 0.0) {
        throw std::runtime_error("reconcile_interval_seconds must be greater than zero");
    }
    check_unique(config.senders, "sender");
    check_unique(config.receivers, "receiver");

    return config;
}

}  // namespace bridge
