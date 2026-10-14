# 阶段10：RabbitMQ 任务执行需求讨论与功能规划

**文档日期：** 2026 年 10 月 14 日
**文档状态：** 未开始（本次统一规划；RabbitMQ、Publisher、Worker、领取租约和恢复代码尚未实现）
**总体规划：** [开发总计划](../architecture/platform-roadmap.md)

> 历史编号：2026 年 8 月 19 日本能力由原阶段3调整为阶段10。2026 年 9 月 2 日因 Workflow 需要脱离浏览器连接继续执行而恢复规划；阶段编号不是其他能力的开发前置顺序。本次不启动 Broker、不迁移数据库、不部署服务器。

## 与统一开发计划的关系

本文只定义共用后台执行和恢复。Controller、Worker、程序 Handoff、确定性验证、Reviewer、最终汇总的业务职责及两轮规则统一见[阶段3当前规则](phase-3-agent-workflow-plan.md#当前节点职责版本与修正规则)；工具权限、检查点内容和工具任务验收见[工具主计划](phase-7-tool-runtime-plan.md#产品展示与独立执行验收)。

基础工具循环可以同步、有界执行；承诺刷新／断线后继续、API 重启续跑或长时间自主执行前，必须完成本文及工具联合验收。普通快速回复异步化、独立文件解析任务、上传补偿和临时对象清理为后续执行类型，不随首版自动启用；Memory 后台形成及通用孤儿对象扫描仍暂停。

## 当前事实与首版范围

- 当前 Workflow 仍在 HTTP／POST SSE 请求中执行；查看历史证据的前端状态已分开，事件提交也不依赖观察者，但没有跨进程领取和恢复。
- 现有 Workflow、Node、Task、Attempt、Tool Call、Event、Message 保存业务事实；Task／Outbox 的预留字段不是可运行队列。
- 不用 FastAPI BackgroundTasks 或进程内任务集合代替可靠执行。
- 首版由后台执行进程领取**整个 Workflow**。Controller 创建的内部 Task 仅由该 Workflow 协调器分派，不写独立 `task.ready` 消息，也不由另一个通用 Task Worker 领取。
- 后续若增加独立 Task handler，须单独定义所属类型、状态、执行规格和验收，不改变首版内部 Task 的执行所有权，不把“依赖完成”直接等同于“自动入队”。
- 计划已统一，运行实现未开始。实际代码中的“工作 Agent”和本阶段的“后台 Worker 进程”是不同概念，不能互相替代。

## 目标与执行决定

| 对象 | 首版规划 | 完成条件 |
|---|---|---|
| 执行单位 | 一个版本固定的 Agent Workflow | 消息定位 Workflow，内部节点复用现有协调器 |
| 创建 | 同一 MySQL 事务保存 Workflow、初始 Event、`agent_workflow.ready` Outbox | 任一写入失败整体回滚；提交成功后才能返回接受结果 |
| 执行所有者 | 带租约和递增领取版本的单个后台 Worker | 陈旧执行者不能继续提交状态或调度新模型调用 |
| 观察 | HTTP／SSE／GET Result 只创建或读取事实 | 断开、刷新、关闭证据面板不取消执行 |
| 取消 | 显式请求持久化取消事实，执行者在安全点停止后续步骤 | 不声称已经撤销外部 Provider 费用 |
| 重复投递 | 至少一次投递，按消息及 Workflow 状态去重 | 已确认步骤不重复执行，终态结果可重读 |
| 模型未知结果 | 保存 `outcome_unknown`，停止自动重发 | 不因租约过期推断 Provider 没有执行 |
| 凭据 | 读取部署配置引用，使用真实 Run owner 检查授权 | 消息／执行配置不存 secret；不依赖暂停中的用户 Provider 凭据 |

## Agent Workflow 独立执行合同

- 创建前校验真实用户、Run、消息锚点、角色绑定、版本和来源权限；幂等重复返回同一 Workflow，不新建第二个 Outbox 执行意图。
- Publisher 只传定位字段；Worker 从数据库读取执行配置、预算、取消事实和 Workflow → Run → User 关系。服务身份只允许领取任务，不授予对任意用户文件的访问。
- 领取时使用数据库条件更新；每次成功领取增加执行所有权版本。续租、检查点写入、节点推进及发起新外部调用前都检查当前所有者与版本。失去租约的进程停止推进，不允许用迟到响应覆盖新所有者事实。
- 一次领取可以包含多个业务轮次；Controller refinement 不重新投递整个 Workflow，也不重置次数、总时间或费用预算。队列投递次数不计为模型调用，后台领取次数也不计为业务轮次。
- 同一 Session 的活动 Run 防重在服务端事务中处理，不能只依赖浏览器锁。默认拒绝另一活动轮次；未来显式并行需独立定义。
- 已完成节点及最终 Message 不重新生成；恢复前核对版本化配置、节点、Attempt、Tool Call、Handoff、Evaluation 和事件，不能仅凭最后一条文案判断进度。
- 未知外部结果保持可查询，不把消息重投当作用户授权重新计费。人工处理接口若尚未实现，未知状态保持停止，不假造批准或自动恢复。

## 状态机合同

复用现有 `AgentWorkflowStatus`：

```text
pending → running → completed | failed | cancelled | outcome_unknown
```

领取、续租及消息投递是执行所有权事实，不新增一套替代业务结果的 Worker 状态。`waiting_for_input` 继续保留现有枚举，但首版不因此启用通用人工恢复；新增恢复动作须定义身份、版本校验、幂等及未知结果处理。

- 显式取消使用持久取消事实；不把不存在于当前 Workflow 枚举的 `cancel_requested` 写成已实现状态。具体字段由实施时迁移定义，并由现有取消语义兼容。
- 租约过期不直接把 Workflow 改为失败，也不重跑全部节点。先停止陈旧执行者推进，再按下表判断是否可继续。
- 当前外层节点业务状态及 Reviewer 拒绝处理仍由阶段3决定，队列不将审核意见改成基础设施重试。

### 检查点与恢复决定

| 已保存事实／故障窗口 | 恢复行为 |
|---|---|
| Workflow 与 Outbox 已提交，未发布 | 发布原事件，不创建第二个 Workflow |
| Broker 收到消息但 confirm 丢失 | 可重发同一消息，消费端重新检查执行所有权及终态 |
| 尚未准备外部调用，节点未执行 | 可领取并从该未执行步骤继续，保留历史调用及总预算 |
| 已保存外部调用派发意图，响应未确认 | 保守进入 `outcome_unknown`，即使进程可能尚未真正发出请求也不盲目重发 |
| Provider 响应和 Attempt 已确认，Node 尚未提交 | 从保存的响应继续校验／持久化；不得重新调用模型。材料不足或损坏时明确停止 |
| 工具结果已提交，模型尚未续接 | 检查权限、固定文件版本和完整工具历史后续接；不重做已完成读取。副作用工具须有自己的恢复证明 |
| Handoff／Evaluation／Node 已完成 | 复用原记录，从后继步骤继续；不能覆盖首轮结果或重计修正费用 |
| 最终 Message 与完成事实已提交，ack 丢失 | 读取终态后确认消息，不生成第二条回答 |
| 租约过期但外部调用可能仍在运行 | 停止自动调用；租约／领取版本不能取消已发送的外部请求，不能据此承诺恰好一次计费 |
| 文件撤权、删除、用户停用、快照损坏 | 拒绝下一次读取／模型调用并保存原因，不使用旧摘要绕过 |
| 总时限或预算已耗尽 | 按阶段3规则结束，不因 Worker 重启获得新额度或新时间 |

发布失败与业务调用失败分开：Broker 连接、发布确认可有限重试；模型或写工具只有确认未产生外部作用、或有经过验证的幂等协议时才允许自动重试。未知结果不会因为被称为“网络错误”就自动重发。

## 消息契约

首版只接受 `agent_workflow.ready`：

```json
{
  "schema_version": 1,
  "message_id": "outbox event UUID",
  "event_type": "agent_workflow.ready",
  "workflow_execution_id": "workflow UUID",
  "run_id": "run UUID",
  "created_at": "RFC 3339 timestamp"
}
```

- `message_id` 唯一；业务状态、领取次数和所有权版本从数据库读取，不信任消息中自报的执行尝试。
- 未知版本、事件或缺失 ID 视为非法消息，有限记录失败后进入死信，不执行模型。
- 消息不含完整提示词、文件、模型结果、凭据引用、环境变量名或命令。重读 Run 与 Workflow 关系，拒绝不一致 ID。
- 执行配置只保留不含 secret 的 `credential_reference`，首版使用 `source=deployment_config`。用户凭据暂停期间不实现解密依赖；后续启用时再增加来源类型及归属检查，规则见阶段5。
- `task.ready` 与通用 `execution_spec_json` 留作后续独立 Task 类型的设计，不是首版创建 Workflow 的前置。

## 拟议数据调整

以下均为待实施设计，不表示迁移或字段已经存在。

| 对象 | 首版拟增加／复用的内容 | 约束 |
|---|---|---|
| 现有 Workflow 执行记录 | 领取者、租约到期时间、递增领取版本、持久取消事实；复用固定请求配置和起始时间 | 迁移时核对现有字段，避免重复；所有推进写入带所有权条件 |
| Node／Attempt／Tool Call | 复用已保存输入输出，补足派发意图、结果确认与可恢复检查点 | 业务修正不覆盖历史；未知外部作用不得猜测成功或重发 |
| `llm_outbox_events` | 发布锁、锁到期、最后错误、确认时间；复用 Workflow 聚合对象关联 | 发布 confirm 后才标记成功，过期发布锁可以重新领取 |
| 拟新增 `llm_message_deliveries` | message_id 唯一、workflow_execution_id、worker_id、投递计数、处理状态、首收／完成时间、最后错误 | 首版关联 Workflow，不要求存在外层 task_id；仅记录投递，不能代替 Workflow 业务结果 |
| `llm_tasks` | 保留内部任务用途，首版不为这些任务增加独立自动领取行为 | 后续独立 Task handler 再规划执行规格及其租约迁移 |

新增唯一约束、回填和迁移回退须有独立 MySQL 测试。外部模型调用不包在数据库事务中；最终消息与完成事实沿用既有原子提交。无法确认外部结果时保留 Attempt，不删除记录来“回滚调用”。

## 队列拓扑提案

```text
nexuspilot.tasks exchange
  └── agent_workflow.ready
        └── nexuspilot.workflows.execute
              ├── 已确认可安全重试 → 有限延迟队列
              └── 非法消息／永久失败 → nexuspilot.workflows.dead
```

采用持久消息、publisher confirms、mandatory return 和 manual ack；关键队列评估 quorum queue。队列名、虚拟主机、延迟和 prefetch 来自配置，部署时确认，不在本次启动。Outbox 发布状态仍是 pending → publishing → published；临时失败记录 next_retry_at，永久或超限失败保存错误。

重复消息遇到有效领取者时不能创建第二个执行者；须有可靠恢复器负责过期租约重新检查与调度，不能依赖原消息一定仍未 ack。最终结果／已安排的恢复事实提交前不能确认消费成功。恢复调度必须与 Workflow 所有权版本关联，旧消息不重置预算。

## 实施步骤与验收

| 步骤 | 修改及验收 |
|---|---|
| 1 | 先定义消息、Workflow 领取、取消与恢复测试；核对持久执行配置能够重建实际请求，尤其工具最终轮和修正轮的消息。阶段2有效请求保存缺口修复前，不能宣称工具节点可完整恢复 |
| 2 | 在现有业务模块和必要 Worker 入口实现领取、续租、派发意图、所有权版本与迁移；真实 MySQL 并发测试证明旧执行者不能推进。确有共用消费者时再提取包，不预建第二套任务编排 |
| 3 | Workflow／Event／Outbox 原子创建与幂等测试，确认内部 Task 不产生 task.ready |
| 4 | Publisher 与 RabbitMQ 集成：confirm 丢失、不可路由、连接重建、发布锁恢复 |
| 5 | 后台 Workflow handler 复用阶段3控制流及统计；从检查点恢复，不从头重放已计费调用 |
| 6 | 消费去重、安全重试、死信和恢复器；未知 Provider 结果停止，不自动换模型或重新计费 |
| 7 | Web 重连和刷新只观察原 Workflow；成功／失败／未知结果均可恢复，查看历史证据不改变当前执行 |
| 8 | 真实 MySQL／MinIO／RabbitMQ 故障注入、迁移及进程关闭验证；Provider 可用受控替身保证分支重复性，真实模型样例另记范围 |

必要故障测试包括：派发意图前后终止进程、响应已存但节点未提交、ack 丢失、过期租约与迟到结果、第二轮中断、输出修正中断、取消竞争、权限撤销以及工具历史续接。所有测试检查实际调用数、消息数、两轮节点／Task 关联和统计未重复。

初次测试使用专用 User／Run 和独立服务测试 Key，不增加登录后门。目标生产启用另需明确授权及相应版本验收，不把本地容器结果当作线上已通过。

## 风险与停止条件

- 无法原子保存 Workflow／Outbox，或缺少领取去重与所有权检查时，不启用消费。
- 不能区分派发意图与已确认响应时，按未知结果停止，不凭异常名称选择重试。
- 缺失工具历史、私有续接或版本化请求时，不冒充可恢复；先补存储和校验。
- 运行记录或队列需要携带 secret、环境变量名、任意服务器路径时，停止并改为受控引用。
- 缺少真实 RabbitMQ／MySQL 故障测试时可以完成局部实现，但阶段保持进行中，不报告后台可靠执行完成。

## 完成标准

- Workflow 创建原子且幂等，内部 Task 不被另一执行器重复领取。
- 断线、刷新、查看旧证据不影响后台任务；同一 Workflow 的已确认模型调用和最终消息不重复生成。
- 重启按检查点恢复；派发后未知结果明确停止，陈旧执行者不能继续推进。
- 取消、权限撤销、预算、总时限及统计在恢复后仍成立；业务返工不是消息重试。
- 单元、真实数据库、Broker、对象存储、迁移、故障注入和浏览器观察测试按上述范围通过，未执行项单列。
- 当前规则、运行配置和版本化验证记录一致；没有以历史单轮成功代替新版两轮／恢复验收。

## 官方依据

以下保留原规划参考资料，本次未重新进行外部产品资料核验；实施时按项目锁定版本核对。

- [RabbitMQ Reliability Guide](https://www.rabbitmq.com/docs/reliability)
- [Consumer Acknowledgements and Publisher Confirms](https://www.rabbitmq.com/docs/confirms)
- [RabbitMQ Publishers](https://www.rabbitmq.com/docs/publishers)
- [RabbitMQ Dead Letter Exchanges](https://www.rabbitmq.com/docs/dlx)
- [RabbitMQ Quorum Queues](https://www.rabbitmq.com/docs/quorum-queues)
- [aio-pika API Reference](https://docs.aio-pika.com/apidoc.html)
