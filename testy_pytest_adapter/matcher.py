from __future__ import annotations

import re

_PARAM_SUFFIX = re.compile(r"\[.*\]$")


def external_id(nodeid: str, *, strip_params: bool = True) -> str:
    nodeid = nodeid.replace("\\", "/").strip()
    if strip_params:
        last = nodeid.rsplit("::", 1)
        last[-1] = _PARAM_SUFFIX.sub("", last[-1])
        nodeid = "::".join(last)
    return nodeid
