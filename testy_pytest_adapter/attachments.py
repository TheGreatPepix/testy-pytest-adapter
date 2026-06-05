from __future__ import annotations

_current: str | None = None
_pending: dict[str, list[str]] = {}


def set_current(nodeid: str | None) -> None:
    global _current
    _current = nodeid


def register(path) -> None:
    if _current is None:
        return
    _pending.setdefault(_current, []).append(str(path))


def pop(nodeid: str) -> list[str]:
    return _pending.pop(nodeid, [])
