---
id: F_20261112_1036_session-conversation-store
title: 共享对话存储、Session 绑定与 ContextEngine 接入
type: F
author: Yanqing-Shi
author_email: toddsyq@gmail.com
designs:
  - jiuwenswarm:D-20261112-0530-session-conversation-window
modules:
  - openjiuwen/core/context_engine
  - openjiuwen/core/session
  - openjiuwen/harness
  - openjiuwen/agent_teams/harness
files:
  - openjiuwen/core/context_engine/schema/conversation.py
  - openjiuwen/core/context_engine/__init__.py
  - openjiuwen/core/context_engine/context_engine.py
  - openjiuwen/core/context_engine/context/context.py
  - openjiuwen/core/context_engine/conversation.py
  - openjiuwen/core/context_engine/processor/base.py
  - openjiuwen/core/context_engine/processor/forked/base.py
  - openjiuwen/core/context_engine/processor/offloader/tool_result_budget_processor.py
  - openjiuwen/core/session/agent.py
  - openjiuwen/core/session/internal/agent.py
  - openjiuwen/core/session/checkpointer/base.py
  - openjiuwen/core/session/checkpointer/conversation_store.py
  - openjiuwen/core/session/checkpointer/conversation_state.py
  - openjiuwen/core/session/checkpointer/inmemory.py
  - openjiuwen/core/session/checkpointer/persistence.py
  - openjiuwen/core/common/exception/codes.py
  - tests/unit_tests/core/session/checkpointer/test_conversation_store.py
  - tests/unit_tests/core/context_engine/test_shared_conversation.py
  - openjiuwen/core/session/agent_team.py
  - openjiuwen/harness/deep_agent.py
  - openjiuwen/harness/subagent_runtime/session_manager.py
  - openjiuwen/agent_teams/harness/team_harness.py
  - openjiuwen/agent_teams/harness/native_harness.py
  - tests/unit_tests/agent_teams/harness/test_shared_conversation.py
  - openjiuwen/core/single_agent/agents/react_agent.py
---

# 共享对话存储、Session 绑定与 ContextEngine 接入

## 范围与依据

以本地 agent-core `82b2b4efc` 为基线，用户在 I-00 之后授权继续 I-01；实现为未提交工作区差异。
跨仓设计位于同级 JiuwenSwarm 的
`.agents/agent-docs/designs/proposals/D-20261112-0530-session-conversation-window/design.md`。
本授权不代表整份草案批准，不迁移草案状态，不采用 PR #10243 或其临时代码为实现基线。
JiuwenSwarm 保留原 I-00 工作区，本阶段无新增 Swarm 改动。

当前契约见 [S_02_session-conversation-store](../specs/core_runtime/S_02_session-conversation-store.md)。
这是新的存储权威边界；历史 `F_146_host-conversation-handoff` 与 `F_32_conversation-ownership`
记录的复制交接实现仍保留，本阶段未替代其运行时入口。

## I-01 实现与决策

- 增加显式 key、versioned snapshot、async store Protocol 和 runtime-only binding，公共导出在 context_engine。
  Key 不自动从 executor/card 推导；产品 Session 与 child executor Session 可使用不同 ID。
- 在现有 InMemory/PersistenceCheckpointer 上增加非抽象 `conversation_store()` 能力，复用既有 KV/连接配置和
  pickle codec。无新增 DB/provider 配置路径，不改已有 CheckpointerFactory 选择时机。
- Snapshot 只持有 messages/offload_messages/schema_version；存储允许 pending tool calls。
  原 `conversation_snapshot` 对完整交接和路径可移植性的严格要求不适用于执行中数据，旧 API 保持兼容。
- 新记录单 key 写入，采用文本封装 codec 输出。首轮 SQLite 真 provider 测试发现现有 exclusive_set/get
  对 bytes 的返回与普通 set/get 不同；统一文本封装后可直接复用 provider，不扩大为 KV 实现重构。
- 新存储 key 使用分别编码的 slash 分量且不含字面冒号，避免与现有任意 `<session_id>:` release 前缀碰撞。
  对话记录不随 executor/checkpoint release 删除，生命周期接入属于 I-05。
- 绑定在 pre_run 前建立且不能重定向；不会自动 create/read/commit。两种支持的 AgentStorage 保存/恢复时
  仅投影掉 `global_state.context` 的已绑定槽，不原地更改 live state 或旧记录，其他执行状态保持恢复。
- 缺失、损坏和 provider 失败分别暴露明确错误，不自动回填旧 context、不重试不确定写入。
  不增加对话锁、窗口 CAS、epoch 或 lease；宿主持有 I-00 的操作边界，后续接线才会实际使用它。

设计允许自行扩展 provider；本阶段实际接入范围为 InMemory 和 Persistence（SQLite/shelve 已验证）。
RedisCheckpointer 的专用 AgentStorage/TTL 路径未改接；第三方 checkpoint 接收 binding 前须实现投影。
`Checkpointer.conversation_store()` 默认明确失败，现有子类无需新增抽象方法即可继续实例化。

## I-01 验证证据

测试环境复用同级 Swarm `.venv`（Python 3.12.14），源码加载本地 agent-core；可选测试依赖沿用 I-00 环境。
缺 pytest-html，故通过 `-o addopts=''` 禁用原有 HTML 报告参数，不改变仓库配置。
最初沙箱内 SQLite 测试停滞，已终止该进程，随后在沙箱外完成本地临时数据库测试。

```bash
source ../jiuwenswarm/.venv/bin/activate
export PYTHONPATH=.:/tmp/i00-agent-gateway:/tmp/i00-agent-protocol/openjiuwen-protocol:/tmp/i00-agent-protocol/A4P:${PYTHONPATH:-}
python -m pytest -o addopts='' \
  tests/unit_tests/core/session/checkpointer \
  tests/unit_tests/core/context_engine/test_conversation.py \
  tests/unit_tests/core/context_engine/test_context_engine.py \
  tests/unit_tests/core/context_engine/test_context_model.py \
  tests/unit_tests/core/foundation/store/test_db_based_kv_store.py \
  tests/unit_tests/core/foundation/store/test_in_memory_kv_store.py -q
```

结果：**246 passed, 2 skipped**，6.78s，日志 `/tmp/i01-regression.log`。
两个跳过项为既有 MySQL/外部数据库环境测试；已有 Pydantic/SQLAlchemy 弃用警告不影响结果。

新增行为测试通过公开 Session/pre_run/commit 和真实内存、SQLite、shelve provider 验证：
幂等创建、引用隔离、key 分量碰撞、main/child/Session 隔离、结构化消息和 offload buffer、pending tool call、
缺失/损坏/未知 schema 不清空、存储错误 cause、持久化重开、旧 checkpoint 恢复投影、多个绑定 context、
保留任务/global/agent 状态、release 不删除共享记录、绑定不可重定向和建立时机。
故障注入仅用于 provider 异常边界，其余测试使用真实存储实现。

下游兼容检查使用当前 Swarm I-00 工作区与本地 core，执行：

```bash
source .venv/bin/activate
export PYTHONPATH=.:../agent-core:/tmp/i00-agent-gateway:/tmp/i00-agent-protocol/openjiuwen-protocol:/tmp/i00-agent-protocol/A4P:${PYTHONPATH:-}
python -m pytest -o addopts='' -o log_cli=false -o faulthandler_timeout=10 \
  tests/unit_tests/test_context_handoff.py \
  tests/unit_tests/runtime/test_session_operation_lock.py -q
```

结果：**83 passed**，6.15s，正常退出，日志 `/tmp/i01-swarm-without-reports.log`。
起初保留默认 coverage 参数的 83 项及末项单独运行都已报告用例通过，但在收尾阶段超时；
按 I-00 既有验证方式关闭 coverage 报告后完整通过。该证据不包括 coverage 报告生成。

未验证真实模型、跨模式模型输入、Team/NativeHarness、真实 Redis/MySQL/PostgreSQL 和已安装发布包。
本阶段未接线 ContextEngine，因此不能据本结果声称压缩或模式切换已经共享窗口。
未运行格式化、静态检查，未提交、推送或创建 PR。

## I-01 检视与复查

按 `jw-review` 对本地基线到 I-01 工作区差异检视公开导出、Session 构造/恢复/提交、两种已接入 AgentStorage、
KV 记录边界、错误传播和既有复制 API。检视发现 create 应先读取现存记录再决定是否调用 exclusive_set：
SQLite provider 对任意 JSON 形式损坏数据的内部解析不等同于 conversation schema 校验。
已回开发补充缺失检查，并将损坏记录负向测试扩展到内存、SQLite 和 shelve；上述 246 项通过结果对应修复后候选。
当前限定范围无未解决的阻断项；下游 83 项确认既有交接与 I-00 锁消费者兼容，
不代表 Redis、模型或后续共享窗口接线已验收。

执行文档同步后，还原工具自动补写的 9 份无关旧文档 frontmatter，保留原索引作者；
本阶段仅保留新 F/S 的索引视图变化，不迁移历史文档元数据。

## I-01 交接（当时状态）

I-02 从 Session binding 接入 ContextEngine/SessionModelContext：取得宿主操作权后始终读取当前 record，
命中本地缓存也须刷新；追加、压缩、offload 通过同一数据通道提交。保留当前执行者的 processors/model budget，
不能把旧执行者 checkpoint 或 public history 作为共享记录缺失时的隐式来源。
I-03 再为 Agent/实际 Leader 和私有子执行者注入 key，并把最终提交纳入完成边界。
旧 Session 的产品行为/迁移继续留待用户决定；Swarm core pin 更新留待匹配发布。

后续候选改变 schema、key 编码、provider、绑定或 checkpoint 投影时，应重跑本记录受影响测试；
模型输入和生命周期行为需要在相应后续阶段补充实际入口证据。

## I-02 接入与决策（2026-11-12 Asia/Singapore）

用户在核对两仓整体 delta 后授权继续。此阶段在既有 I-01 工作区上推进，仅增加 core 改动。
交付比较基线应为 core `b412128ab`、Swarm `7853595c7`，包含当前 HEAD 中已经提交的旧交接补丁，
以及当前工作区与新增文件。不能把相对 HEAD 的增量当作完整补丁；Swarm 更早 TUI 改动若一并评估，
另用 `550bc92ba` 为基线。当前实现仍基于本地源码，不采用 PR #10243 为基础。

本阶段续写同一 F/S，保留 I-01 的历史验证事实，不按开发单元另造一份状态记录。

- `ContextEngine.create_context` 对显式绑定窗口每次读取当前 record，缓存命中也刷新；不使用旧 checkpoint、
  history_messages 或缓存回填。缺失不自动 create，曾共享的缓存不能通过未绑定 Session 降级加载。
- `SessionModelContext` 沿用现有 processor 锁串行化存储提交。异步 add、GET processor、active compression
  都提交共享数据；同步 set/pop/offload 由后续异步路径或显式 save_contexts/save_conversation 保存。
  没有新增窗口锁、CAS、epoch、lease 或后台写入任务。
- 活跃压缩中的旁路追加保留原有入队语义，由当前持锁者保存。提交阶段提前关闭旁路，使新追加等待 processor 锁，
  避免序列化快照生成后到达的消息漏出本次或下次提交。存储/processor 异常或取消后需成功刷新才能再构建模型窗口、追加或保存。
- store 继续允许 pending tool calls；恢复/切换读取复用旧补丁的纯 `conversation_snapshot` 校验器要求工具序列完整。
  复用的是消息校验，不执行复制或 receipt 事务。输入保存失败会中止后续模型调用；确认丢失只允许重新读真实 record，
  不从旧缓冲重写或重放外部工具。
- 共享 offload 经现有官方和 forked 两套公共 processor 入口改用 `in_memory` 消息表示，原文 buffer 同 record 持久化；不依赖 executor
  私有文件。Budget/Window 引用按真实存储类型渲染，内存引用不再附带未创建的私有路径。无法解析的文件引用明确拒绝，
  不扩展为私有文件迁移或生命周期复制。
- 保留当前执行者的 processor/model budget、系统提示与 tools；共享装载清除 usage 报告/测量 cache 并使旧 usage 无效。
  检视发现不能同时清掉上次 LLM 请求窗口：KV 差异检测需要用它识别其他执行者压缩后的前缀变化。
  已保留该差异基线，并通过真实 `detect_context_window_change` 验证共享刷新后的失效范围。
  同时保留缓存实例的本地最后访问时间；真实 forked MessageSummaryOffloader 回归验证跨刷新后 idle TTL 仍触发，
  该执行状态不进入共享 snapshot。
  此回归还暴露了 forked processors 的独立 offload 基类入口；已同步接入共享 buffer，避免 TTL 路径仍生成私有文件引用。
  Session checkpoint 只保留未绑定 context；部分共享保存保留未缓存的局部 context。context ID 的点号规范化与现有引擎一致。
- 缓存 key 从拼接字符串改为 Session/context 二元组，避免 `a_b/c` 与 `a/b_c` 命中同一对象并串写不同 ConversationKey。
- 检视旧 `replace_conversation` 发现：其失败回滚只恢复 live context 和 executor checkpoint，无法回滚新 store 已写入的记录。
  因此对绑定窗口明确拒绝旧事务，未绑定路径保持既有兼容。该保护不代表 Swarm 的复制路径已删除；完整移除仍属 I-06。

### I-02 验证证据

环境沿用 I-01 的本地源码/venv；测试禁用默认 HTML/coverage 报告参数。最终 core 命令：

```bash
source ../jiuwenswarm/.venv/bin/activate
export PYTHONPATH=.:/tmp/i00-agent-gateway:/tmp/i00-agent-protocol/openjiuwen-protocol:/tmp/i00-agent-protocol/A4P:${PYTHONPATH:-}
python -m pytest -o addopts='' \
  tests/unit_tests/core/context_engine tests/unit_tests/core/session/checkpointer tests/unit_tests/core/kv_cache \
  tests/unit_tests/core/single_agent/test_final_request_check.py \
  tests/unit_tests/core/single_agent/test_context_usage_events.py \
  tests/unit_tests/harness/test_deep_agent_interaction.py \
  tests/unit_tests/harness/test_deep_agent_terminal_stream.py \
  tests/unit_tests/agent_teams/harness/test_conversation.py \
  tests/unit_tests/agent_teams/harness/test_agent_template_prepare.py -q
```

最终复验：**857 passed**，18.69s，日志 `/tmp/i02-core-regression-final.log`。包含整个 ContextEngine、checkpoint、KV cache 与旧补丁实际消费者。
新增共享窗口测试使用三种真实 provider，覆盖跨执行者/缓存恢复、被压缩内容不复活、真实 MicroCompact/MessageOffloader/
ToolResultWindowProcessor、usage、partial save、pending tools、私有窗口、cache key 碰撞、GET 修改、提交/读取失败、ACK 丢失、
取消，以及压缩和存储提交处的并发屏障。真实 forked TTL 路径验证本地访问时间跨共享刷新保留，
offload 原文逐字保存到 provider buffer；KV 回归验证其他执行者改写共享前缀后仍返回正确失效范围。

新增真实 `ReActAgent.invoke` 使用现有 `MockLLMModel` 作为模型边界替身，未 mock ContextEngine/store/session：
最终模型请求包含 A/answer-A/B/D/C，并且只使用当前 executor 的系统提示；共享记录只追加一次。
输入存储失败时模型调用数为零。此证据不等同于实际 Team Leader/NativeHarness 的 key 自动注入或真实模型联调。

下游验证扩大至旧补丁与 I-00 的所有相关 Swarm 消费者。初轮 `/tmp/i02-swarm-regression.log` 为
318 passed、67 failed：主要因 Gateway 导入缺少 `openjiuwen_runtime`，另有关闭默认 addopts 后遗漏原有 asyncio auto 模式。
不将这些失败修改为 stub 或跳过。按 Swarm `uv.lock` 从 GitCode 稀疏获取
`agent-runtime` 精确提交 `d4a8761c8a561b1ee1b8a4333def0077b308e709` 的 foundation 子目录，
放在 `/tmp/i02-agent-runtime-foundation`（约 1.1 MB）；并仅向被忽略的 Swarm 测试 venv 安装锁定的 `redis==8.0.0` 客户端。
不连接 Redis/MySQL 服务，不修改两仓 pyproject/lock。该目录是可按锁定提交重建的测试依赖，不是业务实现或 PR 参考副本。

```bash
# cwd: repos/jiuwenswarm
source .venv/bin/activate
export PYTHONPATH=.:../agent-core:/tmp/i00-agent-gateway:/tmp/i02-agent-runtime-foundation/foundation:/tmp/i00-agent-protocol/openjiuwen-protocol:/tmp/i00-agent-protocol/A4P:${PYTHONPATH:-}
python -m pytest -o addopts='' --asyncio-mode=auto -o log_cli=false \
  tests/unit_tests/test_context_handoff.py \
  tests/unit_tests/test_session_ops_warmup.py \
  tests/unit_tests/agentserver/test_deep_adapter_stream_end_final.py \
  tests/unit_tests/agentserver/test_team_helpers.py \
  tests/unit_tests/server/test_session_metadata_write_options.py \
  tests/unit_tests/runtime/test_session_operation_lock.py -q
```

最终结果：**385 passed**，30.80s，正常退出，日志 `/tmp/i02-swarm-regression-final.log`。
这覆盖既有 handoff/warmup、Deep/Team adapter、metadata 与 Session 操作锁；不证明新主窗口已经接到宿主真实模式切换。

### I-02 检视与后续

按 `jw-review` 核对旧补丁与当前新增模块的读取→消息处理→模型请求→保存→恢复链，
依据为根 AGENTS、core_runtime/S_02 及相关旧 harness spec。旧 Native/DeepAgent 最终提交分支仍按当前无 binding 的行为回归。
没有运行格式化或静态检查，没有提交、推送或创建 PR。

I-03 需要显式注入主 Agent/实际 Leader 的共同 key、子执行者私有 key，并把最终共享保存纳入完成边界。
必须核对旧补丁“提交失败只记日志并继续/进入 IDLE”的分支：不能把共享窗口存储失败当作普通 checkpoint 失败吞掉。
主路径绑定后不能继续调用旧复制/receipt 事务；Swarm 接线与完整旧路径清理按 I-04/I-06 推进。
旧 Session 处理、宿主生命周期和依赖 pin 更新仍未纳入此阶段。


## I-03 范围与补充约束

用户授权继续 I-03，并补充普通 chat 也必须遵守删除、归档、模式切换的操作互斥。
本阶段在当前 core 上接通显式 Team 主绑定、实际 Leader 继承、成员/嵌套 subagent 私有窗口及最终保存边界；
Swarm 增加反向 public chat 回归和相关互斥说明。旧 Session 处理仍待决定，不改依赖 pin，不删除旧 handoff。

Team 私有身份使用 checkpoint 中的随机 UUID，显示名称仅用于已有 executor registration 槽位定位；
Subagent 使用现有已持久化 instance ID，嵌套派生包含父窗口身份。
只对分配身份序列化；不新增共享窗口锁、CAS 或分布式协议。成员移除后的身份退役须在 I-05 接线。
已登记身份的窗口缺失明确失败。绑定引用只保留在运行内存，checkpoint 保存身份字符串及原执行状态。

已绑定 DeepAgent/NativeHarness 必须在成功终态或下一任务之前等待 ContextEngine 与 checkpoint 保存；
失败不再按旧兼容路径吞错或自动重放。Swarm I-04 注入尚未开始，本阶段验证不等于已发布产品整体接通。


### I-03 验证与交接

最终 core 回归：**1687 passed**，27.11s，日志 `/tmp/i03-core-regression-final.log`。
范围为 `tests/unit_tests/agent_teams/harness`、`agent_teams/test_team_harness_integration.py`、
`harness/test_deep_agent.py`、`harness/test_deep_agent_interaction.py`、`harness/test_deep_agent_terminal_stream.py`、
`harness/subagent_runtime`、`core/session`、`core/context_engine`、`core/kv_cache`。
新增 I-03 行为测试 30 项（内存/SQLite/shelve），包含真实 ReAct 请求入口的可控 MockLLM、
Agent → 实际 Leader → 共享压缩 → Agent、当前 system prompt、Team 私有身份冷恢复、嵌套 subagent 隔离，
以及最终 context 保存和 executor checkpoint 确认失败时的答案、round 事件、状态与队列边界。
模型无真实网络调用，不声称实际模型供应商或已发布依赖通过。

下游当前 Swarm 工作区配本地 core 回归：**1447 passed, 1 skipped**，47.54s，
日志 `/tmp/i03-swarm-regression-final.log`；另包含 chat 反向互斥修复的 Runtime/模式切换检查。
测试仍使用 I-02 已记录的本地源码依赖、Swarm `.venv`、`-o addopts=''`，Swarm 明确 `--asyncio-mode=auto`。
既有 Pydantic/SQLAlchemy 弃用警告保留；无依赖文件更新。

检视了绑定时机、实际 role 判定、父 checkpointer 继承、运行内存与 checkpoint 身份元数据边界、
最终答案/事件先后关系、未绑定兼容路径，以及删除/归档受理与 finalizer 间隙的 fence。
I-03 在当前工作区完成；下一阶段 I-04 才在 Swarm Service/Manager/adapter 注入主绑定。
成员退役、共享 record 的 delete/archive/fork/rewind 仍归 I-05；旧 Session 决策保留，不以旧 copy API 回填。
未提交、未发布、未更改 pin；总差异盘点必须包括旧 handoff 已提交补丁，不能只看 HEAD 工作区 diff。


## 按收敛设计复查与简化（2026-11-12）

用户要求重新审查当前 design.md 并直接简化冗余。依据为收敛版 §4.1、§4.2、§4.4、§5/§6：
共享窗口与执行状态分离、复用必要保存边界、主运行共锁，以及保留现有生命周期保护。
复查范围包括旧 handoff 已提交补丁与当前工作区，不以 HEAD 工作区差异代替总候选。

确认并简化的冗余：
- ContextEngine 对未变化窗口的重复全量写入：复用既有本地 message revision，仅记录已成功保存的位置；
  消息与 offload 有变更才保存。该位置仅为进程内写入去重，不是存储 revision/CAS 或第二份消息权威。
- invoke、stream、interaction、Native round 重复的普通提交异常处理：归并到一个内部完成收尾入口，
  共享绑定仍传播错误，旧未绑定路径仍记录异常并继续；公开 commit_conversation 保持原异常契约。
- Team 私有身份复用时的无变更 checkpoint 提交：仅新分配身份时持久化映射；恢复仍读取并核实已有私有记录。
- Swarm 操作边界的嵌套异常收尾：合并为同一状态标记与释放路径，保留控制输入、后台执行和取消语义。

本轮不删除旧 handoff 活跃消费者，不撤销已落地的生命周期保护；独立私有身份仅用于当前 Team 注册缺少稳定实例 ID 的情况。
共享主绑定、当前模型预算、中断恢复及取消/回滚全路径的产品验收仍以收敛设计为准；此前 I-03 的定向通过不替代这些验收。


本轮进一步核对 Team 私有身份：原实现按 card ID 查找注册槽，两个成员复用同一模板 card 时会取到同一 UUID。
使用真实 TeamHarness 的同模板双成员场景，在内存、SQLite、shelve 上均复现串写风险：
`/tmp/session-simplify-identity-before.log`，3 failed。
定位现改为现有成员注册 ID（TeamHarness 的 member_name，是成员注册标识，不是 display_name）；
未命名执行者保留 executor ID 适配，不创建第二套成员注册表。
显示名称变更的恢复测试同步改为真正修改 card.name，同时保持成员注册 ID 不变。
这也是设计 §4.1 要求的复用：不以模板或显示信息推断实例身份；只在缺少 incarnation ID 时保留最小持久化 UUID 映射。


### 本轮简化后的验证与保留边界

最终 core 候选：**1702 passed**，25.06s，日志 `/tmp/session-simplify-core-rechecked.log`。
范围沿用 I-03 的完整受影响模块：Agent Teams Harness 与集成、DeepAgent/interaction/terminal stream、
subagent runtime、core Session、ContextEngine、KV cache。新增写入去重/并发/offload 行为 12 项、
同模板 card 的双成员隔离 3 项，均覆盖内存、SQLite、shelve。
同模板负向对照修复前 3 项失败、修复后 Harness 33 项通过；最终全量包含这些用例，不另相加。

具体结果：一次真实 append 后，重复窗口读取、重复 ContextEngine/save_conversation flush 与无变化主动压缩
总共只提交一次共享记录；offload-only 修改、clear、restore 和保存期间的后续修改仍可从真实 provider 读回。
普通提交失败的兼容行为、绑定后的严格失败/禁止自动重放、最终答案与 round 事件边界均保持。
日志仍包含 invoke/stream/interaction 或 round_id，公开 commit_conversation 继续传播失败。

最终本地 Swarm + core 候选：**1447 passed, 1 skipped**，44.01s，
日志 `/tmp/session-simplify-swarm-rechecked.log`；完整 Runtime、archive lifecycle、旧 handoff、warmup、Team helpers/terminal stream。
单独 Coordinator/操作锁/mode 定向回归 136 项通过，与完整回归重叠，不相加。

保留并核实有实际职责的组件：ConversationKey/binding 公共适配、processor 锁、主 Session 操作锁及其运行引用、
lifecycle fence/finalizer，以及当前尚有活跃消费者的 copy/owner/pending/receipt。
后者是 I-04/I-06 消费者改接后的清理目标；本轮不把删除仍被调用的路径当作简化。
最小私有 UUID 映射用于尚无 incarnation ID 的成员生命周期，不新建独立成员注册表或锁服务。

文档及生成索引已同步。未提交、未发布、不更改依赖 pin；旧 Session 迁移仍不纳入本轮。
本轮只对列出的冗余与身份定位进行了修复及复查，不把通过结果扩展为新版设计的全部端到端验收。


## 2026-11-13：移除复制式交接（设计 I-06）

用户检视后确认：旧交接提交（rebase 后为 `4706b05ec`）未进入 develop、无上游依赖，随共享对话方案直接移除，不保留到后续收尾。
同日先将本分支 rebase 到 `origin/develop`（`f152a3a42`），代码无冲突，生成索引重新生成。

移除：`export_conversation` / `replace_conversation` 及其导出；`persisted_team_conversation_is_idle`；
DeepAgent 与 NativeHarness 的 `conversation_is_idle`、`conversation_peers_are_idle`；`AsyncToolRuntime.has_pending_tasks`；
“安装了 guard 即提交并吞掉失败”的分支（`_post_run_owned_session`、`_commit_conversation_on_completion`、stream 的 `session_finalized`）；
guard 的 `request_id` 参数与 `RunContext.extra["conversation_request_id"]`。

保留（共享路径仍在使用）：`conversation_snapshot` 校验器、`set_conversation_guard` / `check_conversation_admission`（改为无参数）、
`NativeHarness.prepare`、`bind_leader_preparation` / `prepare_leader_conversation`。
`commit_conversation` 现在只对显式绑定的 Session 生效且失败直接传播；未绑定 Session 回到绑定能力加入前的行为（不在完成边界额外提交，自建 Session 的 post_run 异常传播）。

测试：删除只覆盖已移除接口或吞错分支的用例（context_engine/test_conversation 4 项、agent_teams/harness/test_conversation 5 项、
test_shared_conversation 的旧复制拒绝 1 项），`test_deep_agent_interaction.py` 与 `test_deep_agent_terminal_stream.py` 恢复为交接提交之前的内容。
保留 Leader 启动前钩子、派发拒绝不致 supervisor 崩溃、快照校验的用例。

验证（本地源码，Swarm `.venv`，`-o addopts=''`）：rebase 后、移除前 **1878 passed**；移除后同一范围 **1833 passed**，37.69s。
范围：agent_teams/harness、test_team_harness_integration、test_deep_agent*、subagent_runtime、core/session、core/context_engine、core/kv_cache、
agent_teams/test_fork、agent_teams/tools/test_tool_expert。配套 Swarm 回归见其 F_20261113_0307 同日记录。
未运行格式化或静态检查，未提交、未推送；分支 rebase 后与远端同名分支分叉，推送需强制。

本次只做移除。设计同日确认的其余变更（Leader 轮次异常后回到空闲、写回时点、成员身份确定性派生、专家 fork 适配）尚未实施，
上文 I-02/I-03 的对应描述仍是当前代码行为。

## 2026-11-13：写回时点、Leader 轮次异常与 send 顺序

依据设计 §4.2、§4.5 及同日用户决定（设计 §9），用户确认后实施；对应检视意见 R2、R4（send 顺序部分）、R6。

- 写回时点：`add_messages` 不再逐条提交共享记录。写回发生在每次模型请求之前（`get_context_window`，原已存在）、
  主动压缩结束，以及运行结束的最终保存。ReAct 在回答、强制结束、中断、达到迭代上限、取消和异常路径上原本就调用
  `save_contexts`，因此不需要新增保存点。长会话的写入量由“每条消息一次整份重写”降为“每次模型请求一次”。
- `KVSessionConversationStore.commit` 不再先读取并解码已有记录，只做存在性检查后整份替换。
  损坏记录仍由 `read` / `create` 报告并保持原样；装载失败的窗口处于需刷新状态，本身不能提交，引擎层的保护不变。
  拒绝方案：保留提交前完整读取——每次写入都要解码整份历史，是检视指出的 O(N²) 来源。
- 已绑定路径上 Leader 某一轮运行时异常：该轮报告 failed 后回到 IDLE，不重放输入，supervisor 保持存活。
  此前这里直接抛错并终止 supervisor。保存失败仍然传播并终止，两者是不同路径。未绑定路径的一次自动重放不变。
- `NativeHarness.send` 先 `_require_alive()` 再做宿主 admission。此前向未启动的 harness 发送会先取得宿主的
  Session 操作，随后抛错，而没有任何状态迁移来释放它。

已接受的代价（设计 §4.2）：进程在两次模型请求之间异常退出时，上一次请求之后的 assistant 回复和工具结果不在记录中。
在运行之外追加消息的宿主必须显式保存；已核对 Swarm 中的此类调用点（审批批次清理、撤回最后一条输入、warmup）均已显式调用 `save_contexts`。

测试：依赖“追加即写回”的既有用例改为显式保存（context_engine 共享用例、team harness 共享用例各若干处）；
`test_corrupt_record_is_reported_and_never_overwritten` 改为只覆盖 `read` / `create`。新增：
`test_appends_reach_the_record_at_a_model_request_or_the_final_save_only`、
`test_real_react_run_writes_before_its_model_request_and_at_the_end`、
`test_commit_replaces_the_record_without_decoding_the_stored_one`、
`test_bound_leader_round_crash_reports_failure_and_goes_idle_without_replay`、
`test_send_to_a_harness_that_is_not_running_never_asks_the_host_for_admission`。

验证（本地源码，Swarm `.venv`，`-o addopts='' -o log_cli=false`）：**1796 passed**，32.80s。
范围：core/context_engine、core/session、core/single_agent、agent_teams/harness、test_deep_agent_interaction、
test_deep_agent_terminal_stream、harness/subagent_runtime。配套 Swarm 回归见其 F_20261113_0307 同日记录。
未运行格式化或静态检查，未提交、未推送。

尚未实施：成员窗口身份按 team 与注册名确定性派生、主记录单一建档入口、专家 fork 以一次性复制作为新窗口起点、
切换到较小窗口模型时的压缩验证（V-07）。规约 S_02 §7 关于随机实例身份的描述仍是当前代码行为。

## 2026-11-14：成员窗口身份、专家 fork 起点

依据设计 §4.1、§6 及 2026-11-13 的用户决定，用户确认后实施；对应检视意见 R8、R9。

- 成员私有窗口 key 改为由 `team_id` 与成员注册名确定性派生（新增 `Session.member_conversation_binding`）。
  移除 Team checkpoint 中的 `conversation_children` 映射、identity allocation lock 及为登记身份所做的 Team checkpoint 提交。
  首次使用时幂等创建空记录；同名重建沿用原窗口。
  代价：没有登记后，“已登记但记录丢失”无法与首次使用区分，丢失的成员窗口会变成空窗口；原用例
  `test_recorded_private_identity_never_recreates_a_missing_window` 随之删除。
- `create_member_session` 不再创建临时 AgentSession，也不再把 conversation id 填入成员 Team Session 的 `team_id`；
  成员 Team checkpoint key 与 KV-cache 作用域恢复为绑定能力加入之前的默认值。参数 `card` 已无用途，移除（该 API 尚未发布）。
- 专家 fork：in-process spawn 在已绑定成员的私有窗口为空时，把 fork 内容（含 fork 压缩结果）一次性写入作为起点。
  此前 fork 只注入成员引擎的内存 context，成员运行时从 store 装载，继承内容被静默丢弃。
  写入前丢弃未闭合的工具调用块；窗口已有内容时不覆盖。
  限制：`ForkContext` 只携带消息，不携带 offload 原文。fork 含 offload 引用时无法通过完整性校验，成员从空窗口开始并记录告警，
  与既有“fork 捕获失败降级为不继承”的约定一致。让 fork 携带 offload buffer 属于分叉功能扩展，设计 §6 已列为另行设计。
  另：来源成员的 context 未在内存池时，`ForkContext._read_persisted_messages` 仍读取 checkpoint 中的 context，
  已绑定来源在那里没有数据，捕获会按原约定失败并降级；本次未改。

测试：`test_teammate_private_window_follows_its_registered_name_across_team_sessions`（改写）、
`test_member_window_is_derived_from_team_and_registered_name`、`test_spawned_member_session_keeps_the_team_checkpoint_identity`、
`test_forked_member_window_starts_from_the_fork_once`（新增）。

验证（本地源码，Swarm `.venv`，`-o addopts='' -o log_cli=false`）：**2018 passed**，63.45s。
范围：core/context_engine、core/session、core/single_agent、core/kv_cache、agent_teams/harness、agent_teams/test_fork、
test_team_harness_integration、tools/test_tool_expert、test_messager、test_deep_agent_interaction、test_deep_agent_terminal_stream、
harness/subagent_runtime；另 agent_teams/agent 下两个 spawn 相关文件 23 passed。
未运行格式化或静态检查，未提交、未推送。

尚未实施（写于当日较早时）：切换到较小窗口模型时的压缩验证（V-07，见下一节）；I-05 生命周期消费者；I-07 文档与发布依赖。

## 2026-11-14：V-07 当前模型预算验证

只新增测试，未改产品代码。`test_smaller_model_compresses_before_its_first_request_and_larger_model_keeps_the_summary`
（主窗口与成员私有窗口各一组，三种 provider）用两个真实 ReAct 执行者共用同一条记录：

- 约 6000 token 的历史在 200k 窗口的执行者上原样发送，不压缩；
- 切到 5k 窗口的执行者后，压缩在它的首次模型请求之前执行一次，触发点为它自己窗口的 80%；
  发出的请求不含原文且不超过 5k，记录中保存的是摘要；
- 切回 200k 执行者：缓存窗口被摘要替换，请求和记录都不展开原文，也不再次压缩。

压缩器使用 Swarm 实际配置的 forked `RoundLevelCompressor`（`trigger_context_ratio`），只在压缩模型调用处打桩。
探查中确认两点配置前提，均为既有行为：该压缩器必须配置自己的 `model` / `model_client` 才会触发；
非 forked 版 `RoundLevelCompressor` 的 `target_total_tokens` 默认 160000，对窗口小于该值的模型不会压缩。
设计 §4.3 中“不可压缩的提示/工具仍超限时明确失败”未在本次验证，也不在已实现范围内。

验证：该文件 **93 passed**；未运行格式化或静态检查，未提交、未推送。

尚未实施：I-05 生命周期消费者；I-07 文档与发布依赖。

## 2026-11-14：删除/归档移出范围

用户决定删除与归档不属于共享对话方案；Swarm 已移除相关改动并把操作锁与归档/删除的执行锁分开（见 Swarm 的 F_20261112_0650 同日记录）。
core 无代码改动，只修订规约与 API 文档中“宿主操作锁覆盖删除、归档”的表述。`checkpointer.release` 不清理共享记录的现状不变，
删除 Session 后其对话记录仍留在存储中，接入点预留给后续开发。


## 2026-11-20：去除共享记录读写中的重复拷贝

起因：每个新建的原生模式 Session 都使用共享记录，不切换模式的用户也在每次模型请求前整窗写回一次。
测量发现写回与装载里有不起作用的深拷贝：

- 写回：`_encode` 先 `to_state()` 深拷贝整个窗口，随即 pickle，拷贝结果立刻丢弃；
- 装载：`store.read` 为校验调用 `to_state()` 并丢弃返回值，`load_conversation` 再调用一次 `to_state()`，
  `conversation_snapshot` 又拷贝一次，共三次。

改动：`ConversationSnapshot.to_state` 增加 keyword-only `copy`（默认 True，原行为不变）。
`KVSessionConversationStore` 的 `_encode` 与 `read` 使用 `copy=False`；`load_conversation` 把读到的记录以
`copy=False` 交给 `conversation_snapshot`。结果：写回 0 次拷贝，装载 1 次。

取舍：

- 保留装载时 `conversation_snapshot` 的那一次拷贝。规约要求 store 自己提供隔离，现有两个 provider 都经 pickle，
  这一次也可以省；保留它是为了窗口在任何 store 实现下都不持有对方返回的对象，代价是每次运行一次拷贝。
- 不新增独立的 `validate()`：字段列表只在 `to_state` 一处维护。
- `conversation_snapshot` 的签名与拷贝行为不变，in-process fork 起点写入沿用。

测量（本机 SQLite，合成消息，经 ContextEngine 的真实装载与保存入口，15 次取中位数）：

| 窗口 | 保存：改前 → 改后 | 装载：改前 → 改后 |
| --- | --- | --- |
| 约 150k token | 7.7 ms → 5.6 ms | 13.3 ms → 10.1 ms |
| 约 250k token | 14.5 ms → 11.1 ms | 21.5 ms → 15.2 ms |

未测量远程数据库后端；整窗写回的频率与数据量不变。

验证：新增 `test_to_state_copies_unless_the_caller_asks_for_its_own_lists`、
`test_store_detaches_by_serialising_without_copying_the_window_first`（三种 provider，写后改原对象、读后改返回对象，
记录均不变且无拷贝）、`test_loading_copies_the_record_once_and_saving_does_not_copy_it`（三种 provider），
`test_invalid_snapshot_is_rejected` 同时覆盖 `copy=False`。新增与扩展的用例在还原产品代码后 12 项失败。
`tests/unit_tests/core/context_engine`、`tests/unit_tests/core/session` 与
`tests/unit_tests/agent_teams/harness/test_shared_conversation.py` 共 **1197 passed**，
`tests/unit_tests/agent_teams/test_fork.py` **86 passed**；Swarm `test_shared_conversation_runtime.py` **50 passed**。
未运行格式化或静态检查，未提交、未推送。

## 2026-11-21 05:46（Asia/Singapore）：宿主决定式压缩与共享记录

用户确认后实施（开发会话，2026-11-20）。

起因：合入上游 develop（`fbe93360f`）后，core 新增宿主决定式压缩（`ContextEngine.compact_context` 与 `compaction.py`
的协调器）：先在草稿上压缩，宿主 hook 决定接受、改写或拒绝，接受后才安装并保存。上游目前只开放接口，
原生自动调用点仍是注释（`TODO(compact)`）；Swarm 的 `session_compaction` 扩展会调用它，但尚未接到在线 Session。
该设计隔离了草稿的 buffer、processor、Session 写入和暂存文件，但不知道共享记录这一处写入，因此在共享窗口上有两个问题：

- 草稿由 `copy.copy` 得到，仍带着原 context 的共享绑定；本特性“主动压缩结束时写回”的规则在草稿上同样生效，
  未经宿主接受的候选被写进共享记录。
- 安装后的收尾保存经 `save_contexts` 调用 `save_conversation`，后者再次获取 `compact_context` 已持有的 processor 锁
  （不可重入），等到上游 30 秒上限后以 `COMPACT_COMMIT_UNCERTAIN` 失败，该 context 此后拒绝写入。

改动前在真实入口上复现（SQLite 持久化 store，四条消息的共享窗口，脚本化的摘要模型）：

| 场景 | 改动前 | 改动后 |
|---|---|---|
| 宿主拒绝候选 | 在线窗口不变，但共享记录已是摘要；下一次装载得到被拒绝的摘要 | 记录与在线窗口都不变 |
| precompute 进行中用户追加一条消息并保存 | precompute 结束时用旧四条的摘要覆盖较新的记录，重启后该消息丢失 | 记录保留五条消息 |
| 宿主接受候选 | 30.0 秒后 `COMPACT_COMMIT_UNCERTAIN`，下一条消息被拒绝 | 立即返回，记录写入一次，可继续追加 |

改动（两处，`context.py` 与 `context_engine.py`，共 +22 / -6 行）：

- `configure_compaction` 建立草稿时清除 `_conversation_binding` 及其装载状态。草稿上的写回因此为空操作；
  安装步骤（`restore_compaction_state`）只带回 offload 与 processor 状态，不涉及绑定。
- `save_conversation` 增加 keyword-only `processor_lock_held`（默认 False，原行为不变），为 True 时在调用方已持有的锁内直接
  `_commit_conversation()`；`save_contexts` 透传该参数。`compact_context` 的收尾保存与自动桥接
  （`_intercept_auto_compaction`，其两个被注释的调用点都位于持锁区间内）传 True。

未采用：对共享窗口直接返回 `COMPACT_UNSUPPORTED`。本分支上每个新建的原生模式 Session 都使用共享记录，
那样等于在上游启用该能力后对几乎所有新 Session 关闭它。

行为说明：`compact_context` 在 processor 锁内抛出异常时，沿用本特性已有规则，该共享 context 需重新装载后才能继续使用。

验证：新增 `tests/unit_tests/core/context_engine/test_shared_conversation_compaction.py`（内存与 SQLite 两种 provider，8 例）：
被拒绝的候选不进入记录；precompute 不能覆盖较新的记录；被接受的候选只保存一次、窗口可继续使用、另一执行者读到安装后的窗口；
压缩之外的保存仍等待 processor 锁。还原产品代码后其中 6 例失败（含两例各等待 30 秒）。
`tests/unit_tests` 全量（Linux / Python 3.12；本机无法导入的 `agent_rl/online/test_ppo_executor.py` 与
`message_queue/test_wire_formats.py` 两个文件除外）：**28585 passed, 18 failed**。18 项失败在不含本次改动的同一提交上同样失败
（`extensions/message_queue` 14 项、`agent_rl/online/test_sft_primitives.py` 2 项、`harness_providers/test_codex.py` 1 项、
`agent_teams/test_pending_approval_native.py::test_parallel_reply_survives_the_state_clear_window` 1 项，后者是本分支的既有问题）。
Swarm 以本地 core 运行压缩相关、共享对话、模式切换与快照用例共 **430 passed**。
未覆盖：把内容写入文件的压缩（共享记录只接受 provider 自有的 offload）；自动桥接在上游仍是关闭状态，未能实际运行。
未运行格式化或静态检查，未提交、未推送。

## 2026-11-21 16:50（Asia/Singapore）：未绑定窗口沿用上游的审批清除顺序

`ReActAgent` 带待审批中断恢复时，上游的顺序是先清除中断状态、再装载 context。本特性的首个 feat 提交把清除挪到了装载之后：
共享记录装载失败（记录缺失或读取失败）时，审批仍可回答。但该顺序对所有 Session 生效，包括没有共享绑定的窗口。
上游用例 `tests/unit_tests/agent_teams/test_pending_approval_native.py::test_parallel_reply_survives_the_state_clear_window`
在装载期间暂停并断言中断状态已清除，因此在本分支上失败（上一节 28585 passed / 18 failed 中标为“本分支的既有问题”的那一项）。
产品行为没有受损——轮次进行中到达的回复在两种顺序下都会被挂起——但未绑定窗口没有理由偏离上游。

改动（`react_agent.py`，+17 / -5 行）：按 `session.get_conversation_binding()` 是否返回绑定区分。
未绑定窗口先清除再装载，与上游一致；已绑定窗口在装载成功后清除，保持本特性原有行为。

未采用：修改上游用例去适应新顺序。那会把只有共享窗口需要的差异变成所有 Session 的行为变化。

验证（Linux / Python 3.12）：上述上游用例不经修改通过；审批、中断与共享对话相关用例
（`test_pending_approval_native.py`、`test_pending_approval_mailbox.py`、`agent_teams/harness`、`agent/react_agent`、
`test_shared_conversation.py`、`test_shared_conversation_compaction.py`、`persistent_agent/test_window_assembly.py`）**605 passed**。
Swarm 以本地 core 运行共享对话、模式切换与权限用例 **2455 passed**。
未运行格式化或静态检查。
