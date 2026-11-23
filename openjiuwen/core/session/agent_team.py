# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
import uuid
from typing import Any, TYPE_CHECKING

from openjiuwen.core.session import Config
from openjiuwen.core.session.checkpointer import Checkpointer, CheckpointerFactory
from openjiuwen.core.session.internal.agent_team import AgentTeamSession
from openjiuwen.core.session.stream import (
    BaseStreamMode,
    OutputSchema,
)
from openjiuwen.core.kv_cache.kv_cache_metadata import (
    KVCacheIdentity,
)
from openjiuwen.core.kv_cache.kv_cache_types import KVCacheRuntimeProtocol
from openjiuwen.core.single_agent import (
    create_agent_session,
    Session as AgentSession,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.schema.conversation import ConversationBinding


class Session:
    """AgentTeam Session"""

    def __init__(
            self,
            session_id: str = None,
            envs: dict[str, Any] = None,
            team_id: str = "agent_team",
            source_metadata_enabled: bool = True,
            kv_cache_runtime: KVCacheRuntimeProtocol | None = None,
            *,
            trace_id: str = None,
            conversation_binding: "ConversationBinding | None" = None,
            checkpointer: Checkpointer | None = None,
            member_conversation_bindings: "dict[str, ConversationBinding] | None" = None,
    ):
        if session_id is None:
            session_id = str(uuid.uuid4())
        self._session_id = session_id
        self._team_id = team_id
        self._source_metadata_enabled = source_metadata_enabled
        config = Config()
        if envs is not None:
            config.set_envs(envs)
        self._inner = AgentTeamSession(
            session_id=session_id, team_id=team_id, config=config, trace_id=trace_id, checkpointer=checkpointer,
        )
        self._checkpointer_injected = checkpointer is not None
        self._pre_run_done = False
        self._post_run_done = False
        self._kv_cache_runtime: KVCacheRuntimeProtocol | None = kv_cache_runtime
        self._kvc_released = False
        self._conversation_binding = None
        self._member_conversation_bindings = dict(member_conversation_bindings or {})
        if conversation_binding is not None:
            self.bind_conversation(conversation_binding)

    def get_conversation_binding(self) -> "ConversationBinding | None":
        return self._conversation_binding

    def bind_conversation(self, binding: "ConversationBinding") -> None:
        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import build_error
        from openjiuwen.core.context_engine.schema.conversation import ConversationBinding

        current = self._conversation_binding
        if not isinstance(binding, ConversationBinding):
            raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="invalid Team conversation binding")
        if current is not None:
            if current.key == binding.key and current.store is binding.store:
                return
            raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="Team conversation is already bound")
        if self._pre_run_done:
            raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="bind Team conversation before pre_run")
        self._conversation_binding = binding

    def get_session_id(self) -> str:
        return self._session_id

    def checkpointer(self) -> Checkpointer:
        return self._inner.checkpointer()

    async def member_conversation_binding(self, member_id: str) -> "ConversationBinding":
        """Return a member's private window, creating its empty record on first use.

        The key is derived from the team and the member's registered name, so no
        identity is stored: a member re-created under the same name in this
        Session gets the same window. Display names and template cards do not
        take part.
        """
        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import build_error

        binding = self._conversation_binding
        if binding is None:
            raise build_error(StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="member binding requires a bound parent Team")
        if not isinstance(member_id, str) or not member_id.strip():
            raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="member conversation needs a registered name")
        private = binding.child(kind="team_member", identity=json.dumps([self._team_id, member_id], ensure_ascii=False))
        await private.store.create(private.key)
        return private

    async def prepare_agent_conversation(
        self, child: AgentSession, *, leader: bool, member_id: str | None = None,
    ) -> None:
        """Bind the leader to the main window or a member to its private window before pre_run."""
        binding = self._conversation_binding
        if binding is None:
            return
        if not leader:
            binding = await self.member_conversation_binding(member_id or child.get_agent_id())
        child.bind_conversation(binding)

    async def create_member_session(self, *, member_id: str) -> "Session":
        """Give a spawned member its private binding without exposing the main binding."""
        binding = await self.member_conversation_binding(member_id)
        return create_agent_team_session(
            session_id=self._session_id, envs=self.get_envs(), checkpointer=self.checkpointer(),
            kv_cache_runtime=self.get_kv_cache_runtime(),
            member_conversation_bindings={member_id: binding},
        )

    def get_env(self, key: str, default: Any = None) -> Any:
        return self._inner.config().get_env(key, default)

    def get_team_id(self) -> str:
        return self._team_id

    def get_cache_identity(self) -> KVCacheIdentity:
        """Return the root identity shared by this Team session."""
        return KVCacheIdentity(
            cache_id=self._session_id,
            parent_cache_id=self._session_id,
        )

    def get_kv_cache_runtime(self) -> KVCacheRuntimeProtocol | None:
        """Return the process-local KVC runtime while this Team Session is live."""
        return None if self._kvc_released else self._kv_cache_runtime

    async def prepare_kvc(self) -> bool:
        runtime = self.get_kv_cache_runtime()
        if runtime is None:
            return False
        try:
            return bool(await runtime.prepare(self.get_cache_identity()))
        except Exception as exc:
            logger.warning("Team KVC prepare failed; continue normal flow: %s", exc)
            return False

    async def suspend_kvc(self) -> bool:
        runtime = self.get_kv_cache_runtime()
        if runtime is None:
            return False
        try:
            return bool(await runtime.suspend(self.get_cache_identity()))
        except Exception as exc:
            logger.warning("Team KVC suspend failed; continue normal flow: %s", exc)
            return False

    async def release_kvc(self) -> bool:
        if self._kvc_released:
            return False
        runtime = self._kv_cache_runtime
        self._kvc_released = True
        if runtime is None:
            return False
        try:
            return bool(await runtime.release(self.get_cache_identity()))
        except Exception as exc:
            logger.warning("Team KVC release failed; continue normal cleanup: %s", exc)
            return False

    def get_envs(self):
        return self._inner.config().get_envs()

    def set_source_metadata_enabled(self, enabled: bool) -> None:
        self._source_metadata_enabled = enabled

    def update_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_state(self, key=None) -> Any:
        return self._inner.state().get_global(key)

    def dump_state(self) -> dict:
        return self._inner.state().dump()

    async def write_stream(self, data: dict | OutputSchema):
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.OUTPUT).write(
            self._normalize_output_stream(self._tag_stream_payload(data))
        )

    async def write_custom_stream(self, data: dict):
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.CUSTOM).write(
            self._tag_stream_payload(data)
        )

    def stream_iterator(self):
        return self._inner.stream_writer_manager().stream_output()

    async def close_stream(self):
        await self._inner.stream_writer_manager().stream_emitter().close()

    async def pre_run(self, **kwargs):
        if self._pre_run_done:
            return
        checkpointer = self.checkpointer() if self._checkpointer_injected else CheckpointerFactory.get_checkpointer()
        await checkpointer.pre_agent_team_execute(self._inner, kwargs.get("inputs"))
        self._pre_run_done = True

    async def post_run(self):
        if self._post_run_done:
            return
        await self.close_stream()
        await self.commit()
        self._post_run_done = True

    async def commit(self):
        """Persist the current team session state without closing the stream."""
        await self._inner.checkpointer().post_agent_team_execute(self._inner)

    async def flush_checkpoint(self):
        """Persist the current team session state without closing the stream."""
        await self.commit()

    def create_agent_session(
        self,
        card: AgentCard | None = None,
        agent_id: str | None = None,
        *,
        share_stream_writer: bool = True,
        member_name: str | None = None,
    ) -> AgentSession:
        if card is None:
            card = AgentCard(id=agent_id or "team_agent", name=agent_id or "team_agent")
        stream_writer_manager = self._inner.stream_writer_manager() if share_stream_writer else None
        source_metadata = None
        if self._source_metadata_enabled:
            source_metadata = {
                "source_agent_id": card.id,
                "source_team_id": self._team_id,
            }
            if member_name:
                source_metadata["source_member_name"] = member_name
        child_options = ({"checkpointer": self.checkpointer()}
                         if self._conversation_binding is not None or self._member_conversation_bindings else {})
        child = create_agent_session(
            session_id=self._session_id,
            envs=self.get_envs(),
            card=card,
            stream_writer_manager=stream_writer_manager,
            close_stream_on_post_run=False,
            source_metadata=source_metadata,
            kv_cache_runtime=self.get_kv_cache_runtime(),
            **child_options,
        )
        child.set_team_cache_scope(team_id=self._team_id, agent_id=card.id)
        if member_name in self._member_conversation_bindings:
            child.bind_conversation(self._member_conversation_bindings[member_name])
        return child

    def _tag_stream_payload(self, data: dict | OutputSchema):
        if not self._source_metadata_enabled:
            return data
        metadata = {"source_team_id": self._team_id}
        if isinstance(data, dict):
            return {**data, **metadata}
        if isinstance(data, OutputSchema):
            payload = data.payload
            if isinstance(payload, dict):
                payload = {**payload, **metadata}
            else:
                payload = {
                    "value": payload,
                    **metadata,
                }
            return data.model_copy(update={"payload": payload})
        return data

    @staticmethod
    def _normalize_output_stream(data: dict | OutputSchema) -> OutputSchema:
        if isinstance(data, OutputSchema):
            return data
        if {"type", "index", "payload"}.issubset(data.keys()):
            return OutputSchema.model_validate(data)
        return OutputSchema(type="message", index=0, payload=data)


def create_agent_team_session(
        session_id: str = None,
        envs: dict[str, Any] = None,
        team_id: str = "agent_team",
        source_metadata_enabled: bool = True,
        kv_cache_runtime: KVCacheRuntimeProtocol | None = None,
        *,
        trace_id: str = None,
        conversation_binding: "ConversationBinding | None" = None,
        checkpointer: Checkpointer | None = None,
        member_conversation_bindings: "dict[str, ConversationBinding] | None" = None,
) -> Session:
    """Create AgentTeam Session"""
    return Session(
        session_id=session_id,
        envs=envs,
        team_id=team_id,
        source_metadata_enabled=source_metadata_enabled,
        kv_cache_runtime=kv_cache_runtime,
        trace_id=trace_id,
        conversation_binding=conversation_binding,
        checkpointer=checkpointer,
        member_conversation_bindings=member_conversation_bindings,
    )
