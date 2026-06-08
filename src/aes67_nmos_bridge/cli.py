from __future__ import annotations

import argparse
import json
import signal
from collections.abc import Sequence

from .config import load_config
from .daemon_client import DaemonClient
from .service import BridgeService
from .store import DesiredStateStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aes67-nmos-bridge")
    parser.add_argument("--config", required=True, help="Path to bridge JSON configuration")
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write IS-05 activations back to the config file (in-memory only)",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    reconcile = subcommands.add_parser("reconcile", help="Run one reconciliation pass")
    reconcile.add_argument("--dry-run", action="store_true", help="Print planned changes only")

    subcommands.add_parser("run", help="Run the long-lived bridge service")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    state_path = None if args.no_persist else args.config
    store = DesiredStateStore(config, path=state_path)
    service = BridgeService(store, DaemonClient(config.daemon_base_url))

    if args.command == "reconcile":
        report = service.reconcile_once(dry_run=args.dry_run)
        print(
            json.dumps(
                {
                    "changed": report.changed,
                    "dry_run": report.dry_run,
                    "operations": [
                        {
                            "action": operation.action,
                            "side": operation.side,
                            "daemon_id": operation.daemon_id,
                            "reason": operation.reason,
                            "payload": operation.payload,
                        }
                        for operation in report.operations
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2 if report.changed and args.dry_run else 0

    def stop(_signum: int, _frame: object) -> None:
        service.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
