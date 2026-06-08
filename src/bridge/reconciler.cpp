#include "bridge/reconciler.h"

#include <map>
#include <set>

namespace bridge {

namespace {

const std::map<int, DaemonStream>& existing_for(const DaemonState& state, Side side) {
    return side == Side::sender ? state.sources : state.sinks;
}

bool payload_drifted(const web::json::value& existing, const web::json::value& desired) {
    if (!desired.is_object()) return existing != desired;
    for (const auto& kv : desired.as_object()) {
        const auto key = kv.first;
        if (!existing.has_field(key)) return true;
        if (existing.at(key) != kv.second) return true;
    }
    return false;
}

}  // namespace

std::string to_string(PlannedOp::Action action) {
    switch (action) {
        case PlannedOp::Action::create:
            return "create";
        case PlannedOp::Action::update:
            return "update";
        case PlannedOp::Action::remove:
            return "remove";
    }
    return "unknown";
}

Reconciler::Reconciler(std::string ns, IDaemon& daemon) : ns_(std::move(ns)), daemon_(daemon) {}

ReconcileReport Reconciler::plan(const std::vector<DesiredStream>& desired,
                                 const DaemonState& state) const {
    ReconcileReport report;

    for (Side side : {Side::sender, Side::receiver}) {
        const auto& existing = existing_for(state, side);

        std::set<int> handled_ids;
        std::set<std::string> enabled_nmos_ids;
        std::set<int> enabled_daemon_ids;

        for (const auto& d : desired) {
            if (d.side != side) continue;
            if (d.enabled) {
                enabled_nmos_ids.insert(d.nmos_id);
                enabled_daemon_ids.insert(d.daemon_id);
            }
        }

        for (const auto& d : desired) {
            if (d.side != side) continue;
            const auto it = existing.find(d.daemon_id);
            const bool present = it != existing.end();

            if (!d.enabled) {
                // The bridge should not own a stream here. Remove if we own it.
                if (present) {
                    const auto ownership = parse_managed_name(it->second.name);
                    if (ownership && ownership->ns == ns_ && ownership->side == side &&
                        ownership->nmos_id == d.nmos_id) {
                        report.ops.push_back({PlannedOp::Action::remove, side, d.daemon_id,
                                              "stream disabled (master_enable=false)", {}});
                        handled_ids.insert(d.daemon_id);
                    }
                }
                continue;
            }

            if (!present) {
                report.ops.push_back(
                    {PlannedOp::Action::create, side, d.daemon_id, "configured stream missing", d.payload});
                handled_ids.insert(d.daemon_id);
                continue;
            }

            const auto ownership = parse_managed_name(it->second.name);
            if (!ownership) {
                report.errors.push_back(to_string(side) + " daemon_id " +
                                        std::to_string(d.daemon_id) +
                                        " is occupied by unmanaged stream '" + it->second.name +
                                        "'; refusing to overwrite");
                handled_ids.insert(d.daemon_id);
                continue;
            }
            if (ownership->ns != ns_) {
                report.errors.push_back(to_string(side) + " daemon_id " +
                                        std::to_string(d.daemon_id) + " is owned by namespace '" +
                                        ownership->ns + "', expected '" + ns_ + "'");
                handled_ids.insert(d.daemon_id);
                continue;
            }

            handled_ids.insert(d.daemon_id);
            if (payload_drifted(it->second.payload, d.payload)) {
                report.ops.push_back(
                    {PlannedOp::Action::update, side, d.daemon_id, "daemon stream drifted", d.payload});
            }
        }

        // Orphan cleanup: remove bridge-owned streams no longer wanted.
        for (const auto& kv : existing) {
            const int daemon_id = kv.first;
            if (handled_ids.count(daemon_id)) continue;
            const auto ownership = parse_managed_name(kv.second.name);
            if (!ownership || ownership->ns != ns_ || ownership->side != side) continue;
            const bool wanted = enabled_nmos_ids.count(ownership->nmos_id) &&
                                enabled_daemon_ids.count(daemon_id);
            if (!wanted) {
                report.ops.push_back({PlannedOp::Action::remove, side, daemon_id,
                                      "managed stream not in active bridge state", {}});
            }
        }
    }

    return report;
}

void Reconciler::apply(const PlannedOp& op) {
    if (op.side == Side::sender) {
        if (op.action == PlannedOp::Action::remove) {
            daemon_.delete_source(op.daemon_id);
        } else {
            daemon_.put_source(op.daemon_id, op.payload);
        }
    } else {
        if (op.action == PlannedOp::Action::remove) {
            daemon_.delete_sink(op.daemon_id);
        } else {
            daemon_.put_sink(op.daemon_id, op.payload);
        }
    }
}

ReconcileReport Reconciler::reconcile(const std::vector<DesiredStream>& desired) {
    const auto state = daemon_.get_state();
    auto report = plan(desired, state);
    for (const auto& op : report.ops) {
        apply(op);
    }
    return report;
}

}  // namespace bridge
