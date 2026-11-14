# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host-owned Leader preparation at the restored, pre-supervisor boundary."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Callable, Any

LeaderPreparation = Callable[[Any, Any, Any], Awaitable[None]]
_preparation: ContextVar[LeaderPreparation | None] = ContextVar("leader_conversation_preparation", default=None)


@contextmanager
def bind_leader_preparation(callback: LeaderPreparation):
    """Bind a host preparation callback to newly started Leader cycles."""
    token = _preparation.set(callback)
    try:
        yield
    finally:
        _preparation.reset(token)


async def prepare_leader_conversation(harness, session, team_session=None):
    callback = _preparation.get()
    if callback is not None:
        await callback(harness, session, team_session)

