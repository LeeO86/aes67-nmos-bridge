#!/usr/bin/env python3
"""Run AMWA nmos-testing against a live bridge node."""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


DAEMON = "http://127.0.0.1:8080"
BRIDGE_PORT = 3210
IGNORED_TESTS = {
    "IS-04-01": [
        # DNS-SD registration and registry/Query API checks. This project is a
        # standalone Node, and DNS-SD cannot be exercised in CI with Avahi's
        # Bonjour compatibility layer.
        "test_01",
        "test_01_01",
        "test_02",
        "test_02_01",
        "test_03",
        "test_03_01",
        "test_04",
        "test_05",
        "test_07",
        "test_07_01",
        "test_08",
        "test_08_01",
        "test_09",
        "test_09_01",
        "test_10",
        "test_10_01",
        "test_11",
        "test_11_01",
        "test_15",
        "test_16",
        "test_16_01",
        "test_21",
    ],
    "IS-04-03": [
        # Peer-to-peer mDNS advertisement check; see UserConfig.py rationale.
        "test_01",
    ],
    "IS-05-02": [
        # AES67 daemon sources are multicast RTP sources. Multicast sender
        # destination variation is tested, but unicast sender activation is not
        # a supported bridge/daemon mode.
        "test_10",
        "test_11",
    ],
}


def request(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = response.read().decode("utf-8")
        if response.headers.get_content_type() == "application/json" and payload:
            return json.loads(payload)
        return payload


def wait_for(description, func, timeout=30, interval=0.5):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = func()
            if result is not None:
                return result
        except Exception as exc:  # noqa: BLE001 - surfaced on timeout
            last_error = exc
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for {description}: {last_error}")


def start_process(args, cwd=None):
    return subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def stop_process(proc):
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def collect_output(proc):
    if not proc.stdout:
        return ""
    try:
        return proc.stdout.read()
    except ValueError:
        return ""


def bridge_config():
    return {
        "logging_level": 0,
        "http_port": BRIDGE_PORT,
        "label": "AES67 NMOS Bridge Conformance",
        "description": "AMWA nmos-testing target",
        "daemon_base_url": DAEMON,
        "namespace": "conformance",
        "reconcile_interval_seconds": 0.2,
        "senders": [
            {
                "nmos_id": "program-main",
                "daemon_id": 0,
                "label": "Program Main",
                "description": "Main programme bus",
                "io": "Audio Device",
                "codec": "L24",
                "address": "239.2.0.1",
                "rtp_port": 5004,
                "sample_rate": 48000,
                "max_samples_per_packet": 48,
                "ttl": 15,
                "payload_type": 98,
                "dscp": 34,
                "refclk_ptp_traceable": True,
                "map": [0, 1],
            }
        ],
        "receivers": [
            {
                "nmos_id": "return-feed",
                "daemon_id": 0,
                "label": "Return Feed",
                "description": "Return from remote AES67 source",
                "io": "Audio Device",
                "delay": 576,
                "source": "",
                "sample_rate": 48000,
                "ignore_refclk_gmid": True,
                "map": [0, 1],
            }
        ],
    }


def advertised_bridge_host():
    node = request(f"http://127.0.0.1:{BRIDGE_PORT}/x-nmos/node/v1.3/self")
    for endpoint in node.get("api", {}).get("endpoints", []):
        if endpoint.get("protocol") == "http" and endpoint.get("port") == BRIDGE_PORT:
            return endpoint.get("host", "127.0.0.1")
    return "127.0.0.1"


def write_user_config(nmos_testing_dir, host):
    # The Linux Avahi Bonjour compatibility layer used by nmos-cpp reports
    # DNSServiceCreateConnection as unsupported in CI, so run deterministic
    # direct API conformance and skip DNS-SD-dependent checks.
    (nmos_testing_dir / "nmostesting" / "UserConfig.py").write_text(
        "\n".join(
            [
                "from . import Config as CONFIG",
                "CONFIG.ENABLE_DNS_SD = False",
                "CONFIG.DNS_SD_MODE = 'unicast'",
                f"CONFIG.QUERY_API_HOST = '{host}'",
                f"CONFIG.QUERY_API_PORT = {BRIDGE_PORT}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def suite_args(suite, output_dir, host):
    output = output_dir / f"{suite}.xml"
    ignored = IGNORED_TESTS.get(suite, [])
    if suite == "IS-05-02":
        args = [
            "suite",
            suite,
            "--host",
            host,
            host,
            "--port",
            str(BRIDGE_PORT),
            str(BRIDGE_PORT),
            "--version",
            "v1.3",
            "v1.1",
            "--selection",
            "all",
            "--output",
            str(output),
        ]
    else:
        version = "v1.1" if suite.startswith("IS-05") else "v1.3"
        args = [
            "suite",
            suite,
            "--host",
            host,
            "--port",
            str(BRIDGE_PORT),
            "--version",
            version,
            "--selection",
            "all",
            "--output",
            str(output),
        ]
    if ignored:
        args.extend(["--ignore", *ignored])
    return args


def run_suite(nmos_testing_dir, suite, output_dir, host):
    cmd = [sys.executable, "nmos-test.py"] + suite_args(suite, output_dir, host)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    completed = subprocess.run(cmd, cwd=nmos_testing_dir, env=env, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"{suite} failed with exit code {completed.returncode}")


def run(args):
    daemon = start_process([str(args.daemon), "-c", str(args.daemon_config), "-p", "8080"])
    bridge = None
    error = None
    try:
        wait_for("daemon REST API", lambda: request(f"{DAEMON}/api/streams"))
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            config_path = tmpdir / "bridge-conformance.json"
            config_path.write_text(json.dumps(bridge_config(), indent=2), encoding="utf-8")
            bridge = start_process([str(args.bridge), str(config_path)])
            wait_for(
                "bridge Node API",
                lambda: request(f"http://127.0.0.1:{BRIDGE_PORT}/x-nmos/node/v1.0/"),
            )
            host = advertised_bridge_host()
            print(f"Running nmos-testing against advertised Node API host {host}:{BRIDGE_PORT}")
            write_user_config(args.nmos_testing, host)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for suite in ("IS-04-01", "IS-04-03", "IS-05-01", "IS-05-02"):
                print(f"Running {suite}")
                run_suite(args.nmos_testing, suite, args.output_dir, host)
        print("nmos-testing conformance suites passed")
    except Exception as exc:  # noqa: BLE001 - include process logs below
        error = exc
    finally:
        if bridge is not None:
            stop_process(bridge)
            output = collect_output(bridge)
            if bridge.returncode not in (0, -signal.SIGTERM):
                print(f"Bridge exited with {bridge.returncode}\n{output}", file=sys.stderr)
            if error is not None:
                print(f"Bridge output:\n{output}", file=sys.stderr)
        stop_process(daemon)
        output = collect_output(daemon)
        if daemon.returncode not in (0, -signal.SIGTERM):
            print(f"Daemon exited with {daemon.returncode}\n{output}", file=sys.stderr)
        if error is not None:
            raise error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--daemon", type=Path, required=True)
    parser.add_argument("--daemon-config", type=Path, required=True)
    parser.add_argument("--nmos-testing", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:  # noqa: BLE001 - concise CI failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
