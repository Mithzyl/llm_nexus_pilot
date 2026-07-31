# NexusPilot LLM Platform 总体规划

**文档日期：** 2026 年 7 月 31 日
**文档状态：** 当前总体规划（权威入口）
**当前实施焦点：** 阶段1已完成；下一步继续阶段2；阶段3仍暂停

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

## 当前事实

| 能力 | 当前状态 | 结论 |
|---|---|---|
| FastAPI、认证、SQLAlchemy、Alembic、MinIO | 已完成阶段1门禁 | 公共与内部认证、事务、查询、迁移和真实基础设施验证均通过；公共认证仍是受信调用方级 API Key |
| User 管理 | 已形成首个查询闭环 | 支持新增、详情、查询条件绑定 cursor、激活状态过滤和受限更新 |
| Run、Task 管理 | 已形成查询与动作闭环 | 支持独立分页、过滤、取消、Task 重试、并发锁顺序和 outbox 控制事实 |
| Attempt、Retry、Artifact 管理 | 已形成查询闭环 | 支持归属范围内独立分页、安全详情、物理重试顺序查询和 Artifact 受控流式读取 |
| Session、Conversation、Message | 已形成首个查询闭环 | 支持会话新增、详情、查询条件绑定 cursor、更新，以及不可变消息追加、详情和会话绑定顺序分页 |
| Tool Call、Evaluation、Outbox 查询 | 已形成内部查询闭环 | 使用独立内部密钥、查询绑定 cursor、归属校验和递归脱敏详情 |
| 多供应商 Model Gateway、Responses、SSE | 已完成 | 属于阶段2已完成部分 |
| Context、Memory、Knowledge、Prompt、Evaluation 单元 | 未实施 | Agent 之前必须补齐的 LLM 核心能力 |
| RabbitMQ、Publisher、Worker | 未实施 | 阶段3已暂停 |
| Agent、工具、MCP、OpenTelemetry、Web | 未实施 | 不能以规划或空目录视为完成 |

## 总体结构

```text
业务调用方 / 后续 Web
          │
          ▼
API 与控制平面
用户、会话、消息、运行、任务、调用、产物、审计查询
          │
          ▼
LLM 核心能力层
Model Gateway / Context / Memory / Knowledge / Prompt / Evaluation
          │
          ▼
异步执行层
Transactional Outbox / RabbitMQ / Worker / Idempotency
          │
          ▼
工具与 Agent 层
Tool Runtime / Code Search / MCP / Agent Workflow
          │
          ▼
MySQL + MinIO + OpenTelemetry
```

Agent 只组合稳定的基础单元，不自行重复实现会话历史、记忆检索、上下文裁剪、知识读取或模型调用。

## 阶段路线图

| 阶段 | 交付目标 | 当前状态 | 完成门禁 |
|---|---|---|---|
| 阶段1：数据与控制平面 | 数据库事务、User、Session、Message、Run、Task、Attempt、Artifact、内部审计和完整查询 | 已完成 | 已通过完整 API、迁移、MySQL、MinIO、事务、分页、受信调用方权限边界和脱敏门禁 |
| 阶段2：LLM 核心能力 | Model Gateway、Conversation Context、Memory、Knowledge、Prompt/模型能力目录、Evaluation/Guardrail | 进行中 | 所有能力均可脱离 Agent 单独调用、测试和观测 |
| 阶段3：异步任务执行 | Transactional outbox、RabbitMQ、Worker、幂等、重试、死信和恢复 | 暂停 | 阶段1事务底座完成；任务执行规格确认；故障窗口验证通过 |
| 阶段4：工具能力 | 工具契约、权限、文件、搜索、Shell、Git 和调用证据 | 未开始 | 禁止目录与高风险操作无法绕过；所有调用可审计 |
| 阶段5：Agent Runtime 与工作流 | 单 Agent 循环、总控与工作模型、并行只读任务、写任务隔离、独立审核 | 未开始 | 只组合已完成单元；上下文完整；循环和成本有边界 |
| 阶段6：代码搜索增强 | 文件索引、ripgrep、Tree-sitter，按需 LSP | 未开始 | 结果包含稳定文件位置、定义引用和可复核证据 |
| 阶段7：MCP Client | 连接、能力发现、工具、资源、认证、超时和权限 | 未开始 | 外部调用受控且完整记录，不绕过工具权限层 |
| 阶段8：可观测性 | API、模型、消息、Worker 和工具链路关联 | 未开始 | 不记录密钥或完整敏感内容；MySQL 仍是事实源 |
| 阶段9：应用入口 | 按实际需求建设 Web 或接入其他业务应用 | 未开始 | 核心 API 和异步状态查询稳定后再规划 |

## 阶段1与阶段2的关系

- 阶段1解决“数据是否可管理、可查询、可组合事务、可追溯”。
- 阶段2解决“LLM 能力是否可以作为独立模块使用”。
- 阶段3以后解决“这些稳定单元如何可靠异步执行与组合”。
- Memory 不是 Agent 工作流中的一个临时步骤，而是阶段2的独立能力；它复用阶段1的 User、Session、Message 和事务底座，但由阶段2自行定义版本、逻辑删除和保留策略。
- 阶段1公共认证是受信调用方级 API Key；任何核心资源直接面向最终用户前，必须另行完成可验证的用户认证授权。

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
10. 未通过阶段门禁时不开始下一阶段，不以空实现或预留表声称完成。

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
- 阶段2进行中：Model Gateway 已完成，但 Context、Memory、Knowledge、Prompt 和 Evaluation 单元尚未实现；具体契约、失败恢复和测试门禁已经固化。
- 阶段3暂停，不继续 RabbitMQ 或 Worker 开发。
- 阶段1详见 [`phase-1-foundation.md`](../implementation/phase-1-foundation.md)。
- 阶段2详见 [`phase-2-llm-core-capabilities.md`](../implementation/phase-2-llm-core-capabilities.md)。
