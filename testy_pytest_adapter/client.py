from __future__ import annotations

import json
import logging
import mimetypes
import os
import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .config import TestyConfig
from .models import CaseResult

log = logging.getLogger("testy_pytest")
_FAILURE_OUTCOMES = {"failed", "error", "xpassed"}

def _flatten_steps(tree: list) -> list[dict]:
    steps: list[dict] = []

    def walk(nodes: list) -> None:
        for node in nodes:
            title = node["title"]
            steps.append({
                "name": title[:255],
                "scenario": title,
                "expected": "",
                "sort_order": len(steps) + 1,
            })
            if node.get("children"):
                walk(node["children"])

    walk(tree)
    return steps


def _flatten_step_statuses(tree: list) -> list[str]:
    statuses: list[str] = []

    def walk(nodes: list) -> None:
        for node in nodes:
            statuses.append(str(node.get("status") or "passed"))
            if node.get("children"):
                walk(node["children"])

    walk(tree)
    return statuses


def _runtime_step_statuses(result: CaseResult) -> list[str]:
    statuses = _flatten_step_statuses(result.steps)
    if (
        result.outcome in {"failed", "error", "xpassed"}
        and statuses
        and all(status.lower() != "failed" for status in statuses)
    ):
        statuses[-1] = "failed"
    return statuses


def _steps_signature(steps: list[dict]) -> list[tuple[str, str, str]]:
    ordered = sorted(
        enumerate(steps),
        key=lambda pair: int(pair[1].get("sort_order") or pair[0] + 1),
    )
    return [
        (
            str(step.get("name") or ""),
            str(step.get("scenario") or ""),
            str(step.get("expected") or ""),
        )
        for _, step in ordered
    ]


def _ordered_steps(steps: list[dict]) -> list[dict]:
    return [
        step for _, step in sorted(
            enumerate(steps),
            key=lambda pair: int(pair[1].get("sort_order") or pair[0] + 1),
        )
    ]


def _step_results_payload(
    case_steps: list[dict],
    runtime_statuses: list[str],
    passed_status_id: int,
    failed_status_id: int,
) -> list[dict]:
    payload: list[dict] = []
    for case_step, runtime_status in zip(case_steps, runtime_statuses):
        step_id = case_step.get("id")
        if step_id is None:
            continue
        status_id = failed_status_id if runtime_status.lower() == "failed" else passed_status_id
        payload.append({"step": int(step_id), "status": int(status_id)})
    return payload


def _variants_summary(result: CaseResult) -> str:
    if len(result.variants) <= 1:
        return ""
    failed = [str(lbl) for lbl, oc in result.variants if oc in _FAILURE_OUTCOMES]
    total = len(result.variants)
    if failed:
        return f"Параметризация: {total} вариант(ов), упали: {', '.join(failed)}"
    return f"Параметризация: {total} вариант(ов), все пройдены"


def _compose_comment(result: CaseResult, limit: int = 5000) -> str:
    parts: list[str] = []
    summary = _variants_summary(result)
    if summary:
        parts.append(summary)
    if result.comment:
        prefix, suffix = "```text\n", "\n```"
        budget = max(limit - len(prefix) - len(suffix) - len("\n\n".join(parts)) - 2, 0)
        body = result.comment.strip()[:budget]
        parts.append(f"{prefix}{body}{suffix}")
    return "\n\n".join(parts)


def _inline_url(link: str) -> str:
    if not link:
        return link
    separator = "&" if "?" in link else "?"
    return f"{link}{separator}view=true"


def _comment_with_images(comment: str, images: list[dict]) -> str:
    if not images:
        return comment
    gallery = "\n\n".join(
        f"![{image['name']}]({_inline_url(image['link'])})" for image in images
    )
    return f"{comment}\n\n{gallery}" if comment else gallery


class TestyClient:
    __test__ = False  # not a pytest test class despite the Test* name

    def __init__(self, config: TestyConfig):
        self.cfg = config
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"{config.auth_scheme} {config.token}"
        self._status_ids: dict[str, int] = {}
        self._test_by_case: dict[int, int] = {}
        self._case_by_external: dict[str, int | None] = {}
        self._suite_cache: dict[tuple, int] = {}
        self._suite_attrs_cache: set[tuple[int, str]] = set()
        self._plan_cache: dict[tuple, int] = {}


    def _get(self, path: str, **params):
        resp = self.session.get(
            f"{self.cfg.api}/{path.lstrip('/')}",
            params=params,
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_ssl,
        )
        resp.raise_for_status()
        return resp.json()

    def _paged(self, path: str, **params):
        data = self._get(path, **params)
        while True:
            results = data["results"] if isinstance(data, dict) else data
            yield from results
            nxt = data.get("links", {}).get("next") if isinstance(data, dict) else None
            nxt = nxt or (data.get("next") if isinstance(data, dict) else None)
            if not nxt:
                return
            data = self._get(nxt.split("/api/v2/", 1)[-1])

    def load_statuses(self) -> None:
        data = self._get("statuses/", project=self.cfg.project_id)
        results = data["results"] if isinstance(data, dict) else data
        for status in results:
            self._status_ids[status["name"].lower()] = status["id"]

    def load_plan_tests(self) -> None:
        self.ensure_roots(need_plan=True)
        path = (
            f"testplans/{self.cfg.plan_id}/tests/"
            f"?project={self.cfg.project_id}&show_descendants=true"
        )
        for test in self._paged(path):
            self._test_by_case[int(test["case"])] = int(test["id"])

    def resolve_case_by_external_id(self, external_id: str) -> int | None:
        if external_id in self._case_by_external:
            return self._case_by_external[external_id]
        attr_filter = json.dumps({self.cfg.automation_key: external_id})
        data = self._get(
            "cases/", project=self.cfg.project_id, case_attributes=attr_filter,
        )
        results = data["results"] if isinstance(data, dict) else data
        case_id = int(results[0]["id"]) if results else None
        if case_id is None:
            log.warning("TestY: no case with %s=%r in project %s",
                        self.cfg.automation_key, external_id, self.cfg.project_id)
        self._case_by_external[external_id] = case_id
        return case_id


    def ensure_roots(self, need_suite: bool = False, need_plan: bool = False) -> None:
        """Resolve configured root suite/plan ids, creating them by name if needed."""
        if need_suite and self.cfg.suite_id is None:
            suite_name = self.cfg.suite_name or self.cfg.root_name
            if not suite_name:
                raise ValueError("TestY suite root is missing: set TESTY_SUITE_ID or TESTY_ROOT_NAME")
            self.cfg.suite_id = self._ensure_child_suite(suite_name, None)
            if self.cfg.suite_id is None:
                raise RuntimeError(f"TestY: failed to resolve root suite {suite_name!r}")
        if need_plan and self.cfg.plan_id is None:
            plan_name = self.cfg.plan_name or self.cfg.root_name
            if not plan_name:
                raise ValueError("TestY plan root is missing: set TESTY_PLAN_ID or TESTY_ROOT_NAME")
            self.cfg.plan_id = self._ensure_root_plan(plan_name)

    def _ensure_root_plan(self, name: str) -> int:
        key = (None, name)
        if key in self._plan_cache:
            return self._plan_cache[key]
        for plan in self._paged("testplans/", project=self.cfg.project_id):
            if plan.get("name") != name:
                continue
            parent = plan.get("parent") if isinstance(plan, dict) else None
            if "parent" not in plan or parent in (None, "", "null"):
                plan_id = int(plan["id"])
                self._plan_cache[key] = plan_id
                return plan_id
        payload = self._plan_payload(name)
        resp = self.session.post(f"{self.cfg.api}/testplans/", json=payload,
                                 timeout=self.cfg.timeout, verify=self.cfg.verify_ssl)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"TestY: failed to create root plan {name!r}: "
                f"{resp.status_code} {resp.text[:300]}"
            )
        body = resp.json()
        obj = body[0] if isinstance(body, list) else body
        plan_id = int(obj["id"])
        self._plan_cache[key] = plan_id
        log.info("TestY: created root plan %s %r", plan_id, name)
        return plan_id

    def ensure_suite_attributes(self, suite_id: int, attributes: dict) -> None:
        self._ensure_suite_attributes(suite_id, attributes)

    def ensure_suite_path(
        self,
        path: list[str],
        root_id: int | None,
        attribute_values: list[str] | None = None,
    ) -> int | None:
        parent = root_id
        for index, name in enumerate(path):
            attributes = None
            if attribute_values and index < len(attribute_values):
                attributes = {self.cfg.automation_key: attribute_values[index]}
            parent = self._ensure_child_suite(name, parent, attributes=attributes)
            if parent is None:
                return root_id
        return parent

    def _ensure_child_suite(
        self,
        name: str,
        parent_id: int | None,
        attributes: dict | None = None,
    ) -> int | None:
        key = (parent_id, name)
        if key in self._suite_cache:
            suite_id = self._suite_cache[key]
            self._ensure_suite_attributes(suite_id, attributes)
            return suite_id
        for suite in self._paged("suites/", project=self.cfg.project_id,
                                 parent=parent_id if parent_id else "null"):
            if suite.get("name") == name:
                suite_id = int(suite["id"])
                self._suite_cache[key] = suite_id
                self._ensure_suite_attributes(suite_id, attributes, suite.get("attributes"))
                return suite_id
        payload = {"name": name[:255], "project": self.cfg.project_id}
        if parent_id:
            payload["parent"] = parent_id
        if attributes:
            payload["attributes"] = attributes
        resp = self.session.post(f"{self.cfg.api}/suites/", json=payload,
                                 timeout=self.cfg.timeout, verify=self.cfg.verify_ssl)
        if resp.status_code >= 400:
            log.error("TestY: failed to create suite %r under %s: %s %s",
                      name, parent_id, resp.status_code, resp.text[:300])
            return None
        body = resp.json()
        suite_id = int((body[0] if isinstance(body, list) else body)["id"])
        self._suite_cache[key] = suite_id
        log.info("TestY: created suite %s %r under %s", suite_id, name, parent_id)
        return suite_id

    def _ensure_suite_attributes(
        self,
        suite_id: int | None,
        attributes: dict | None,
        current: dict | None = None,
    ) -> None:
        desired = {key: value for key, value in (attributes or {}).items() if value}
        if not suite_id or not desired:
            return
        cache_key = (int(suite_id), json.dumps(desired, sort_keys=True, ensure_ascii=False))
        if cache_key in self._suite_attrs_cache:
            return
        if current is None:
            try:
                suite = self._get(f"suites/{suite_id}/")
                current = suite.get("attributes") or {}
            except requests.HTTPError as exc:
                log.warning("TestY: cannot read suite %s for attributes: %s", suite_id, exc)
                return
        merged = dict(current or {})
        changed = False
        for key, value in desired.items():
            if merged.get(key) != value:
                merged[key] = value
                changed = True
        if not changed:
            self._suite_attrs_cache.add(cache_key)
            return
        resp = self.session.patch(
            f"{self.cfg.api}/suites/{suite_id}/",
            json={"attributes": merged},
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_ssl,
        )
        if resp.status_code >= 400:
            log.error("TestY: failed to update suite %s attributes: %s %s",
                      suite_id, resp.status_code, resp.text[:300])
            return
        self._suite_attrs_cache.add(cache_key)
        log.info("TestY: updated suite %s attributes with %s", suite_id, sorted(desired))

    def ensure_case(self, external_id: str, name: str, suite_id: int | None = None) -> int | None:
        case_id = self.resolve_case_by_external_id(external_id)
        if case_id is not None:
            return case_id
        target_suite = suite_id or self.cfg.suite_id
        if not target_suite:
            log.error("TestY: cannot auto-create case for %r — no suite (set --testy-suite)",
                      external_id)
            return None
        created = self.session.post(
            f"{self.cfg.api}/cases/",
            json={
                "name": name[:255], "project": self.cfg.project_id,
                "suite": target_suite, "scenario": "Created by testy-pytest sync",
                "attributes": {self.cfg.automation_key: external_id},
            },
            timeout=self.cfg.timeout, verify=self.cfg.verify_ssl,
        )
        if created.status_code >= 400:
            log.error("TestY: failed to create case for %r: %s %s",
                      external_id, created.status_code, created.text[:300])
            return None
        case_id = int(created.json()["id"])
        self._case_by_external[external_id] = case_id
        log.info("TestY: created case %s for %r", case_id, external_id)
        return case_id

    def sync_plan(self, case_ids: list[int], plan_id: int | None = None) -> None:
        self.ensure_roots(need_plan=True)
        target_plan = plan_id or self.cfg.plan_id
        if target_plan is None:
            raise ValueError("TestY plan root is missing")
        existing = self._load_tests_for_single_plan(target_plan)
        to_add = [cid for cid in case_ids if cid not in existing]
        added = 0
        for case_id in to_add:
            resp = self.session.post(
                f"{self.cfg.api}/tests/",
                json={"project": self.cfg.project_id, "case": case_id,
                      "plan": target_plan},
                timeout=self.cfg.timeout, verify=self.cfg.verify_ssl,
            )
            if resp.status_code >= 400:
                log.error("TestY: failed to add case %s to plan %s: %s %s",
                          case_id, target_plan, resp.status_code, resp.text[:300])
                continue
            body = resp.json()
            obj = body[0] if isinstance(body, list) else body
            self._test_by_case[case_id] = int(obj["id"])
            added += 1
        if added:
            log.info("TestY: added %s case(s) to plan %s (%s already present)",
                     added, target_plan, len(existing))

    def _load_tests_for_single_plan(self, plan_id: int) -> dict[int, int]:
        tests: dict[int, int] = {}
        for test in self._paged(f"tests/?plan={plan_id}&project={self.cfg.project_id}"):
            tests[int(test["case"])] = int(test["id"])
        return tests

    def ensure_plan_path(self, path: list[str], root_id: int) -> int:
        parent = root_id
        for name in path:
            parent = self._ensure_child_plan(name, parent)
        return parent

    def _ensure_child_plan(self, name: str, parent_id: int) -> int:
        key = (parent_id, name)
        if key in self._plan_cache:
            return self._plan_cache[key]
        for plan in self._paged("testplans/", project=self.cfg.project_id, parent=parent_id):
            if plan.get("name") == name:
                self._plan_cache[key] = int(plan["id"])
                return int(plan["id"])
        payload = self._plan_payload(name, parent_id)
        resp = self.session.post(f"{self.cfg.api}/testplans/", json=payload,
                                 timeout=self.cfg.timeout, verify=self.cfg.verify_ssl)
        if resp.status_code >= 400:
            log.error("TestY: failed to create plan %r under %s: %s %s",
                      name, parent_id, resp.status_code, resp.text[:300])
            return parent_id
        body = resp.json()
        obj = body[0] if isinstance(body, list) else body
        plan_id = int(obj["id"])
        self._plan_cache[key] = plan_id
        log.info("TestY: created plan %s %r under %s", plan_id, name, parent_id)
        return plan_id

    def _plan_payload(self, name: str, parent_id: int | None = None) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        payload = {
            "name": name[:255],
            "project": self.cfg.project_id,
            "started_at": now.isoformat(),
            "due_date": (now + dt.timedelta(days=7)).isoformat(),
            "test_cases": [],
        }
        if parent_id:
            payload["parent"] = parent_id
        return payload


    def report(self, result: CaseResult) -> bool:
        if not result.external_id:
            return False
        case_id = self.resolve_case_by_external_id(result.external_id)
        if case_id is None:
            return False

        test_id = self._test_by_case.get(case_id)
        if test_id is None:
            log.warning(
                "TestY: case %s is not part of plan %s — skipping result for %s",
                case_id, self.cfg.plan_id, result.nodeid,
            )
            return False

        status_name = self.cfg.status_name_for(result.outcome)
        status_id = self._status_ids.get(status_name.lower())
        if status_id is None:
            log.warning("TestY: status %r not found in project %s (available: %s)",
                        status_name, self.cfg.project_id, sorted(self._status_ids))
            return False

        attributes = {"source": "gitlab-ci", "nodeid": result.nodeid}
        if self.cfg.ci_pipeline_url:
            attributes["ci_pipeline_url"] = self.cfg.ci_pipeline_url
        if self.cfg.ci_job_url:
            attributes["ci_job_url"] = self.cfg.ci_job_url

        attachment_ids: list[int] = []
        image_attachments: list[dict] = []
        if result.attachments and self.cfg.should_attach(result.outcome):
            uploaded = self._upload_attachments(result.attachments)
            attachment_ids = [item["id"] for item in uploaded]
            image_attachments = [item for item in uploaded if item["is_image"] and item["link"]]

        steps_results: list[dict] = []
        if result.steps:
            case_steps = self._sync_case_steps(case_id, result.steps)
            passed_status_id = self._status_ids.get(self.cfg.status_name_for("passed").lower())
            failed_status_id = self._status_ids.get(self.cfg.status_name_for("failed").lower())
            if case_steps and passed_status_id is not None and failed_status_id is not None:
                steps_results = _step_results_payload(
                    case_steps,
                    _runtime_step_statuses(result),
                    passed_status_id,
                    failed_status_id,
                )

        payload = {
            "test": test_id,
            "status": status_id,
            "project": self.cfg.project_id,
            "comment": _comment_with_images(_compose_comment(result), image_attachments),
            "execution_time": result.execution_time,
            "attributes": attributes,
        }
        if attachment_ids:
            payload["attachments"] = attachment_ids
        if steps_results:
            payload["steps_results"] = steps_results
        resp = self.session.post(
            f"{self.cfg.api}/results/",
            json=payload,
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_ssl,
        )
        if resp.status_code >= 400:
            log.error("TestY: failed to post result for %s: %s %s",
                      result.nodeid, resp.status_code, resp.text[:500])
            return False
        return True

    def _clone_for_thread(self) -> "TestyClient":
        clone = object.__new__(TestyClient)
        clone.cfg = self.cfg
        clone.session = requests.Session()
        clone.session.headers["Authorization"] = f"{self.cfg.auth_scheme} {self.cfg.token}"
        clone._status_ids = self._status_ids
        clone._test_by_case = self._test_by_case
        clone._case_by_external = dict(self._case_by_external)
        clone._suite_cache = self._suite_cache
        clone._suite_attrs_cache = self._suite_attrs_cache
        clone._plan_cache = self._plan_cache
        return clone

    def report_all(self, results: list[CaseResult], max_workers: int = 8) -> int:
        for result in results:
            if result.external_id:
                self.resolve_case_by_external_id(result.external_id)

        _local: threading.local = threading.local()

        def _get_worker() -> "TestyClient":
            if not hasattr(_local, "client"):
                _local.client = self._clone_for_thread()
            return _local.client

        def _report_one(result: CaseResult) -> bool:
            return _get_worker().report(result)

        ok = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_report_one, r): r for r in results}
            for future in as_completed(futures):
                r = futures[future]
                try:
                    ok += bool(future.result())
                except Exception as exc:  # noqa: BLE001
                    log.error("TestY: error reporting %s: %s", r.nodeid, exc)
        return ok

    def _sync_case_steps(self, case_id: int, steps_tree: list) -> list[dict]:
        try:
            case = self._get(f"cases/{case_id}/")
        except requests.HTTPError as exc:
            log.warning("TestY: cannot read case %s for steps: %s", case_id, exc)
            return []
        desired_steps = _flatten_steps(steps_tree)
        current_steps = case.get("steps") or []
        if current_steps and not self.cfg.override_cases:
            return _ordered_steps(current_steps)  # frozen: never overwrite
        if _steps_signature(current_steps) == _steps_signature(desired_steps):
            return _ordered_steps(current_steps)
        suite = case.get("suite")
        suite_id = suite["id"] if isinstance(suite, dict) else suite
        payload = {
            "name": case["name"], "project": self.cfg.project_id, "suite": suite_id,
            "setup": case.get("setup", ""), "scenario": case.get("scenario", ""),
            "expected": case.get("expected", ""), "teardown": case.get("teardown", ""),
            "description": case.get("description", ""),
            "attributes": case.get("attributes", {}),
            "is_steps": True, "steps": desired_steps,
        }
        resp = self.session.put(f"{self.cfg.api}/cases/{case_id}/", json=payload,
                                timeout=self.cfg.timeout, verify=self.cfg.verify_ssl)
        if resp.status_code >= 400:
            log.error("TestY: failed to write steps to case %s: %s %s",
                      case_id, resp.status_code, resp.text[:300])
            return []
        action = "updated" if current_steps else "wrote"
        log.info("TestY: %s %s step(s) into case %s",
                 action, len(payload["steps"]), case_id)
        try:
            updated_case = self._get(f"cases/{case_id}/")
        except requests.HTTPError as exc:
            log.warning("TestY: cannot re-read case %s after writing steps: %s", case_id, exc)
            return []
        return _ordered_steps(updated_case.get("steps") or [])

    def _upload_attachments(self, paths: list[str]) -> list[dict]:
        uploaded: list[dict] = []
        for path in paths:
            if not os.path.isfile(path):
                log.warning("TestY: attachment not found, skipping: %s", path)
                continue
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                resp = self.session.post(
                    f"{self.cfg.api}/attachments/",
                    data={"project": self.cfg.project_id},
                    files={"file": (os.path.basename(path), fh, content_type)},
                    timeout=self.cfg.timeout,
                    verify=self.cfg.verify_ssl,
                )
            if resp.status_code >= 400:
                log.error("TestY: failed to upload %s: %s %s",
                          path, resp.status_code, resp.text[:300])
                continue
            body = resp.json()
            obj = body[0] if isinstance(body, list) else body
            uploaded.append({
                "id": int(obj["id"]),
                "link": str(obj.get("link") or ""),
                "is_image": content_type.startswith("image/"),
                "name": os.path.splitext(os.path.basename(path))[0],
            })
        return uploaded
