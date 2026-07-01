#include "nmos/nmos_resources.h"

#include <nmos/capabilities.h>
#include <nmos/channels.h>
#include <nmos/clock_name.h>
#include <nmos/connection_resources.h>
#include <nmos/format.h>
#include <nmos/json_fields.h>
#include <nmos/media_type.h>
#include <nmos/model.h>
#include <nmos/node_interfaces.h>
#include <nmos/node_resource.h>
#include <nmos/node_resources.h>
#include <nmos/random.h>
#include <nmos/resource.h>
#include <nmos/slog.h>
#include <nmos/transport.h>

#include <cpprest/host_utils.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

#include "bridge/ownership.h"

namespace bridge {

namespace {

// Fixed root namespace UUID for deriving a stable per-namespace seed.
const utility::string_t kSeedRoot = U("6d1f9b6a-4b2e-5c8a-9f3d-0a1b2c3d4e5f");

std::string u8(const utility::string_t& s) { return utility::conversions::to_utf8string(s); }

std::uint32_t parse_ipv4_address(const std::string& address) {
    std::array<int, 4> octets{};
    char dot1 = 0;
    char dot2 = 0;
    char dot3 = 0;
    std::istringstream input(address);
    if (!(input >> octets[0] >> dot1 >> octets[1] >> dot2 >> octets[2] >> dot3 >>
          octets[3]) ||
        dot1 != '.' || dot2 != '.' || dot3 != '.' || !input.eof()) {
        throw std::runtime_error("invalid IPv4 address: " + address);
    }
    std::uint32_t result = 0;
    for (const auto octet : octets) {
        if (octet < 0 || octet > 255) {
            throw std::runtime_error("invalid IPv4 address: " + address);
        }
        result = (result << 8) | static_cast<std::uint32_t>(octet);
    }
    return result;
}

struct Ipv4Cidr {
    std::uint32_t network = 0;
    int prefix = 0;
};

Ipv4Cidr parse_ipv4_cidr(const std::string& cidr) {
    const auto slash = cidr.find('/');
    if (slash == std::string::npos) {
        throw std::runtime_error("invalid CIDR (missing prefix): " + cidr);
    }
    const auto address = cidr.substr(0, slash);
    const auto prefix_text = cidr.substr(slash + 1);
    int prefix = -1;
    try {
        std::size_t consumed = 0;
        prefix = std::stoi(prefix_text, &consumed);
        if (consumed != prefix_text.size()) {
            throw std::invalid_argument("trailing characters");
        }
    } catch (const std::exception&) {
        throw std::runtime_error("invalid CIDR prefix: " + cidr);
    }
    if (prefix < 0 || prefix > 32) {
        throw std::runtime_error("CIDR prefix must be in range 0..32: " + cidr);
    }
    const auto mask = prefix == 0 ? 0u : (0xffffffffu << (32 - prefix));
    return {parse_ipv4_address(address) & mask, prefix};
}

bool matches_cidr(const std::string& address, const Ipv4Cidr& cidr) {
    const auto mask = cidr.prefix == 0 ? 0u : (0xffffffffu << (32 - cidr.prefix));
    try {
        return (parse_ipv4_address(address) & mask) == cidr.network;
    } catch (const std::runtime_error&) {
        return false;
    }
}

struct HostPort {
    std::string host;
    int port = 0;
};

HostPort parse_host_port(const std::string& value) {
    const auto colon = value.rfind(':');
    if (colon == std::string::npos) {
        return {value, 0};
    }
    const auto host = value.substr(0, colon);
    const auto port_text = value.substr(colon + 1);
    if (host.empty() || port_text.empty()) {
        throw std::runtime_error("invalid static NMOS registration address: " + value);
    }
    try {
        std::size_t consumed = 0;
        const auto port = std::stoi(port_text, &consumed);
        if (consumed != port_text.size() || port <= 0 || port > 65535) {
            throw std::invalid_argument("invalid port");
        }
        return {host, port};
    } catch (const std::exception&) {
        throw std::runtime_error("invalid static NMOS registration address: " + value);
    }
}

void disable_registry_discovery(web::json::value& settings) {
    settings[nmos::fields::highest_pri] =
        web::json::value::number((std::numeric_limits<int>::max)());
}

nmos::channel_symbol channel_symbol_for(std::size_t index) {
    if (index == 0) return nmos::channel_symbols::L;
    if (index == 1) return nmos::channel_symbols::R;
    // NMOS audio channel symbols for undefined channels: "U01".."U64".
    utility::string_t symbol = U("U");
    if (index + 1 < 10) symbol += U("0");
    symbol += utility::conversions::to_string_t(std::to_string(index + 1));
    return nmos::channel_symbol(symbol);
}

std::vector<nmos::channel> make_channels(std::size_t count) {
    std::vector<nmos::channel> channels;
    for (std::size_t i = 0; i < count; ++i) {
        const auto label = U("Channel ") + utility::conversions::to_string_t(std::to_string(i + 1));
        channels.push_back({label, channel_symbol_for(i)});
    }
    return channels;
}

std::vector<web::hosts::experimental::host_interface> all_host_interfaces() {
    return web::hosts::experimental::host_interfaces();
}

web::hosts::experimental::host_interface find_interface_by_name(const std::string& name) {
    for (const auto& iface : all_host_interfaces()) {
        if (u8(iface.name) == name) return iface;
    }
    throw std::runtime_error("daemon_interface_name does not match a local interface: " + name);
}

bool contains_interface(const std::vector<web::hosts::experimental::host_interface>& interfaces,
                        const utility::string_t& name) {
    return std::any_of(interfaces.begin(), interfaces.end(), [&](const auto& iface) {
        return iface.name == name;
    });
}

web::hosts::experimental::host_interface rtp_host_interface(const nmos::settings& settings,
                                                            const BridgeConfig& config) {
    if (!config.daemon_interface_name.empty()) {
        return find_interface_by_name(config.daemon_interface_name);
    }
    const auto host_interfaces = nmos::get_host_interfaces(settings);
    if (!host_interfaces.empty()) {
        return host_interfaces.front();
    }
    return {};
}

std::vector<web::hosts::experimental::host_interface> node_host_interfaces(
    const nmos::settings& settings, const BridgeConfig& config) {
    auto interfaces = nmos::get_host_interfaces(settings);
    if (!config.daemon_interface_name.empty()) {
        const auto rtp_interface = find_interface_by_name(config.daemon_interface_name);
        if (!contains_interface(interfaces, rtp_interface.name)) {
            interfaces.push_back(rtp_interface);
        }
    }
    return interfaces;
}

std::vector<utility::string_t> interface_names(const nmos::settings& settings,
                                               const BridgeConfig& config) {
    const auto iface = rtp_host_interface(settings, config);
    std::vector<utility::string_t> names;
    if (!iface.name.empty()) {
        names.push_back(iface.name);
    }
    return names;
}

std::vector<utility::string_t> interface_addresses(const nmos::settings& settings,
                                                   const BridgeConfig& config) {
    return rtp_host_interface(settings, config).addresses;
}

}  // namespace

unsigned int codec_bit_depth(const std::string& codec) {
    std::string digits;
    for (char ch : codec) {
        if (std::isdigit(static_cast<unsigned char>(ch))) digits.push_back(ch);
    }
    return digits.empty() ? 24u : static_cast<unsigned int>(std::stoi(digits));
}

void ensure_seed_id(web::json::value& settings, const std::string& ns) {
    const auto key = nmos::experimental::fields::seed_id;
    if (!settings.has_field(key) || settings.at(key).is_null() ||
        settings.at(key).as_string().empty()) {
        const auto seed = nmos::make_repeatable_id(kSeedRoot,
                                                   U("aes67-nmos-bridge/") +
                                                       utility::conversions::to_string_t(ns));
        settings[key] = web::json::value::string(seed);
    }
}

void apply_api_address_filters(web::json::value& settings, const BridgeConfig& config) {
    if (config.nmos_api_address_cidrs.empty()) return;

    std::vector<Ipv4Cidr> cidrs;
    for (const auto& cidr : config.nmos_api_address_cidrs) {
        cidrs.push_back(parse_ipv4_cidr(cidr));
    }

    auto addresses = web::json::value::array();
    std::size_t index = 0;
    for (const auto& iface : all_host_interfaces()) {
        for (const auto& address : iface.addresses) {
            const auto address_u8 = u8(address);
            const auto matched = std::any_of(cidrs.begin(), cidrs.end(), [&](const auto& cidr) {
                return matches_cidr(address_u8, cidr);
            });
            if (matched) {
                addresses[index++] = web::json::value::string(address);
            }
        }
    }

    if (index == 0) {
        throw std::runtime_error("nmos_api_address_cidrs did not match any local IPv4 address");
    }

    settings[nmos::fields::host_addresses] = addresses;
    settings[nmos::fields::host_address] = addresses.at(0);
}

void apply_registration_settings(web::json::value& settings, const BridgeConfig& config) {
    const auto& registration = config.nmos_registration;
    if (registration.mode.empty()) return;

    if (registration.mode == "static") {
        if (registration.address.empty()) {
            throw std::runtime_error("nmos_registration static mode requires address");
        }
        const auto endpoint = parse_host_port(registration.address);
        settings[nmos::fields::registry_address] = web::json::value::string(
            utility::conversions::to_string_t(endpoint.host));
        const auto port = registration.port > 0 ? registration.port : endpoint.port;
        if (port > 0) {
            settings[nmos::fields::registration_port] = web::json::value::number(port);
        }
        if (!registration.version.empty()) {
            settings[nmos::fields::registry_version] =
                web::json::value::string(utility::conversions::to_string_t(registration.version));
        }
        disable_registry_discovery(settings);
        return;
    }

    if (registration.mode == "dns-sd") {
        settings[nmos::fields::dns_sd_browse_mode] = web::json::value::number(1);
        if (!registration.domain.empty()) {
            settings[nmos::fields::domain] =
                web::json::value::string(utility::conversions::to_string_t(registration.domain));
        }
        return;
    }

    if (registration.mode == "mdns" || registration.mode == "bonjour") {
        settings[nmos::fields::dns_sd_browse_mode] = web::json::value::number(2);
        settings[nmos::fields::domain] = web::json::value::string(U("local."));
        return;
    }

    if (registration.mode == "registryless" || registration.mode == "peer-to-peer") {
        disable_registry_discovery(settings);
        return;
    }

    throw std::runtime_error("unsupported nmos_registration mode: " + registration.mode);
}

nmos::id sender_resource_id(const nmos::id& seed_id, const std::string& nmos_id) {
    return nmos::make_repeatable_id(
        seed_id, U("/sender/") + utility::conversions::to_string_t(nmos_id));
}

nmos::id receiver_resource_id(const nmos::id& seed_id, const std::string& nmos_id) {
    return nmos::make_repeatable_id(
        seed_id, U("/receiver/") + utility::conversions::to_string_t(nmos_id));
}

ResourceRegistry build_registry(const web::json::value& settings, const BridgeConfig& config) {
    const auto seed_id = nmos::experimental::fields::seed_id(settings);
    ResourceRegistry registry;
    registry.node_id = nmos::make_repeatable_id(seed_id, U("/node"));
    registry.device_id = nmos::make_repeatable_id(seed_id, U("/device"));
    for (const auto& sender : config.senders) {
        registry.senders[sender_resource_id(seed_id, sender.nmos_id)] = sender;
    }
    for (const auto& receiver : config.receivers) {
        registry.receivers[receiver_resource_id(seed_id, receiver.nmos_id)] = receiver;
    }
    return registry;
}

void insert_node_resources(nmos::node_model& model, const BridgeConfig& config,
                           const ResourceRegistry& registry, slog::base_gate& gate) {
    using web::json::value;
    using web::json::value_of;

    const auto& settings = model.settings;
    const auto seed_id = nmos::experimental::fields::seed_id(settings);
    const auto names = interface_names(settings, config);
    const auto addresses = interface_addresses(settings, config);

    auto lock = model.write_lock();

    // PTP clock (the host is expected to be PTP-synced).
    const auto clocks = value_of({nmos::make_ptp_clock(nmos::clock_names::clk0, true,
                                                       U("00-00-00-00-00-00-00-00"), true)});
    const auto host_interfaces = node_host_interfaces(settings, config);
    const auto interfaces = nmos::experimental::node_interfaces(host_interfaces);

    auto node = nmos::make_node(registry.node_id, clocks, nmos::make_node_interfaces(interfaces),
                                settings);
    nmos::insert_resource(model.node_resources, std::move(node));

    std::vector<nmos::id> sender_ids;
    std::vector<nmos::id> receiver_ids;
    for (const auto& kv : registry.senders) sender_ids.push_back(kv.first);
    for (const auto& kv : registry.receivers) receiver_ids.push_back(kv.first);

    auto device =
        nmos::make_device(registry.device_id, registry.node_id, sender_ids, receiver_ids, settings);
    nmos::insert_resource(model.node_resources, std::move(device));

    for (const auto& kv : registry.senders) {
        const auto& sender_id = kv.first;
        const auto& cfg = kv.second;
        const auto source_id =
            nmos::make_repeatable_id(seed_id, U("/source/") +
                                                  utility::conversions::to_string_t(cfg.nmos_id));
        const auto flow_id = nmos::make_repeatable_id(
            seed_id, U("/flow/") + utility::conversions::to_string_t(cfg.nmos_id));

        auto source = nmos::make_audio_source(source_id, registry.device_id, nmos::clock_names::clk0,
                                              {}, make_channels(cfg.map.size()), settings);
        source.data[nmos::fields::label] = value::string(utility::conversions::to_string_t(cfg.label));
        source.data[nmos::fields::description] =
            value::string(utility::conversions::to_string_t(cfg.description));

        auto flow = nmos::make_raw_audio_flow(flow_id, source_id, registry.device_id,
                                              nmos::rational(cfg.sample_rate, 1),
                                              codec_bit_depth(cfg.codec), settings);
        flow.data[nmos::fields::label] = value::string(utility::conversions::to_string_t(cfg.label));

        nmos::insert_resource(model.node_resources, std::move(source));
        nmos::insert_resource(model.node_resources, std::move(flow));

        const auto manifest_href = nmos::experimental::make_manifest_api_manifest(sender_id, settings);
        auto sender = nmos::make_sender(sender_id, flow_id, nmos::transports::rtp,
                                        registry.device_id, manifest_href.to_string(), names,
                                        settings);
        sender.data[nmos::fields::label] = value::string(utility::conversions::to_string_t(cfg.label));

        auto connection_sender = nmos::make_connection_rtp_sender(sender_id, false);
        if (!addresses.empty()) {
            connection_sender.data[nmos::fields::endpoint_constraints][0][nmos::fields::source_ip] =
                value_of({{nmos::fields::constraint_enum,
                           value_of({value::string(addresses.front())})}});
        }
        if (!cfg.address.empty()) {
            connection_sender.data[nmos::fields::endpoint_constraints][0][nmos::fields::destination_ip] =
                value_of({{nmos::fields::constraint_enum,
                           value_of({value::string(utility::conversions::to_string_t(cfg.address))})}});
        }
        connection_sender.data[nmos::fields::endpoint_constraints][0][nmos::fields::source_port] =
            value_of({{nmos::fields::constraint_enum, value_of({value::number(cfg.rtp_port)})}});
        connection_sender.data[nmos::fields::endpoint_constraints][0][nmos::fields::destination_port] =
            value_of({{nmos::fields::constraint_enum, value_of({value::number(cfg.rtp_port)})}});
        auto& active_sender_tp =
            nmos::fields::transport_params(nmos::fields::endpoint_active(connection_sender.data))[0];
        if (!addresses.empty()) {
            active_sender_tp[nmos::fields::source_ip] = value::string(addresses.front());
        }
        if (!cfg.address.empty()) {
            active_sender_tp[nmos::fields::destination_ip] =
                value::string(utility::conversions::to_string_t(cfg.address));
        }
        active_sender_tp[nmos::fields::source_port] = value::number(cfg.rtp_port);
        active_sender_tp[nmos::fields::destination_port] = value::number(cfg.rtp_port);
        nmos::insert_resource(model.node_resources, std::move(sender));
        nmos::insert_resource(model.connection_resources, std::move(connection_sender));
    }

    for (const auto& kv : registry.receivers) {
        const auto& receiver_id = kv.first;
        const auto& cfg = kv.second;

        auto receiver = nmos::make_receiver(
            receiver_id, registry.device_id, nmos::transports::rtp, names, nmos::formats::audio,
            {nmos::media_types::audio_L(24), nmos::media_types::audio_L(16)}, settings);
        receiver.data[nmos::fields::label] =
            value::string(utility::conversions::to_string_t(cfg.label));
        receiver.data[nmos::fields::description] =
            value::string(utility::conversions::to_string_t(cfg.description));

        auto connection_receiver = nmos::make_connection_rtp_receiver(receiver_id, false);
        if (!addresses.empty()) {
            connection_receiver
                .data[nmos::fields::endpoint_constraints][0][nmos::fields::interface_ip] =
                value_of({{nmos::fields::constraint_enum,
                           value_of({value::string(addresses.front())})}});
        }
        nmos::insert_resource(model.node_resources, std::move(receiver));
        nmos::insert_resource(model.connection_resources, std::move(connection_receiver));
    }

    slog::log<slog::severities::info>(gate, SLOG_FLF)
        << "Inserted " << registry.senders.size() << " sender(s) and " << registry.receivers.size()
        << " receiver(s) into the NMOS node model";

    model.notify();
}

}  // namespace bridge
