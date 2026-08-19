# NexusPilot LLM Platform 总体规划

**文档日期：** 2026 年 8 月 19 日
**文档状态：** 当前总体规划（权威入口）
**当前实施焦点：** 阶段1～3已完成；阶段4前端继续实施，优先形成稳定可用的内部服务；随后完成阶段5身份与凭据、阶段6可观测性，再进入阶段7工具、阶段8代码搜索和阶段9 MCP；阶段10异步执行仅在出现可靠后台任务需求时恢复

## 目标

- 建设一个具有稳定数据底座、可独立复用 LLM 核心能力、可靠异步执行和受控 Agent 工作流的平台。
- 为后续 Web 或其他业务应用提供可查询、可恢复、可审计的 API，而不是只打通一条 Agent 演示流程。
- MySQL 保存业务事实；MinIO 保存大型内容；消息队列负责通知和调度，不成为唯一状态源。
- 默认多模型投票、自训练模型路由器、Temporal、自定义 MySQL LangGraph Checkpointer和无人确认的生产发布不在当前范围。

## 文档治理

- 本文是阶段顺序、状态和完成门禁的唯一总体入口。
- `docs/implementation/phase-*.md` 记录对应阶段的详细规划、实施事实和验证证据。
- README 只提供运行入口和当前能力摘要，不作为完成状态依据。
- 一个阶段只有在其完整能力和查询闭环通过验收后才能标记“已完成”；只有表、POST 接口或目录不能代表完成。
- 代码、测试和迁移与文档冲突时，必须更新唯一当前描述，不保留两个互相冲突的路线图。

## 排序原则

- 阶段编号表达当前交付顺序，不再保留因历史规划形成的空档或倒序。
- 已实现能力排在前面：数据与控制平面、LLM 核心能力、Agent 工作流依次为阶段1～3。
- 当前优先完成“服务可用闭环”：阶段4 Web 内部产品面、阶段5最终用户身份与凭据、阶段6可观测性。
- 代码执行与外部扩展后置：阶段7工具、阶段8代码搜索、阶段9 MCP 必须建立在稳定服务、授权和诊断能力之上。
- RabbitMQ/Worker 不再作为所有后续能力的默认前置；阶段10只在长任务恢复、多 Worker、削峰或持久重试出现真实需求时启动。

2026 年 8 月 19 日统一重排如下；后续文档只使用新编号：

| 当前阶段 | 原阶段 | 调整原因 |
|---|---|---|
| 阶段3：Agent Runtime 与工作流 | 阶段5 | 核心实现已经完成，应紧随数据与 LLM 能力 |
| 阶段4：Web 前端 | 阶段9 | 当前正在实施，优先形成可用产品面 |
| 阶段5：身份、授权与凭据 | 阶段10 | 最终用户公开服务的安全前置 |
| 阶段6：可观测性 | 阶段8 | 先具备生产诊断能力，再扩展代码执行 |
| 阶段7：工具能力 | 阶段4 | 文件、命令和 Git 属于服务可用闭环后的高风险扩展 |
| 阶段8：代码搜索增强 | 阶段6 | 复用阶段7的只读工具和 Workspace 边界 |
| 阶段9：MCP Client | 阶段7 | 外部能力必须经过阶段7权限层 |
| 阶段10：异步任务执行 | 阶段3 | 当前无消息队列需求，改为条件触发的可靠执行阶段 |

## 当前事实

| 能力 | 当前状态 | 结论 |
|---|---|---|
| FastAPI、认证、SQLAlchemy、Alembic、MinIO | 已完成阶段1门禁 | 公共与内部认证、事务、查询、迁移和真实基础设施验证均通过；公共认证仍是受信调用方级 API Key |
| User 管理 | 已形成首个查询闭环 | 支持新增、详情、查询条件绑定 cursor、激活状态过滤和受限更新 |
| Run、Task 管理 | 已形成查询与动作闭环 | 支持独立分页、过滤、取消、Task 重试、并发锁顺序和 outbox 控制事实 |
| Attempt、Retry、Artifact 管理 | 已形成查询闭环 | 支持归属范围内独立分页、安全详情、物理重试顺序查询和 Artifact 受控流式读取 |
| Session、Conversation、Message | 已形成首个查询闭环 | 支持会话新增、详情、查询条件绑定 cursor、更新，以及不可变消息追加、详情和会话绑定顺序分页 |
| Tool Call、Evaluation、Outbox 查询 | 已形成内部查询闭环 | 使用独立内部密钥、查询绑定 cursor、归属校验和递归脱敏详情 |
| 多供应商 Model Gateway、Responses、SSE | 已完成 | DeepSeek 最新 Chat Completions 思考与流式约束已通过契约和 API 集成测试；真实供应商连通性未运行 |
| Memory 设计与实验性准备代码 | 规划中 | 已固定 L0～L4 边界并存在同步接口和快速测试；本轮不继续扩展，不接入实际 `/responses` 调用链，也不作为阶段2运行能力验收 |
| Conversation Context、Prompt、模型能力目录、Evaluation 单元 | 已完成 | 已通过行为、失败、幂等、历史快照、MySQL 并发和迁移门禁；保持可独立调用，不隐式拼接 Memory |
| Knowledge 知识库 | 暂停 | Knowledge 属于独立知识库能力；现有结构代码仅作实验性准备，需求、数据来源、解析、检索和权限将在后续单独规划，不计入阶段2 |
| RabbitMQ、Publisher、Worker | 暂停 | 当前同步调用和内部开发没有持久异步执行需求；保留阶段10规划，达到长任务、恢复或多 Worker 触发条件后再继续 |
| Agent、工具、MCP、OpenTelemetry、Web | Agent 模型工作流已完成、可观测性优先级提高、工具暂停、Web 第一版进行中 | 阶段3已实现 `model_only_v1` 同步协调器、最多两个无依赖工作 Agent 并行、费用预留、显式取消、完整节点结果、持久事件、核心查询与实时 POST SSE；跨进程恢复、工具和遥测分别由阶段10、阶段7和阶段6负责 |
| 最终用户认证与凭据管理 | 规划中 | 当前只有部署级受信调用方密钥；登录、当前用户、个人 API Key 和用户供应商凭据尚未实现，详见阶段5 |

## 总体结构

```text
受信业务调用方 / Web
          │
          ▼
身份、授权与凭据层
登录会话 / 当前主体 / 个人 API Key / 供应商凭据
          │
          ▼
API 与控制平面
用户、会话、消息、运行、任务、调用、产物、审计查询
          │
          ▼
LLM 核心能力层
Model Gateway / Context / Prompt / Model Catalog / Evaluation
          │
          ├── Memory（仅规划和实验性管理能力，不进入 Responses）
          └── Knowledge（独立知识库，后续另行规划）
          │
          ▼
Agent 与服务交付层
Agent Workflow / Web / Identity / Observability
          │
          ▼
后续代码能力层
Tool Runtime / Code Search / MCP
          │
          ▼
按需可靠执行层
Transactional Outbox / RabbitMQ / Worker / Idempotency
          │
          ▼
MySQL + MinIO + OpenTelemetry
```

Agent 只组合稳定的基础单元，不自行重复实现会话历史、记忆检索、上下文裁剪、知识读取或模型调用。

## 阶段路线图

| 阶段 | 交付目标 | 当前状态 | 完成门禁 |
|---|---|---|---|
| 阶段1：数据与控制平面 | 数据库事务、User、Session、Message、Run、Task、Attempt、Artifact、内部审计和完整查询 | 已完成 | 已通过完整 API、迁移、MySQL、MinIO、事务、分页、受信调用方权限边界和脱敏门禁 |
| 阶段2：LLM 核心能力 | Model Gateway、Conversation Context、Prompt/模型能力目录、Evaluation/Guardrail；Memory 只保留规划边界 | 已完成 | 运行能力可脱离 Agent 单独调用、测试和观测；`/responses` 不隐式读取 Memory；Knowledge 不计入本阶段 |
| 阶段3：Agent Runtime 与工作流 | 完整节点结果、Controller/工作模型、依赖分组、有界并行、确定性验证、独立审核和显式取消 | 已完成 | `model_only_v1` 主流程、数据库事实、核心 HTTP/SSE、原子费用预留、终态一致提交和失败收敛已通过自动化验证；工具恢复后以新的执行配置扩展，不回退本阶段；详见 [`phase-3-agent-workflow-plan.md`](../implementation/phase-3-agent-workflow-plan.md) |
| 阶段4：Web 前端 | 建设类似 ChatGPT 交互方式的对话平台，并逐步展示 Run、Agent、模型与工具执行过程 | 进行中 | 第一版工程与核心对话外壳已实现；初始界面只消费已验证 API；后续 Agent/Tool 状态必须来自稳定事件，不伪造执行进度；详见 [`phase-4-web-ui-plan.md`](../frontend/phase-4-web-ui-plan.md) |
| 阶段5：平台身份、授权与凭据管理 | 最终用户登录、当前主体、浏览器会话、个人 API Key、用户供应商凭据和资源授权迁移 | 规划中 | Cookie/Bearer 身份、撤销、scope、跨用户隔离、凭据加密/轮换/验证及 Worker 撤权测试通过；详见 [`phase-5-platform-identity-credentials.md`](../implementation/phase-5-platform-identity-credentials.md) |
| 阶段6：可观测性 | API、工作流节点、模型、未来消息/Worker/工具的 trace、metrics 和关联 | 未开始 | 阶段3已固定 ID、span 层级和敏感字段边界；本阶段完成遥测装配、采样、指标、导出和安全诊断，MySQL 始终是事实源 |
| 阶段7：工具能力 | 工具契约、权限、Workspace、文件、搜索、隔离命令、Git 和调用证据 | 暂停 | 阶段5授权边界和阶段6诊断能力稳定后恢复；阶段3不得绕过该阶段执行工具；详见 [`phase-7-tool-runtime-plan.md`](../implementation/phase-7-tool-runtime-plan.md) |
| 阶段8：代码搜索增强 | 文件索引、ripgrep、Tree-sitter，按需 LSP | 未开始 | 复用阶段7只读工具与 Workspace 权限；结果包含稳定文件位置、定义引用和可复核证据 |
| 阶段9：MCP Client | 连接、能力发现、工具、资源、认证、超时和权限 | 未开始 | 外部调用经过阶段7工具权限层，受控且完整记录 |
| 阶段10：异步任务执行 | Transactional outbox、RabbitMQ、Worker、幂等、重试、死信和恢复 | 暂停 | 仅在长任务恢复、多 Worker、削峰或持久重试出现真实需求后恢复；不作为阶段4～9的默认前置 |

## 阶段顺序与依赖关系

- 阶段1解决“数据是否可管理、可查询、可组合事务、可追溯”。
- 阶段2解决“LLM 能力是否可以作为独立模块使用”。
- 阶段3在阶段1、2之上交付可独立使用的 `model_only_v1` 同步工作流；工具节点、Implementer 和代码修改仍明确不可用。
- 阶段4～6优先解决服务是否可被人使用、是否具备最终用户安全边界、是否能够运维诊断。阶段7～9才扩展代码工具、语义搜索和外部 MCP 能力。
- 阶段10解决可靠异步执行，当前没有消息队列收益，继续暂停。需要后台恢复或工具时必须分别恢复阶段10或阶段7，不能在 Agent Runtime 内复制这些能力。
- 阶段5不回退阶段1、阶段2在受信调用方边界内的完成状态。阶段4当前内部开发界面可以继续使用服务端部署级 key；任何最终用户公开发布、用户自带供应商密钥、L3/L4 Memory 或多用户 Project 都必须先通过阶段5。
- Memory 不是 Agent 工作流中的一个临时字典，而是 L0 Agent Working、L1 Session、L2 Collaboration/Run、L3 Project 和 L4 User 五层设计。当前只保留边界、数据合同和实验性管理接口，不继续扩展，不由 `/responses` 自动读取；Context Preview 中现存的可选候选分支应在阶段2验收前禁用或移出稳定合同。未来启用前必须重新确认形成策略、权限、删除语义和评估门禁。
- MySQL 保存五层 Memory 的事实、状态、版本和当前指针；L1～L4 使用 MinIO 不可变 JSON/Markdown 快照，L0 只保存有界 MySQL 检查点。现有 `llm_memories` 继续承担细粒度长期事实，不用一个通用状态对象代替所有层级。
- Project Memory 默认关闭，只服务显式绑定的 Session/Run；它是最小记忆 scope，不建设看板、排期或项目管理业务。多用户共享必须等待最终用户认证与 Membership 权限模型。
- 五层 Memory 默认不引入 Vector Store 或 Mem0。只有固定评估证明词法/精确选择不足，且 Embedding、可靠异步投影、删除同步和重建门禁均满足时，才评估向量投影。
- L0 数据合同在规划中固定；自动检查点和跨进程崩溃恢复等待阶段10可靠执行机制，L0 不能复制 Run/Task 状态、Model Attempt、Tool Call 或 L2 Handoff。
- Knowledge 明确从阶段2移出。它属于独立知识库能力，后续先确定资料导入、版本、解析器、引用、检索质量、权限和删除恢复，再决定实施阶段；现有实验性代码不代表稳定接口。
- 阶段1公共认证是受信调用方级 API Key；任何核心资源直接面向最终用户前，必须另行完成可验证的用户认证授权。

## 各阶段的身份与凭据前置关系

阶段2、阶段3及后续内部能力开发不必等待阶段5全部完成，但任何最终用户公开访问都必须通过阶段5门禁。测试使用事务 fixture/受控 seed 创建 User，平台测试 key 每次运行生成，供应商普通测试使用假 Provider；不得增加公开测试登录端点、固定后门用户或仓库内密钥。

| 阶段 | 当前是否需要阶段5接口 | 当前必须固定的边界 |
|---|---|---|
| 阶段2 | 不需要；已在受信调用方边界完成 | 测试 User 独立创建；真实供应商冒烟仅从显式环境变量读取密钥 |
| 阶段10 | 不需要登录/个人 key/用户凭据 CRUD | Task 执行规格只保存凭据引用；RabbitMQ 消息不含 secret；Worker 从 Run 重新确认 owner |
| 阶段7 | 不需要 | 工具权限使用内部执行上下文与 Run/Task owner，不信任工具输入中的 `user_id` |
| 阶段3 | 内部 Agent 测试不需要；用户自带密钥启用前需要 | Agent、Handoff 和 Working Memory 只能携带凭据 ID，不得复制 secret |
| 阶段8 | 不需要 | 测试用户与 workspace 强绑定；代码搜索结果不得跨 fixture owner 或路径范围 |

完整测试供给、接口优先级和凭据引用合同以 [`phase-5-platform-identity-credentials.md`](../implementation/phase-5-platform-identity-credentials.md) 为准。

## 质量原则

1. 正确性、可查询性和可恢复性优先于开发速度。
2. 先定义资源、状态、事务、故障模型和验收测试，再连接外部系统。
3. Controller 只处理协议；Application Service 负责用例及其数据库事务；复杂且可复用的数据查询出现后再提取 Repository。
4. 单资源 Service 直接使用请求级数据库会话；跨资源操作必须在同一事务内完成，届时再提取事务协调器。
5. 所有增长型历史使用稳定 cursor 分页、限制 page size，并具有明确过滤与排序语义。
6. MySQL 是任务、消息、记忆、费用、错误和结果的业务事实来源。
7. LLM 基础单元必须可独立测试，不以 Agent 工作流跑通代替单元完成。
8. 外部副作用必须有幂等键、超时、有限重试、权限和可查询结果。
9. 程序验证优先于模型自评；重要结果才增加独立审核。
10. 有直接依赖的后续能力必须等待前置门禁；经依赖分析确认独立的阶段可以调整实施顺序，但必须记录限制，不能以跳过消息队列声称具备异步恢复能力。

## 通用完成标准

- 公共资源具有新增、详情、列表和必要动作接口，并覆盖权限、归属、分页和错误路径。
- 新增或实质修改函数具有准确说明注释。
- 单元测试、集成测试、静态检查、迁移检查和关键故障测试通过。
- 外部依赖不可用时安全失败，不留下无法解释的中间状态。
- API Key、凭据和敏感内容不进入普通日志、队列消息或公开错误。
- 当前文档、Schema、迁移、环境示例和运行命令与代码一致。
- 未运行的真实基础设施或供应商验证明确标记“未运行”。

## 当前结论

- 阶段1已完成：核心数据资源、内部审计、事务、分页、归属、脱敏和真实基础设施门禁均已通过。
- 阶段2已完成：Model Gateway、Context、Prompt/模型能力目录和 Evaluation 已通过快速测试、真实基础设施与迁移门禁。Memory 当前只保留规划和实验性准备代码，不进入实际模型响应链路；Knowledge 已移出本阶段，等待独立规划。
- 阶段3已完成：同步 `model_only_v1` 编排、完整节点返回参数、最多两个无依赖工作 Agent 并行、业务事件、原子费用预留、显式取消、审核门禁、完成事务和核心查询接口已经实现并验证。跨进程恢复、工具运行和遥测分别留在阶段10、阶段7和阶段6。
- 阶段4进行中：当前内部 Web 已具备核心对话外壳、Agent 合同适配以及会话/运行稳定路由和刷新恢复；继续优先完成真实 API、完整运行证据、安全和浏览器门禁。
- 阶段5规划中：相关内部开发继续使用隔离测试身份、动态测试 key、假 Provider 和可选环境凭据；最终用户公开 API、个人 API Key 和用户供应商凭据尚未实现。
- 阶段6未开始：下一步在服务可用闭环上补齐 trace、metrics、导出和内部诊断，不以遥测替代 MySQL 业务事实。
- 阶段7暂停：规划已固定，但必须等待服务授权和运维边界稳定；阶段3不得绕过工具权限和隔离边界。
- 阶段8、阶段9未开始：代码搜索复用阶段7的只读能力与 Workspace 权限；MCP 调用必须经过阶段7工具权限层。
- 阶段10暂停：当前同步调用和内部开发没有 RabbitMQ 的必要收益；等待长任务恢复、多 Worker、削峰或持久重试等真实触发条件。
- 阶段1详见 [`phase-1-foundation.md`](../implementation/phase-1-foundation.md)。
- 阶段2详见 [`phase-2-llm-core-capabilities.md`](../implementation/phase-2-llm-core-capabilities.md)。
- 阶段3 Agent 工作流详见 [`phase-3-agent-workflow-plan.md`](../implementation/phase-3-agent-workflow-plan.md)。
- 阶段4前端详见 [`phase-4-web-ui-plan.md`](../frontend/phase-4-web-ui-plan.md)。
- 阶段5身份与凭据详见 [`phase-5-platform-identity-credentials.md`](../implementation/phase-5-platform-identity-credentials.md)。
- 阶段7工具运行详见 [`phase-7-tool-runtime-plan.md`](../implementation/phase-7-tool-runtime-plan.md)。
- 阶段10保留规划详见 [`phase-10-rabbitmq-task-execution-plan.md`](../implementation/phase-10-rabbitmq-task-execution-plan.md)。
