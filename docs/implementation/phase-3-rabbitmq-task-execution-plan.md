# 第三阶段：RabbitMQ 任务执行需求讨论与功能规划

**文档日期：** 2026 年 7 月 30 日
**文档状态：** `PAUSED`，保留设计但暂停实施
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

> Phase 1 的 Unit of Work、资源查询、Session/Message 和状态管理完成前，不恢复本阶段开发。

## 目标

- 要实现的结果：API 在一个 MySQL 事务中创建任务和 outbox 事件，发布进程可靠地把事件交给 RabbitMQ，Worker 至少一次消费并以幂等方式更新任务结果。
- 要实现的结果：进程崩溃、连接中断、发布确认丢失、消息重复、临时失败和永久失败都有确定处理方式与可查询证据。
- 明确不处理：多 Agent 编排、工具循环、代码修改、MCP、OpenTelemetry 和前端页面。
- 明确不处理：用 RabbitMQ 消息正文保存完整提示词、文件、模型回答、凭据或任务唯一状态。

## 当前事实

- `llm_tasks` 已有任务状态、最大尝试次数、当前尝试和超时字段，但没有明确的执行输入、领取租约、最后错误和结果位置。
- `llm_outbox_events` 已有事件、聚合对象、载荷、发布次数和下次重试时间，但没有并发发布锁、最后发布错误和发布确认编号。
- 当前 API 创建任务时不会写 outbox，也没有 RabbitMQ 依赖、发布器或 Worker。
- 当前 `ModelInvocationService` 可以执行统一模型调用，但 `llm_tasks` 尚未定义足以让 Worker 重建模型请求的稳定执行载荷。
- 当前不能安全声称阶段 3 已开始运行。
- 当前优先级已调整为完成 Phase 1 数据与控制平面，并继续规划 Phase 2 的 LLM 核心能力单元。

## 可靠性结论

- 交付语义采用**至少一次**，不承诺 RabbitMQ 或分布式系统无法直接保证的“恰好一次”。
- 发布方使用 publisher confirms，并对不可路由消息启用 `mandatory` 失败处理。
- 消费方使用 manual acknowledgement；只有业务事务提交成功后才能 ack。
- 消费者必须幂等。发布确认可能在网络中丢失，发布方重发会产生重复消息；Worker 提交结果后、ack 前崩溃也会产生重复投递。
- 临时失败使用有限次数的延迟重试；不得使用立即 `nack(requeue=True)` 形成热循环。
- 永久失败和超过上限的任务进入死信队列，同时在 MySQL 中记录最终失败事实。
- 关键长期队列优先评估 quorum queue；是否在本地开发环境使用单节点 classic queue，不改变应用层幂等要求。

以上结论依据 RabbitMQ 官方对 publisher confirms、consumer acknowledgements、重复投递和 dead lettering 的说明，以及 aio-pika 的 robust connection/channel 能力。

## 待确认的业务契约

阶段 3 编码前必须确认以下四项。推荐值作为评审默认值，但尚不视为已授权业务规则。

| 决策 | 推荐值 | 原因与影响 |
|---|---|---|
| 哪类任务首先可执行 | 只开放 `model_response` | 复用已验证的统一模型服务，避免空 Worker 假装完成任务 |
| Worker 如何重建请求 | 为任务增加版本化 `execution_spec_json`，只保存小型非敏感配置；大型内容使用 MinIO URI | `objective` 不足以表达 provider/model/messages/结构化输出 |
| 创建任务是否自动入队 | 对可执行且无未完成依赖的任务，在同一事务写 `task.ready` outbox | 避免数据库已创建任务但消息丢失 |
| 取消语义 | `cancel_requested` 阻止新领取；运行中调用采用协作式取消，不能承诺强制终止外部模型计费 | 与当前状态枚举一致，避免虚假即时取消 |

如果不接受 `execution_spec_json`，必须先提供另一种能够版本化、校验并重建执行请求的数据契约；否则停止 Worker 实现。

## 消息契约

消息只包含定位和去重所需的小型字段：

```json
{
  "schema_version": 1,
  "message_id": "outbox event UUID",
  "event_type": "task.ready",
  "task_id": "task UUID",
  "run_id": "run UUID",
  "attempt": 1,
  "created_at": "RFC 3339 timestamp"
}
```

约束：

- `message_id` 全局唯一，并写入消费幂等记录。
- Worker 必须从 MySQL 重新读取任务及执行规格，不信任消息中的业务状态。
- 未知 `schema_version`、未知事件类型、缺失 ID 和非法 attempt 属于永久失败，不能无限重试。
- 消息不得携带 API Key、完整用户文件、完整模型结果或任意可执行命令。

## 拟议数据调整

### `llm_tasks`

拟增加：

```text
execution_spec_json
result_artifact_id
last_error_code
last_error_message
claimed_by
claim_expires_at
version
```

- `execution_spec_json` 必须由 Pydantic 的版本化 schema 校验。
- `version` 或等价条件更新用于状态 compare-and-set，防止两个 Worker 同时领取。
- 租约只处理 Worker 崩溃后的重新领取，不替代 RabbitMQ ack。

### `llm_outbox_events`

拟增加：

```text
locked_by
locked_at
last_error
confirmed_at
```

- 多发布器通过 MySQL 8 的行锁与 `SKIP LOCKED` 分批领取。
- 只有 broker confirm 成功后设置 `published_at/confirmed_at`。
- confirm 超时保留事件供重发；由消费幂等处理可能的重复消息。

### `llm_message_deliveries`

拟新增消费去重表：

```text
message_id primary key
task_id
worker_id
status
delivery_count
first_received_at
completed_at
last_error
```

该表记录消息处理事实；业务结果仍由 `llm_tasks`、`llm_attempts` 和 `llm_artifacts` 表达。

## 队列拓扑提案

```text
nexuspilot.tasks exchange (direct, durable)
  └── task.ready
        └── nexuspilot.tasks.execute
              ├── 临时失败 → 分级延迟重试队列
              └── 永久失败/超限 → nexuspilot.tasks.dead
```

建议：

- 生产关键队列使用 durable quorum queue、持久化消息、publisher confirms 和 manual ack。
- retry 使用有限的固定档位，例如短、中、长三个延迟，不允许调用方提供无限 TTL。
- DLX 优先通过 RabbitMQ policy 配置，不把环境策略硬编码到应用声明参数。
- consumer prefetch 从小值开始，以单个任务的模型调用时长和数据库连接占用实测调整。

队列名称、虚拟主机、重试延迟和 prefetch 均通过环境配置提供；代码不得假设生产拓扑与本地完全相同。

## 处理流程

```text
API 创建可执行任务
  ↓ 同一数据库事务
写 llm_tasks + llm_outbox_events
  ↓ commit
Outbox publisher 领取事件
  ↓ persistent + mandatory + publisher confirm
RabbitMQ 接受并路由
  ↓
标记 outbox confirmed
  ↓ manual ack consumer
Worker 读取任务并原子领取
  ↓
执行已注册 handler
  ↓ 同一数据库事务
写 attempt/result + task terminal state + delivery completed
  ↓ commit 成功
ack RabbitMQ delivery
```

## 故障窗口与预期行为

| 故障窗口 | 必须发生的行为 |
|---|---|
| task 已提交、publisher 未运行 | outbox 保持 pending，恢复后发布 |
| broker 已接收、confirm 丢失 | publisher 可重发；同一 `message_id` 不重复产生业务副作用 |
| 消息不可路由 | mandatory return 视为发布失败，outbox 不标记成功 |
| Worker 收到消息后崩溃 | 未 ack 消息重新投递；过期任务租约允许重新领取 |
| 业务事务已提交、ack 丢失 | 重投时读取 delivery/任务终态并安全 ack，不再次执行模型 |
| 临时网络或供应商故障 | 写失败证据并进入有限延迟重试 |
| 参数错误或不支持的任务类型 | 不重试，写最终失败并进入死信 |
| 超过最大尝试次数 | 任务标记 failed，消息进入死信，不再执行 |
| 收到取消请求 | 未领取任务不执行；运行中任务在安全点终止并记录 cancelled/failed 事实 |

## 实施范围

- 新增 `packages/messaging`：消息 schema、拓扑声明、可靠 publisher/consumer 抽象，不依赖 FastAPI Router。
- 新增 `packages/tasks`：任务状态转换、领取、幂等和 handler 协议。
- 新增 `apps/worker`：进程生命周期、配置、消费循环和健康检查。
- 修改 FastAPI task service：可执行任务与 outbox 在同一事务写入。
- 修改 ORM 与 Alembic：增加执行规格、发布锁、消费去重与错误证据字段。
- 修改 Compose：加入 RabbitMQ 和 Worker；生产凭据不写入仓库。
- 保留现有同步 `POST /api/v1/responses`，阶段 3 不强制所有模型调用异步化。

## 实施步骤

| 步骤 | 修改对象 | 预期结果 | 验证方式 |
|---|---|---|---|
| 1 | 版本化任务与消息 schema | 非法或未知版本在边界拒绝 | Pydantic 参数化单元测试 |
| 2 | 状态机和数据库迁移 | 只允许合法转换；并发领取只有一个成功 | 状态表测试、并发数据库测试、`alembic check` |
| 3 | task + outbox 原子创建 | 任意事务失败都不会只留下其中一方 | 事务故障注入测试 |
| 4 | outbox publisher | confirm 后才标记成功；不可路由和超时可恢复 | fake channel 单元测试、RabbitMQ 集成测试 |
| 5 | Worker 幂等消费 | 重复 `message_id` 和终态任务不重复执行 | 重复投递与崩溃窗口测试 |
| 6 | 有限重试与死信 | 临时/永久错误分类明确，无热循环 | retry/DLQ 集成测试 |
| 7 | 首个真实 handler | `model_response` 使用统一模型服务并记录 attempt | fake Provider 端到端测试 |
| 8 | 生命周期和运维入口 | SIGTERM 停止领取、等待在途任务、关闭连接 | 进程生命周期测试 |
| 9 | 文档与真实基础设施验证 | 本地运行可复现，限制与未执行项明确 | Compose 冒烟和故障注入 |

## 测试策略

- 单元测试：消息 schema、错误分类、状态转换、退避、幂等判断、publisher/consumer adapter。
- 数据库集成：outbox 原子性、并发领取、租约过期、唯一 message ID、终态保护。
- RabbitMQ 集成：publisher confirm、mandatory return、manual ack、重投、retry、DLQ、断线重连。
- 端到端：API 创建任务，Worker 完成一个 fake Provider 的 `model_response`，最终结果可按 `run_id` 查询。
- 故障注入：在 publish 前后、业务 commit 前后和 ack 前主动终止进程，验证恢复结果。
- 真实供应商模型不作为 RabbitMQ 可靠性测试前提，避免测试费用和外部波动掩盖消息问题。

## 官方依据

- [RabbitMQ Reliability Guide](https://www.rabbitmq.com/docs/reliability)：确认至少一次投递、重复投递和消费者幂等责任。
- [Consumer Acknowledgements and Publisher Confirms](https://www.rabbitmq.com/docs/confirms)：区分 publisher confirm 与 consumer ack，并说明 nack/requeue 行为。
- [RabbitMQ Publishers](https://www.rabbitmq.com/docs/publishers)：确认 mandatory return、不可路由消息和连接恢复边界。
- [RabbitMQ Dead Letter Exchanges](https://www.rabbitmq.com/docs/dlx)：确认死信触发条件、策略配置和 dead-letter 安全限制。
- [RabbitMQ Quorum Queues](https://www.rabbitmq.com/docs/quorum-queues)：确认关键队列的数据安全、confirm 和 manual ack 建议。
- [aio-pika API Reference](https://docs.aio-pika.com/apidoc.html)：确认 robust connection/channel、publisher confirms 和 topology recovery 能力。

## 风险与停止条件

- 任务执行规格未确认时，不实现会把 `objective` 猜测为模型提示词的 Worker。
- 无法在同一 MySQL 事务写 task/outbox 时，不发布任务消息。
- 消费幂等表或等价唯一约束未实现时，不启用自动重投。
- 没有真实 RabbitMQ 集成测试环境时，可以完成单元实现，但阶段状态只能是 `PARTIAL`。
- 发现业务任务包含不可安全重试的外部副作用时，必须为该 handler 单独设计幂等键与补偿方式。

## 完成标准

- task 与 outbox 原子创建，RabbitMQ 暂时不可用不会丢任务。
- publisher confirm 丢失导致的重复消息不会产生重复模型调用或重复结果。
- Worker 只在业务事务提交后 ack；崩溃重启后任务可恢复。
- 临时失败有限重试，永久失败和超限任务进入死信并留下 MySQL 事实。
- 同一任务不能被两个 Worker 同时执行；过期租约可恢复。
- API、Worker 和发布器均能优雅关闭，不新领任务且不会把在途任务误报完成。
- 单元、数据库集成、RabbitMQ 集成、故障注入、迁移和静态检查全部通过。
- 当前文档与运行配置一致，真实 RabbitMQ 验证结果明确记录。
