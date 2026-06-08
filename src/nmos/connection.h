#pragma once

#include <nmos/connection_activation.h>

#include <atomic>
#include <condition_variable>
#include <mutex>
#include <string>

#include "bridge/config.h"
#include "bridge/daemon_client.h"
#include "bridge/reconciler.h"
#include "nmos/nmos_resources.h"

namespace nmos {
struct node_model;
}
namespace slog {
class base_gate;
}

namespace bridge {

// IS-05 "auto" resolution for our RTP senders/receivers.
nmos::connection_resource_auto_resolver make_auto_resolver(const ResourceRegistry& registry);

// Generate the sender /transportfile (SDP) from the IS-04 resources + active params.
nmos::connection_sender_transportfile_setter make_transportfile_setter(const nmos::node_model& model);

// Build the daemon payloads from active connection state (exposed for clarity).
web::json::value make_source_payload(const SenderConfig& cfg, const std::string& ns,
                                     const std::string& address);
web::json::value make_sink_payload(const ReceiverConfig& cfg, const std::string& ns,
                                   const std::string& sdp);

// Translates NMOS IS-05 active state onto the AES67 daemon, with ownership-safe
// reconciliation. Immediate/scheduled/bulk activations all funnel through here
// because nmos-cpp invokes the activation handler once a resource is activated;
// the handler simply triggers a reconcile pass that reads the resulting active
// state and pushes it to the daemon.
class DaemonReconciler {
public:
    DaemonReconciler(nmos::node_model& model, ResourceRegistry registry, BridgeConfig config,
                     IDaemon& daemon, slog::base_gate& gate);

    nmos::connection_activation_handler activation_handler();

    // Long-running thread function: reconciles periodically and whenever an
    // activation occurs, until the model is shut down.
    void run();

private:
    std::vector<DesiredStream> build_desired_locked() const;

    nmos::node_model& model_;
    ResourceRegistry registry_;
    BridgeConfig config_;
    Reconciler reconciler_;
    slog::base_gate& gate_;
    std::atomic<bool> dirty_{true};
    std::mutex wait_mutex_;
    std::condition_variable wait_cv_;
};

}  // namespace bridge
