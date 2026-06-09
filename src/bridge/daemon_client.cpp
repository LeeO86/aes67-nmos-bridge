#include "bridge/daemon_client.h"

#include <cpprest/http_client.h>

#include <chrono>

namespace bridge {

namespace {

DaemonStream parse_stream(Side side, const web::json::value& item) {
    DaemonStream stream;
    stream.side = side;
    stream.id = item.at(utility::conversions::to_string_t("id")).as_integer();
    const auto name_key = utility::conversions::to_string_t("name");
    if (item.has_field(name_key) && item.at(name_key).is_string()) {
        stream.name = utility::conversions::to_utf8string(item.at(name_key).as_string());
    }
    stream.payload = item;
    return stream;
}

void parse_array(DaemonState& state, const web::json::value& payload, const char* key, Side side) {
    const auto field = utility::conversions::to_string_t(key);
    if (!payload.has_field(field) || !payload.at(field).is_array()) {
        return;
    }
    for (const auto& item : payload.at(field).as_array()) {
        auto stream = parse_stream(side, item);
        if (side == Side::sender) {
            state.sources[stream.id] = stream;
        } else {
            state.sinks[stream.id] = stream;
        }
    }
}

}  // namespace

DaemonState parse_streams(const web::json::value& payload) {
    DaemonState state;
    parse_array(state, payload, "sources", Side::sender);
    parse_array(state, payload, "sinks", Side::receiver);
    return state;
}

DaemonClient::DaemonClient(std::string base_url, int timeout_seconds)
    : base_url_(std::move(base_url)), timeout_seconds_(timeout_seconds) {}

namespace {

web::http::client::http_client make_client(const std::string& base_url, int timeout_seconds) {
    web::http::client::http_client_config cfg;
    cfg.set_timeout(std::chrono::seconds(timeout_seconds));
    return web::http::client::http_client(utility::conversions::to_string_t(base_url), cfg);
}

void check_status(const web::http::http_response& response, const std::string& what) {
    if (response.status_code() >= 400) {
        throw DaemonError(what + " failed with HTTP " + std::to_string(response.status_code()));
    }
}

}  // namespace

DaemonState DaemonClient::get_state() {
    try {
        auto client = make_client(base_url_, timeout_seconds_);
        auto response =
            client.request(web::http::methods::GET, utility::conversions::to_string_t("/api/streams"))
                .get();
        check_status(response, "GET /api/streams");
        const auto body = response.extract_json().get();
        return parse_streams(body);
    } catch (const web::http::http_exception& exc) {
        throw DaemonError(std::string("GET /api/streams transport error: ") + exc.what());
    }
}

void DaemonClient::put_source(int id, const web::json::value& payload) {
    auto client = make_client(base_url_, timeout_seconds_);
    const auto path = utility::conversions::to_string_t("/api/source/" + std::to_string(id));
    auto response = client.request(web::http::methods::PUT, path, payload).get();
    check_status(response, "PUT /api/source/" + std::to_string(id));
}

void DaemonClient::delete_source(int id) {
    auto client = make_client(base_url_, timeout_seconds_);
    const auto path = utility::conversions::to_string_t("/api/source/" + std::to_string(id));
    auto response = client.request(web::http::methods::DEL, path).get();
    check_status(response, "DELETE /api/source/" + std::to_string(id));
}

void DaemonClient::put_sink(int id, const web::json::value& payload) {
    auto client = make_client(base_url_, timeout_seconds_);
    const auto path = utility::conversions::to_string_t("/api/sink/" + std::to_string(id));
    auto response = client.request(web::http::methods::PUT, path, payload).get();
    check_status(response, "PUT /api/sink/" + std::to_string(id));
}

void DaemonClient::delete_sink(int id) {
    auto client = make_client(base_url_, timeout_seconds_);
    const auto path = utility::conversions::to_string_t("/api/sink/" + std::to_string(id));
    auto response = client.request(web::http::methods::DEL, path).get();
    check_status(response, "DELETE /api/sink/" + std::to_string(id));
}

}  // namespace bridge
