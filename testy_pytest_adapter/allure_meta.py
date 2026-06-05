from __future__ import annotations

_TREE_ORDER = {
    "epic": 0, "feature": 1, "story": 2,
    "parentSuite": 3, "suite": 4, "subSuite": 5,
}


def suite_path(item) -> list[str]:
    found: dict[int, str] = {}
    for marker in item.iter_markers(name="allure_label"):
        label_type = marker.kwargs.get("label_type")
        if label_type in _TREE_ORDER and marker.args:
            found[_TREE_ORDER[label_type]] = str(marker.args[0]).strip()
    return [found[key] for key in sorted(found) if found[key]]


def title(item) -> str | None:
    name = getattr(getattr(item, "function", None), "__allure_display_name__", None)
    return str(name) if name else None


def root_attribute_path(item) -> str:
    parts = _nodeid_dir_parts(item.nodeid)
    return parts[0] if parts else ""


def suite_attribute_paths(item, suites: list[str]) -> list[str]:
    if not suites:
        return []
    parts = _nodeid_dir_parts(item.nodeid)
    if not parts:
        return ["" for _ in suites]

    paths: list[str] = []
    for index, _ in enumerate(suites):
        if len(suites) == 1 or index == len(suites) - 1:
            depth = len(parts)
        elif index == 0:
            depth = min(2, len(parts))
        else:
            depth = min(index + 2, len(parts))
        paths.append("/".join(parts[:depth]))
    return paths


def _nodeid_dir_parts(nodeid: str) -> list[str]:
    path = str(nodeid).replace("\\", "/").split("::", 1)[0]
    path = path.split("[", 1)[0].strip("/")
    if not path:
        return []
    return [part for part in path.split("/")[:-1] if part]
