# AES67 NMOS Bridge

Standalone control service for making NMOS-controlled streams the source of truth for
`bondagit/aes67-linux-daemon`.

The bridge owns only streams listed in its config. On each reconciliation pass it:

1. Reads daemon state from `GET /api/streams`.
2. Creates configured senders/receivers that are missing.
3. Updates configured senders/receivers whose daemon payload drifted.
4. Deletes daemon streams that carry this bridge's NMOS ownership marker but are no longer
   present in the bridge config.
5. Refuses to overwrite unmanaged streams that occupy a configured daemon slot.

## Ownership marker

The AES67 daemon does not expose custom metadata for streams, so the bridge uses the stream name
as its durable ownership marker:

```text
NMOS[<namespace>]/sender/<nmos_id> <label>
NMOS[<namespace>]/receiver/<nmos_id> <label>
```

Only streams matching the configured namespace are considered bridge-owned. Unmanaged streams are
left alone unless they block a configured daemon ID, in which case reconciliation fails.

## Current implementation status

This is the first service slice: config parsing, daemon REST adapter, reconciliation engine,
one-shot CLI, long-running service loop, health/status endpoints, tests, and CI.

Next implementation slices should add:

- IS-04 Node API resources and registration/peer discovery.
- IS-05 Connection API staged/active resources.
- IS-05 activation callbacks that update the config-backed desired receiver/sender state.
- AMWA `nmos-testing` conformance jobs once the NMOS HTTP APIs are present.

## Configuration

See `config/example.json`.

Sender config maps directly to `PUT /api/source/:id`. Receiver config maps directly to
`PUT /api/sink/:id` with `use_sdp: true`.

## Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
aes67-nmos-bridge --config config/example.json reconcile --dry-run
aes67-nmos-bridge --config config/example.json run
```

Health endpoints are served on the configured host/port:

- `GET /healthz`
- `GET /readyz`
- `GET /status`

## Test

```bash
pytest
ruff check .
```
