from __future__ import annotations

from dataclasses import dataclass, field

OUTCOME_TO_STATUS = {
    "passed": "Passed",
    "failed": "Failed",
    "error": "Broken",  # failed in setup / fixture
    "skipped": "Skipped",
    "xfailed": "Passed",
    "xpassed": "Failed",
}


@dataclass
class CaseResult:
    outcome: str
    external_id: str = ""
    comment: str = ""
    execution_time: int = 0
    nodeid: str = ""
    attachments: list[str] = field(default_factory=list)
    steps: list = field(default_factory=list)
    variants: list = field(default_factory=list)
    case_id: int | None = None  # set when linked explicitly via @testy.case(id)
    external_ids: list = field(default_factory=list)

    @property
    def key(self) -> str:
        if self.case_id is not None:
            return f"case:{self.case_id}"
        return f"ext:{self.external_id}"

    @property
    def status_name(self) -> str:
        return OUTCOME_TO_STATUS.get(self.outcome, "Untested")
