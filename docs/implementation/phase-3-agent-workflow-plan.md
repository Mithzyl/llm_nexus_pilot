# 阶段3：Agent Runtime 与完整工作流节点结果规划

**文档日期：** 2026 年 8 月 24 日
**文档状态：** 已完成（`model_only_v1` 同步 Agent Runtime 已实现并通过自动化验证）
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)
**前端事件依据：** [`phase-4-web-ui-plan.md`](../frontend/phase-4-web-ui-plan.md)

> 本文记录阶段3的最终实施事实。`model_only_v1` 已具备同步执行、最多两个无依赖工作 Agent 并行、显式取消、费用预留、实时服务器发送事件（SSE）、持久化事件回放和完整节点查询。工具循环属于阶段7恢复后的新执行配置，跨进程恢复属于阶段10，遥测装配属于阶段6，不再作为阶段3完成条件。

> 2026 年 8 月 19 日路线图重排：本能力由原阶段5调整为阶段3；实现范围和完成状态不变。

本文统一使用以下口径：

- **当前实现**：以 2026 年 8 月 13 日的 Schema、迁移、Service、Router 和自动化测试为依据，说明现在可调用、可持久化和已验证的行为。
- **目标态**：阶段3完整验收或后续 `tool_enabled_v1` 需要增加的行为；目标态字段和接口不得被描述为当前已可用。

## 目标

- 要实现的结果：使用普通异步 Python 编排 Controller、工作 Agent、确定性验证、独立审核和最终汇总，不以 LangGraph Graph API 作为平台核心。
- 要实现的结果：每一个工作流节点都返回版本化、类型明确、字段完整、可持久化、可查询的 `AgentWorkflowNodeResult`，而不是只返回一个自由格式字符串。
- 要实现的结果：同步请求和结果查询返回整个工作流及全部节点结果；SSE 只发送同一持久化事实的有界公共投影、节点 ID 和详情路径，前端通过查询接口读取完整节点合同，不从事件文案猜测状态。
- 要实现的结果：模型调用、Agent Run、Agent Turn、Task、Handoff、Evaluation、Artifact 和工作流节点之间具有稳定引用关系；费用、token、错误和证据可聚合但不重复保存原始事实。
- 要实现的结果：循环、节点数、并发、token、费用、时长、上下文和输出均有硬上限；取消、失败和未知结果具有确定状态。
- 明确不处理：RabbitMQ、后台 Worker、服务重启自动恢复、无人确认的长期后台任务、自动发布代码、Memory Packet、L1～L4 自动注入和 Knowledge。
- 明确不处理：在节点结果中保存模型隐藏推理、完整思维链、完整系统提示词、供应商密钥、未脱敏原始响应或无限制工具输出。

## 当前事实

- 阶段2 Model Gateway、Context Builder、Prompt/Model Catalog 和 Evaluation 可以独立调用。当前工作流已复用 Model Gateway 的 `ModelInvocationService`、Model Attempt 与费用事实，但上下文节点尚未调用 Context Builder 或 Prompt Catalog，Evaluation 也由工作流直接写入现有表。
- `llm_runs`、`llm_tasks`、`llm_attempts`、`llm_evaluations` 和 `llm_artifacts` 已保存主要业务事实。
- Memory 实验性代码已有 `llm_agent_runs`、`llm_agent_turns`、L0 Working State、L2 Handoff 和 Run Snapshot。阶段3已新增实际 Agent 执行协调器、工作流/节点/事件持久化表和统一节点结果合同；Memory Packet 与 Knowledge 仍不参与工作流上下文组装。
- `AgentRun` 表示一个角色对一个 Task 的完整执行；`AgentTurn` 只表示其中一次模型或工具循环。Controller 规划、计划校验、任务分派、验证、审核和汇总不能全部冒充 Agent Turn。
- 阶段10 RabbitMQ 和阶段7 Tool Runtime 均已暂停。阶段3第一实施配置只能使用模型调用和确定性程序节点，不提供文件、Shell、Git、MCP 或外部工具能力。
- 阶段4已实现第一版 `model_only_v1` 接入：前端消费 Workflow 创建/发现/结果、Node 和事件有限回放接口，按事件序号恢复并通过节点查询显示完整结果；显式取消、并行组和费用预留字段的前端适配已经完成，真实 Provider 端到端验收仍未完成，工具视图等待阶段7稳定合同。
- 阶段6 OpenTelemetry 尚未实施，但优先级已提高。阶段3必须提前固定 trace/span 关联字段，用户可见运行事实仍保存在 MySQL，不能由遥测数据反推业务状态。

## 当前实施结果（2026 年 8 月 13 日）

| 能力 | 当前状态 | 已验证行为与边界 |
|---|---|---|
| `model_only_v1` 主流程 | 已实现 | 请求归一化、上下文组装、Controller 规划、计划校验、Task/Agent Run 分派、工作 Agent、Handoff、确定性验证、可选 Reviewer、最终汇总和完成节点按普通异步 Python 顺序执行 |
| 完整节点结果 | 已实现 | 所有节点使用 `agent_workflow_node_result.v1` 信封，包含输入引用、类型化输出、分支、预算、证据、错误和生命周期；详情与完整结果可查询 |
| 数据库事实 | 已实现 | 新增 Workflow、Node、Event 表；补充 Agent Run/Turn 到 Workflow/Node 的引用；Run 目前最多创建一个 Workflow，节点键和节点尝试具有数据库唯一约束 |
| 依赖与交接 | 已实现 | Controller 返回的有向无环任务图先由程序校验；无依赖的工作 Agent 最多两个并行，每个 Provider 调用使用独立数据库会话；依赖 Task 的 Worker 输出与 Handoff 会显式传给下游 Agent |
| 审核门禁 | 已实现 | 确定性验证失败不能被 Reviewer 覆盖；无错误级发现且不要求重试或重新规划的 `needs_revision` 进入最终汇总并应用修改，明确失败、错误级发现、重新规划或重试要求会阻止最终汇总；Evaluation 保留候选 Attempt 与 Task 的正确归属 |
| 最终消息一致性 | 已实现 | Assistant Message 与最终汇总节点的完成事件使用同一事务；节点合同或持久化失败会回滚尚未提交的消息，避免失败工作流留下成功答案 |
| HTTP 与 SSE | 已实现 | POST 支持同步 JSON 或提交后实时 SSE；GET 支持 Workflow、Result、Node 列表/详情和按序号回放已持久化事件；显式取消接口可阻止后续节点；GET `/events` 按设计只做有限回放 |
| 幂等和失败收敛 | 已实现核心范围 | 完全相同的幂等请求返回原 Workflow；不同请求或同一 Run 的第二个 Workflow 返回冲突；超时、取消和模型结果校验失败会终结已经启动的 Node、Task、Agent Run 与 Agent Turn，避免遗留虚假的 `running` 状态 |
| 资源限制 | 已实现 | Run 请求最长 100,000 字符；节点数、模型调用数、工作流总时长、单次模型超时和单节点输出大小均有限制；`max_parallel_agents` 范围为 `1`～`2`；并发调用使用原子费用预留避免超卖 |
| 工具、Memory 与 Knowledge | 明确禁用 | `implementer`、工具、文件写入、命令、网络及外部副作用在 `model_only_v1` 计划校验时被拒绝；Memory 和 Knowledge 不自动注入 |

阶段3边界说明：

- `POST /agent-workflows/{workflow_execution_id}/cancel` 是协作式取消：立即持久化取消事实并阻止后续节点；已经进入供应商网络边界的请求不会伪装成从未发送，返回后只做事实收敛。
- `model_only_v1` 没有等待用户输入的分支，因此不提供没有实际状态来源的 `resume` 接口。需要请求结束后继续、进程重启恢复或跨进程执行时恢复阶段10。
- 类型化节点公共结果保持 64 KiB 硬上限，超限明确失败。模型网关已经把原始供应商请求和响应保存到 MinIO；不会把公共节点合同静默改成另一种 Artifact 形状。
- POST SSE 用于当前请求的实时事件；GET `/events` 按设计提供有限、可重复的持久化回放，前端刷新后再读取 Summary/Result，不增加第二套持续订阅协议。
- Agent Runtime 已完成执行职责划分：`workflow_execution_service.py` 负责创建工作流、管理同步或 SSE 使用的数据库会话并返回结果；`model_only_workflow_orchestrator.py` 决定 `model_only_v1` 的节点执行顺序和审核分支；Provider 调用和类型化输出校验由 `agent_model_node_service.py` 负责；Workflow、Node、Event、预算、Task、Agent Run 和 Agent Turn 的状态写入，以及成功、失败、取消时的相关状态收尾，均由 `workflow_execution_state_service.py` 负责。
- Workflow Completion 节点、Workflow/Run 成功终态及两个完成事件使用同一数据库事务；实时观察者只能在该事务提交后收到这两个事件。
- OpenTelemetry 仍由阶段6装配；阶段3只保留稳定关联字段。真实供应商冒烟测试需要部署环境密钥，是可选验证，不作为离线自动化完成条件。

## 阶段3首版能力边界

### `model_only_v1` 执行配置

阶段7暂停期间，阶段3先交付不依赖工具的完整工作流：

```text
用户请求
  ↓
请求归一化（确定性）
  ↓
上下文组装（当前仅使用 Run 请求；消息、Prompt 与 Artifact 引用为目标态）
  ↓
Controller 规划（模型）
  ↓
计划校验（确定性）
  ↓
创建 Task / Agent Run 并分派
  ↓
      工作 Agent 执行（模型；无依赖任务最多两个并行）
  ↓
Handoff 校验与提交
  ↓
确定性验证
  ↓
必要时独立 Reviewer
  ↓
Controller 最终汇总
  ↓
完整 AgentWorkflowResult
```

首版角色：

| 角色 | 首版能力 | 明确限制 |
|---|---|---|
| `controller` | 目标理解、是否拆解、任务依赖、汇总 | 不亲自读取仓库或执行工具 |
| `planner` | 根据显式上下文形成实施或研究计划 | 不能声称检查了未提供的文件 |
| `researcher` | 分析请求中明确提供的文本、Artifact 摘要和证据 | 无代码搜索工具，不允许生成虚假文件证据 |
| `reviewer` | 独立读取目标、候选结果、确定性检查和证据引用 | 不继承实现模型隐藏上下文，不修改结果 |
| `verifier` | 预留角色，当前不创建模型节点 | 首版由确定性程序节点直接验证 Handoff 和持久化引用，不把程序检查交给模型 |
| `implementer` | 不启用 | 阶段7写入、命令、Git 和隔离 Workspace 恢复前拒绝创建该角色任务 |

- Controller 输出包含工具或代码修改要求时，计划校验当前返回 `agent_capability_unavailable` 并使工作流失败；未来实现等待输入后才允许进入 `waiting_for_input`。不能创建一个假 Implementer 后返回空成功。
- 阶段7恢复后新增 `tool_enabled_v1` 工作流定义，复用相同节点结果信封，并增加工具节点输出；不能在 `model_only_v1` 中静默改变行为。
- 阶段3以 `model_only_v1` 作为可独立交付能力。阶段7恢复后新增 `tool_enabled_v1` 并完成工具循环、写隔离与程序测试集成；该新增能力不回退阶段3的完成状态。

## 核心实体边界

```text
Run
└── AgentWorkflowExecution
    ├── AgentWorkflowNodeExecution（编排节点事实）
    │   ├── Task（需要执行的业务工作）
    │   ├── AgentRun（一个角色执行一个 Task）
    │   │   └── AgentTurn（模型/工具循环步骤）
    │   │       ├── ModelAttempt
    │   │       └── ToolCall（阶段7恢复后）
    │   ├── AgentHandoff（跨 Agent 可依赖结果）
    │   ├── Evaluation（审核或确定性检查事实）
    │   └── Artifact（大型输出或证据）
    └── AgentWorkflowEvent（用户可恢复的有序业务事件）
```

- `AgentWorkflowExecution` 是一次 Run 使用某个版本化工作流定义的执行事实，不代替 Run。
- `AgentWorkflowNodeExecution` 记录编排步骤的输入引用、完整类型化输出、分支和生命周期，不代替 Agent Turn、Attempt、Tool Call 或 Evaluation。
- `AgentRun`、`AgentTurn` 的表和含义继续保留，但代码职责应从 Memory 实验目录迁移到 Agent Runtime；L0 Working State 仍由 Memory 模块引用，不复制表或创建第二套状态。
- Handoff 继续是不可变的 L2 协作事实。阶段3可以显式把依赖 Task 的 Handoff 作为下一节点输入，但不启用 Memory Packet，也不自动注入 L1、L3、L4 Memory。
- 用户可见事件属于 MySQL 业务事实；OpenTelemetry span 属于阶段6遥测。两者通过 ID 关联，但任何一方都不替代另一方。

## 工作流定义

### 当前实现

- `workflow_name`、`workflow_version` 和 `execution_profile` 由请求 Schema 限定为 `model_only`、`1.0.0` 和 `model_only_v1`。
- 协调器使用普通异步 Python 的 `if`/`for` 和明确函数调用按顺序执行，不使用 LangGraph Graph API。
- 节点输出由 `NODE_OUTPUT_MODELS` 按 `output_type` 映射到唯一 Pydantic 类型，写入前和读取后都会重新校验。
- `workflow_definition_registry.py` 已登记 `model_only/1.0.0/model_only_v1` 的固定节点和按任务生成的节点，应用导入该模块时会检查重复定义、未知输出类型和未知后续节点。
- 创建工作流时必须找到完全匹配的名称、版本和执行配置；节点开始前校验 `node_key`、`node_type` 和 `output_type`，节点完成前校验实际后续节点及跳过节点是否属于允许连接。
- `model_only_workflow_orchestrator.py` 使用普通异步 Python 的 `if`、`for` 和明确函数调用决定节点顺序、计划拒绝、是否审核，以及审核意见进入最终修改或终止工作流的分支。

### 后续目标

工作流定义使用代码注册表和普通 Python 控制流：

```python
class AgentWorkflowNode(Protocol):
    async def execute(
        self,
        context: AgentWorkflowNodeContext,
    ) -> AgentWorkflowNodeResult:
        ...
```

- 节点使用稳定 `node_key`、`node_type` 和 `node_version` 注册；函数重命名不能改变已持久化语义。
- 工作流定义登记表拒绝重复的工作流名称、版本和执行配置组合，并检查节点输出类型和允许连接。
- 工作流定义版本固定节点集合、允许转移和输出 Schema；执行开始后不能切换到 `latest`。
- Python `if`、`for`、`asyncio.TaskGroup` 和明确函数调用表达分支与并行，不把状态隐藏在字符串边关系中。
- 节点函数只返回结果，不自行决定 HTTP 表现；协调器负责状态转换，状态持久化组件负责事务和事件。

## 完整节点返回合同

### 通用信封：`AgentWorkflowNodeResult`

每个节点通过 POST JSON、Node 详情和 Result 查询返回以下全部字段。字段不适用时返回 `null`、空数组或空对象，序列化时不得省略；数据库保存同一有界事实，SSE 只发送其公共投影、节点 ID 和详情路径：

```json
{
  "schema_version": "agent_workflow_node_result.v1",
  "workflow_execution_id": "uuid",
  "workflow_name": "model_only",
  "workflow_version": "1.0.0",
  "node_execution_id": "uuid",
  "node_key": "controller_planning",
  "node_type": "model",
  "node_version": "1.0.0",
  "node_sequence": 3,
  "node_attempt": 1,
  "parent_node_execution_id": null,
  "run_id": "uuid",
  "task_id": "uuid-or-null",
  "agent_run_id": "uuid-or-null",
  "agent_turn_id": "uuid-or-null",
  "agent_role": "controller-or-null",
  "status": "completed",
  "input": {
    "schema_version": "controller_planning_input.v1",
    "input_hash": "sha256-hex",
    "source_node_execution_ids": [],
    "message_ids": [],
    "artifact_ids": [],
    "handoff_ids": [],
    "context_build_id": null,
    "prompt_release_id": null
  },
  "output_type": "controller_plan",
  "output_schema_version": "controller_plan_output.v1",
  "output": {},
  "transition": {
    "selected_transition": "validate_plan",
    "next_node_keys": ["plan_validation"],
    "skipped_node_keys": [],
    "condition_summary": "Plan is valid and requires decomposition"
  },
  "evidence": {
    "model_attempt_ids": [],
    "tool_call_ids": [],
    "evaluation_ids": [],
    "artifact_ids": [],
    "handoff_ids": []
  },
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cached_tokens": 0,
    "estimated_cost": "0",
    "model_call_count": 0,
    "tool_call_count": 0
  },
  "budget": {
    "cost_limit": null,
    "cost_used_before": "0",
    "cost_used_after": "0",
    "remaining_cost": null,
    "remaining_model_calls": 0,
    "remaining_nodes": 0,
    "remaining_wall_time_ms": 0
  },
  "timing": {
    "started_at": "RFC3339",
    "completed_at": "RFC3339-or-null",
    "duration_ms": 0
  },
  "public_view": {
    "status_label": "正在规划",
    "summary": "Controller created a validated task plan",
    "progress_current": 3,
    "progress_total": 11
  },
  "observability": {
    "trace_id": "hex-or-null",
    "span_id": "hex-or-null"
  },
  "warnings": [],
  "error": null,
  "created_at": "RFC3339"
}
```

### 合同约束

- `output` 必须是按 `output_type + output_schema_version` 判别的 Pydantic 类型，不允许任意 `dict` 直接穿透。
- `input` 只保存精确资源引用、版本和 hash，不复制完整 Prompt、消息、Handoff 或文件内容。
- `evidence` 只保存既有业务事实 ID；token、费用和时长聚合自 Attempt/Tool Call，不代替原表。
- `transition.condition_summary` 允许保存简短、可公开的规则依据，不保存模型逐步推理或隐藏思维链。
- `public_view` 由后端根据节点事实确定性生成，不能让模型自行宣称进度或成功。
- `error` 固定包含 `error_code`、`error_type`、`public_message`、`is_retryable`、`outcome_is_known` 和 `details_reference_id`；成功时为 `null`。
- 当前 `64 KiB` 检查只对校验后的节点 `output` JSON 做 UTF-8 字节计数，并未计算整个信封；超限会明确失败。目标态需分别限制完整 HTTP 信封与事件投影。后续大型正文、完整报告和大量证据应保存为 Artifact，并让 `output` 返回 Artifact ID、hash、MIME 和安全预览。“完整参数”不等于无限内联内容。
- 内部与授权 API 返回所有合同字段；敏感值从形成时就不进入合同，而不是读取时临时删除。

## 各节点完整输出参数

### 1. `request_intake` → `RequestIntakeOutput`

```text
normalized_objective
request_type
complexity
requires_decomposition
constraints[]
acceptance_criteria[]
explicit_assumptions[]
clarification_questions[]
required_capabilities[]
unavailable_capabilities[]
requested_output_format
language
```

- 当前这是确定性节点：使用 Run 请求构造结果，`complexity` 固定为 `complex`、`requires_decomposition` 固定为 `true`，不调用小模型；用户原文仍由 Run 保存。
- `explicit_assumptions` 只能列出协调器允许继续使用的公开假设，不能保存隐藏推理。
- 目标态在存在阻塞性问题时转移到 `waiting_for_input`；当前还没有该分支或恢复接口。

### 2. `context_assembly` → `ContextAssemblyOutput`

当前 Schema 字段：

```text
included_run_id
included_message_ids[]
included_artifact_ids[]
included_handoff_ids[]
excluded_sources[] {source_type, source_id, reason}
input_character_count
is_truncated
memory_packet_id
```

- 当前实现仅将 `Run.user_request` 作为 Controller 上下文；消息、Artifact 和 Handoff ID 列表初始为空，`memory_packet_id` 固定为 `null`。
- 目标态再接入 Context Builder 和 Prompt Catalog，增加 `context_build_id`、`prompt_release_id`、token 估算和裁剪证据；这些字段当前不在 `ContextAssemblyOutput` 中。
- 目标态中单个结构化来源不能从中间截断；排除或裁剪必须返回证据。

### 3. `controller_planning` → `ControllerPlanOutput`

当前 Schema 字段：

```text
decision_summary
tasks[]
  task_key
  task_type
  title
  objective
  assigned_role
  required_capabilities[]
  expected_output_type
  completion_criteria[]
  priority
  timeout_seconds
  max_model_calls
dependencies[] {task_key, depends_on_task_key, dependency_type}
review_policy
known_risks[]
unknowns[]
```

- 当前计划没有 `plan_id`、直达模式、并行组、独立预算或 `verification_plan`。
- Controller 只能使用请求已绑定的 `planner`/`researcher`；`review_policy` 必须与请求一致；`model_only_v1` 出现 write、execute、network 或外部副作用能力时计划不得通过。
- `decision_summary` 是简短决定依据，不要求模型输出思维链。

### 4. `plan_validation` → `PlanValidationOutput`

当前 Schema 字段：

```text
is_valid
checks[] {check_id, status, error_code, message, related_task_keys[]}
topological_task_order[]
cycle_paths[]
total_task_count
total_model_call_limit
capability_gaps[]
relationship_errors[]
rejected_plan_reason
```

当前确定性检查覆盖重复 Task Key、审核策略篡改、角色/能力不可用、未知或自依赖、DAG 环、最大依赖深度 6、Task 数、模型调用数与节点数。输出类型和完成条件由 Pydantic Schema 校验；工作 Agent 执行组再按依赖完成情况和 `max_parallel_agents` 形成。

### 5. `agent_dispatch` → `AgentDispatchOutput`

```text
created_tasks[] {task_key, task_id, status}
created_agent_runs[] {task_id, agent_run_id, agent_role, provider, model, status}
dispatch_groups[] {group_id, agent_run_ids[], concurrency_limit}
blocked_tasks[] {task_key, task_id, reason_code, summary}
skipped_tasks[] {task_key, reason_code}
role_model_bindings[] {agent_role, provider, model}
```

- Task、Agent Run 和依赖关系必须在明确事务边界创建；重复 idempotency key 返回原有映射。
- 当前按有向无环任务图生成一个或多个 `worker-group-{sequence}`。同组无依赖 Task 的 `concurrency_limit` 最大为 `2`；依赖 Task 先保存为 `waiting_for_dependency`，开始前再次校验所有前置 Task 已完成。
- 父级工作流会话只负责依次提交 Task、Agent Run、Node、Turn 和 Handoff 事实；每个并行 Provider 调用通过数据库 session factory 获取独立 `AsyncSession`，不会把一个会话交给多个协程。

### 6. `agent_model_execution` → `AgentModelExecutionOutput`

```text
agent_turn_id
model_attempt_id
provider
model
response_kind
text_preview
text_artifact_id
structured_output
requested_tool_calls[]
finish_reason
provider_request_id
input_tokens
output_tokens
cached_tokens
estimated_cost
latency_ms
transport_attempt_count
capability_warnings[]
```

- `response_kind` 当前固定为 `structured`；`structured_output` 包含 `summary`、`confirmed_facts[]`、`decisions[]`、`remaining_work[]`、`risks[]` 和 `unknowns[]`。
- `requested_tool_calls` 的 Schema 当前限制为空列表；模型仍请求工具时返回 `agent_capability_unavailable`，不能把 Tool Call 当成已经执行。`text_artifact_id` 已预留但 model-only 当前不生成文本 Artifact。
- 原始供应商响应继续由 Attempt 的对象引用保存，节点结果只返回标准化字段和引用。

### 7. `handoff_submission` → `AgentHandoffOutput`

```text
agent_handoff_id
schema_version
status
objective
confirmed_facts[]
decisions[]
files_read[]
files_changed[]
artifacts[]
tests[]
remaining_work[]
risks[]
unknowns[]
invariants_for_next_agent[]
supersedes_handoff_id
```

- 复用已有 `agent_handoff.v1`，不创建另一套交接 JSON。
- `agent_handoff.v1` 保持 1000 个 UTF-8 字节上限。Worker 结构化输出超过该上限时，工作流按字段轮转生成限长 Handoff 投影，并在 `invariants_for_next_agent` 记录完整 Worker 输出所在的源节点；完整结果继续保存在 `agent_model_execution` 节点，不通过放宽 Memory 合同或静默丢弃源结果解决。
- 首版没有工具时 `files_read`、`files_changed` 和真实测试证据通常为空；模型不能仅凭描述填入不存在的文件或测试。

### 8. `deterministic_verification` → `DeterministicVerificationOutput`

```text
verdict
checks[]
  check_id
  check_type
  status
  expected
  actual
  evidence_refs[]
  error_code
blocking_findings[]
non_blocking_findings[]
verified_claims[]
unverified_claims[]
coverage_summary
requires_independent_review
```

- 程序检查不调用模型；没有真实工具和测试结果时必须把相关声明放入 `unverified_claims`。
- 任何阻塞检查失败都不能被后续模型 Reviewer 改写成程序验证通过。

### 9. `independent_review` → `IndependentReviewOutput`

```text
verdict
score
findings[]
  finding_id
  severity
  category
  description
  evidence_refs[]
  required_action
accepted_claims[]
rejected_claims[]
missing_evidence[]
requires_replan
requires_retry
review_summary
evaluation_ids[]
reviewer_task_id
reviewer_agent_run_id
reviewer_model_attempt_id
```

- Reviewer 只读取用户目标、任务要求、候选结果、确定性检查和明确证据，不读取实现模型隐藏消息或 Working State。
- 当前为每个候选 Worker Attempt 创建一条 Evaluation，因此返回 `evaluation_ids[]`，不是单个 `evaluation_id`。
- Reviewer 输出不能覆盖候选结果。当前 `fail`、`needs_revision`、`requires_replan=true` 或 `requires_retry=true` 都会以 `agent_review_rejected` 终止工作流；目标态才创建一次有界修订 Task/Attempt/Node Execution。

### 10. `final_synthesis` → `FinalSynthesisOutput`

```text
answer_type
final_text
completed_objectives[]
unresolved_items[]
warnings[]
recommended_next_actions[]
source_agent_run_ids[]
source_handoff_ids[]
source_artifact_ids[]
source_evaluation_ids[]
assistant_message_id
```

- 最终文本必须区分已验证、模型判断和未确认事项。
- 当前没有 `final_text_artifact_id`；文本最长 16,000 字符，超限时输出合同失败，`source_artifact_ids` 在 model-only 路径为空。
- Run 有 `session_id` 时，Assistant Message 会先以 `commit=False` 暂存，再与 `final_synthesis` 节点完成事件同一事务提交。当前 Message 暂存失败会使工作流失败，尚未降级为“节点成功但 Message 带警告”。

### 11. `workflow_completion` → `WorkflowCompletionOutput`

```text
workflow_status
final_node_execution_id
node_count
node_counts_by_status
task_ids[]
agent_run_ids[]
model_attempt_ids[]
tool_call_ids[]
evaluation_ids[]
artifact_ids[]
handoff_ids[]
total_input_tokens
total_output_tokens
total_cached_tokens
total_estimated_cost
total_duration_ms
remaining_model_calls
final_output_reference
warnings[]
errors[]
```

该节点只聚合既有节点证据和使用量，不重新生成业务内容。当前 Schema 不包含 `remaining_budget`，只返回 `remaining_model_calls`；费用剩余量由节点 `budget` 投影表达。目标态需增加聚合值与 Attempt 明细的独立一致性检查。

### 阶段7恢复后的 `tool_execution`

未来节点的 `output` 必须直接采用阶段7 `ToolExecutionResult`：工具名/版本、权限决定、状态、结构化输出、Artifact、stdout/stderr 摘要、退出码、耗时、截断和稳定错误。阶段3不复制工具权限或执行实现。

## 工作流级返回合同

同步完成或最终结果查询返回 `AgentWorkflowResult`：

```text
workflow_execution_id
run_id
workflow_name
workflow_version
execution_profile
status
version
snapshot_version                 # 本次完整快照版本
current_stage
primary_node_execution_id
active_node_execution_ids[]
model_call_count
max_model_calls
node_count
max_nodes
started_at
completed_at
created_at
updated_at
nodes[]                         # 完整 AgentWorkflowNodeResult，按 sequence 排序
final_output                    # FinalSynthesisOutput 或 null
completion                     # WorkflowCompletionOutput 或 null
aggregate_usage
warnings[]
error
trace_id
```

- `POST` 同步执行成功时返回全部节点结果；最大节点数受硬上限约束。
- `GET /agent-workflows/{id}/result` 是刷新和审计时获取完整节点参数的权威入口；运行中也返回已经提交的完整节点及当前状态。
- 节点列表接口支持 cursor 分页，但 result 接口在节点硬上限内一次返回整个执行快照和 `snapshot_version`，避免客户端拼出不一致版本。
- 节点 `output` JSON 以 64 KiB 上限保持有界，超限时明确失败，不截断结构化 JSON，也不把公共返回合同静默替换成另一种 Artifact 形状。原始供应商请求和响应继续由 Model Attempt 引用对象存储中的审计对象。

## 状态机

### 当前工作流状态转换

```text
创建成功 → running
running → completed | failed | cancelled | outcome_unknown
```

### 当前节点状态转换

```text
创建成功 → running
running → completed | failed | cancelled | outcome_unknown
```

- `pending`、`waiting_for_input`、`skipped` 和 `blocked` 已在枚举中保留，但当前执行路径不会生成这些事实。
- Provider 超时、网络/连接错误或无法确认的内部 Provider 错误会使当前节点和 Workflow 进入 `outcome_unknown`；父 Run 因现有 `RunStatus` 没有未知态，当前仍记为 `failed`。
- POST SSE 的执行任务在响应生成器结束时会传播取消，并收敛已启动的 Node、Task、Agent Run 和 Agent Turn；独立 HTTP 取消接口可以在连接之外持久化取消终态并阻止后续节点。
- API 重启后不会自动恢复运行中工作流；启动审计和显式恢复也尚未实现，因此遗留 `running` 事实是当前已知缺口，不能声称已经转成 `outcome_unknown`。

### 目标态补充

- `waiting_for_input` 必须保存等待原因、允许操作和版本，由带 `expected_workflow_version` 的显式恢复请求转回 `running`。
- `skipped` 必须返回分支原因和选中的替代转移；`blocked` 表示依赖、能力或用户输入尚不满足，不得当作内部错误。
- 模型已接收请求但响应未持久化时，节点和 Attempt 必须保留未知结果证据，不得自动重新计费调用。

## 持久化规划

### `llm_agent_workflow_executions`

```text
workflow_execution_id
run_id
workflow_name
workflow_version
execution_profile
status
version
snapshot_version
current_stage
primary_node_execution_id
active_node_execution_ids_json
role_bindings_json
request_json
review_policy
idempotency_key
request_hash
max_nodes
max_model_calls
model_call_count
max_parallel_agents
wall_time_limit_ms
event_count
total_estimated_cost
trace_id
error_code
error_json
started_at
completed_at
created_at
updated_at
```

- 当前数据库对 `run_id` 建立唯一约束，因此一个 Run 最多对应一个 Workflow Execution；幂等 key 和请求 hash 用于判断是原请求回放还是冲突。

### `llm_agent_workflow_node_executions`

保存完整节点信封的可查询索引和有界 JSON：

```text
node_execution_id
workflow_execution_id
run_id
node_key
node_type
node_version
node_sequence
node_attempt
parent_node_execution_id
task_id
agent_run_id
agent_turn_id
agent_role
status
input_schema_version
input_hash
input_json
output_type
output_schema_version
output_json
transition_json
evidence_json
usage_json
budget_json
public_view_json
warnings_json
error_code
error_json
trace_id
span_id
started_at
completed_at
duration_ms
created_at
```

- 当前同时约束 `(workflow_execution_id, node_sequence)` 和 `(workflow_execution_id, node_key, node_attempt)` 唯一；`parent_node_execution_id`、`task_id`、`agent_run_id` 和 `agent_turn_id` 均已有外键。

### `llm_agent_workflow_events`

用户界面恢复使用追加式业务事件：

```text
event_id
workflow_execution_id
run_id
node_execution_id
event_sequence
event_type
workflow_status
node_status
public_payload_json
occurred_at
public_summary
trace_id
```

- `(workflow_execution_id, event_sequence)` 唯一；事件保存和对应状态转换在同一 MySQL 事务提交。
- 事件只包含节点结果的有界公共投影或节点 ID；当前 `agent.node.completed` 返回节点详情查询路径，不内联完整节点信封，查询接口始终是权威事实。
- OpenTelemetry exporter 故障不能阻止业务事件提交；MySQL 事件也不能被当成遥测 span 导出队列。

### 现有实体调整

- `llm_agent_runs` 已增加 `workflow_execution_id`，并继续使用现有 provider/model 和生命周期字段；角色配置版本、独立预算和错误摘要尚未增加。现有 `(task_id, agent_role)` 唯一约束仍不支持同一 Task 创建第二个同角色 Agent Run，因此当前没有 Agent 返工轮次。
- `llm_agent_turns` 已增加 `node_execution_id`，仍只记录模型/工具 Turn；输入/输出引用继续由 Node 与 Model Attempt 保存，Turn 当前没有独立的 `cancelled` 或 `outcome_unknown` 状态。
- `llm_attempts`、`llm_tool_calls`、`llm_evaluations` 和 `llm_artifacts` 通过 node evidence 引用，不复制原始结果。
- 迁移复用现有 Agent 表，不创建 `agent_runs_v2`、`workflow_tasks` 或第二套 Handoff。

## HTTP 与 SSE 接口

当前已实现：

```http
POST /api/v1/runs/{run_id}/agent-workflows
GET  /api/v1/runs/{run_id}/agent-workflow
GET  /api/v1/agent-workflows/{workflow_execution_id}
GET  /api/v1/agent-workflows/{workflow_execution_id}/result
GET  /api/v1/agent-workflows/{workflow_execution_id}/nodes
GET  /api/v1/agent-workflows/{workflow_execution_id}/nodes/{node_execution_id}
GET  /api/v1/agent-workflows/{workflow_execution_id}/events
POST /api/v1/agent-workflows/{workflow_execution_id}/cancel
```

当前不提供：

```http
POST /api/v1/agent-workflows/{workflow_execution_id}/resume
```

`resume` 只有在新的版本化工作流定义真正进入 `waiting_for_input` 时才加入，不能为当前没有等待分支的执行配置提供空动作。

### 创建与同步执行

当前请求合同包含：

```text
workflow_name
workflow_version
execution_profile
idempotency_key
role_bindings（必须包含 controller、至少一个 planner/researcher；review_policy 不是 never 时必须包含 reviewer）
review_policy
max_nodes
max_model_calls
max_parallel_agents（范围 1～2，默认 1）
wall_time_limit_ms
stream
```

其中只有 `idempotency_key` 和 `role_bindings` 没有默认值；工作流名称、版本与执行配置当前固定为 `model_only`、`1.0.0` 和 `model_only_v1`。`review_policy` 默认为 `always`；`reviewer` 在策略不是 `never` 时必填，`verifier` 可以绑定但当前执行路径不会创建该模型节点。`max_nodes` 默认和最大值均为 `32`，按审核策略最小为 `10` 或 `11`；`max_model_calls` 默认和最大值均为 `16`，按审核策略最小为 `3` 或 `4`；`wall_time_limit_ms` 允许 `1,000`～`600,000`，默认 `600,000`。当前没有 `expected_run_version` 或通用 `budget_overrides` 字段，Run 级唯一约束阻止同一 Run 创建第二个工作流。

- `idempotency_key` 长度必须为 `8`～`128`；`stream` 默认 `false`。
- `role_bindings` 只接受 `controller`、`planner`、`researcher`、`reviewer` 和 `verifier` 键。每个绑定包含 `provider`、`model`、`structured_output_mode`、`timeout_seconds` 和可选的 `max_output_tokens`；结构化输出模式默认为 `native_schema`，超时默认 `60` 秒且范围为 `1`～`600`。平台不提供默认业务输出上限，字段缺省时不主动缩短输出；显式 `max_output_tokens` 的统一允许范围为 `1`～`65536`。具有费用预算的 Run 为了在 Provider 调用前完成保守费用预留，仍必须显式提供 `max_output_tokens` 和模型价格；无预算 Run 可以保持缺省。

- `stream=false` 已在同一 HTTP 生命周期同步运行并返回完整 `AgentWorkflowResult`。
- `stream=true` 已使用 SSE 实时发送提交后的业务事件；完成事件提供权威结果查询路径，节点事件提供节点 ID、类型和详情查询路径。完整、有界 `AgentWorkflowNodeResult` 由 Node 详情或 Result 接口返回。
- GET `/runs/{run_id}/agent-workflow` 用于发现该 Run 唯一关联的 Workflow；创建前查询返回资源不存在，不创建隐式工作流。
- GET `/nodes` 使用绑定 Workflow 的 sequence cursor 分页，`limit` 默认 `50`，允许 `1`～`100`；其他 Workflow 的 cursor 会被拒绝。
- GET `/events` 使用 `after_sequence` 或合法的 `Last-Event-ID` 回放已经持久化的后续事件；非法游标返回输入错误，不会静默从头回放。
- 新建 Workflow 的 POST 返回 HTTP `201`，即使业务结果是 `failed` 或 `outcome_unknown`；完全相同的幂等回放返回 `200`，请求校验、资源不存在和冲突分别沿用 `422`、`404` 和 `409`。
- `stream` 是传输选择，不参与请求 hash；相同幂等 key 可以在 JSON 与 SSE 表现之间回放同一个持久化 Workflow，不会再次调用模型。
- 创建请求超时后不能无条件自动创建新请求；调用方应使用原 idempotency key 重试或通过 Run 发现接口查询原 Workflow。
- 当前最终用户身份未完成，接口仍处于受信调用方边界；阶段5完成后必须从 principal 派生 Run owner。

### 事件枚举

当前执行路径实际产生：

```text
agent.workflow.started
agent.workflow.completed
agent.workflow.failed
agent.workflow.cancelled
agent.workflow.outcome_unknown
agent.node.started
agent.node.completed
agent.node.failed
agent.node.cancelled
agent.node.outcome_unknown
```

目标态已经预留但当前不会产生：

```text
agent.workflow.waiting_for_input
agent.node.skipped
agent.node.blocked
agent.budget.updated
```

事件合同共同包含：

```text
event_id
event_sequence
workflow_execution_id
run_id
node_execution_id
event_type
workflow_status
node_status
occurred_at
public_summary
public_payload
trace_id
```

`agent.node.completed` 通过节点 ID 和查询路径引用完整结果。`waiting_for_input`、`skipped`、`blocked` 和独立预算更新事件必须在相应行为实现后才允许产生。超过节点结果内联上限时明确失败，不能截断 JSON、自动改变节点合同或用 Artifact 引用伪装成完整结构化结果；原始供应商响应继续由 Model Attempt 指向 MinIO 审计对象。

## 并行和数据库会话

### 当前实现

- `max_parallel_agents` 允许 `1`～`2`。程序先根据已验证依赖图形成执行组；只有无未完成依赖且处于同组的工作 Agent 才并行。
- 非流式执行使用 FastAPI `yield` 注入的请求级 `AsyncSession`；流式执行在响应体生命周期内创建执行级 `AsyncSession`。并行工作 Agent 的 Provider 调用各自再创建独立短生命周期数据库会话。
- 节点开始事实与开始事件在调用 Provider 前提交；模型 HTTP 调用不包在数据库事务中。Model Attempt、Handoff 和节点状态仍按现有 Service 的多个持久化检查点提交。
- 同组调用使用 `asyncio.TaskGroup`。已经进入供应商边界的同组调用会分别记录结果或错误，程序先持久化可确认的成功事实，再用失败节点终止工作流，不把已计费成功结果丢成无证据状态。
- 父级验证节点只有在全部必需工作 Agent 和 Handoff 到达终态后开始。

## 预算与循环限制

第一版当前硬上限与后续目标：

| 限制 | 当前值 | 作用与剩余要求 |
|---|---:|---|
| 单工作流节点数 | 默认/最大 32；最小 10 或 11 | 不审核时最小 10，其他审核策略最小 11；防止无限分支 |
| Controller 生成 Task 数 | 8 | 防止任务爆炸 |
| 最大依赖深度 | 6 | 防止递归编排 |
| 并行 Agent 数 | 1～2 | 只并行无依赖的模型工作节点，每个调用使用独立数据库会话 |
| 每 Agent 实际 Model Turn | 1 | 当前没有工具循环或 Agent 内重试；Controller 计划合同也只接受字面值 `1`，不会接受无法执行的多轮预算 |
| 单工作流模型调用 | 默认/最大 16；最小 3 或 4 | 不审核时最小 3，其他审核策略最小 4；控制费用 |
| Reviewer 返工轮次 | 0 | 非阻断的 `needs_revision` 由最终汇总一次性应用；明确失败、错误级发现、要求重试或重新规划时终止工作流，不启动新的 Worker 返工轮次；未来最多允许 1 次返工 |
| 同步总时长 | 默认/最大 600 秒；最小 1 秒 | 匹配当前无 Worker 边界 |
| 单节点 `output` JSON | 64 KiB | 类型化输出超限明确失败；公共合同不自动切换为 Artifact |
| 单事件 public payload | 固定小型投影 | 事件只包含节点 ID、类型、状态、错误摘要和权威查询路径，不内联节点完整输出或供应商响应 |

- Run 存在 `budget_limit` 时，每次调用按 UTF-8 输入字节上界、结构化输出 Schema、消息框架余量和最大输出 token 计算保守费用；模型没有显式价格配置时在进入 Provider 前拒绝。
- 并行节点通过数据库条件更新原子增加 `model_call_count` 和 `reserved_estimated_cost`。只有当前实际费用加全部在途预留仍不超过 Run 上限时才能调用；完成或失败后释放临时预留，`cost_used` 只保留已确认实际估算费用。
- 达到任一硬上限时工作流以稳定边界结果结束，不能让模型自行申请绕过。

## 错误合同

### 当前实现

当前执行路径和计划校验会产生以下稳定代码：

```text
node_output_invalid
agent_capability_unavailable
agent_plan_invalid
agent_review_policy_mismatch
agent_dependency_cycle
agent_budget_exhausted
agent_budget_output_limit_required
agent_budget_price_unavailable
agent_node_limit_exceeded
agent_model_call_limit_exceeded
agent_verification_failed
agent_review_rejected
agent_workflow_timeout
agent_workflow_cancelled
agent_provider_outcome_unknown
agent_workflow_internal_error
```

- Provider 错误保留 Model Gateway 的 `error_type` 作为 `error_code`；`timeout`、`network_error`、`connection_error`、`internal_error` 和 `response_persistence_error` 当前会让 Node 与 Workflow 进入 `outcome_unknown`，并强制 `is_retryable=false`。`response_audit_failed` 表示 Provider 响应及费用事实已确认、但原始响应审计对象未保存，因此进入已知 `failed`；其他 Provider 错误也进入 `failed`。
- Handoff 的确定性检查项可以使用 `agent_handoff_invalid`，但当前工作流终态错误代码是 `agent_verification_failed`，不能把前者描述为工作流对外终态错误。
- Run 预算无法覆盖保守费用预留时会在 Provider 调用前返回 `agent_budget_exhausted`；预算 Run 没有显式 `max_output_tokens`、因而无法计算最大费用时返回 `agent_budget_output_limit_required`；模型缺少显式价格时返回 `agent_budget_price_unavailable`。三种情况都不会进入 Provider 调用。
- 请求字面值、必填角色、范围和未知字段由 Pydantic 返回 HTTP `422`；资源不存在和幂等/Run 状态冲突沿用平台现有 HTTP `404`/`409` 错误合同，不转换为节点错误。
- 节点输出不符合 Schema 时保留已经保存的 Attempt，并以 `node_output_invalid` 失败；不会把自由文本强制塞入结构化字段。

### 目标态补充

以下代码只有在相应 Registry、等待/恢复、费用预留或持久化恢复能力实现后才能加入当前合同：

```text
workflow_not_registered
workflow_version_not_available
workflow_profile_not_available
workflow_state_conflict
node_contract_invalid
node_input_unavailable
agent_role_not_registered
agent_waiting_for_input
agent_result_persistence_failed
```

- Schema、能力、预算和依赖错误不可重试；Provider 临时错误只沿用 Model Gateway 的有限重试，逻辑 Agent 节点不能再套一层无界重试。
- 模型结果已经产生但节点持久化结果无法确认时，目标态必须进入 `outcome_unknown` 并禁止自动重复计费。

## 阶段6可观测性协同

阶段6优先级已经提高，但独立阶段规划尚未开始。本阶段只提前固定不可逆的关联边界，使后续阶段6能够装配遥测而不改写业务合同；业务正确性不等待 exporter：

```text
agent.workflow              # 根 span
├── agent.node              # 每个 Node Execution
│   ├── model.generate      # Model Attempt
│   ├── evaluation.execute
│   └── tool.execute        # 阶段7恢复后
└── mysql.* / object_store.*
```

- `workflow_execution_id`、`node_execution_id`、`run_id`、`task_id`、`agent_run_id`、`attempt_id` 作为低风险关联标识；完整 Prompt、Node output、工具参数、Handoff 和用户文件不进入 span attribute。
- trace/span ID 写入节点事实仅用于跳转关联，不作为状态、顺序或恢复依据。
- OpenTelemetry Generative AI 语义约定仍可能变化；阶段6必须固定所采用的语义约定版本，并用平台稳定字段映射，不能把实验性属性名变成数据库公共合同。
- 当前代码尚未提供 `WorkflowTelemetry` 或等价端口，只有可空的 trace/span 关联字段。目标态先抽象无操作遥测端口，再由阶段6装配 SDK、exporter、采样、指标和跨服务传播。
- 关键指标规划：工作流/节点数量与耗时、各状态数量、模型调用和 token/费用、预算终止、等待输入、审核拒绝、未知结果和事件恢复缺口。

OpenTelemetry 官方将 traces、metrics、logs 作为不同信号，并说明 Python traces/metrics 稳定而 logs 仍在开发；阶段6首批优先 trace 和 metrics。Generative AI 语义属性包含潜在敏感内容警告，因此阶段3节点正文只留在受控业务存储。

## 项目目录现状与后续调整

```text
apps/api/src/nexuspilot_api/features/agent_runtime/
├── models/
│   └── agent_workflow.py
├── routers/
│   └── agent_workflows.py
├── schemas/
│   └── agent_workflows.py
├── services/
│   ├── agent_model_node_service.py
│   ├── model_only_workflow_orchestrator.py
│   ├── workflow_definition_registry.py
│   ├── workflow_execution_service.py
│   ├── workflow_execution_state_service.py
│   ├── workflow_policy.py
│   └── workflow_query_service.py
└── dependencies.py
```

- 当前先按业务功能保留在 API 的 `agent_runtime` feature 中。Router 只处理 HTTP；查询服务组装读取结果；策略模块校验 Controller 计划；工作流定义登记表校验节点名称、类型和允许连接；状态服务负责数据库事务；模型节点服务负责 Provider 调用；执行服务目前仍决定节点顺序和审核分支。
- 暂不为了未来可能的第二个消费者创建 `packages/agents` 平行实现。出现 API 之外的真实消费者后，再提取不依赖 FastAPI、SQLAlchemy、具体 Provider SDK 或 Memory ORM 的纯工作流包。
- 现有 Agent Run/Turn 表仍由 Memory feature 定义，但已经通过外键关联 Workflow/Node。后续迁移职责时必须保持表名和外部关系，不创建第二套 Agent Run/Turn。

### 工作流执行职责调整结果

调整前 `workflow_execution_service.py` 为 2,335 行。2026 年 8 月 13 日完成职责调整后，该文件为 178 行，只负责工作流创建、请求或 SSE 数据库会话以及结果读取；`model_only_workflow_orchestrator.py` 负责当前模型工作流的节点顺序、各节点业务动作和审核分支。状态服务负责提交工作流状态以及成功、失败和取消时的相关状态收尾，模型节点服务负责 Provider 调用和类型化输出校验。阶段7恢复后的工具工作流必须使用独立执行配置，不能把工具循环继续写入 `model_only_workflow_orchestrator.py`。

最小拆分边界：

```text
Router
  ↓
workflow_execution_service.py            # 接收创建和执行请求，并管理一次请求使用的数据库会话
  ↓
model_only_workflow_orchestrator.py       # 使用普通 Python 决定节点执行顺序和分支
  ├── workflow_execution_state_service.py # Workflow/Node/Event/Task/Turn/Evaluation/Handoff 状态与事务
  └── agent_model_node_service.py         # Provider 调用、结构化输出校验和模型节点结果
             ↓
      workflow_execution_state_service.py # 只提供状态读写，不反向依赖工作流编排服务
```

拆分必须保持以下运行语义：

- 工作流状态、节点和 Handoff 仍由一个父级执行会话按顺序提交；只有同组并行 Provider 调用各自创建独立 `AsyncSession`，不会把父级会话交给多个协程。
- Node started 状态和 `agent.node.started` 事件必须先提交，再调用可能计费的 Provider；事件只能在对应数据库事务提交后推送给实时 SSE observer。
- `ModelInvocationService` 和 Handoff 提交仍保留现有耐久检查点；上层主要传递 ID，跨提交边界后按需重新读取 ORM，不长期持有过期实体。
- 当前执行 Service 为请求/执行级对象，因此其可变 `event_sink` 尚不会跨请求共享；拆分后仍必须把它作为每次执行的构造参数或执行上下文，禁止提升为单例或让并行 Workflow 相互覆盖。此项必须在提高并行度前完成。
- `workflow_execution_state_service.py` 不依赖模型节点服务或工作流编排服务；`agent_model_node_service.py` 可以调用状态服务，但状态服务不得反向调用 Provider。

调整过程保持小步验证：阶段3定向测试已经包含事件“提交后通知”、Provider“开始事件提交后调用”、终态事务可见性、原子费用预留、并行数据库会话和显式取消；状态写入、工作流结束时的状态收尾、模型调用、版本化工作流定义登记、运行时节点连接检查、节点顺序和审核分支均已具有明确负责文件。后续扩展新工作流定义时继续运行 Agent Workflow 测试、完整 API 测试和 Ruff，不为每个只包含一次简单调用的节点创建一个 Service 文件。

## 2026 年 8 月 24 日工作流 Token 缓存优化规划

本工作项是阶段3完成后的性能与费用优化，不更改阶段3“已完成”状态，也不改变模型输出、证据合同或审核语义。当前代码已经从 OpenAI 兼容、Anthropic 和 Gemini 响应中归一化读取 `cached_tokens`，并聚合到 Workflow 用量；尚未形成按 Provider、Model、节点角色和稳定前缀分析的命中率基线，也没有统一的显式缓存请求策略。DeepSeek 和 OpenAI 兼容调用当前主要依赖供应商自动前缀缓存；Anthropic 适配器只读取 `cache_read_input_tokens`，尚未发送 `cache_control`；Gemini 适配器只读取 `cachedContentTokenCount`，尚未创建或引用显式缓存对象。

供应商事实以官方文档为准：

- [DeepSeek 上下文缓存](https://api-docs.deepseek.com/guides/kv_cache/)默认启用并按重复前缀命中，响应提供命中与未命中 Token；命中属于尽力而为，不能作为业务正确性的前置条件。
- [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)要求精确前缀匹配，建议把稳定指令、示例、工具和结构化输出 Schema 放在前面，把用户输入、请求标识和时间戳放在后面；不同模型存在最低可缓存前缀长度。
- [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)支持顶层自动缓存和内容块显式断点，缓存前缀顺序为 tools、system、messages；默认生存时间为 5 分钟，显式断点放在变化内容之后会持续写入却无法复用。
- [Gemini Context Caching](https://ai.google.dev/gemini-api/docs/caching)对 Gemini 2.5 及更新模型默认启用隐式缓存；显式缓存依赖具体 API，并非所有 Gemini 调用接口都支持。

### 当前需要先验证的基线

- 以一次模型调用为最小统计单位，记录 Provider、Model、工作流定义版本、节点角色、输入 Token、缓存命中 Token、首 Token 延迟和总耗时；不得用跨模型、跨节点角色的单一平均值判断优化效果。
- 统一基础命中率口径为“缓存读取 Token / 供应商归一化后的总输入 Token”，同时保留供应商原始用量证据。OpenAI 兼容与 Gemini 可从总 Prompt Token 中计算；Anthropic 的 `input_tokens`、缓存读取和缓存创建字段语义不同，必须先将三者归一化后再计算，不能直接使用当前 `cached_tokens / input_tokens`。供应商提供缓存写入或未命中 Token 时，先增加兼容读取与诊断，不修改既有 `cached_tokens` 含义。
- 对同一模型和节点角色执行冷请求、等待首个响应开始后的相同稳定前缀请求、仅改变动态后缀请求三组样本；缓存未达到供应商最低长度、缓存尚未建立或已过期时必须单独标记，不能归类为序列化缺陷。
- 生成安全的前缀指纹用于比较相邻请求首次分歧位置。指纹不得保存 Prompt 正文、用户内容、密钥或可逆缓存键，原始 Provider 证据继续遵循现有 MinIO 和脱敏边界。

### 稳定前缀与请求合同设计

- 保持稳定的系统指令、节点输出合同、示例、工具定义和共享参考材料在动态内容之前；用户输入、Workflow/Run/Task/Attempt 标识、时间戳和节点结果等变化内容放在后缀。仅用于日志的标识移出 Prompt，不以改变模型语义为代价追求缓存。
- 对提示式 JSON 中由平台生成的 Schema 投影和结构化模型输入使用确定性序列化：键顺序、数组顺序、空白和数字表现保持稳定。不得重排有语义的对话消息、证据列表或用户文本。
- 为稳定指令与 Schema 建立明确版本；内容修改应有意生成新版本和新指纹，禁止为了维持命中而继续发送已经失效的合同。
- 先用现有自动缓存验证稳定前缀收益。只有供应商能力目录声明支持且真实样本证明有收益时，才扩展 Provider 无关的缓存策略合同，并分别映射 Anthropic 缓存断点或 Gemini 显式缓存；工作流层不得按 Provider 名称拼接私有字段。
- 显式缓存必须定义作用域、生存时间、费用、失效和删除责任。缓存写入失败或未命中只能回退为普通模型输入，不能改变节点成功条件、权限判断、审核结论或最终回答。

### 测试与完成条件

- 先新增失败基线测试：语义相同但平台生成 JSON 键顺序不同会产生不同前缀指纹；实现确定性序列化后，稳定输入生成相同前缀，动态后缀变化不会改变稳定前缀指纹。
- Provider 合同测试覆盖：自动缓存不增加私有请求字段；能力不支持时不发送显式缓存配置；支持时只由适配器映射；命中、未命中、缓存创建和缺失用量都不会被误报为零成本或业务失败。
- 工作流回归覆盖 Controller、Worker、Reviewer 和最终汇总的输出合同、用量聚合、审核与最终消息，证明缓存优化不改变语义和持久化事实。
- 使用至少一个自动缓存 Provider 完成真实重复请求对照，记录稳定前缀长度、命中 Token 和首 Token 延迟；Anthropic 或 Gemini 显式缓存只有在对应凭据和已注册模型能力可用时作为可选集成验证。
- 完成条件：能够定位低命中来自前缀过短、前缀变化、TTL、供应商路由或能力不支持中的哪一类；相同 Provider/Model/节点角色的稳定前缀命中率可查询；优化前后语义回归通过；没有新增敏感 Prompt 日志。未获得可重复收益时保留自动缓存和诊断能力，不引入显式缓存的资源生命周期复杂度。

实施顺序：

| 步骤 | 修改对象 | 预期结果 | 验证方式 | 状态 |
|---|---|---|---|---|
| 1 | 现有 Attempt/Workflow 用量读取与诊断 | 得到按 Provider、Model、节点角色分组的冷/热请求基线，并纠正跨供应商总输入口径 | 固定样本单元测试、原始用量对照和本地查询 | 未开始 |
| 2 | Agent 稳定前缀和平台生成 JSON 序列化 | 静态指令与 Schema 在前，动态内容在后；相同语义的稳定前缀字节一致 | 先行失败测试、前缀指纹快照和工作流语义回归 | 未开始 |
| 3 | Model Gateway 能力目录与 Provider 适配器 | 只在能力明确且有收益时增加 Provider 无关缓存策略，由适配器映射供应商字段 | Provider 请求合同、能力拒绝和用量归一化测试 | 未开始 |
| 4 | 真实供应商对照与启用决定 | 记录命中、延迟、费用和失效行为；无可重复收益时不启用显式缓存 | 重复请求集成测试、敏感字段扫描和结果等价评估 | 未开始 |

## 测试设计

公共节点合同、状态、预算、并发和幂等必须测试先行。

### 当前已验证

2026 年 8 月 13 日已在 `apps/api` 目录运行 `.venv/bin/pytest tests/test_agent_workflows.py tests/test_agent_runtime_architecture.py tests/test_workflow_definition_registry.py -q`，结果为 `39 passed`。当前定向测试覆盖：完整串行主流程和持久化节点、真实并发信号、并行会话隔离、Run 到 Workflow 发现、幂等回放与冲突、能力拒绝、调用前预算和价格拒绝、并发费用预留、有序事件回放、依赖 Handoff、Evaluation 归属、非法 Worker 输出清理、实时 SSE、显式取消及重复取消、Reviewer 绑定/跳过/拒绝/重试、完成原因校验、最终消息事务回滚、终态事务可见性、Provider 超时与请求中取消的未知结果、非法事件游标、OpenAPI JSON/SSE 合同、未注册输出版本、Handoff 所属 Task 校验，以及节点开始/事件事实先提交后调用 Provider 或通知 SSE observer 的事务顺序。

工作流定义登记另有 4 项单元测试，覆盖固定节点和按任务生成的节点、重复工作流定义、未知输出类型、未知连接目标、运行时未知节点、节点类型不一致和非法节点连接。

2026 年 8 月 13 日运行完整后端测试为 `212 passed, 4 skipped`，Model Provider 包测试为 `28 passed`；4 项跳过项是需要真实 MySQL/MinIO 环境变量的基础设施测试。`ruff check` 与本阶段修改文件的格式检查均通过。Alembic 当前唯一 head 为 `20260813_0011`，面向 MySQL 的 `alembic upgrade head --sql` 离线迁移生成通过，并包含工作流费用预留字段。真实供应商调用需要部署环境密钥，保持为可选冒烟验证，不把外部供应商可用性作为离线自动化完成条件。

2026 年 8 月 20 日补充回归确认：真实运行曾产生 3352 字节 Handoff，并因既有 1000 字节合同在 `handoff_submission` 节点失败。新增超长 Worker 输出回归后，工作流会保存完整 Worker 节点并生成不超过合同上限、可追溯源节点的 Handoff 投影；`tests/test_agent_workflows.py` 与 L2 Handoff 上限定向测试共 33 项通过，原有独立 Handoff 超限拒绝行为保持不变。

2026 年 8 月 20 日补充回归确认：DeepSeek prompted JSON 曾仅依赖 Schema 提示词，真实 Worker 响应虽以 `finish_reason=stop` 完成，却因末尾数组缺少 `]` 而产生 `node_output_invalid`。当前 Agent 调用使用独立的 `json_object_output` 合同，DeepSeek 适配器映射为官方 `response_format: {"type":"json_object"}` 并在 Provider 边界解析；节点层继续执行原有 Pydantic Schema 校验。普通 JSON object 与原生 JSON Schema 仍是两个能力，不允许静默降级。真实浏览器回归已完成 10 个节点和 3 次模型调用，刷新后最终消息与节点事实均可恢复。

2026 年 8 月 20 日补充合同修复：后续真实 Worker 响应已经是完整 JSON，却把 Schema 元数据 `additionalProperties` 误写成业务字段 `additional_properties`，因严格合同禁止额外字段而失败。修复位于 Provider 无关的 `prompted_json` 指令构造层：提示明确列出唯一允许的顶层响应字段，声明 Schema 关键字不是响应字段，并从面向模型的 Schema 投影中移除 `additionalProperties` 与 `title` 元数据；原始 Pydantic 合同及 `extra="forbid"` 校验保持不变。该行为不按 Provider 名称分支，所有不具备原生 JSON Schema、使用提示式 JSON 的模型共享同一协议。Agent 工作流 38 项定向测试通过，其中新增回归覆盖字段白名单、Schema/响应边界、元数据剔除和普通解释任务的知识边界。

2026 年 8 月 21 日补充知识边界修复：统一 Agent 指令原先要求 `Use only the supplied content`，导致普通解释问题也被错误当成必须提供外部资料的证据任务；Worker 只能报告资料缺失，Reviewer 因目标未回答而拒绝。当前 Provider 无关策略将已提供内容视为权威上下文，并允许普通解释问题使用模型已有的通用知识；任务明确限制来源时仍必须遵守，且任何模型都不得伪造工具、文件、测试、网络请求或外部验证事实。Playwright 使用真实 DeepSeek 对同一问题完成 15 个节点和 6 次模型调用，三个 Worker、Reviewer、最终汇总和消息关联全部成功，最终回答无需刷新即可见。后端与模型适配层全量验证为 `264 passed, 4 skipped`，Ruff 全量检查通过。

2026 年 8 月 24 日补充审核门禁修复：Reviewer 返回 `needs_revision` 且不存在错误级发现、重试或重新规划要求时，工作流不再丢弃候选回答，而是把完整审核意见交给 Final synthesis 应用后生成最终消息；明确 `fail`、错误级发现、重试或重新规划仍会阻止汇总。定向回归覆盖上述四类分支，结果为 `4 passed`。

以下各节保留阶段3回归边界和后续执行配置扩展时需要补充的测试方向；阶段10的进程恢复、阶段7的工具执行、阶段6的遥测装配和阶段5的用户权限测试不属于阶段3未完成项。

### 节点合同

- 每个 `output_type` 正常、空列表、nullable 字段、额外字段、缺失字段、错误类型和字节上限。
- 所有节点返回通用信封全部字段；POST、GET node 和 GET result 使用同一完整节点类型。SSE 只需验证其投影 ID、状态和详情路径指向同一持久化节点，不要求内联完整信封。
- 大正文转换为 Artifact 引用后仍返回完整参数；不产生中途截断 JSON。
- 检查禁止字段：chain-of-thought、scratchpad、完整 prompt、Authorization、API Key 和原始凭据不得进入节点结果。

### 状态、幂等和恢复

- 合法/非法状态转换、重复创建、重复节点完成、取消竞争、等待输入版本冲突和终态保护。
- 同一 idempotency key 同内容返回原结果，不同内容返回 409。
- 模型成功后数据库失败进入未知结果；显式恢复不重跑不可确认节点。
- API 重启后运行中节点被审计为未知结果，不伪造恢复完成。

### 计划和分派

- 目标态的简单任务直达，以及复杂任务 DAG、未知角色、依赖环、任务上限、深度上限、能力不可用和预算超分配。当前 Request Intake 固定按复杂任务拆解，不存在直达分支。
- `model_only_v1` 拒绝 implementer、tool、write、execute、network 和 external side effect。
- Task/Agent Run 创建部分失败全部回滚；并行节点各自使用独立数据库会话。

### 验证、审核和汇总

- 程序验证失败不能被 Reviewer 改成通过；无证据声明进入 unverified。
- Reviewer 只读明确输入，不读取 Worker Working State；无错误级发现且不要求重试或重新规划的 `needs_revision` 交由 Final synthesis 应用，明确失败、错误级发现、重新规划或重试要求直接终止工作流，尚未实现 Worker 返工轮次。
- Final synthesis 引用正确 Handoff/Evaluation/Artifact；Message 保存失败不再次调用模型。
- 汇总 totals 与 Attempt 明细不一致时明确失败，不使用默认零值。

### API、SSE 和前端恢复

- 同步返回所有完整节点；节点 sequence 稳定；结果快照版本一致。
- SSE 乱序、重复、断开、事件过大降级为查询引用、最终事件缺失和 after-sequence 恢复。
- 浏览器刷新后从 MySQL 事件和 result 恢复，不从计时器猜测进度。
- 当前只验证 Workflow、Node、Task 与 Handoff 的资源归属关系；用户身份尚未实现。阶段5接入 principal 后再增加跨用户查询和创建拒绝测试。

### 可观测性

- trace/span ID 关联节点、Attempt 和 Evaluation；exporter 故障不改变工作流结果。
- span attribute 扫描不含 Prompt、完整输出、工具参数、用户文件或 secret。
- 采样不会影响 MySQL 业务事件完整性。

## 实施步骤

| 步骤 | 修改对象 | 当前状态 | 预期结果 | 验证方式 |
|---|---|---|---|---|
| 1 | Node Result、各节点 Output 和 Workflow Result 合同测试 | 已完成 | 类型化输出、未注册版本拒绝、API 序列化和大小限制已验证；不为每个无独立规则的字段机械复制测试 | Pydantic、API 序列化和大小限制测试 |
| 2 | 工作流/节点/事件状态与迁移设计 | 已完成离线门禁 | 状态、唯一性、引用、版本约束和费用预留字段可执行；真实 MySQL 作为部署前环境验证 | Alembic head、MySQL 离线 SQL、SQLite 集成测试和可选真实 MySQL 升级 |
| 3 | 工作流定义登记、预算和节点编排职责调整 | 已完成 | 状态服务、模型节点服务、成功/失败/取消状态收尾、版本化工作流定义、原子费用预留和节点顺序职责均已实现 | 架构依赖、工作流定义、工作流执行、预算并发测试和 Ruff 检查 |
| 4 | 请求、上下文、Controller plan 和 plan validation | 已完成当前执行配置 | Controller 统一产生可验证任务图；简单请求由一个工作 Agent 执行，不增加绕过节点记录的隐藏直达路径 | 假 Provider、任务图、能力和预算测试 |
| 5 | Task/Agent Run 分派与 model-only 工作 Agent | 已完成 | 依赖分组、最多两个工作 Agent 并行、每个 Provider 调用独立会话及 Attempt 关联已实现 | 分派、依赖输入、Attempt、真实并发信号和费用预留冲突测试 |
| 6 | Handoff、确定性验证和 Reviewer | 已完成 | 下游只消费有效证据，审核独立且可阻断汇总 | Handoff/Evaluation 归属、审核拒绝和错误收敛测试 |
| 7 | Final synthesis、completion 和 Assistant Message | 已完成 | 最终消息与汇总节点一致提交；完成节点、Workflow/Run 终态和两个完成事件使用一个事务；全量聚合可查询 | 消息回滚、终态原子可见性和汇总查询测试 |
| 8 | HTTP/SSE、result 快照和恢复事件 | 已完成 | POST 实时事件、完整结果、持久事件回放和幂等显式取消可用；当前工作流没有等待状态，因此不提供空 `resume` 动作 | ASGI、SSE sequence、实时流、游标、非法游标、取消竞争与 OpenAPI 合同测试 |
| 9 | 阶段6遥测关联边界 | 已完成本阶段边界 | trace/span 字段和敏感内容边界已固定；实际 exporter 由阶段6装配 | Schema 和持久化字段检查 |
| 10 | `model_only_v1` 基础设施与供应商验证 | 已完成离线门禁 | 快速测试、迁移链和 MySQL 离线 SQL 可验证；真实供应商调用必须使用部署环境密钥，保持可选 | 完整后端测试、Alembic 检查、可选供应商冒烟 |
| 11 | 阶段7恢复后的 Tool Runtime 集成 | 不属于阶段3完成条件 | 新 `tool_enabled_v1` 复用结果合同和隔离边界 | 阶段7工具循环、写隔离、取消和未知结果测试 |

## 失败与恢复设计

### 当前实现

- 节点开始事实和开始事件提交成功后才调用 Provider；提交失败时不进入模型调用。
- Provider 的超时、网络/连接错误、无法确认的内部错误或请求中取消会使当前 Node 与 Workflow 进入 `outcome_unknown`；已启动的 Task、Agent Run 和 Agent Turn 被收敛到其现有可表达的终态。
- 节点输出校验失败、计划拒绝、验证失败，以及 Reviewer 的明确失败、错误级发现、重新规划或重试要求会终止当前 Workflow；非阻断的 `needs_revision` 进入最终汇总，不再被当作异常丢弃回答。
- SSE observer 在事件提交后收到通知；当前 `event_sink` 仍是执行服务上的可变字段，通知自身失败还没有独立隔离合同。
- Final synthesis 的 Assistant Message 与最终汇总节点完成事件共用事务；测试已验证最终节点无法持久化时不会遗留成功消息。
- 当前没有跨请求继续、等待输入、resume 或启动审计。同步请求所在进程意外退出仍可能留下 `running` 事实；该问题需要阶段10的可靠执行与恢复机制，不能由 API 进程内后台任务安全解决。

### 目标态

- 模型返回成功但节点结果持久化无法确认时，保存可确认的 Attempt 关联并进入 `outcome_unknown`，不得自动重试计费调用。
- 并行组部分失败时，已进入供应商边界的调用分别收敛；成功结果先保存 Attempt、Node 与 Handoff，随后由失败节点终止工作流。父级验证不会在必要子节点结束前开始。
- Context、Prompt、Evaluation 或 Artifact 服务接入后不可用时，返回明确依赖错误，不用空数据伪装完整上下文。
- 未来版本若新增等待用户输入，必须同时定义 `expected_workflow_version`、输入幂等 key 和恢复测试；当前版本不预留一个没有运行语义的接口。
- 阶段10恢复后，启动审计必须识别进程中断遗留的 `running` 节点，并依据是否存在不可确认外部调用决定失败、取消或未知结果；不得伪造恢复完成。

## 风险与停止条件

- 节点返回合同若仍允许任意自由 `dict`、省略公共字段或把大型文本无限内联，停止实现并先修正 Schema。
- 要求展示模型隐藏思维链、系统 Prompt、完整 Provider 原始响应或敏感工具参数时拒绝实施；改为可公开决定摘要和证据引用。
- 阶段7暂停期间要求 Implementer、文件修改、Shell、Git 或 MCP 时，返回能力不可用，不能在 API 进程临时执行命令绕过工具层。
- 共享一个 `AsyncSession` 给并行 Agent、在模型 HTTP 调用期间持有数据库事务或允许并行超卖预算时，停止实现。
- 需要请求结束后继续、自动崩溃恢复或跨进程 Agent 时，必须重新评估阶段10，不使用 `BackgroundTasks` 假装可靠执行。
- OpenTelemetry 实验性 GenAI 属性发生变化时，只调整阶段6映射，不改变节点数据库合同。

## 完成标准

### `model_only_v1` 与阶段3

当前状态：已完成。自动化测试已经验证主流程、完整节点合同、核心 HTTP/SSE、幂等、有界并行、原子费用预留、显式取消、审核门禁、最终消息回滚、终态一致提交、超时和未知结果。

- Controller、Planner/Researcher、确定性验证、Reviewer 和最终汇总可在同步请求内完成。
- 每个执行节点返回完整 `AgentWorkflowNodeResult`，所有节点特有输出通过判别联合校验。
- POST、节点详情和 result 查询返回一致的完整节点合同；SSE 返回可恢复公共投影和权威详情路径；result 可以返回该工作流全部节点参数。
- 计划、并行、预算、固定单轮 Worker 限制、取消、失败和未知结果通过测试；当前定义没有等待输入或恢复分支。
- 不读取 Memory Packet/Knowledge，不执行工具，不伪造文件、测试或外部证据。
- 业务事件保存在 MySQL，trace/span 关联字段已经预留；阶段6装配 exporter 时必须验证故障不影响运行。
- 两个无依赖模型工作 Agent 可以有界并行。写任务、工具调用和独立 Workspace 仍明确不可用，等待阶段7以新的版本化执行配置实现。
- Model Attempt、Handoff、Evaluation 和 Node Result 均能从 `run_id` 查询并相互关联；阶段7加入 Tool Call、Artifact 和程序测试证据时沿用同一引用方式。

## 官方依据

- [OpenTelemetry Signals](https://opentelemetry.io/docs/concepts/signals/)：区分 traces、metrics、logs 和 baggage，阶段3据此不把业务事件与遥测混为一体。
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)：当前 Python traces 和 metrics 稳定，logs 仍在开发，阶段6首批优先 traces/metrics。
- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)：确认手工创建嵌套 span、属性、事件、错误和指标的装配方式。
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)：语义约定提供公共命名，但实验性约定可能变化，数据库合同不能直接依赖不稳定字段名。
- [OpenTelemetry GenAI attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)：工具参数和结果等属性可能包含敏感信息，不默认写入 span。
