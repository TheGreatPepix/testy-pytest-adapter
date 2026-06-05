from __future__ import annotations

_current: str | None = None
_trees: dict[str, list] = {}
_stacks: dict[str, list] = {}
_node_stacks: dict[str, list] = {}
_installed = False


def set_current(nodeid: str | None) -> None:
    global _current
    _current = nodeid
    if nodeid is not None and nodeid not in _trees:
        roots: list = []
        _trees[nodeid] = roots
        _stacks[nodeid] = [roots]
        _node_stacks[nodeid] = []


def pop(nodeid: str) -> list:
    _stacks.pop(nodeid, None)
    _node_stacks.pop(nodeid, None)
    return _trees.pop(nodeid, [])


def _on_start(title: str) -> None:
    stack = _stacks.get(_current)
    if not stack:
        return
    node = {"title": title, "children": [], "status": "passed"}
    stack[-1].append(node)
    stack.append(node["children"])
    _node_stacks.setdefault(_current, []).append(node)


def _on_stop(exc_type=None) -> None:
    stack = _stacks.get(_current)
    node_stack = _node_stacks.get(_current)
    if node_stack:
        node = node_stack.pop()
        if exc_type is not None:
            node["status"] = "failed"
    if stack and len(stack) > 1:
        stack.pop()


def render(tree: list, indent: int = 0) -> str:
    lines: list[str] = []
    for node in tree:
        lines.append("  " * indent + f"- {node['title']}")
        if node["children"]:
            lines.append(render(node["children"], indent + 1))
    return "\n".join(lines)


def install() -> bool:
    global _installed
    if _installed:
        return True
    try:
        import allure_commons
        from allure_commons import hookimpl
    except ImportError:
        return False

    class _StepListener:
        @hookimpl
        def start_step(self, uuid, title, params):
            _on_start(title)

        @hookimpl
        def stop_step(self, uuid, exc_type=None, exc_val=None, exc_tb=None):
            _on_stop(exc_type)

    allure_commons.plugin_manager.register(_StepListener())
    _installed = True
    return True
