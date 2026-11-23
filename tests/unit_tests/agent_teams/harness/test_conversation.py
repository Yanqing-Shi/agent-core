# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.harness import TeamHarness, NativeHarness, HarnessState, bind_leader_preparation
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.runner import Runner
from openjiuwen.harness.goal.schema import GoalStatus, GoalStopConfig, GoalStopStrategy
from tests.unit_tests.agent_teams.harness.fixtures import drain_outputs, make_spec, start_harness


@pytest.mark.asyncio
async def test_leader_prepare_observes_restored_child_before_supervisor(monkeypatch):
    spec = make_spec()
    spec.agent_template_spec = {
        "agent_card": {"id": "leader-template", "name": "leader-template", "description": "test"},
    }
    native = NativeHarness(spec)
    native.load_agent_template_spec = AsyncMock()
    harness = TeamHarness(spec, None, native, role=TeamRole.LEADER, member_name="custom-chief")
    native.start = AsyncMock()
    seen = []

    async def prepare(actual, child, _team_session):
        assert actual is native
        assert child.get_agent_id() == native.card.id
        assert child._pre_run_done
        assert actual.is_initialized
        native.load_agent_template_spec.assert_awaited_once()
        native.start.assert_not_awaited()
        seen.append(child)

    with bind_leader_preparation(prepare):
        await harness.start()
    native.start.assert_awaited_once_with(session=seen[0])


@pytest.mark.asyncio
async def test_failed_preparation_never_starts_native():
    spec = make_spec()
    native = NativeHarness(spec)
    harness = TeamHarness(spec, None, native, role=TeamRole.LEADER, member_name="chief")
    native.start = AsyncMock()

    async def failed(_actual, _child, _team_session):
        raise OSError("commit failed")

    with bind_leader_preparation(failed), pytest.raises(OSError):
        await harness.start()
    native.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_teammate_never_invokes_leader_preparation():
    spec = make_spec()
    native = NativeHarness(spec)
    harness = TeamHarness(spec, None, native, role=TeamRole.TEAMMATE, member_name="worker")
    native.start = AsyncMock()
    callback = AsyncMock()
    with bind_leader_preparation(callback):
        await harness.start()
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_input_is_rejected_without_crashing_native_supervisor():
    from openjiuwen.agent_teams.harness.control import _CmdSend
    from openjiuwen.agent_teams.harness.state import InboxMessage

    native = NativeHarness(make_spec())
    native._on_send = AsyncMock()

    def blocked():
        raise RuntimeError("inactive_owner")

    native.set_conversation_guard(blocked)
    ack = asyncio.get_running_loop().create_future()
    await native._dispatch(_CmdSend(msg=InboxMessage(seq=0, content="late member message", immediate=False), ack=ack))
    with pytest.raises(RuntimeError, match="inactive_owner"):
        await ack
    native._on_send.assert_not_awaited()
    assert native.state is HarnessState.IDLE


@pytest.mark.asyncio
async def test_send_to_a_harness_that_is_not_running_never_asks_the_host_for_admission():
    from openjiuwen.core.common.exception.errors import BaseError

    native = NativeHarness(make_spec())
    admitted = []
    native.set_conversation_guard(lambda: admitted.append("taken"))
    with pytest.raises(BaseError, match="not started"):
        await native.send("too early")
    assert admitted == []


def _refuse() -> None:
    raise RuntimeError("session mode changed")


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resume_of_an_idle_harness_without_query_never_asks_the_host_for_admission():
    """A no-op resume starts no round, so it must not take the host's Session operation."""
    await Runner.start()
    try:
        native = NativeHarness(make_spec())
        fake = await start_harness(native)
        admitted: list[str] = []
        native.set_conversation_guard(lambda: admitted.append("taken"))
        try:
            await native.resume()
            assert native.state is HarnessState.IDLE
        finally:
            await native.stop()
        assert admitted == []
        assert fake.invocations == []
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cold_resume_refused_by_the_host_starts_no_round():
    """A cold resume that would start a round still passes the host guard first."""
    await Runner.start()
    try:
        native = NativeHarness(make_spec())
        fake = await start_harness(native)
        native.set_conversation_guard(_refuse)
        try:
            with pytest.raises(RuntimeError, match="session mode changed"):
                await native.resume(query="original task")
            assert native.state is HarnessState.IDLE
        finally:
            await native.stop()
        assert fake.invocations == []
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_goal_work_refused_by_the_host_starts_no_attempt():
    """A goal attempt from idle is new work: the host guard decides whether it may start."""
    await Runner.start()
    try:
        native = NativeHarness(make_spec())
        native.enable_goal(GoalStopConfig(strategy=GoalStopStrategy.AGENT_REPORT))
        fake = await start_harness(native)
        native.set_conversation_guard(_refuse)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(native, collected))
        try:
            await native.goal_manager.set("ship the parser", max_attempts=2)
            await asyncio.sleep(0.1)
            assert native.state is HarnessState.IDLE
            record = await native.goal_manager.get()
        finally:
            await native.stop()
            await consumer
        assert fake.invocations == []
        assert record.attempt_count == 0
        assert record.status is GoalStatus.ACTIVE
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_goal_work_that_finds_its_goal_gone_returns_the_admission():
    """Admission taken for an attempt that never starts is handed back to the host."""
    await Runner.start()
    try:
        native = NativeHarness(make_spec())
        native.enable_goal(GoalStopConfig(strategy=GoalStopStrategy.AGENT_REPORT))
        fake = await start_harness(native)
        taken: list[str] = []
        released: list[str] = []
        native.set_conversation_guard(lambda: taken.append("taken"), release=lambda: released.append("released"))
        # The goal is replaced or cleared between queueing the work and starting it.
        native.goal_manager.begin_attempt = AsyncMock(return_value=None)
        collected: list = []
        consumer = asyncio.create_task(drain_outputs(native, collected))
        try:
            await native.goal_manager.set("ship the parser", max_attempts=2)
            await asyncio.sleep(0.1)
            assert native.state is HarnessState.IDLE
        finally:
            await native.stop()
            await consumer
        assert fake.invocations == []
        assert taken
        assert len(released) == len(taken)
    finally:
        await Runner.stop()
