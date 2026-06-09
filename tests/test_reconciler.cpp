#include <catch2/catch_test_macros.hpp>

#include <tuple>
#include <vector>

#include "bridge/ownership.h"
#include "bridge/reconciler.h"

using namespace bridge;

namespace {

web::json::value named(const std::string& name) {
    web::json::value v;
    v[U("name")] = web::json::value::string(utility::conversions::to_string_t(name));
    return v;
}

web::json::value sender_payload(const std::string& name, const std::string& codec) {
    auto v = named(name);
    v[U("codec")] = web::json::value::string(utility::conversions::to_string_t(codec));
    return v;
}

DaemonStream stream(Side side, int id, const std::string& name) {
    DaemonStream s;
    s.side = side;
    s.id = id;
    s.name = name;
    s.payload = named(name);
    return s;
}

DesiredStream desired_sender(int daemon_id, const std::string& nmos_id, bool enabled,
                             const web::json::value& payload = {}) {
    DesiredStream d;
    d.side = Side::sender;
    d.daemon_id = daemon_id;
    d.nmos_id = nmos_id;
    d.enabled = enabled;
    d.name = managed_name("default", Side::sender, nmos_id, "Label");
    d.payload = payload;
    return d;
}

DesiredStream desired_receiver(int daemon_id, const std::string& nmos_id, bool enabled,
                               const web::json::value& payload = {}) {
    DesiredStream d;
    d.side = Side::receiver;
    d.daemon_id = daemon_id;
    d.nmos_id = nmos_id;
    d.enabled = enabled;
    d.name = managed_name("default", Side::receiver, nmos_id, "Label");
    d.payload = payload;
    return d;
}

class FakeDaemon : public IDaemon {
public:
    DaemonState state;
    std::vector<std::tuple<std::string, int>> calls;

    DaemonState get_state() override { return state; }
    void put_source(int id, const web::json::value&) override { calls.emplace_back("put_source", id); }
    void delete_source(int id) override { calls.emplace_back("delete_source", id); }
    void put_sink(int id, const web::json::value&) override { calls.emplace_back("put_sink", id); }
    void delete_sink(int id) override { calls.emplace_back("delete_sink", id); }
};

}  // namespace

TEST_CASE("creates missing configured streams") {
    FakeDaemon daemon;
    Reconciler reconciler("default", daemon);

    std::vector<DesiredStream> desired = {
        desired_sender(1, "main", true, sender_payload(managed_name("default", Side::sender, "main", "Label"), "L24")),
        desired_receiver(2, "ret", true, named(managed_name("default", Side::receiver, "ret", "Label"))),
    };

    const auto report = reconciler.reconcile(desired);
    REQUIRE(report.ops.size() == 2);
    CHECK(report.errors.empty());
    CHECK(daemon.calls == std::vector<std::tuple<std::string, int>>{{"put_source", 1}, {"put_sink", 2}});
}

TEST_CASE("updates a drifted managed sender") {
    FakeDaemon daemon;
    daemon.state.sources[1] =
        stream(Side::sender, 1, managed_name("default", Side::sender, "main", "Label"));
    daemon.state.sources[1].payload =
        sender_payload(managed_name("default", Side::sender, "main", "Label"), "L16");
    Reconciler reconciler("default", daemon);

    std::vector<DesiredStream> desired = {desired_sender(
        1, "main", true, sender_payload(managed_name("default", Side::sender, "main", "Label"), "L24"))};

    const auto report = reconciler.reconcile(desired);
    REQUIRE(report.ops.size() == 1);
    CHECK(report.ops[0].action == PlannedOp::Action::update);
    CHECK(daemon.calls == std::vector<std::tuple<std::string, int>>{{"put_source", 1}});
}

TEST_CASE("no drift means no operations") {
    FakeDaemon daemon;
    const auto payload = sender_payload(managed_name("default", Side::sender, "main", "Label"), "L24");
    daemon.state.sources[1] =
        stream(Side::sender, 1, managed_name("default", Side::sender, "main", "Label"));
    daemon.state.sources[1].payload = payload;
    Reconciler reconciler("default", daemon);

    const auto report = reconciler.reconcile({desired_sender(1, "main", true, payload)});
    CHECK(report.ops.empty());
    CHECK(daemon.calls.empty());
}

TEST_CASE("deletes orphaned managed stream but keeps unmanaged") {
    FakeDaemon daemon;
    daemon.state.sources[7] =
        stream(Side::sender, 7, managed_name("default", Side::sender, "old", "Old"));
    daemon.state.sources[8] = stream(Side::sender, 8, "Manual source");
    Reconciler reconciler("default", daemon);

    const auto report = reconciler.reconcile({});
    REQUIRE(report.ops.size() == 1);
    CHECK(report.ops[0].action == PlannedOp::Action::remove);
    CHECK(report.ops[0].daemon_id == 7);
    CHECK(daemon.calls == std::vector<std::tuple<std::string, int>>{{"delete_source", 7}});
}

TEST_CASE("refuses to overwrite unmanaged stream in a configured slot") {
    FakeDaemon daemon;
    daemon.state.sources[1] = stream(Side::sender, 1, "Manual source");
    Reconciler reconciler("default", daemon);

    const auto report = reconciler.reconcile({desired_sender(1, "main", true, named("x"))});
    CHECK(report.ops.empty());
    REQUIRE(report.errors.size() == 1);
    CHECK(daemon.calls.empty());
}

TEST_CASE("refuses to overwrite a stream owned by another namespace") {
    FakeDaemon daemon;
    daemon.state.sources[1] =
        stream(Side::sender, 1, managed_name("truck-b", Side::sender, "main", "Label"));
    Reconciler reconciler("default", daemon);

    const auto report = reconciler.reconcile({desired_sender(1, "main", true, named("x"))});
    CHECK(report.ops.empty());
    REQUIRE(report.errors.size() == 1);
}

TEST_CASE("disabled receiver deletes the owned sink") {
    FakeDaemon daemon;
    daemon.state.sinks[2] =
        stream(Side::receiver, 2, managed_name("default", Side::receiver, "ret", "Label"));
    Reconciler reconciler("default", daemon);

    const auto report = reconciler.reconcile({desired_receiver(2, "ret", false)});
    REQUIRE(report.ops.size() == 1);
    CHECK(report.ops[0].action == PlannedOp::Action::remove);
    CHECK(daemon.calls == std::vector<std::tuple<std::string, int>>{{"delete_sink", 2}});
}

TEST_CASE("disabled receiver leaves an unmanaged sink untouched") {
    FakeDaemon daemon;
    daemon.state.sinks[2] = stream(Side::receiver, 2, "Manual sink");
    Reconciler reconciler("default", daemon);

    const auto report = reconciler.reconcile({desired_receiver(2, "ret", false)});
    CHECK(report.ops.empty());
    CHECK(report.errors.empty());
    CHECK(daemon.calls.empty());
}
