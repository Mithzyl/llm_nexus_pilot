# 第二阶段：LLM 核心能力规划与实施说明

**文档日期：** 2026 年 7 月 30 日
**文档状态：** `PARTIAL`（Model Gateway 已完成，其余核心单元未实施）
**前置阶段：** `phase-1-foundation.md`
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

## 目标

- 提供可独立调用的 Model Gateway、Conversation Context、Memory、Knowledge、Prompt/模型能力目录和 Evaluation/Guardrail。
- Agent 只组合这些稳定单元，不在工作流节点中临时实现消息历史、上下文裁剪、记忆检索或知识查询。
- 业务调用方通过同一请求、响应和流式事件协议调用 OpenAI、DeepSeek、Anthropic、Gemini 和 OpenAI-compatible 服务。
- 每次模型调用、上下文构建、记忆读写、知识引用和评估都具有可查询输入来源、结果、耗时和错误。
- 明确不处理：RabbitMQ Worker、任务自动领取、总控与工作模型、工具执行循环、MCP、代码搜索、LangGraph、OpenTelemetry 和前端页面。
- 明确不处理：自训练模型选择器、默认多候选答案投票，以及对真实供应商发起默认批量测试请求。

## 当前事实

- 当前实现：FastAPI 已能创建用户、运行、任务、模型调用记录和 MinIO 产物，并通过 `GET /api/v1/runs/{run_id}` 返回调用历史。
- 当前实现：`llm_attempts` 已保存供应商、模型、token、费用、耗时、供应商请求编号、原始请求/响应 URI 和错误；阶段 2 应复用该表，不创建第二套调用记录。
- 当前实现：`ObjectStorage` 已能保存二进制内容并返回 SHA-256、大小和 `minio://` URI；原始供应商请求和响应应复用该能力。
- 当前实现：`POST /api/v1/runs/{run_id}/attempts` 是阶段 1 的记录写入接口，不具备模型调用能力。阶段 2 增加统一生成接口后，该接口暂时保留，用于内部迁移和兼容，不作为普通业务调用入口。
- 已完成：四家 Provider、OpenAI-compatible codec、统一 Responses、SSE、重试、费用与原始响应证据。
- 未实施：正式 Session/Message、Context Builder、Memory、Knowledge、Prompt Registry、模型能力目录和 Evaluation/Guardrail Service。
- 当前结论：Model Gateway 已完成不等于 Phase 2 完成，Phase 2 状态必须保持 `PARTIAL`。
- 当前约束：后端依赖必须安装在 `apps/api/.venv`，测试、迁移和静态检查均从该虚拟环境运行。
- 当前应以什么为准：用户目标和本计划优先；统一请求/响应结构、MySQL 事实边界和 MinIO 大对象边界以总技术方案为准；具体字段以实际 ORM 模型和迁移为准。
- 待确认事项：供应商生产模型名称、各环境可用 API Key、代理地址及费用表不写死在本计划中，实施时通过环境配置和独立价格配置提供。

## 阶段 2 核心能力范围

### Conversation 与 Context

- Conversation Service 读取 Phase 1 的 Session 和 Message，不另建隐藏会话状态。
- Context Builder 根据 token 预算选择 system instruction、最近消息、历史摘要、记忆、知识引用和工具结果。
- 每次构建结果记录来源 ID、裁剪原因、token 估算和顺序，便于复现模型输入。
- Context 生成与 Provider 调用分离，可以单独测试和预览。
- 长对话必须支持摘要和分段，不允许无限追加完整历史。

### Memory

Memory 是独立能力，不是 Agent 工作流内部的临时字典。至少提供：

```text
Memory Store
Memory Writer
Memory Retriever
Memory Ranker
Memory Compactor
Memory Policy
```

至少区分：

- 短期工作记忆；
- Session 情节记忆；
- 用户事实与偏好；
- 可复用知识；
- 执行经验与过程摘要。

建议统一字段：

```text
memory_id
owner_type
owner_id
session_id
memory_type
content_text 或 content_uri
source_message_ids
importance
confidence
created_at
updated_at
last_accessed_at
expires_at
supersedes_memory_id
```

必须定义：

- 哪些内容允许写入长期记忆；
- 如何保留消息和工具结果来源；
- 冲突事实如何替换而不是并存注入；
- 过期、遗忘、压缩和用户删除；
- 如何避免把模型推测直接固化成事实；
- 检索结果如何按权限、相关度、时效和 token 预算排序；
- 用户如何查看、纠正和删除自己的记忆。

### Knowledge

- 文档登记、分块、内容哈希、来源 URI、版本和访问范围必须独立建模。
- Retrieval 返回内容片段时必须同时返回来源和定位证据。
- 第一版可以使用结构化过滤与全文检索；没有事实依据时不强制引入向量数据库。
- Knowledge 与 Memory 分开：Knowledge 表达外部资料，Memory 表达用户或执行过程中的长期状态。

### Prompt 与模型能力目录

- Prompt Template 具有名称、版本、用途、变量 Schema、启用状态和变更记录。
- Model Catalog 记录 provider/model 的 context window、工具、结构化输出、视觉、缓存和流式能力。
- 路由策略只读取能力和显式配置，不根据模型名称硬编码业务分支。
- Prompt 渲染与模型调用分离，渲染结果可单独测试。

### Evaluation 与 Guardrail

- 确定性 Schema、引用完整性、长度和安全检查优先于模型评分。
- 重要结果可以调用独立 evaluator，但必须保存评估输入、规则版本、结论和证据。
- Guardrail 失败必须返回明确类型，不以删除用户参数后继续请求来伪造成功。
- Evaluation 单元可以独立调用，不依赖完整 Agent 工作流。

## Model Gateway：统一接口契约（已完成）

### 模型请求

统一请求对象（代码标识建议：`ModelRequest`）至少包含：

```text
provider
model
messages
system_instruction
tools
output_schema
temperature
max_output_tokens
timeout_seconds
metadata
```

业务 HTTP 请求还必须包含 `run_id`，可以包含 `task_id`。服务必须校验任务属于对应运行，再创建模型调用记录。

约束：

- `provider` 使用受控枚举，不允许将任意 URL 当作供应商地址。
- `messages`、工具结构和输出结构在进入适配器前完成 Pydantic 校验。
- `timeout_seconds` 受平台最小值和最大值限制，调用方不能无限延长请求。
- `metadata` 只保存可检索的非敏感标识，不保存 API Key、完整私人文件或未脱敏凭据。
- `output_schema` 只表示期望的 JSON Schema；适配器必须根据供应商能力决定原生结构化输出或返回明确的“不支持”错误，不得静默忽略。

### 模型响应

统一响应对象（代码标识建议：`ModelResponse`）至少包含：

```text
text
tool_calls
structured_output
finish_reason
input_tokens
output_tokens
cached_tokens
estimated_cost
latency_ms
provider_request_id
raw_response
```

HTTP 默认响应不直接返回无限制的 `raw_response`。完整原始内容写入 MinIO，API 返回 `attempt_id` 和必要的统一字段；只有内部受控调用才能读取原始对象。

### 流式事件

流式接口使用 SSE（Server-Sent Events），事件类型固定为：

```text
response.started
response.text.delta
response.tool_call.delta
response.usage
response.completed
response.failed
```

每个事件至少携带 `attempt_id` 和单调递增的 `sequence`。正常结束、供应商错误、超时、客户端断开都必须结束或更新对应 `llm_attempts`，不能长期保留无法解释的 `started` 状态。

## Model Gateway 实施范围（已完成）

- 需要修改：后端依赖与配置，增加四家供应商凭据、允许的基础 URL、统一超时、重试和价格配置。
- 需要修改：数据库模型与 Alembic 迁移，补足调用重试次数、统一错误类型及必要的幂等标识；不复制已有 token、费用、URI 字段。
- 需要新增：`packages/models` 独立 Python 包，承担统一类型、供应商协议、适配器注册、错误转换、重试、流式事件归一化和费用计算。
- 需要修改：FastAPI 增加统一 `POST /api/v1/responses` 入口，通过 `stream` 决定普通 JSON 或 SSE，并由应用服务协调调用记录与对象存储。
- 需要新增：供应商契约测试、服务测试、SSE 测试、超时和重试测试，以及可选的真实供应商冒烟测试入口。
- 需要修改：README、环境变量示例和当前实施说明。
- 明确不修改：任务调度状态机、outbox 发布逻辑、工具权限模型、Agent 角色和 Compose 中尚未启用的 RabbitMQ。

## 目录与职责

```text
packages/models/
├── pyproject.toml
├── src/nexuspilot_models/
│   ├── contracts.py          # 统一请求、响应、消息、工具调用和流事件
│   ├── errors.py             # 稳定的平台错误分类
│   ├── provider.py           # ModelProvider 抽象协议
│   ├── registry.py           # 供应商名称到适配器的显式登记
│   ├── pricing.py            # 基于显式版本价格表估算费用
│   ├── transport.py          # 通用 HTTP、SSE、超时、重试与物理请求证据
│   └── providers/
│       ├── openai.py
│       ├── openai_compatible.py # DeepSeek 与可配置兼容服务共用
│       ├── anthropic.py
│       └── gemini.py
└── tests/
    └── test_providers.py

apps/api/src/nexuspilot_api/
├── main.py                   # FastAPI 应用组装与资源生命周期
├── core/
│   ├── config.py             # 凭据、端点、超时及功能开关
│   ├── security.py           # API 认证
│   └── dependencies.py       # Provider 与业务服务依赖注入
├── routers/                  # 按业务资源拆分的 HTTP Controller
│   ├── responses.py          # 统一生成和 SSE 路由
│   ├── providers.py          # Provider 发现
│   ├── users.py
│   ├── runs.py
│   ├── tasks.py
│   ├── attempts.py
│   └── artifacts.py
├── services/                 # 事务边界和业务规则
│   ├── model_response_service.py
│   ├── user_service.py
│   ├── run_service.py
│   ├── task_service.py
│   ├── model_attempt_service.py
│   └── run_artifact_service.py
├── models/                   # 按身份、执行、调用和证据拆分的 ORM Model
├── schemas/                  # 按资源拆分的 Pydantic 请求/响应结构
└── infrastructure/          # SQLAlchemy、MinIO 和 Provider 组装
```

`packages/models` 不直接访问 FastAPI、SQLAlchemy、MySQL 或 MinIO。`routers` 只处理 HTTP 输入输出，`services` 负责业务规则和事务，`models` 负责持久化映射，`infrastructure` 封装外部系统。`apps/api` 通过这些分层把独立模型包接入平台事实记录，避免供应商代码反向依赖业务数据库。

## Model Gateway 实施步骤（已完成）

| 步骤 | 修改对象 | 预期结果 | 验证方式 |
|---|---|---|---|
| 1 | `packages/models` 包结构与统一类型 | 普通生成、工具调用、结构化输出和流事件都有稳定类型；所有新增或实质修改函数带说明注释 | Ruff、类型导入检查、Pydantic 边界测试 |
| 2 | `ModelProvider`、registry、错误类型 | 业务按 `provider` 获取适配器；未知供应商和不支持能力返回稳定错误 | registry 单元测试、错误映射参数化测试 |
| 3 | OpenAI 与 DeepSeek 适配器 | 两家供应商通过相同调用入口完成普通生成、SSE、token 和请求编号归一化 | 使用录制/伪造 HTTP 响应的适配器契约测试，不产生费用 |
| 4 | Anthropic 与 Gemini 适配器 | 将消息、工具调用、结构化输出和结束原因映射到同一协议 | 与步骤 3 相同的契约测试矩阵 |
| 5 | 超时、重试和错误转换 | 仅对限流、短暂网络故障和部分 5xx 重试；认证、参数错误和明确不支持不重试 | 超时、退避、最大次数、不可重试错误测试 |
| 6 | token 与费用估算 | 优先记录供应商返回 usage；费用按带生效日期和币种的显式价格配置计算，未知模型返回 `estimated_cost=null` | 已知/未知模型、缓存 token、舍入边界测试 |
| 7 | `services/model_response_service.py` 和数据库迁移 | 调用开始即产生 attempt；成功、失败、超时和断流均写入最终状态；原始内容写入 MinIO | 服务事务测试、迁移升级与 `alembic check` |
| 8 | FastAPI 普通生成接口 | `POST /api/v1/responses` 校验运行和任务后返回统一结果及 `attempt_id` | ASGI 集成测试、鉴权和跨运行 task 拒绝测试 |
| 9 | FastAPI SSE 接口 | 同一接口设置 `stream=true` 后按固定事件协议输出并正确处理断开 | SSE 顺序、结束事件、错误事件、客户端取消测试 |
| 10 | 文档与可选真实冒烟测试 | 环境变量、调用示例、供应商能力差异和测试方式可复现 | README 命令审查；仅在显式开关及凭据存在时运行真实测试 |

## 错误与重试规则

统一错误至少区分：

```text
authentication_error
invalid_request
unsupported_capability
rate_limited
provider_unavailable
timeout
network_error
response_parse_error
cancelled
internal_error
```

- 认证失败、请求无效、不支持能力和响应结构确定性错误默认不重试。
- 限流、网络中断和供应商暂时不可用可以在总超时内按指数退避重试。
- 每次物理供应商请求仍应有独立证据。若继续复用一个 `attempt_id`，必须新增可查询的 retry 明细；不能只覆盖最后一次错误。
- 日志、错误文本、原始请求和原始响应在持久化前必须移除 Authorization、API Key 和供应商凭据。
- 未配置某供应商凭据时，启动可以继续，但调用该供应商必须返回明确配置错误。

## 兼容策略

- 保留阶段 1 的运行、任务、attempt 和 artifact 标识，不修改现有公开标识语义。
- `POST /api/v1/runs/{run_id}/attempts` 在阶段 2 暂时保留；新业务调用使用统一模型生成接口。确认没有外部调用方后，才能在后续版本弃用。
- 新数据库字段通过向前迁移添加，不重写或删除已有记录。
- 供应商原始结束原因保存在原始响应，公共 `finish_reason` 使用平台枚举，避免业务依赖厂商字符串。
- 某供应商不支持工具或结构化输出时返回明确能力错误，不通过删除参数后继续请求来伪造成功。

## 风险与停止条件

- 风险：四家供应商对 system message、工具参数、JSON Schema、缓存 token 和流结束事件的语义不同；适配器契约测试必须覆盖差异，不能假设 OpenAI 格式适用于全部供应商。
- 风险：SSE 客户端断开可能发生在供应商仍输出时；实现必须取消上游请求并最终记录 attempt 状态。
- 风险：价格会变化；价格配置必须注明来源、生效时间和币种，未知价格不允许按 0 计费。
- 风险：原始响应可能包含用户敏感内容；只写入受控对象存储，不写普通日志或 OpenTelemetry 属性。
- 风险：Memory 可能把模型推测固化为长期事实；没有来源、置信度和纠正机制的内容不得自动写入长期记忆。
- 风险：Context、Memory 和 Knowledge 同时注入可能超出 token 预算或重复内容；必须由统一 Context Builder 排序、去重和裁剪。
- 风险：不同用户、Session 或项目的记忆和知识可能越权泄露；所有读写必须在查询层验证 owner scope。
- 需要停止并确认的情况：实施要求使用未提供且无法通过安全配置表达的私有供应商协议。
- 需要停止并确认的情况：需要执行会产生明显费用的批量真实模型测试。
- 需要停止并确认的情况：现有 `llm_attempts` 无法表达每次物理重试且必须进行破坏性数据迁移。
- 需要停止并确认的情况：供应商能力与验收要求冲突，例如指定模型明确不支持工具调用但要求静默兼容。
- 需要停止并确认的情况：Phase 1 尚未提供 Session、Message、资源归属和删除能力，却要求实现长期 Memory。

## Model Gateway 完成标准（已达到）

- OpenAI、DeepSeek、Anthropic 和 Gemini 均实现 `ModelProvider` 契约并通过同一参数化契约测试。
- 至少 OpenAI 和 DeepSeek 可在配置凭据后通过同一业务接口切换调用。
- 普通生成和 SSE 流式输出均返回统一协议。
- 工具调用和结构化 JSON 输出在供应商支持时能被统一解析；不支持时返回明确错误。
- 成功、失败、超时、重试和客户端取消都有与实际行为一致的 attempt 记录。
- token、缓存 token、费用、耗时、供应商请求编号和原始响应位置可以通过 `run_id` 查询。
- API Key 和供应商凭据不出现在数据库原始内容、错误响应和普通日志中。
- Ruff、定向单元测试、ASGI 集成测试、迁移升级和 `alembic check` 全部通过。
- 真实供应商测试若未运行，文档明确标为 `NOT_RUN`，不以伪造响应测试代替真实连通性结论。
- README、`.env.example` 和当前实施文档与最终运行方式一致，不保留相互冲突的阶段 2 描述。

## 实施结果

- `provider` 使用稳定枚举；`model` 保留供应商原始字符串，并由对应 Provider 的可选 allowlist 校验组合。
- OpenAI 使用 Responses API codec；DeepSeek 使用通用 OpenAI-compatible Chat Completions codec；Anthropic 使用 Messages codec；Gemini 使用 GenerateContent codec。
- 公共 HTTP 层封装为 `POST /api/v1/responses`，业务方不直接接触任何供应商 payload。
- `HttpTransport` 统一负责 JSON、SSE、连接超时、HTTP 状态分类和指数退避，Provider 只负责编解码。
- 每个逻辑调用写入 `llm_attempts`；每个物理网络请求写入 `llm_attempt_retries`。
- API Key 仅从服务端 `SecretStr` 配置进入认证 Header，不进入公共请求、数据库原始请求或错误响应。
- 价格不内置可能过期的数字，只读取带供应商/模型键的显式 JSON 配置；未知价格保持 `null`。
- 未配置凭据的 Provider 不注册，服务仍可启动；调用时返回 `provider_not_configured`。
- 单元、Provider 契约、SSE、ASGI、迁移一致性和静态检查均为阶段 2 验收项。
- 真实供应商连通性测试：`NOT_RUN`，本次没有读取用户供应商凭据，也没有产生模型费用。

## 2026 年 7 月稳定化结果

- API、Service、ORM、Schema、依赖注入和外部适配已拆分到明确的 MVC 风格职责目录。
- 当前测试共 43 项，新增覆盖数据库 session 的 yield/rollback/close 生命周期、请求取消回滚、重复身份、缺失资源、非法任务依赖、跨 run attempt、上传路径清理、上传大小限制、对象存储失败回滚与安全错误转换、Provider timeout 和 retry 证据。
- 资源 Router 有架构测试约束，不能直接导入 ORM Model 或 SQLAlchemy 事务实现。
- 上传文件名同时清理 POSIX 和 Windows 风格路径片段。
- `get_session()` 在请求异常时显式 rollback，并由异步上下文保证 session 关闭。
- RabbitMQ、Worker 和任务自动执行仍未实现，不计入阶段 2 完成范围。

## Phase 2 剩余实施顺序

以下内容仍属于同一个 Phase 2，不拆分为新的阶段编号：

| 顺序 | 能力 | 前置条件 | 验证重点 |
|---|---|---|---|
| 1 | Conversation 与 Context Builder | Phase 1 Session/Message 和分页查询完成 | 消息顺序、token 预算、裁剪来源、可复现输入 |
| 2 | Memory Store 与 Policy | Phase 1 资源归属、查询和删除能力完成 | 来源、权限、纠正、遗忘、冲突和敏感信息 |
| 3 | Memory Retriever/Ranker/Compactor | Memory Store 契约稳定 | 相关度、时效、去重、token 上限和压缩可追溯 |
| 4 | Knowledge Store 与 Retrieval | Artifact 和内容读取闭环完成 | 文档版本、分块、引用、权限和更新一致性 |
| 5 | Prompt Registry 与 Model Catalog | Model Gateway 能力字段确认 | 版本、变量 Schema、能力匹配和禁用策略 |
| 6 | Evaluation 与 Guardrail | Context、Memory 和 Knowledge 输出契约稳定 | 确定性检查、规则版本、独立评估和错误分类 |
| 7 | Phase 2 集成验收 | 上述所有能力独立测试通过 | 不通过 Agent 工作流也能完整调用和观测 |

## Phase 2 总体完成标准

- Model Gateway、Conversation Context、Memory、Knowledge、Prompt/模型能力目录和 Evaluation 均具有独立 Service、Schema 和测试。
- Session/Message、Memory 和 Knowledge 数据具有正式归属、分页查询、修改或删除策略。
- Context Builder 可以在固定 token 预算下生成可复现输入，并记录被选中和被裁剪的来源。
- Memory 写入必须有来源和策略，检索必须有权限、排序、数量和 token 上限。
- 用户可以查询、纠正和删除自己的长期记忆。
- Knowledge 检索结果包含稳定引用，文档更新不会静默混用旧版本。
- Prompt 和模型能力配置具有版本，Agent 不直接硬编码厂商能力差异。
- Evaluation 与 Guardrail 可以脱离 Agent 单独执行，并保留规则与证据。
- 全部能力通过单元测试、数据库集成、权限、分页、删除、并发和敏感信息测试。
- 以上条件未满足前，Phase 2 状态保持 `PARTIAL`。

## 官方接口依据

- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)
- [Anthropic Messages streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Gemini GenerateContent](https://ai.google.dev/api/generate-content)
