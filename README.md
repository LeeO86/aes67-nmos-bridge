# AES67 NMOS Bridge

Standalone control service that makes NMOS (AMWA IS-04/IS-05) the source of truth
for [`bondagit/aes67-linux-daemon`](https://github.com/bondagit/aes67-linux-daemon).

This is a separate service, **not** a fork of the daemon. It owns only the daemon
streams listed in its desired-state config and controls them through the daemon's
REST API.

## How it works

The bridge keeps a **desired-state store** (file-backed JSON today) that is the
authoritative description of the NMOS-managed senders/receivers. On each
reconciliation pass it:

1. Reads daemon state from `GET /api/streams`.
2. Creates configured senders/receivers that are missing.
3. Updates configured senders/receivers whose daemon payload drifted.
4. Deletes daemon streams that carry this bridge's NMOS ownership marker but are
   no longer present (or have been disabled) in the desired state.
5. Refuses to overwrite unmanaged streams that occupy a configured daemon slot
   (fail closed).

NMOS controllers read and drive the bridge through a native IS-04/IS-05 HTTP API.
An IS-05 immediate activation updates the desired-state store first (preserving
source-of-truth behaviour) and then reconciles the change onto the daemon.

```
NMOS controller ──IS-04/IS-05──▶ aes67-nmos-bridge ──REST──▶ aes67-linux-daemon
                                   (desired-state store)
```

## Ownership marker

The AES67 daemon exposes no custom per-stream metadata, so the bridge encodes
ownership in the daemon stream **name**:

```text
NMOS[<namespace>]/sender/<nmos_id> <label>
NMOS[<namespace>]/receiver/<nmos_id> <label>
```

Only streams matching the configured `namespace` are considered bridge-owned.
Unmanaged streams are never modified or deleted; if one occupies a configured
daemon slot, reconciliation fails closed rather than overwriting it. (If the
daemon ever gains a metadata field, that would be the preferred ownership marker;
the parsing lives in `src/aes67_nmos_bridge/ownership.py`.)

## Implementation status

Implemented:

- Config-driven desired-state store (`store.py`), thread-safe, optionally
  file-backed (atomic writes), the bridge's source of truth.
- Daemon REST adapter (`daemon_client.py`) — isolated and never called from unit
  tests (fakes are used instead).
- Reconciliation engine (`reconciler.py`) including create/update/delete,
  unmanaged-slot protection, and disabling receivers (`master_enable=false`
  removes the daemon sink).
- NMOS data model (`nmos.py`) with deterministic UUIDv5 IDs derived from
  `namespace` + `nmos_id` (stable across restarts/hosts).
- **IS-04 Node API (read-only)**: `self`, `devices`, `sources`, `flows`,
  `senders`, `receivers` under `/x-nmos/node/v1.3/`.
- **IS-05 Connection API**:
  - Read: `constraints`, `staged`, `active`, `transporttype`, and (senders only)
    `transportfile` under `/x-nmos/connection/v1.1/single/`.
  - Write: `PATCH .../staged` with **immediate** activation for senders and
    receivers, applying the change to the desired state and reconciling it onto
    the daemon.
- One-shot CLI (`reconcile`) and long-running service (`run`) with health/status
  endpoints.
- CI on Python 3.11 and 3.12 (`ruff` + `pytest`).

Planned (not yet implemented):

- IS-04 Registration API client and DNS-SD (unicast + peer-to-peer) discovery.
- IS-05 **scheduled** activations (`activate_scheduled_absolute` /
  `activate_scheduled_relative`) and bulk operations — these currently return
  `501 Not Implemented`.
- Reading the authoritative sender SDP from the daemon
  (`GET /api/source/sdp/:id`); today the sender `transportfile` is generated
  from desired config as a best-effort transport file.
- AMWA `nmos-testing` CI jobs (IS-04-01, IS-04-03, IS-05-01, IS-05-02).

## NMOS API surface

Base paths served by the bridge HTTP server (`http_host`/`http_port`):

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/x-nmos/node/v1.3/self/` | Node resource |
| GET | `/x-nmos/node/v1.3/{devices,sources,flows,senders,receivers}/` | Collections + `/{id}/` |
| GET | `/x-nmos/connection/v1.1/single/{senders,receivers}/{id}/constraints/` | One constraint set per leg |
| GET | `/x-nmos/connection/v1.1/single/{senders,receivers}/{id}/{staged,active}/` | Connection params |
| GET | `/x-nmos/connection/v1.1/single/{senders,receivers}/{id}/transporttype/` | `urn:x-nmos:transport:rtp.mcast` |
| GET | `/x-nmos/connection/v1.1/single/senders/{id}/transportfile/` | SDP (`application/sdp`) |
| PATCH | `/x-nmos/connection/v1.1/single/{senders,receivers}/{id}/staged/` | Immediate activation only |

Resource IDs are deterministic. For a given namespace and `nmos_id`, the sender,
receiver, source and flow UUIDs never change.

## Configuration

See `config/example.json`. Top-level keys:

| Key | Default | Purpose |
| --- | --- | --- |
| `daemon_base_url` | `http://127.0.0.1:8080` | AES67 daemon REST base URL |
| `namespace` | `default` | Ownership namespace + UUID seed |
| `reconcile_interval_seconds` | `5` | Reconcile loop period |
| `http_host` / `http_port` | `127.0.0.1` / `8090` | Bridge HTTP bind address |
| `advertised_host` | `""` | Host used in NMOS `href`s (falls back to `http_host`) |
| `node_label` | derived | NMOS Node/Device label |

Sender config maps to `PUT /api/source/:id`. Receiver config maps to
`PUT /api/sink/:id` with `use_sdp: true`.

### Config schema migration

This release adds optional fields; **existing configs remain valid** (all new
fields have defaults):

- Sender: `description`, `rtp_port` (default `5004`), `sample_rate` (default
  `48000`).
- Receiver: `enabled` (default `true`), `description`, `sample_rate` (default
  `48000`).
- Top-level: `advertised_host`, `node_label`.

The receiver `enabled` flag is new and meaningful: setting it to `false` (or an
IS-05 `master_enable: false` activation) causes the bridge to remove the daemon
sink it owns for that receiver. The ownership-marker format is unchanged.

When `run` is used, IS-05 immediate activations are written back to the
`--config` file so they survive restarts. Pass `--no-persist` to keep the
desired state in memory only.

## Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
aes67-nmos-bridge --config config/example.json reconcile --dry-run
aes67-nmos-bridge --config config/example.json run
```

Health endpoints (served on `http_host`/`http_port`):

- `GET /healthz`
- `GET /readyz`
- `GET /status`

## Test

```bash
pytest
ruff check .
```
