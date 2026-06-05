from __future__ import annotations

import logging
from dataclasses import asdict

import pytest

from . import allure_meta, attachments, step_capture
from .client import TestyClient
from .config import TestyConfig
from .matcher import external_id
from .models import CaseResult

log = logging.getLogger("testy_pytest")


def pytest_addoption(parser):
    group = parser.getgroup("testy", "TestY TMS reporting")
    group.addoption("--testy", action="store_true", default=False,
                    help="Enable reporting results to TestY.")
    group.addoption("--testy-url", dest="testy_url", default=None)
    group.addoption("--testy-token", dest="testy_token", default=None)
    group.addoption("--testy-project", dest="testy_project", default=None)
    group.addoption("--testy-plan", dest="testy_plan", default=None)
    group.addoption("--testy-root-name", dest="testy_root_name", default=None,
                    help="Common root TestSuite/TestPlan name to find or create.")
    group.addoption("--testy-suite-name", dest="testy_suite_name", default=None,
                    help="Root TestSuite name fallback; defaults to --testy-root-name.")
    group.addoption("--testy-plan-name", dest="testy_plan_name", default=None,
                    help="Root TestPlan name fallback; defaults to --testy-root-name.")
    group.addoption("--testy-suite", dest="testy_suite", default=None,
                    help="Target suite id for cases auto-created by --testy-sync.")
    group.addoption("--testy-sync", action="store_true", default=False,
                    help="Create missing cases and add them to the plan, then exit "
                         "without reporting (use with --collect-only as a pre-run step).")
    parser.addini("testy_enabled", "Enable TestY reporting.", type="bool", default=False)
    parser.addini("testy_url", "Base TestY url.")
    parser.addini("testy_project_id", "TestY project id.")
    parser.addini("testy_plan_id", "Root TestY test plan id.")
    parser.addini("testy_root_name", "Common root TestSuite/TestPlan name.")
    parser.addini("testy_suite_name", "Root TestSuite name fallback.")
    parser.addini("testy_plan_name", "Root TestPlan name fallback.")
    parser.addini("testy_suite_id", "Root TestY suite id for auto-created cases.")
    parser.addini("testy_automation_key", "TestCase attribute key for nodeid matching.",
                  default="automation_id")
    parser.addini("testy_override_cases",
                  "Re-write an existing case when its Allure steps changed "
                  "(False = seed stepless cases once, never overwrite).",
                  type="bool", default=True)
    parser.addini("testy_keep_params", "Keep pytest parameter suffixes in external ids.",
                  type="bool", default=False)
    parser.addini("testy_attach", "Attachment upload mode: failure, always, never.",
                  default="failure")
    parser.addini("testy_attach_bytes",
                  "Capture in-memory allure.attach(bytes/str) payloads (e.g. screenshots).",
                  type="bool", default=True)
    parser.addini("testy_auth_scheme", "Authorization scheme: Token or Bearer.",
                  default="Token")
    parser.addini("testy_insecure", "Disable TLS certificate verification.",
                  type="bool", default=False)
    parser.addini("testy_status_passed", "Project status name for passed tests.")
    parser.addini("testy_status_failed", "Project status name for failed tests.")
    parser.addini("testy_status_skipped", "Project status name for skipped tests.")
    parser.addini("testy_status_broken", "Project status name for broken tests.")
    parser.addini("testy_status_untested", "Project status name for untested tests.")


def pytest_configure(config):
    try:
        cfg = TestyConfig.from_pytest(config)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    if cfg is not None and config.getoption("testy_sync", default=False) and _xdist_requested(config):
        raise pytest.UsageError("TestY sync must run without xdist; remove -n/--numprocesses")
    config._testy = _TestyState(cfg) if cfg else None
    if config._testy is not None:
        step_capture.install()
        attachments.install(cfg.attach_bytes)


def pytest_runtest_setup(item):
    if getattr(item.config, "_testy", None) is not None:
        attachments.set_current(item.nodeid)
        step_capture.set_current(item.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    state: "_TestyState | None" = getattr(item.config, "_testy", None)
    if state is None:
        return
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and (report.failed or report.skipped)):
        state.record(item, report)


def pytest_runtest_teardown(item):
    state: "_TestyState | None" = getattr(item.config, "_testy", None)
    if state is None:
        return
    leftover = attachments.pop(item.nodeid)
    if leftover:
        state.add_attachments(item, leftover)


def pytest_collection_finish(session):
    state: "_TestyState | None" = getattr(session.config, "_testy", None)
    if state is None or not session.config.getoption("testy_sync", default=False):
        return
    state.sync(session.items)


def pytest_collection_modifyitems(config, items):
    state: "_TestyState | None" = getattr(config, "_testy", None)
    if state is None or not state.cfg.pytest_targets:
        return
    selected = [
        item for item in items
        if _matches_any_target(item.nodeid, state.cfg.pytest_targets)
    ]
    deselected = [item for item in items if item not in selected]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    log.info(
        "TestY: selected %s/%s collected tests by TESTY_PYTEST_TARGETS",
        len(selected), len(selected) + len(deselected),
    )


def pytest_sessionfinish(session, exitstatus):
    state: "_TestyState | None" = getattr(session.config, "_testy", None)
    if state is None:
        return
    if _is_xdist_worker(session.config):
        session.config.workeroutput["testy_results"] = state.serialized_results()
        return
    state.flush()


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    state: "_TestyState | None" = getattr(node.config, "_testy", None)
    if state is None:
        return
    state.merge_serialized(node.workeroutput.get("testy_results") or [])


class _TestyState:

    def __init__(self, cfg: TestyConfig):
        self.cfg = cfg
        self.results: dict[str, CaseResult] = {}  # result.key -> latest result

    def serialized_results(self) -> list[dict]:
        return [asdict(result) for result in self.results.values()]

    def merge_serialized(self, rows: list[dict]) -> None:
        for row in rows:
            self._absorb(CaseResult(**row))

    def _absorb(self, res: CaseResult) -> None:
        prev = self.results.get(res.key)
        if prev is None:
            self.results[res.key] = res
            return
        combined = (prev.variants or []) + (res.variants or [])
        winner = res if _severity(res.outcome) > _severity(prev.outcome) else prev
        winner.variants = combined
        self.results[res.key] = winner

    def record(self, item, report) -> None:
        outcome = _outcome(report)
        comment = _comment(report)
        duration = int(getattr(report, "duration", 0) or 0)
        files = attachments.pop(item.nodeid)
        steps = step_capture.pop(item.nodeid)
        label = _param_label(item)
        variants = [[label, outcome]] if label else []

        self._absorb(CaseResult(
            outcome=outcome,
            external_id=external_id(item.nodeid, strip_params=self.cfg.strip_params),
            comment=comment, execution_time=duration, nodeid=item.nodeid,
            attachments=files, steps=steps, variants=list(variants),
        ))

    def add_attachments(self, item, files: list[str]) -> None:
        key = f"ext:{external_id(item.nodeid, strip_params=self.cfg.strip_params)}"
        res = self.results.get(key)
        if res is not None:
            res.attachments = list(res.attachments) + list(files)

    def sync(self, items) -> None:
        auto: dict[str, tuple[list, list, str]] = {}
        root_attribute_path = ""
        for item in items:
            ext = external_id(item.nodeid, strip_params=self.cfg.strip_params)
            if ext not in auto:
                suite_path = allure_meta.suite_path(item)
                name = allure_meta.title(item) or item.name
                auto[ext] = (
                    suite_path,
                    allure_meta.suite_attribute_paths(item, suite_path),
                    name,
                )
                root_attribute_path = root_attribute_path or allure_meta.root_attribute_path(item)
        try:
            client = TestyClient(self.cfg)
            client.ensure_roots(need_suite=bool(auto), need_plan=True)
            cases_by_plan: dict[int, set[int]] = {}
            if self.cfg.suite_id is not None and root_attribute_path:
                client.ensure_suite_attributes(
                    self.cfg.suite_id,
                    {self.cfg.automation_key: root_attribute_path},
                )
            for ext, (suite_path, suite_attribute_paths, name) in auto.items():
                leaf_suite = client.ensure_suite_path(
                    suite_path,
                    self.cfg.suite_id,
                    attribute_values=suite_attribute_paths,
                )
                cid = client.ensure_case(ext, name, suite_id=leaf_suite)
                if cid is not None:
                    if self.cfg.plan_id is None:
                        raise ValueError("TestY plan root was not resolved")
                    leaf_plan = client.ensure_plan_path(suite_path, self.cfg.plan_id)
                    cases_by_plan.setdefault(leaf_plan, set()).add(cid)
            for plan_id, plan_case_ids in sorted(cases_by_plan.items()):
                client.sync_plan(sorted(plan_case_ids), plan_id=plan_id)
        except Exception as exc:  # noqa: BLE001
            log.error("TestY: sync failed: %s", exc)

    def flush(self) -> None:
        if not self.results:
            log.info("TestY: no collected test results in this run, nothing to report.")
            return
        try:
            client = TestyClient(self.cfg)
            client.ensure_roots(need_plan=True)
            client.load_statuses()
            client.load_plan_tests()
        except Exception as exc:  # noqa: BLE001 — reporting must never crash CI
            log.error("TestY: could not initialise client: %s", exc)
            return
        ok = 0
        for result in self.results.values():
            try:
                ok += bool(client.report(result))
            except Exception as exc:  # noqa: BLE001
                log.error("TestY: error reporting %s: %s", result.nodeid, exc)
        log.info("TestY: reported %s/%s results to plan %s",
                 ok, len(self.results), self.cfg.plan_id)


_SEVERITY = {"passed": 0, "xfailed": 0, "skipped": 1, "xpassed": 3, "error": 4, "failed": 4}


def _severity(outcome: str) -> int:
    return _SEVERITY.get(outcome, 0)


def _param_label(item) -> str:
    callspec = getattr(item, "callspec", None)
    return getattr(callspec, "id", "") if callspec is not None else ""


def _outcome(report) -> str:
    if hasattr(report, "wasxfail"):
        return "xpassed" if report.passed else "xfailed"
    if report.skipped:
        return "skipped"
    if report.when == "setup" and report.failed:
        return "error"
    return "passed" if report.passed else "failed"


def _comment(report, limit: int = 5000) -> str:
    if report.passed:
        return ""
    if report.skipped and isinstance(getattr(report, "longrepr", None), tuple):
        return str(report.longrepr[2])
    return str(getattr(report, "longrepr", "") or "")[-limit:]


def _matches_any_target(nodeid: str, targets: list[str]) -> bool:
    nodeid = nodeid.replace("\\", "/")
    return any(_matches_target(nodeid, target) for target in targets)


def _matches_target(nodeid: str, target: str) -> bool:
    target = target.replace("\\", "/").rstrip("/")
    return (
        nodeid == target
        or nodeid.startswith(f"{target}::")
        or nodeid.startswith(f"{target}[")
        or nodeid.startswith(f"{target}/")
    )


def _is_xdist_worker(config) -> bool:
    return hasattr(config, "workerinput")


def _xdist_requested(config) -> bool:
    numprocesses = config.getoption("numprocesses", default=None)
    return numprocesses not in (None, 0, "0")
