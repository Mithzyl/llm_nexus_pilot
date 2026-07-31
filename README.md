# NexusPilot LLM Platform

NexusPilot 是一个统一调用模型、管理上下文与记忆、拆解任务、执行工具并保存审核证据的 LLM 运行平台。当前 Model Gateway 已可运行，但 Phase 1 数据与查询底座、Phase 2 其余 LLM 核心能力仍在完善，Phase 3 已暂停。

## 当前能力

- FastAPI 健康检查和版本化 REST API；
- API Key 基础认证；
- 受信 API 调用方可以创建、查询、分页筛选和受限更新用户及会话；
- 可以追加不可变会话消息，按数据库分配的序号分页读取，并验证 Message、Run 与 Session 归属；
- Run 和 Task 支持独立签名 cursor 分页、归属与状态过滤；列表只返回有界预览；
- Run/Task 取消和 Task 重试经过状态机保护，运行中取消及重试请求与 outbox 事实原子提交；
- 基础用户身份创建与运行归属校验；
- 创建运行、任务和模型调用记录，并通过现有详情接口读取部分历史；完整列表和反向查询仍属于 Phase 1 待办；
- 保存任务依赖、工具调用、产物、审核结果和 outbox 事件的数据结构；
- Alembic MySQL 数据库迁移；
- MinIO 对象存储客户端和产物上传接口；
- Docker Compose 本地基础设施定义。
- OpenAI、DeepSeek、Anthropic 和 Gemini 的独立 Provider codec；
- 可配置的通用 OpenAI Chat Completions 兼容 Provider；
- 单一 `POST /api/v1/responses` 普通生成和 SSE 流式接口；
- 供应商注册发现、超时、重试、结构化输出、工具调用和费用估算；
- 每次物理 HTTP 重试及原始请求/响应的持久化证据。

RabbitMQ Worker、工具循环、Agent 协作和 OpenTelemetry 按规划留到后续阶段，不在当前服务中伪实现。

## 后端结构

后端采用按职责分层的 MVC 风格结构。Router 是 HTTP Controller，不直接编写数据库事务；Service 承担业务规则；SQLAlchemy Model 和 Pydantic Schema 分开维护：

```text
apps/api/src/nexuspilot_api/
├── main.py                 # 应用创建与顶层资源生命周期
├── core/                   # 配置、认证、FastAPI 依赖注入
├── routers/                # 按 users/sessions/runs/tasks/attempts/artifacts/responses 拆分的控制器
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

- Phase 1 数据与控制平面：[`phase-1-foundation.md`](docs/implementation/phase-1-foundation.md)
- Phase 2 LLM 核心能力：[`phase-2-llm-core-capabilities.md`](docs/implementation/phase-2-llm-core-capabilities.md)
- Phase 3 RabbitMQ 规划（暂停）：[`phase-3-rabbitmq-task-execution-plan.md`](docs/implementation/phase-3-rabbitmq-task-execution-plan.md)
