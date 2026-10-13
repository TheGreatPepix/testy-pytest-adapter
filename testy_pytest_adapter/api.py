from __future__ import annotations

import pytest

MARKER = "testy_case"


def case(*case_ids: int):
    """Link a test to existing (manually written) TestY case(s) by id.

    The case stays the source of truth for its steps: the adapter never rewrites or
    matches them, every step just gets the overall status of the autotest.
    """
    if not case_ids:
        raise ValueError("testy.case() needs at least one case id")
    return getattr(pytest.mark, MARKER)(*(int(case_id) for case_id in case_ids))


def case_ids(item) -> list[int]:
    ids: list[int] = []
    for mark in item.iter_markers(MARKER):
        for case_id in mark.args:
            if int(case_id) not in ids:
                ids.append(int(case_id))
    return ids
