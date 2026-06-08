#pragma once

#include <cpprest/json.h>
#include <nmos/id.h>

#include <map>
#include <string>

#include "bridge/config.h"

namespace nmos {
struct node_model;
}
namespace slog {
class base_gate;
}

namespace bridge {

// Stable mapping between NMOS resource ids and the configured streams, so the
// connection glue can translate IS-05 activations back to daemon ids.
struct ResourceRegistry {
    nmos::id node_id;
    nmos::id device_id;
    std::map<nmos::id, SenderConfig> senders;      // sender_id -> config
    std::map<nmos::id, ReceiverConfig> receivers;  // receiver_id -> config
};

// Ensure settings carry a deterministic seed_id derived from the namespace so
// that all resource ids are stable across restarts and hosts.
void ensure_seed_id(web::json::value& settings, const std::string& ns);

// Compute the (deterministic) resource ids for the configuration. Pure; does not
// touch the model.
ResourceRegistry build_registry(const web::json::value& settings, const BridgeConfig& config);

// Insert the IS-04 resource graph and IS-05 connection resources into the model.
// Intended to run as an nmos-cpp node server thread function.
void insert_node_resources(nmos::node_model& model, const BridgeConfig& config,
                           const ResourceRegistry& registry, slog::base_gate& gate);

// Helpers exposed for the connection glue.
nmos::id sender_resource_id(const nmos::id& seed_id, const std::string& nmos_id);
nmos::id receiver_resource_id(const nmos::id& seed_id, const std::string& nmos_id);
unsigned int codec_bit_depth(const std::string& codec);

}  // namespace bridge
