#include "nmos/connection.h"

#include <nmos/connection_api.h>
#include <nmos/connection_resources.h>
#include <nmos/json_fields.h>
#include <nmos/model.h>
#include <nmos/resource.h>
#include <nmos/sdp_utils.h>
#include <nmos/slog.h>
#include <nmos/transport.h>
#include <sdp/sdp.h>

#include <boost/range/algorithm/find.hpp>
#include <chrono>
#include <map>

#include "bridge/ownership.h"

namespace bridge {

namespace {

using web::json::value;
using web::json::value_of;

utility::string_t us(const std::string& s) { return utility::conversions::to_string_t(s); }
std::string u8(const utility::string_t& s) { return utility::conversions::to_utf8string(s); }

value int_array(const std::vector<int>& values) {
    auto array = value::array();
    for (std::size_t i = 0; i < values.size(); ++i) array[i] = value::number(values[i]);
    return array;
}

std::string active_destination_ip(const value& transport_params, const std::string& fallback) {
    if (transport_params.is_array() && transport_params.size() > 0) {
        const auto& leg = transport_params.at(0);
        const auto field = nmos::fields::destination_ip(leg);
        if (field.is_string()) {
            const auto ip = u8(field.as_string());
            if (!ip.empty() && ip != "auto") return ip;
        }
    }
    return fallback;
}

bool active_rtp_enabled(const value& transport_params) {
    if (transport_params.is_array() && transport_params.size() > 0) {
        return nmos::fields::rtp_enabled(transport_params.at(0));
    }
    return false;
}

}  // namespace

value make_source_payload(const SenderConfig& cfg, const std::string& ns,
                          const std::string& address) {
    value payload;
    payload[U("enabled")] = value::boolean(true);
    payload[U("name")] = value::string(us(managed_name(ns, Side::sender, cfg.nmos_id, cfg.label)));
    payload[U("io")] = value::string(us(cfg.io));
    payload[U("codec")] = value::string(us(cfg.codec));
    payload[U("address")] = value::string(us(address));
    payload[U("max_samples_per_packet")] = value::number(cfg.max_samples_per_packet);
    payload[U("ttl")] = value::number(cfg.ttl);
    payload[U("payload_type")] = value::number(cfg.payload_type);
    payload[U("dscp")] = value::number(cfg.dscp);
    payload[U("refclk_ptp_traceable")] = value::boolean(cfg.refclk_ptp_traceable);
    payload[U("map")] = int_array(cfg.map);
    return payload;
}

value make_sink_payload(const ReceiverConfig& cfg, const std::string& ns, const std::string& sdp) {
    value payload;
    payload[U("name")] = value::string(us(managed_name(ns, Side::receiver, cfg.nmos_id, cfg.label)));
    payload[U("io")] = value::string(us(cfg.io));
    payload[U("delay")] = value::number(cfg.delay);
    payload[U("use_sdp")] = value::boolean(true);
    payload[U("source")] = value::string(us(cfg.source));
    payload[U("sdp")] = value::string(us(sdp));
    payload[U("ignore_refclk_gmid")] = value::boolean(cfg.ignore_refclk_gmid);
    payload[U("map")] = int_array(cfg.map);
    return payload;
}

nmos::connection_resource_auto_resolver make_auto_resolver(const ResourceRegistry& registry) {
    struct SenderDestination {
        std::string address;
        int port = 0;
    };
    std::map<nmos::id, SenderDestination> sender_destinations;
    std::vector<nmos::id> receiver_ids;
    for (const auto& kv : registry.senders) {
        sender_destinations[kv.first] = {kv.second.address, kv.second.rtp_port};
    }
    for (const auto& kv : registry.receivers) receiver_ids.push_back(kv.first);

    return [sender_destinations, receiver_ids](const nmos::resource& /*resource*/,
                                               const nmos::resource& connection_resource,
                                               value& transport_params) {
        const auto& id = connection_resource.id;
        const auto& type = connection_resource.type;
        const auto& constraints = nmos::fields::endpoint_constraints(connection_resource.data);

        const auto sender = sender_destinations.find(id);
        if (sender != sender_destinations.end()) {
            nmos::details::resolve_auto(transport_params[0], nmos::fields::source_ip, [&] {
                return web::json::front(
                    nmos::fields::constraint_enum(constraints.at(0).at(nmos::fields::source_ip)));
            });
            // The daemon transmits to the configured multicast group; resolve the
            // sender destination so the generated SDP carries real RTP details.
            if (!sender->second.address.empty()) {
                nmos::details::resolve_auto(transport_params[0], nmos::fields::destination_ip,
                                            [&] { return value::string(us(sender->second.address)); });
            }
            nmos::details::resolve_auto(transport_params[0], nmos::fields::source_port,
                                        [&] { return value::number(sender->second.port); });
            nmos::details::resolve_auto(transport_params[0], nmos::fields::destination_port,
                                        [&] { return value::number(sender->second.port); });
        } else if (boost::range::find(receiver_ids, id) != receiver_ids.end()) {
            nmos::details::resolve_auto(transport_params[0], nmos::fields::interface_ip, [&] {
                return web::json::front(
                    nmos::fields::constraint_enum(constraints.at(0).at(nmos::fields::interface_ip)));
            });
        }
        nmos::resolve_rtp_auto(type, transport_params);
    };
}

nmos::connection_sender_transportfile_setter make_transportfile_setter(
    const nmos::node_model& model) {
    const auto& node_resources = model.node_resources;
    return [&node_resources](const nmos::resource& sender, const nmos::resource& connection_sender,
                             value& endpoint_transportfile) {
        const nmos::id flow_id = nmos::fields::flow_id(sender.data).as_string();
        const std::pair<nmos::id, nmos::type> flow_key{flow_id, nmos::types::flow};
        auto flow = nmos::find_resource(node_resources, flow_key);
        if (node_resources.end() == flow) return;
        const auto src_id = nmos::fields::source_id(flow->data);
        const std::pair<nmos::id, nmos::type> source_key{src_id, nmos::types::source};
        auto source = nmos::find_resource(node_resources, source_key);

        auto node = node_resources.end();
        for (auto it = node_resources.begin(); it != node_resources.end(); ++it) {
            if (it->type == nmos::types::node) {
                node = it;
                break;
            }
        }
        if (node_resources.end() == source || node_resources.end() == node) return;

        const double packet_time = 1;
        auto sdp_params = nmos::make_audio_sdp_parameters(node->data, source->data, flow->data,
                                                          sender.data,
                                                          nmos::details::payload_type_audio_default,
                                                          {U("PRIMARY")}, {}, packet_time);
        auto& transport_params =
            nmos::fields::transport_params(nmos::fields::endpoint_active(connection_sender.data));
        auto session_description = nmos::make_session_description(sdp_params, transport_params);
        auto sdp = utility::s2us(sdp::make_session_description(session_description));
        endpoint_transportfile = nmos::make_connection_rtp_sender_transportfile(sdp);
    };
}

DaemonReconciler::DaemonReconciler(nmos::node_model& model, ResourceRegistry registry,
                                   BridgeConfig config, IDaemon& daemon, slog::base_gate& gate)
    : model_(model),
      registry_(std::move(registry)),
      config_(std::move(config)),
      reconciler_(config_.ns, daemon),
      gate_(gate) {}

nmos::connection_activation_handler DaemonReconciler::activation_handler() {
    return [this](const nmos::resource& /*resource*/, const nmos::resource& /*connection_resource*/) {
        {
            std::lock_guard<std::mutex> guard(wait_mutex_);
            dirty_.store(true);
        }
        wait_cv_.notify_all();
    };
}

std::vector<DesiredStream> DaemonReconciler::build_desired_locked() const {
    std::vector<DesiredStream> desired;

    for (const auto& kv : registry_.senders) {
        const auto& id = kv.first;
        const auto& cfg = kv.second;
        const std::pair<nmos::id, nmos::type> key{id, nmos::types::sender};
        auto conn = nmos::find_resource(model_.connection_resources, key);
        DesiredStream stream;
        stream.side = Side::sender;
        stream.daemon_id = cfg.daemon_id;
        stream.nmos_id = cfg.nmos_id;
        stream.name = managed_name(config_.ns, Side::sender, cfg.nmos_id, cfg.label);
        if (model_.connection_resources.end() != conn) {
            const auto& active = nmos::fields::endpoint_active(conn->data);
            const bool master_enable = nmos::fields::master_enable(active);
            const auto& tp = nmos::fields::transport_params(active);
            const auto address = active_destination_ip(tp, cfg.address);
            stream.enabled = master_enable && active_rtp_enabled(tp) && !address.empty();
            if (stream.enabled) {
                stream.payload = make_source_payload(cfg, config_.ns, address);
            }
        }
        desired.push_back(std::move(stream));
    }

    for (const auto& kv : registry_.receivers) {
        const auto& id = kv.first;
        const auto& cfg = kv.second;
        const std::pair<nmos::id, nmos::type> key{id, nmos::types::receiver};
        auto conn = nmos::find_resource(model_.connection_resources, key);
        DesiredStream stream;
        stream.side = Side::receiver;
        stream.daemon_id = cfg.daemon_id;
        stream.nmos_id = cfg.nmos_id;
        stream.name = managed_name(config_.ns, Side::receiver, cfg.nmos_id, cfg.label);
        if (model_.connection_resources.end() != conn) {
            const auto& active = nmos::fields::endpoint_active(conn->data);
            const bool master_enable = nmos::fields::master_enable(active);
            const auto transport_file = nmos::fields::transport_file(active);
            std::string sdp;
            if (transport_file.is_object() && transport_file.has_field(nmos::fields::data) &&
                transport_file.at(nmos::fields::data).is_string()) {
                sdp = u8(transport_file.at(nmos::fields::data).as_string());
            }
            stream.enabled = master_enable && !sdp.empty();
            if (stream.enabled) {
                stream.payload = make_sink_payload(cfg, config_.ns, sdp);
            }
        }
        desired.push_back(std::move(stream));
    }

    return desired;
}

void DaemonReconciler::run() {
    const auto interval = std::chrono::milliseconds(
        static_cast<long long>(config_.reconcile_interval_seconds * 1000));

    for (;;) {
        std::vector<DesiredStream> desired;
        bool shutdown = false;
        {
            auto lock = model_.read_lock();
            shutdown = model_.shutdown;
            if (!shutdown) {
                desired = build_desired_locked();
            }
        }
        if (shutdown) break;

        dirty_.store(false);
        try {
            auto report = reconciler_.reconcile(desired);
            for (const auto& op : report.ops) {
                slog::log<slog::severities::info>(gate_, SLOG_FLF)
                    << "daemon " << to_string(op.action) << " " << to_string(op.side) << " "
                    << op.daemon_id << " (" << op.reason << ")";
            }
            for (const auto& err : report.errors) {
                slog::log<slog::severities::warning>(gate_, SLOG_FLF) << err;
            }
        } catch (const std::exception& exc) {
            slog::log<slog::severities::warning>(gate_, SLOG_FLF)
                << "daemon reconcile failed: " << exc.what();
        }

        // Wake early on activation; otherwise reconcile periodically to self-heal.
        std::unique_lock<std::mutex> guard(wait_mutex_);
        wait_cv_.wait_for(guard, interval, [this] { return dirty_.load(); });
    }
}

}  // namespace bridge
