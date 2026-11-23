# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Explicit main binding, private identities and durable harness completion."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.harness import HarnessState, NativeHarness, TeamHarness
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ConversationSnapshot
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.session_manager import SubagentSessionManager
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent, drain_outputs, make_spec, rebind_bridge_rails, wait_for_state,
)
from tests.unit_tests.agent_teams.harness.test_run_once import _spec
from tests.unit_tests.core.context_engine.test_shared_conversation import (
    contents, conversation, engine, react_agent, session, write,
)


async def team_session(conversation):
    checkpointer, binding = conversation
    team = create_agent_team_session(
        session_id="team-executor", team_id="team", conversation_binding=binding, checkpointer=checkpointer,
    )
    await team.pre_run()
    return team


@pytest.mark.asyncio
async def test_real_agent_and_actual_leader_share_main_with_current_rules(conversation, monkeypatch):
    await Runner.start()
    try:
        _, binding = conversation
        main_inner, main_llm = react_agent("main", monkeypatch, ["answer-A", "answer-C"])
        main = DeepAgent(main_inner.card)
        main.configure(DeepAgentConfig(enable_task_loop=False))
        main.set_react_agent(main_inner, initialized=True)
        assert (await main.invoke({"query": "A"}, await session(conversation, "main")))["output"] == "answer-A"

        spec = _spec(enable_task_loop=False)
        native = NativeHarness(spec)
        await native.prepare()
        leader_inner, leader_llm = react_agent("actual-leader", monkeypatch, ["answer-B"])
        native.set_react_agent(leader_inner, initialized=True)
        harness = TeamHarness(spec, None, native, role=TeamRole.LEADER, member_name="custom-chief")
        team = await team_session(conversation)
        result = await harness.run_once("B", team_session=team)
        assert result["output"] == "answer-B"
        assert contents([m for m in leader_llm.call_history[-1] if m.role != "system"]) == ["A", "answer-A", "B"]
        assert contents([m for m in leader_llm.call_history[-1] if m.role == "system"]) == ["current rules for actual-leader"]

        other = await engine().create_context(session=await session(conversation, "compressor"))
        other.set_messages([UserMessage(content="summary")])
        await other.save_conversation()
        await main.invoke({"query": "C"}, await session(conversation, "main"))
        assert contents([m for m in main_llm.call_history[-1] if m.role != "system"]) == ["summary", "C"]
        assert contents((await binding.store.read(binding.key)).messages) == ["summary", "C", "answer-C"]
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_deep_final_save_failure_never_returns_a_success_answer(conversation, monkeypatch, streaming):
    await Runner.start()
    try:
        inner, llm = react_agent("direct", monkeypatch, ["generated answer"])
        main = DeepAgent(inner.card).configure(DeepAgentConfig(enable_task_loop=False))
        main.set_react_agent(inner, initialized=True)
        value = await session(conversation, "direct")
        original_save = inner.context_engine.save_contexts
        original_invoke, original_stream = inner.invoke, inner.stream
        inner_finished = False
        failed_saves = []

        async def invoke(*args, **kwargs):
            nonlocal inner_finished
            result = await original_invoke(*args, **kwargs)
            inner_finished = True
            return result

        async def stream(*args, **kwargs):
            nonlocal inner_finished
            async for chunk in original_stream(*args, **kwargs):
                yield chunk
            inner_finished = True

        async def save(current):
            if inner_finished:
                failed_saves.append(current)
                raise OSError("final save failed")
            await original_save(current)

        monkeypatch.setattr(inner, "invoke", invoke)
        monkeypatch.setattr(inner, "stream", stream)
        monkeypatch.setattr(inner.context_engine, "save_contexts", save)
        chunks = []
        with pytest.raises(OSError, match="final save failed"):
            if streaming:
                async for chunk in main.stream({"query": "A"}, value):
                    chunks.append(chunk)
            else:
                await main.invoke({"query": "A"}, value)
        assert llm.call_count == 1
        assert failed_saves == [value]
        assert not any(getattr(chunk, "type", None) == "answer" for chunk in chunks)
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_teammate_private_window_follows_its_registered_name_across_team_sessions(conversation, monkeypatch):
    _, main = conversation
    team = await team_session(conversation)
    commit_identity = AsyncMock(wraps=team.commit)
    monkeypatch.setattr(team, "commit", commit_identity)
    spec = make_spec(AgentCard(id="worker-card", name="leader"))
    native = NativeHarness(spec)
    native.start = AsyncMock()
    harness = TeamHarness(spec, None, native, role=TeamRole.TEAMMATE, member_name="leader")
    await harness.start(team_session=team)
    child = native.start.call_args.kwargs["session"]
    private = child.get_conversation_binding()
    assert private.key != main.key
    assert child.checkpointer() is team.checkpointer()
    ctx = await engine().create_context(session=child)
    await write(ctx, UserMessage(content="worker only"))
    assert (await main.store.read(main.key)).messages == []
    await harness.start(team_session=team)
    # The key is derived, so no identity is stored in the Team checkpoint.
    commit_identity.assert_not_awaited()
    assert team.get_state("conversation_children") is None

    restored = await team_session(conversation)
    renamed_spec = make_spec(AgentCard(id="worker-card", name="renamed display label"))
    other_native = NativeHarness(renamed_spec)
    other_native.start = AsyncMock()
    other = TeamHarness(renamed_spec, None, other_native, role=TeamRole.TEAMMATE, member_name="leader")
    await other.start(team_session=restored)
    recovered = other_native.start.call_args.kwargs["session"]
    assert recovered.get_conversation_binding().key == private.key
    assert contents((await engine().create_context(session=recovered)).get_messages()) == ["worker only"]


@pytest.mark.asyncio
async def test_members_using_the_same_template_card_keep_distinct_private_windows(conversation):
    team = await team_session(conversation)
    spec = make_spec(AgentCard(id="shared-template", name="same display label"))
    children = []
    for member_name in ("registered-worker-a", "registered-worker-b"):
        native = NativeHarness(spec)
        native.start = AsyncMock()
        harness = TeamHarness(spec, None, native, role=TeamRole.TEAMMATE, member_name=member_name)
        await harness.start(team_session=team)
        children.append(native.start.call_args.kwargs["session"])
    first, second = children
    await write(await engine().create_context(session=first), UserMessage(content="worker A private"))
    assert second.get_conversation_binding().key != first.get_conversation_binding().key
    assert (await engine().create_context(session=second)).get_messages() == []


@pytest.mark.asyncio
async def test_member_window_is_derived_from_team_and_registered_name(conversation):
    checkpointer, main = conversation
    team = await team_session(conversation)
    first = await team.member_conversation_binding("worker")
    await first.store.commit(first.key, ConversationSnapshot(messages=[UserMessage(content="kept")]))
    # Removed and re-created under the same name: the same window, content intact.
    again = await team.member_conversation_binding("worker")
    assert again.key == first.key
    assert contents((await again.store.read(again.key)).messages) == ["kept"]
    assert (await team.member_conversation_binding("other")).key != first.key
    other_team = create_agent_team_session(
        session_id="team-executor", team_id="other-team", conversation_binding=main, checkpointer=checkpointer,
    )
    assert (await other_team.member_conversation_binding("worker")).key != first.key
    for invalid in ("", "  ", None):
        with pytest.raises(BaseError):
            await team.member_conversation_binding(invalid)
    assert (await main.store.read(main.key)).messages == []


@pytest.mark.asyncio
async def test_spawned_member_session_keeps_the_team_checkpoint_identity(conversation):
    team = await team_session(conversation)
    member = await team.create_member_session(member_id="worker")
    assert member.get_conversation_binding() is None
    assert member._inner.team_id() == "agent_team"
    child = member.create_agent_session(card=AgentCard(id="worker-card"), member_name="worker")
    assert child.get_conversation_binding().key == (await team.member_conversation_binding("worker")).key


@pytest.mark.asyncio
async def test_forked_member_window_starts_from_the_fork_once(conversation):
    from openjiuwen.agent_teams.spawn.inprocess_spawn import _seed_member_window
    from openjiuwen.core.context_engine.schema.messages import OffloadToolMessage
    from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage

    team = await team_session(conversation)

    def call(call_id):
        return AssistantMessage(tool_calls=[ToolCall(id=call_id, name="search", type="function", arguments="{}")])

    def native(messages):
        return SimpleNamespace(get_current_context=lambda session_id=None: messages)

    forked = [UserMessage(content="task"), call("done"), ToolMessage(tool_call_id="done", content="result"),
              call("spawning")]
    binding = await team.member_conversation_binding("expert")
    await _seed_member_window(native(forked), "team-executor", binding, "expert")
    seeded = (await binding.store.read(binding.key)).messages
    # The call that was still running when the fork was taken is not inherited.
    assert contents(seeded) == ["task", "", "result"]
    restored = await engine().create_context(session=await session(conversation, "expert", binding=binding))
    assert contents(restored.get_messages()) == ["task", "", "result"]

    await _seed_member_window(native([UserMessage(content="second fork")]), "team-executor", binding, "expert")
    assert contents((await binding.store.read(binding.key)).messages) == ["task", "", "result"]

    offloaded = [UserMessage(content="task"), call("big"),
                 OffloadToolMessage(tool_call_id="big", content="placeholder", offload_type="in_memory",
                                    offload_handle="source-buffer")]
    empty = await team.member_conversation_binding("expert-without-buffers")
    await _seed_member_window(native(offloaded), "team-executor", empty, "expert-without-buffers")
    assert (await empty.store.read(empty.key)).messages == []


@pytest.mark.asyncio
async def test_nested_subagent_identity_restores_without_sharing_parent_history(conversation):
    parent = await session(conversation, "parent")
    parent_agent = SimpleNamespace(create_subagent=lambda *args: SimpleNamespace(card=AgentCard(id="child-card")))
    manager = SubagentSessionManager(parent_agent, SubagentRuntimeConfig(), asyncio.Semaphore(2), parent_session=parent)
    args = dict(subagent_type="explore", subagent_id="persisted-instance-id", parent_session_id="parent",
                display_name="same label", role="researcher")
    instance = await manager.create(**args)
    child = instance._session_factory()
    await child.pre_run()
    child_binding = child.get_conversation_binding()
    assert child_binding.key != parent.get_conversation_binding().key
    assert child.checkpointer() is parent.checkpointer()
    await write(await engine().create_context(session=child), UserMessage(content="private subagent"))
    await child.commit()

    restored_manager = SubagentSessionManager(parent_agent, SubagentRuntimeConfig(), asyncio.Semaphore(2),
                                              parent_session=parent)
    restored = await restored_manager.restore(**args)
    restored_child = restored._session_factory()
    await restored_child.pre_run()
    assert restored_child.get_conversation_binding().key == child_binding.key
    assert contents((await engine().create_context(session=restored_child)).get_messages()) == ["private subagent"]
    nested_manager = SubagentSessionManager(parent_agent, SubagentRuntimeConfig(), asyncio.Semaphore(2),
                                            parent_session=restored_child)
    nested = await nested_manager.create(**args)
    assert nested._session_factory().get_conversation_binding().key != child_binding.key
    assert (await parent.get_conversation_binding().store.read(parent.get_conversation_binding().key)).messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("failure_stage", ["context", "checkpoint"])
async def test_native_waits_for_final_save_and_does_not_replay_on_failure(
    conversation, monkeypatch, fail, failure_stage,
):
    await Runner.start()
    native = NativeHarness(make_spec())
    release, entered = asyncio.Event(), asyncio.Event()
    consumer = None
    try:
        value = await session(conversation, "native")
        await native.start(session=value)
        rounds = []

        async def on_round(kind):
            rounds.append(kind)

        await native.subscribe(on_round=on_round)
        fake = FakeReactAgent(native.card)
        native.set_react_agent(fake, initialized=True)
        await rebind_bridge_rails(native, fake)
        collected = []
        consumer = asyncio.create_task(drain_outputs(native, collected))

        async def save(_session):
            entered.set()
            await release.wait()
            if fail and failure_stage == "context":
                raise OSError("final save acknowledgement lost")

        monkeypatch.setattr(fake.context_engine, "save_contexts", save, raising=False)
        original_commit = value.commit

        async def commit():
            if fail and failure_stage == "checkpoint" and entered.is_set():
                raise OSError("executor checkpoint acknowledgement lost")
            await original_commit()

        monkeypatch.setattr(value, "commit", commit)
        await native.send("first")
        await asyncio.wait_for(entered.wait(), 3)
        assert native.state is HarnessState.RUNNING
        assert not any(getattr(c, "type", None) == "answer" for c in collected)
        assert "finished" not in rounds
        queued = asyncio.create_task(native.send("queued", immediate=False))
        await asyncio.sleep(0)
        release.set()
        queued_result = await asyncio.wait_for(asyncio.gather(queued, return_exceptions=True), 3)
        assert await wait_for_state(native, HarnessState.TERMINATED if fail else HarnessState.IDLE)
        assert [call["query"] for call in fake.invocations] == (["first"] if fail else ["first", "queued"])
        if fail:
            assert isinstance(queued_result[0], Exception)
            await asyncio.wait_for(consumer, 3)
            assert not any(getattr(c, "type", None) == "answer" for c in collected)
            assert "finished" not in rounds
    finally:
        release.set()
        await native.stop()
        if consumer is not None and not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await Runner.stop()


@pytest.mark.asyncio
async def test_bound_leader_round_crash_reports_failure_and_goes_idle_without_replay(conversation, monkeypatch):
    await Runner.start()
    native = NativeHarness(make_spec())
    consumer = None
    try:
        value = await session(conversation, "native")
        await native.start(session=value)
        rounds = []

        async def on_round(kind, round_id=None, result=None):
            rounds.append(kind)

        await native.subscribe(on_round=on_round)
        fake = FakeReactAgent(native.card)
        native.set_react_agent(fake, initialized=True)
        await rebind_bridge_rails(native, fake)
        monkeypatch.setattr(fake.context_engine, "save_contexts", AsyncMock(), raising=False)
        collected = []
        consumer = asyncio.create_task(drain_outputs(native, collected))
        submit_round = native.loop_controller.submit_round
        crashed = asyncio.Event()

        async def crash_once(*args, **kwargs):
            monkeypatch.setattr(native.loop_controller, "submit_round", submit_round)
            crashed.set()
            raise RuntimeError("round runtime died")

        monkeypatch.setattr(native.loop_controller, "submit_round", crash_once)
        await native.send("first")
        await asyncio.wait_for(crashed.wait(), 3)
        assert await wait_for_state(native, HarnessState.IDLE)
        await asyncio.sleep(0.1)  # an unintended replay would start here
        assert native.state is HarnessState.IDLE
        assert rounds.count("failed") == 1 and "finished" not in rounds
        assert fake.invocations == []

        await native.send("second")
        for _ in range(300):
            if "finished" in rounds:
                break
            await asyncio.sleep(0.01)
        assert await wait_for_state(native, HarnessState.IDLE)
        assert [call["query"] for call in fake.invocations] == ["second"]
        assert rounds.count("failed") == 1 and rounds.count("finished") == 1
    finally:
        await native.stop()
        if consumer is not None and not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await Runner.stop()
