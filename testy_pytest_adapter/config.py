from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .models import OUTCOME_TO_STATUS


@dataclass
class TestyConfig:
    __test__ = False
    url: str
    token: str
    project_id: int
    plan_id: int | None
    automation_key: str = "automation_id"
    strip_params: bool = True
    suite_id: int | None = None
    root_name: str = ""
    suite_name: str = ""
    plan_name: str = ""
    override_cases: bool = True
    attach_mode: str = "failure"
    attach_bytes: bool = True
    ci_pipeline_url: str = ""
    ci_job_url: str = ""
    pytest_targets: list[str] = field(default_factory=list)
    status_names: dict = field(default_factory=dict)
    auth_scheme: str = "Token"
    verify_ssl: bool = True
    timeout: int = 30
    workers: int = 8
    sync: bool = False
    test_map: dict = field(default_factory=dict)
    targets_url: str = ""
    run_id: str = ""

    @property
    def targets_endpoint(self) -> str:
        if self.targets_url:
            return self.targets_url
        if self.run_id:
            return f"{self.url.rstrip('/')}/plugins/gitlab-runner/api/runs/{self.run_id}/targets/"
        return ""

    @property
    def api(self) -> str:
        return self.url.rstrip("/") + "/api/v2"

    def status_name_for(self, outcome: str) -> str:
        canonical = OUTCOME_TO_STATUS.get(outcome, "Untested")
        return self.status_names.get(canonical.lower(), canonical)

    @classmethod
    def from_pytest(cls, config) -> "TestyConfig | None":

        def ini(name: str, default=None):
            val = config.getini(name)
            return default if val in (None, "") else val

        def opt(name: str | None, env: str, ini_name: str | None = None, default=None):
            if name:
                val = config.getoption(name, default=None)
                if val is not None:
                    return val
            val = os.environ.get(env)
            if val not in (None, ""):
                return val
            return ini(ini_name, default) if ini_name else default

        def bool_opt(cli_name: str | None, env: str, ini_name: str, default=False) -> bool:
            if cli_name and bool(config.getoption(cli_name, default=False)):
                return True
            val = os.environ.get(env)
            if val not in (None, ""):
                return _truthy(val)
            val = config.getini(ini_name)
            if isinstance(val, bool):
                return val
            if val not in (None, ""):
                return _truthy(val)
            return default

        enabled = bool_opt("testy", "TESTY_ENABLED", "testy_enabled")
        if not enabled:
            return None

        sync = (
            bool(config.getoption("testy_sync", default=False))
            or _truthy(os.environ.get("TESTY_SYNC"))
            or os.environ.get("TESTY_MODE", "").strip().lower() == "sync"
        )
        url = opt("testy_url", "TESTY_URL", "testy_url")
        token = opt("testy_token", "TESTY_TOKEN")
        project_id = opt("testy_project", "TESTY_PROJECT_ID", "testy_project_id")
        plan_id = opt("testy_plan", "TESTY_PLAN_ID", "testy_plan_id")
        root_name = opt("testy_root_name", "TESTY_ROOT_NAME", "testy_root_name", "")
        suite_name = opt("testy_suite_name", "TESTY_SUITE_NAME", "testy_suite_name", root_name)
        plan_name = opt("testy_plan_name", "TESTY_PLAN_NAME", "testy_plan_name", root_name)
        test_map = _test_map_from_env(os.environ.get("TESTY_TEST_MAP"))
        targets_url = str(os.environ.get("TESTY_TARGETS_URL") or "").strip()
        run_id = str(os.environ.get("TESTY_RUN_ID") or "").strip()
        remote_selection = bool(targets_url or run_id)

        missing = [n for n, v in [
            ("TESTY_URL", url), ("TESTY_TOKEN", token),
            ("TESTY_PROJECT_ID", project_id),
        ] if not v]
        if not plan_id and not plan_name and not test_map and not remote_selection:
            missing.append(
                "TESTY_PLAN_ID or TESTY_PLAN_NAME/TESTY_ROOT_NAME or "
                "TESTY_TEST_MAP or TESTY_RUN_ID/TESTY_TARGETS_URL"
            )
        if missing:
            raise ValueError(
                "TestY reporting enabled but required settings are missing: "
                + ", ".join(missing)
            )

        status_names = {}
        for canonical in {v for v in OUTCOME_TO_STATUS.values()} | {"Untested"}:
            ini_name = f"testy_status_{canonical.lower()}"
            override = os.environ.get(f"TESTY_STATUS_{canonical.upper()}") or ini(ini_name)
            if override:
                status_names[canonical.lower()] = override

        return cls(
            url=str(url),
            token=str(token),
            project_id=int(project_id),
            plan_id=_int_or_none(plan_id),
            strip_params=not bool_opt(None, "TESTY_KEEP_PARAMS", "testy_keep_params"),
            auth_scheme=opt(None, "TESTY_AUTH_SCHEME", "testy_auth_scheme", "Token"),
            status_names=status_names,
            suite_id=_int_or_none(opt("testy_suite", "TESTY_SUITE_ID", "testy_suite_id")),
            root_name=str(root_name or ""),
            suite_name=str(suite_name or ""),
            plan_name=str(plan_name or ""),
            override_cases=bool_opt(None, "TESTY_OVERRIDE_CASES", "testy_override_cases",
                                    default=True),
            attach_mode=str(opt(None, "TESTY_ATTACH", "testy_attach", "failure")).lower(),
            attach_bytes=bool_opt(None, "TESTY_ATTACH_BYTES", "testy_attach_bytes", default=True),
            ci_pipeline_url=os.environ.get("CI_PIPELINE_URL", ""),
            ci_job_url=os.environ.get("CI_JOB_URL", ""),
            pytest_targets=_targets_from_env(os.environ.get("TESTY_PYTEST_TARGETS")),
            verify_ssl=not bool_opt(None, "TESTY_INSECURE", "testy_insecure"),
            workers=int(os.environ.get("TESTY_WORKERS") or ini("testy_workers") or 8),
            sync=sync,
            test_map=test_map,
            targets_url=targets_url,
            run_id=run_id,
        )

    def should_attach(self, outcome: str) -> bool:
        if self.attach_mode == "always":
            return True
        if self.attach_mode == "never":
            return False
        return outcome not in ("passed", "skipped", "xfailed")


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_or_none(value):
    return int(value) if value not in (None, "") else None


def normalize_targets(data) -> list[str]:
    """Validate/normalize a list of nodeid targets (from env JSON or a remote API)."""
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("targets must be a JSON array of strings")
    return [item.strip().replace("\\", "/") for item in data if item.strip()]


def normalize_test_map(data) -> dict[str, list[int]]:
    """Validate/normalize a {external_id: [test_id, ...]} map (from env JSON or API)."""
    if not isinstance(data, dict):
        raise ValueError("test map must be a JSON object of {external_id: [test_id, ...]}")
    result: dict[str, list[int]] = {}
    for key, raw_ids in data.items():
        ids = raw_ids if isinstance(raw_ids, list) else [raw_ids]
        normalized = str(key).strip().replace("\\", "/")
        if not normalized:
            continue
        result[normalized] = [int(test_id) for test_id in ids]
    return result


def _targets_from_env(value: str | None) -> list[str]:
    if value in (None, ""):
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("TESTY_PYTEST_TARGETS must be a JSON array of strings") from exc
    return normalize_targets(data)


def _test_map_from_env(value: str | None) -> dict[str, list[int]]:
    if value in (None, ""):
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "TESTY_TEST_MAP must be a JSON object of {external_id: [test_id, ...]}",
        ) from exc
    return normalize_test_map(data)
