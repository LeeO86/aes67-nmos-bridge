#pragma once

#include <cpprest/json.h>

#include <map>
#include <stdexcept>
#include <string>

#include "bridge/ownership.h"

namespace bridge {

struct DaemonStream {
    Side side;
    int id = 0;
    std::string name;
    web::json::value payload;  // full daemon representation (for drift detection)
};

struct DaemonState {
    std::map<int, DaemonStream> sources;  // keyed by daemon id
    std::map<int, DaemonStream> sinks;    // keyed by daemon id
};

class DaemonError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Abstraction over the AES67 daemon REST API so the reconciler can be tested
// against a fake without touching a real daemon.
class IDaemon {
public:
    virtual ~IDaemon() = default;
    virtual DaemonState get_state() = 0;
    virtual void put_source(int id, const web::json::value& payload) = 0;
    virtual void delete_source(int id) = 0;
    virtual void put_sink(int id, const web::json::value& payload) = 0;
    virtual void delete_sink(int id) = 0;
};

class DaemonClient : public IDaemon {
public:
    explicit DaemonClient(std::string base_url, int timeout_seconds = 5);

    DaemonState get_state() override;
    void put_source(int id, const web::json::value& payload) override;
    void delete_source(int id) override;
    void put_sink(int id, const web::json::value& payload) override;
    void delete_sink(int id) override;

private:
    std::string base_url_;
    int timeout_seconds_;
};

// Parse a GET /api/streams payload ({"sources": [...], "sinks": [...]}).
DaemonState parse_streams(const web::json::value& payload);

}  // namespace bridge
