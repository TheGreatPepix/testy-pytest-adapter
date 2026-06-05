from __future__ import annotations

import os
import tempfile

_current: str | None = None
_pending: dict[str, list[str]] = {}
_installed = False
_capture_bytes = True


def set_current(nodeid: str | None) -> None:
    global _current
    _current = nodeid


def register(path) -> None:
    if _current is None:
        return
    _pending.setdefault(_current, []).append(str(path))


def pop(nodeid: str) -> list[str]:
    return _pending.pop(nodeid, [])


def install(capture_bytes: bool) -> bool:
    global _installed, _capture_bytes
    _capture_bytes = capture_bytes
    if _installed:
        return True
    try:
        import allure_commons
        from allure_commons import hookimpl
    except ImportError:
        return False

    class _AttachListener:
        @hookimpl
        def attach_file(self, source, name, attachment_type, extension):
            register(source)

        @hookimpl
        def attach_data(self, body, name, attachment_type, extension):
            if not _capture_bytes:
                return
            data = bytes(body) if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
            ext = str(extension or getattr(attachment_type, "extension", "") or "").lstrip(".")
            stem = "".join(
                ch for ch in str(name or "attachment") if ch.isalnum() or ch in (" ", "-", "_")
            ).strip() or "attachment"
            suffix = f".{ext}" if ext else ""
            folder = tempfile.mkdtemp(prefix="testy_attach_")
            path = os.path.join(folder, f"{stem}{suffix}")
            with open(path, "wb") as handle:
                handle.write(data)
            register(path)

    allure_commons.plugin_manager.register(_AttachListener())
    _installed = True
    return True
