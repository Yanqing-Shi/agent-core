---
id: B_20261118_0610_leader-admission-only-for-starting-rounds
title: NativeHarness 只在确定起轮时做宿主 admission
type: B
author: Yanqing-Shi
author_email: toddsyq@gmail.com
modules:
- openjiuwen/agent_teams
- openjiuwen/harness
files:
- openjiuwen/agent_teams/harness/native_harness.py
- openjiuwen/harness/deep_agent.py
- tests/unit_tests/agent_teams/harness/test_conversation.py
designs: []
design_note: 共享会话检视（2026-11-17）发现的两处 admission 缺口，修复沿用 S_18 / S_02 的既有 guard 契约，只新增一个可选的 release 回调。
---

# NativeHarness 只在确定起轮时做宿主 admission

## 现象与影响

宿主（JiuwenSwarm）给共享会话的 Leader 安装 admission guard：guard 通过即取得 Session 操作锁，
Leader 下一次转到 IDLE / TERMINATED 时释放。检视发现两处问题：

1. **空闲 Leader 的 no-op resume 占住锁。** `_dispatch()` 在 `_CmdResume` 分派前就调用 guard；
   IDLE 且无 query 的 `resume()` 在 `_on_resume` 中直接确认返回，没有状态转换，锁一直不释放，
   之后 `mode.set` 一直报 `SESSION_BUSY`，直到 Leader 再跑完一轮。
2. **从 IDLE 起的 goal attempt 绕过 guard。** 入口检查只覆盖 `_CmdSend` / `_CmdResume`，
   `_CmdGoalWork` 直接经 `_start_goal_round_if_ready` 起轮。宿主切走模式后，缓存的 Leader
   仍可被 `nudge_goal()` 唤醒并写共享记录，也不持有会话锁。

修复前用例（`test_conversation.py`）：IDLE 无 query 的 resume 调用了 guard；guard 始终拒绝时
`nudge_goal()` 仍产生一次模型调用。Swarm 侧的端到端复现见 Swarm
`B_20261118_0610_shared-session-rewind-and-leader-ownership`。

## 关联设计与当前契约

无独立 D。依据 agent_teams `S_18`「Host 对话归属扩展」与 harness `S_02`「Host 管理的对话归属」，
本次已同步修订两处（检查时机、`release` 回调）。

## 根因与修复决策

根因是 guard 的检查点放在“命令到达”而不是“确定起轮”：guard 有取得所有权的副作用，
而释放依赖之后的状态转换。

- `_dispatch` 只对 `_CmdSend` 预检；`_on_resume` 在阶段判断确定要起轮后、`_start_round` 前检查。
  准入到 `_transition(RUNNING)` 之间没有会失败的步骤。
- goal attempt 仅在 `from_idle=True`（`_on_goal_work`）时检查，位置在取到 goal work 之后、
  `begin_attempt` 之前：拒绝不计尝试次数、不让 supervisor 崩溃。收尾续跑仍沿用本轮所有权，不检查。
- 检查通过后 `begin_attempt` 返回 None（goal 被清除/替换）时没有状态转换，因此给
  `set_conversation_guard` 增加仅关键字的可选 `release` 回调，并新增
  `DeepAgent.release_conversation_admission()`。公开 API 只增不改。

考虑过只在 `begin_attempt` 之前检查、不加 release（用户选择了加 release）：那样极少数情况下锁会
残留到下一轮结束，与问题 1 同类。

## 回归证据与适用范围

候选：agent-core 工作区（基于 `ed2754c39`），本地 `local-web-mode/.venv`。

- 新增 `test_resume_of_an_idle_harness_without_query_never_asks_the_host_for_admission`、
  `test_cold_resume_refused_by_the_host_starts_no_round`、
  `test_goal_work_refused_by_the_host_starts_no_attempt`、
  `test_goal_work_that_finds_its_goal_gone_returns_the_admission`。修复前 3 个失败（冷恢复被拒一项
  原本即通过，保留作移动检查点后的回归）；修复后通过。
- harness/pause/goal/shared-conversation/team_goal 相关 99 项通过；与 #5 一起跑的 Core 选集
  （store、session、context_engine、memory、agent_teams/harness、harness/test_deep_agent、
  persistent_agent）3866 passed / 26 skipped。
- Swarm 端到端：在 Core 旧 `native_harness.py` 下，空闲 Leader no-op resume 后再取会话锁得到
  `SessionOperationBusyError`；修复后成功。

未验证：Swarm 当前不给 Team Leader 启用 goal，goal 路径只有单元与宿主接线测试，没有真实 Web 入口。

## 防错模式与资料同步

模式：带“取得所有权”副作用的 admission，检查点必须放在确定会产生释放转换的位置；否则提供显式交还。
已写入 agent_teams `S_18`、harness `S_02`、`openjiuwen/agent_teams/harness/AGENTS.md`。

## 遗留与后续

- 被拒的 goal attempt 不自动重试，等下一次 `nudge_goal()` 或下一轮收尾。
- Swarm 需传入 `release`（已在本地集成副本接线），锁定的 Core 版本必须包含本修复。
