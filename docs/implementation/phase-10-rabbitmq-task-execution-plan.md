# 阶段10：RabbitMQ 任务执行需求讨论与功能规划

**文档日期：** 2026 年 10 月 24 日
**文档状态：** 进行中（稳定提交身份、独立Worker、租约及主Agent/Reviewer循环恢复已有实现和限定真实联合验收；节点切换、格式修正和委派恢复已通过确定性故障测试，新增场景真实进程验证及迁移验收待补；RabbitMQ暂不接入）
**总体规划：** [开发总计划](../architecture/platform-roadmap.md)

> 历史编号：2026 年 8 月 19 日本能力由原阶段3调整为阶段10。2026 年 9 月 2 日因 Workflow 需要脱离浏览器连接继续执行而恢复规划；阶段编号不是其他能力的开发前置顺序。本次不启动 Broker、不迁移数据库、不部署服务器。

## 与统一开发计划的关系

### 当前实施记录（2026-10-24）

当前优先顺序由路线图维护：项目/文件工作区、必要MCP/联网和动态工具提高优先级，本项
验收持续推进；不要求其他能力编码等待RabbitMQ。长时间无人值守任务仍须完成适用恢复验收。
不提交、不推送、不部署、不自动删除、不增加付费模型调用，保留文件保留/预览实现。

修改前：`llm_conversation_turns`已按Session和幂等键保存Run/Message映射，但前端只在当前
函数内重用随机键；刷新丢失Session、Turn、Workflow三个创建步骤的提交身份。本批将Agent
原输入、附件ID、角色模型/审核设置和三个稳定键在创建请求前保存到按用户区分的localStorage。
存储失败不发送创建请求；刷新使用原键和原配置确认原提交，终态确认才清除记录。快速回复
不在本批跨刷新提交恢复范围。浏览器记录包含原问题正文，不包含服务端凭据。每个提交使用
独立存储键，避免不同会话同时修改列表造成覆盖；兼容读取之前的列表记录。交错写入单元测试
已复现并修复该问题，多标签页真实浏览器联合恢复仍需补验。
现有`task_execution_checkpoint.v1`明确`automatic_resume_supported=false`，只是观察记录，
不得将其直接宣称为Worker可恢复检查点。

已增加只读`GET /api/v1/sessions/{session_id}/turns/{submission_key}`，复用原映射，查询不写入
Run/Message、不发模型请求。注册在现有用户资源权限表，前端代理继续检查Session归属；
客户端新增只读调用函数，不将404或网络错误转换成新提交。此接口需要已有Session ID，
页面自动恢复使用同键POST确认原Session/Turn；只读发现接口保留供已有Session ID的调用方使用。
SessionCreate新增可选idempotency_key，服务端按用户锁定并核对请求内容，原响应丢失后重试
返回原Session；同键不同内容拒绝409。已有无键创建方式保持兼容。

新增迁移0027保存Session创建键/请求摘要，0028保存Workflow执行方式、所有者、领取版本和
租约到期时间。现有记录默认inline，不领取历史API进程任务；项目工作区真实验证期间本地库
已完成0026→0029升级，含0027/0028，历史81条Workflow仍inline，未启用独立Worker或RabbitMQ。
worker模式原子保存Workflow、初始Event和agent_workflow.ready Outbox；独立命令
`PYTHONPATH=src .venv/bin/python -m nexuspilot_api.commands.workflow_worker`轮询Outbox，
复用现有执行器。默认仍inline，未启动该Worker，不能直接在已有付费Provider配置下试运行。
领取和续租使用数据库时间及行锁；每次执行提交检查所有者/版本/租约，陈旧提交被拒绝。
MinIO上传后丢失租约可能留下未引用对象，不代表有自动清理，本批不删除对象。

恢复范围：未开始节点的已受理任务可领取；最终答案节点已提交且完成节点尚未开始时可复用
原答案收尾，不调用模型。新增主Agent/Reviewer工具循环恢复见下节；其他没有对应恢复路径
的执行位置明确停止，结果未知不自动重发。`task_execution_checkpoint.v1`仍是观察记录，
不改变其含义，也不直接将旧快照当作可恢复状态。

此前批次证据（本批新增真实联合验收见下一节）：真实MySQL隔离临时库运行
`pytest -q tests/test_workflow_worker_ownership.py tests/test_conversation_turn_idempotency.py --tb=short`
最新17项通过（56.73秒，含最终节点恢复、工具未知结果），模型和对象存储均为测试替身。
随后增加审核修正轮次和消费者停止检查，使用同一MySQL配置执行
`pytest -q tests/test_workflow_worker_ownership.py -k 'recovers_saved or consumer_survives or claim_race' --tb=short`，
4项通过（11.95秒），其余5项按本次筛选未执行，不计入新增通过数。
浏览器`npx playwright test e2e/web-shell.spec.ts --grep 'queued Agent acknowledgement|recovers lost'`
桌面/手机共6项通过（最新15.5秒）：创建响应丢失重用原键、Worker有限受理回放转为结果轮询。
这是实际浏览器加模拟HTTP，不是真实Worker/MinIO两端联合验收。最终节点恢复另有故障注入
测试，模拟提交后进程退出，再由新领取版本收尾；当时真实进程重启尚未验证。单个任务取消不退出
消费者，显式取消消费者仍向下传递停止；该区分有独立回归测试。SQLite完整相关组合测试
`pytest -q tests/test_workflow_worker_ownership.py tests/test_conversation_turn_idempotency.py tests/test_session_creation_migration.py tests/test_workflow_execution_tasks.py tests/test_general_agent.py tests/test_task_execution_checkpoint.py --tb=short`
63项通过、2项仅真实MySQL执行的检查跳过（最新17.43秒）。包含审核修正后从第二轮最终节点
恢复，完成节点保留原轮次，不重置为第一轮。前端`npm test`177项通过，TypeScript及相关
ESLint通过；`pending-submissions.test.ts`最终3项复验通过。Python产物/保留回归23项通过、
2项Docker/MinIO显式启用测试跳过；Unix socket停止测试普通沙箱绑定失败，经允许本地socket
的执行环境复验通过。本批未运行真实Python容器及MinIO验证。
迁移0027/0028仅执行隔离SQLite
升级/回退检查，未验证真实MySQL上的Alembic升级，不能用metadata建表测试代替该项。

下一动作：完成迁移验收和扩展恢复场景的真实进程验证；多标签页真实浏览器联合故障矩阵
仍待补。节点切换、Reviewer格式修正及委派恢复进度见下文。RabbitMQ/Publisher暂不接入，
不新增付费模型调用。
在独立Worker和浏览器适用故障验收完成前，不开放长时间无人值守任务；不阻塞项目和工具
的本地开发及各自限定范围验收。

### 工具循环中途恢复与真实本地联合验证（2026-10-24）

本批不改变默认inline配置、不重启业务API、不应用业务库迁移，不提交/推送/部署。
新增`agent_loop_resume.v1`，复用`llm_memory_snapshot_objects`登记，不增加表；正文保存于
MinIO，单个JSON内容上限8MiB。检查点在模型派发前、模型返回后、工具消息完整追加后发布，
保存连续消息、调用轮次、当前响应/工具批次位置、已完成工具ID、已加载工具版本、无进展检测、
原到期时间、来源编号、候选版本、主Agent历史、Reviewer批次和已收集决定。私有续接只保存
原Attempt引用，恢复时经原Run/Task/Provider/Model作用域读取既有加密对象，不复制明文私有状态。

- 模型完成与检查点之间：Worker请求的原响应对象增加规范化`nexus_completed_response`，
  与Attempt完成状态一同持久化。恢复使用原请求键读取已完成响应，不再调用Provider。
- 工具提交与检查点之间：按原Attempt及Provider工具调用ID读取既有结果，并重新检查读取权限。
  已完成工具不再执行，消息配对及进度从恢复位置继续；requested/running/未知结果不自动重跑。
- 主Agent、Reviewer补读、返回主Agent修订及后续审核批次保留原Task/Node/来源编号；剩余时间
  根据原绝对到期时间还原，不因换Worker延长。原节点的审核策略保持不变。
- 取消、领取版本和租约仍由原执行状态服务检查，取消不被检查点覆盖。

联合命令（需显式设置测试环境变量，凭据只通过进程环境传入，不写文档）：
`pytest -q tests/test_worker_process_integration.py --tb=short -s`。
使用loopback测试MySQL的UUID临时数据库、MinIO的authorization-audit测试桶、临时API/BFF/Web
与实际Chromium。Worker是独立OS进程，注册的Provider仅返回固定测试结果，无供应商HTTP实现。
模型不是DeepSeek/OpenCode/Strata真实调用，不能算供应商联调。

2026-10-24实际结果：1项组合测试通过（15.20秒，含两个场景）。工具结果提交后测试Worker
以退出码86退出；确认进程已退出后仅将该测试Workflow租约置为已过期，新进程恢复。最终2次
模型调用、1次工具调用，正文存在，页面刷新前后均可见。第二场景在固定模型等待期间由页面
“运行过程→取消工作流”发出取消，最终cancelled、1次模型调用、0次工具调用、无最终正文。
这不是等候自然租约超时的验收；领取竞争另有MySQL测试。测试结束停止测试进程；UUID测试库
按原fixture清理，MinIO测试对象保留，没有启用产物或工作目录自动删除。

记录：[结构化联合结果](../../output/security/worker-checkpoint-joint-20261024.json)；
[恢复截图](../../output/playwright/worker-joint-resume.png)、
[取消截图](../../output/playwright/worker-joint-cancel.png)。记录只含运行ID、状态、计数及验证标记，
无真实用户正文或凭据。

本批确定性回归命令：
`pytest -q tests/test_tool_loop_recovery.py tests/test_workflow_worker_ownership.py tests/test_task_execution_checkpoint.py tests/test_general_agent.py tests/test_review_submission.py tests/test_general_delegation.py tests/test_agent_file_tools.py tests/test_response_file_tools.py tests/test_application_tool_loop.py tests/test_tool_context.py tests/test_workflow_execution_tasks.py tests/test_conversation_turn_idempotency.py tests/test_session_creation_migration.py --tb=short`。
首次157项通过、6项跳过、22项因沙箱禁止本地HTTP端口绑定而setup报错（54.67秒）；
在允许本地端口绑定的执行环境重跑`pytest -q tests/test_application_tool_loop.py --tb=short`，
22项通过（16.18秒）。6项跳过分别为不适用的审核策略组合4项、只在真实MySQL执行的
竞争检查2项（此前MySQL批次已有证据，本批未重复这些竞争检查）。没有将setup错误算作通过。
新增恢复测试还覆盖加密续接引用、原文S1/S2及补读、返回修订、第二批原生审核提交；既有
App远程工具与旧工作流/文件调用回归保留。相关Ruff、浏览器脚本语法和git diff --check通过。

### 节点切换、审核格式修正与委派恢复（2026-10-24）

当前实现复用同一轮已有Task/Node，覆盖主节点、审核节点、最终答案和完成节点之间的提交窗口。
已提交的Reviewer格式修正响应按原请求身份读取，不再次预留模型调用额度；同一节点和审核批次
只保存一份修正开始/完成事件。修订事件与完成节点计数也不因恢复重复。

委派子任务身份由平台保存的工具调用ID确定；沿用原Agent Run、Turn和Handoff。父循环和
子循环分开保存检查点；恢复时补齐父结果，不重新调用已完成的子任务模型或工具。
只有内部delegate_task允许根据这些记录恢复。其他仍在调用中且结果未知的工具不自动重试；
子任务模型响应未确认时同样进入outcome_unknown，不将重复调用当作恢复。
显式取消阻止接管，也会中断正在等待模型的子任务。

故障测试维护在`tests/test_workflow_boundary_recovery.py`及`tests/test_delegation_recovery.py`。
它们使用固定Provider、测试数据库和对象存储，以异常模拟执行者丢失后重新领取；不是付费
模型测试，也不是本次新增场景的真实操作系统进程/浏览器验收。此前真实MySQL/MinIO及浏览器
的限定联合结果仍按上节独立记录，不扩大解释为本批所有新增位置均已真实验证。

本次先复现节点切换重复记录、委派恢复失败和Reviewer修正事件重复，再修正实现。
最终执行命令（`apps/api`目录）：
`.venv/bin/pytest -q tests/test_workflow_boundary_recovery.py tests/test_delegation_recovery.py tests/test_tool_loop_recovery.py tests/test_workflow_worker_ownership.py tests/test_task_execution_checkpoint.py tests/test_general_agent.py tests/test_review_submission.py tests/test_general_delegation.py tests/test_agent_file_tools.py tests/test_response_file_tools.py tests/test_application_tool_loop.py tests/test_tool_context.py tests/test_workflow_execution_tasks.py tests/test_conversation_turn_idempotency.py tests/test_session_creation_migration.py --tb=short`。
结果：209项通过、6项跳过，81.34秒。跳过4项不适用审核组合、2项未启用的真实MySQL检查。
模拟App HTTP测试在允许绑定本地回环端口的环境执行；没有真实供应商请求。
新增30个故障场景包含节点切换8项、Reviewer修正恢复/取消2项、委派提交窗口18项、
子任务在途取消/未知结果2项。检查原模型调用数、Task/Turn/Handoff数量、终态和修正事件不重复。
相关Python文件Ruff和`git diff --check`通过。当前业务库迁移、配置启用与真实新增场景验收
未执行；未提交、推送、部署，也未启用自动删除。

仍不承诺任意位置恢复：未知外部调用、检查点缺失/损坏、旧model_only工作流中途恢复，以及
未专项验证的落盘窗口仍保留失败/未知结果，不重放副作用；阶段10仍为进行中。

### 后台执行与提交恢复联合交付要求（2026-10-24）

本项与阶段4的浏览器提交身份恢复作为一个交付结果验收，状态为进行中；当前实现及限定证据
见上文。开放长时间自主任务前，下列两部分必须同时通过：

- 服务端：独立Worker、持久执行所有权、检查点、重启恢复及有效取消；旧执行者不能继续推进。
- 浏览器：发出创建请求前保存稳定提交标识，刷新后发现并恢复原Run/Workflow，不重新创建。

提交标识不是登录凭据，也不授予读取权限；服务端必须按当前用户和请求内容核对同一次提交。
创建已提交但响应尚未到达时，浏览器重启仍可按原提交身份查询或使用相同请求幂等确认。
接口细节在实施前核对已有提交接口和字段，不在此虚构已经存在的发现接口。查询失败或结果
未知时显示未确认并保留原身份，不自动换新标识再次创建。终态确认后才结束该次恢复记录；
再次提问必须明确视为新的用户提交。

联合验收必须覆盖：创建响应丢失后刷新、取得Run但未取得Workflow时刷新、运行中刷新、
浏览器断开期间Worker继续、API/Worker重启后恢复、重复投递与重复发现、取消与迟到结果竞争。
每项核对同一提交/Run/Workflow、已完成工具不重复执行、保存结果可读取及权限不扩大。
不能用“已有幂等键”替代跨刷新验收，也不能用“断开SSE仍执行”替代独立Worker重启恢复。

本文只定义共用后台执行和恢复。新运行机制见[阶段7通用循环](phase-7-tool-runtime-plan.md#通用-agent-工具循环目标设计)，角色及审核见[阶段3组合规则](phase-3-agent-workflow-plan.md#可组合节点职责与审核策略)。原两轮规则只用于已有版本，不是队列的通用执行限制。

### 新循环的执行所有权与检查点

状态：进行中，领取租约、循环检查点、节点切换、审核格式修正和委派恢复已编码；限定真实联合验证与扩展确定性测试分开记录，不能宣称任意位置均可恢复。本地库已在项目工作区验证期间升级至0029；未启用Worker，不启动Broker或部署。首版领取一个版本固定的Workflow执行记录，不另建同一任务的第二个执行者，不要求新循环经过所有旧节点。

- 除现有节点结果外，检查点需定位连续消息、已加载工具版本、调用意图与结果配对、工作笔记/产物引用、短来源映射、候选答案版本、审核策略/审核版本及未完成动作；完整正文在 MinIO，SQL 保存必要关联和状态。
- 恢复以最后已提交事实及固定执行版本为依据，不能只找“下一个旧节点”。已取得模型响应但尚未完成后端组装时，从原响应继续；工具结果已保存则续接模型，不重新执行；短来源编号恢复后保持稳定。
- 委派子任务仍受当前 Workflow 所有者控制，不因模型提出分工就独立写入队列；将来确需独立领取再定义父子取消、权限和归并规则。后台 Worker 与工作角色 Worker 不是同一个对象。
- 等待用户/批准需持久化原因、请求版本与恢复所需记录；恢复必须鉴权且幂等，不由浏览器重发原任务模拟恢复。新接口未实现前不可声称具备此能力。
- 审核内容版本变更时不能复用旧通过决定；取消、撤权、资源额度、未知副作用及陈旧执行者限制覆盖普通工具循环，不仅覆盖旧节点切换。

联合测试补充：无 Controller 的直接任务恢复、工具结果后恢复、任务笔记/来源映射恢复、审核等待与内容修正后恢复、同一 Run 重复领取不重复工具/计费。旧 1.0.0/1.1.0 节点恢复样例仍保留。所有执行类型的检查点可靠性必须各自验收，不能由单一旧工作流通过推断全部通用任务可恢复。

基础工具循环可以同步、有界执行。2026-10-17 阶段3新增进程持有的 POST SSE 执行任务，使观察连接断开不取消执行，规则和限定验收见阶段3“后端执行观察分离”；这不依赖 RabbitMQ，也不完成本阶段。承诺 API 重启续跑或长时间自主执行前，仍必须完成本文及工具联合验收。普通快速回复异步化、独立文件解析任务、上传补偿和临时对象清理为后续执行类型，不随首版自动启用；Memory 后台形成及通用孤儿对象扫描仍暂停。

## 当前事实与首版范围

- 默认inline方式的POST SSE由API进程持有执行任务，非流式JSON创建同步执行；查看历史证据与当前执行状态分开。新增但未启用的worker方式只保存受理记录，JSON/SSE返回已有事实，消费者独立领取；完整崩溃恢复尚未完成。
- 现有 Workflow、Node、Task、Attempt、Tool Call、Event、Message 保存业务事实；Task／Outbox 的预留字段不是可运行队列。
- 不用 FastAPI BackgroundTasks 或进程内任务集合代替可靠执行。
- 首版由后台执行进程领取**整个 Workflow**。Controller 创建的内部 Task 仅由该 Workflow 协调器分派，不写独立 `task.ready` 消息，也不由另一个通用 Task Worker 领取。
- 后续若增加独立 Task handler，须单独定义所属类型、状态、执行规格和验收，不改变首版内部 Task 的执行所有权，不把“依赖完成”直接等同于“自动入队”。
- 运行实现进行中，当前为关闭状态的数据库轮询原型，不等于RabbitMQ投递实现。实际代码中的“工作 Agent”和本阶段的“后台 Worker 进程”是不同概念。

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
| 7 | Web发送前保存提交身份，创建响应丢失或尚未取得Run/Workflow ID时刷新仍发现原任务；与独立Worker重启恢复一起验收，成功／失败／未知结果均可查询，查看历史证据不改变当前执行 |
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
- 浏览器提交身份跨刷新保留，服务端创建响应丢失也能发现原任务；与独立Worker恢复联合通过前，不开放长时间自主任务。
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
