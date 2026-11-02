"""Bound synthetic Pro no-decision turns using the real Operator lifecycle and isolated model output."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError
import pytest

from artemis.agents.operator.operator import OperatorNode, OperatorNoActionLimitError
from artemis.config.agent import (
    AgentGlobalConfig,
    ExecutionConfig,
    FlashProfileConfig,
    MemoryTranscriptConfig,
)
from artemis.context import ArtemisContext
from artemis.graph.state import State


@pytest.fixture
def operator(monkeypatch):
    """Return a task-scoped Operator/state with synthetic observations; patches expire after each test."""
    # Config construction uses built-in defaults only; no credential validation is required.
    monkeypatch.setattr("artemis.config.load_agent_config", lambda: AgentGlobalConfig())
    # Context exposes the task lifecycle but owns no real device, trace writer or model.
    ctx = MagicMock(spec=ArtemisContext)
    ctx.execution_setup = None
    ctx.data_engine = None
    ctx.trace_id = "fixture-progress"
    ctx.actuator = None
    ctx.transcript_ledger = None
    # Each test receives a fresh guard state on the existing Operator instance.
    node = OperatorNode(ctx, transcript_config=MemoryTranscriptConfig(enabled=False))
    node._get_history_and_plan = lambda: ([], "- [/] Fixture task")
    node._read_task_plan_text = lambda: "- [/] Fixture task"
    node._build_prompt = AsyncMock(
        side_effect=lambda *a, **kw: [HumanMessage(content="Fixture observation")]
    )
    node._stage_transcript_turn = MagicMock()
    # No background ADB work or model request is permitted by the fixture.
    registry = SimpleNamespace(active_task_summaries=lambda: [], pop_unnotified_finished=lambda: [])
    monkeypatch.setattr(
        "artemis.agents.operator.operator.get_adb_task_registry", lambda ctx: registry
    )
    # Tool binding is retained while completion itself is controlled by each test.
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    monkeypatch.setattr("artemis.agents.operator.operator.get_llm", lambda **kw: llm)
    # Real graph state receives an empty, valid synthetic viewport.
    state = State.initial("Fixture task")
    state.latest_ui_hierarchy = []
    state.operator_raw_data = {
        "screenshot_b64": "AA==",
        "xml_hierarchy": [],
        "width": 100,
        "height": 200,
    }
    return node, state


@pytest.mark.asyncio
@pytest.mark.parametrize("helper_only", [False, True])
async def test_actual_operator_stops_after_four_no_decision_turns(
    operator, monkeypatch, helper_only
):
    """Repeated plain replies or helper-only tool budgets must fail across graph turns, retaining feedback."""
    # Fixture node/state are task-scoped; helper work must not reset the cross-turn budget.
    node, state = operator
    # Real ordinary replies traverse the actual inner model loop and are staged in history.
    response = AIMessage(content="Fixture cannot proceed.")
    monkeypatch.setattr(
        "artemis.agents.operator.operator.acomplete", AsyncMock(return_value=response)
    )
    if helper_only:
        node._invoke_llm_loop = AsyncMock(return_value=(None, None, None, True))
    for _ in range(3):
        assert (await node(state))["structured_decisions"] is None
    with pytest.raises(OperatorNoActionLimitError, match="operator_no_action_limit"):
        await node(state)
    assert node._no_action_turns == 4
    if not helper_only:
        assert response in node._stage_transcript_turn.call_args.args[0]


def test_progress_reset_and_continuous_completion_rules(operator):
    """Actions/completed plans reset the guard; continuous completion requires explicit user stop."""
    # Reuse this task's Operator state to verify resets without new model requests.
    node, _ = operator
    node._record_progress(None, "- [/] Fixture task")
    node._record_progress([{"action": "wait", "seconds": 1}], "- [/] Fixture task")
    assert node._no_action_turns == 0
    node._record_progress(None, "- [x] Fixture task")
    assert node._no_action_turns == 0
    node._record_progress(None, "- [x] [Loop:continuous] Fixture monitor")
    assert node._no_action_turns == 1
    node._record_progress(None, "- [x] [Loop:continuous] Fixture monitor", user_stopped=True)
    assert node._no_action_turns == 0


@pytest.mark.asyncio
async def test_cancelled_model_await_does_not_consume_a_completed_turn(operator, monkeypatch):
    """Cancelling an in-flight completion must leave the no-decision counter unchanged and release the coroutine."""
    # Pending event prevents the fake model from returning a completed response.
    pending = asyncio.Event()
    # Existing task-scoped Operator is the sole owner of progress accounting.
    node, state = operator

    async def wait_for_response(*args, **kwargs):
        """Wait until cancelled; unused model arguments do not cause any device/network effect."""
        await pending.wait()
        return AIMessage(content="")

    monkeypatch.setattr("artemis.agents.operator.operator.acomplete", wait_for_response)
    # Cancellation reaches the real Operator await, before its progress-accounting boundary.
    task = asyncio.create_task(node(state))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert node._no_action_turns == 0


@pytest.mark.parametrize("value", [0, -1, 21])
def test_pro_limit_rejects_unbounded_or_invalid_values(value):
    """Pro's cross-turn guard accepts only 1..20; total task duration/steps remain separate settings."""
    with pytest.raises(ValidationError):
        ExecutionConfig(max_no_action_turns=value)


def test_flash_and_pro_defaults_are_separate():
    """Defaults bound quiet streaks while retaining Flash's unlimited total-turn setting and explicit guard opt-out."""
    assert FlashProfileConfig().max_turns == 0
    assert FlashProfileConfig().max_silent_turns == 5
    assert FlashProfileConfig(max_silent_turns=0).max_silent_turns == 0
    assert ExecutionConfig().max_no_action_turns == 4


def test_operator_uses_the_configured_no_decision_limit(operator, monkeypatch):
    """A custom Pro limit must reach the actual task-scoped node rather than remaining a dead config field."""
    # Set only this task's execution limit; leave model/device configuration untouched.
    config = AgentGlobalConfig.model_validate({"pro": {"execution": {"max_no_action_turns": 2}}})
    monkeypatch.setattr("artemis.config.load_agent_config", lambda: config)
    # Construction exercises the real config-to-Operator wiring.
    node = OperatorNode(operator[0].ctx, transcript_config=MemoryTranscriptConfig(enabled=False))
    node._record_progress(None, "- [/] Fixture task")
    with pytest.raises(OperatorNoActionLimitError):
        node._record_progress(None, "- [/] Fixture task")


@pytest.mark.parametrize("plan", [None, "", "- [!] Fixture blocked task"])
def test_missing_or_blocked_plans_do_not_bypass_the_guard(operator, plan):
    """Missing/blocked task plans cannot be treated as successful completion to reset the no-decision streak."""
    # Count completed empty turns through the existing task-scoped guard.
    node, _ = operator
    for _ in range(3):
        node._record_progress(None, plan)
    with pytest.raises(OperatorNoActionLimitError):
        node._record_progress(None, plan)
