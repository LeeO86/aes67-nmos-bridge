#include <nmos/log_gate.h>
#include <nmos/model.h>
#include <nmos/node_server.h>
#include <nmos/process_utils.h>
#include <nmos/server.h>
#include <nmos/settings.h>
#include <nmos/slog.h>

#include <cpprest/json.h>

#include <fstream>
#include <iostream>

#include "bridge/config.h"
#include "bridge/daemon_client.h"
#include "nmos/connection.h"
#include "nmos/nmos_resources.h"

int main(int argc, char* argv[]) {
    nmos::node_model node_model;
    nmos::experimental::log_model log_model;

    std::filebuf error_log_buf;
    std::ostream error_log(std::cerr.rdbuf());
    std::filebuf access_log_buf;
    std::ostream access_log(&access_log_buf);
    nmos::experimental::log_gate gate(error_log, access_log, log_model);

    try {
        slog::log<slog::severities::info>(gate, SLOG_FLF) << "Starting aes67-nmos-bridge";

        if (argc > 1) {
            std::error_code error;
            node_model.settings = web::json::value::parse(utility::s2us(argv[1]), error);
            if (error) {
                std::ifstream file(argv[1]);
                file.exceptions(std::ios_base::failbit);
                node_model.settings = web::json::value::parse(file);
                node_model.settings.as_object();
            }
        }

        // Parse and validate the bridge-specific configuration first, so errors
        // are reported clearly before any NMOS machinery starts.
        const auto config = bridge::parse_bridge_config(node_model.settings);

        // Deterministic, stable resource ids derived from the namespace.
        bridge::ensure_seed_id(node_model.settings, config.ns);

        nmos::insert_node_default_settings(node_model.settings);
        log_model.settings = node_model.settings;
        log_model.level = nmos::fields::logging_level(log_model.settings);

        slog::log<slog::severities::info>(gate, SLOG_FLF)
            << "Process ID: " << nmos::details::get_process_id();
        slog::log<slog::severities::info>(gate, SLOG_FLF)
            << "Bridge namespace '" << config.ns << "', daemon " << config.daemon_base_url << ", "
            << config.senders.size() << " sender(s), " << config.receivers.size() << " receiver(s)";

        const auto registry = bridge::build_registry(node_model.settings, config);

        bridge::DaemonClient daemon(config.daemon_base_url);
        bridge::DaemonReconciler reconciler(node_model, registry, config, daemon, gate);

        // nmos-cpp provides IS-04 (node + registration + DNS-SD + heartbeats) and
        // IS-05 (immediate/scheduled/bulk). We only supply the device-specific
        // callbacks and the desired-state reconcile loop.
        auto node_server = nmos::experimental::make_node_server(
            node_model, bridge::make_auto_resolver(registry),
            bridge::make_transportfile_setter(node_model), reconciler.activation_handler(),
            log_model, gate);

        node_server.thread_functions.push_back(
            [&] { bridge::insert_node_resources(node_model, config, registry, gate); });
        node_server.thread_functions.push_back([&] { reconciler.run(); });

        slog::log<slog::severities::info>(gate, SLOG_FLF) << "Preparing for connections";
        nmos::server_guard node_server_guard(node_server);
        slog::log<slog::severities::info>(gate, SLOG_FLF) << "Ready for connections";

        nmos::details::wait_term_signal();

        slog::log<slog::severities::info>(gate, SLOG_FLF) << "Closing connections";
    } catch (const web::json::json_exception& e) {
        slog::log<slog::severities::error>(gate, SLOG_FLF) << "JSON error: " << e.what();
        return 1;
    } catch (const std::ios_base::failure& e) {
        slog::log<slog::severities::error>(gate, SLOG_FLF) << "File error: " << e.what();
        return 1;
    } catch (const std::runtime_error& e) {
        slog::log<slog::severities::error>(gate, SLOG_FLF) << "Configuration/implementation error: "
                                                           << e.what();
        return 1;
    } catch (const std::exception& e) {
        slog::log<slog::severities::error>(gate, SLOG_FLF) << "Unexpected exception: " << e.what();
        return 1;
    }

    slog::log<slog::severities::info>(gate, SLOG_FLF) << "Stopping aes67-nmos-bridge";
    return 0;
}
