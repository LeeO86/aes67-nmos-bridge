"""Minimal SDP helpers for the NMOS transport-file surface.

The AES67 daemon is the authoritative source of a running sender's SDP
(``GET /api/source/sdp/:id``). To keep the NMOS read/connection API decoupled
from the daemon (and unit-testable without it), the bridge generates a
best-effort SDP for senders from desired config, and parses just enough of a
receiver SDP to populate IS-05 transport parameters.

These helpers deliberately implement only the small subset of RFC 4566 / AES67
that the bridge needs; they are not a general-purpose SDP library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import SenderConfig

_CONNECTION_RE = re.compile(r"^c=IN IP4 ([0-9.]+)(?:/\d+)?\s*$")
_MEDIA_RE = re.compile(r"^m=audio (\d+) RTP/AVP (\d+)\s*$")
_SOURCE_FILTER_RE = re.compile(r"^a=source-filter: incl IN IP4 \S+ ([0-9.]+)\s*$")


@dataclass(frozen=True)
class SdpConnection:
    multicast_ip: str | None = None
    destination_port: int | None = None
    source_ip: str | None = None


def parse_sdp_connection(sdp: str) -> SdpConnection:
    multicast_ip: str | None = None
    destination_port: int | None = None
    source_ip: str | None = None

    for line in sdp.splitlines():
        media = _MEDIA_RE.match(line)
        if media is not None:
            destination_port = int(media.group(1))
            continue
        source_filter = _SOURCE_FILTER_RE.match(line)
        if source_filter is not None:
            source_ip = source_filter.group(1)
            continue
        connection = _CONNECTION_RE.match(line)
        if connection is not None and multicast_ip is None:
            multicast_ip = connection.group(1)

    return SdpConnection(
        multicast_ip=multicast_ip,
        destination_port=destination_port,
        source_ip=source_ip,
    )


def sender_sdp(sender: SenderConfig, *, session_id: str) -> str:
    """Generate a best-effort AES67/RAVENNA-style SDP for a configured sender.

    The authoritative SDP for a live stream comes from the daemon; this is used
    for the IS-05 ``transportfile`` endpoint and IS-04 ``manifest_href`` so that
    controllers can read a transport file even before the daemon is reachable.
    """

    channels = max(1, len(sender.map))
    address = sender.address or "0.0.0.0"
    origin_ip = address
    refclk = (
        "a=ts-refclk:ptp=IEEE1588-2008:traceable"
        if sender.refclk_ptp_traceable
        else "a=ts-refclk:ptp=IEEE1588-2008:00-00-00-00-00-00-00-00:0"
    )
    lines = [
        "v=0",
        f"o=- {session_id} {session_id} IN IP4 {origin_ip}",
        f"s={sender.label}",
        "t=0 0",
        f"m=audio {sender.rtp_port} RTP/AVP {sender.payload_type}",
        f"c=IN IP4 {address}/{sender.ttl}",
        f"a=rtpmap:{sender.payload_type} {sender.codec}/{sender.sample_rate}/{channels}",
        "a=ptime:1",
        "a=mediaclk:direct=0",
        refclk,
        "a=sendonly",
    ]
    return "\n".join(lines) + "\n"
