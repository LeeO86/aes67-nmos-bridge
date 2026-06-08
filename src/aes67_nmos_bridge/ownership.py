from __future__ import annotations

from dataclasses import dataclass

from .models import StreamSide


@dataclass(frozen=True)
class Ownership:
    namespace: str
    side: StreamSide
    nmos_id: str


def managed_name(namespace: str, side: StreamSide, nmos_id: str, label: str) -> str:
    return f"NMOS[{namespace}]/{side}/{nmos_id} {label}"


def parse_managed_name(name: str) -> Ownership | None:
    prefix = "NMOS["
    if not name.startswith(prefix):
        return None

    namespace_end = name.find("]/", len(prefix))
    if namespace_end == -1:
        return None

    namespace = name[len(prefix) : namespace_end]
    rest = name[namespace_end + 2 :]
    parts = rest.split("/", 2)
    if len(parts) < 2 or parts[0] not in {"sender", "receiver"}:
        return None

    nmos_id = parts[1].split(" ", 1)[0]
    if not namespace or not nmos_id:
        return None

    return Ownership(namespace=namespace, side=parts[0], nmos_id=nmos_id)  # type: ignore[arg-type]
