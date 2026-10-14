"""Paged history preserves the full-list contract and session isolation."""

import json

import pytest

from apps.admin_console.database.connection import db_session
from apps.admin_console.database.repositories.step_repository import StepRepository


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / "history.sqlite3"
    repo = StepRepository(path)
    with db_session(path) as db:
        db.execute(
            "INSERT INTO sessions(session_id,initial_goal,start_time,status) VALUES('session','test',1,'completed')"
        )
        db.executemany(
            "INSERT INTO steps(step_id,session_id,step_number,timestamp) VALUES(?,'session',?,?)",
            [(f"step-{n}", n, n * 100) for n in range(1, 5)],
        )
        db.executemany(
            "INSERT INTO traces(trace_id,session_id,step_id,type,name,timestamp,status,payload) VALUES(?,?,?,?,?,?,?,?)",
            [
                ("planning", "session", None, "tool", "plan", 50, "completed", "{}"),
                (
                    "planning-usage",
                    "session",
                    None,
                    "llm_call",
                    "planner",
                    60,
                    "completed",
                    json.dumps(
                        {
                            "token_usage": {
                                "prompt_tokens": 2,
                                "completion_tokens": 1,
                                "total_tokens": 3,
                            }
                        }
                    ),
                ),
                ("direct", "session", "step-1", "tool", "click", 110, "completed", "{}"),
                (
                    "usage",
                    "session",
                    "step-1",
                    "llm_call",
                    "operator",
                    120,
                    "completed",
                    json.dumps(
                        {
                            "token_usage": {
                                "prompt_tokens": 4,
                                "completion_tokens": 2,
                                "total_tokens": 6,
                            }
                        }
                    ),
                ),
                ("orphan-first", "session", None, "tool", "observe", 150, "completed", "{}"),
                ("orphan-next-page", "session", None, "tool", "observe", 350, "completed", "{}"),
                ("foreign", "other-session", "step-1", "tool", "other", 115, "completed", "{}"),
            ],
        )
        db.commit()
    return repo


def test_pages_equal_legacy_full_list_with_one_virtual_planning_step(repository):
    full = repository.get_session_steps("session")
    first = repository.get_session_steps("session", limit=2)
    second = repository.get_session_steps("session", limit=2, offset=2)
    assert [s["step_id"] for s in first] == ["pre-planning", "step-1", "step-2"]
    assert [s["step_id"] for s in second] == ["step-3", "step-4"]
    assert first + second == full
    assert repository.get_session_steps("session", limit=2, offset=4) == []
    assert repository.get_session_steps("missing", limit=2) == []


def test_orphan_tools_and_usage_stay_with_their_actual_page(repository):
    first = repository.get_session_steps("session", limit=2)
    second = repository.get_session_steps("session", limit=2, offset=2)
    assert first[0]["total_tokens"] == 3
    assert first[1]["total_tokens"] == 6
    assert {t["trace_id"] for t in first[1]["generic_tools"]} == {"direct", "orphan-first"}
    assert {t["trace_id"] for t in second[0]["generic_tools"]} == {"orphan-next-page"}
    assert all(t["trace_id"] != "foreign" for s in first + second for t in s["generic_tools"])


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"limit": 0}, {"limit": -1}])
def test_invalid_pagination_is_rejected(repository, kwargs):
    with pytest.raises(ValueError, match="pagination"):
        repository.get_session_steps("session", **kwargs)


def test_legacy_screenshot_blobs_are_not_included_in_history(repository):
    with db_session(repository.db_path) as db:
        db.execute("ALTER TABLE steps ADD COLUMN pre_screenshot_bytes BLOB")
        db.execute("ALTER TABLE steps ADD COLUMN post_screenshot_bytes BLOB")
        db.execute(
            "UPDATE steps SET pre_screenshot_bytes=?,post_screenshot_bytes=?",
            (b"large image", b"large image"),
        )
        db.commit()
    result = repository.get_session_steps("session", limit=2)
    assert all(
        s["pre_screenshot_bytes"] is None and s["post_screenshot_bytes"] is None
        for s in result
        if s["step_id"] != "pre-planning"
    )
