# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.harness import TeamHarness, NativeHarness, HarnessState, bind_leader_preparation
from openjiuwen.agent_teams.schema.team import TeamRole
from tests.unit_tests.agent_teams.harness.fixtures import make_spec


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
