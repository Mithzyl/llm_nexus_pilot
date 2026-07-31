# 第二阶段：LLM 核心能力规划与实施说明

**文档日期：** 2026 年 7 月 31 日
**文档状态：** 进行中（Model Gateway 已完成，其余核心单元未实施）
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

- 当前实现：阶段1已经完成 User、Session、Message、Run、Task、Model Attempt、Provider Transport Attempt、Run Artifact 以及内部审计资源的新增、查询和必要动作闭环。
- 当前实现：`llm_attempts` 已保存供应商、模型、token、费用、耗时、供应商请求编号、原始请求/响应 URI 和错误；阶段2应复用该表，不创建第二套调用记录。
- 当前实现：`ObjectStorage` 已能保存二进制内容并返回 SHA-256、大小和 `minio://` URI；原始供应商请求和响应应复用该能力。
- 当前实现：`POST /api/v1/runs/{run_id}/attempts` 是阶段1的记录写入接口，不具备模型调用能力。阶段2增加统一生成接口后，该接口暂时保留，用于内部迁移和兼容，不作为普通业务调用入口。
- 已完成：四家 Provider、OpenAI-compatible codec、统一 Responses、SSE、重试、费用与原始响应证据。
- 未实施：Context Builder、Memory、Knowledge、Prompt Registry、模型能力目录和 Evaluation/Guardrail Service。
- 当前结论：Model Gateway 已完成不等于阶段2完成，阶段2状态保持“进行中”。
- 当前约束：后端依赖必须安装在 `apps/api/.venv`，测试、迁移和静态检查均从该虚拟环境运行。
- 当前应以什么为准：用户目标和本计划优先；统一请求/响应结构、MySQL 事实边界和 MinIO 大对象边界以总技术方案为准；具体字段以实际 ORM 模型和迁移为准。
- 待确认事项：供应商生产模型名称、各环境可用 API Key、代理地址及费用表不写死在本计划中，实施时通过环境配置和独立价格配置提供。

## 已确认的实施边界

### 身份与资源归属

- 当前公共接口使用平台级 API Key，只能视为受信调用方管理接口；阶段2不把请求中的 `owner_id` 解释为已经验证的最终用户身份。
- 阶段2所有资源必须在 Service 和数据库查询中校验 User、Session、Run、Task 的关系，防止受信调用方误把一个用户的资源关联到另一个用户。
- 直接面向最终用户的身份认证和授权不在阶段2实现范围。若在该能力完成前对终端用户开放 Memory 或 Knowledge 接口，必须停止实施并先确定认证主体、权限声明和越权响应。
- 内部评估、规则管理和模型目录写入接口继续要求公共 API Key 与内部 API Key，不能仅依赖公共密钥。

### 删除与保留

- 阶段1的 Message 是不可变历史，不增加通用删除接口作为阶段2前置条件。
- Memory 必须提供逻辑删除；删除后不再参与检索，但保留最小审计字段、来源标识和删除时间。敏感正文必须按保留策略清空或删除对象存储内容。
- Knowledge Document 使用停用和版本替换；对象内容只有在没有有效版本引用且满足保留期后才能清理。
- Context Build、模型调用和 Evaluation 是执行证据，不提供普通修改接口；其敏感大对象按统一保留策略处理。
- 物理删除、法定保留期和最终用户数据导出属于部署策略，不能由业务 Service 静默猜测默认值。

### 资源限制

- 所有列表继续使用签名 cursor，默认 `limit=20`，允许范围为 1～100；cursor 必须绑定完整查询条件。
- Context 请求必须设置平台允许范围内的 `token_budget`，第一版范围为 256～200000；实际可用预算还必须受模型目录中的 context window 和预留输出 token 限制。
- Memory 检索数量范围为 1～100，进入 Context 前仍受独立 token 预算限制。
- Knowledge 检索数量范围为 1～50；上传大小继续使用现有 Artifact 上传上限，分块数量和单块字符数通过带边界的配置提供。
- Prompt 变量数量、渲染结果长度、规则数量和 Evaluation 输入长度必须有配置上限；超过上限返回输入错误，不进行截断后伪装成功。

## 阶段2核心能力范围

### Conversation 与 Context

Conversation Service 是读取和编排阶段1 Session/Message 的应用服务，不创建第二套会话或消息表。

第一版接口：

```text
POST /api/v1/context-builds/preview
GET  /api/v1/context-builds/{context_build_id}
```

请求至少包含 `user_id`、`session_id`、`provider`、`model`、`token_budget`，可以包含 `system_instruction`、Memory/Knowledge 开关和预留输出 token。返回统一消息、总 token 估算、各来源记录以及未选中原因。

持久化事实至少包含：

```text
llm_context_builds
context_build_id, user_id, session_id, provider, model,
token_budget, tokenizer_name, tokenizer_version,
input_token_estimate, status, created_at

llm_context_sources
context_source_id, context_build_id, source_type, source_id,
source_version, source_order, token_estimate, selection_status,
exclusion_reason, content_hash
```

规则：

- 选择顺序固定为 system instruction、必要安全指令、最近不可分割消息、有效 Memory、Knowledge 引用和历史摘要；每类来源使用独立预算，不能由单一长来源耗尽全部空间。
- 同一请求使用固定 tokenizer 名称、版本、来源版本和排序键；重复读取 `context_build_id` 必须能够解释当时输入，不使用当前最新内容覆盖历史证据。
- 单条消息超过该类预算时返回明确超限原因；不在 UTF-8 字符、工具调用结构或消息结构中间截断。
- 长对话摘要属于带来源和版本的 Context 产物，不覆盖原始 Message。
- Context 构建与 Provider 调用分离；预览不发起模型请求，也不产生供应商费用。

### Memory

Memory 是独立能力，不是 Agent 工作流内部的临时字典。第一版内部职责包括：

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

第一版接口：

```text
POST   /api/v1/memories
GET    /api/v1/memories
GET    /api/v1/memories/{memory_id}
PATCH  /api/v1/memories/{memory_id}
DELETE /api/v1/memories/{memory_id}
POST   /api/v1/memory-retrievals
```

`POST` 只接受具有来源的候选记忆。模型自动提取结果必须先标记为 `candidate`，只有显式策略或受信调用方确认后才能变为 `active`。`PATCH` 只允许纠正正文、置信度和状态；纠正必须创建新版本并通过 `supersedes_memory_id` 关联旧版本。

正式持久化字段至少包含：

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
status
content_hash
deleted_at
created_by_type
```

必须定义：

- 哪些内容允许写入长期记忆；
- 如何保留消息和工具结果来源；
- 冲突事实如何替换而不是并存注入；
- 过期、遗忘、压缩和用户删除；
- 如何避免把模型推测直接固化成事实；
- 检索结果如何按权限、相关度、时效和 token 预算排序；
- 用户如何查看、纠正和删除自己的记忆。

状态只允许：

```text
candidate -> active -> superseded
candidate -> rejected
candidate | active -> deleted
active -> expired
```

- 非法状态转换返回冲突错误；重复删除幂等返回当前删除状态。
- `owner_type + owner_id + memory_type + content_hash + active 状态` 使用唯一约束或等效事务检查，避免重试生成重复有效记忆。
- 写入、版本替换和来源关系在同一数据库事务提交；对象存储写入失败时不得创建可检索记录，数据库提交失败时清理本次新建且未被引用的对象。
- Retriever 先按 owner scope、状态和过期时间过滤，再进行相关度、重要度和时效排序；稳定排序键必须包含 `memory_id`。
- Compactor 只创建带完整来源的新记忆版本，不修改或删除原始来源；压缩失败不影响当前可用记忆。

### Knowledge

- Knowledge 与 Memory 分开：Knowledge 表达外部资料，Memory 表达用户或执行过程中的长期状态。
- 第一版接口包括文档登记、版本查询、停用和检索；正文通过已有 Artifact 引用，不创建第二套上传通道。

```text
POST  /api/v1/knowledge-documents
GET   /api/v1/knowledge-documents
GET   /api/v1/knowledge-documents/{document_id}
POST  /api/v1/knowledge-documents/{document_id}/versions
PATCH /api/v1/knowledge-documents/{document_id}
POST  /api/v1/knowledge-retrievals
```

- `knowledge_documents` 保存 owner scope、稳定文档标识和当前有效版本；`knowledge_document_versions` 保存 Artifact、内容哈希、解析器版本和状态；`knowledge_chunks` 保存版本、顺序、定位、正文或 URI 和内容哈希。
- 文档版本状态只允许 `pending -> processing -> ready`，处理失败进入 `failed`；`ready` 可以转为 `inactive`。只有 `ready` 版本参与检索。
- 新版本的解析和分块完成前继续使用旧的 `ready` 版本；新版本全部写入成功后，在单一数据库事务中切换当前版本，不能混用两个版本的块。
- 相同 owner scope、来源和内容哈希的重复登记返回已有版本，不重复生成分块。
- 检索结果必须返回 `document_id`、版本、chunk ID、稳定定位和内容哈希。第一版使用结构化过滤与 MySQL 全文能力；没有实际评估证据时不引入向量数据库。
- MinIO 或解析失败保留可查询失败状态和安全错误；不会把未完成版本标记为可检索。

### Prompt 与模型能力目录

- Prompt Template 具有稳定名称、不可变版本、用途、变量 Schema、启用状态和变更记录；Model Catalog 记录 provider/model 的 context window、工具、结构化输出、视觉、缓存和流式能力。
- 内部管理接口负责新增版本、启用和停用；受信公共接口只允许读取有效模型能力和渲染已启用 Prompt。

```text
POST /api/v1/prompt-renders
GET  /api/v1/model-capabilities

POST  /api/v1/internal/prompt-templates
POST  /api/v1/internal/prompt-templates/{template_name}/versions
PATCH /api/v1/internal/prompt-templates/{template_name}/active-version
POST  /api/v1/internal/model-catalog-versions
PATCH /api/v1/internal/model-catalog-versions/{catalog_version}/status
```

- Prompt 渲染接口必须拒绝缺失变量、未知变量、类型不符和超长结果；渲染与模型调用分离，单独调用不会产生模型费用。
- 同一 Prompt 名称只能有一个启用版本；版本内容创建后不可修改。并发启用通过唯一约束和事务条件更新保证。
- 模型目录使用 `provider + model + catalog_version` 标识不可变能力快照，包含来源、确认时间和启用状态；未知能力表示 `unknown`，不能默认为支持。
- 路由策略只读取能力快照和显式配置，不根据模型名称硬编码业务分支。调用证据必须记录实际使用的 Prompt 和目录版本。

### Evaluation 与 Guardrail

- 复用并扩展现有 `llm_evaluations`，不创建语义重复的评估事实表；增加规则集版本、幂等键、输入证据 URI、状态和有界错误字段。
- 确定性 Schema、引用完整性、长度和安全检查优先于模型评分。重要结果可以调用独立 evaluator，但必须通过 Model Gateway 并记录 evaluator attempt。

```text
POST /api/v1/evaluations
GET  /api/v1/evaluations/{evaluation_id}

POST  /api/v1/internal/evaluation-rule-sets
PATCH /api/v1/internal/evaluation-rule-sets/{rule_set_id}/status
```

- Evaluation 接口接受 Run/Task、候选 Attempt 或 Artifact、规则集版本和幂等键；同一幂等键及同一输入版本重复提交返回原结果。
- 状态只允许 `pending -> running -> completed | failed | cancelled`；规则结论使用独立 `verdict`，不能用执行失败代替“不通过”。
- Guardrail 失败返回稳定错误类型、规则 ID 和可公开证据，不删除用户参数后继续请求，不泄露内部规则正文或敏感输入。
- Evaluation 单元可以脱离 Agent 单独调用；外部 evaluator 超时或失败时保留确定性检查结果并明确整体状态，不伪造完整评估成功。

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
│   ├── sessions.py
│   ├── runs.py
│   ├── tasks.py
│   ├── model_attempts.py
│   ├── run_artifacts.py
│   └── internal_audit.py
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

- 保留阶段1的运行、任务、attempt 和 artifact 标识，不修改现有公开标识语义。
- `POST /api/v1/runs/{run_id}/attempts` 在阶段2暂时保留；新业务调用使用统一模型生成接口。确认没有外部调用方后，才能在后续版本弃用。
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
- 风险：不同用户、Session 或项目的记忆和知识可能错误关联；当前受信调用方边界内所有读写必须在 Service 和查询层验证 owner scope。直接面向最终用户前必须另行完成用户认证授权。
- 风险：Memory 或 Knowledge 同时写入 MySQL 和 MinIO 时可能部分成功；每个写入用例必须定义暂存对象、数据库提交和孤儿对象清理顺序。
- 风险：Prompt、模型目录、解析器、tokenizer 或评估规则升级后可能无法复现历史结果；执行证据必须记录实际使用的不可变版本。
- 风险：并发纠正 Memory、切换 Knowledge 或 Prompt 版本可能产生多个有效版本；必须使用唯一约束、状态条件更新和事务冲突响应。
- 需要停止并确认的情况：实施要求使用未提供且无法通过安全配置表达的私有供应商协议。
- 需要停止并确认的情况：需要执行会产生明显费用的批量真实模型测试。
- 需要停止并确认的情况：现有 `llm_attempts` 无法表达每次物理重试且必须进行破坏性数据迁移。
- 需要停止并确认的情况：供应商能力与验收要求冲突，例如指定模型明确不支持工具调用但要求静默兼容。
- 需要停止并确认的情况：要求把受信调用方接口直接开放给最终用户，但没有确定可验证的认证主体和权限声明。
- 需要停止并确认的情况：要求对阶段1不可变 Message 做物理级联删除，或要求使用尚未确定的法定保留策略。

## Model Gateway 完成标准（已达到）

- OpenAI、DeepSeek、Anthropic 和 Gemini 均实现 `ModelProvider` 契约并通过同一参数化契约测试。
- 至少 OpenAI 和 DeepSeek 可在配置凭据后通过同一业务接口切换调用。
- 普通生成和 SSE 流式输出均返回统一协议。
- 工具调用和结构化 JSON 输出在供应商支持时能被统一解析；不支持时返回明确错误。
- 成功、失败、超时、重试和客户端取消都有与实际行为一致的 attempt 记录。
- token、缓存 token、费用、耗时、供应商请求编号和原始响应位置可以通过 `run_id` 查询。
- API Key 和供应商凭据不出现在数据库原始内容、错误响应和普通日志中。
- Ruff、定向单元测试、ASGI 集成测试、迁移升级和 `alembic check` 全部通过。
- 真实供应商测试若未运行，文档明确标为“未运行”，不以伪造响应测试代替真实连通性结论。
- README、`.env.example` 和当前实施文档与最终运行方式一致，不保留相互冲突的阶段2描述。

## 实施结果

- `provider` 使用稳定枚举；`model` 保留供应商原始字符串，并由对应 Provider 的可选 allowlist 校验组合。
- OpenAI 使用 Responses API codec；DeepSeek 使用通用 OpenAI-compatible Chat Completions codec；Anthropic 使用 Messages codec；Gemini 使用 GenerateContent codec。
- 公共 HTTP 层封装为 `POST /api/v1/responses`，业务方不直接接触任何供应商 payload。
- `HttpTransport` 统一负责 JSON、SSE、连接超时、HTTP 状态分类和指数退避，Provider 只负责编解码。
- 每个逻辑调用写入 `llm_attempts`；每个物理网络请求写入 `llm_attempt_retries`。
- API Key 仅从服务端 `SecretStr` 配置进入认证 Header，不进入公共请求、数据库原始请求或错误响应。
- 价格不内置可能过期的数字，只读取带供应商/模型键的显式 JSON 配置；未知价格保持 `null`。
- 未配置凭据的 Provider 不注册，服务仍可启动；调用时返回 `provider_not_configured`。
- 单元、Provider 契约、SSE、ASGI、迁移一致性和静态检查均为阶段2验收项。
- 真实供应商连通性测试：未运行。本阶段规划修订没有读取供应商凭据，也没有产生模型费用。

## 2026 年 7 月 31 日当前验证基线

- API、Service、ORM、Schema、依赖注入和外部适配已拆分到明确的 MVC 风格职责目录。
- 快速测试共收集 97 项，其中 95 项通过，2 项真实基础设施测试按默认配置跳过；显式启用后，真实 MySQL/MinIO 测试 2 项通过。
- 当前覆盖数据库 session 的 yield/rollback/close 生命周期、请求取消回滚、重复身份、缺失资源、非法任务依赖、跨 Run Model Attempt、上传路径清理、上传大小限制、对象存储失败回滚与安全错误转换、Provider timeout 和 retry 证据。
- 资源 Router 有架构测试约束，不能直接导入 ORM Model 或 SQLAlchemy 事务实现。
- 上传文件名同时清理 POSIX 和 Windows 风格路径片段。
- `get_session()` 在请求异常时显式 rollback，并由异步上下文保证 session 关闭。
- RabbitMQ、Worker 和任务自动执行仍未实现，不计入阶段2完成范围。

## 阶段2剩余实施顺序

以下内容仍属于同一个阶段2，不拆分为新的阶段编号。每个工作项必须先建立公共契约测试或状态转换测试，再实现数据库和 Service：

| 顺序 | 工作项 | 具体任务 | 失败与恢复设计 | 验证门禁 |
|---|---|---|---|---|
| 1 | Context Builder | 定义 Schema、tokenizer 接口、来源优先级、预览接口、构建及来源表 | 来源缺失明确失败；超预算返回裁剪证据；历史版本不被当前内容覆盖 | 固定样本可复现；消息顺序、预算边界、来源版本和超限测试通过 |
| 2 | Memory Store 与 Policy | 先写状态转换和幂等测试，再实现表、来源、版本、纠正与逻辑删除接口 | 重复候选去重；非法转换回滚；对象与数据库部分失败可清理 | 来源、owner scope、纠正、删除、过期、冲突和敏感正文测试通过 |
| 3 | Memory Retrieval 与 Compaction | 实现过滤、稳定排序、数量/token 上限和带来源压缩 | 超时不改变已有记忆；压缩失败不替换来源；并发压缩只能产生一个有效版本 | 相关度、时效、去重、预算、稳定排序和并发测试通过 |
| 4 | Knowledge Store 与 Retrieval | 复用 Artifact，增加文档、版本、分块、切换及检索接口 | 新版本失败继续使用旧版本；MinIO/数据库部分失败可恢复；重复内容幂等 | 版本切换、分块引用、owner scope、重复上传、失败恢复和检索上限测试通过 |
| 5 | Prompt Registry 与 Model Catalog | 实现不可变版本、内部管理接口、渲染接口及能力快照 | 并发启用只有一个有效版本；未知能力明确拒绝；禁用不影响历史证据 | 变量 Schema、注入文本、长度、版本并发和能力匹配测试通过 |
| 6 | Evaluation 与 Guardrail | 扩展现有 Evaluation，增加规则版本、幂等、确定性检查和独立 evaluator | 外部评估失败保留确定性结果但整体明确失败；重复提交不重复计费 | 规则版本、verdict/执行状态分离、幂等、超时、脱敏和错误分类测试通过 |
| 7 | 阶段2集成验收 | 将 Context 结果显式传入 Model Gateway，并验证其他单元仍可独立调用 | 任一单元失败不伪造模型成功；保存可定位的输入版本和错误 | Ruff、完整快速测试、真实 MySQL/MinIO、迁移、OpenAPI 和敏感信息测试全部通过 |

## 逐能力接口和错误契约

所有新增接口沿用阶段1的错误响应结构，至少稳定区分：输入错误、未认证、资源不存在、资源关系不匹配、状态冲突、依赖暂时不可用和内部错误。

| 能力 | 必要输入 | 正常输出 | 不允许的隐式行为 |
|---|---|---|---|
| Context Build | User、Session、模型、预算和来源开关 | 构建 ID、统一消息、token 估算、选中与排除来源 | 不自动调用模型；不无限读取会话；不静默丢弃完整消息 |
| Memory Write | owner scope、类型、正文或 URI、来源和策略 | Memory 版本及状态 | 不把无来源模型推测直接标记为有效事实 |
| Memory Retrieval | owner scope、查询、类型、数量和 token 上限 | 稳定排序结果及来源 | 不跨 owner 检索；不返回已删除、已替代或过期内容 |
| Knowledge Version | 文档 ID、Artifact ID、来源和解析配置版本 | 可查询处理状态和版本 | 不在分块未完成时切换当前版本 |
| Prompt Render | Prompt 名称/版本和变量 | 渲染文本及版本证据 | 不忽略未知变量；不因渲染而发起模型调用 |
| Model Capability | provider、model 和目录版本 | 明确支持、不支持或未知 | 不把未知能力当作支持 |
| Evaluation | 输入事实、规则版本和幂等键 | 执行状态、verdict、规则证据 | 不把执行失败写成通过或不通过结论 |

## 测试设计

必须先行的测试：

- Memory、Knowledge、Prompt 和 Evaluation 的状态转换、重复执行和并发冲突。
- owner scope 允许、关系不匹配、缺少公共认证、缺少内部认证；最终用户级越权测试必须等认证主体确定后才能声称覆盖。
- Context、Memory Retrieval 和 Knowledge Retrieval 的数量、长度、token、排序和 cursor 边界。
- Knowledge/Memory 跨 MySQL 与 MinIO 写入的部分失败及清理。
- Prompt 变量 Schema、模板注入文本、并发启用和不可变版本。
- Evaluation 的幂等、规则版本、独立 evaluator 超时和敏感证据脱敏。

其他验证：

- Router/Service/ORM 依赖边界架构测试。
- Alembic 空库升级、已有阶段1数据库向前升级和 `alembic check`。
- 真实 MySQL 唯一约束、行锁和全文检索行为；真实 MinIO 对象失败与清理。
- OpenAPI 路由、Schema 示例、错误结构和内部接口双密钥检查。
- Model Gateway 既有 Provider 契约与 SSE 回归测试。

## 阶段2总体完成标准

- Model Gateway、Conversation Context、Memory、Knowledge、Prompt/模型能力目录和 Evaluation 均具有独立 Service、Schema 和测试。
- Session/Message、Memory 和 Knowledge 数据具有正式归属、分页查询、修改或删除策略。
- Context Builder 可以在固定 token 预算下生成可复现输入，并记录被选中和被裁剪的来源。
- Memory 写入必须有来源和策略，检索必须有权限、排序、数量和 token 上限。
- 受信调用方可以在明确 owner scope 内查询、纠正和逻辑删除长期记忆；在完成最终用户认证授权前，不宣称用户本人隔离已经完成。
- Knowledge 检索结果包含稳定引用，文档更新不会静默混用旧版本。
- Prompt 和模型能力配置具有版本，Agent 不直接硬编码厂商能力差异。
- Evaluation 与 Guardrail 可以脱离 Agent 单独执行，并保留规则与证据。
- 全部能力通过单元测试、数据库集成、权限、分页、删除、并发和敏感信息测试。
- 当前文档、迁移、Schema、环境配置和运行说明与代码一致，不保留过期测试数量或不存在的资源状态。
- 以上条件未满足前，阶段2状态保持“进行中”。

## 官方接口依据

- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)
- [Anthropic Messages streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Gemini GenerateContent](https://ai.google.dev/api/generate-content)
