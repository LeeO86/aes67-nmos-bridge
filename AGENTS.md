# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

**AES67 NMOS Bridge** is a Python control service that reconciles NMOS-configured streams against `bondagit/aes67-linux-daemon` via REST (`GET /api/streams`, `PUT/DELETE /api/source/:id`, `PUT/DELETE /api/sink/:id`). There is no frontend, database, or docker-compose in this repo.

### System prerequisites

- Python **≥ 3.11** (CI tests 3.11 and 3.12).
- `python3-venv` must be installed on the VM (`apt install python3.12-venv`) before creating `.venv`.

### Dependency install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

### Lint / test (no external services required)

```bash
. .venv/bin/activate
ruff check .
pytest
```

### Running the bridge

| Service | Required? | Default URL | Notes |
|---|---|---|---|
| `aes67-linux-daemon` | Yes for `run` / live `reconcile` | `http://127.0.0.1:8080` | External; not in this repo |
| `aes67-nmos-bridge run` | Yes for E2E | `http://127.0.0.1:8090` | Health: `/healthz`, `/readyz`, `/status` |

**One-shot dry-run (plan only):**

```bash
aes67-nmos-bridge --config config/example.json reconcile --dry-run
```

Exits with code `2` when changes would be made (expected in dry-run).

**Long-running service:**

```bash
aes67-nmos-bridge --config config/example.json run
```

No hot-reload; restart the process after code changes.

### E2E without real daemon

For local dev without `bondagit/aes67-linux-daemon`, start a minimal mock HTTP server on port 8080 that implements `/api/streams`, `/api/source/:id`, and `/api/sink/:id` (see `tests/test_daemon_client.py` for the expected API shape). Then run the bridge against `config/example.json`.

### Gotchas

- `/readyz` returns **503** until the first successful reconciliation pass completes.
- The bridge refuses to overwrite unmanaged daemon streams that occupy a configured slot; reconciliation fails in that case.
- NMOS IS-04/IS-05 APIs are **not implemented yet** (planned future slices).
