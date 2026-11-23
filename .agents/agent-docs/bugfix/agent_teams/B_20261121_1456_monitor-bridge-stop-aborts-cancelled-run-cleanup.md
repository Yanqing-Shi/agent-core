---
id: B_20261121_1456_monitor-bridge-stop-aborts-cancelled-run-cleanup
title: 已取消的运行收尾时 TeamMonitorEventBridge.stop() 再次抛出取消，团队残留在运行池
type: B
author: Yanqing-Shi
author_email: toddsyq@gmail.com
modules:
- openjiuwen/agent_teams
files:
- openjiuwen/agent_teams/extensions/monitor_event_bridge.py
- tests/unit_tests/agent_teams/extensions/test_monitor_event_bridge.py
designs: []
design_note: 缺陷修复只改 stop() 判断“取消是否冲着自己”的方式，不新增契约；依据是 F_100 对桥接 stop 语义的描述。
---

# 已取消的运行收尾时 TeamMonitorEventBridge.stop() 再次抛出取消，团队残留在运行池

## 现象与影响

三个条件同时满足时触发：

1. 进程接了扩展总线（`TeamRuntimeManager.set_extension_bus`），团队冷启动时挂载了 `TeamMonitorEventBridge`；
2. 一次 leader 运行所在的任务被取消；
3. 收尾走 stop 分支：团队不是 `persistent` 生命周期，或已请求 shutdown。

`team_runner` 在 `finally` 中调用 `TeamRuntimeManager.finalize()`，其中 `_stop_monitor_event_bridge()` 调到
`bridge.stop()`，后者抛出 `CancelledError`。`finalize()` 在 `agent.stop_coordination()` 和
`self._pool.remove(team_name)` 之前中断；外层只捕获 `Exception`，所以紧随其后的
`_close_team_interact_gate()` 与 `session.post_run()` 也被跳过。后果：

- 团队留在运行池里，协作循环没有停止；`entry.monitor_event_bridge` 已被置空，但 listener 没有摘除。
- 同名团队之后再运行时，如果数据库里已没有这个团队（换库、清理后重建），`decide_run_action` 判为
  `REJECT_INCONSISTENT`：`team '<name>' present in pool but missing from DB`。
- 被取消的那次运行不执行 `session.post_run()`。

预期：收尾完整执行，团队停止并移出运行池，原有的取消在收尾后继续向上传播。

没有扩展总线时不挂桥接，不受影响。调用方没有带着取消请求时，`stop_team()` / `finalize()` 也不受影响。

发现过程：JiuwenSwarm 的两个 Team 用例只在整套运行时失败——排在前面的用例文件为整个进程接了扩展总线，
其后一个被取消的 Team 运行把团队留在池里，下一个用例换了数据库后被拒绝。单独运行这两个用例通过。

## 关联设计与当前契约

无独立 D。F_100 对桥接 stop 的描述是“cancel + await 消费任务，积压计丢弃；stop 自身被取消时传播调用方取消”。
这条语义不变，本次只修正“stop 自身被取消”的判断方式，F_100 的对应句子已同步。

## 根因与修复决策

`stop()` 取消消费任务后 `await consumer`，收到 `CancelledError` 时用
`asyncio.current_task().cancelling()` 是否非零来判断取消是不是冲着 `stop()` 自己来的。
`Task.cancelling()` 返回的是任务上尚未 `uncancel()` 的取消请求**累计数**：一个因取消而正在收尾的任务，
这个数在整个收尾期间都不小于 1。于是消费任务自己的 `CancelledError`（正是 `stop()` 刚刚请求的）
被当成调用方的新取消重新抛出。

修复：进入等待前记下 `cancelling()`，只有等待期间这个数**增加**才重新抛出。

- 调用方带着更早的取消请求进入：数值不变，吞掉消费任务的取消，`stop()` 正常返回，收尾继续；
  调用方原有的取消不受影响，由它自己的 `finally` 结束后继续传播。
- `stop()` 等待期间被取消：数值加一，照旧传播（`test_stop_propagates_cancellation_of_stop_itself` 不变）。

考虑过在 `TeamRuntimeManager.finalize()` 或 `team_runner` 的 `finally` 里接住 `CancelledError` 后继续收尾。
没有采用：误判发生在 `stop()` 内部，`stop_team()` 等其他调用点同样会中断；在收尾代码里吞取消还容易把
真正冲着调用方来的取消弄丢。

## 回归证据与适用范围

候选：`upstream/develop`（`fbe93360f`）加本次改动，Python 3.12，本地虚拟环境，未接真实模型。

- 新增 `test_stop_completes_while_its_caller_unwinds_from_an_earlier_cancellation`：一个任务在等待中被取消，
  在 `finally` 里调用 `bridge.stop()`，断言 `stop()` 之后的收尾代码执行、桥接已停止、任务仍以取消结束。
  修复前失败（`stop()` 之后的代码没有执行），修复后通过。
- `tests/unit_tests/agent_teams/extensions/test_monitor_event_bridge.py`：10 passed。
- `tests/unit_tests/agent_teams/extensions`、`tests/unit_tests/agent_teams/runtime`、
  `test_runner_team_runtime.py`、`test_collaboration_session_binding.py`、`test_persistent_team.py`：298 passed。
- `tests/unit_tests/agent_teams` 整个目录：4632 passed / 2 failed；未改动的 `fbe93360f` 为 4631 passed / 2 failed。
  两边失败的是同两个 `test_paths.py` 用例，原因是本次运行把 `OPENJIUWEN_HOME` 指到了临时目录，
  而它们检查默认主目录；去掉该变量后通过。
- 宿主侧（JiuwenSwarm 的一个下游分支，Core 为包含 `fbe93360f` 的下游分支）：接扩展总线的用例文件与
  Team 用例文件同进程运行，修复前 2 个用例以 `present in pool but missing from DB` 失败，修复后全部通过；
  `tests/unit_tests/runtime` 整个目录 1368 passed。

未验证：真实模型下的取消；接了扩展总线的生产部署。

边界：任务在同步代码里对自己请求取消、中间没有任何挂起就进入 `stop()` 时，这个请求算作“更早的请求”，
不会由 `stop()` 重新抛出。来自其他任务的取消只能在目标任务挂起时到达，不属于这种情况。

## 防错模式与资料同步

模式：收尾代码 cancel 并 await 自己启动的子任务后，要判断 `CancelledError` 是不是冲着自己来的，
应比较等待前后的 `Task.cancelling()`，不能只看它是否非零——调用方可能本来就在因取消而收尾。

本次只有这一处有失败证据，因此没有写进模块 AGENTS.md，只同步了 F_100 的对应句子。

## 遗留与后续

`agent_teams` 内还有两处同样写法（cancel + await 后以 `cancelling()` 非零为准重新抛出）：
`organization/runtime.py` 的 `_stop_review_turn` 与 `harness/native_harness.py` 的 `_cancel_round_task`。
本次没有核实它们是否会被正在因取消而收尾的任务调用，未改动；如果出现同类“收尾被中断”的现象，先看这两处。
