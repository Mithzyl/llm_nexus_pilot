# NexusPilot LLM Platform

NexusPilot 是一个统一调用模型、管理运行与调用证据，并为后续任务和 Agent 执行提供底座的 LLM 平台。阶段1数据与控制平面、阶段2 LLM 核心能力已经完成；阶段3进入需求与可靠性规划，尚未实施。Memory 当前只保留规划和实验性准备代码，Knowledge 等待独立知识库规划，二者都不进入实际 `/responses` 调用链。

## 当前能力

- FastAPI 健康检查和版本化 REST API；
- API Key 基础认证；
- 受信 API 调用方可以创建、查询、分页筛选和受限更新用户及会话；分页 cursor 与完整筛选条件绑定；
- 可以追加不可变会话消息，按数据库分配的序号分页读取，并验证 Message、Run 与 Session 归属；
- Run 和 Task 支持独立签名 cursor 分页、归属与状态过滤；列表只返回有界预览，Run 兼容聚合详情对每类子资源最多返回 100 条并明确标记是否还有更多；
- Run/Task 取消和 Task 重试经过状态机保护，运行中取消及重试请求与 outbox 事实原子提交；
- Attempt 支持按 Run/Task、Provider、Model、状态和时间独立分页，物理请求 Retry 可按 Attempt 顺序查询；
- Artifact 支持元数据独立分页和受控流式内容读取，查询响应不暴露 MinIO 内部对象 URI；
- Model Tool Call、Task Evaluation 和 Outbox Event 提供双密钥内部分页、归属校验和脱敏详情；
- 基础用户身份创建与运行归属校验；
- 创建运行、任务和模型调用记录，并通过独立详情和分页接口读取执行历史；
- 保存任务依赖、工具调用、产物、审核结果和 outbox 事件的数据结构；
- Alembic MySQL 数据库迁移；
- MinIO 对象存储客户端和产物上传接口；
- Docker Compose 本地基础设施定义。
- OpenAI、DeepSeek、Anthropic 和 Gemini 的独立 Provider codec；
- 可配置的通用 OpenAI Chat Completions 兼容 Provider；
- 单一 `POST /api/v1/responses` 普通生成和 SSE 流式接口；
- 供应商注册发现、超时、重试、结构化输出、工具调用和费用估算；
- 每次物理 HTTP 重试及原始请求/响应的持久化证据。
- 当前用户长期 Memory 事实账本支持有来源的候选/有效事实、不可变版本、显式 User/Session/Run/Task 范围、激活/拒绝/替代/逻辑删除、持久化幂等和乐观并发控制；
- Memory Retrieval 使用有界的中英文词法候选、版本化整数评分、整条内容 token 预算和持久化检索证据，不返回跨 User、范围不匹配、非有效或已过期内容。
- L0 手动 Agent Working State、L1 Session State/Summary、L2 Handoff/Run Snapshot、L3 Project/Profile 和 L4 User Profile 已有实验性同步接口；这些接口不属于当前模型响应链，Memory Packet 不在阶段2创建或绑定 Model Attempt。

当前 Memory 只作为规划与实验性准备代码保留，不继续扩展，也不接入 `/responses`。Context Preview 的稳定合同已移除 Memory/Knowledge 候选字段，只选择显式 instruction、安全 instruction 和 Session Message，并使用 Model Catalog 与不可变来源快照。Knowledge 属于后续独立知识库规划；现有 Knowledge 结构代码不是稳定能力。Conversation Context、Prompt/模型能力目录和 Evaluation/Guardrail 已通过阶段2行为与基础设施验收。默认不引入 Vector Store 或 Mem0。

## 后端结构

后端采用按职责分层的 MVC 风格结构。Router 是 HTTP Controller，不直接编写数据库事务；Service 承担业务规则；SQLAlchemy Model 和 Pydantic Schema 分开维护：

```text
apps/api/src/nexuspilot_api/
├── main.py                 # 应用创建与顶层资源生命周期
├── core/                   # 配置、认证、FastAPI 依赖注入
├── features/
│   └── memory/             # Memory 业务域，内部按 models/schemas/services/routers 分层
├── routers/                # 非 Memory 资源的 HTTP Controller
├── services/               # 按业务资源拆分的事务与业务逻辑
├── models/                 # SQLAlchemy 持久化模型
├── schemas/                # Pydantic HTTP 请求和响应结构
└── infrastructure/         # 数据库、MinIO、Provider 注册等外部适配
```

`packages/models` 是独立的模型供应商适配包，不依赖 FastAPI 和平台数据库。新增接口时应在对应资源 Router 和 Service 中扩展，不再向单一聚合路由文件追加所有行为。

## 后端本地启动

后端必须使用项目虚拟环境：

```bash
cp .env.example .env
make api-install
docker compose -f deployments/compose/docker-compose.yml up -d mysql minio
make api-migrate
make api-run
```

如果宿主机 MySQL 已占用 `3306`，可以把项目 MySQL 隔离到其他端口：

```bash
NEXUSPILOT_MYSQL_HOST_PORT=3307 \
  docker compose -f deployments/compose/docker-compose.yml up -d mysql minio
```

此时本地虚拟环境使用：

```text
NEXUSPILOT_DATABASE_URL=mysql+aiomysql://nexuspilot:nexuspilot@localhost:3307/nexuspilot
```

Compose 中的 API 会自动使用容器内部的 `mysql:3306` 和 `minio:9000`，不会读取宿主机地址。

API 文档位于 `http://127.0.0.1:8000/docs`。除 `/health` 外，请求需要携带：

```text
X-API-Key: .env 中的 NEXUSPILOT_API_KEY
```

`/api/v1/internal/*` 审计接口还必须同时携带：

```text
X-Internal-API-Key: .env 中独立配置的 NEXUSPILOT_INTERNAL_API_KEY
```

公共和内部密钥必须不同，否则应用配置校验失败。

分页 cursor 使用服务端 HMAC 签名。生产环境应另外配置 `NEXUSPILOT_CURSOR_SIGNING_KEY`；未配置时暂时回退使用 API Key，便于本地启动。

## 统一 Responses 接口

Provider 只在服务端配置相应凭据后注册。可以通过以下接口查看当前可用 Provider：

```http
GET /api/v1/providers
```

普通生成请求：

```json
{
  "run_id": "已创建的 run_id",
  "provider": "deepseek",
  "model": "供应商实际模型名称",
  "input": "解释事务隔离级别",
  "instructions": "回答应简洁且准确",
  "max_output_tokens": 1000,
  "idempotency_key": "业务侧唯一请求编号"
}
```

DeepSeek 当前通过专用 Chat Completions 适配器调用，不调用 DeepSeek 提供的 Responses 端点。示例模型 allowlist 为 `deepseek-v4-flash,deepseek-v4-pro`；思考控制使用平台统一字段，由适配器转换为 DeepSeek 顶层 `thinking` 和 `reasoning_effort`：

```json
{
  "run_id": "已创建的 run_id",
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "input": "分析这段代码的失败边界",
  "reasoning": {
    "enabled": true,
    "effort": "high"
  },
  "idempotency_key": "业务侧唯一请求编号"
}
```

`deepseek-v4-pro` 的 `low` 会在网络请求前被拒绝；思考启用时传入 `temperature` 也会被拒绝。流式响应只有收到 DeepSeek 的 `[DONE]` 终止标记才会记为完成。`strict` 工具和结构化 JSON Schema 目前没有经过模型能力目录确认，因此不会被静默降级或发送到普通 DeepSeek 端点。

同一个接口设置 `"stream": true` 后返回 SSE。公开事件固定为：

```text
response.started
response.text.delta
response.tool_call.delta
response.usage
response.completed
response.failed
```

除四家内置适配器外，OpenAI Chat Completions 风格的本地或第三方服务可配置为：

```text
NEXUSPILOT_OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
NEXUSPILOT_OPENAI_COMPATIBLE_API_KEY=
NEXUSPILOT_OPENAI_COMPATIBLE_MODELS=example-model
```

请求时使用 `"provider": "openai_compatible"`。模型 allowlist 留空表示允许该 Provider 下任意非空模型名；生产环境建议显式配置。

## 实验性 Memory 管理接口

以下资源只用于受信开发调用和 Memory 设计验证，不会被 `/responses` 或前端第一版自动使用，也不代表 Memory 已成为正式运行能力。现有 Context Preview 的实验性候选分支不得作为正式消费者。Session、Collaboration、Project 和 Profile 使用 `features/memory` 中的独立实验性接口与持久化结构：

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

正文限制为 1～4000 个字符，每个版本必须有 1～20 个来源。模型提出的内容只能从 `candidate` 开始，不能直接成为有效事实。带时区的过期时间会先归一化为 UTC；Session、Run、Task 范围外键禁止通过删除父资源静默放宽。删除会立即使 Memory 不可检索，并擦除所有版本的内联正文和词法检索词；独立的 Message、Artifact 等原始来源不会被级联删除。当前公共 API Key 代表受信服务调用方，不代表已经完成最终用户身份认证授权。

运行测试和静态检查：

```bash
make api-test
make api-lint
```

真实基础设施测试默认跳过，显式配置后执行：

```bash
NEXUSPILOT_TEST_MYSQL_URL=mysql+aiomysql://nexuspilot:nexuspilot@127.0.0.1:3307/nexuspilot \
NEXUSPILOT_RUN_MINIO_TEST=1 \
apps/api/.venv/bin/pytest -q apps/api/tests/test_real_infrastructure.py
```

项目阶段、质量门禁和当前完成度以 [`platform-roadmap.md`](docs/architecture/platform-roadmap.md) 为准。

- 阶段1数据与控制平面：[`phase-1-foundation.md`](docs/implementation/phase-1-foundation.md)
- 阶段2 LLM 核心能力：[`phase-2-llm-core-capabilities.md`](docs/implementation/phase-2-llm-core-capabilities.md)
- 阶段3 RabbitMQ 规划（规划中，尚未实施）：[`phase-3-rabbitmq-task-execution-plan.md`](docs/implementation/phase-3-rabbitmq-task-execution-plan.md)
- 阶段9 Web 前端规划：[`phase-9-web-ui-plan.md`](docs/frontend/phase-9-web-ui-plan.md)

## Web 前端本地启动

阶段9第一版前端位于 `apps/web`，采用 Next.js 服务端转发层连接 FastAPI。浏览器不直接持有平台 API Key：

```bash
cd apps/web
npm install
NEXUSPILOT_API_URL=http://127.0.0.1:8000 \
NEXUSPILOT_API_KEY=local-development-key-change-me \
npm run dev
```

打开 `http://localhost:3000`。界面以三栏黑白主题为基线，包含会话侧栏、对话工作区、SSE 流式响应和运行证据检查器。具体行为边界见 [`phase-9-web-ui-plan.md`](docs/frontend/phase-9-web-ui-plan.md)。
