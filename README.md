# AES67 NMOS Bridge

A standalone NMOS (AMWA IS-04/IS-05) **Node** that makes NMOS the control surface
for [`bondagit/aes67-linux-daemon`](https://github.com/bondagit/aes67-linux-daemon).

It is built on **[`sony/nmos-cpp`](https://github.com/sony/nmos-cpp)**, the
reference NMOS implementation, so the bridge behaves on the wire like other
production NMOS devices (registration, discovery, connection management) rather
than a bespoke re-implementation. The bridge adds the device-specific logic:
mapping NMOS senders/receivers onto AES67 daemon streams over the daemon's REST
API, and reconciling them safely.

> This is a C++ project. It replaces an earlier Python prototype (see
> "Project history" below). There is **no hybrid**: the service is C++ only.

## Architecture

```
NMOS controller ──IS-04/IS-05──▶ aes67-nmos-bridge (nmos-cpp Node) ──REST──▶ aes67-linux-daemon
                                  │
                                  └─ desired-state reconcile (ownership-safe)
```

`nmos-cpp` provides, out of the box:

- **IS-04 Node API**, **Registration API client with heartbeats**, and
  **DNS-SD** (unicast + peer-to-peer) discovery/advertisement.
- **IS-05 Connection API** with **immediate**, **scheduled** (absolute/relative,
  using the PTP-synced system clock) and **bulk** activations.

The bridge supplies only the device-specific parts (`src/`):

- `bridge/config` — parse/validate the bridge config (senders, receivers,
  daemon URL, namespace).
- `bridge/ownership` — the daemon-stream ownership marker (below).
- `bridge/daemon_client` — REST adapter for the AES67 daemon (cpprestsdk).
- `bridge/reconciler` — ownership-safe create/update/delete planning.
- `nmos/nmos_resources` — builds the IS-04 resource graph (Node, Device, Source,
  Flow, Sender, Receiver) with deterministic UUIDs.
- `nmos/connection` — IS-05 `auto` resolution, sender SDP/transport-file
  generation, and the activation→daemon reconcile.

### How activation reaches the daemon

When a controller activates a sender/receiver (immediate, scheduled or bulk),
`nmos-cpp` resolves `auto` parameters and moves the connection to `/active`, then
invokes the bridge's activation callback. The callback wakes a reconcile pass
that reads the resulting active state for **all** configured streams and pushes
the difference to the daemon (`PUT/DELETE /api/source|sink/:id`). The same
reconcile runs periodically, so the bridge self-heals if the daemon restarts.

## Ownership marker (fail-closed safety)

The AES67 daemon exposes no custom per-stream metadata, so the bridge encodes
ownership in the daemon stream **name**:

```text
NMOS(<namespace>)/sender/<nmos_id> <label>
NMOS(<namespace>)/receiver/<nmos_id> <label>
```

Only streams matching the configured `namespace` are considered bridge-owned.
The parser also recognizes older square-bracket markers, including names already
sanitized by the daemon, so existing bridge-owned streams can be reconciled.

- Bridge-owned streams that are no longer wanted are deleted.
- A receiver with `master_enable = false` has its daemon sink removed.
- An **unmanaged** stream occupying a configured daemon slot is **never
  overwritten**; reconciliation reports the conflict and leaves it alone (fail
  closed). The same applies to streams owned by a different namespace.

This logic lives in `bridge/reconciler` and is covered by unit tests.

## Implementation status

Implemented:

- NMOS Node on `nmos-cpp`: IS-04 (node/registration/DNS-SD/heartbeats) and
  IS-05 (immediate/scheduled/bulk) via the library.
- Deterministic resource UUIDs derived from `namespace` + `nmos_id`
  (stable across restarts/hosts).
- Audio (L16/L24) sender/receiver mapping, sender SDP generation, IS-05 `auto`
  resolution to the configured multicast/interface.
- Ownership-safe reconciliation onto the daemon REST API, periodic + on
  activation.
- Unit tests (Catch2) for ownership, config and reconciler; CI building against
  `nmos-cpp` from Conan.
- Layered CI test gates:
  - Fast hermetic unit tests for config, ownership and reconcile planning.
  - Real-daemon integration against `bondagit/aes67-linux-daemon` built with its
    upstream `FAKE_DRIVER=ON` test mechanism, covering IS-05 activation,
    daemon `/api/streams`, receiver disable cleanup and fail-closed unmanaged
    stream conflicts.
  - AMWA `nmos-testing` conformance suites for IS-04-01, IS-04-03, IS-05-01 and
    IS-05-02.

Not implemented (by design / future work):

- IS-07 (events) and IS-10/BCP-003 (authorization) — explicitly out of scope.
- Reading the authoritative sender SDP from the daemon
  (`GET /api/source/sdp/:id`); the bridge currently generates the sender SDP
  from the IS-04 resources via `nmos-cpp`.

## Building

Dependencies are fetched with [Conan](https://conan.io) (`nmos-cpp`, Boost,
cpprestsdk, OpenSSL, Catch2). A DNS-SD implementation must be present on the
system; on Linux install the Avahi Bonjour-compat headers.

```bash
sudo apt-get install -y g++-13 cmake ninja-build libavahi-compat-libdnssd-dev avahi-daemon
pip install "conan~=2.20"
conan profile detect   # then ensure it uses gcc (see .github/workflows/ci.yml for a known-good profile)

conan install . --build=missing -s build_type=Release
cmake -S . -B build/Release -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$PWD/build/Release/generators/conan_toolchain.cmake"
cmake --build build/Release
ctest --test-dir build/Release --output-on-failure
```

> Note: on systems where `/usr/bin/c++` points to clang, force gcc in the Conan
> profile (`tools.build:compiler_executables`) — see `.github/workflows/ci.yml`.

## Test layers

The CI workflow intentionally keeps three separate layers:

1. `unit` runs the Catch2 tests. These remain the fast, blocking hermetic gate for
   ownership parsing, config validation and reconcile decisions. In-process fakes
   are limited to this pure logic layer.
2. `real-daemon-integration` checks out `bondagit/aes67-linux-daemon`, builds it
   with the upstream `buildfake.sh` path (`FAKE_DRIVER=ON`, real HTTP/session/SDP
   daemon code), starts it on loopback, starts the bridge, performs IS-05
   immediate sender/receiver activations, and verifies the real daemon state.
   The daemon exposes multicast address and channel map via `/api/streams`; its
   source RTP port is verified through `GET /api/source/sdp/:id`, because
   `/api/streams` does not include a per-source port field.
3. `nmos-conformance` starts the same real daemon plus the bridge and runs
   AMWA-TV `nmos-testing` for IS-04-01, IS-04-03, IS-05-01 and IS-05-02. Failures
   fail CI, with JUnit XML written under `build/nmos-testing`. The runner disables
   DNS-SD-dependent checks because the Linux Avahi Bonjour compatibility layer
   used by the Conan `nmos-cpp` build reports `DNSServiceCreateConnection` as
   unsupported in this CI environment; direct IS-04/IS-05 API checks still fail
   the job. IS-05-02 sender destination-variation tests are also ignored because
   each bridge sender is intentionally constrained to the configured daemon
   multicast address.

The generated AMWA XML may also contain AMWA-reported skips that are not bridge
failures: IS-10 authorization checks are out of scope, optional BCP tag/capability
checks are skipped when the configured resources do not advertise those optional
features, peer-to-peer runs have no Query API registry, and sender manifest tests
can be unclear while the sender remains inactive. The harness-managed exceptions
are limited to the DNS-SD/registry checks and fixed sender-destination checks
above.

To run the real-daemon and conformance layers locally, install the additional
packages used by CI for `bondagit/aes67-linux-daemon`: `build-essential`,
`clang-18`, `libboost-all-dev`, `libasound2-dev`, `libavahi-client-dev`,
`libfaac-dev`, `alsa-utils`, `dbus` and `python3-venv`. Upstream
`buildfake.sh` expects `/usr/bin/clang` and `/usr/bin/clang++`; the Cursor Cloud
image creates those symlinks to clang 18.

## Running

```bash
./build/Release/aes67-nmos-bridge config/example.json
```

The configuration file is a single JSON document that carries both standard
`nmos-cpp` settings (e.g. `http_port`, `label`, registry settings) and the
bridge-specific keys:

| Key | Default | Purpose |
| --- | --- | --- |
| `daemon_base_url` | `http://127.0.0.1:8080` | AES67 daemon REST base URL |
| `daemon_interface_name` | first NMOS host interface | Local interface used for RTP `interface_bindings` and source/interface IP constraints; set this to the AES67 daemon `interface_name` (for example `eno2`) when NMOS control and AES67 media are on different networks |
| `namespace` | `default` | Ownership namespace + UUID seed |
| `nmos_api_address_cidrs` | unset | Optional IPv4 CIDR allow-list used to derive `nmos-cpp` `host_addresses`, limiting advertised Node API endpoints (for example `["10.0.0.0/8"]`) |
| `reconcile_interval_seconds` | `5` | Periodic reconcile period |
| `senders` / `receivers` | `[]` | Stream definitions (see `config/example.json`) |

Each sender maps to `PUT /api/source/:id`; each receiver to `PUT /api/sink/:id`
with `use_sdp: true`. Senders require a multicast `address`; receivers are
configured from the SDP transport file supplied at IS-05 activation.

For stable resource IDs across restarts the bridge derives a `seed_id` from the
namespace automatically; set `seed_id` explicitly in the config to override.

Example for a host whose NMOS control API should be advertised only on 10/8
addresses while the AES67 daemon streams on `eno2`:

```json
{
  "nmos_api_address_cidrs": ["10.0.0.0/8"],
  "daemon_interface_name": "eno2"
}
```

## Project history

This repository began as a Python prototype that implemented a minimal NMOS API
surface by hand. To guarantee on-the-wire behaviour matching other NMOS devices
(and to get registration, DNS-SD, scheduled and bulk activations for free), it
was rewritten in C++ on top of `nmos-cpp`. The Python implementation has been
removed.
