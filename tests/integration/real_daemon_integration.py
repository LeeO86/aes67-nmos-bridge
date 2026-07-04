#!/usr/bin/env python3
"""End-to-end bridge test against bondagit's real daemon with fake driver."""

import argparse
import json
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


DAEMON = "http://127.0.0.1:8080"


def request(method, url, body=None, expected=(200,)):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = response.read().decode("utf-8")
            if response.status not in expected:
                raise AssertionError(f"{method} {url} returned HTTP {response.status}: {payload}")
            if response.headers.get_content_type() == "application/json" and payload:
                return json.loads(payload)
            return payload
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        if exc.code in expected:
            return payload
        raise AssertionError(f"{method} {url} returned HTTP {exc.code}: {payload}") from exc


def wait_for(description, func, timeout=20, interval=0.25):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = func()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001 - diagnostics are reported on timeout
            last_error = exc
        time.sleep(interval)
    raise AssertionError(f"Timed out waiting for {description}: {last_error}")


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


def bridge_config(port, namespace):
    return {
        "logging_level": 0,
        "http_port": port,
        "label": f"AES67 NMOS Bridge {namespace}",
        "description": "CI integration test bridge",
        "daemon_base_url": DAEMON,
        "namespace": namespace,
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


def first_resource(port, resource):
    resources = request("GET", f"http://127.0.0.1:{port}/x-nmos/node/v1.0/{resource}/")
    if not isinstance(resources, list) or not resources:
        raise AssertionError(f"No NMOS {resource} reported")
    return resources[0]["id"]


def activate_sender(port, sender_id, destination_ip="239.2.0.1"):
    request(
        "PATCH",
        f"http://127.0.0.1:{port}/x-nmos/connection/v1.0/single/senders/{sender_id}/staged/",
        {
            "master_enable": True,
            "transport_params": [
                {
                    "source_ip": "auto",
                    "destination_ip": destination_ip,
                    "destination_port": 5004,
                    "rtp_enabled": True,
                }
            ],
            "activation": {"mode": "activate_immediate"},
        },
    )


def activate_receiver(port, receiver_id, enabled):
    body = {"master_enable": enabled, "activation": {"mode": "activate_immediate"}}
    if enabled:
        body["transport_file"] = {
            "type": "application/sdp",
            "data": (
                "v=0\r\n"
                "o=- 1 0 IN IP4 127.0.0.1\r\n"
                "s=Return Feed\r\n"
                "c=IN IP4 239.2.0.1/15\r\n"
                "t=0 0\r\n"
                "a=clock-domain:PTPv2 0\r\n"
                "m=audio 5004 RTP/AVP 98\r\n"
                "c=IN IP4 239.2.0.1/15\r\n"
                "a=rtpmap:98 L24/48000/2\r\n"
                "a=sync-time:0\r\n"
                "a=framecount:48-48\r\n"
                "a=ptime:1\r\n"
                "a=maxptime:1\r\n"
                "a=mediaclk:direct=0\r\n"
                "a=ts-refclk:ptp=IEEE1588-2008:00-00-00-00-00-00-00-00:0\r\n"
                "a=recvonly\r\n"
            ),
        }
    request(
        "PATCH",
        f"http://127.0.0.1:{port}/x-nmos/connection/v1.0/single/receivers/{receiver_id}/staged/",
        body,
    )


def stream_by_id(streams, side, daemon_id):
    key = "sources" if side == "source" else "sinks"
    for stream in streams.get(key, []):
        if stream.get("id") == daemon_id:
            return stream
    return None


def assert_source_created(expected_address="239.2.0.1"):
    streams = request("GET", f"{DAEMON}/api/streams")
    source = stream_by_id(streams, "source", 0)
    assert source, "Expected daemon source id 0 to be created"
    assert source["name"] == "NMOS(e2e)/sender/program-main Program Main"
    assert source["address"] == expected_address
    assert source["map"] == [0, 1]
    assert source["codec"] == "L24"
    sdp = request("GET", f"{DAEMON}/api/source/sdp/0")
    assert "m=audio 5004 RTP/AVP 98" in sdp, sdp
    return source


def assert_sink_state(expected_present):
    streams = request("GET", f"{DAEMON}/api/streams")
    sink = stream_by_id(streams, "sink", 0)
    if expected_present:
        assert sink, "Expected daemon sink id 0 to be created"
        assert sink["name"] == "NMOS(e2e)/receiver/return-feed Return Feed"
        assert sink["map"] == [0, 1]
        assert "m=audio 5004 RTP/AVP 98" in sink["sdp"]
    else:
        assert sink is None, f"Expected daemon sink id 0 to be removed, found {sink}"
    return True


def put_unmanaged_source():
    request(
        "PUT",
        f"{DAEMON}/api/source/0",
        {
            "enabled": True,
            "name": "External source",
            "io": "Audio Device",
            "map": [2, 3],
            "max_samples_per_packet": 48,
            "codec": "L24",
            "address": "239.9.9.9",
            "ttl": 15,
            "payload_type": 98,
            "dscp": 34,
            "refclk_ptp_traceable": False,
        },
    )


def run_bridge_case(bridge_bin, config_path, port, namespace, body):
    config_path.write_text(json.dumps(bridge_config(port, namespace), indent=2), encoding="utf-8")
    proc = start_process([str(bridge_bin), str(config_path)])
    error = None
    try:
        wait_for("bridge Node API", lambda: request("GET", f"http://127.0.0.1:{port}/x-nmos/node/v1.0/"))
        return body(port)
    except Exception as exc:  # noqa: BLE001 - include bridge logs below
        error = exc
    finally:
        stop_process(proc)
        output = collect_output(proc)
        if proc.returncode not in (0, -signal.SIGTERM):
            raise AssertionError(f"Bridge exited with {proc.returncode}\n{output}")
        if error is not None:
            raise AssertionError(f"{error}\nBridge output:\n{output}") from error


def run(args):
    daemon = start_process([str(args.daemon), "-c", str(args.daemon_config), "-p", "8080"])
    try:
        wait_for("daemon REST API", lambda: request("GET", f"{DAEMON}/api/streams"))
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            def activation_case(port):
                sender_id = first_resource(port, "senders")
                receiver_id = first_resource(port, "receivers")
                activate_sender(port, sender_id)
                wait_for("daemon source after sender activation", assert_source_created)
                activate_sender(port, sender_id, "239.2.0.55")
                wait_for(
                    "daemon source after sender destination update",
                    lambda: assert_source_created("239.2.0.55"),
                )
                activate_receiver(port, receiver_id, True)
                wait_for("daemon sink after receiver activation", lambda: assert_sink_state(True))
                activate_receiver(port, receiver_id, False)
                wait_for("daemon sink removal after receiver disable", lambda: assert_sink_state(False))

            run_bridge_case(args.bridge, tmpdir / "bridge-e2e.json", 3210, "e2e", activation_case)
            request("DELETE", f"{DAEMON}/api/source/0", expected=(200, 404))
            put_unmanaged_source()

            def conflict_case(port):
                sender_id = first_resource(port, "senders")
                activate_sender(port, sender_id)

                def unmanaged_preserved():
                    streams = request("GET", f"{DAEMON}/api/streams")
                    source = stream_by_id(streams, "source", 0)
                    assert source, "Expected pre-existing unmanaged source"
                    assert source["name"] == "External source"
                    assert source["address"] == "239.9.9.9"
                    assert source["map"] == [2, 3]
                    return True

                wait_for("unmanaged daemon source to remain untouched", unmanaged_preserved, timeout=5)

            run_bridge_case(
                args.bridge,
                tmpdir / "bridge-conflict.json",
                3211,
                "e2e-conflict",
                conflict_case,
            )
        print("real-daemon integration passed")
    finally:
        stop_process(daemon)
        output = collect_output(daemon)
        if daemon.returncode not in (0, -signal.SIGTERM):
            raise AssertionError(f"Daemon exited with {daemon.returncode}\n{output}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--daemon", type=Path, required=True)
    parser.add_argument("--daemon-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:  # noqa: BLE001 - preserve concise CI failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
