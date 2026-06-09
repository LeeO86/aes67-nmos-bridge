# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

**AES67 NMOS Bridge** is a C++ NMOS Node (AMWA IS-04/IS-05) built on [`sony/nmos-cpp`](https://github.com/sony/nmos-cpp). It maps NMOS senders/receivers onto `bondagit/aes67-linux-daemon` streams via REST and reconciles them with ownership-safe create/update/delete logic.

The Python prototype has been **removed**; this repo is C++ only.

### System prerequisites

Install once on the VM (not in the update script):

```bash
sudo apt-get install -y g++-13 cmake ninja-build libavahi-compat-libdnssd-dev avahi-daemon
pip install "conan~=2.20"
```

Configure the Conan profile for gcc-13 (see `.github/workflows/ci.yml` for the full profile with `tools.build:compiler_executables`).

### Build

```bash
conan install . --build=missing -s build_type=Release
cmake -S . -B build/Release -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/build/Release/generators/conan_toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build/Release
ctest --test-dir build/Release --output-on-failure
```

### Run

| Service | Required? | Default URL | Notes |
|---|---|---|---|
| `aes67-linux-daemon` | Yes for live daemon sync | `http://127.0.0.1:8080` | External; not in this repo |
| `aes67-nmos-bridge` | Yes for E2E | `http://127.0.0.1:3210` | NMOS IS-04/IS-05 (`http_port` in config) |
| `avahi-daemon` | Optional | — | DNS-SD/registration discovery; node still serves APIs without a registry |

```bash
./build/Release/aes67-nmos-bridge config/example.json
```

### Important behaviour

- **Daemon sync is activation-driven**: streams are pushed to the AES67 daemon only after IS-05 activation (`master_enable: true`). Config alone does not populate the daemon.
- Periodic reconcile (default 5s) self-heals daemon drift after activation.
- Without a running registry, expect DNS-SD browse warnings in logs; the Node API still works for direct HTTP testing.

### E2E without real daemon

Start a minimal mock HTTP server on port 8080 implementing `/api/streams`, `PUT/DELETE /api/source/:id`, and `PUT/DELETE /api/sink/:id` (see `tests/test_reconciler.cpp` and `src/bridge/daemon_client.cpp`). Then:

1. Start the bridge: `./build/Release/aes67-nmos-bridge config/example.json`
2. Query IS-04: `GET http://127.0.0.1:3210/x-nmos/node/v1.0/senders/`
3. Activate via IS-05: `PATCH .../connection/v1.0/single/senders/{id}/staged/` with `"master_enable": true` and `"activation": {"mode": "activate_immediate"}`
4. Verify daemon: `GET http://127.0.0.1:8080/api/streams`

### Lint / test

No separate linter in-repo; CI runs `ctest` only. Unit tests use Catch2 and do not require external services.
