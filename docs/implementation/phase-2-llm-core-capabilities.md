# 第二阶段：LLM 核心能力规划与实施说明

**文档日期：** 2026 年 8 月 3 日
**文档状态：** 进行中（Model Gateway 已完成；Conversation Context、Prompt/模型能力目录和 Evaluation 尚缺行为验证；Memory 只保留规划与实验性准备代码；Knowledge 已移出本阶段）
**前置阶段：** `phase-1-foundation.md`
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

## 目标

- 提供可独立调用的 Model Gateway、Conversation Context、Prompt/模型能力目录和 Evaluation/Guardrail。
- Memory 按 L0 Agent Working、L1 Session、L2 Collaboration/Run、L3 Project 和 L4 User 分层记录规划；现有同步代码作为实验性准备保留，本阶段不继续扩展、不接入实际 `/responses` 调用链，也不作为阶段2完成门禁。Context Preview 中已经存在的 Memory 候选分支属于待收敛代码，不构成稳定集成。
- Knowledge 明确属于独立知识库能力。本阶段不继续实施或验收；现有 ORM、Schema、Service 和 Router 只作为待重新评审的实验性代码，后续另行确定需求、边界和实施阶段。
- Agent 后续只组合已经完成验收的稳定单元，不在工作流节点中临时实现消息历史或上下文裁剪；Memory 和 Knowledge 在各自重新通过启用门禁前不得被 Agent 自动读取。
- 业务调用方通过同一请求、响应和流式事件协议调用 OpenAI、DeepSeek、Anthropic、Gemini 和 OpenAI-compatible 服务。
- 每次模型调用、阶段2 Context Build、Prompt 渲染和 Evaluation 都具有可查询输入来源、结果、耗时或错误。实验性 Memory 与 Knowledge 的证据要求留到各自正式启用规划。
- 明确不处理：RabbitMQ Worker、任务自动领取、总控与工作模型、工具执行循环、MCP、代码搜索、LangGraph、OpenTelemetry 和前端页面。
- 明确不处理：自训练模型选择器、默认多候选答案投票，以及对真实供应商发起默认批量测试请求。

## 当前事实

- 当前实现：阶段1已经完成 User、Session、Message、Run、Task、Model Attempt、Provider Transport Attempt、Run Artifact 以及内部审计资源的新增、查询和必要动作闭环。
- 当前实现：`llm_attempts` 已保存供应商、模型、token、费用、耗时、供应商请求编号、原始请求/响应 URI 和错误；阶段2应复用该表，不创建第二套调用记录。
- 当前实现：`ObjectStorage` 已能保存二进制内容并返回 SHA-256、大小和 `minio://` URI；原始供应商请求和响应应复用该能力。
- 当前实现：`ObjectStorage` 已增加受控删除、按前缀列举和内容 hash 复核；Memory 快照会在登记数据库记录前复核对象。自动孤儿对象巡检与补偿任务尚未实现。
- 当前实现：`POST /api/v1/runs/{run_id}/attempts` 是阶段1的记录写入接口，不具备模型调用能力。阶段2增加统一生成接口后，该接口暂时保留，用于内部迁移和兼容，不作为普通业务调用入口。
- 已验证：四家 Provider、OpenAI-compatible codec、统一 Responses、SSE、重试、费用与原始响应证据；DeepSeek 使用专用 Chat Completions 适配器完成最新模型、思考参数和流终止约束。
- 当前实现及本阶段保持不变：`POST /api/v1/responses` 校验 Run/Task 后直接调用 Model Gateway；不读取 Memory、不创建 Memory Packet。Conversation Context 保持独立预览能力，不在本阶段隐式接入该接口。
- 已验证：Memory 事实账本与确定性检索基线包含正式归属、来源、不可变内容版本、状态、逻辑删除、持久化幂等、并发冲突、词法排序、检索证据和 mutation 审计查询。
- 当前实现：平台只有公共 API Key 与内部 API Key，没有可验证的最终用户认证主体；现有 Memory 详情、纠正和删除按 `memory_id` 与受信调用方边界工作，不能声称已完成用户本人越权隔离。
- 当前实现：L0 手动检查点、L1 Session State/Summary、L2 Handoff/Run Snapshot、L3 Project/Profile、L4 User Profile 及对应内部接口已经存在，并通过 SQLite/伪对象存储契约测试。这些代码只作为 Memory 规划验证和实验性准备保留；Memory Packet 没有创建流程或 Model Attempt 绑定，本阶段不补该链路。
- 当前实现：Context Builder、Prompt Registry、模型能力目录和 Evaluation/Guardrail 已有 ORM、Schema、Service 和 Router，但目前只有路由结构检查，没有各自的行为、状态转换和失败恢复测试，不能视为完成。Context Preview 仍有默认开启的 Memory/Knowledge 候选分支，阶段2收敛时必须禁用或移出稳定合同。Knowledge 也存在结构代码，但已经移出阶段2，不能据此声明知识库能力可用。
- 当前结论：Model Gateway 已完成不等于阶段2完成，阶段2状态保持“进行中”。
- 当前约束：后端依赖必须安装在 `apps/api/.venv`，测试、迁移和静态检查均从该虚拟环境运行。
- 当前应以什么为准：用户目标和本计划优先；统一请求/响应结构、MySQL 事实边界和 MinIO 大对象边界以总技术方案为准；具体字段以实际 ORM 模型和迁移为准。
- 待确认事项：各环境可用 API Key、代理地址及费用表不写死在本计划中；模型 allowlist 通过环境配置提供，后续由 Model Catalog 保存有来源的能力快照。Memory 的正式启用阶段和 Knowledge 的独立路线图均待后续决策。

## 已确认的实施边界

### 身份与资源归属

- 当前公共接口使用平台级 API Key，只能视为受信调用方管理接口；阶段2不把请求中的 `owner_id` 解释为已经验证的最终用户身份。
- 阶段2所有资源必须在 Service 和数据库查询中校验 User、Project、Session、Run、Task、Agent Run 的关系，防止受信调用方误把一个用户或项目的资源关联到另一个 scope。
- 直接面向最终用户的身份认证和授权不在阶段2实现范围。现有实验性 Memory/Knowledge 接口不得被前端视为最终用户能力；正式开放前必须先确定认证主体、权限声明和越权响应。
- 内部评估、规则管理和模型目录写入接口继续要求公共 API Key 与内部 API Key，不能仅依赖公共密钥。

### 删除与保留

- 阶段1的 Message 是不可变历史，不增加通用删除接口作为阶段2前置条件。
- 现有用户长期记忆事实必须提供逻辑删除；删除后不再参与检索，但保留最小审计字段、来源标识和删除时间。当前 Memory 正文仅存在 MySQL。规划中的 JSON/Markdown 快照还必须处理 MinIO 对象、当前快照指针和模型读取包，不能用当前单库删除行为代表五层 Memory 已经完成。
- Knowledge 的停用、版本替换、对象保留和清理策略留到知识库独立规划中确定；阶段2不固定这些公共合同。
- Context Build、模型调用和 Evaluation 是执行证据，不提供普通修改接口；其敏感大对象按统一保留策略处理。
- 物理删除、法定保留期和最终用户数据导出属于部署策略，不能由业务 Service 静默猜测默认值。

### 资源限制

- 所有列表继续使用签名 cursor，默认 `limit=20`，允许范围为 1～100；cursor 必须绑定完整查询条件。
- Context 请求必须设置平台允许范围内的 `token_budget`，第一版范围为 256～200000；实际可用预算还必须受模型目录中的 context window 和预留输出 token 限制。
- 当前实验性用户长期记忆事实正文只保存在 MySQL，单个不可变版本的 `content_text` 长度为 1～4000 个 Unicode 字符；超过上限的资料只能作为 Artifact 保存，不能用 `content_uri` 绕过限制。未来是否进入 Knowledge 必须等待知识库规划。L1～L4 快照和 L0 检查点规则只作为 Memory 设计合同保留。
- 每个 Memory 版本必须具有 1～20 个来源，正文最多保留 256 个不重复的哈希检索词；单次查询最多生成 64 个查询词。
- 实验性 Memory 检索查询长度为 1～2000 个字符，返回数量范围为 1～100，独立 token 估算预算范围为 1～20000；未来若允许 Context Builder 使用 Memory，必须使用目标模型 tokenizer 重新核算。
- Knowledge 的上传、分块、检索数量和资源上限不在阶段2承诺，后续知识库规划必须重新确定并验证。
- Prompt 变量数量、渲染结果长度、规则数量和 Evaluation 输入长度必须有配置上限；超过上限返回输入错误，不进行截断后伪装成功。

## 阶段2核心能力范围

### Conversation 与 Context

Conversation Service 是读取和编排阶段1 Session/Message 的应用服务，不创建第二套会话或消息表。

第一版接口：

```text
POST /api/v1/context-builds/preview
GET  /api/v1/context-builds/{context_build_id}
```

请求至少包含 `user_id`、`session_id`、`provider`、`model`、`token_budget`，可以包含 `system_instruction` 和预留输出 token。阶段2稳定合同只选择显式指令与 Session Message；现有 Memory/Knowledge 开关属于实验性字段，不进入 `/responses`，在后续对应能力重新规划前不得作为稳定接口承诺。返回统一消息、总 token 估算、各来源记录以及未选中原因。

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

- 阶段2选择顺序固定为 system instruction、必要安全指令和最近不可分割消息；各来源使用独立预算，不能由单一长来源耗尽全部空间。Memory、Knowledge 和自动历史摘要注入留待对应能力正式启用后重新设计。
- 同一请求使用固定 tokenizer 名称、版本、来源版本和排序键；重复读取 `context_build_id` 必须能够解释当时输入，不使用当前最新内容覆盖历史证据。
- 单条消息超过该类预算时返回明确超限原因；不在 UTF-8 字符、工具调用结构或消息结构中间截断。
- 长对话摘要的形成和启用策略暂不纳入实际响应链路，原始 Message 始终保持不可变。
- Context 构建与 Provider 调用分离；预览不发起模型请求、不产生供应商费用，也不会被 `/responses` 自动使用。

### Memory：规划边界与实验性准备代码

Memory 设计为独立、可查询的长期事实账本，不是 Agent 工作流内部的临时字典，也不是知识库的第二套文档库。仓库中已有 Store、Policy、确定性 Retrieval 和 L0～L4 同步准备代码，但当前决策是只保留规划与实验性验证：不继续补全，不接入 Context Builder 或 `/responses`，不把它描述为模型运行时能力。

当前代码的 `memory_type` 只允许：

```text
working_context  必须绑定 Session、Run 或 Task 之一，并必须设置过期时间
session_episode  必须绑定 Session，表示会话中已发生事件的摘要
user_fact         有明确来源的用户事实
user_preference   用户明示或已确认的偏好
execution_lesson  必须绑定 Run 或 Task，表示已验证的处理约束或过程摘要
```

外部文档、长文本和通用资料属于 Knowledge，不得以 `memory_type` 扩展或 `content_uri` 的方式写入 Memory。五层机制实施时，`user_fact` 与 `user_preference` 继续由本账本承担；`working_context`、`session_episode` 和 `execution_lesson` 只为存量兼容保留，新的职责分别迁移到 Agent Working State/Session State、Session Summary 和 Lesson Candidate，不删除现有记录或枚举值。

第一版接口：

```text
POST   /api/v1/memories
GET    /api/v1/memories
GET    /api/v1/memories/{memory_id}
GET    /api/v1/memories/{memory_id}/versions
PATCH  /api/v1/memories/{memory_id}
DELETE /api/v1/memories/{memory_id}
POST   /api/v1/memory-retrievals
GET    /api/v1/memory-retrievals/{memory_retrieval_id}
```

`POST` 只接受具有来源的 Memory。受信调用方可以明确创建 `candidate` 或 `active`；模型生成内容必须以 `candidate` 写入，只有后续显式策略或受信调用方确认才能激活。创建、纠正、激活和检索请求都必须提供最大 128 字符的幂等键；同键同请求不会重复应用，写接口返回该稳定 Memory 的当前表示，检索接口返回原有检索证据；同键不同请求返回状态冲突。

#### 归属和范围

- `user_id` 始终必填，并通过 User 外键和 Service 校验表示受信调用方代表的资源所有者。
- `session_id`、`run_id`、`task_id` 是可建立真实外键的显式可选范围列，不使用 `scope_type` 或无法建立参照完整性的 `owner_type + owner_id`。它们可以同时表达一条 User → Session → Run → Task 关系链；外键使用 `RESTRICT`，禁止删除父资源后把受限 Memory 或历史检索证据静默扩大为更宽范围。
- Service 必须验证 Session 属于 User、Run 属于 User 且与已传 Session 一致、Task 属于已传 Run；任何链路关系不一致都返回资源冲突。
- 检索请求必须带 `user_id`，并可带当前 Session、Run、Task 链路；结果只能来自该 User，且 Memory 中任一非空下级范围都必须与检索链路的对应标识一致。

#### 持久化模型

```text
llm_memories
memory_id, user_id, session_id, run_id, task_id,
memory_type, status, current_version_number, semantic_key,
active_semantic_key_hash, supersedes_memory_id, expires_at, deleted_at,
creation_idempotency_key, creation_request_hash, created_at, updated_at

llm_memory_versions
memory_version_id, memory_id, version_number, content_text,
content_hash, normalized_content_hash, importance, confidence,
estimated_token_count, token_estimator_version, unique_search_term_count,
created_by_type, created_by_attempt_id, content_erased_at, created_at

llm_memory_mutations
memory_mutation_id, memory_id, idempotency_key, request_hash,
result_version_number, result_status, created_at

llm_memory_sources
memory_source_id, memory_version_id, source_type, source_resource_id,
source_content_hash, source_order, created_at

llm_memory_search_terms
memory_version_id, term_hash, term_frequency

llm_memory_retrievals
memory_retrieval_id, idempotency_key, user_id,
session_id, run_id, task_id, query_text, query_hash,
memory_types_json, result_limit, token_budget, candidate_method,
ranker_version, status, request_hash, created_at, completed_at

llm_memory_retrieval_results
memory_retrieval_result_id, memory_retrieval_id, memory_id,
memory_version_id, rank, total_score, lexical_score, scope_score,
importance_score, confidence_score, recency_score,
estimated_token_count, selected, exclusion_reason
```

- `memory_id` 是稳定的逻辑资源；正文、重要度或置信度变化时新增不可变的 `llm_memory_versions` 记录，并在同一事务将 `current_version_number` 增加一，不覆盖旧版本。
- 普通 `PATCH` 必须带 `expected_version_number`；Service 对 `llm_memories` 行加锁，版本已变化时返回冲突，防止并发纠正相互覆盖。
- 每个版本至少关联一个已校验来源；`source_type` 第一版只允许 `message | model_attempt | artifact | tool_call | trusted_request`。Message、Attempt、Artifact 和 Tool Call 来源必须属于同一 User；当 Memory 声明 Session/Run/Task 范围时，每一个来源还必须精确证明对应的非空范围，不能用一条合法来源掩盖另一条范围不符的来源。来源表保存标识、内容哈希和顺序，不复制敏感正文。
- `trusted_request` 表示受信服务边界提供的请求标识，只能用于 User 级 Memory；它没有 Session/Run/Task 归属，不能证明下级范围。来源哈希由该标识确定生成，但不代表已完成最终用户身份验证。`created_by_type=model_attempt` 必须带归属一致的 `created_by_attempt_id`，而且新 Memory 只能是 `candidate`。
- `llm_memory_search_terms` 由带版本的确定性规则从正文生成：Unicode NFKC、大小写与空白归一，拉丁词使用规范化 token，中文使用重叠二元词。表中只保存 SHA-256 词哈希和频次，每个版本/词哈希组合唯一，不保存明文检索词。未加盐的确定性哈希只减少偶然明文暴露，不是加密，数据库访问仍应视为可推断敏感内容的高权限边界。
- 创建幂等事实保存在 `llm_memories.creation_idempotency_key/creation_request_hash`；内容纠正和纯状态变更统一保存在 `llm_memory_mutations`，记录请求哈希及当次结果版本/状态，不用进程内缓存代替持久化幂等记录。重放只保证不重复应用；写接口返回当前资源表示，不伪称能够重建后续变更前的完整历史响应。
- `PATCH` 只有在创建新内容版本时才接受 `sources`；纯状态或元数据变更携带来源会在 Schema 层拒绝，避免证据被静默丢弃。`expires_at` 输入必须带时区并在写库前归一化为 UTC。
- 普通更新不能修改已存版本。逻辑删除是唯一的隐私擦除例外：删除事务将状态改为 `deleted`、清空所有版本正文和检索词，将 token/词数归零并记录 `content_erased_at`，仅保留内容哈希、来源标识/哈希和时间。这不会删除独立的原始 Message/Artifact。

#### 状态、幂等和并发

状态只允许：

```text
candidate -> active -> superseded
candidate -> rejected
candidate | active | rejected | superseded -> deleted
```

- `rejected | superseded` 不能重新激活，但仍允许执行隐私删除；`deleted` 是最终状态。非法转换返冲突，需恢复内容时创建新的稳定 Memory 并保留新来源。
- 一般纠正在同一 `memory_id` 下创建新版本；与已激活事实相冲突的新事实创建新 `memory_id`，并在同一事务将旧 Memory 标记为 `superseded`。
- `active_semantic_key_hash` 是 User/Session/Run/Task 范围、`memory_type` 和归一化 `semantic_key` 的唯一哈希，只在 `active` 状态有值。MySQL 唯一约束负责阻止两个并发事务激活同一语义槽位；未提供 `semantic_key` 时不猜测语义去重，唯一冲突转换成稳定的资源冲突错误。
- 创建 Memory、首版、来源和幂等记录在同一 MySQL 事务提交；纠正、冲突替换、激活和删除同样只有一个数据库事务边界。Memory 第一版不访问 MinIO，因此不存在 MySQL/MinIO 双写成功的虚假保证。
- `DELETE` 重复请求返回同一删除结果。删除与纠正通过同一 Memory 行锁串行化：删除先提交时纠正返回冲突；纠正先提交时随后删除仍可成功，最终以隐私删除为准，不错误承诺两个请求只能有一个成功。
- 过期不是持久化状态。检索和 Context 始终使用 `expires_at` 与当前时间过滤过期 Memory，不依赖尚未实现的 Worker 修改状态；过期记录仍可通过列表和详情接口查询审计，但不得参与检索或 Context。

#### 确定性检索与证据

- Retriever 先按 `user_id`、已提供的 Session/Run/Task 链路、`active`、未过期和可选 `memory_type` 过滤，再使用查询词哈希与当前 Memory 版本的词哈希计算匹配。已删除、拒绝、已替代或过期内容绝不进入排序。
- 查询与正文使用同一确定性分词规则。`memory_ranker_v1` 使用整数基点计算：60% 词集 Jaccard 相关度、15% scope 精确度、10% importance、10% confidence 和 5% recency；各分量与总分都保存在检索结果证据中。
- 最终稳定排序使用 `total_score DESC, updated_at DESC, memory_id ASC`。同一 User 下同一幂等键重试直接返回原检索记录和排序结果，不重算分数。
- token 估算第一版使用 `utf8_bytes_upper_bound_v1`，按每个 UTF-8 字节最多预算一个 token，作为保守且可复现的上界；它不伪装成目标模型的精确 tokenizer，Context Builder 必须重新核算。单项超出剩余预算时记录 `token_budget_exceeded` 并继续检查后续项，不截断正文。
- 每次检索都写入请求哈希、查询文本/哈希、类型条件、候选/排序策略版本及有界结果的各分量。响应可返回同一 User 下被 token 预算排除的有界候选及原因，但不得返回跨 User、状态不可用、过期或已擦除的正文。

#### 为什么第一版不引入向量存储（Vector Store）

- 当前 Model Gateway 只有文本生成契约，没有供应商中立的向量嵌入（Embedding）请求、维度、费用、重试和调用证据契约；直接引入向量库会让新的外部副作用绕过现有记录边界。
- 尚无固定中英文检索样本证明语义检索能提升实际召回，也没有数据规模证据证明有界 MySQL 候选集无法满足时延。向量相似不等于事实正确，不能替代 scope、状态、过期和来源过滤。
- 第一版先用可解释排序建立评估基线，避免同时引入 Embedding 供应商、模型迁移、异步索引、删除一致性和新运维系统。
- 当前部署固定为 MySQL 8.4 Community。其官方 8.4 数据类型目录没有 Vector 类型；把浮点数组写进 JSON/BLOB 再由 API 进程全表计算距离既没有近似最近邻索引，也会形成无界内存和扫描成本，因此不作为在线检索的替代实现。

如果后续门禁证明 Memory 确实需要语义召回，条件性首选候选为：继续以 MySQL 为事实源时，优先评估 Qdrant 作为独立、可重建的向量投影；只有主事实数据库因独立业务原因迁移到 PostgreSQL 时才改评 pgvector；Milvus 只在数据规模和分布式吞吐已经证明需要时评估；FAISS 只用于离线正确性或召回基线，不承担多进程在线存储。Knowledge 可以复用 Embedding 和投影基础契约，但必须使用独立 profile、collection、评估集、排序权重和删除策略，不能用 Knowledge 的规模需求反向证明 Memory 需要向量库。

未来只有在以下门禁全部满足后才能增加向量投影（Vector Projection）：

1. 先证明当前词法基线无法满足已经声明的需求，例如固定样本 Recall/SLO 不达标，或 500 条候选上限、P95 时延和数据规模已经成为实测瓶颈；否则不增加新的在线基础设施。
2. 建立固定、去敏的多语言检索样本，在实验前冻结 Recall@k、nDCG@k、P95 时延、单次费用和跨 User/Scope 泄漏必须为 0 的阈值；向量或混合检索必须达到预定改善线，不得在看到结果后改验收条件。
3. 先实现供应商中立 Embedding 契约和调用证据，明确 provider、model、revision、dimension、distance metric、content hash、费用、超时、有限重试和不可重试错误；同时确定哪些 Memory/查询文本允许发送给哪个供应商、脱敏和保留规则，禁止默认外发敏感长期记忆。
4. 增加 `embedding_profiles` 与 `memory_vector_projections` 等可重建投影事实。前者至少记录 profile、供应商、模型修订、维度、距离度量和状态；后者以 `(memory_version_id, profile_id)` 唯一，记录确定性 point ID、内容哈希、投影代次、`pending | indexed | delete_pending | deleted | failed`、尝试次数、下次重试、最后错误和完成时间。MySQL 仍是唯一事实源，向量库只是可丢弃重建的派生投影。
5. 验证 owner/scope 前置过滤和返回后二次校验、重建、模型换代、重复投递、删除墓碑、索引落后和外部服务不可用；降级时响应必须明示实际使用的检索策略，不能静默改变语义。
6. 阶段3的 Outbox Publisher、RabbitMQ 和可重放投影 Worker 已经可用，并复用阶段1的 `llm_outbox_events` 事实表；Memory API 事务只提交事实、期望投影状态和 outbox 事件，绝不在数据库事务内同步调用 Embedding 服务或向量库。

未来只投影“当前版本、`active`、未过期且正文未擦除”的 Memory；`candidate/rejected/superseded/deleted` 和历史版本禁止进入投影。Worker 每次 upsert 前重新读取 MySQL，目标失效时只执行 delete。向量库只保存确定性 point ID、Memory/版本/Profile 标识、owner/scope 过滤字段、内容哈希、代次和过期时间，不复制 `content_text`，但向量本身仍按敏感派生数据保护。查询先在向量库按 owner/scope 过滤，再回 MySQL 批量复核 owner、范围、状态、当前版本、过期时间和内容哈希，正文始终只从 MySQL 读取。

删除先在 MySQL 中使事实立即不可检索并写入墓碑/outbox，投影记录进入可查询的 `delete_pending`；外部删除采用有限重试、最终失败告警和部署级清理时限，并以 `deleted_at` 证明物理投影清理完成。即使残留 point 也不能通过 MySQL 二次校验。模型换代使用新 collection 代次完整重建和验证后再原子切换别名，不在同一 collection 中混写不同维度。混合检索只能对有界词法/向量候选并集使用版本化融合算法，并持久化 lexical/vector/fusion 分数、`profile_id`、策略版本和实际降级原因，不能用单一向量相似度覆盖现有确定性证据。

### L0～L4 五层 Memory 目标机制与同步最小实现

Memory 的长期目标不是把所有历史文字塞回模型，而是按执行主体和生命周期提供五种边界明确、可版本化且可追溯的信息。`llm_memories` 的现有实验性代码作为长期事实账本候选保留；自动形成、后台压缩、定时擦除、Memory Packet 和模型调用集成都不属于当前阶段验收，未来启用时必须重新评审。

| 层级 | 名称 | 解决的问题 | 默认生命周期 | 写入者 | 默认读取者 | 不保存 |
|---|---|---|---|---|---|---|
| L0 | Agent Working Memory | 单个 Agent 当前执行到哪里、正在等待什么、下一步做什么 | 单个 Agent Run 运行期；终止后短期保留 | 该 Agent 的执行协调器 | 该 Agent；Controller 仅用于恢复和诊断 | 隐藏推理、完整消息、完整工具结果、其他 Agent 状态 |
| L1 | Session Memory | 当前会话正在讨论什么、已经确认什么、哪些旧消息仍相关 | Session 活跃期及部署保留期 | Session Memory Service；摘要可由受控模型生成 | 未来启用后的 Context Builder、当前 Session 内授权 Agent | 跨会话项目规则、全局用户偏好、隐藏推理 |
| L2 | Collaboration / Run Memory | 同一次 Run 内不同 Agent 已完成什么、证据在哪里、下一步必须遵守什么 | Run/Task 执行期及审计保留期 | Agent 执行协调器和 Run Memory Service | 下游 Agent、Controller、Reviewer、Verifier | 单个 Agent 尚未提交的临时发现、隐藏推理、整个工作目录副本 |
| L3 | Project Memory | 跨 Session/Run 持续存在的项目事实、决定、术语、约束和规则 | Project 有效期，直到纠正、替代、过期或删除 | Project Memory Service；Agent/模型只能提交候选 | 绑定同一 Project 的 Session、Run 和 Agent | 用户全局偏好、当前 Run 进度、未验证代码猜测、凭据 |
| L4 | User Memory | 用户跨全部 Project 保持稳定的身份、偏好和长期限制 | 直到纠正、替代、过期或删除 | 受信调用方；模型只能提交候选 | 未来启用后的 Context Builder、经同一 User scope 校验的 Agent | 项目专属约定、外部资料全文、未确认推断、凭据和密钥 |

外部方案校验结论：LangChain/LangGraph 的官方 Memory 概念把短期记忆限定在线程/会话范围，并把跨会话长期记忆区分为事实、经历和过程规则；它也明确区分 Profile 与事实集合，以及请求热路径写入和后台形成。本计划采用会话/长期事实、核心 Profile/细粒度集合和 M4 后台形成的边界，但不引入 LangGraph Store 作为平台事实源。Mem0 官方开源方案默认组合 LLM、Embedding、Vector Store 和历史库，近期算法与 Graph 支持也有变化；这证明它是可评估的抽取/检索组件，而不是当前 MySQL 审计模型的无成本替代。Qdrant 的 payload filter 可以辅助向量候选过滤，但近似检索和 payload 过滤不能代替 NexusPilot 的 User/scope/删除权限复核。因此本计划先完成可审计事实与确定性基线，再由 M5 评估向量组件。

规划原则：MySQL 保存业务事实、状态、版本、来源和当前指针；L1～L4 的持久快照可由 MinIO 保存不可变结构化 JSON 与确定性 Markdown 视图；JSON 是规范内容，Markdown 仅用于人工检查，不是第二事实源。L0 是不超过 800 token 的运行检查点，只写 MySQL。未来若启用模型读取，必须另行实现固定版本的 Memory Packet；当前 `/responses` 不创建或读取 Packet。

建议初始预算使用“目标值/硬上限”，而不是保证每层都会占满的配额：

| 内容 | 目标值 | 硬上限 | 超限处理 |
|---|---:|---:|---|
| L4 用户核心记忆 | 500 tokens | 800 tokens | 按已确认禁止、稳定偏好、身份事实选择，不截断单项 |
| L3 项目核心记忆 | 800 tokens | 1500 tokens | 按有效决定、项目约束、术语和稳定事实选择 |
| L1 会话摘要 | 800 tokens | 1500 tokens | 生成新摘要版本；原 Message 不变 |
| L2 Run 共享记忆 | 1000 tokens | 2000 tokens | 归并已完成 Handoff，排除可由引用恢复的大文本 |
| L2 直接前置 Handoff | 每条 500 tokens | 每条 1000 tokens | 默认最多 4 条且合计不超过 3000 tokens，更多内容通过 Artifact 引用 |
| L0 Agent Working State | 300 tokens | 800 tokens | 删除低价值搜索轨迹，保留恢复所需状态，不截断 JSON |

这些数字作为未来启用门禁的预算基线，届时必须进入带上下限配置并记录目标模型 tokenizer 名称和版本。当前 Context Builder 和 `/responses` 不给 Memory 分配运行时预算；未来预算不足时必须整项排除并记录原因，不能截断 JSON、Handoff 或事实正文。

#### 与当前 Memory 代码的兼容关系

- `user_fact` 和 `user_preference` 继续由现有 `llm_memories`、`llm_memory_versions`、`llm_memory_sources` 与检索证据表承担。
- 当前 `candidate` 对应候选状态，`active` 对应已批准且可使用状态，`rejected` 对应拒绝状态。阶段2计划增加显式 `expired` 状态用于候选审批生命周期；正式事实仍同时使用 `expires_at` 做读取时过滤，不能依赖后台任务及时改状态。
- `working_context`、`session_episode` 和 `execution_lesson` 为存量兼容保留；新的写入职责分别转移到 Agent Working State/Session State、Session Summary 和 Lesson Candidate。迁移前不得删除枚举或历史记录。
- L3 Project Memory 的细粒度事实继续复用 `llm_memories` 的不可变版本、来源、状态和检索证据，但必须增加 `project_id` 和项目专属类型；L4 User Profile 必须排除所有带 `project_id` 的事实，避免把项目约定升级成全局偏好。
- 不新建第二套用户事实表，也不增加独立 `llm_memory_candidates`。候选、批准、拒绝、替代和删除继续是稳定 `memory_id` 的生命周期，避免候选批准时复制正文并丢失来源。
- 当前 `trusted_request` 只证明受信服务传入了一个请求标识，不证明最终用户本人确认。用户确认来源必须等最终用户认证主体确定后才能声称成立。

#### 通用快照对象与 MinIO 规则

新增 `llm_memory_snapshot_objects`，统一登记 L1～L4 Memory 生成的不可变对象：

```text
memory_snapshot_object_id, memory_layer, user_id, project_id,
session_id, run_id, task_id,
object_type, schema_version, object_version, storage_uri, content_hash,
size_bytes, mime_type, status, created_at, deleted_at, cleanup_error
```

- `user_id` 必填，`project_id` 只用于 L3，其余范围字段按对象类型建立可验证关系；`storage_uri` 唯一。
- 继续使用现有 MinIO bucket，不为 Memory 创建第二套对象存储客户端。对象键固定为：

```text
memory/v1/{memory_layer}/{scope_id}/{object_type}/{yyyy}/{mm}/{dd}/
{utc_timestamp}_v{version:06d}_{object_id}_{sha256前8位}.{json|md}
```

- 对象键只含不透明 ID、UTC 时间、固定宽度版本和哈希，不含用户名、标题、提示词或其他个人信息。
- 同一版本的 JSON 和 Markdown 各有独立对象记录，并通过所属业务快照行关联；对象创建后不覆盖，也不使用 `latest.json` 或 `latest.md`。当前版本只能通过 MySQL 指针确定。
- 现有 `llm_artifacts` 要求 `run_id`，语义是 Run 产物，不适合承担 User/Session 快照。Run 内大型测试结果、补丁和报告继续使用 `llm_artifacts`；Memory 快照使用上述新表。
- `ObjectStorage` 需要在现有 `put/open` 基础上增加受控删除、按确定性前缀列举和内容哈希复核能力，用于孤儿对象清理和一致性巡检；不得让 API 端点接受任意对象键。

所有结构化对象使用统一信封：

```json
{
  "schema_version": "session_state.v1",
  "object_id": "opaque-id",
  "version": 3,
  "created_at": "2026-08-03T00:00:00Z",
  "source_ids": ["message-id-or-handoff-id"],
  "content": {}
}
```

`schema_version` 变化表示结构兼容边界，`version` 表示同一业务对象内容演进；读取端遇到未知结构版本必须明确失败，不能猜测字段或把未知字段静默丢弃。

#### L0 Agent Working Memory

Agent Working Memory 是单个 Agent Run 的可恢复操作状态，不是对话历史，也不是提供给其他 Agent 的交接结果。当前仓库已有最小 `llm_agent_runs`、`llm_agent_turns`、检查点模型和内部接口，用于验证版本、恢复和终止边界；它们不构成 Agent Runtime，实际自动写入、工具状态复核和执行循环仍由阶段5实现。

不能只使用 L1 Session Memory：同一 Session 中可以并行存在 Controller、多个 Researcher、Planner 和 Reviewer，它们具有不同目标、文件读取进度、待处理工具和预算。如果共用一份 Session State，并发检查点会互相覆盖，Reviewer 还可能看到实现 Agent 尚未验证的临时发现。L1 只保存用户会话共享事实；每个 Agent 使用独立 L0，完成后再通过 L2 Handoff 发布可依赖结果。

在计划中的 `llm_agent_runs` 基础上新增：

```text
llm_agent_turns
agent_turn_id, agent_run_id, turn_sequence, turn_type, status,
model_attempt_id, started_at, completed_at, error_code, created_at

llm_agent_working_state_versions
agent_working_state_version_id, agent_run_id, agent_turn_id,
version, status, state_json, previous_version_id,
estimated_token_count, tokenizer_name, tokenizer_version,
idempotency_key, request_hash, expires_at, content_erased_at, created_at
```

`llm_agent_runs` 增加 `current_working_state_version_id` 和 `current_turn_sequence`。`AgentRun` 表示一个角色承担一个 Task 的完整执行；`AgentTurn` 表示该执行中的一次模型/工具循环步骤；Model Attempt 仍表示一次逻辑模型调用，Tool Call 仍表示真实工具调用。Working State 只能引用这些事实，不能复制原始请求、供应商响应、完整工具结果或费用。

`agent_working_state.v1` 至少包含：

```json
{
  "current_objective": "当前具体目标",
  "searched_queries": ["已执行的有价值搜索词"],
  "files_read": [{"file_reference": "workspace-relative-path", "content_hash": "..."}],
  "rejected_hypotheses": [{"hypothesis": "...", "evidence_ids": ["..."]}],
  "next_actions": ["下一步准备读取或验证什么"],
  "pending_tool_call_ids": ["..."],
  "remaining_budget": {"input_tokens": 0, "tool_calls": 0, "time_seconds": 0},
  "tentative_findings": [{"finding": "尚未提交的发现", "source_ids": ["..."]}],
  "blocked_on": []
}
```

- 在 Agent Run 启动前创建首版；每个完整 Agent Turn、进入工具等待、人工等待、暂停或可恢复重试之前创建检查点。逐 token、每个日志行和工具轮询不写版本。
- 采用期望版本和 Agent Run 行锁切换当前指针；崩溃后从最后一个 active 检查点恢复。恢复必须重新核对待处理 Tool Call 的实际状态，不能把状态 JSON 当成工具已完成证据。
- 只有该 Agent 执行协调器可以写入。该 Agent 及恢复协调器可以读取；其他工作 Agent 默认不可见。Controller 可以读取状态和阻塞原因用于取消/恢复，Reviewer 只能读 Handoff、Artifact 和测试证据。
- `status` 使用 `active | superseded | finalized | erased`。Agent Run 结束后立即停止注入，最终状态保留到可配置清理期；建议默认 7 天、允许 1～30 天，之后擦除 `state_json` 并保留版本、hash 和时间审计。最终保留期仍受部署策略约束。
- 结束时只有经来源校验的结论可以提升到 L2 Handoff；Working State 本身不会自动写入 L1、L3 或 L4。
- 内部接口为 `POST /api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints`、`GET /api/v1/internal/agent-runs/{agent_run_id}/working-memory` 和版本列表；必须同时校验公共/内部 API Key，外部业务调用方不直接修改。

与 Agent Runtime 不冲突的判断：L0 负责“这个 Agent 现在做到哪一步”，L2 Handoff 负责“其他 Agent 可以依赖什么结果”，Run/Task 负责正式执行状态，Attempt/Tool Call 负责真实调用事实。Memory Packet 仅作为未来“本轮模型实际读了什么”的设计对象；当前不创建。未来必须继续保持这些引用关系，避免形成第二套状态机、调用记录或共享进度。

#### L1 Session Memory

Session Memory 由原始 Message、版本化 Session State、版本化 Session Summary 和有界的相关旧消息组成。原始 `llm_messages` 保持不可变并继续作为最终证据；摘要不能覆盖或删除原消息。

新增表：

```text
llm_session_states
session_state_id, session_id, schema_version, version, status,
state_json, previous_state_id, state_through_message_id,
state_through_message_sequence, generation_attempt_id,
json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_session_summaries
session_summary_id, session_id, schema_version, version, status,
summary_json, previous_summary_id, summary_from_message_sequence,
summary_through_message_id, summary_through_message_sequence,
generation_attempt_id, json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code
```

`status` 使用 `generating | active | failed | superseded`。`llm_sessions` 增加可空的 `current_state_id` 和 `current_summary_id`；外键必须在快照表建立后再增加，且只能指向同一 Session 的 active 版本，Service 事务中再次校验该关系。

`session_state.v1` 至少包含：

```json
{
  "goal": "当前用户目标",
  "constraints": ["已确认限制"],
  "decisions": [{"decision": "...", "source_message_ids": ["..."]}],
  "open_questions": ["尚待确认的问题"],
  "active_entities": [{"kind": "project", "reference": "opaque-id"}]
}
```

`session_summary.v1` 至少包含摘要覆盖范围、已经确认的事实、主要决策、未解决事项和重要产物引用。生成规则如下：

- 默认在未摘要消息达到 20 条、估算输入达到 8000 token，或绑定该 Session 的 Run 结束时触发；这些值进入带上下限的配置，固定样本评估后才能调整。
- Context 默认携带当前摘要、最近 12 条完整消息和通过有界检索选出的相关旧消息；不默认重新加载完整会话。摘要目标为 800 token、硬上限为目标模型 tokenizer 的 1500 token。
- Worker 未实现前，只提供显式或内部同步生成入口；不得以 FastAPI `BackgroundTasks` 假装具备可恢复的后台形成能力。Worker 完成后再增加异步刷新。
- 增量摘要必须记录上一摘要和精确消息序列区间；累计 5 个增量版本后是否重建完整摘要由固定样本评估决定，不能无限递归拼接摘要。
- 摘要生成失败时旧 active 版本继续服务，新版本记录 `failed` 和安全错误；消息写入不因摘要失败而回滚。
- 第一版相关旧消息先评估 MySQL FULLTEXT。部署启用中文 `ngram` parser 的可用性和迁移行为必须用 MySQL 8.4 实测；不满足时复用有界确定性词项索引，禁止无范围的 `%LIKE%` 或全表载入。

#### L2 Collaboration / Run Memory

Collaboration Memory 规划服务于一次 Run 内的 Agent 交接。Agent Handoff 是不可变交接事实，Run Memory Snapshot 是对已完成 Handoff 的版本化归并；Memory Packet 是未来某次 Agent/模型调用实际收到的精确输入清单，本阶段不创建也不接入模型调用。

新增表：

```text
llm_agent_runs
agent_run_id, run_id, task_id, agent_role, status,
provider, model, started_at, completed_at, created_at

llm_agent_handoffs
agent_handoff_id, agent_run_id, run_id, task_id, schema_version, version,
status, handoff_json, supersedes_handoff_id,
json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at

llm_run_memory_snapshots
run_memory_snapshot_id, run_id, schema_version, version, status,
state_json, previous_snapshot_id, expected_previous_version,
json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_run_memory_snapshot_handoffs
run_memory_snapshot_id, agent_handoff_id, handoff_order

llm_memory_packets
memory_packet_id, agent_run_id, run_id, task_id, user_id,
schema_version, packet_json, user_profile_snapshot_id,
project_memory_profile_snapshot_id, session_state_id, session_summary_id,
run_memory_snapshot_id, agent_working_state_version_id,
estimated_token_count, tokenizer_name, tokenizer_version,
json_snapshot_object_id, created_at

llm_memory_packet_items
memory_packet_id, item_type, resource_id, resource_version,
item_order, selected_token_count, content_hash
```

现有实验性模型已经为 `llm_runs` 准备可空 `current_memory_snapshot_id`，并为 `llm_attempts` 预留可空 `memory_packet_id`；当前响应链路不得写入或依赖这些字段。未来若启用，Attempt 的原始请求仍保存动态消息、工具定义和最终厂商负载，Packet 只记录 Memory 输入及精确版本，两者不能互相替代。

`agent_handoff.v1` 至少包含：

```json
{
  "objective": "本 Agent 收到的完整目标",
  "status": "completed",
  "confirmed_facts": [],
  "decisions": [],
  "files_read": [],
  "files_changed": [],
  "artifacts": [],
  "tests": [],
  "remaining_work": [],
  "risks": [],
  "unknowns": [],
  "invariants_for_next_agent": []
}
```

- Agent 执行协调器负责提交，Collaboration Memory Service 校验 Task/Run/Agent Run 归属和 Schema。完成状态缺少必要字段时，Task 不能通过 Handoff 完成门禁；异常退出可以写 `partial` Handoff。
- Handoff 状态允许 `completed | partial | corrected | superseded`；内容不可覆盖，纠正创建新行并用 `supersedes_handoff_id` 指向旧版本。
- 下游 Agent 默认只读取依赖祖先 Task 的有效 Handoff、当前 Run Snapshot 和显式 Artifact，不读取整个 Run 的所有历史。Controller、Reviewer 和 Verifier 可以按职责申请更宽但仍有界的读取范围。
- Run Memory 合并时对 Run 当前指针加锁，校验 `expected_previous_version`，从确切 Handoff 集合生成新 JSON/Markdown，再原子切换指针。冲突事实写入 `conflicts`，由 Controller 或 Reviewer 明确裁决，不能按最后写入时间静默选择。
- `run_memory_state.v1` 至少包含目标、已完成工作、关键决策、变更文件、Artifact、测试证据、剩余工作、风险、冲突和必须保持的约束。
- Run Memory 目标为 1000 token、硬上限 2000 token。每条直接前置 Handoff 目标 500 token、硬上限 1000 token；默认最多选择 4 条且合计不超过 3000 token。超过范围的测试、补丁和日志只保留 Artifact 引用。
- `agent_memory_packet.v1` 固定角色、目标、预算、Agent Working/User/Project/Session/Run 版本以及选中的 Handoff、Memory、Message 和 Artifact 引用。Packet 创建后不可修改；供应商重试默认复用相同 Packet，除非上层明确创建新的逻辑 Attempt。

`RUN_MEMORY.md` 由 Run Snapshot 确定性渲染，固定为“目标、已完成、关键决策、文件与产物、测试、剩余工作、风险与冲突、后继约束”。它不包含 Agent 隐藏推理、完整终端输出或未引用的大型文件。

#### L3 Project / Workspace Memory

Project Memory 规划为跨 Session 和 Run 的稳定项目范围，不是工作台账或项目管理系统。仓库已经存在实验性的最小 Project 实体，但尚未作为正式产品能力验收；不能用仓库目录、Git remote、Session title 或任意字符串冒充权限 scope。未来正式启用时只保留必要归属和 Memory 边界，不增加看板、排期、负责人或业务项目管理字段。

建议新增：

```text
llm_projects
project_id, owner_user_id, project_name, status, memory_status,
memory_enabled_at, memory_enabled_by_actor_id,
current_memory_profile_snapshot_id, created_at, updated_at

llm_project_workspaces
project_workspace_id, project_id, workspace_type,
credential_free_locator, locator_hash, is_active, created_at, updated_at

llm_project_memory_profile_snapshots
project_memory_profile_snapshot_id, project_id, schema_version, version,
status, profile_json, estimated_token_count, tokenizer_name, tokenizer_version,
previous_snapshot_id, json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_project_memory_profile_items
project_memory_profile_snapshot_id, memory_id, memory_version_id,
field_path, item_order, content_hash
```

`llm_sessions` 和 `llm_runs` 增加可空 `project_id`；Session 绑定 Project 后，其下 Run 必须继承同一 Project，Task/Attempt/Artifact 继续从 Run 派生范围。现有 `llm_memories` 增加可空 `project_id`，以及 `project_fact | project_decision | project_rule | project_convention | project_environment` 类型。Project Memory 仍保留 `user_id` 作为当前 owner；多用户 Membership 在最终用户认证和授权模型确定前不实施。

`llm_project_workspaces` 只保存不含凭据的规范 locator，例如去除账号令牌的仓库地址或隔离工具提供的不透明 workspace reference；本地绝对路径、SSH 凭据和带 token URL 不进入 Memory。一个 Project 可以没有代码 Workspace，也可以绑定多个 Workspace。

##### 是否启用以及何时启用

- 默认 `memory_status=disabled`。只有受信调用方明确启用并提供 `owner_user_id`、Project 名称、保留策略和可用 token 预算后才能写入或注入；创建 Project 不自动开启记忆。
- 可以在创建 Project 时显式启用，也可以在第二次 Session/Run 前启用。系统可以在检测到同一 Project 出现多个 Session/Run 时提出建议，但不能自动开启、回填历史或产生模型费用。
- `enabled` 允许提交候选、审批和构建 Profile；`suspended` 停止新候选与模型注入，但保留查询和审计；`disabled` 表示从未启用或已经明确关闭。Project `archived` 时自动停止注入，但不物理删除事实。
- 只有显式绑定该 `project_id` 的 Session/Run 才能读取 Project Memory。没有 Project scope 的普通聊天只读取 L1/L4，不尝试按文本猜测项目。
- 当前认证只支持单一 owner 的受信调用方。若需要多个用户共享 Project Memory，必须停止并先实现可验证 Membership、角色、撤权和逐资源越权测试。

##### 记录内容

可以记录：

- 项目身份、目标、明确范围和稳定术语；
- 已接受的架构/产品决定、决定状态、理由摘要及证据；
- 经过验证的代码库入口、模块职责和不易变化的环境事实；
- 项目专属编码、测试、发布和审核约定；
- 技术与合规约束、明确禁止的方案；
- 有来源的长期风险、已知限制和纠正记录；
- 与 Workspace 的不含凭据关联，以及稳定 Artifact/文档引用。

不记录：当前 Agent 搜索进度、当前 Run 待办、未提交临时发现、分支瞬时状态、完整文件/网页、完整日志、密钥、个人全局偏好、模型隐藏推理或未经测试的架构猜测。文档正文属于 Knowledge，Run 进度属于 L2，用户跨项目偏好属于 L4。

模型、Agent Handoff、外部文档和工具结果只能产生 Project Memory Candidate；Project owner 或独立 Reviewer 基于来源和确定性检查批准后才变为 `active`。Project 决定发生变化时创建新版本或替代事实，不覆盖历史。Project Memory 不自动提升到 User Memory，也不因在多个项目重复出现就变成全局规则。

`project_profile.v1` 至少包含：

```json
{
  "project_identity": {"name": "...", "scope": "..."},
  "goals": [],
  "glossary": [],
  "architecture_decisions": [],
  "constraints": [],
  "conventions": [],
  "verified_environment": [],
  "known_risks": [],
  "explicit_prohibitions": []
}
```

Project Profile 目标为 800 token、硬上限 1500 token。`PROJECT.md` 由该 JSON 确定性生成，固定为项目范围、目标、术语、有效决定、约束、约定、环境、风险和来源版本；Markdown 不作为写入口。

接口规划：`POST/GET /api/v1/projects`、`GET/PATCH /api/v1/projects/{project_id}` 只管理最小 Project scope；`PATCH /api/v1/projects/{project_id}/memory-policy` 显式启用/暂停/关闭；候选列表与批准/拒绝位于 `/api/v1/projects/{project_id}/memory-candidates`；当前 Profile 与重建位于 `/api/v1/projects/{project_id}/memory` 和 `/memory/rebuild`。写接口在最终用户认证完成前同时要求公共和内部 API Key。

#### L4 User Memory

User Memory 包含现有细粒度事实账本和一个可快速注入的核心 Profile Snapshot。Profile 是正式事实的投影，不允许反向覆盖来源事实。

现有表计划补充：

```text
llm_memory_sources
+ trust_level

llm_memory_mutations
+ operation, actor_type, actor_id,
+ before_version_number, after_version_number,
+ before_status, after_status, change_json

llm_memories
+ approval_method, approved_at, approved_by_actor_id,
+ sensitivity_classification, is_core_profile_eligible
```

`trust_level` 固定为 `direct_user_statement | user_confirmed | model_inference | internal_system_result | external_untrusted`。信任级别由接收边界和来源类型决定，不能由模型正文自报。网页、文件、邮件、知识库和 MCP 返回均为 `external_untrusted`，只能形成候选，不能自动激活或进入 Profile。

新增表：

```text
llm_user_memory_profile_snapshots
user_memory_profile_snapshot_id, user_id, schema_version, version, status,
profile_json, estimated_token_count, tokenizer_name, tokenizer_version,
previous_snapshot_id, json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_user_memory_profile_items
user_memory_profile_snapshot_id, memory_id, memory_version_id,
field_path, item_order, content_hash
```

`users` 增加可空 `current_memory_profile_snapshot_id`。`user_profile.v1` 使用以下可扩展结构：

```json
{
  "confirmed_preferences": [],
  "confirmed_facts": [],
  "ongoing_goals": [],
  "explicit_prohibitions": [],
  "environment_constraints": []
}
```

- 默认目标为 500 token、硬上限为 800 token，并记录实际 tokenizer 名称和版本。超限时按显式禁止项、稳定环境限制、已确认偏好、身份事实、长期目标的规则选择；不截断单个事实。
- 只有 `active`、未过期、`direct_user_statement | user_confirmed`、非敏感且 `is_core_profile_eligible=true` 的当前版本可以进入 Profile。其他有效事实只能按需检索。
- 修正正文创建新不可变版本；冲突事实使用新的 `memory_id` 替代旧事实；两者都会使旧 Profile 过时并触发重建。删除必须在同一事务使包含该版本的当前 Profile 失效或切走当前指针，确保之后创建的 Memory Packet 不再读取正文。
- 凭据、访问令牌、密码和私钥在候选创建前即拒绝保存；拒绝证据只保留规则编号和输入哈希。身份证件、财务和健康等敏感信息是否允许长期保存必须由部署保留策略明确授权，缺失策略时拒绝进入 Profile。
- 已经开始的供应商请求无法从外部模型撤回内容。Memory Packet 会固定使用过的版本和哈希，删除后保留无正文审计标识，但后续 Packet 不得再选中已删除内容。

`USER.md` 由 `user_profile.v1` 确定性渲染，固定为“确认偏好、确认事实、长期目标、明确禁止、环境限制、来源版本”六部分。它不包含候选、外部不可信文本、隐藏推理或完整来源正文；Markdown 丢失时从 JSON 重建，不反向解析 Markdown 更新事实。

#### Lesson Candidate 与污染防护

`execution_lesson` 不再承担“自动学习成通用规则”的新职责。新增 `llm_lesson_candidates`：

```text
lesson_candidate_id, run_id, task_id, agent_handoff_id,
evaluation_id, status, scope_json, lesson_json,
valid_conditions_json, invalid_conditions_json,
promoted_memory_id, created_at, reviewed_at
```

状态为 `candidate | verified | promoted | rejected | superseded`。只有包含确定性测试或 Evaluation 证据、明确适用范围、有效条件和失效条件的候选，经过独立 Reviewer 后才能提升为 L3 Project Memory、L4 User Memory 或后续 Skill 候选；阶段2不自动写 Skill，不让一次成功或一次失败变成项目或全局规则。

统一污染防护：

- 模型输出、外部资料中的“请记住”“忽略规则”等文字只作为数据，不得提升信任级别、扩大 scope 或触发批准。
- 用户直接陈述与用户粘贴/引用的外部文本必须由接口结构区分；不能仅根据自然语言猜测引用内容是用户事实。
- 写入前执行凭据与高敏感标识扫描，记录规则版本、结果和输入哈希；不得把被拒绝原文复制到错误信息、日志或审计 JSON。
- 来源校验、信任判定、候选审批和 Profile 选择都在 Service 中执行，Router 和 Provider Adapter 不复制策略。
- 任何模型生成内容都从 `model_inference` 或 `external_untrusted` 开始，不因模型置信度字段较高而自动激活。

#### 双存储事务与失败恢复

跨 MySQL/MinIO 的快照写入使用“旧版本持续可用、上传完成后才切换指针”的五步协议：

1. MySQL 事务创建 `generating` 业务版本和幂等事实，记录期望的当前版本后提交。
2. 在数据库事务外生成并校验规范 JSON、确定性 Markdown、token 估算和内容哈希。
3. 使用包含对象 ID 与哈希的唯一键上传 JSON/Markdown；重复上传必须得到相同内容，否则标记冲突。
4. 通过对象元数据重新核对大小和 SHA-256，并登记 `llm_memory_snapshot_objects`。
5. MySQL 事务锁定 User/Project/Session/Run 当前指针，重新校验期望版本，将新版本改为 `active`、旧版本改为 `superseded` 并切换指针。

失败处理：

- 第 2～4 步失败：业务版本标记 `failed`，旧指针不变；模型生成超时和对象存储不可用区分为可重试依赖错误，Schema/敏感内容错误不可重试。
- 上传成功而数据库登记或指针提交失败：对象成为待清理对象；能连接数据库时记录 `cleanup_error`，数据库不可用时由确定性前缀巡检找出无引用对象。清理失败不影响旧版本读取。
- 数据库指针只有在对象校验完成后才提交，因此不允许出现“当前版本已切换但对象尚未上传”的状态。
- 同一幂等键同一请求返回原生成记录；同键不同请求返回 409。并发生成时只有基于当前版本的事务可以切换指针，失败者重新读取并决定重算，不能覆盖胜者。
- 服务重启只读取 MySQL 当前指针，扫描并恢复超时的 `generating` 记录；绝不按 MinIO 文件名猜测最新版本。
- 删除与生成并发时锁定同一 scope 指针。删除先提交则生成不能激活；生成先提交则删除使新版本失效并安排对象清理。已创建 Packet 的无正文审计记录保留，但不可继续解析已删除正文。

#### 未来启用时的读取方案（当前不执行）

以下内容只保留为未来评审输入，不是阶段2运行流程。当前 `/responses` 不读取任何 L0～L4 Memory，也不创建 Memory Packet；Context Preview 代码虽然已有候选读取分支，但未通过验收，阶段2稳定合同要求禁用或移除。若未来决定正式启用，必须先重新验证权限、删除、预算、污染防护和版本固定，再考虑以下候选顺序：

1. 校验 User、可选 Project、Session、Run、Task、Agent Run 的完整归属链，确定 provider/model、tokenizer 版本和总预算；没有显式 `project_id` 时不读取 L3。
2. 读取 L0～L4 对应 MySQL 当前指针；L1～L4 按指针读取不可变 JSON，并校验对象 hash、schema version、状态和删除/过期条件；L0 直接读取有版本的 MySQL JSON。
3. 选择 L4 User 核心 Profile；需要细节时再检索不带 Project scope 的有效用户事实，不能把整个用户事实账本默认注入。
4. 若已绑定 Project 且 Memory 为 `enabled`，选择 L3 Project Profile；需要细节时只检索该 Project 的有效事实。Project 禁用/暂停时记录排除原因，不退回文本猜测。
5. 选择 L1 Session Summary、最近完整 Message 和有界相关旧消息；Summary 已经覆盖且没有新价值的旧消息不重复注入。
6. 对 Agent 调用选择 L2 当前 Run Snapshot、直接依赖祖先的有效 Handoff、显式 Artifact 和 Task 要求；普通会话模型调用跳过此层。
7. 对 Agent 调用读取该 Agent Run 的 L0 Working State，用于恢复当前目标、待处理工具和下一步；不得把其他 Agent Working State 注入。
8. 按安全与系统指令、当前明确请求、L0 当前目标、L2 Run/Handoff、L1 Session、L3 Project、L4 User 默认信息的类别预算组装；单个结构化对象不能从中间截断，排除项记录原因。
9. 在请求 Provider 前创建不可变 Memory Packet 和明细，随后把 `memory_packet_id` 绑定逻辑 Model Attempt。此步骤当前未实现，也不是阶段2工作项。

未来若启用，Memory 内容应以带层级和来源类型的数据区块进入 Prompt，不能与系统指令拼成同一自由文本；`external_untrusted` 内容必须标记为待分析资料。Context Builder 只能选择已通过 Policy 的内容，不能批准候选、提高信任级别或修改事实。这些规则当前只用于设计审查，不表示已有模型调用集成。

#### L0～L4 Memory 实验性接口合同

所有读取继续要求平台 API Key；新五层 Memory 的生成、审批、纠正、删除和重建在最终用户认证完成前同时要求内部 API Key。现有 `/memories` 写接口当前只要求平台 API Key，User/Project Memory 实施时必须以兼容迁移方式增加内部写边界并更新 OpenAPI/调用方测试，不能用文档提前声称已经生效。审批接口未来可以转为最终用户或 Project 成员动作，但当前不能宣称请求中的 `user_id` 等于已认证用户。

| 接口 | 用途与主要输入 | 正常输出 | 主要错误 |
|---|---|---|---|
| `POST /api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints` | 带期望版本保存 L0 检查点 | 新 Working State 版本和当前指针 | 401、404、409、422 |
| `GET /api/v1/internal/agent-runs/{agent_run_id}/working-memory` | 读取该 Agent 当前可恢复状态 | 当前版本、预算、待处理引用和过期时间 | 401、404 |
| `GET /api/v1/sessions/{session_id}/memory` | 读取当前 State、Summary、覆盖消息范围和版本 | 当前可用版本；生成失败时可返回旧版本并明确 `is_stale` | 401、404、409 |
| `POST /api/v1/sessions/{session_id}/summaries` | 使用期望消息序列、模型和幂等键生成摘要 | `generating/active/failed` 记录及版本 | 401、409、422、503 |
| `GET /api/v1/sessions/{session_id}/summaries` | 分页读取摘要版本 | 不可变版本和来源范围 | 401、404 |
| `PATCH /api/v1/projects/{project_id}/memory-policy` | 显式启用、暂停或关闭 L3 | Project Memory 状态和生效时间 | 401、404、409、422 |
| `GET /api/v1/projects/{project_id}/memory` | 读取当前 Project Profile | Profile JSON/Markdown、来源和 token 证据 | 401、404 |
| `GET /api/v1/projects/{project_id}/memory-candidates` | 分页读取项目候选 | 候选、信任来源和审批状态 | 401、404 |
| `POST /api/v1/projects/{project_id}/memory-candidates/{memory_id}/approve` | 批准 Project 候选 | active Project Memory；不隐式重建 Profile | 401、404、409、422 |
| `POST /api/v1/projects/{project_id}/memory/rebuild` | 重建 Project Profile | 生成记录及新版本 | 401、409、503 |
| `GET /api/v1/users/{user_id}/memory-profile` | 读取当前核心 Profile | Profile JSON/Markdown 版本和 token 证据 | 401、404 |
| `GET /api/v1/users/{user_id}/memory-candidates` | 分页读取候选 | 候选、来源信任和审批状态 | 401、404 |
| `POST /api/v1/users/{user_id}/memory-candidates/{memory_id}/approve` | 带期望版本批准候选 | active Memory；不隐式重建 Profile | 401、404、409、422 |
| `POST /api/v1/users/{user_id}/memory-candidates/{memory_id}/reject` | 带期望版本拒绝候选 | rejected Memory | 401、404、409 |
| `POST /api/v1/users/{user_id}/memory-profile/rebuild` | 以当前正式事实重建 Profile | 生成记录及新版本 | 401、409、503 |
| `POST /api/v1/internal/runs/{run_id}/agent-handoffs` | 提交完整或部分 Handoff | 不可变 Handoff 及快照对象版本 | 401、404、409、422、503 |
| `GET /api/v1/internal/runs/{run_id}/agent-handoffs` | 按 Task/Agent Run 分页读取 | Handoff 与替代关系 | 401、404 |
| `GET /api/v1/internal/runs/{run_id}/memory` | 读取当前 Run Memory | 当前状态、来源 Handoff 和版本 | 401、404 |
| `POST /api/v1/internal/runs/{run_id}/memory/rebuild` | 以期望版本与 Handoff 集合重建 | 生成记录及新当前版本 | 401、409、422、503 |
| `GET /api/v1/internal/agent-runs/{agent_run_id}/memory-packet` | 读取实际交给 Agent 的固定 Memory 输入 | Packet、每项版本、hash 与 token 证据 | 401、404 |

这些接口当前只供受信开发和设计验证使用，不纳入前端或 `/responses`。L0 和原始 L2 接口位于 `/api/v1/internal` 并要求双密钥；未来面向用户的 Run 进度接口只能返回经脱敏的共享状态，不暴露 Working State、完整 Handoff 或 Memory Packet。正式启用前还必须重新审核签名 cursor、写入 Schema、资源关系、错误分类和对象存储失败行为。

#### 向量存储决定

五层 Memory 默认不引入 Vector Store 或 Mem0：Agent Working State、User/Project 核心 Profile 直接读取，Collaboration Memory 使用 Run/Task 依赖与 Handoff 精确引用，Session 使用 Summary、最近消息和有界词法/全文检索。当前也没有供应商中立 Embedding 契约、异步索引 Worker、删除同步或固定检索评估集。

只有本节前述向量门禁全部满足后才允许新增投影：MySQL/MinIO 始终是事实源；Qdrant 或其他向量库只保存可重建向量和事实版本标识；查询后必须回 MySQL 复核 owner、scope、状态、过期和删除；使用 Outbox 驱动索引、tombstone 和全量重建；固定评估证明词法基线在真实样本上不足后再启用。Mem0 若未来评估，也只能作为可替换的编排组件，不能成为事实源或绕过现有审计与权限边界。

#### Memory 后续规划工作项

M1～M5 编号仅作为既有设计引用保留。它们不再属于阶段2实施顺序；在用户明确决定 Memory 正式启用阶段前，不继续补充代码或接入模型调用。

| 工作项 | 需要修改或新增的实际对象 | 测试与验证 | 完成条件 | 状态 |
|---|---|---|---|---|
| Agent Working Memory | `features/memory` 中已有最小 Agent Run、Turn、`agent_working_state.v1`、预算和内部检查点实验代码 | 已有部分快速测试；等待恢复、工具事实复核和定时擦除未验证 | 正式启用前重新确认与阶段5 Agent Runtime 的边界 | 暂停 |
| M1 Session Memory | 已有 State/Summary、版本指针、MinIO JSON/Markdown 快照实验代码 | 已有部分快速测试；真实 MySQL/MinIO 部分失败与孤儿清理未验证 | 正式启用前重新确认摘要形成、保留和失败恢复策略 | 暂停 |
| Project Memory | 已有最小 Project scope、策略、Workspace、候选和显式 Profile rebuild 实验代码 | 跨 Project、真实并发和双存储失败矩阵未验证 | 正式启用前重新确认 Project 权限和 Profile 生命周期 | 暂停 |
| M2 User Memory | 已有候选审批/拒绝、信任过滤、凭据拒绝和 Profile rebuild 实验代码 | 纠正后并发 rebuild、删除语义和真实存储失败未验证 | 正式启用前先确定最终用户认证、敏感信息和删除策略 | 暂停 |
| M3 Collaboration Memory | 已有 Handoff、Run Snapshot 实验代码；Packet 没有创建流程 | Packet 固定版本、并发指针和真实存储失败未验证 | 与 Agent Runtime 一起重新规划，不接入当前 Model Gateway | 暂停 |
| M4 后台形成与压缩 | 尚无正式实施安排 | 重复消息、乱序、崩溃重投、永久失败和状态恢复 | 只有 Memory 正式启用且阶段3 Worker 可用后才能排期 | 暂停 |
| M5 语义检索评估 | 固定中英文评估集、Embedding Provider 契约、Outbox 投影、候选向量库适配器；仅门禁通过后实施 | recall@k、精确率、延迟、费用、删除一致性、降级、重建和数据出境 | 评估证明相对词法基线有必要收益，且权限/删除/重建测试全部通过；否则保持不实施 | 暂停 |

当前实验性迁移为 `20260803_0008_phase2_memory_layers.py`。它通过了模型建表型快速测试，但尚未在真实 MySQL 从 `20260801_0007` 执行升级、降级和 `alembic check`，因此不能标记为生产可用。由于 Memory 当前暂停，这些验证保留为未来正式启用前的门禁，不计入阶段2完成条件。

当前不实施：Vector Store、Mem0、知识图谱、自动 Skill 提升、没有 Worker 的后台抽取、最终用户公开审批接口、多用户 Project Membership、项目看板/排期、隐藏思维链保存、覆盖式快照和从 `latest` 文件恢复。待确认：最终用户认证主体；摘要模型与费用上限；敏感信息保留/法定删除策略；MySQL 中文全文 parser；MinIO 生命周期与快照保留期；预算是否经固定样本调整；Agent Run 状态和 Task Handoff 完成门禁；Agent Working State 擦除期；Markdown 是否提供人工查看端点。

#### Memory 当前目录边界

Memory 已从全局 `models/routers/schemas/services` 平铺目录迁移到单一业务特性目录，同时保留 MVC 内部分层：

```text
apps/api/src/nexuspilot_api/features/memory/
├── models/       # L0～L4、事实账本、快照和 Project ORM
├── schemas/      # Memory、Session、Project、User 和协作接口契约
├── services/     # 策略、事务、检索、Profile、快照和 L0～L4 用例
└── routers/      # 公共及 internal Memory HTTP Controller
```

`nexuspilot_api.models` 继续作为 ORM 元数据统一登记入口，避免 Alembic 漏载模型；通用 `routers/common.py`、数据库、对象存储和错误类型仍留在全局基础设施层。Context、Knowledge、Prompt 和 Evaluation 不属于 Memory 内部实现，不因目录调整被搬入 Memory。

#### Memory 复杂度审查结论

当前决定是停止扩展 Memory，只维护规划和已经存在的实验性准备代码。可审计事实账本、L0 手动检查点、L1 State/Summary、L2 Handoff/Run Snapshot、L3/L4 候选和 Profile 都不进入实际模型响应链路；Memory Packet 不在阶段2创建，也不绑定 Context Builder 或 Model Attempt。

暂不继续实现：自动记忆形成、定时压缩/擦除、Agent Runtime 自动检查点、Embedding、Vector Store、Mem0、知识图谱、后台 Profile 刷新和自动 Lesson 提升。它们分别依赖阶段3 Worker、阶段5 Agent Runtime 或固定检索评估门禁；现在加入只会扩大状态机和双存储恢复面，不能提高当前同步接口的可靠性。

### Knowledge：移出阶段2

- Knowledge 表达外部资料及其引用、版本、解析和检索，属于独立知识库，不属于 Memory，也不再属于阶段2。
- 仓库中现有 Knowledge ORM、Schema、Service 和 Router 是未完成的实验性准备代码，不作为稳定 API，不进入阶段2测试数量、完成状态或前端第一版功能。
- 后续必须先单独讨论资料来源、上传与 Artifact 关系、解析器、分块、检索质量、向量方案、引用展示、用户/项目权限、版本切换、删除和失败恢复，再建立知识库实施文档。
- 在独立规划批准前，不继续扩展 Knowledge，也不接入 Context Builder、`/responses`、Agent 或前端。

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

## Model Gateway：统一接口契约（含 DeepSeek 最新 Chat 适配，已验证）

### 模型请求

统一请求对象（代码标识建议：`ModelRequest`）至少包含：

```text
provider
model
messages
system_instruction
tools
output_schema
reasoning
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
- `reasoning` 是可选的供应商中立思考参数；Provider 必须依据模型能力显式映射或返回 `unsupported_capability`，不得静默删除、改写强度或切换到另一种调用语义。

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

## Model Gateway 实施范围（原有基线已完成）

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
│       ├── deepseek.py          # DeepSeek Chat Completions 专用约束
│       ├── openai_compatible.py # 可配置兼容服务及共享 Chat codec
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

## Model Gateway 实施步骤（原有基线已完成）

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
- 风险：Memory 可能把模型推测固化为长期事实，因此当前禁止将 Memory 自动接入 `/responses`；未来启用时必须先验证来源、置信度、纠正和删除机制。
- 风险：不同用户、Session 或项目的数据可能错误关联；当前受信调用方边界内所有读写必须在 Service 和查询层验证 owner scope。直接面向最终用户前必须另行完成用户认证授权。
- 风险：实验性 L1～L4 Memory 快照同时写入 MySQL/MinIO 时可能部分成功；当前不把这些接口声明为生产能力。未来正式启用时必须验证版本记录、指针切换和孤儿清理。
- 风险：Prompt、模型目录、解析器、tokenizer 或评估规则升级后可能无法复现历史结果；执行证据必须记录实际使用的不可变版本。
- 风险：并发切换 Prompt 版本可能产生多个有效版本；必须使用唯一约束、状态条件更新和事务冲突响应。Memory/Knowledge 并发规则留到各自正式启用规划中复核。
- 需要停止并确认的情况：实施要求使用未提供且无法通过安全配置表达的私有供应商协议。
- 需要停止并确认的情况：需要执行会产生明显费用的批量真实模型测试。
- 需要停止并确认的情况：现有 `llm_attempts` 无法表达每次物理重试且必须进行破坏性数据迁移。
- 需要停止并确认的情况：供应商能力与验收要求冲突，例如指定模型明确不支持工具调用但要求静默兼容。
- 需要停止并确认的情况：要求把受信调用方接口直接开放给最终用户，但没有确定可验证的认证主体和权限声明。
- 需要停止并确认的情况：要求对阶段1不可变 Message 做物理级联删除，或要求使用尚未确定的法定保留策略。

## Model Gateway 原有基线完成标准（已达到）

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

## Model Gateway 原有基线实施结果

- `provider` 使用稳定枚举；`model` 保留供应商原始字符串，并由对应 Provider 的可选 allowlist 校验组合。
- OpenAI 使用 Responses API codec；DeepSeek 使用专用 Chat Completions 策略适配器并复用通用 Chat codec；Anthropic 使用 Messages codec；Gemini 使用 GenerateContent codec。
- 公共 HTTP 层封装为 `POST /api/v1/responses`，业务方不直接接触任何供应商 payload。
- `HttpTransport` 统一负责 JSON、SSE、连接超时、HTTP 状态分类和指数退避，Provider 只负责编解码。
- 每个逻辑调用写入 `llm_attempts`；每个物理网络请求写入 `llm_attempt_retries`。
- API Key 仅从服务端 `SecretStr` 配置进入认证 Header，不进入公共请求、数据库原始请求或错误响应。
- 价格不内置可能过期的数字，只读取带供应商/模型键的显式 JSON 配置；未知价格保持 `null`。
- 未配置凭据的 Provider 不注册，服务仍可启动；调用时返回 `provider_not_configured`。
- 单元、Provider 契约、SSE、ASGI、迁移一致性和静态检查均为阶段2验收项。
- 真实供应商连通性测试：未运行。本阶段规划修订没有读取供应商凭据，也没有产生模型费用。

## DeepSeek 官方接口复核与实施结果

以 2026 年 8 月 1 日的 [DeepSeek Chat Completions 官方文档](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/) 为兼容依据。以下约束已经通过 Provider 契约和 FastAPI 集成测试；真实 DeepSeek 连通性仍未运行：

- NexusPilot 只调用 DeepSeek `POST /chat/completions`。DeepSeek 的 Responses 端点只作为协议设计参考，本阶段不实现；平台自有 `POST /api/v1/responses` 门面仍由 DeepSeek Chat Completions 适配器完成转换。
- 官方当前模型值为 `deepseek-v4-flash` 和 `deepseek-v4-pro`。平台仍保留可配置 allowlist，不把模型名散落在业务分支中；后续由 Model Catalog 记录模型能力快照。
- 统一请求增加可选的供应商中立 `reasoning: {enabled, effort}`，其中 `effort` 只允许 `low | high | max`，且禁用 reasoning 时不允许同时传 effort。DeepSeek 适配器将其转换为顶层 `thinking: {"type": "enabled" | "disabled"}` 和顶层 `reasoning_effort`；未显式提供 `reasoning` 时不额外发送字段，由官方默认为 `thinking=enabled`、`reasoning_effort=high`。
- `deepseek-v4-flash` 接受 `low | high | max`。`deepseek-v4-pro` 精确支持 `high | max`；虽然官方会将 `low` 映射为 `high`，平台为避免静默改变请求语义，对 `deepseek-v4-pro + low` 返回明确输入或能力错误。
- reasoning 启用时 DeepSeek 不支持 `temperature`；平台返回 `invalid_request`，不删除参数后重试。reasoning 显式禁用时才允许传递已验证的 `temperature`。
- DeepSeek 流式响应必须收到 `data: [DONE]` 才视为完整结束。缺失终止标记或响应提前断开都按 `response_parse_error` 处理，生成 `response.failed` 并将 attempt 结束为失败，不把已收到的部分文本伪装成成功。
- 官方已标记 `frequency_penalty` 和 `presence_penalty` 弃用且无效，统一 HTTP Schema 不引入这两个参数，未知字段继续由 `extra="forbid"` 拒绝。官方 `user_id` 也不在本次实施范围。
- 工具或结构化输出中的 `strict` 只能在 Model Catalog 对当前模型明确记录支持时传递。未声明或未验证时必须返回 `unsupported_capability`，不得删除 `strict`、放宽 Schema 或改用普通文本后继续请求。本计划不声称 DeepSeek beta strict 已经完成真实供应商验证。
- 已验证新参数 Schema 边界、两个模型的参数组合、Pro/low 拒绝、reasoning/temperature 冲突、`[DONE]` 完整/缺失、公开流不暴露 `reasoning_content`、失败 Attempt 落库、弃用字段拒绝、strict 能力失败和原有 Chat Completions 回归。真实请求未运行，不能据此声称供应商凭据、网络或账户权限已经可用。

## 2026 年 8 月 3 日本轮审查与验证结果

- Memory 已迁入 `features/memory`，内部继续按 Model、Schema、Service、Router 分层；架构测试同时检查全局 Router 和 Memory Router 不直接导入 ORM/SQLAlchemy。
- 快速测试共收集 182 项，其中 179 项通过，3 项真实基础设施测试按默认配置跳过；Ruff 全量静态检查通过。
- 本轮修复了 Session 当前指针先于版本插入导致的外键冲突、Profile 快照/明细/指针写入顺序、L2 合并遗漏 confirmed facts、L0 Schema 接受未知隐藏字段，以及 Agent Run 终止参数和非终止状态校验。
- Memory 审批与 Profile rebuild 已拆成两个显式操作。审批只在 MySQL 中转换事实状态；调用方按当前 Profile 版本单独重建快照，MinIO 失败不会被吞掉或伪装成审批已完整刷新。
- `20260803_0008` 已通过 `alembic upgrade head --sql` 离线 SQL 生成，但尚未执行本轮真实 MySQL 升级/降级和 `alembic check`；3 项真实基础设施测试也因未启用环境开关而跳过。先前对 `20260801_0007` 的验证不能替代新迁移验收。
- Context、Prompt/Model Catalog 和 Evaluation 目前只有实现代码和路由结构检查，没有行为测试，因此保持“进行中”，不能由全量测试通过数量推断为已完成。Knowledge 已移出阶段2；Memory 只保留规划和实验性准备代码。
- 上传文件名同时清理 POSIX 和 Windows 风格路径片段。
- `get_session()` 在请求异常时显式 rollback，并由异步上下文保证 session 关闭。
- 真实 DeepSeek 请求未运行，没有读取供应商凭据，也没有产生模型费用；当前结论只证明本地契约与集成行为。
- RabbitMQ、Worker 和任务自动执行仍未实现，不计入阶段2完成范围。

## 阶段2实施顺序与当前状态

以下内容属于同一个阶段2，不拆分为新的阶段编号。Memory 与 Knowledge 不再是本阶段实施或集成门禁；Context、Prompt/Model Catalog 和 Evaluation 仍缺必要行为测试，因此阶段2保持“进行中”：

| 顺序 | 工作项 | 具体任务 | 失败与恢复设计 | 验证门禁 | 状态 |
|---|---|---|---|---|---|
| 1 | Model Gateway 回归 | 保持四家 Provider、统一 Responses、SSE、重试、费用和原始证据行为 | 供应商差异显式失败，不静默降级 | 既有契约和 API 测试持续通过 | 已完成 |
| 2 | Prompt Registry 与 Model Catalog | 验证不可变 Prompt 版本、内部管理/渲染和模型能力目录 | 并发启用只有一个有效版本；未知能力明确拒绝；禁用不影响历史证据 | 补齐变量、并发、能力和版本行为测试 | 进行中 |
| 3 | Conversation Context | 收敛为独立预览能力，稳定选择显式指令、安全指令和 Session Message | 来源缺失明确失败；超预算返回排除证据；历史证据可重读 | 补齐 owner、预算、版本重读和错误行为测试；不接入 Memory/Knowledge 或 `/responses` | 进行中 |
| 4 | Evaluation 与 Guardrail | 验证规则集、确定性检查和评估接口 | 未知规则安全失败；同键异请求冲突；执行状态与 verdict 分离；敏感输入不泄露 | 补齐状态、幂等、脱敏和失败测试 | 进行中 |
| 5 | 阶段2验收 | 验证上述单元可独立调用，并保持 `/responses` 直接使用显式请求输入 | 任一独立单元失败不影响或伪造 Model Gateway 成功；没有隐式 Memory/Knowledge 读取 | Ruff、完整快速测试、迁移、OpenAPI、鉴权和敏感信息测试全部通过 | 未开始 |

## 逐能力接口和错误契约

所有新增接口沿用阶段1的错误响应结构，至少稳定区分：输入错误、未认证、资源不存在、资源关系不匹配、状态冲突、依赖暂时不可用和内部错误。

| 能力 | 必要输入 | 正常输出 | 不允许的隐式行为 |
|---|---|---|---|
| Context Build | User、Session、模型、总预算和显式指令 | 构建 ID、统一消息、token 估算、选中与排除来源 | 不自动调用模型；不读取 Memory/Knowledge；不无限读取会话；不静默丢弃完整对象 |
| Prompt Render | Prompt 名称/版本和变量 | 渲染文本及版本证据 | 不忽略未知变量；不因渲染而发起模型调用 |
| Model Capability | provider、model 和目录版本 | 明确支持、不支持或未知 | 不把未知能力当作支持 |
| Evaluation | 输入事实、规则版本和幂等键 | 执行状态、verdict、规则证据 | 不把执行失败写成通过或不通过结论 |

## 测试设计

### Memory 未来启用门禁（不属于阶段2完成条件）

以下 Memory 测试矩阵作为未来正式启用时的最低门禁保留，不因现有实验性路由或快速测试而缩减，也不计入当前阶段2完成状态：

| Memory 范围 | 必须覆盖的场景 | 验收结果 |
|---|---|---|
| Schema 与上限 | 1/4000/4001 字符、1/20/21 个来源、置信度/重要度边界、无时区过期时间、未知字段 | 边界内接受，超限和含糊时间在数据库之前被拒绝 |
| 类型与范围 | 五种 MemoryType，User/Session/Run/Task 正常链路，跨 User、跨 Session、跨 Run 和跨 Task | 只接受归属一致的显式范围；每种类型的必要范围和过期规则生效 |
| 来源与创建者 | 五种 source type，来源不存在、跨 owner、重复来源，模型创建 active 或缺少 attempt | 来源标识/哈希可追溯；不可验证来源和模型直接激活均被拒绝 |
| 状态转换 | candidate 激活/拒绝、active 被替代、所有非删除状态删除、终止状态重新激活 | 合法转换成功；非法转换返回 409 并完整回滚 |
| 幂等 | 创建、纠正和检索的同键同请求/同键异请求，重复删除 | 写请求不重复应用并返回当前 Memory 表示；检索返回原证据；改变请求返回 409；不产生重复事实 |
| 版本与并发 | 纠正创建新版本、旧版本不变、stale `expected_version_number`、并发纠正/激活/替代 | 版本号单调递增，只有一个并发修改成功，同一语义槽位只有一个 active Memory |
| 过期与删除 | `expires_at` 之前/之后检索，删除后的列表/详情/检索，所有版本正文和词哈希清理 | 过期和删除内容不参与检索；删除幂等；仅保留无正文审计证据 |
| 检索排序 | 英文归一、中文二元词、owner/scope/status/type 过滤、同分 tie-break、limit 和 token budget | `lexical_hash_v1` 候选和 `memory_ranker_v1` 分数可复现；不截断 Memory；超预算原因可查询 |
| 检索证据与隐私 | 证据详情重读、幂等重放、token 预算排除项、删除后历史证据 | 分数、版本、选中状态和排除原因不变；响应不泄露跨 User、不可用或已擦除正文 |
| 数据库与迁移 | SQLite 快速契约、MySQL 唯一约束/行锁并发、空库与阶段1升级、`alembic check` | 测试与生产数据库差异被显式验证，迁移无漂移 |

L0 Agent Working Memory 在未来与阶段5 Agent Runtime 集成前必须建立以下测试：

| 场景 | 通过条件 |
|---|---|
| Agent 私有范围 | 只能读写相同 `agent_run_id`；其他 Agent、跨 Task/Run 请求被拒绝 |
| Turn 与调用事实 | Working State 只引用 Turn/Attempt/Tool Call；不复制原始请求、响应、费用和完整工具结果 |
| 检查点并发与幂等 | 同键同请求复用；stale 期望版本返回 409；当前指针只有一个 |
| 等待、暂停和崩溃恢复 | 从最近 active 版本恢复，并重新核对待处理工具真实状态 |
| 完成和擦除 | Agent Run 终止后不再注入；清理期后正文擦除，版本/hash 审计仍存在 |
| 隐藏推理边界 | Schema 不接受思维链字段；临时发现必须带来源且只在 Handoff 后对其他 Agent 可见 |

M1 Session Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 正常摘要与长度边界 | JSON Schema、消息起止序号和硬 token 上限均可验证，不截断结构 |
| 重复请求与并发生成 | 同键同请求复用，同键异请求 409，同一 Session 只有一个当前版本 |
| 生成或 MinIO 失败 | 新版本为 `failed`，旧 State/Summary 仍可读取且明确 `is_stale` |
| 会话归属与历史搜索 | 跨 User/Session 被拒绝；相同版本和查询得到稳定排序 |
| 外部工具文本污染 | 外部文本保持不可信数据，不能变成系统指令、决策或正式用户事实 |
| 重启恢复 | 只由 MySQL 指针确定当前版本，不读取对象键猜测最新快照 |

L3 Project Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 默认关闭与显式启用 | 创建 Project 不自动写入/注入；启用需要 owner、策略、预算和双密钥 |
| Session/Run 绑定 | 只有显式同一 `project_id` 可读取；Run 与 Session Project 不一致返回 409 |
| 候选与跨层污染 | Agent/外部内容只能形成 Project 候选；项目事实不进入 User Profile |
| Profile 容量 | 使用记录 tokenizer，目标 800、硬上限 1500 token；单项不截断 |
| Workspace 安全 | locator 不含 token、用户信息或本地敏感路径；没有 Workspace 的非代码项目仍可使用 |
| 暂停、归档、删除 | 停止新写入与注入但保留审计；删除后新 Packet 不再读取正文 |
| 跨 Project/用户隔离 | 相同词项也不能跨 scope 召回；共享项目前不得声称 Membership 已实现 |
| 并发和双存储失败 | Profile 只有一个当前版本；上传或提交失败时旧版本继续服务 |

M2 User Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 明确写入、模型推断和外部来源 | 用户明确来源按策略进入候选/正式事实；模型和外部来源只能形成低信任候选 |
| 候选批准、拒绝和过期 | 状态转换、期望版本和审计字段完整，非法重复操作返回 409 |
| 同一语义冲突、纠正和删除 | 不产生两个 active 语义槽；旧版本不变；删除后新 Profile/Packet 不再注入正文 |
| Profile 容量 | 目标与硬 token 上限使用记录的 tokenizer；单项不被截断；选择原因可解释 |
| 敏感信息和污染 | 凭据拒绝且错误不复制原文；外部指令不能自升信任或扩大 scope |
| 并发与双存储失败 | 只有一个 Profile 指针切换成功；上传/提交任一失败时旧 Profile 继续服务 |
| 归属和权限 | 受信调用方关系校验通过；缺少公共/内部密钥拒绝；不虚构最终用户身份隔离 |

M3 Collaboration Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 完整与部分 Handoff | 完成记录缺少必要字段时拒绝；异常路径可保存可识别的 `partial` 版本 |
| 并行只读 Agent | 两个 Handoff 可并行创建，Run Snapshot 通过期望版本串行归并且不丢来源 |
| 冲突事实 | 合并结果显式列出冲突并等待 Controller/Reviewer 裁决，不静默最后写入胜出 |
| 下游选择 | 只选择依赖祖先和显式引用，Reviewer 不读取实现模型隐藏推理 |
| Memory Packet | 记录实际读取的每个版本、hash、token 和顺序；供应商重试复用相同 Packet |
| MinIO 对象 | 键不含敏感文本，JSON/Markdown hash 与数据库登记一致，孤儿对象可识别和清理 |

五层共同的真实基础设施测试未来必须在 MySQL 8.4 执行范围外键、行锁、并发版本、UTC 时间和当前指针恢复；L1～L4 还必须在 MinIO 验证对象上传/读取/删除、数据库回滚后的孤儿对象和对象键脱敏。快速 SQLite 测试只验证契约和普通 Service 行为，不替代这些结论。

### 阶段2必须先行的测试

- Prompt 和 Evaluation 的状态转换、重复执行和并发冲突。
- Context 的 owner scope、关系不匹配、消息数量、token 预算、稳定排序和历史重读。
- 缺少公共认证、缺少内部认证和资源关系不匹配；最终用户级越权测试必须等认证主体确定后才能声称覆盖。
- Prompt 变量 Schema、模板注入文本、并发启用和不可变版本。
- Model Catalog 未知能力、禁用版本、重复版本和 Provider 配置不一致。
- Evaluation 的幂等请求哈希、未知规则拒绝、规则版本、执行失败和敏感证据脱敏。
- `/responses` 回归测试必须证明请求只使用调用方显式输入，不会隐式读取实验性 Memory 或 Knowledge。

其他验证：

- Router/Service/ORM 依赖边界架构测试。
- 阶段2新增或修改表的 Alembic 空库升级、已有数据库向前升级和 `alembic check`；实验性 Memory/Knowledge 表的生产验收留到其正式启用阶段。
- OpenAPI 路由、Schema 示例、错误结构和内部接口双密钥检查。
- Model Gateway 既有 Provider 契约与 SSE 回归测试。

## 阶段2总体完成标准

- Model Gateway 保持四家 Provider、普通响应、流式响应、工具调用、结构化输出、重试、费用和调用证据测试通过。
- Conversation Context 可以在固定 token 预算下对显式指令、安全指令和 Session Message 生成可复现预览，并记录选中与排除证据；它不自动调用模型。
- Prompt Template 具有不可变版本、严格变量合同、并发安全的当前版本切换和可查询变更证据。
- Model Catalog 对 provider/model 能力给出明确支持、不支持或未知结论，并保留实际能力快照版本。
- Evaluation 与 Guardrail 可以脱离 Agent 单独执行；未知规则安全失败，同键异请求返回冲突，执行状态与 verdict 分离，敏感输入不进入公开证据。
- `/responses` 继续只使用调用方显式请求内容，不读取 Memory、不创建 Memory Packet、不读取 Knowledge，也不隐式使用 Context Preview。
- Memory 只保留规划和实验性准备代码，不以现有路由、表或快速测试声称已成为模型运行时能力；它的真实 MySQL/MinIO、形成、删除、Packet 和 Agent 集成留到未来正式启用规划。
- Knowledge 不属于阶段2，现有实验性接口不作为稳定合同，也不进入阶段2或阶段9前端第一版。
- 阶段2范围内能力通过单元测试、数据库集成、鉴权、并发、OpenAPI 和敏感信息测试；未运行验证必须明确记录。
- 当前文档、迁移、Schema、环境配置和运行说明与代码事实一致，不保留相互冲突的阶段状态。
- 以上条件未满足前，阶段2状态保持“进行中”。

## 官方接口依据

- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)
- [DeepSeek Responses API 指南（仅作设计参考，不实施该端点）](https://api-docs.deepseek.com/zh-cn/guides/responses_api)
- [Anthropic Messages streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Gemini GenerateContent](https://ai.google.dev/api/generate-content)
- [MySQL 8.4 数据类型](https://dev.mysql.com/doc/refman/8.4/en/data-types.html)
- [LangChain/LangGraph Memory 概念](https://docs.langchain.com/oss/python/concepts/memory)
- [LangChain Deep Agents Memory 与后台归并](https://docs.langchain.com/oss/python/deepagents/memory)
- [Mem0 Open Source 概览与默认组件](https://docs.mem0.ai/open-source/overview)
- [Mem0 新 Memory 算法与 Graph 变化](https://docs.mem0.ai/platform/features/graph-memory)
- [Qdrant Fundamentals：过滤与近似检索约束](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)
- [Qdrant Points](https://qdrant.tech/documentation/manage-data/points/)
- [Qdrant Payload](https://qdrant.tech/documentation/concepts/payload/)
- [Qdrant Collections](https://qdrant.tech/documentation/manage-data/collections/)
