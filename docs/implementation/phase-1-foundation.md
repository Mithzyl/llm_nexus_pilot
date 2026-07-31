# 第一阶段：数据与控制平面规划

**文档日期：** 2026 年 7 月 30 日
**文档状态：** `PARTIAL`，重新打开
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

## 目标

- 建立稳定事务、Repository、查询和资源归属框架。
- 用户能够从自身身份开始查询 Session、Message、Run、Task、Attempt、Artifact 和相关历史。
- 为 Memory、RabbitMQ outbox、Worker 和 Agent 提供可组合事务与可追溯事实。
- 明确不实现：RabbitMQ、Worker、Memory 检索算法、Agent 工作流、工具执行和前端页面。

## 当前事实

当前已经实现：

- FastAPI、API Key 认证、请求级 `AsyncSession`、SQLAlchemy ORM、Alembic 和 MinIO。
- `POST /users`、`POST /runs`、`POST /runs/{run_id}/tasks`、`POST /runs/{run_id}/attempts` 和 artifact 上传。
- `GET /runs/{run_id}` 聚合详情、`GET /tasks/{task_id}` 和 Provider 列表。
- Run、Task、Attempt、Retry、Artifact、Tool Call、Evaluation 和 Outbox ORM 表。

当前缺失：

- User 详情与列表查询。
- Session、Conversation 和 Message 实体及接口。
- Run、Task、Attempt、Retry、Artifact 的独立分页列表。
- Artifact 内容读取或受控下载。
- Tool Call、Evaluation、Outbox 的内部查询接口。
- 统一 cursor、过滤、排序和资源归属规范。
- 可以组合多个 Repository 的 Unit of Work；当前多个 Service 自己 `commit()`，不能作为 transactional outbox 的可靠基础。

因此 Phase 1 不能标记为完成。

## 资源边界

### 用户

```http
POST  /api/v1/users
GET   /api/v1/users/{user_id}
GET   /api/v1/users
PATCH /api/v1/users/{user_id}
```

- 支持按激活状态和创建时间查询。
- 更新仅允许显示名称、激活状态等明确字段。
- 禁止通过通用 PATCH 修改资源归属或主键。

### Session 与 Message

新增持久化实体：

```text
llm_sessions
llm_messages
```

建议接口：

```http
POST  /api/v1/sessions
GET   /api/v1/sessions/{session_id}
GET   /api/v1/sessions?user_id=&status=
PATCH /api/v1/sessions/{session_id}

POST  /api/v1/sessions/{session_id}/messages
GET   /api/v1/sessions/{session_id}/messages
GET   /api/v1/messages/{message_id}
```

Message 是不可变历史，至少保存：

```text
message_id
session_id
run_id
parent_message_id
role
content_type
content_text 或 content_uri
sequence
token_count
metadata_json
created_at
```

完整大内容写入 MinIO；列表接口只返回摘要和 URI。

### Run

```http
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs?user_id=&session_id=&status=&created_after=&created_before=
POST /api/v1/runs/{run_id}/cancel
GET  /api/v1/runs/{run_id}/tasks
```

- `cancel` 使用状态机，不开放任意状态覆盖。
- Run 详情可以聚合摘要，但大量 Task、Attempt 和 Artifact 必须通过独立分页接口读取。

### Task

```http
POST /api/v1/runs/{run_id}/tasks
GET  /api/v1/tasks/{task_id}
GET  /api/v1/tasks?run_id=&status=&task_type=&assigned_role=
POST /api/v1/tasks/{task_id}/cancel
POST /api/v1/tasks/{task_id}/retry
```

- 父任务和依赖任务必须属于同一 Run。
- retry 必须创建新的执行事实，不能覆盖旧错误。
- 状态转换由统一状态机校验。

### Attempt 与 Retry

```http
GET /api/v1/attempts/{attempt_id}
GET /api/v1/attempts?run_id=&task_id=&provider=&model=&status=
GET /api/v1/attempts/{attempt_id}/retries
```

- 列表默认不返回原始请求和响应内容。
- 每个物理重试保留独立顺序、耗时、状态码和安全错误。
- 手工写入 attempt 的兼容接口是否继续公开，需要在 Phase 1 完成前确认。

### Artifact

```http
POST /api/v1/runs/{run_id}/artifacts
GET  /api/v1/artifacts/{artifact_id}
GET  /api/v1/artifacts?run_id=&task_id=&artifact_type=
GET  /api/v1/artifacts/{artifact_id}/content
```

- 内容读取必须验证资源归属和权限。
- 可以返回受时限约束的下载地址或代理内容，但不能暴露对象存储凭据。
- 哈希、大小、MIME 和存储位置必须与实际对象一致。

### 内部审计资源

Tool Call、Evaluation 和 Outbox 使用独立内部接口与更严格认证：

```http
GET /api/v1/internal/tool-calls
GET /api/v1/internal/evaluations
GET /api/v1/internal/outbox-events
GET /api/v1/internal/outbox-events/{event_id}
```

这些接口用于审计和运维，不与普通用户资源查询混用。

## 两类 Message 的边界

平台必须区分：

1. LLM 会话消息：User、Assistant、Tool 的对话和上下文历史，存入 `llm_messages`。
2. 基础设施消息：Outbox 和后续 RabbitMQ delivery，存入 outbox/delivery 表。

两者具有不同 Schema、权限、保留周期和查询接口，不能共用一张“messages”表。

## 事务框架

目标结构：

```text
FastAPI dependency
  ↓
Request-scoped AsyncSession
  ↓
Unit of Work
  ├── UserRepository
  ├── SessionRepository
  ├── RunRepository
  ├── TaskRepository
  ├── AttemptRepository
  └── OutboxRepository
  ↓
一次 commit 或 rollback
```

约束：

- Application Service 决定一个业务用例的事务边界。
- Repository 负责查询、add、flush，不自行 commit。
- 多个写操作可以在同一个 Unit of Work 中原子完成。
- 请求异常和取消必须 rollback 并关闭 session。
- 状态更新使用版本号或带旧状态条件的 update，防止并发静默覆盖。
- 数据库唯一约束作为幂等和竞争条件的最后保护，不能只依赖先查后写。

## 查询契约

所有增长型列表统一使用 cursor：

```json
{
  "items": [],
  "next_cursor": "opaque-or-null",
  "has_more": false
}
```

统一要求：

- cursor 基于稳定排序，例如 `created_at + primary_key`。
- cursor 对调用方不透明，非法或篡改值返回稳定 422。
- `limit` 有默认值和最大值。
- 同一查询的排序方向固定。
- 时间统一使用带时区 UTC。
- 列表不默认嵌入大内容、原始模型响应或完整文件。
- 常用过滤组合必须有对应数据库索引。
- 所有业务查询检查 user/session/run 归属，不能只凭资源 ID 返回。

## Service 与 Query 分层

- Command Service：创建、取消、重试、归档和状态转换。
- Query Service：详情、分页列表、过滤和聚合摘要。
- Repository：封装 SQLAlchemy 查询与持久化，不返回 HTTP Response。
- Router：解析 HTTP 输入并调用 Service，不直接编写 ORM 查询。
- HTTP 错误转换位于应用边界，领域和 Repository 不应依赖厂商 SDK。

## 实施步骤

| 步骤 | 修改对象 | 预期结果 | 验证方式 |
|---|---|---|---|
| 1 | Unit of Work 与 Repository 契约 | 跨多个资源可以原子提交或回滚 | 事务故障注入测试 |
| 2 | Cursor、分页结果和查询参数 | 所有列表行为一致且有上限 | cursor 边界与排序测试 |
| 3 | User 查询与更新 | 用户能够查询和管理自身基础状态 | API 与归属测试 |
| 4 | Session 与 Message | 会话和消息成为正式资源 | 顺序、不可变性和分页测试 |
| 5 | Run 与 Task 查询/动作 | 可以发现历史并执行合法取消、重试 | 状态机和并发测试 |
| 6 | Attempt、Retry 与 Artifact 查询 | 调用与文件历史可独立检索 | 过滤、分页和权限测试 |
| 7 | Tool/Evaluation/Outbox 内部查询 | 运维和审计事实可读取 | 内部认证和脱敏测试 |
| 8 | MySQL 集成与迁移 | ORM、索引和迁移与真实数据库一致 | MySQL 集成、`alembic check` |
| 9 | 文档和兼容策略 | 旧接口去留和迁移方式明确 | OpenAPI diff 与文档检查 |

## 测试要求

- 每个核心资源覆盖新增、详情、列表和必要动作。
- 空列表、单页、多页、非法 cursor、最大 limit 和稳定排序。
- 不存在资源、跨用户访问、跨 Run 关联和停用用户。
- 非法状态转换、重复幂等键和并发更新。
- 跨多个 Repository 的整体提交与整体回滚。
- Session message 顺序、不变性和大内容 URI。
- Artifact 上传、元数据查询、权限读取和对象存储故障。
- Outbox 和内部审计接口的脱敏与权限。
- SQLite 只用于快速测试；Phase 1 完成必须有真实 MySQL 行为验证。

## 完成标准

Phase 1 标记 `DONE` 前必须满足：

- User、Session、Message、Run、Task、Attempt、Retry 和 Artifact 具有可用查询闭环。
- Tool Call、Evaluation 和 Outbox 具有受限内部查询入口。
- 列表接口统一 cursor、过滤、排序、limit 和归属检查。
- Service 不再各自随意 commit，事务由 Unit of Work 统一控制。
- 合法状态转换、幂等与并发保护经过测试。
- MySQL、MinIO、迁移、OpenAPI、单元和集成测试全部通过。
- Phase 1 完成前不恢复 Phase 3 开发。

## 后续阶段入口

Phase 2 统一包含 Model Gateway、Context、Memory、Knowledge、Prompt/模型能力目录和 Evaluation，详见 [`phase-2-llm-core-capabilities.md`](./phase-2-llm-core-capabilities.md)。
