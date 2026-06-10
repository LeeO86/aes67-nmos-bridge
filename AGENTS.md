# AGENTS.md

## Cursor Cloud specific instructions

### Environment (automated)

Cloud agents use **`.cursor/environment.json`**, which:

1. Builds **`.cursor/Dockerfile`** with `gcc-13`/`g++-13`, `make`, `cmake`, `ninja`, Avahi DNS-SD headers, `avahi-daemon`, and the daemon/conformance test packages used by CI (`clang-18`, Boost, ALSA, FAAC, dbus, Python venv support). The image remains free of heavy Conan builds.
2. Writes the **gcc-13 Conan profile** (`tools.build:compiler_executables`) via `.cursor/setup-conan-profile.sh`.
3. On each session **`install`** (`.cursor/install.sh`) exports `CC`/`CXX`, runs Conan + CMake with `-DCMAKE_C_COMPILER=gcc-13 -DCMAKE_CXX_COMPILER=g++-13`, and builds.

No manual apt/Conan setup is required when this environment is active.

### Product overview

**AES67 NMOS Bridge** is a C++ NMOS Node (AMWA IS-04/IS-05) built on [`sony/nmos-cpp`](https://github.com/sony/nmos-cpp). It maps NMOS senders/receivers onto `bondagit/aes67-linux-daemon` streams via REST and reconciles them with ownership-safe create/update/delete logic.

### Build (manual / if install was skipped)

```bash
bash .cursor/install.sh
ctest --test-dir build/Release --output-on-failure
```

Or step-by-step (same as `install.sh`):

```bash
export CC=gcc-13 CXX=g++-13
bash .cursor/setup-conan-profile.sh
conan install . --build=missing -s build_type=Release
cmake -S . -B build/Release -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/build/Release/generators/conan_toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=gcc-13 \
  -DCMAKE_CXX_COMPILER=g++-13
cmake --build build/Release
ctest --test-dir build/Release --output-on-failure
```

### Run

| Service | Required? | Default URL | Notes |
|---|---|---|---|
| `aes67-linux-daemon` | Yes for live daemon sync | `http://127.0.0.1:8080` | External; not in this repo |
| `aes67-nmos-bridge` | Yes for E2E | `http://127.0.0.1:3210` | NMOS IS-04/IS-05 (`http_port` in config) |
| `avahi-daemon` | Optional | — | DNS-SD warnings OK without a registry; start manually if needed |

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
