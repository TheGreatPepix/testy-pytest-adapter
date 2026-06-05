from __future__ import annotations

from . import attachments as _attachments

__all__ = ["attach"]


def attach(path) -> None:
    _attachments.register(path)
