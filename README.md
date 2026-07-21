# NexusPilot LLM Platform

NexusPilot 是一个统一调用模型、拆解任务、执行工具并保存审核证据的 LLM 运行平台。本仓库当前实现规划中的阶段 1 基础服务。

## 当前能力

- FastAPI 健康检查和版本化 REST API；
- API Key 基础认证；
- 基础用户身份创建与运行归属校验；
- 创建并查询运行、任务和模型调用记录；
- 保存任务依赖、工具调用、产物、审核结果和 outbox 事件的数据结构；
- Alembic MySQL 数据库迁移；
- MinIO 对象存储客户端和产物上传接口；
- Docker Compose 本地基础设施定义。

多供应商模型调用、RabbitMQ Worker、工具循环、Agent 协作和 OpenTelemetry 按规划留到后续阶段，不在当前服务中伪实现。

## 后端本地启动

后端必须使用项目虚拟环境：

```bash
cp .env.example .env
make api-install
docker compose -f deployments/compose/docker-compose.yml up -d mysql minio
make api-migrate
make api-run
```

API 文档位于 `http://127.0.0.1:8000/docs`。除 `/health` 外，请求需要携带：

```text
X-API-Key: .env 中的 NEXUSPILOT_API_KEY
```

运行测试和静态检查：

```bash
make api-test
make api-lint
```

详细接口和阶段边界见 `docs/implementation/phase-1-foundation.md`。

下一阶段的实施范围、统一模型契约和验收条件见 `docs/implementation/phase-2-multi-provider-model-plan.md`。
