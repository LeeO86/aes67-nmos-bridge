#pragma once

#include <cpprest/json.h>

#include <string>
#include <vector>

#include "bridge/daemon_client.h"
#include "bridge/ownership.h"

namespace bridge {

// A stream the bridge wants to exist (or not) on the daemon, derived from NMOS
// connection state. When enabled is false, the bridge ensures it does NOT own a
// daemon stream in that slot.
struct DesiredStream {
    Side side;
    int daemon_id = 0;
    std::string nmos_id;
    bool enabled = false;
    std::string name;          // managed ownership-marker name
    web::json::value payload;  // full daemon payload (used when enabled)
};

struct PlannedOp {
    enum class Action { create, update, remove };
    Action action;
    Side side;
    int daemon_id;
    std::string reason;
    web::json::value payload;  // present for create/update
};

std::string to_string(PlannedOp::Action action);

struct ReconcileReport {
    std::vector<PlannedOp> ops;
    std::vector<std::string> errors;  // fail-closed conflicts (unmanaged slots, etc.)
    bool changed() const { return !ops.empty(); }
};

class Reconciler {
public:
    Reconciler(std::string ns, IDaemon& daemon);

    // Pure planning: compute operations needed to make the daemon match desired,
    // protecting unmanaged streams (fail closed).
    ReconcileReport plan(const std::vector<DesiredStream>& desired, const DaemonState& state) const;

    void apply(const PlannedOp& op);

    // get_state + plan + apply.
    ReconcileReport reconcile(const std::vector<DesiredStream>& desired);

private:
    std::string ns_;
    IDaemon& daemon_;
};

}  // namespace bridge
