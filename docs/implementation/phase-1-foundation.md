# 第一阶段：数据与控制平面规划

**文档日期：** 2026 年 7 月 31 日
**文档状态：** `PARTIAL`，重新打开
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

## 目标

- 建立稳定数据库事务、查询和资源归属框架。
- 用户能够从自身身份开始查询 Session、Message、Run、Task、Attempt、Artifact 和相关历史。
- 为 Memory、RabbitMQ outbox、Worker 和 Agent 提供可组合事务与可追溯事实。
- 明确不实现：RabbitMQ、Worker、Memory 检索算法、Agent 工作流、工具执行和前端页面。

## 当前事实

当前已经实现：

- FastAPI、API Key 认证、请求级 `AsyncSession`、SQLAlchemy ORM、Alembic 和 MinIO。
- `POST /users`、`POST /runs`、`POST /runs/{run_id}/tasks`、`POST /runs/{run_id}/attempts` 和 artifact 上传。
- `GET /runs/{run_id}` 聚合详情、`GET /tasks/{task_id}` 和 Provider 列表。
- Run、Task、Attempt、Retry、Artifact、Tool Call、Evaluation 和 Outbox ORM 表。
- User Service 的请求级数据库事务和签名 cursor 基础设施。
- `GET /users/{user_id}`、`GET /users` 和受限 `PATCH /users/{user_id}`。
- Session、Message ORM 与迁移，以及 Session 新增、详情、筛选、更新和 Message 追加、详情、顺序分页接口。
- Run、Task 独立分页查询，以及 Run/Task 取消和 Task 重试状态动作。

当前缺失：

- Attempt、Retry、Artifact 的独立分页列表。
- Artifact 内容读取或受控下载。
- Tool Call、Evaluation、Outbox 的内部查询接口。
- 统一 cursor、过滤、排序和资源归属规范。
- 各 Service 当前直接操作请求级数据库会话并自行 `commit()`；尚未形成支持 transactional outbox 的跨资源原子事务用例。

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

Run 取消状态转换：

| 当前状态 | 取消结果 | 子任务处理 |
|---|---|---|
| `pending` / `running` | 没有运行中任务时进入 `cancelled`；否则进入 `cancel_requested` | 未开始任务直接 `cancelled`；运行中任务进入 `cancel_requested` 并写入 outbox 事实 |
| `cancel_requested` / `cancelled` | 幂等返回当前状态 | 不重复写入 outbox |
| `completed` / `failed` | 返回 409 | 不修改任何记录 |

Run 列表只返回请求预览，不嵌入完整用户请求、Task、Attempt 或 Artifact。cursor 与完整过滤条件绑定，不能跨另一组 user/session/status/time-range 过滤复用。

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

Task 动作状态转换：

| 动作 | 允许状态 | 结果 |
|---|---|---|
| cancel | `pending`、`ready`、各 waiting 状态、`retry_scheduled` | 直接进入 `cancelled` |
| cancel | `running` | 进入 `cancel_requested`，并原子写入 `task.cancel_requested` outbox 事实 |
| cancel | `cancel_requested`、`cancelled` | 幂等返回，不重复写入事件 |
| cancel | `completed`、`failed` | 返回 409 |
| retry | `failed` 且 `current_attempt < max_attempts` | `current_attempt + 1`，进入 `retry_scheduled`，原子写入 `task.retry_requested` outbox 事实 |
| retry | 其他状态或已达最大次数 | 返回 409 |

Task 列表只返回 objective 预览；支持按 run、status、task type 和 assigned role 过滤。`GET /runs/{run_id}/tasks` 是强制限定 Run 的便捷查询，和 `GET /tasks?run_id=` 使用同一查询实现。

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

## 数据库事务框架

当前结构：

```text
FastAPI dependency
  ↓
Request-scoped AsyncSession
  ↓
Application Service
  ├── ORM 查询与持久化
  └── 用例级 commit 或 rollback
```

当出现一个用例同时写入多个资源的真实需求（例如 Task + Outbox）时，再提取数据库事务协调器：

```text
Application Service
  ↓
Database Transaction Coordinator
  ├── Task 数据操作
  └── Outbox 数据操作
  ↓
一次 commit 或 rollback
```

约束：

- Application Service 决定一个业务用例的数据库事务边界。
- 当前单资源用例直接使用请求级 `AsyncSession`，不为薄查询包装额外 Repository。
- Service 参数统一命名为 `db_session`，明确它是数据库会话而不是普通业务会话。
- 只有跨多个资源的事务边界真实出现后，才提取事务协调器和有独立查询职责的 Repository。
- 多个写操作必须能够在同一个数据库会话中原子完成。
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
- Service 内的数据操作不返回 HTTP Response；复杂、可复用查询出现后可以提取专用 Repository。
- Router：解析 HTTP 输入并调用 Service，不直接编写 ORM 查询。
- HTTP 错误转换位于应用边界，领域和 Repository 不应依赖厂商 SDK。

## 实施步骤

| 步骤 | 修改对象 | 预期结果 | 验证方式 |
|---|---|---|---|
| 1 | 数据库会话与事务边界 | 单资源 Service 明确提交或回滚；跨资源用例出现时再提取协调器 | 事务故障注入测试 |
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
- 跨多个资源的整体提交与整体回滚。
- Session message 顺序、不变性和大内容 URI。
- Artifact 上传、元数据查询、权限读取和对象存储故障。
- Outbox 和内部审计接口的脱敏与权限。
- SQLite 只用于快速测试；Phase 1 完成必须有真实 MySQL 行为验证。

## 2026 年 7 月 31 日补充实现

- User Service 直接使用请求级 `db_session` 完成查询、提交和回滚；删除当前仅转发 SQLAlchemy 调用的 Repository 和 Unit of Work，避免在没有跨资源事务时过早抽象。
- 新增 HMAC 签名 cursor；内部数据库分页键（`DatabasePaginationKey`）包含 `created_at + user_id`，非法、篡改或错误密钥返回稳定 422。
- User 列表支持 `is_active` 过滤，`limit` 范围为 1～100，排序固定为创建时间和用户 ID 正序。
- User PATCH 只允许 `display_name` 和 `is_active`，禁止主键和未知字段。
- 为新 ORM 记录统一应用侧 UTC 时间，同时保留数据库 server default，避免 SQLite 和 MySQL 时间精度差异破坏 cursor 边界。
- 新增 API、cursor、数据库事务和唯一约束竞争保护测试；公开 API 行为不受数据层简化影响。
- 当前认证仍是平台级 API Key，只能将这些接口定义为“受信调用方管理接口”，尚不能声称完成用户本人资源隔离。
- Phase 1 状态保持 `PARTIAL`；本次数据层简化后继续以真实资源用例验证事务边界。

## 2026 年 7 月 31 日 Session / Message 实施

- 新增 `llm_sessions` 和 `llm_messages`；会话归属于 User，消息归属于 Session，并可选关联同一用户、同一会话下的 Run 和父消息。
- Session 支持新增、详情、按 user/status 筛选的签名 cursor 分页，以及 title/status 的受限更新。
- Message 创建后不提供更新或删除接口；内容必须在最多 16000 字符的内联文本和对象 URI 中二选一，列表只返回最多 200 字符的预览和 URI，完整内联内容仅由详情接口返回。
- Message 顺序由锁定会话行后读取并递增 `next_message_sequence` 分配，数据库唯一约束 `(session_id, sequence)` 作为竞争条件的最终保护。
- Message 分页使用数据库序号键，并把 `session_id` 写入签名 cursor；其他会话不能复用该 cursor 跳过历史。
- Run 创建现在会验证 Session 存在、处于 active 状态且与 Run 属于同一 User；存量 `llm_runs.session_id` 暂不增加数据库外键，以避免现有任意字符串数据导致不可逆迁移失败。
- Session 归档后仍可读取历史，但不能追加新消息。
- 该切片后的 Run / Task 查询与动作已在下一节完成，Phase 1 仍保持 `PARTIAL`。

## 2026 年 7 月 31 日 Run / Task 查询与动作实施

- 新增 `GET /runs`，支持 user、session、status 和 UTC 时间范围过滤；列表不返回完整 `user_request`，只返回 200 字符预览。
- 新增 `GET /tasks` 和 `GET /runs/{run_id}/tasks`，共享 run、status、task type、assigned role 过滤；列表只返回 200 字符 objective 预览。
- Run/Task cursor 使用 `DatabaseQueryPaginationKey`，包含稳定的 `created_at + primary_key` 数据库边界和完整过滤条件指纹；改变过滤条件后复用 cursor 返回 422。
- 新增 Run cancel、Task cancel 和 Task retry；状态转换严格按本节状态表执行，不提供通用状态 PATCH。
- Run 取消和 Task 动作统一采用“先锁 Run、再锁 Task”的 MySQL 行锁顺序；并发 retry 只有一个请求能够创建 `task.retry_requested` outbox 事实。
- 运行中 Task 的取消创建 `task.cancel_requested` outbox 事实；立即取消的未开始任务不产生无意义队列消息。
- outbox 记录与状态更新使用同一个 `db_session.commit()`，Publisher 尚未实现，因此当前只保存可靠待发布事实，不发送 RabbitMQ 消息。
- 新增 Run/Task 常用过滤和稳定排序组合索引，并通过 Alembic `20260731_0004` 管理。
- 将 SQLAlchemy 升级到 `2.0.51`、aiomysql 升级到 `0.3.2`，并增加 `cryptography 46.0.7`，修复 MySQL 8/9 默认认证和连接池 `ping()` 兼容问题。
- 真实基础设施验证：MySQL 8.4 空库迁移到 head、`alembic check` 和双会话并发 retry 通过；MinIO bucket 初始化、对象上传、stat 和清理通过。
- 宿主机 MySQL 9.0.1 保持运行且未被修改；因没有可用管理员登录，真实验证使用项目 Compose MySQL 8.4 并映射到 `3307`，避免占用宿主机 `3306`。
- 当前 Phase 1 仍为 `PARTIAL`；下一切片是 Attempt、Retry 和 Artifact 的独立查询与受控内容读取。

## 完成标准

Phase 1 标记 `DONE` 前必须满足：

- User、Session、Message、Run、Task、Attempt、Retry 和 Artifact 具有可用查询闭环。
- Tool Call、Evaluation 和 Outbox 具有受限内部查询入口。
- 列表接口统一 cursor、过滤、排序、limit 和归属检查。
- 每个 Service 的事务边界明确；跨资源用例由同一数据库事务原子提交。
- 合法状态转换、幂等与并发保护经过测试。
- MySQL、MinIO、迁移、OpenAPI、单元和集成测试全部通过。
- Phase 1 完成前不恢复 Phase 3 开发。

## 后续阶段入口

Phase 2 统一包含 Model Gateway、Context、Memory、Knowledge、Prompt/模型能力目录和 Evaluation，详见 [`phase-2-llm-core-capabilities.md`](./phase-2-llm-core-capabilities.md)。
