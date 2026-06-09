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

#include <cctype>
#include <string>

#include "bridge/ownership.h"

namespace bridge {

namespace {

// Fixed root namespace UUID for deriving a stable per-namespace seed.
const utility::string_t kSeedRoot = U("6d1f9b6a-4b2e-5c8a-9f3d-0a1b2c3d4e5f");

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

std::vector<utility::string_t> interface_names(const nmos::settings& settings) {
    const auto host_interfaces = nmos::get_host_interfaces(settings);
    std::vector<utility::string_t> names;
    if (!host_interfaces.empty()) {
        names.push_back(host_interfaces.front().name);
    }
    return names;
}

std::vector<utility::string_t> interface_addresses(const nmos::settings& settings) {
    const auto host_interfaces = nmos::get_host_interfaces(settings);
    if (!host_interfaces.empty()) {
        return host_interfaces.front().addresses;
    }
    return {};
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
    const auto names = interface_names(settings);
    const auto addresses = interface_addresses(settings);

    auto lock = model.write_lock();

    // PTP clock (the host is expected to be PTP-synced).
    const auto clocks = value_of({nmos::make_ptp_clock(nmos::clock_names::clk0, true,
                                                       U("00-00-00-00-00-00-00-00"), true)});
    const auto host_interfaces = nmos::get_host_interfaces(settings);
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
