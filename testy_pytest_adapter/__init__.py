from __future__ import annotations

from . import attachments as _attachments
from .api import case

__all__ = ["attach", "case"]


def attach(path) -> None:
    _attachments.register(path)
