# 阶段4：NexusPilot Web 前端规划

2026-10-14 计划修正事件：解析白名单及 TypeScript 类型增加 `agent.controller.plan_repair.requested/completed/skipped`，保持现有事件保存方式，不新增页面或统计面板。定向 `agent-workflow.test.ts` 7 项及 TypeScript 检查通过；本批没有浏览器验收。2026-10-15 后端专用计划修正统计已实现，前端结果类型同步为可选字段以兼容旧 API，TypeScript 检查通过；未新增统计展示。字段含义和成功时机只维护在阶段3当前规则。

2026-10-14 工作流兼容：新 Agent 请求使用 `workflow_version=1.1.0`、`max_workflow_rounds=2`（含首轮）。事件解析器接受节点修正及返回 Controller 的请求/完成/未执行事件，按既有事件序号保存，不当成终态；历史 1.0.0 类型和记录继续可读。结果类型增加可选 `loop_statistics` 与节点轮次，不新增统计面板。两轮节点仍按 node ID 和 Task ID 关联，不能按重复的节点键覆盖旧节点。详细规则维护在[阶段3](../implementation/phase-3-agent-workflow-plan.md#当前节点职责版本与修正规则)，历史验证记录见工具主计划。

2026-10-13 前端修复（已完成本地限定验证）：最新轮失败/取消且没有保存 Assistant 时，按所属会话的服务端 Run 恢复可见状态，不写入伪造回答；历史 Agent 答案按消息 `metadata_json.source` 恢复来源入口。Composer 增加最小化/展开，保留草稿、附件和运行中停止动作，展开恢复输入焦点；完整高度仍由 ResizeObserver 驱动底部留白。仅聚焦消息输入框的普通 Enter 发送，Shift+Enter 换行；输入法组合、229 兼容键码、长按及其他修饰键不触发发送。来源结论保留原始换行，长标识可折行，审核状态/展开动作另起一行。前端 135 项测试、类型检查、ESLint、构建通过；本地浏览器验证真实历史失败恢复、草稿/附件保留、快捷键提交前校验和 390px 来源布局。详细记录见根目录 `design-qa.md`。未修改后端引用修正机制、未追加付费工作流、未部署；先前真实续聊失败结论仍有效。

2026-10-13 后续完整浏览器验证：最新本地前端/API 配合 DeepSeek、独立 MySQL/MinIO 实际执行两轮。首轮上传、Worker 引用、Reviewer 补读、最终回答与 5 条来源展示通过；续聊新文件读取与 Handoff 成功，但 Reviewer 错误组合读取调用和块 ID，服务端拒绝后未生成最终回答，完整两轮验收失败。运行期间打开历史来源不阻止服务端继续执行，但成功终态实时渲染场景尚未验证完成。刷新后失败轮消息区缺少失败占位，历史消息来源入口/执行摘要恢复也需补验修正。详情、Run ID 和后续任务只维护于[工具主计划的完整浏览器验证](../implementation/phase-7-tool-runtime-plan.md#新前端完整工作流浏览器验证2026-10-13)。以下首次界面验证记录保留其原适用范围，不代表本次完整验收通过。

2026-10-13 结论与来源界面：已实现经确认的 Agent 回复入口及运行详情双视图。结论按 task/claim 对应 Reviewer 意见，原文展开时才从受控接口读取；呈现文件名、解析器实际位置、Worker 读取/Reviewer 补读、本次分页范围与解析限制。引用有效和模型认为原文支持分别显示，不将其合并为“内容已经确认”。长审核说明可展开，原文不可用时明确报错并可重试；旧 v1 记录不伪造来源。来源视图与当前执行状态分开，切换不改路由、输入草稿或执行请求。最终答案逐句引用映射仍未实现，快速回复没有 Worker claims，不添加假来源入口。详细接口与验证维护在[工具主计划：节点结果修正与结论证据传递](../implementation/phase-7-tool-runtime-plan.md#节点结果修正与结论证据传递)。本次浏览器验证读取此前本地真实 DeepSeek 保存的 MySQL/MinIO 样例，没有新增付费调用或部署；执行中切换历史来源的完整浏览器场景本轮未重新运行。

2026-10-12 文件交互调整：删除 Composer 的“Agent 文件工具”选择。用户绑定文件后，快速回复和 Agent 均由服务端自动提供只读工具，模型决定是否调用；快速回复仍直接生成答案，不被强制切为 Agent。多轮快速回复只接收一次最终完成事件，刷新时累计调用用量，并识别已保存的最终答案，避免续聊被错误阻止。Agent 工具结果提交后显示活动，节点详情保留工具名称和状态。设计、迁移、验收和剩余任务统一见[工具主计划](../implementation/phase-7-tool-runtime-plan.md#首批附件工具实现与运行2026-10-09)。Context 抽屉仍是基础构建快照，未增加逐条工具消息展示。

## 文件上传当前实现（2026 年 9 月 16 日）

2026-10-11 缺陷修复：Office/PDF 可选格式来自需登录的 `/files/capabilities`，未配置、不可达或繁忙时不提前承诺可解析；上传前再次检查，后端独立拒绝。上传和创建新轮次成功后才清除上一轮执行事实，避免第二轮附件失败时将已保存的快速回复误判为仍在运行。草稿、附件和旧回答保留，可继续重试。桌面/移动 Playwright 四项回归通过；执行器接入和工具调用验收归[阶段7文件执行环境](../implementation/phase-7-tool-runtime-plan.md#office-执行器启动与权限)，服务器仍未更新。

浏览器文件选择已升级为正式上传：发送时先逐文件上传并解析，再原子创建带 `file_ids` 的轮次；失败保留草稿和文件选择，不发送缺失附件的提问。快速回复和 Agent 都支持，不自动切换模式。消息显示可下载附件，刷新后仍保留；“已上传文件”列表显示待上传、处理中、已保存、失败或待清理，可下载和确认删除。列表不是项目文件管理，也不提供历史附件重新绑定。

下文“文件仅本地选择／尚未上传／选文件阻止发送”是此前基线，已由本节取代。2026-09-26 后端已支持同一会话后续轮次复用之前的附件文字，并保存逐轮 Context 快照；前端无需重传。当前仍不支持项目文件、跨刷新恢复未上传的本地 File 或完整上传重试键。每文件 1 MiB、自动携带最多 10 个附件，解析 JSON 超过 55000 字节明确拒绝拆分；图片与扫描件 OCR 未开放。删除文件后新的读取明确失败，历史回答和上下文快照仍保留。接口和部署限制以[阶段2正式文件上传](../implementation/phase-2-llm-core-capabilities.md#正式文件上传2026-年-9-月-16-日)为准。

验证：121 项前端测试、TypeScript、ESLint 和生产构建通过。Playwright 在独立 MySQL/MinIO、真实 BFF/API 与 FakeProvider 下验证文件选择、上传、发送、收到回复及刷新后的附件链接；不计为真实 Provider 验收。后端同时验证所有 Agent 模型节点收到附件内容。新增数据库迁移只应用于测试库，开发环境需先迁移再启用新版页面。

实际查看桌面及 390×844 布局，文件面板在窄屏左右保留 12px；刷新后真实下载文件名为 `upload-sample.txt`。截图位于本地忽略目录 `output/playwright/upload-desktop.png`、`output/playwright/upload-mobile-final.png`，不是版本化测试夹具。快速回复页面查询不存在的 Agent 工作流返回 404，按现有行为处理，不将其计作文件上传失败。

**文档日期：** 2026 年 10 月 8 日
**文档状态：** 进行中。对话、推理、独立运行/Context 证据、代码块工具栏、正式文件上传与浏览器身份已实现；仍缺跨刷新提交 key、完整执行恢复、项目文件、完整 Evaluation 视图和代码语法高亮。2026-10-04 目标服务器自签 HTTPS、Flash 新回复和刷新保留通过基础验收，见[部署记录](../implementation/server-deployment-20261004.md)。下文带日期的测试数量与环境是历史验证范围，不代替当前全部能力验收；归档、Memory、真实 Provider 矩阵和附件专用 Agent 编排仍后置。
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)
**数据与控制平面依据：** [`phase-1-foundation.md`](../implementation/phase-1-foundation.md)
**LLM 核心能力依据：** [`phase-2-llm-core-capabilities.md`](../implementation/phase-2-llm-core-capabilities.md)
**异步执行依据：** [`phase-10-rabbitmq-task-execution-plan.md`](../implementation/phase-10-rabbitmq-task-execution-plan.md)
**身份与凭据依据：** [`phase-5-platform-identity-credentials.md`](../implementation/phase-5-platform-identity-credentials.md)
**Agent 工作流依据：** [`phase-3-agent-workflow-plan.md`](../implementation/phase-3-agent-workflow-plan.md) 及实际 Router、Schema 和测试
**UI 设计基线：** [NexusPilot 历史 Sites 预览](https://nexuspilot-phase9-preview.ryngarzheng.chatgpt.site)

> 2026 年 8 月 19 日路线图重排：本能力由原阶段9调整为阶段4，作为已实现后端能力通向稳定可用服务的当前优先阶段。

## 工具界面的交付归属

2026-10-08 起，工具调用进度/结果、批准、生成工具验证与保存，以及代码搜索/MCP 相关工具界面，作为[工具主计划](../implementation/phase-7-tool-runtime-plan.md#产品展示与独立执行验收)的工作项一起设计和验收，不再等某个后端 Phase 全部完成后单独排前端阶段。下面保留的检查点仅是模块接入索引；当前未实现的动作不得提前显示为可用。

本文仍维护对话、布局、响应式、浏览器交互及既有验证记录。跨刷新提交与后台执行应作为一个用户流程验收；事件回放不代表执行恢复。

## 目标

- 建设以对话为主要入口的 Web 应用，采用类似 ChatGPT 的清晰布局与渐进式交互，但不复制其品牌、图标、文案或视觉资产。
- 第一版只消费已经验证的 User、Session、Message、Run、Task、Provider、Responses、Model Attempt 和 Artifact API。
- 普通生成与服务器发送事件（SSE）流式生成使用同一消息界面；用户可以看到生成中、完成、失败和取消状态。
- 为当前 `model_only_v1` 提供可解释的 Agent、步骤、Handoff、审核和验证轨迹；工具执行继续等待后续合同。
- 运行状态必须来自后端事实或事件，不根据前端定时器伪造“正在思考”“正在调用工具”等状态。

## 明确不处理

- 第一版不直接消费实验性Memory或Knowledge接口，不在浏览器拼装这些来源。来源正式启用后由Context Builder统一加入模型输入；前端只展示经过脱敏的Context Build证据。
- 第一版已经实现当前后端提供的 `model_only_v1` Agent Runtime 展示与控制；不实现 RabbitMQ Worker、工具执行、MCP 或 OpenTelemetry，只为这些后续能力保留界面扩展点。
- 不把模型流中的 `response.tool_call.delta` 显示成“工具正在执行”。该事件只表示模型提出了工具调用参数；只有未来后端产生正式 Tool Call 执行事件后才能显示执行进度。
- 不在浏览器中保存平台内部 API Key、供应商 API Key、MinIO URI 或未脱敏原始供应商响应。
- 不在阶段4顺便建设项目看板、生活管理、邮件、日历或其他超级 App 功能。

## 当前后端事实与前端边界

阶段5已开始实现最终用户浏览器身份。后端默认关闭登录，`/auth/me` 返回固定本地 owner，直接显示工作台且不显示退出按钮；显式启用登录后才要求浏览器会话。Next.js 后端为前端（Backend-for-Frontend，BFF）仍保存迁移期部署级 API Key，按需转发不透明登录 Cookie；每个业务请求先解析后端选定的主体，再校验或覆盖资源归属。免登录仅供可信个人本机使用，不代表多用户权限已经完成：

```text
Browser
  ↓ same-origin request（启用登录时携带 Cookie）
Next.js Server / Backend-for-Frontend
  ↓ server-side X-API-Key + browser Cookie
FastAPI
```

浏览器不能直接持有 `X-API-Key` 或 `X-Internal-API-Key`。当前已实现登录主体、不透明 Session Cookie、精确 Origin/跨站请求伪造防护、退出、会话撤销、BFF 归属和 FastAPI 资源主体授权。目标服务器仅完成认证和部分资源隔离冒烟，完整部署矩阵仍待执行；个人 API Key 未实现，用户 Provider 凭据暂停。

阶段5的 `/auth/login`、`/auth/logout`、`/auth/me`、会话列表/撤销和密码修改已经提供稳定首版；`/me/api-keys`、`/me/provider-credentials`、找回密码与账户安全页面尚未实现。Next.js 已不再注入固定开发用户，而是从当前登录主体派生 `user_id`；部署级 API Key 要等 FastAPI 各业务路由完成主体授权迁移后才能退出最终用户请求链。

当前内部版已经确认的后端能力：

| 前端用途 | 后端资源 | 前端行为 |
|---|---|---|
| Provider/Model 选择 | `GET /api/v1/providers` | 展示服务端实际注册的 Provider 与 `models_by_provider`；列表为空表示该 Provider 允许自定义模型标识，不代表 Provider 不可用 |
| 会话列表与详情 | Session 查询接口 | 左侧栏分页加载，cursor 与筛选条件绑定 |
| 消息历史 | Session Message 接口 | 按不可变顺序读取，不在前端改写历史消息 |
| 创建运行与任务 | Run、Task 接口 | 每次需要可审计模型执行时创建对应 Run；任务信息进入详情抽屉 |
| 普通与流式生成 | `POST /api/v1/responses` | 前端传递原子轮次返回的Run和当前Message标识，由后端创建Context Build；浏览器不拼接上下文来源 |
| 推理能力与展示策略 | `GET /api/v1/providers`、Responses Reasoning 事件 | 只按服务端声明的 `raw`、`summary`、`hidden`、`none` 能力和最终公开块渲染，不按 Provider 名称判断 |
| 响应事件恢复 | `GET /api/v1/attempts/{attempt_id}/events` | 从最后确认的事件序号有限回放；重复序号忽略、缺口拒绝、最终快照替换流式临时状态 |
| 推理历史 | `GET /api/v1/attempts/{attempt_id}/reasoning-blocks`、Message `reasoning_blocks` | Chat 恢复已保存 Assistant 的公开推理块，Trajectory 查询每次模型调用的权威快照 |
| 调用详情 | Model Attempt 和 Retry 接口 | 展示 Provider、Model、状态、token、费用、时长与安全错误 |
| 生成产物 | Artifact 接口 | 展示元数据并通过受控内容接口下载，不暴露 MinIO 对象键 |
| 运行审计 | Run、Task、内部审计接口 | 仅内部开发/运维视图使用，不默认暴露给普通用户 |
| 模型 Agent 工作流 | Agent Workflow 创建、发现、结果、节点和事件回放接口 | 只在内部开发功能门禁下启用 `model_only_v1`；完整节点查询是权威事实，事件只驱动增量提示 |

Context Build和Model Catalog已有允许公开的证据展示；完整Evaluation开发视图仍待实施。证据查询不应阻塞对话阅读，也不能由前端组合成第二套模型调用链。后端 Prompt 属于 System Prompt，普通前端已移除其名称、版本、变量、正文、是否纳入及选择入口。Memory、Knowledge、Artifact和Agent来源只通过受控Context Build证据出现，未启用来源不能显示为已使用。

### 前端实施前必须处理的后端缺口

- Session下的轮次提交已新增可选客户端幂等键，匹配请求可重放原Run与User Message；Web仅在同一次提交调用内对网络/5xx不确定结果以同key再试一次。跨刷新/再次点击后的key持久化和完整模型执行恢复仍缺失，不能据此自动重发可能计费的模型请求，详见下节。
- `/responses` 不会自动追加用户或 Assistant Message。第一版 BFF 可以显式编排，但必须记录每一步结果并处理部分失败，不能把多次 HTTP 调用描述为原子事务。
- FastAPI 已通过共享主体授权依赖检查资源归属，并通过本地双向隔离检查；目标部署完整资源矩阵仍未执行。部署级服务密钥仍只能由服务端持有，个人程序调用凭据尚未实现。
- `/responses` 已使用 `conversation-stream.v2` 信封持久化公开事件，并按 Attempt 提供有限回放。浏览器收到首个事件并取得 `attempt_id` 后，断线会从最后确认的 `sequence` 继续读取；若连接在首个事件前断开，仍只能通过 Run/Attempt 最终事实恢复，不能自动重发可能计费的请求。阶段3 Agent Workflow 保留自己的事件合同，两者不能混用。
- 当前 `response.tool_call.delta` 不是工具执行事实。正式工具时间线必须等待 Tool Call API 和运行事件合同。
- 阶段3后端当前提供显式取消、最多两个无依赖工作 Agent 并行和费用预留；前端已经完成对应合同、代理和交互适配。恢复、等待用户输入、进程重启恢复和大型节点结果 Artifact 降级仍不存在，前端不能显示这些动作，也不能把有限事件回放描述为持续订阅。

### 轮次提交幂等与当前恢复范围（2026 年 9 月 12 日）

状态：轮次幂等已实现；后端测试和当前117项完整前端测试通过，localhost与127.0.0.1本地入口经真实BFF与实际FastAPI路由完成限定范围检查，后端使用独立临时SQLite、FakeProvider和内存对象存储，详见本轮验证记录。首次轮次响应丢失后使用同key重试，不重复创建Run/User Message或调用Responses；来源误拒绝及已保存快速回复刷新禁发已修复并验证。本工作项只让一次User Message/Run数据库提交能够重复确认，不启动或重试Provider，不等于完整回答已可恢复。

- `POST /api/v1/sessions/{session_id}/turns`新增可选`idempotency_key`。后端保存`llm_conversation_turns`，以Session与key的SHA-256组合确定同一次提交，记录请求哈希以及原Run/Message引用；轮次记录与Run、Message同事务提交。新建返回201，相同key和输入返回原对象及200，不同输入使用同key返回409。省略key保持旧行为，每次创建独立轮次。
- 迁移`20260912_0021`新增该表，不为历史轮次猜测补key，不改写已有Run或Message。启用新的Web提交前先运行`make api-migrate`；迁移是否已应用到具体数据库须另行核对，不能由代码存在推定。
- Web的`createConversationTurn`未收到调用方key时，每次调用生成一个新key；一次调用中的重试复用同一key和相同请求体。仅网络/5xx或无法确认响应的解析失败最多再试一次，4xx不重试；重试对象仅是轮次数据库提交，不是`/responses`或Workflow执行。
- 当前没有跨刷新或再次点击发送后的key持久化。输入草稿恢复、Web Locks和相同key重放不能替代它；如果本次有限重试仍失败，保留输入并显示未确认，不宣称没有保存，也不声称再次点击会自动恢复原轮次。
- 得到Run/Message后启动模型是另一个操作。未知Attempt的首事件前断线、创建轮次后尚未调用模型的中断、跨刷新完整执行恢复仍未解决；继续依据现有Run/Attempt/Workflow事实查询，不自动重发可能计费的请求。
- 快速回复已保存后的续聊不再仅由通用Run的`pending`状态决定。只有当前Run明确无Workflow（本次查询返回404）、完整查询中无Task且仅一个无Task的`completed` Attempt、最新User属于同Run/Session、其后的已保存Assistant也属于同Run/Session并准确引用该Attempt时，才允许在Run仍`pending`时继续发送。活动Run/Workflow、查询失败、未知或不完整事实仍阻止发送；不将模型Attempt完成自动写成后端Run完成。
- Workflow恢复读取记录请求代次，切换会话或开始新读取后忽略旧读取结果，避免旧会话结果覆盖当前状态。该改动处理静态检查发现的异步风险；修复前未稳定复现浏览器故障，不记作已复现缺陷。

## 后端阶段检查点驱动的前端交付设计

阶段4是一个持续消费后端稳定能力的前端阶段，不把每个后端阶段重新编号成新的前端阶段。后端阶段达到完成门禁，只表示对应前端工作可以开始；只有前端合同、交互、失败恢复、安全和浏览器验证也通过后，该前端检查点才算完成。

路线图重排后，本阶段优先把阶段1～3已经实现的能力建设为稳定可用的内部服务；身份、授权和可观测性完成前，不提前启用工具、代码搜索或 MCP 入口。

### 检查点使用规则

- 后端阶段状态以总体规划为准，前端不得因页面已有占位区域就声称后端能力可用。
- 后端阶段尚未完成时，对应导航和动作默认隐藏；需要内部联调时可以显示明确的“开发预览”，但不能使用模拟运行状态混入正常会话。
- 前端只消费稳定的公开查询、动作和事件合同，不读取数据库、RabbitMQ、内部对象存储地址或内部审计原始载荷拼装用户状态。
- 每个可增长历史都使用后端 cursor；每个实时状态都需要稳定事件顺序或可查询最终事实；刷新后不得依赖浏览器内存恢复最终状态。
- 后端状态枚举、允许动作、权限和错误分类是事实源。前端可以转换成人类可读文案，但不能发明第二套状态机。
- 阶段5完成前，阶段4只作为内部开发界面；任何最终用户公开发布都必须同时通过阶段5的身份、授权和凭据门禁。

### 总体映射

| 后端检查点 | 后端交付能力 | 对应前端交付 | 主要界面 | 前端启用条件 | 当前前端状态 |
|---|---|---|---|---|---|
| 阶段1：数据与控制平面 | User、Session、Message、Run、Task、Attempt、Retry、Artifact 和安全查询闭环 | 会话历史、运行证据、分页、取消/重试入口和产物元数据 | 会话侧栏、对话页、运行详情页 | 公开响应不含内部 URI；归属、cursor、允许动作和稳定错误合同通过 | 进行中 |
| 阶段2：LLM核心能力 | Provider、Responses、SSE、统一Context Builder、Prompt/Model Catalog、Evaluation | 模型选择、普通/流式生成、全部上下文来源与评估证据 | Composer、模型选择器、证据抽屉 | 终止事件、用量、错误、能力目录和Context来源证据稳定；所有来源只能通过Context Builder | 进行中 |
| 阶段3：Agent Runtime 与工作流 | 后端已完成 `model_only_v1`、完整节点合同、持久事件、最多两个无依赖工作 Agent 并行、费用预留和显式取消；工具与恢复不属于当前配置 | 展示 Agent 分工、节点结果、Handoff、验证、审核和预算证据，并适配取消与并行组 | Run 下的 Agent 时间线和工作流详情 | 八个接口、取消动作、并行组和费用预留已接入；真实 Provider 端到端验收待完成，工具另等阶段7 | 进行中 |
| 阶段4：Web 前端 | BFF、对话外壳、响应式、主题、可访问性和前端验证体系 | 承载各后端检查点的统一交互面 | `apps/web` 全部页面 | 只启用已通过对应后端检查点的能力；浏览器边界和真实状态门禁持续通过 | 进行中 |
| 阶段5：身份、授权与凭据 | 登录主体、浏览器会话、个人 API Key、供应商凭据和资源授权 | 登录/退出、会话管理、密钥与供应商凭据设置，移除固定开发用户 | 登录页、账户安全、API Key 与 Provider 凭据页 | Cookie/Bearer、跨用户隔离、跨站请求伪造防护、撤销和加密门禁全部通过 | 进行中：登录页、Cookie主体和BFF归属已完成 |
| 阶段6：可观测性 | API、模型、消息、Worker、工具和 Agent 的关联链路 | 关联标识、耗时分解、健康状态和内部诊断 | 运行诊断页、内部运维视图 | 公开/内部字段分层完成；追踪不含密钥和完整敏感内容 | 未开始 |
| 阶段7：工具能力 | 工具契约、权限、文件、搜索、Shell、Git 和调用证据 | 工具提议、批准、执行结果、失败和 Artifact 展示 | Assistant 执行过程、批准对话框、工具详情 | 工具执行事实、风险等级、批准合同和脱敏结果稳定 | 未开始 |
| 阶段8：代码搜索增强 | 文件索引、ripgrep、Tree-sitter 和可选语言服务器协议（LSP）证据 | 文件/行引用、定义与引用关系、索引新鲜度 | 代码证据抽屉、搜索结果视图 | workspace 归属、安全路径、稳定定位和有界结果合同通过 | 未开始 |
| 阶段9：MCP Client | 连接、能力发现、工具、资源、认证、超时和权限 | MCP 连接管理、能力目录、资源选择和受控调用 | MCP 设置页、能力面板、调用详情 | 连接身份、权限、超时、撤销和审计合同稳定 | 未开始 |
| 阶段10：异步任务执行 | Outbox、RabbitMQ、Worker、幂等、有限重试、死信和恢复 | 排队任务状态、Worker 执行时间线、取消、重试和刷新恢复 | 任务时间线、运行详情、失败恢复操作 | Task 状态机、允许动作、公开事件和恢复 cursor 完成并通过故障窗口测试 | 未开始 |

### 阶段1检查点：会话与运行证据

**前端范围：**

- 会话侧栏负责 Session 列表、当前会话、分页和空状态；对话页只按不可变 sequence 展示 Message，不提供历史内容编辑。
- 运行详情展示 Run、Task、Model Attempt、Provider Transport Attempt、费用、耗时、错误摘要和 Artifact 元数据；完整列表使用各自 cursor，不依赖聚合详情无限增长。
- Artifact 下载只能经过受控内容接口；浏览器响应、错误和 DOM 中不得出现 MinIO URI、原始请求 URI 或原始响应 URI。
- 取消 Run、重试 Task 等动作只在后端明确允许时出现。`cancel_requested`、`cancelled`、`failed` 等状态分别展示，不把“已请求取消”写成“已经停止”。

**失败与恢复：**

- Session 不存在或不属于当前主体时显示统一不可用状态，不区分“存在但无权限”和“不存在”。
- 消息页、Attempt、Artifact 或 Task 局部查询失败时保留已经读取的对话正文，只让对应证据区域进入失败状态。
- cursor 失效后重新读取该资源第一页，不把旧页和新查询结果混合。

**前端通过条件：**

- 会话、消息、运行详情、分页、取消和重试具有允许、拒绝、冲突和依赖失败测试。
- 浏览器安全测试确认不出现内部 URI、平台密钥、内部审计原文和未脱敏供应商错误。
- 刷新后可以从 Message、Run、Task 和 Attempt 事实恢复，不使用本地定时器补写运行状态。

### 阶段2检查点：模型调用与独立 LLM 证据

**前端范围：**

- Composer 从后端 Provider 和 Model Catalog 事实提供 Provider/Model 选择；未知或未验证能力明确禁用，不由前端静默删除参数或切换模型。
- `/responses` 的普通与服务器发送事件（SSE）模式使用同一 Assistant 消息结构；`response.started`、文本增量、用量、完成和失败分别驱动界面。
- Reasoning 使用 `reasoning.raw`、`reasoning.summary`、`reasoning.status` 可辨识联合类型；Chat 默认折叠公开文本，隐藏推理完成后不保留空状态行，Trajectory 保留不可见标记、时间和 Reasoning Token 证据。
- 服务端按模型注册能力与当前展示策略裁剪公开数据。前端不允许把摘要提升为原始推理，也不接收 `reasoning_content`、`encrypted_content`、Provider response ID 或续接状态。
- Context Build已在独立证据区域展示允许公开的来源纳入/排除、信任和策略事实，并过滤System Prompt存在性与全部元数据；Prompt Render不进入普通产品界面。完整Evaluation开发视图后续实施，必须把执行状态与verdict分开；Memory、Knowledge和Tool未启用时不能显示为已使用。
- 前端不得直接调用Memory、Knowledge、Artifact或Agent内部接口补充Prompt。尚未启用的来源不显示为已使用；启用后的来源由Context Builder策略自动选择，或使用后端提供的受控高层选项，不能接受任意资源ID注入。

**失败与恢复：**

- 供应商未配置、能力不支持、输入校验失败、超时和供应商暂不可用使用不同可操作文案，但不展示原始供应商响应。
- SSE 缺失终止事件、断线或取消时保留部分文本并标记未完成；已知 Attempt 的断线从已提交事件回放，重复事件幂等忽略，序号缺口明确失败，不自动产生第二次计费请求。
- 模型响应完成与 Assistant Message 保存是两个状态；保存失败保留内容并指向 Run/Attempt，不把模型成功改写成整体失败。

**前端通过条件：**

- 普通响应与 SSE 具有一致的完成、失败、用量和 Attempt 关联测试。
- DeepSeek 原始推理、OpenAI 摘要或隐藏状态、推理 token、完成/中断、重复/乱序/断线回放、历史消息快照和敏感续接状态隔离均有合同测试。
- Context、Model Catalog 和后续 Evaluation 视图只显示允许公开的后端证据，历史版本和未知能力行为与阶段2合同一致；普通界面不提供Prompt视图。
- 长响应按批次刷新，不能每个 token 重新解析全部 Markdown；生成期间页面交互和滚动保持可用。

#### 思考程度选择与跨 Provider 对齐

状态：前端实现与确定性浏览器验证已完成，真实 Provider 调用矩阵待后续执行。阶段2已完成稳定模型策略字段、默认中等解析、四个官方Provider映射、非法组合拒绝和Attempt证据；阶段4已实现Composer选择器、模型切换归一化以及快速回复和Agent请求透传。

后端已完成`ResponsesRequest.reasoning`、完整统一等级、逐模型策略、四家官方Provider映射、Attempt证据和Agent Workflow工作流级透传。Web从稳定模型目录读取允许等级、是否支持关闭及默认程度，并把“思考程度”和“推理展示”收在同一个Composer入口内；两项设置在界面上合并收纳，但仍分别写入`reasoning`和`reasoning_display_policy`合同。

推理展示的Web默认值按模型目录能力自动匹配：`raw`明确提交`provider-visible`并显示“显示推理”，`summary`明确提交`summary-only`并显示“显示摘要”，`hidden`和`none`保持`hidden`且不展示无效选项。用户手动选择后保留其披露偏好；切换模型时可以把原始推理偏好降为摘要，但不能把“仅摘要”偏好自动升级为原始推理。后端接口缺省值继续保持`hidden`，因此没有Web显式选择的其他客户端不会意外暴露推理内容。

统一交互采用“模型默认、关闭、最小、低、中、高、极高、最大”的候选集合，但页面只展示当前 Provider/Model 能力目录明确声明的选项：

- “模型默认”表示使用 `llm_models` 中经过验证的产品默认，不再表示 Provider 原生默认。所有已启用模型的初始默认必须是“思考已开启 + 中等”，即 `enabled=true`、`effort=medium`；前端可以省略显式覆盖，但服务端必须解析并保存这一默认事实。
- “关闭”映射为 `reasoning: {enabled: false}`，只在模型明确支持关闭时显示。不能关闭的模型不显示该选项，手工构造的非法请求由后端拒绝。
- 选择具体程度时发送 `reasoning: {enabled: true, effort: <level>}`。统一程度枚举扩展为 `minimal | low | medium | high | xhigh | max`；不把一个 Provider 不支持的程度静默向上或向下取整。
- 用户切换模型后，如果原选择不在新模型的允许集合中，Composer回到新模型的“中等”默认并给出可访问提示；如果该模型无法解析中等等级，则它不应出现在可提交模型列表中。
- 第一版为整个请求选择一个程度。快速回复直接写入 `/responses`；Agent Workflow写入顶层`reasoning`字段，由后端预检全部角色绑定并传给每个模型节点，Attempt保存实际解析程度。角色级单独调节留待有评测依据后再设计。

思考控制的产品配置存放在稳定 `llm_models`：有序 `supported_efforts`、`supports_disable`、固定启用的 `default_enabled`、默认 `medium`、逐Provider映射和稳定策略版本。Model Catalog与Provider适配器继续提供供应商能力证据；`GET /models`返回两者校验后的有效结果，并作为前端唯一事实源。前端不能根据模型名称前缀推断，也不能直接读取映射表构造Provider请求。

| Provider/模型族 | 官方原生控制 | NexusPilot 对齐规则 |
|---|---|---|
| OpenAI Responses 推理模型 | `reasoning.effort`，实际集合按模型可能包含 `none/minimal/low/medium/high/xhigh` | `none` 通过“关闭”表达，其余同名直传；每个模型只暴露目录声明的子集，不能假设所有 OpenAI 模型能力一致 |
| DeepSeek V4 | `thinking.type=enabled/disabled`；`reasoning_effort` 的原生档位和兼容行为按模型验证 | 稳定模型记录必须为产品默认 `medium` 提供显式、版本化的原生映射；只有该映射通过适配器合同和真实调用验证后才能启用，不能依赖上游静默改写 |
| Anthropic 自适应思考模型 | `thinking.type=adaptive` 与 `output_config.effort=low/medium/high/xhigh/max`，支持集合按模型变化 | 对同名等级直传；启用具体程度时同时使用模型声明的 adaptive thinking 模式。只支持旧式 `budget_tokens` 的模型不得套用类别映射，除非目录为该模型提供经过验证的显式预算表 |
| Gemini 3.x | `thinkingConfig.thinkingLevel=minimal/low/medium/high`，支持集合按模型变化 | 同名直传并按模型过滤；不支持关闭的模型不显示关闭 |
| Gemini 2.5 | 原生 `thinkingBudget`，官方兼容层给出类别到预算的模型相关映射 | 只由后端版本化能力配置转换，前端不发送 token 预算；未配置中等默认映射时不能作为可提交模型 |
| OpenAI-compatible 自定义端点 | 不可从协议名称推断 | 管理员必须为具体 Provider/Model 配置并验证至少中等默认映射后才能启用；不能把协议兼容误当成思考能力兼容 |

实施进度：`llm_models`字段与当前模型回填、统一枚举、四家官方Provider映射、Agent Workflow顶层reasoning透传、Composer选择器、模型切换归一化及快速回复/Agent请求提交均已完成。确定性浏览器测试覆盖桌面和Pixel 7外壳、稳定模型中等默认和交互布局；真实Provider浏览器矩阵仍需覆盖当前DeepSeek Flash实际声明的中等、较高程度和关闭路径，不在本次无数据库写入的界面验证中伪造完成。

官方对齐依据：[OpenAI Responses reasoning](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal/delta)、[DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)、[Anthropic Effort](https://platform.claude.com/docs/en/build-with-claude/effort)、[Anthropic Adaptive Thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)、[Gemini Thinking](https://ai.google.dev/gemini-api/docs/thinking) 与 [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)。

### 阶段10检查点：异步任务与可靠恢复

**前端范围：**

- 在同步模型调用之外增加异步 Task 提交与运行视图；界面区分“已接受”“等待 Worker”“执行中”“等待重试”“最终失败”“已取消”。
- 运行时间线消费公开任务事件或查询事实，展示任务编号、当前状态、已确认的尝试次数、下一步用户动作和公开错误摘要；不展示 RabbitMQ exchange、routing key 或消息原文。
- 用户取消只发起协作式取消请求；允许重试时调用后端受控动作，不在浏览器复制 Task 或重新提交执行规格。
- 刷新、断线和跨设备打开同一 Run 时，使用事件恢复 cursor 或 MySQL 查询事实恢复；浏览器不缓存未确认的 Worker 状态作为事实。

**后端合同门禁：**

- 必须提供稳定 Task 状态枚举、状态版本或更新时间、允许动作、最终错误分类和有界重试事实。
- 公开事件至少能够按 Run/Task 关联、按 sequence 排序并从 cursor 恢复；如果阶段10只提供查询而没有事件，前端使用有界退避刷新并明确这是查询状态，不伪装实时流。
- 死信和 Publisher 故障先转成可公开的任务失败/运维状态，浏览器不能直接消费 RabbitMQ 管理接口。

**前端通过条件：**

- 覆盖重复投递、乱序事件、Worker 崩溃、有限重试、死信、取消竞争和刷新恢复。
- 同一 Task 的重复事件不会重复追加时间线或重复触发用户通知。
- 后端阶段10未完成前，仅保留静态布局接口，不显示虚假的 Worker 进度。

### 阶段7检查点：工具调用、批准与产物

具体行为、公开字段、服务端批准要求及通过条件已归并到[工具主计划](../implementation/phase-7-tool-runtime-plan.md#产品展示与独立执行验收)。本节只保留接入位置：复用工作流活动、独立运行证据抽屉和受控 Artifact 读取；功能随对应工具工作项一同交付，而非等待全部 Phase 7 完成。

前端不得把模型参数增量显示成真实执行，也不得自行批准、提供目录权限或展示秘密。创建/测试工具的过程与普通工具调用使用同一历史活动，不覆盖旧事件。

### 阶段3检查点：Agent Runtime 与工作流

阶段3后端已经完成 `model_only_v1`。当前前端已消费完整节点结果、持久化事件、显式取消、费用预留字段和最多两个工作 Agent 的执行组，不改变阶段3后端状态；本节剩余工作是使用真实 Provider 完成端到端验收。

#### 当前可实施能力

- 在已有 `/runs/[runId]` 页面增加“Agent 工作流”页签，不创建独立的 Agent 管理页面。Run 与 Workflow 是一对一关系，先按 Run 发现 Workflow，再读取摘要、结果和节点。
- 在对话 Composer 增加受内部功能门禁保护的“Agent 工作流”提交模式；普通 `/responses` 模式保持不变，不能静默改用 Agent Workflow。
- 展示 Controller 规划、工作 Agent、Task、Agent Run、Agent Turn、Handoff、确定性验证、可选 Reviewer、最终汇总和完成节点；所有显示内容来自节点完整合同或事件公共投影。
- 展示节点累计 token、估算费用、模型调用次数、节点数、剩余模型调用、剩余节点、剩余总时长和当前保守费用预留。界面区分 `cost_used` 与 `reserved_estimated_cost`，不能把临时预留显示成已经消费。
- 工作 Agent 按依赖分组执行，同组最多两个并行。纵向时间线继续按 `node_sequence` 保持稳定顺序；详情可以用 `dispatch_groups` 表示同组关系，不能根据事件到达时间猜测依赖。
- `model_only_v1` 不展示 Implementer、Tool Call、文件修改、Shell、Git、网络调用、Memory 或 Knowledge；即使通用组件已有这些槽位，也保持隐藏。

#### 当前接口与前端用途

| 方法与路径 | 前端用途 | 关键行为 |
|---|---|---|
| `POST /api/v1/runs/{run_id}/agent-workflows` | 创建并执行 Workflow | `stream=false` 返回完整 JSON；`stream=true` 返回提交后的实时服务器发送事件（SSE）。新建返回 `201`，完全相同的幂等回放返回 `200`；HTTP 成功不等于业务一定完成 |
| `GET /api/v1/runs/{run_id}/agent-workflow` | 从 Run 发现唯一 Workflow | 创建前 `404` 表示该 Run 尚无 Agent Workflow，不应自动创建 |
| `GET /api/v1/agent-workflows/{workflow_execution_id}` | 刷新工作流摘要 | 读取当前阶段、活动节点、计数、版本、时间和安全错误 |
| `GET /api/v1/agent-workflows/{workflow_execution_id}/result` | 读取权威完整快照 | 一次返回全部有界节点、最终输出、完成汇总、聚合用量和 `snapshot_version` |
| `GET /api/v1/agent-workflows/{workflow_execution_id}/nodes` | 分页浏览节点 | cursor 与 Workflow 绑定；`limit` 默认 `50`、范围 `1`～`100` |
| `GET /api/v1/agent-workflows/{workflow_execution_id}/nodes/{node_execution_id}` | 按事件引用读取单节点详情 | 必须同时校验 Node 属于路径中的 Workflow |
| `GET /api/v1/agent-workflows/{workflow_execution_id}/events` | 按 `after_sequence` 或 `Last-Event-ID` 回放已提交事件 | 当前是有限回放，不持续等待新事件；非法游标明确失败 |
| `POST /api/v1/agent-workflows/{workflow_execution_id}/cancel` | 显式停止后续节点 | 重复取消返回同一 `cancelled` 摘要；已进入供应商边界的调用会先完成事实收敛，不能写成网络请求一定被撤回 |

当前没有 `/resume`，也没有提交等待用户输入的接口。前端“取消工作流”调用独立 `/cancel`，可以跨刷新读取取消终态；单纯关闭 POST SSE 仍只表示中断当前传输，不能替代显式取消。

#### 创建请求设计

- Agent Workflow 请求本身不包含用户目标；后端从所属 Run 的 `user_request` 读取目标。因此 Agent 提交模式必须先得到包含当前显式输入的 `run_id`，不能把 Composer 文本临时附加到 Workflow 请求或复用目标不一致的旧 Run。
- 当前 Message/Run 轮次创建与 Workflow POST 不是一个端到端幂等事务。轮次创建可用同key重放原Run/Message；当前Web仅在同一次`createConversationTurn`调用内有限重试一次。浏览器拿到确定`run_id`后，才可依赖Workflow自身的`idempotency_key`查询或回放；轮次重放不代表Workflow已经启动，也不恢复丢失的模型请求身份。
- Run 带有 `session_id` 时，阶段3后端会把最终 Assistant Message 与 `final_synthesis` 节点完成事实放在同一事务提交。Agent 模式不得沿用普通 `/responses` 流程再次保存 Assistant Message；前端在完成后读取 Result 和 Session Message 事实。
- 新 Agent 请求使用 `model_only`／`1.1.0`，`max_workflow_rounds=2` 包含首轮；执行配置按服务端附件授权规则选择，历史 `1.0.0` 仍可读取。版本、节点职责和修正条件只引用[阶段3当前规则](../implementation/phase-3-agent-workflow-plan.md#当前节点职责版本与修正规则)，不允许任意工作流名称或版本文本输入。
- 每次用户明确提交生成一个 `8`～`128` 字符的 `idempotency_key`，并在该提交恢复完成前持久保存在当前页面状态中。请求超时或断线后先发现原 Workflow；不能生成新 key 自动重试模型调用。
- `role_bindings` 只允许 `controller`、`planner`、`researcher`、`reviewer`、`verifier`。必须包含 Controller 和至少一个 Planner/Researcher；`review_policy` 不是 `never` 时必须包含 Reviewer。Verifier 当前不会创建模型节点，应隐藏在默认表单，只在开发者合同测试中保留类型支持。
- 每个角色绑定展示 Provider、Model、结构化输出模式、超时和最大输出 token。Provider/Model 只能来自阶段2已经验证的能力事实；不支持的结构化输出模式必须在提交前禁用并说明原因。
- `review_policy` 支持 `always`、`on_verification_failure`、`never`。`max_nodes` 按审核策略最小 10 或 11、最大 32；模型调用额度按阶段3的版本／配置规则校验（最小 3、4 或 6，最大 16），前端不能以旧单轮额度保证两轮成功；总时限 1,000～600,000 毫秒，并行 Agent 数 1～2、默认 1。
- 表单使用与后端相同的范围和角色依赖校验提供即时提示，但服务端 `422` 仍是权威结果；校验失败保留用户目标、角色绑定和高级设置。

#### 状态、事件与恢复模型

- Workflow 状态类型完整保留 `pending`、`running`、`waiting_for_input`、`completed`、`failed`、`cancelled`、`outcome_unknown`。当前执行路径不会进入 `waiting_for_input`，遇到该值时只能作为未知的新后端能力安全展示，不出现输入框或恢复按钮。
- Node 状态类型完整保留 `pending`、`running`、`completed`、`skipped`、`blocked`、`failed`、`cancelled`、`outcome_unknown`。当前事件不会产生 `skipped` 和 `blocked`；前端不得据此虚构跳过原因。
- 当前事件只接受：`agent.workflow.started|completed|failed|cancelled|outcome_unknown` 和 `agent.node.started|completed|failed|cancelled|outcome_unknown`。`waiting_for_input`、`skipped`、`blocked`、`agent.budget.updated` 只作为后续合同预留。
- `public_payload` 虽然在 OpenAPI 中是对象，前端仍按 `event_type` 做窄化校验：Workflow started 读取 `execution_profile`；Node started 读取节点 ID/key/type/role；Node completed 读取 output 类型、Schema 版本和 `node_result_path`；Node 失败/取消/未知结果读取安全错误与节点详情路径；Workflow completed 读取完成节点 ID 和 `result_path`；Workflow failed/outcome unknown 只读取安全错误；Workflow cancelled 不假设额外字段。
- 事件按 `workflow_execution_id + event_sequence` 去重，`event_sequence` 必须严格递增。收到重复事件不重复追加；发现缺口时暂停“实时”标识，使用最后确认序号调用有限回放，再以 Summary/Result 收敛。
- `agent.node.completed`、失败和未知结果事件只使用 `node_execution_id` 与 `node_result_path` 定位详情；不能把 `public_summary` 解析成状态或完整结果。`agent.workflow.completed` 使用 `result_path` 读取最终快照。
- `version` 用于 Workflow 状态版本，`snapshot_version` 用于 Result 快照一致性。前端缓存按 `workflow_execution_id` 隔离，只接受不低于当前版本的摘要/结果；不得把不同快照的节点数组手工拼成“最新结果”。
- 初始 POST SSE 断开后，先通过 Run 发现接口确认 Workflow，再回放 `lastEventSequence` 之后已经提交的事件并读取 Summary。若仍为运行中，只能以有界退避查询 Summary 和有限事件回放，并明确显示“查询恢复中”，不能把 GET `/events` 当成长连接。
- 刷新后以 Run 发现、Workflow Summary 和 Result 为事实恢复。浏览器内的事件列表只是投影缓存，不能覆盖后端终态。
- 点击某条 Assistant Message 的“查看运行证据”时，必须使用该 Message 自身持久化的 `run_id` 加载独立只读快照。该动作只开关证据抽屉，不改变会话路由、当前 Run、正在接收的 Workflow 事件、待生成 Assistant 投影或取消目标。
- 当前 Session 的最新 Run 或 Workflow 仍为活动状态时，刷新恢复必须补回待完成 Assistant 投影并禁止再次提交；浏览器锁和标签页广播只是体验优化，服务端活动状态才是恢复后的事实。跨浏览器/跨设备的最终并发拒绝仍需后台执行阶段增加服务端条件约束。

#### 完整节点结果展示

每个节点先使用统一信封渲染：`public_view`、状态、输入来源引用、转移结果、证据 ID、用量、剩余预算、时间、警告和安全错误。节点特定输出按 `output_type` 使用穷尽类型分支，未知 `output_type` 显示“当前前端不支持此合同版本”并保留通用信封，不能猜测字段。

节点没有输出时按持久化状态说明原因：等待或运行表示尚未提交，失败、取消、阻塞和结果未知分别显示对应终态，只有 `completed` 且 `output=null` 才提示输出事实缺失，不能把所有情况统一描述为“该节点没有已提交输出”。

| `output_type` | 默认展示 |
|---|---|
| `request_intake` | 规范化目标、任务复杂度、约束、验收条件、假设与不可用能力 |
| `context_assembly` | 纳入的 Message/Artifact/Handoff 引用、排除原因、字符数和是否截断 |
| `controller_plan` | 决策摘要、Task、依赖关系、角色、完成条件、风险和未知项 |
| `plan_validation` | 校验结论、检查项、拓扑顺序、能力缺口、依赖环和拒绝原因 |
| `agent_dispatch` | 已创建 Task、Agent Run、依赖执行组、组并发上限、阻塞/跳过条目和角色模型绑定 |
| `agent_model_execution` | Agent Turn、Model Attempt、Provider/Model、用量、时长、完成原因和结构化工作摘要 |
| `agent_handoff` | Handoff 状态、事实、决定、剩余工作、风险、未知项和后续不变量；不把它称为内部 Working Memory |
| `deterministic_verification` | 程序化 verdict、每项检查、阻塞/非阻塞发现和证据引用 |
| `independent_review` | Reviewer verdict、分数、发现、接受/拒绝主张、缺失证据和 Evaluation 引用 |
| `final_synthesis` | 最终正文、已完成目标、未解决项、警告、后续动作和来源引用 |
| `workflow_completion` | Workflow 汇总、节点/Task/Agent/Attempt/Evaluation/Handoff ID、总用量、费用、耗时和剩余调用 |

节点输出当前受 `64 KiB` JSON 上限约束，超限会失败，尚无 Artifact 自动降级。前端不截断后再标成“完整结果”；视觉折叠只影响显示，展开后必须仍来自完整查询响应。

#### 错误与动作策略

- HTTP `422` 映射到创建表单字段或整体合同错误；`404` 在 Run 发现时表示“尚无 Workflow”，在已有 ID 查询时使用统一不可用状态；`409` 先重新发现该 Run 的 Workflow，不自动改 key 创建第二个 Workflow。
- POST 返回 `200`/`201` 后仍读取响应或终态事件中的业务 `status`。`failed`、`cancelled` 和 `outcome_unknown` 不能因 HTTP 成功显示为完成。
- `WorkflowErrorRead` 的 `public_message` 是用户可见错误，`error_code` 用于稳定映射，`details_reference_id` 只作为受控诊断引用。页面不得显示 Provider 原始响应或内部异常正文。
- 当前稳定错误按用户动作分组：计划/合同类包括 `node_output_invalid`、`agent_capability_unavailable`、`agent_plan_invalid`、`agent_review_policy_mismatch`、`agent_dependency_cycle`；限制类包括 `agent_budget_exhausted`、`agent_budget_price_unavailable`、`agent_node_limit_exceeded`、`agent_model_call_limit_exceeded`、`agent_workflow_timeout`；质量门禁类包括 `agent_verification_failed`；终止/系统类包括 `agent_workflow_cancelled`、`agent_provider_outcome_unknown`、`agent_workflow_internal_error`。Reviewer 的 `fail`、错误级发现、重试或重新规划要求是最终合成的证据约束和 Evaluation 事实，不再映射为 `agent_review_rejected` 工作流终止错误。未知 Provider 错误代码使用通用安全文案，并继续服从 `is_retryable` 和 `outcome_is_known`，不能因前端未登记就改成可重试。
- `outcome_unknown` 表示 Provider 结果是否发生无法确认。当前相应错误会强制 `is_retryable=false`；前端只提供“重新读取证据”，不提供一键重新运行。
- 额度耗尽、验证失败、审核意见和执行失败分别显示真实原因。只有服务端已保存修正／返工事件才显示相应进度，不能仅凭 Reviewer 的 `needs_revision` 推断正在修改；未执行返工显示服务端原因，旧单轮记录不补造返工活动。
- 当前只允许显式取消；取消按钮在 Workflow 为 `running` 时出现，重复提交按幂等结果处理。恢复、批准、用户输入、返工、重排 Task、提高上限或绕过能力拒绝仍不提供。

#### BFF 与授权边界

- Next.js 后端为前端（BFF）允许列表只增加上表八个路径和对应方法；仍使用流式请求/响应大小限制，不增加 Agent 通配代理。
- BFF 以 Run ID 创建/发现时先校验 Run 属于当前登录用户；以 Workflow ID 查询时在服务端解析其 `run_id` 后再校验 Run 归属；Node 详情继续要求 Node 属于路径中的 Workflow。
- 浏览器不能提交或覆盖 `user_id`，不能看到平台 API Key、内部路径、数据库标识以外的内部存储位置、模型 Prompt 或原始 Provider 响应。
- FastAPI 业务 Router 当前仍处于受信调用方边界；BFF 的当前用户校验不能替代服务端逐资源主体授权。公开发布前必须将 Workflow、Node、Event 及其他最终用户资源纳入 FastAPI 主体授权测试。

#### 当前前端实施任务

| 工作项 | 具体任务 | 预期结果 | 验证方式 |
|---|---|---|---|
| 合同类型 | 从 OpenAPI 生成或手写严格类型，覆盖 Create、Summary、Result、Node、Event 和 cursor page；对 `output_type` 做穷尽映射 | 编译期能发现字段、状态和节点输出版本变化 | 合同样本测试、TypeScript 穷尽检查、OpenAPI 差异检查 |
| BFF 代理 | 增加八个精确路由的方法/路径允许列表、Run 归属链校验、SSE 转发和大小限制 | 浏览器可安全访问查询与取消接口，且不能跨开发用户操作 Workflow | 允许/拒绝/缺少归属/跨用户/超限/流式代理测试 |
| 提交模式 | 增加显式 Agent 模式、Run `user_request` 关联、角色模型绑定、审核策略、限制设置和 Workflow 幂等 key；禁止重复保存最终 Assistant Message | 普通响应与 Agent Workflow 不混用；已知 Run 下的 Workflow 超时不重复计费 | Run 目标一致性、Message/Run 部分失败、最终消息单次保存、表单边界、幂等回放、`200`/`201`、`409` 恢复测试 |
| 事件存储 | 实现 sequence 去重、缺口检测、有限回放、终态 Result 收敛和版本保护 | SSE、刷新与断线都回到同一后端事实 | 重复、乱序、缺页、非法游标、断流、终态缺失测试 |
| Workflow 摘要 | 在 Run 详情展示阶段、活动节点、计数、耗时、聚合用量和终态错误 | 用户先看到当前事实，再按需展开节点 | 空、运行中、完成、失败、取消、未知结果组件测试 |
| 节点时间线 | 用统一信封和 11 个 `output_type` 渲染器展示完整节点结果 | Agent 分工、Handoff、验证和审核可复核，不展示隐藏推理 | 每种输出、未知版本、长内容、警告/错误、移动端测试 |
| 并行与取消 | 显示 `dispatch_groups`、`reserved_estimated_cost`，并代理显式取消动作 | 同组并行关系和在途预算可解释；刷新后仍能读取取消终态 | 两个并行 Agent、重复取消、供应商调用在途取消和刷新测试 |
| 浏览器验收 | 使用真实 API 跑通新建 Run 下的 JSON 与 SSE Workflow、刷新恢复和失败状态 | 内部开发版可独立验证阶段3核心接口 | Playwright 关键流程、网络响应敏感字段扫描、性能检查 |

#### 后续后端阶段完成后再启用

- 恢复和等待用户输入：等待版本化工作流真正产生等待状态、幂等输入接口、允许动作和刷新确认合同。
- 进程重启恢复：等待启动审计与遗留 `running` 状态收敛；此前不宣称崩溃可恢复。
- Tool/Implementer/写隔离：等待阶段7恢复及新的 `tool_enabled_v1`，并复用同一节点信封增加工具输出，不能改变 `model_only_v1` 含义。
- 大型节点结果继续服从 64 KiB 公共合同上限；只有后续独立节点类型明确采用 Artifact 输出时，前端才增加 hash、MIME、受控预览和内容读取。

#### 当前前端通过条件

- 八个当前接口的请求、响应、状态码、cursor、SSE ID/事件名和完整节点合同与阶段3 OpenAPI 一致。
- 单 Workflow 依赖分组路径、最多两个并行工作 Agent、Handoff、确定性验证、Reviewer 三种策略、预算预留/耗尽、上限终止、验证失败、审核拒绝、显式取消和未知结果都能准确显示。
- 事件重复、乱序、缺页、断线和刷新不会重复节点、丢失终态或自动发起第二次模型调用。
- BFF 的当前登录用户归属、Workflow/Node 父子关系、路径/方法允许列表、请求大小和敏感字段测试通过。
- 时间线不展示隐藏推理、内部 Prompt、Memory、Knowledge、工具执行或尚未实现的恢复动作。
- 真实浏览器完成 JSON 与 SSE 两条关键流程，并验证窄屏、长节点结果、键盘操作和长时间运行页面性能。

### 阶段8检查点：代码搜索与可复核引用

已归并为工具主计划的代码检索扩展，当前未开始。界面按授权展示安全相对路径、行/符号位置、实际覆盖和索引版本；工具调用记录与普通工具一致，不另建执行流程。专业索引实现后再补展示细节。

### 阶段9检查点：MCP 连接与能力目录

已归并为工具主计划的外部工具来源，当前未开始。连接/认证/撤销需独立安全管理，发现后的工具进入同一目录和调用记录；普通页面不显示服务凭据。详细状态与验收在对应工具工作项实施前固定，不另建一组重复待办。

### 阶段6检查点：可观测性与内部诊断

**前端范围：**

- 普通用户只看到安全的请求关联标识、运行状态、耗时和可操作错误；内部诊断页才展示 API、Model Attempt、Task、Worker、Tool 和 Agent 的关联链路。
- 诊断时间线区分业务事实时间和遥测接收时间；MySQL 事实与追踪不一致时明确标记，不用追踪覆盖业务状态。
- 提供按 Run、Task、Attempt 和关联标识跳转的耗时分解与依赖状态，不在首页增加运维仪表盘卡片。

**后端合同门禁：**

- 公开诊断响应结构与内部追踪字段分离，敏感属性在采集前过滤；浏览器不得直接查询追踪存储或日志系统。
- 采样、追踪缺失和遥测延迟必须可表达为“证据不可用”，不能转换为运行失败。

**前端通过条件：**

- 覆盖无追踪、部分 span、乱序、采样、遥测后到、后端不可用和敏感属性过滤。
- 浏览器网络响应、页面源码和导出内容中均不存在密钥、Cookie、供应商 secret 或完整私人输入。

### 阶段4检查点：前端平台自身

**前端范围：**

- 维护统一的三栏对话外壳、主题令牌、响应式行为、错误语言、可访问性、BFF 允许列表和浏览器安全边界。
- 按本节的后端检查点逐步增加功能；功能开关只能控制是否展示已经存在的后端能力，不能把未完成能力变成模拟成功。
- BFF 负责同源调用、浏览器会话转发、请求大小限制、路径与方法允许列表和公开响应映射，不承载后端业务状态机。

**前端通过条件：**

- 每次新增后端检查点集成，都同时通过类型检查、Lint、生产构建、合同测试、真实浏览器关键流程、响应式和安全扫描。
- 主要操作可用键盘完成；加载、空、失败、无权限、冲突、取消和部分成功均有明确状态。
- 前端性能门禁覆盖长会话、长流式响应、大量时间线事件和详情抽屉，避免按 token 重渲染全部页面。

### 阶段5检查点：最终用户身份与凭据

**前端范围：**

- 增加登录、退出、当前用户、登录会话列表、单会话撤销、全部会话撤销和密码管理入口。
- 增加 NexusPilot 个人 API Key 列表、创建、一次展示和撤销；密钥原文只在创建成功页出现一次，离开后不可恢复。
- 增加供应商凭据创建、命名、验证、默认项、轮换和撤销；列表始终掩码，验证可能产生费用时必须在提交前说明并确认。
- 当前用户由后端选择，BFF 与 FastAPI 已检查资源归属；部署级服务 Key 仍保留但不替代用户主体，未来移除服务 Key 是独立迁移，不是新增资源可省略授权的理由。具体规则和目标环境验证范围以阶段5为准。

**失败与恢复：**

- 401 返回登录或重新认证；无权查看其他用户资源使用后端规定的 404/403，不在前端枚举资源存在性。
- 登录会话撤销后立即清除本地用户状态和敏感缓存；重复退出保持成功体验。
- 个人 API Key 创建响应丢失时提示撤销并重新创建，不能提供“再次显示”。
- 凭据验证超时、版本竞争、撤销和密钥管理服务不可用分别展示；不得自动回退到平台或其他用户凭据。

**前端通过条件：**

- 登录、退出、当前用户、跨站请求伪造防护、会话固定、到期、撤销、跨用户隔离和 Cookie 属性通过浏览器安全测试。
- 个人 API Key 和供应商凭据不进入 localStorage、sessionStorage、日志、URL、错误追踪、SSE 或静态资源。
- 阶段5与对应 Web 集成全部完成前，阶段4不得改为面向最终用户公开发布状态。

## 信息架构

### 主布局

桌面端使用“会话侧栏 + 对话主区 + 按需详情抽屉”，移动端将侧栏和抽屉改为覆盖层：

```text
┌────────────────┬──────────────────────────────────┬──────────────────┐
│ NexusPilot     │ 当前会话标题       主题 / 运行详情│ 运行详情          │
│ + 新对话       ├──────────────────────────────────┤                  │
│                │ 用户消息                          │ Run 状态         │
│ 今天           │                                  │ Task 状态        │
│  会话 A        │ Assistant 流式响应                │ Attempt/Retry    │
│  会话 B        │  ▌                               │ token / 费用     │
│                │                                  │ Artifact         │
│ 更早           │ [未来：Agent/Tool 执行轨迹]       │ 错误证据         │
│  会话 C        ├──────────────────────────────────┤                  │
│                │ 输入框       Provider/Model 发送│                  │
└────────────────┴──────────────────────────────────┴──────────────────┘
```

桌面宽屏默认显示三栏，使用户在阅读回答时可以同时核对运行证据；用户可以主动收起运行详情，收起后对话主区占用释放出的空间。窄屏不常驻侧栏或运行详情，两者均改为由用户主动打开的覆盖层，避免挤压主要对话体验。

### 已确认的布局基线

后续正式前端以当前 Sites 预览为视觉与交互基线。预览用于确认布局、层级、主题和响应式行为，不代表后端接口、数据持久化或模型调用已经实施。

| 区域 | 桌面布局与职责 | 收起或窄屏行为 |
|---|---|---|
| 会话侧栏 | 固定约 252 像素；从上到下依次为品牌、新对话、最近会话和环境/设置入口 | 视口小于约 880 像素时变为左侧覆盖层，通过菜单按钮打开，选择会话后关闭 |
| 对话主区 | 弹性占用剩余空间；顶部栏、消息阅读区和底部输入区纵向排列 | 始终作为页面主体，不因侧栏或详情面板打开而丢失当前输入与滚动位置 |
| 顶部栏 | 高度约 72 像素；左侧显示当前会话，右侧只保留明暗主题和运行详情开关 | 手机宽度进一步压缩操作文字，只保留必要图形入口 |
| 消息阅读列 | 内容最大宽度约 760 像素并居中；通过头像、名称、间距和排版区分用户与 Assistant | 小屏减少左右留白；Assistant 正文取消额外左缩进，避免正文过窄 |
| 输入区 | 固定在主区底部，宽度约为当前对话主区的 80%，最大宽度与消息阅读列一致；Provider/Model 选择位于输入框右下角并与发送按钮组成连续操作区 | 移动端固定在视口底部并使用可用宽度；隐藏次要上下文文案，保留模型选择、多行输入、发送和安全区留白 |
| 运行详情 | 固定约 334 像素；依次展示结果状态、核心指标、模型调用、运行时间线和未来能力提示 | 可以收起；视口小于约 880 像素时变为右侧覆盖层 |

桌面在约 1100 像素以下时，可以将侧栏和详情栏分别收窄到约 224 像素和 300 像素。手机宽度约 580 像素以下时，应进一步精简顶部操作、消息动作和输入区辅助文字。正式实现可以根据真实内容做小幅断点调整，但不得改变“三栏桌面、双侧覆盖层移动端、中央对话为主”的信息层级。

### 已确认的界面状态

- 新对话是左侧栏的唯一主要动作；最近会话使用单层列表，不增加嵌套卡片。
- 当前会话在列表中使用轻微表面色变化标识，不能仅依赖彩色描边。
- 用户消息使用浅色填充气泡；Assistant 消息使用开放式正文排版，不增加完整卡片背景。
- Assistant 消息下方保留复制、重新生成和查看运行证据操作；正式实现必须根据运行状态决定是否启用。
- 运行详情默认展示完成状态、总耗时、token、预估费用、重试次数、Provider、Model 和时间线；所有值必须来自后端事实。
- 输入区相对对话主区固定在底部，不重复扣除侧栏或运行详情宽度；消息列表需要预留足够底部空间，最后一条消息不能被输入框遮挡。
- 页面、侧栏、对话主区和运行详情共享同一动态视口高度，内部内容分别滚动，三栏底部必须对齐。
- 当前 Agent Workflow 只显示阶段3合同中的已提交事实；工具执行提示仍只是能力预留，正式工具事件合同完成前不得显示伪造步骤或进度。

### 页面与路由

| 页面 | 建议路由 | 作用 | 启用检查点 |
|---|---|---|---|
| 新对话 | `/` | 空状态、Provider/Model 选择和首次输入 | 阶段1、阶段2、阶段4当前内部版 |
| 会话 | `/c/[sessionId]` | 消息历史、流式响应和当前 Run 摘要 | 阶段1、阶段2 |
| 运行详情 | `/runs/[runId]` | 阶段1先展示 Task、Attempt、Retry、Artifact；阶段3当前增加 Agent Workflow 摘要、节点时间线和结果；其他阶段继续增量增加任务、工具、代码、MCP 和诊断证据 | 阶段1基线；阶段3只在内部功能门禁下启用当前八个接口 |
| 模型设置 | `/settings/models` | 查看实际注册 Provider 和已验证 Model Catalog | 阶段2 |
| MCP 连接 | `/settings/connections` | 管理 MCP 连接、认证状态和能力目录 | 阶段9 |
| 登录 | `/login` | 建立最终用户浏览器会话 | 阶段5 |
| 账户安全 | `/settings/security` | 登录会话、个人 API Key 和供应商凭据 | 阶段5 |
| 内部审计 | `/internal/audit` | 双密钥保护的开发/运维入口，不出现在普通用户导航中 | 阶段1内部能力；阶段5后继续与普通用户权限隔离 |
| 内部诊断 | `/internal/diagnostics` | 关联 API、模型、Worker、工具和 Agent 的安全诊断证据 | 阶段6 |

第一版只启用阶段1、阶段2和阶段4当前内部版所需页面，不创建 Memory、Knowledge、Project 看板或 Agent 管理页面。后续路由必须等待表中对应检查点，不提前创建可误解为空能力的普通用户入口。

## 已确认的视觉与交互原则

- 中央阅读列保持约 720～820 像素的舒适文本宽度，宽屏剩余空间用于留白或详情抽屉。
- 左侧栏使用低对比度背景，突出“新对话”和最近会话，不堆叠多层卡片。
- 消息主体以排版区分角色；避免每条消息都使用厚重边框和阴影。
- 输入框固定在主区底部并支持多行输入及最小化；仅聚焦输入框时 Enter 发送、Shift+Enter 换行，保留显式发送按钮，生成期间显示停止按钮。
- 流式文本直接追加到当前 Assistant 消息；每一帧不重新渲染完整 Markdown，按批次刷新以避免长响应卡顿。
- 已完成消息中的代码块提供语言标识、复制按钮、保留空白和横向滚动；流式期间先使用低开销的代码展示，不能因每个增量重新高亮整段回复。表格、引用和列表遵循 Markdown 语义。
- 状态、错误和费用使用低干扰的辅助信息，不覆盖正文。
- 使用 NexusPilot 自有名称、标记和排版，不复制 ChatGPT 商标、图标、文案或专有视觉资产。
- 整体采用单色视觉系统。亮色主题使用白色背景、黑色文字和黑色主要动作；暗色主题反相为黑色背景、白色文字和白色主要动作。
- 不使用蓝紫色、绿色等常驻品牌强调色。成功、警告和失败首先通过文字、图标、标签和结构表达；如后续可用性验证要求增加状态颜色，只能作为辅助信号，不能改变黑白主题基线，也不能成为唯一状态区分方式。

第一版设计令牌基线：

```text
亮色页面背景：白色
亮色面板：白色到浅灰色
亮色主文字与主要动作：近黑色
暗色页面背景：近黑色
暗色面板：近黑色到深灰色
暗色主文字与主要动作：白色
辅助文字：与当前主题保持足够对比的中灰色
分隔线：亮色使用浅灰，暗色使用深灰
状态：黑白图标、文字和标签为主，颜色不是唯一信息载体
圆角：输入框和浮层适中，普通消息内容不滥用卡片圆角
动效：150～220ms；遵循 prefers-reduced-motion
```

主题切换必须同时反转页面背景、面板、文字、主要按钮、品牌标记和状态图形，不能只修改主区背景。阴影在亮色主题使用低透明度黑色，在暗色主题使用低透明度白色或边框替代，避免产生彩色光晕。

## 第一版核心流程

### 新建并发送对话

1. 前端服务端获取可用 Provider。
2. 用户选择 Provider 和 Model，输入消息。
3. BFF从后端`/auth/me`解析固定本地owner或当前登录主体，创建/读取其Session；Web不自行创建用户。
4. 调用Session轮次接口，使用本次提交的幂等键原子创建Run与当前User Message；相同key/输入重放原对象，不创建第二个Run或消息。只有实际需要任务对象时才创建Task，不伪造Agent任务。
5. 调用 `/api/v1/responses` 并传入确定的Run和当前Message锚点，由后端统一Context Builder组装已启用来源；浏览器不拼接会话历史或System Prompt。
6. 普通响应一次渲染；SSE 响应按 sequence 去重并增量渲染。
7. 完成后通过 Message 接口追加 Assistant Message；如果追加失败，界面保留已收到内容并显示“响应已生成，但会话保存失败”。在 Message 幂等合同完成前只能由用户查看 Run/Attempt 并人工处理，不得自动重试写入或重新请求模型。
8. 详情抽屉读取 Attempt，展示 token、费用、耗时和 Provider 请求状态。

前端编排必须使用稳定的客户端请求 ID；只有对应后端接口支持幂等键时才允许自动重试。页面刷新后从 Session Message 和 Run/Attempt 事实恢复，不从浏览器内存猜测最终状态。

### SSE 状态映射

| 当前事件 | 界面行为 |
|---|---|
| `response.started` | 创建空 Assistant 消息并显示生成状态 |
| `response.text.delta` | 按 sequence 追加文本 |
| `response.tool_call.delta` | 显示“模型提出工具调用”，只展示有界、脱敏参数预览 |
| `response.usage` | 更新 token 与费用辅助信息 |
| `response.completed` | 固定最终消息，允许复制和打开运行详情 |
| `response.failed` | 停止流并显示稳定错误；保留已经收到的部分文本但标记为未完成 |

浏览器断线后不能把部分文本标为完成。若后端已有最终 Attempt 状态，重新读取；没有恢复协议时明确提示用户查看运行详情，不自动发起第二次计费请求。

## Agent 与工具流转的分阶段显示

`model_only_v1` 接入后，每条由 Agent Workflow 产生的 Assistant 响应下方增加可折叠的“执行过程”。折叠时只展示最新活动，展开后按 `event_sequence` 展示经过脱敏的生命周期瀑布流；同一 `dispatch_group` 使用组标识表达并行关系：

```text
Controller：已规划“解释瑞利散射并核对白天与日落差异”
  ├── Planner：正在执行“整理解释结构”
  ├── Planner：已提交阶段结果“先解释短波更易散射”
  ├── Researcher：正在执行“核对关键事实”
  ├── 确定性验证：等待上游结果
  ├── Reviewer：正在审核候选结果
  └── 最终汇总：正在规划最终答案
```

当前用户可见状态只映射已实现事实：等待执行、正在运行、正在调用模型、正在审核、正在验证、已完成、失败、已取消和结果未知。规划中但当前不可用的“正在等待工具”“正在执行工具”“等待用户输入”“等待用户批准”和“正在并行执行”不能进入正常文案枚举。

阶段3新增的“公开活动摘要”已成为本区域的数据来源。它不是模型推理摘要，而是后端在节点开始、完成和终止等耐久检查点生成的公开事实。前端优先读取`node_activity_summary.v1`和`public_view.activity`，缺失时才回退到历史`public_view.summary`，并遵守以下展示规则：

- 对话工作台默认只显示最新一条活动；展开后按`event_sequence`显示本次运行的全部简单摘要，不展示完整节点参数或模型原始输出。
- 同一Worker的“正在执行”和“已提交阶段结果”必须同时保留，并行Worker按`node_execution_id`分别保留；新活动不能覆盖任何较早的已提交活动。
- Worker完成事件不得使用“已完成任务”，因为它只提交供验证、审核和最终汇总使用的阶段结果；Controller计划摘要、Worker阶段结果和最终答案组织使用不同文案表达各自职责。
- 活动摘要只按纯文本渲染，不解析Markdown，不从句子推断状态；状态、并行关系和完成结果继续读取类型化字段。
- Provider公开推理摘要继续属于现有Reasoning区域，节点活动属于“执行过程”区域；两者视觉上可以相邻，但不能合并成同一个合同或标签。
- 单次Provider调用没有可靠的中间公共事实时，保持一条“Agent正在执行”直到类型化阶段结果提交。界面不显示虚构百分比、隐藏思维链或模型尚未提交的部分输出。
- Final Message到达后保留折叠对话活动区，展开后仍可查看本次运行的简单摘要；完整旧运行证据继续使用独立只读抽屉，不改变当前路由、Composer、活动运行订阅或待完成回复。

阶段7恢复并新增 `tool_enabled_v1` 后，才在相同执行过程内增加 Implementer 与 Tool Call 子节点。每个工具调用最多展示人类可理解名称、风险、开始时间、持续时间、状态、有界输入摘要、安全结果或 Artifact、批准状态和稳定错误；仍不展示完整 Shell 环境、密钥、内部对象 URI、模型隐藏推理、完整私人文件或未脱敏输出。

阶段3当前事件合同已经确定 `event_id`、`event_sequence`、Workflow/Run/Node 标识、事件类型、Workflow/Node 状态、发生时间、公共摘要、公共载荷和可选 trace ID。前端直接使用该合同和节点详情路径，不再设计第二套通用运行事件字段；阶段6只补充遥测关联，不替代 MySQL 业务事实。

## 前端模块建议

```text
apps/web/
├── app/
│   ├── page.tsx
│   ├── c/[sessionId]/page.tsx
│   ├── runs/[runId]/page.tsx
│   ├── login/page.tsx
│   ├── settings/models/page.tsx
│   ├── settings/connections/page.tsx
│   ├── settings/security/page.tsx
│   └── internal/diagnostics/page.tsx
├── components/
│   ├── chat/
│   ├── conversation/
│   ├── execution/
│   ├── model-selector/
│   ├── task-execution/
│   ├── tool-execution/
│   ├── agent-workflow/
│   ├── code-evidence/
│   ├── mcp-connections/
│   ├── observability/
│   ├── auth/
│   └── ui/
├── lib/
│   ├── api/
│   ├── events/
│   ├── feature-gates/
│   ├── errors/
│   └── formatting/
└── tests/
```

- `chat`：消息列表、Markdown、Composer 和流式状态。
- `conversation`：会话侧栏、分页和标题。
- `execution`：Run/Task/Attempt/Artifact 的公共证据外壳。
- `model-selector`：Provider/Model 选择与能力提示。
- `task-execution`：阶段10的排队、Worker、重试、取消与恢复状态。
- `tool-execution`：阶段7的工具提议、批准、执行结果与 Artifact。
- `agent-workflow`：阶段3当前的创建表单、Workflow 摘要、完整节点类型渲染、Handoff、验证、审核和 Result 收敛；等待用户动作只在后端接口实现后增加。
- `code-evidence`：阶段8的文件定位、符号关系和索引状态。
- `mcp-connections`：阶段9的连接、能力目录、认证状态和受控调用。
- `observability`：阶段6的公开关联标识与内部诊断视图。
- `auth`：阶段5的当前主体、登录会话、个人 API Key 和供应商凭据。
- `lib/api`：Next.js 服务端调用 FastAPI，不把内部密钥传给浏览器。
- `lib/events`：Responses SSE 与 Agent Workflow SSE 分别处理 sequence、cursor、取消、断线和终止状态；Agent 事件使用 `event_sequence` 与有限回放，不和 Responses 事件用无约束对象混装。
- `lib/feature-gates`：根据已验证后端能力控制导航和动作显示，不生成模拟后端状态。

以上目录是职责边界，不要求在对应后端检查点之前创建空目录或占位组件。已有文件可以在职责仍清晰时继续使用；只有实际接入新能力时才拆分。

具体技术版本在开始实施时以当前 Next.js 长期支持版本和仓库运行环境为准，不在规划中写死可能过期的版本号。

## 失败与恢复设计

| 失败场景 | 界面与恢复行为 |
|---|---|
| Provider 未配置 | 禁用对应选项或显示明确配置错误，不回退到其他 Provider |
| 请求校验失败 | 在输入区显示字段错误，不清空用户输入 |
| SSE 中途断开 | 保留部分文本；已知 Attempt 时从最后确认序号回放，未知 Attempt 时只读取 Run/Attempt 最终状态；不自动重新计费 |
| 用户主动停止 | 取消上游请求并等待后端最终状态；不能只在前端停止渲染 |
| Message 保存失败 | 保留响应并指向 Run/Attempt；在 Message 幂等能力完成前不自动重试保存，更不能重复调用模型 |
| Run/Attempt 查询失败 | 正文仍可阅读，详情抽屉显示依赖暂不可用并允许手动重试 |
| cursor 失效 | 清空对应分页窗口后从第一页重新加载，不混合新旧筛选结果 |
| 异步 Task 暂无 Worker | 显示已接受或等待执行的后端事实，不显示虚假的执行百分比；允许动作由后端返回 |
| User Message 或 Run 创建结果未知 | 同一次轮次提交调用内仅对网络/5xx不确定结果使用原key和相同请求体再试一次；仍失败则保留输入并说明未确认。跨刷新/再次点击尚未恢复原key，不自动创建第二条Message、Run或Workflow来假装恢复 |
| Agent Workflow POST 断流或超时 | 保留原 `idempotency_key`，先按 Run 发现 Workflow、回放已提交事件并读取 Summary/Result；不生成新 key 自动重试 |
| 运行中查看历史回复证据 | 在独立检查器状态中按所选 Message 的 `run_id` 读取 Run、Workflow、Node、Reasoning 和 Context 证据；不跳转 `/runs/[runId]`，不重建工作台，不取消或替换当前执行 |
| 页面恢复时最新 Run 仍在活动状态 | 恢复待完成 Assistant 投影并禁用新提交，允许读取或显式取消权威 Workflow；不把缺少本地 SSE reader 解释为运行已结束 |
| Agent Workflow 事件重复或缺口 | 按 `event_sequence` 去重；缺口时停止实时标识，执行有限回放并以查询快照收敛 |
| Agent Workflow 结果未知 | 展示安全错误和已提交节点证据，只允许重新读取；不提供自动重跑或“继续执行” |
| Tool/Agent 等待批准或用户输入 | 当前阶段3不显示该动作；后端接口实现后保持 Run 可恢复并使用幂等/状态条件合同提交 |
| MCP 连接或认证失效 | 禁用该连接的新调用并保留已有运行证据；返回连接设置入口，不自动切换其他连接 |
| 遥测或追踪缺失 | 只标记诊断证据不可用，不把业务 Run 改为失败 |
| 未授权或凭据失效 | 清除前端应用会话并返回登录/配置入口，不显示内部错误正文 |

## 第一版实施记录

第一版实现位于 `apps/web`，以本文件前面确认的 Sites 预览为界面基线，当前已经具备以下能力：

- Next.js 服务端转发层：浏览器通过同源 `/api/nexus/*` 访问 FastAPI，平台 API Key 只在服务端读取。
- 三栏工作区：会话侧栏、中央对话与输入区、运行证据检查器；桌面端默认展示详情，窄屏改为左右覆盖层。
- 会话数据流：读取 Session 与最新消息窗口，服务端通过单次完整消息分页合同返回正文，并用游标继续加载更早消息；首次恢复同时读取 Session 最新 RunDetail 与 Attempt 事实。
- 稳定路由：会话使用 `/c/[sessionId]`，运行证据使用 `/runs/[runId]`；新会话首次提交期间保持当前页面实例，只有 Assistant 消息已持久化并可恢复后才替换为会话路由，避免 App Router 在流式处理中重建页面并丢失首条回复；刷新和浏览器历史导航会重新读取持久化事实。
- 证据查看解耦：消息操作不再导航到运行路由，而是按所选 Assistant Message 的 `run_id` 加载独立只读快照；历史证据的加载、失败和关闭均不修改当前工作台 Run、Workflow、消息或取消目标。`/runs/[runId]` 继续仅作为直接访问和分享的稳定入口。
- 模型调用：自定义选择器读取 `GET /api/v1/models?requires_streaming=true`，只投影同时满足稳定登记启用、Provider Registry允许、能力快照已确认和流式合同可用的模型；支持搜索和键盘选择，不再把环境白名单或空白名单直接当作可选择模型。`GET /api/v1/providers` 继续保留兼容。对话通过 `POST /api/v1/responses` 接收正文、Reasoning、用量、完成和失败事件。快速回复和无预算 Agent Workflow 当前不发送 `max_output_tokens`，由 Provider 和模型能力决定输出；接口字段继续保留为可选整数，显式值范围为 1～65536。Provider 如果仍返回 `finish_reason=length`，前端保存部分正文和结束原因，并明确显示“达到输出上限”，不伪装成完整回复。Composer只展示所选模型声明的思考程度，并把统一`reasoning`合同同时透传给快速回复和Agent Workflow。
- Assistant 正文：流式增量与历史恢复统一使用 GitHub Flavored Markdown 渲染，支持标题、强调、带层级标记的列表、引用、表格、行内代码和围栏代码块；原始 HTML 不执行，危险链接被移除，远程图片只显示为不可加载的文本占位。
- 流式安全行为：按 sequence 去重并拒绝缺口；已知 Attempt 断线后读取持久事件直到终止；停止生成会中止浏览器请求且不会触发自动恢复；部分文本不会被标记为完成。
- 服务端代理边界：仅开放前端需要的固定路由，并强制校验当前登录用户的 Session、Message、Run、Attempt、Workflow 和 Node 归属；请求体在流式读取中受大小上限约束。
- 主题与响应式：亮色白底黑字、暗色黑底白字；支持本地主题偏好、仅按钮发送、移动端覆盖层和 reduced motion。
- Agent Workflow：提供显式 `model_only_v1` 模式、审核策略、工作 Agent 最大并行数、八个接口客户端、POST SSE 与有限事件回放、事件缺口检测、显式取消、完整节点类型、11 类节点证据时间线和 Result 恢复；执行组按后端 `dispatch_groups` 展示，并区分已消费费用与当前费用预留；DeepSeek 根据已注册能力使用 prompted JSON。
- Agent 代理边界：BFF 当前开放八个精确路径，以 Run 校验 Workflow、取消动作和 Node 的当前登录用户归属；取消请求不携带 JSON 请求体，仍在转发动作前完成归属校验。

### 当前能力总览

以下状态以当前代码、自动化验证和本地运行事实为准，不用历史计划替代实际行为：

| 能力范围 | 当前已实现 | 当前未实现或未完成 | 状态 |
|---|---|---|---|
| 会话与路由 | 创建、选择和重命名会话，原子创建Run与当前User Message，消息分页、稳定会话/运行路由、首条回复保存后切换路由、消息与草稿恢复、同会话多标签页互斥、再次提问和独立消息证据；轮次重试与已保存快速回复的即时/刷新续聊通过真实BFF和实际FastAPI检查，使用临时SQLite/FakeProvider/内存对象存储；仅在单completed Attempt、对应已保存Assistant、同会话消息顺序及明确Workflow 404等事实完整时允许pending Run续聊 | 会话归档/取消归档暂停；跨刷新/再次点击后的key持久化未实现，轮次重放不等于首事件前模型执行恢复；Workflow查询失败、活动/未知状态与不完整事实仍禁发，后端Run状态保持不变 | 进行中 |
| Provider 与模型选择 | Web读取稳定模型目录，支持搜索、键盘选择、加载/失败/空目录状态，并提供只读 `/settings/models` 服务端模型事实页；保留旧Provider目录兼容接口 | 最终用户自己的Provider凭据和模型授权过滤等待阶段5；当前设置页不提供登记、密钥或启停操作 | 进行中 |
| 稳定模型目录 | `llm_models`稳定模型实体、历史能力快照去重回填、内部登记/更新/显式Registry初始化、`GET /api/v1/models`、能力要求查询、不可用原因和Web选择器交集已实现 | `user_model_access`和最终用户授权过滤按边界留给阶段5 | 已完成 |
| Responses流 | SSE增量正文、序号去重、缺口拒绝、已知Attempt有限回放、完成/保存/失败/取消状态分离、默认不发送输出Token上限、`length`截断证据持久化、Run锚定Session文本Context Build，以及后端System Prompt和明确引用的文本Artifact来源；普通Web的System Prompt选择、提交和提示已移除 | 未知Attempt的首事件前断线仍不能自动重发模型请求；Artifact产品绑定、Memory、Knowledge与Tool来源尚未启用；修复前的历史Message不猜测回填截断元数据 | 进行中 |
| Reasoning 展示 | `raw`、`summary`、`status` 联合类型，Chat 折叠展示、Trajectory 状态与指标、动画帧批量更新、历史恢复 | 隐藏推理继续不可见；不从 Provider 名称推断推理正文 | 已完成 |
| 思考程度控制 | 完整统一等级、逐模型控制能力目录、四家Provider映射、默认medium、Attempt证据、Composer合并入口、模型切换归一化以及快速回复/Agent Workflow透传已实现 | 真实Provider浏览器矩阵仍待后续执行 | 进行中 |
| Markdown 与代码块 | 实时和历史Assistant共用安全GitHub Flavored Markdown；本轮新增语言工具栏、独立复制、成功/失败提示、13px代码和按钮、长行独立横滚；模拟BFF下桌面1200px及移动390px Chromium检查通过，继续禁用原始HTML、危险链接和自动远程图片 | 不代表真实BFF、Provider或完整跨浏览器验收；可选语法高亮尚未实现 | 进行中 |
| 消息输入与发送 | 多行输入、自动增高至 320px 后内部滚动、空白输入拒绝、显式发送及最小化按钮；仅聚焦输入框时 Enter 发送、Shift+Enter 换行，输入法组合不发送；完整 Composer 高度驱动消息底部留白 | 输入区仍需持续进行跨浏览器验证 | 已完成 |
| Agent Workflow | `model_only_v1` 创建/发现/结果/节点/事件、Controller、计划校验、最多两个 Worker 并行、Handoff、验证、Reviewer、最终汇总、取消、费用预留、快照恢复、审核意见不吞掉最终回答、活动运行恢复后的重复提交保护，以及职责明确的公开活动摘要和可折叠生命周期瀑布流 | 新版工具配置和两轮流程已有本地实现，验收范围见文首；脱离 HTTP 独立执行、跨进程恢复、通用等待／批准仍未实现，不能把事件回放当作后台恢复 | 进行中 |
| Agent上下文 | Controller、Worker、Reviewer和最终汇总按各自模型使用Run锚定的Context Build；已提交Handoff、确定性验证、独立审核和Evaluation作为类型化来源进入下游节点，当前用户目标与协作数据不重复拼接，并保留策略版本、信任级别、必选标记及Attempt/Node证据 | 上传附件绑定与只读工具已有实现；基础 Context 不包含全部工具历史，最终有效请求保存缺口见阶段2；System Prompt 不增前台配置，Memory／Knowledge 继续暂停 | 进行中 |
| 运行证据 | Workflow摘要、11类节点、Attempt Reasoning、用量、费用、并行组、安全错误和脱敏Context证据；消息入口按自身Run读取独立只读快照，查询失败不遮挡当前回复或中断执行；Context已过滤System Prompt存在性及元数据 | 完整Task/Attempt/Retry/Artifact视图、允许动作、Artifact详情页及完整Evaluation开发视图尚未实现；普通产品不增加Prompt视图 | 进行中 |
| 前端平台边界 | 三栏布局、亮暗主题、移动覆盖层、固定BFF路由、资源归属、请求大小限制、环境与构建目录隔离、Next.js页面边界；BFF按实际Host与请求协议严格比较Origin，两个本地入口同源POST通过、跨别名Origin返回403；117项测试、ESLint、TypeScript、生产构建与20项桌面/移动模拟BFF浏览器测试通过 | 真实Provider浏览器端到端、完整可访问性、跨浏览器、敏感字段和MinIO检查未完整执行；转发头可信代理及HTTPS真实部署未验收，不将本地同源修复作为公网安全通过 | 进行中 |
| 身份与后续平台能力 | 浏览器登录、Cookie会话、退出和当前主体BFF归属可用于多用户前端联调 | FastAPI 资源主体授权已实现，目标环境完整矩阵待验收；个人 API Key 未实现、用户 Provider 凭据暂停；工具基础循环已有实现，其他后续能力按各自主计划实施 | 进行中 |

### 当前故障定位索引

故障先按发生边界定位，不能把所有失败统一解释为前端渲染问题：

| 失败位置 | 典型原因 | 用户可见结果 | 权威检查位置 |
|---|---|---|---|
| 环境与 BFF | 前后端环境文件混用、API Key 不一致、FastAPI 不可达、代理路由不在允许列表 | Provider 或会话读取失败、401、404 或 502 | 浏览器 `/api/nexus/*` 请求、Next.js 终端、`apps/api/.env` 与 `apps/web/.env.local` |
| 数据库与迁移 | MySQL 不可达或 Alembic 版本低于代码要求 | Message、latest Run、Workflow 或 Node 查询返回 500 | FastAPI 日志、Alembic 当前版本和 MySQL 表结构 |
| 发送编排 | User Message 已创建但 Run 失败，或模型完成但 Assistant Message 保存失败 | 保留已经确认的局部事实，不自动重复调用模型 | Session Message、Run、Attempt 以及页面保存状态 |
| Provider 与 Responses | Provider 未配置、模型不允许、超时、网络错误、能力拒绝、输出合同错误或 `finish_reason=length` | 失败事件保留已接收正文；达到输出上限时保存并明确标记为部分回复 | Model Attempt、Responses 持久事件、Message 元数据和 Provider 安全错误 |
| SSE 与回放 | 连接中断、重复事件、序号缺口或终止事件缺失 | 已知 Attempt 时有限回放；无法恢复时不宣称完成 | Attempt 事件的 `sequence`、最终 Attempt 和 Run 状态 |
| Agent 规划与预算 | 非法角色/能力/依赖、价格缺失、预算不足、节点/调用/时间上限耗尽 | 工作流在对应节点失败并保存稳定错误代码 | Workflow `error_json`、计划校验节点和预算字段 |
| Worker 与结构化输出 | Provider 返回的 JSON 不符合节点 Schema、完成原因非法或 Provider 结果未知 | Worker 节点失败、取消或结果未知 | Worker Node、Model Attempt 和对应事件 |
| Handoff | Task/Agent Run归属冲突、字段合同错误或包含禁止的隐藏推理字段 | `handoff_submission`节点失败且没有可提交输出 | Handoff Node、源Worker Node和`llm_agent_handoffs`；Handoff不再设置独立输出上限，完整Worker结构化输出直接保存 |
| Verification 与 Reviewer | Handoff/Attempt/Task 引用不一致，或 Reviewer 拒绝、要求重试/重规划 | 确定性验证失败返回 `agent_verification_failed`；有效 Reviewer 结论继续生成受证据约束的最终回答，并在 Evaluation/节点中保留失败、缺失证据和未完成工作 | Verification、Reviewer、Evaluation、Final synthesis 和 Workflow 结果 |
| 节点结果合同 | 节点 JSON 超过 64 KiB、`output_type`/Schema 版本不匹配 | `node_output_invalid` 或安全内部错误 | Node `output_type`、`output_schema_version`、`error_json` |
| 前端恢复与展示 | Summary、事件、节点或 Result 接口局部失败 | 正文继续可读，检查器提示对应证据暂不可用 | 浏览器 Network、页面错误栏和权威 Result 快照 |

2026 年 9 月 2 日本地实际故障记录：用户在新 Agent Workflow 执行期间点击旧回复的“查看运行证据”，旧实现却读取全局最新 `run` 并导航到 `/runs/[runId]`。App Router 因页面段切换重建工作台，新的本地 Assistant 投影和事件 reader 不再可见，用户误以为没有回答并再次提交相同问题。当前修复改为按被点击 Message 的 `run_id` 加载独立只读证据快照，消息操作不改变 URL、当前工作台状态或取消目标；刷新恢复发现最新服务端运行仍活动时补回待完成回复并阻止重复提交。让执行真正脱离浏览器连接继续属于阶段10，不在阶段4伪造。

2026年8月20日本地实际故障记录：某次工作流的前六个节点完成，第七个`handoff_submission`节点因当时待提交Handoff为3352字节而失败。该限制和后续968字节投影逻辑已在2026年8月29日移除；新的Run直接保存完整Worker结构化Handoff。原失败Workflow继续作为不可变历史事实保留。

2026 年 8 月 20 日本地实际故障记录：新会话创建后立即执行 `router.replace` 会让 `/` 页面实例在流式请求期间被 `/c/[sessionId]` 页面实例替换。原实例继续完成 Responses 请求和 Assistant Message 保存，但其 React 状态更新已经不可见，因此首条回复只能在手动刷新后出现。当前实现延迟到 Assistant Message 成功保存后再替换路由；Playwright 使用真实 DeepSeek 创建新会话，确认首条回复在请求完成后立即可见，刷新同一路由后仍可见。

2026 年 8 月 20 日本地实际故障记录：截图会话的 Assistant Message 正文与页面 DOM 都在“校验文件类型：不能只看扩展”处结束；对应持久事件为 `response.completed`、`finish_reason=length`、`output_tokens=1200`。因此故障发生在旧前端硬编码的 `1200` 输出 Token 限制，不是 Markdown 或 CSS 漏渲染。当前快速回复和无预算 Agent Workflow 已不再发送平台输出上限；新消息仍持久化 Provider 结束原因，并在 Provider 或模型自身达到上限时显示部分回复提示。历史消息不猜测性回填缺失元数据。

2026 年 8 月 20 日本地实际故障记录：会话 `4e4932ee-68ec-4170-ab55-c438935e0527` 的 DeepSeek Agent Worker 调用以 `finish_reason=stop` 正常结束并返回 1195 字符正文，但最后一个数组缺少 `]`，因此严格 `json.loads` 在末尾失败；该错误与 Token、Handoff 和前端展示无关。Prompted JSON 现显式请求 DeepSeek 官方 `response_format: {"type":"json_object"}`，Provider 层先保证 JSON 语法，工作流仍以 Pydantic 校验完整节点 Schema，不把 JSON mode 误称为原生 JSON Schema。Playwright 使用真实 DeepSeek 完成一个包含 10 个节点、3 次模型调用的 Workflow，并在刷新后恢复最终 Message 和完整节点时间线。

2026 年 8 月 20 日本地实际故障记录：会话 `865c0292-c075-4c07-8523-9a98a2dc0ae6` 的 Worker 调用正常结束并返回完整 JSON，但额外输出了 `additional_properties=false`；该名称来自提示中 JSON Schema 的 `additionalProperties` 元数据，不属于 Worker 六个业务字段，因此被 `extra="forbid"` 拒绝。当前修复不对 DeepSeek 或单一模型写特殊分支：所有 `prompted_json` 调用均获得明确的顶层字段白名单和“Schema 关键字不是响应字段”约束，面向模型的 Schema 投影不再携带容易误抄的 `additionalProperties` 与 `title`，服务端仍按原始严格合同验证结果。前端继续只展示后端权威终态，不在浏览器中猜测性修补模型结果。

2026 年 8 月 21 日本地实际故障记录：结构化输出修复后的首次回归已通过两个 Worker 合同，但统一指令中的 `Use only the supplied content` 让 Agent 拒绝使用模型已有知识，Reviewer 因普通解释问题没有得到回答而按当时合同返回 `agent_review_rejected`。通用知识边界随后已修复；2026 年 9 月 2 日又进一步移除了“审核拒绝即吞掉回答”的旧合同，当前相同审核结果会进入 Final synthesis，明确缺失证据与未完成工作。会话 `6102159d-3a72-4bb9-bc0a-00a723e9448f` 当时已通过 Playwright 完成 15 个节点、6 次真实模型调用和独立审核，最终中文回答即时显示并关联持久化 Message；相关修复没有前端结果修补或 DeepSeek 专用分支。

2026 年 8 月 24 日本地 Composer 故障记录：固定的消息列表底部内边距无法覆盖 Agent 控件和长输入形成的动态 Composer 高度，因此长回复末尾的 `message-actions` 会被遮挡；原生 `select` 菜单也与相邻控件挤在一起，Textarea 只提供手工调整且 Enter 仍会误发送。当前实现使用 `ResizeObserver` 根据完整 Composer 高度更新会话底部间距，执行配置改为自定义可访问列表，并删除 Composer 内的运行机制解释。2026年8月31日重新设计后，Textarea随内容自动增高至320px后再内部滚动；生产构建中855字符输入使Composer增至493px，消息底部留白同步增至558px。Composer使用细内描边和轻阴影，Prompt、思考及模型列表采用浅底选中行，Agent低频设置进入条件设置栏；交互字号统一为12px、次级说明为11px、弹层标题为13px，恢复操作不再使用8～9px文字。前端80项测试、TypeScript、ESLint、生产构建及桌面和Pixel 7共18项Playwright测试通过。

2026 年 8 月 20 日事实审计确认：当时 Web 的 50 项 Node 测试、TypeScript、ESLint 和 Next.js 生产构建已通过；后端与模型适配层合计 262 项通过、4 项真实基础设施测试按默认配置跳过，Ruff 全量检查通过。统一 Reasoning 联合类型、折叠展示、Trajectory 指标、动画帧批量更新、仅在消息流底部自动跟随、未知类型安全忽略、Attempt 归属校验、Responses 事件回放、安全 Markdown、首条回复稳定路由、截断证据和“默认不设置输出 Token 上限”已经实现。真实 DeepSeek 快速回复与单任务 Agent Workflow 均已通过 Playwright 的即时显示和刷新恢复验证；本地 MySQL 已迁移至 `20260820_0012`，该 Agent 回归的原始 Provider 证据已由本地 MinIO 保存和读取。该段保留为当日审计记录；当前设置页和自动化状态以下方2026年8月31日实现事实为准。

当前明确不宣称完成的内容：会话归档/取消归档、FastAPI全资源主体授权、跨刷新/再次点击后的轮次key恢复和模型执行端到端恢复、Run/Task/Attempt/Retry/Artifact完整证据和允许动作、Artifact详情页、完整Evaluation开发视图、可选语法高亮、真实Provider浏览器矩阵、完整可访问性/跨浏览器/敏感字段验证、HTTPS可信代理部署，以及Agent跨请求恢复和工具时间线。可选登录、System Prompt前台移除和允许公开的Context证据已有；当前117项前端测试、ESLint、TypeScript、生产构建及20项桌面/移动模拟BFF浏览器检查通过。127.0.0.1来源误拒绝与已保存快速回复刷新禁发已修复，在临时SQLite/FakeProvider/内存对象存储条件下通过真实BFF轮次重试、即时/刷新续聊和工作流查询失败禁发检查；未修改后端Run状态。阶段4因上述剩余工作继续进行中；会话归档、Memory及真实Provider矩阵仍后置，不用本地状态替代服务端功能。

## 2026 年 8 月 31 日新增并完成的阶段4工作项

### 会话可靠性与可恢复输入

状态：已完成。

- 会话标题通过受固定用户归属校验的精确 `PATCH /api/nexus/sessions/{session_id}` 路径更新；输入只接受去除首尾空白后的 1～255 个字符，不改变消息、Run 或历史证据。
- 未发送文本按“新会话”或具体 Session 分别保存在当前标签页的 `sessionStorage` 中，刷新和会话切换后恢复；持久化用户 Message 成功后才清除草稿。草稿最多保存100000个字符，不写入 `localStorage`，不承诺关闭标签页后的长期恢复，也不把草稿当作服务端消息事实。
- 同一浏览器的同一 Session 使用 Web Locks 保证一次只有一个标签页进入计费提交区；`BroadcastChannel` 只广播 Session 标识、开始/结束和发送方标识，用于其他标签页显示忙碌状态并在完成后读取服务端事实，不传输消息正文、模型输出或凭据。缺少 Web Locks 的浏览器不宣称具备硬互斥，仍依赖后端幂等与事实查询边界。
- “再次提问”只把所选 Assistant 回复前一条配对的用户问题恢复到 Composer，不自动创建 Message、Run、Attempt 或模型调用，不修改历史消息，也不会仅因点击而产生费用。
- 会话归档、取消归档和永久删除没有实现。归档工作项按当前决定暂停；恢复时必须先定义服务端状态、列表过滤、并发更新、历史可读性与授权合同。

### 路由边界、模型事实页与可复现浏览器门禁

状态：已完成。

- App Router 增加加载、页面级错误、全局错误和不存在边界；局部读取失败继续保留已经加载的对话正文，不通过整页回到默认会话掩盖失败。
- `/settings/models` 是只读服务端模型目录：展示登记状态、是否可选择、Provider、模型标识、上下文窗口、默认思考程度和已确认能力；不提供模型登记、启停、供应商密钥或最终用户授权操作。
- Playwright 配置以固定测试端口运行桌面 Chromium 与 Pixel 7 视口，默认拦截浏览器后端代理（BFF）并使用确定性事实，不调用真实 Provider、不会产生模型费用。真实 Provider 矩阵仍是单独的显式验收，不能由模拟测试替代。
- 浏览器性能门槛固定为：恢复单页最多100条消息后，目标消息在5000毫秒内可见；刷新后的草稿在2000毫秒内可见。门槛是回归失败条件，不是生产性能承诺。
- 模型提示词缓存命中率、逐调用缓存 Token 和稳定前缀诊断属于阶段6内部诊断；阶段4普通对话页不暴露原始 Provider 请求或缓存敏感细节。

## 2026 年 8 月 26 日新增阶段4工作项

### 快速对话与Agent统一模型上下文

状态：进行中。原子轮次提交、快速Responses消息锚点、Agent Session文本历史、后端System Prompt引用合同、受限文本Artifact测试读取、Context证据抽屉以及Agent/Handoff/Evaluation类型化来源已经实现；普通Web已经移除System Prompt列表请求、选择弹层、已选标签、变量输入、请求引用和运行证据提示。Composer不再提供“上下文”按钮或二级菜单，工具栏“文件”按钮单击后直接打开浏览器原生文件选择器，并支持本地待上传文件删除；上传服务启用前不会读取内容、创建Artifact引用或允许携带待上传文件发送。对象型Message产品接入、真实文件上传、Artifact产品选择和Memory/Knowledge/Tool门禁尚未实现。全部来源、信任、预算、并发锚点和证据合同以[阶段2统一模型上下文设计](../implementation/phase-2-llm-core-capabilities.md#统一模型上下文构建与阶段3阶段4运行时集成设计)为准；阶段4只负责提交用户意图、消费允许公开的后端事实和展示失败，不在浏览器拼接任何上下文来源。

2026年8月27日实现事实：后端`context_policy.v2`已记录来源类型、版本、SHA-256、信任级别、必选标记、token估算和选择结果；Prompt Release固定实际渲染版本，文本Artifact校验Run归属、MIME、100000字节上限、对象大小、哈希与UTF-8，Agent下游节点按明确引用读取Handoff、验证、审核及Evaluation。Provider输入测试确认当前用户目标只出现一次，协作事实也不再同时复制到节点自由JSON。定向测试`70 passed`、全量后端与模型适配层`276 passed, 4 skipped`、Ruff通过；真实Provider浏览器矩阵仍待后续验证。

2026年8月27日历史实现事实：`GET /api/v1/prompt-templates`曾向普通Web返回已启用Prompt的名称、用途、当前版本和变量Schema，Composer曾允许提交版本固定的Prompt引用和变量。2026年8月31日重新确认语义并完成纠正：这些记录是平台System Prompt，不是用户自定义Prompt；普通前端不再允许代理该列表，也不再列出、选择、覆盖或提交Prompt引用，Context证据在前端服务端投影和组件两层过滤`prompt_release`来源。内部管理与后端渲染合同继续保留。Web正式上传已实现，发送前完成全部附件上传与解析；不提供任意Artifact ID或URI输入。

本次验证结果：后端与模型适配层`277 passed, 4 skipped`，4项跳过仍为显式开启的真实基础设施测试；前端`80 passed`，文件选择相关桌面与移动Chromium端到端测试各`2 passed`，ESLint、TypeScript和Next.js生产构建通过。真实Provider浏览器矩阵按用户决定推迟，不计入本次自动化完成判断。

#### 前端数据流

| 步骤 | 快速对话 | Agent Workflow | 前端约束 |
|---|---|---|---|
| 提交当前轮次 | 调用统一轮次提交动作，获得`run_id`、当前`message_id`和序号 | 相同 | 不再先创建无`run_id`的User Message；局部失败按后端事务结果展示 |
| 构建上下文 | 后端为所选Provider/Model组装Session、System Prompt及全部已启用来源 | 后端按节点、角色和模型组装会话、System Prompt、协作证据及全部已启用来源 | 浏览器不选择或提交System Prompt，不发送本地`messages`、Memory、Knowledge、Artifact或Handoff正文，不从页面加载状态判断模型上下文 |
| 启动执行 | `/responses`提交Run、当前输入和受控来源策略，后端在调用Provider前创建Context Build | Workflow请求仍只携带执行配置和角色模型绑定，由后端逐节点构建Context | 当前User输入在Provider请求中只能出现一次；浏览器不负责预先创建Context Build |
| 流式展示 | 继续使用Responses事件合同 | 继续使用Agent Workflow事件合同 | 上下文集成不混用两种事件协议，不改变断线和幂等规则 |
| 运行证据 | Attempt显示Context Build和各类来源纳入/排除数量 | `context_assembly`和各模型节点显示实际Context Build | 默认不展示完整敏感正文；按类型化引用跳转或显示安全摘要 |

#### 界面行为

- 用户继续已有会话时不需要额外开启“使用历史”开关；Session历史是两种对话模式的默认语义。无状态模型调试仍属于独立开发接口，不暴露为容易误用的普通聊天选项。
- Composer继续以模式、Agent配置、模型和发送按钮为主；删除“＋ 上下文”及其弹层，改为同一工具栏上的“＋ 文件”。用户单击一次即打开浏览器原生文件选择器。已选文件以名称、大小和“待上传”状态显示并可删除；真实上传能力完成前禁用发送，不能把本地选择描述为已上传。System Prompt的选择责任属于后端；普通前端不提示，当前快速回复也不在后端策略尚未定义时猜测绑定某个版本。
- 后端返回Context Build失败时保留当前输入并显示可行动错误：模型目录不可用时允许重新选择模型；消息归属或并发锚点冲突时要求重新读取会话；必选输入超过模型上下文时明确说明当前输入无法完整发送。
- Context Build成功而Provider失败时沿用现有Attempt失败和回放行为，不重新提交轮次或创建第二个Context Build。
- 运行详情可以按来源类型展示纳入和排除数量、版本、信任级别、Context策略版本和模型目录版本；Context Build ID只用于内部请求关联，不作为默认界面内容。完整系统指令、安全指令、来源ID、内容哈希、对象URI和其他敏感正文不进入界面。
- Memory、Knowledge和Tool来源没有正式进入Context Builder前，界面不得显示这些来源已被使用；启用后也不提供绕过权限的原始内容入口。

#### 测试与完成条件

- Playwright在真实会话中完成两轮快速对话，第二轮回答能够引用第一轮信息；网络请求和Provider假实现确认当前User文本只出现一次。
- 相同会话切换为Agent模式后，Controller和Worker节点都记录当前Message锚点；刷新后Context证据与最终回答保持一致。
- 两个并发提交不会相互读取对方消息；失败的一次提交不会留下孤立User Message或触发模型调用。
- 历史分页只影响页面展示，不影响后端构建完整的有界上下文；即使浏览器只加载最新一页，Provider输入仍以Context Build为准。
- Context Build重放、Provider失败、SSE断线和Assistant Message保存失败均不产生重复计费调用。
- 桌面、移动端、亮暗主题和屏幕阅读器能够辨认上下文失败状态及运行证据入口。

#### 尚未实现工作项与阶段归属

| 工作项 | 归属 | 阶段4负责的结果 | 明确不负责 |
|---|---|---|---|
| 对象型Message产品接入 | 阶段2补充后端读取合同，阶段4完成产品接入验收 | 进行中：仅有 `content_uri` 的历史 Message 仍标记 `unsupported_object_content`；已实现的上传使用 UserFile/Artifact，不等于已支持对象型 Message | 浏览器不读取对象存储、不解析`content_uri`、不自行校验哈希，也不把不支持对象转为空文本 |
| System Prompt前台隔离 | 阶段4 | 已完成：普通Web不再代理或请求Prompt列表，Composer不再选择、展示或提交Prompt引用，BFF拒绝伪造引用，Context证据过滤Prompt来源；后端选择与渲染策略作为独立后端工作项定义 | 普通浏览器不接收名称、用途、版本、变量Schema、正文或是否纳入；内部管理与审计接口保持受内部密钥保护 |
| Artifact普通产品绑定 | 阶段4 | 基础上传已实现：UserFile 保存原件和固定解析结果，轮次原子绑定并创建 Run Artifact，消息显示附件且刷新可恢复；项目选择和分段引用待实施 | 不开放任意 Artifact ID 或 URI 输入；上传失败保留草稿且不发送缺失附件的提问；不把本机 PDF/Office 验证当作服务器已可用 |
| Context证据抽屉 | 阶段4 | 已完成当前范围：展示允许公开的来源纳入/排除、信任、预算和模型目录事实，同时过滤System Prompt存在性及其全部元数据；失败不遮挡已生成的对话正文 | 不展示完整系统指令、System Prompt存在性/正文/名称/版本/变量、来源ID、内容哈希、对象URI、Handoff全文或其他敏感内容，不把前端推测显示成后端事实 |

System Prompt只能由后端策略选择、固定版本并渲染；当前快速回复在策略尚未定义时不猜测默认版本。普通浏览器不读取候选项、不提交Prompt引用，也不显示是否使用。Artifact在文件上传完成后使用独立的受控选择合同；资源在选择后被禁用、删除、跨Run引用或读取失败时，本轮必须在Provider调用前失败，不能静默移除后继续生成。

Context证据抽屉读取最新Model Attempt关联的已持久化Context Build及来源证据。证据查询局部失败时只让抽屉进入失败状态，不能清空对话正文、把运行改成成功或重新调用Provider；逐Agent Node切换继续使用节点现有证据区域，后续需要时再增加多Context选择器。

真实Provider浏览器矩阵继续作为后续端到端验证项保留。本轮只固定上述三项的归属、产品行为和失败边界，不展开多Provider、多模型、多审核策略或并行Agent组合，也不把矩阵作为上述功能开始实施的前置条件。

## 2026 年 8 月 27 日新增阶段4工作项

### 稳定模型目录与模型选择数据基础

状态：稳定模型目录基线、阶段2后端思考策略扩展及阶段4前端思考控制已完成；真实Provider浏览器矩阵待后续执行。数据库迁移、当前DeepSeek模型的中等默认回填、内部管理校验、目录读取字段、Provider映射、Attempt证据、模型切换归一化和请求透传已经完成。

当前系统的 `llm_model_catalog_versions` 只保存某个 `provider + model + catalog_version` 的不可变能力快照；`GET /api/v1/providers` 则读取启动时由数据库启用地址、服务端环境凭据和模型 allowlist 共同形成的 Provider Registry。两者都不能作为稳定的模型业务实体，也不能表达“平台当前向用户提供哪些模型”。阶段4先补充稳定模型实体表，供 Web 模型选择器和后续阶段5用户授权复用；不在本工作项中提前实现登录、供应商凭据或用户授权。

#### 数据职责

新增表：`llm_models`。一行表示平台登记的一个稳定逻辑模型，不保存任何 API Key、供应商密钥、Prompt 正文或用户授权。

| 字段 | 含义与约束 |
|---|---|
| `model_id` | 稳定模型标识，作为后续引用和用户授权的外键 |
| `provider` | 已登记的 Provider 名称，必须使用系统支持的枚举 |
| `model_identifier` | 供应商侧模型标识；与 `provider` 组成唯一键 |
| `display_name` | 前端展示名称，不参与模型调用匹配 |
| `description` | 可选的人类可读说明 |
| `status` | 平台登记状态：`enabled` 或 `disabled`；禁用采用软状态，不物理删除 |
| `sort_order` | 稳定的前端排序值 |
| `metadata_json` | 非敏感展示元数据；不得写入密钥、完整供应商响应或内部 URI |
| `created_at`、`updated_at` | 审计和缓存失效依据 |

现有 `llm_model_catalog_versions` 保持为能力事实表，迁移完成前通过 `llm_models.(provider, model_identifier) = llm_model_catalog_versions.(provider, model)` 解析最新的已启用能力快照。能力快照继续记录上下文窗口、流式、工具、结构化输出、缓存、视觉、Tokenizer 和思考程度等事实；不把这些字段复制成第二套“模型能力”规则。现有表中的 `provider`、`model` 在迁移完成前保留兼容含义，后续可增加 `model_id` 外键，但不能删除已有 Context Build 和 Attempt 的目录版本引用。

#### 思考策略字段扩展（阶段2已完成）

稳定模型需要新增以下产品策略字段；它们描述平台开放给用户的控制面，不替代Model Catalog保存的供应商能力事实：

| 字段 | 含义与约束 |
|---|---|
| `reasoning_supported_efforts_json` | 有序的统一等级集合，只能使用 `minimal/low/medium/high/xhigh/max`，且已启用模型必须包含 `medium` |
| `reasoning_supports_disable` | 是否允许用户明确发送 `enabled=false` |
| `reasoning_default_enabled` | 产品默认是否启用思考；当前固定为 `true` |
| `reasoning_default_effort` | 产品默认等级；当前固定为 `medium` |
| `reasoning_effort_mapping_json` | 统一等级到Provider原生字段或预算的显式映射；只允许后台验证和内部管理接口写入 |
| `reasoning_profile_version` | 思考策略版本；用于Attempt证据、缓存失效和历史解释 |

- `llm_models` 保存可变但受控的产品策略，Model Catalog保存不可变能力快照；`GET /models`只返回两者交集后可执行的等级和默认值。
- 迁移必须为现有`deepseek-v4-flash`和`deepseek-v4-pro`建立显式中等默认及经验证的Provider映射。映射尚未验证时，记录可以保留但不得报告为支持中等思考的可提交模型。
- 内部登记或更新模型时必须验证：默认启用、默认值为`medium`、`medium`存在于支持集合、所有公开等级都有Provider解析路径，并且禁用能力与适配器一致。
- 请求未显式携带reasoning时，后端仍将稳定模型默认解析进Model Attempt；不能让快速回复、Agent或不同Provider产生不同的隐式默认。
- 修改稳定模型思考策略不得改写历史Attempt；每次调用保存当时的`reasoning_profile_version`、统一配置和Provider解析结果。

#### 模型目录读取规则

新增面向 Web 的只读接口：

```http
GET /api/v1/models
```

响应至少包含 `model_id`、`provider`、`model_identifier`、`display_name`、`status`、`registry_available`、当前已启用的 `catalog_version`、能力字段和思考程度控制能力。结果按 `provider`、`sort_order`、`display_name`、`model_identifier` 稳定排序。

模型选择器只把同时满足以下条件的记录作为可提交模型：

1. `llm_models.status = enabled`；
2. Provider Registry 已注册该 Provider，且环境白名单允许该 `model_identifier`；
3. 存在匹配的已启用 Model Catalog 能力快照；
4. 当前请求的推理程度、流式和输出合同在能力目录中可用。

不满足条件的模型可以作为“不可用/待确认”证据返回，但不能由前端静默切换模型、猜测能力或补写目录记录。现有 `GET /api/v1/providers` 保留兼容，前端迁移到 `/models` 后再评估是否收敛为单一入口。

#### 与阶段5用户授权的边界

本阶段不创建 `user_model_access`，也不把 `user_id` 写入 `llm_models`。阶段5实现最终用户认证后，再增加用户与稳定 `model_id` 的授权关联，并将读取规则扩展为：

```text
当前用户
  → user_model_access 允许
  → llm_models.enabled
  → enabled Model Catalog 快照
  → Provider Registry 可用
  → 返回模型选择器
```

全局 `enabled` 只表示平台登记和能力可用，不表示任何用户自动获得授权。

#### 迁移与初始化

- 迁移只创建 `llm_models`、唯一约束和查询索引，不依赖运行时环境变量，不在迁移中写入供应商密钥。
- 提供受内部密钥保护的模型登记/更新入口；初始化数据由显式 bootstrap 或内部管理动作创建，不能把 `.env` 白名单隐式当作数据库事实。
- 已存在的 `llm_model_catalog_versions` 按 `(provider, model)` 去重生成稳定模型记录；重复版本保留为能力历史，最新已启用版本仅用于当前读取。
- 禁用模型不得被新提交选择，但已有 Run、Attempt、Context Build 和事件仍可查询，不能因目录状态变化破坏历史证据。

实际实现使用 `20260827_0016` 创建和回填表；`POST /api/v1/internal/models/bootstrap` 是唯一从当前Registry白名单显式生成缺失登记行的动作，重复调用不生成重复记录。当前本地开发库已显式登记 `deepseek-v4-flash` 和 `deepseek-v4-pro`，并以2026年8月27日核对的[DeepSeek模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)和[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)官方资料写入各自的启用能力快照；两者在当前Registry中均满足Web流式选择合同。该本地初始化不是迁移中的隐式环境种子，其他环境仍需通过内部管理动作明确登记和确认能力。

#### 实现时采用的设计约束（历史实施输入，非待办）

```text
你是一名负责大语言模型网关、模型目录、用户授权边界和 Web 模型选择器的高级工程师。

请在不破坏现有 API、Context Build、Model Attempt、Agent Workflow 和历史证据的前提下，为 NexusPilot 设计并实现“稳定模型目录”能力。先检查当前 User、Provider Registry、llm_model_catalog_versions、Context Build、Responses、Agent Workflow、Next.js BFF 及迁移；不要假设存在未检查的模型表或用户-模型关联表。

当前事实：
1. users 已存在，但最终用户登录和授权属于阶段5，当前 user_id 仍由受信调用方提供。
2. llm_model_catalog_versions 已存在，它是 provider + model + catalog_version 的不可变能力快照，不是稳定模型业务实体，也不是用户授权表。
3. GET /api/v1/providers 当前来自服务端 Provider Registry；Registry 组合数据库启用地址、环境凭据和环境模型 allowlist，任何一项都不是数据库模型目录。
4. 当前不得把 API Key、供应商密钥、Prompt 正文或完整供应商响应写入模型目录。

目标：
1. 新增 llm_models 稳定模型实体表：model_id、provider、model_identifier、display_name、description、status、sort_order、metadata_json、created_at、updated_at；provider + model_identifier 唯一。
2. 保留 llm_model_catalog_versions 作为能力事实和版本历史，禁止复制第二套能力字段；迁移完成前通过 llm_models.(provider, model_identifier) 与 llm_model_catalog_versions.(provider, model) 对应，解析最新 enabled 快照，并保留历史 Context Build/Attempt 的 catalog_version_id 引用。
3. 提供受内部凭据保护的模型登记/更新接口，以及 GET /api/v1/models 只读接口。
4. /models 返回稳定模型标识、展示字段、Provider Registry 可用性、最新已启用能力和思考程度控制能力，并按确定规则排序。
5. Web 模型选择器只允许提交同时满足模型启用、Provider Registry 可用、能力快照启用和请求合同支持的模型；不可用模型只能显示原因，不能静默切换或猜测能力。
6. 不在本次实现 user_model_access、登录、个人 API Key 或供应商凭据；为阶段5按 model_id 增加用户授权关联保留清晰外键和接口扩展点。

失败与兼容要求：
- 数据库迁移必须支持空库和已有库；重复 provider/model 不能生成重复 llm_models。
- 禁用模型拒绝新提交，但历史 Run、Attempt、Context Build 和事件仍可读取。
- Model Catalog 缺失、Provider 未注册、环境白名单不允许、能力未知和数据库不可用必须返回可区分且不泄露敏感信息的错误。
- 保留 GET /api/v1/providers 的兼容行为，前端迁移完成前不得删除。
- 不从环境变量自动写数据库，不在浏览器暴露平台密钥或供应商凭据。

请先输出：数据模型、迁移与回填策略、接口合同、前后端数据流、阶段5授权扩展点、失败处理和测试矩阵；确认方案后再修改代码。新增或实质修改的函数必须补充用途、参数、返回值、副作用和失败条件注释。至少覆盖迁移、唯一约束、启用/禁用、能力版本选择、Registry 交集、空目录、未知能力、历史证据可读性、BFF 脱敏和模型选择器不可静默切换等测试。
```

#### 实现验收结果

- `llm_models` 的职责与 `llm_model_catalog_versions` 明确分离，字段、唯一键、禁用语义和历史引用规则固定。
- `/api/v1/models` 的返回结构和可选模型判定规则固定，Provider Registry 与数据库目录不再被描述为同一数据源。
- 阶段4前端模型选择器迁移有明确入口和兼容策略；阶段5可以仅通过新增 `user_model_access` 过滤，不需要重做模型目录。
- 迁移、回填、能力版本选择、禁用模型、空目录和安全边界均有实现前测试设计。

## 2026 年 8 月 24 日新增阶段4工作项

### 对话代码块展示优化

2026 年 9 月 12 日状态：代码已修改，当前Chromium检查范围通过。新增`MarkdownCodeBlock`复用现有安全Markdown入口，展示语言标签、独立复制和成功/失败反馈；代码、工具栏与复制按钮为13px，长行在代码区独立横向滚动。复制只取代码正文，保留空白与换行；流式内容更新后不沿用旧内容的复制成功提示。主任务在真实Chromium的1200px桌面和390px移动视口，使用模拟BFF响应检查了中文/缩进/长行精确复制、未闭合围栏显示、失败反馈、键盘横滚与页面无横向溢出。截图见[桌面检查](../../output/playwright/phase234-desktop.png)和[移动检查](../../output/playwright/phase234-mobile.png)。未加入语法高亮；本结果不代表真实BFF、真实Provider或全部跨浏览器完成条件通过。

- 继续复用当前 `MarkdownContent` 安全渲染入口，不创建第二套实时与历史消息渲染器。
- 为围栏代码块增加可见语言标签、复制按钮和复制成功/失败状态；复制内容必须与原始代码一致，不能带工具栏文字或丢失换行。
- 无语言代码块使用中性标签；长行默认横向滚动，代码区不能撑破中央阅读列或移动端视口，并保留缩进、空格和换行。
- 语法高亮属于可选增强。实施前先比较依赖体积和长回复渲染耗时；流式增量阶段使用低开销展示，代码块闭合或消息完成后再按内容哈希进行缓存或高亮，不能在每个 Token 增量上重新解析整条回复。
- 继续禁止执行原始 HTML，不允许复制按钮或高亮组件绕过现有危险链接、远程图片和内容安全边界。

完成条件：实时消息与历史消息展示一致；有语言、无语言、超长行、多代码块、复制原文、亮暗主题、移动端和流式完成切换测试通过；复制按钮具有可访问名称和键盘焦点；代码块不会导致横向页面溢出。

### 2026 年 9 月 12 日本轮验证结果与限制

以下记录区分修复前、删除未完成行后与两项问题修复后的结果。上一轮106项前端测试、ESLint和TypeScript由前端检查协作者执行；后端检查以及本轮117项测试、构建与浏览器检查由主任务执行并提供记录，文档协作者核对相关实现及本地证据位置后同步。有限范围检查通过不代表阶段4整体验收完成。

- 后端与模型测试：`456 passed, 6 skipped`，包括真实隔离MySQL 8多连接轮次去重和Office运行环境；6项跳过不计为通过。
- 修复前历史：前端相关定向测试20项通过，完整`npm test`为88项通过、18项失败，TypeScript检查失败。当时`apps/web/app/api/nexus/[...path]/route.ts:28`存在`if (session) headers.set`未完成行且`session`未定义，尚未获修复授权，因此保留原样；当时真实BFF轮次浏览器验收及生产构建未完成。这些记录不再代表当前检查状态。
- 删除未完成行后的历史：用户当时仅授权删除上述行，主任务据此删除，没有调整来源校验或Run状态策略。在`apps/web`执行`npm test`，106项通过、0失败、0跳过；`npm run lint`与`npm run typecheck`均退出0，主任务执行`npm run build`通过。首次沙箱内测试因tsx本地IPC socket的`listen EPERM`未启动，获准在沙箱外重跑后通过。
- 代码块UI：上述模拟BFF、真实Chromium桌面/移动检查通过；该项自身未验证真实BFF代理路径，也未请求真实Provider，不能与下一项的轮次验证范围混同。
- 删除未完成行后的浏览器历史：主任务通过Playwright访问`http://localhost:3100`的生产前端，经真实BFF调用实际FastAPI路由，使用独立临时SQLite、FakeProvider和内存对象存储。`route.fetch`让首次轮次真实保存后故意中断该次浏览器响应；同key、相同请求体重试返回201→200，`sameRequest`、`sameRun`、`sameMessage`均为true，最终1条User Message、1条Assistant Message、1次Responses请求和1条Attempt，回复即时与刷新后均可见。同key改input返回409，伪造owner返回403；[当时截图](../../output/playwright/bff-turn-retry.png)为本地验收产物，未提交。当轮结束后临时服务与SQLite已清理，并恢复开发生成引用后再次通过TypeScript检查。
- 两项问题的修复前事实：`http://127.0.0.1:3100`同源POST曾因NextURL把主机正规化为localhost，再与原始Origin比较不等而返回403；快速回复虽已保存、Attempt为`completed`，但Run仍`pending`，旧前端仅使用Run/Workflow状态判定活动，刷新后禁发。两问题当时超出仅删一行的授权，因此保留；用户随后明确授权修复，下列结果为当前状态，不再使用上述历史判定未修复。
- 当前实现：`apps/web/app/api/nexus/[...path]/route.ts`按实际Host和请求协议严格比较序列化Origin，不把localhost与127.0.0.1视为同一来源；转发头不新增为可信来源。`apps/web/lib/conversation-submission.ts`仅在当前pending Run无Task、完整列表中恰有一个无Task的completed Attempt、同Run/Session最新User之后有准确引用该Attempt的已保存Assistant、且当前Workflow查询明确404时允许继续发送；活动、未知、查询失败和部分列表继续禁发。`apps/web/app/page.tsx`增加Workflow读取请求代次检查，旧读取不得覆盖当前会话事实。本轮没有修改后端Run状态；一个Run可承载多次模型调用，不能一概随Attempt完成而终止。
- 当前自动化检查：主任务在`apps/web`执行目标测试34项通过，再执行完整`npm test`为117项通过（包含目标34项）；`npm run lint`、`npm run typecheck`与`npm run build`均通过。对最终构建执行`PLAYWRIGHT_BASE_URL=http://127.0.0.1:3100 npm run test:e2e`，桌面/移动20项通过；这20项使用模拟BFF，不替代下列真实代理路径检查。
- 当前真实BFF轮次与续聊：生产前端通过`http://127.0.0.1:3100`访问真实BFF及实际FastAPI路由，使用独立临时SQLite、FakeProvider和内存对象存储。首次轮次已提交后丢失浏览器响应，同key重试201→200、复用同Run与同User Message，仅一次Responses请求；同会话随后即时续聊，再刷新后续聊，共3条User、3条Assistant、3次Responses、3个Run各1条completed Attempt。Run仍为pending，所有已保存Assistant的`source_model_attempt_id`均与对应Attempt匹配，回复保存且可继续发；[续聊截图](../../output/playwright/quick-response-continued.png)为本地验收产物。
- 当前来源与失败检查：真实BFF收到从localhost向127.0.0.1发送的跨别名Origin时返回403；localhost入口在最终构建下另实际发送，Session201、轮次201、Responses200、Assistant201，回复保存且仍可继续发。将Workflow查询注入503时禁发，恢复真实404后恢复可发，没有把查询失败当作无Workflow。
- 旧读取结果检查：延迟旧会话A的真实Workflow 404，待新会话B的404查询完成后再释放A，B仍可继续发。请求代次检查用于防止静态检查发现的旧结果覆盖风险；修复前未稳定复现浏览器故障，不把本项写成旧浏览器失败的复现记录。
- 证据与范围：`output/playwright/bff-repair-verification.js`、`output/playwright/stale-workflow-check.js`及上述续聊截图均为本地忽略文件，不是提交产物。本轮两项修复没有后端修改、没有开发数据库迁移，没有真实Provider调用，也未验收真实MinIO、生产数据库、转发头可信代理或HTTPS部署，不能称为公网安全验证通过。
- 最终检查与清理：主任务对最终代码再次执行117项测试通过，并重拍查看续聊截图，确认3条回复可见且`canSend=true`；临时Next、FastAPI及专用浏览器已关闭，临时SQLite随服务生命周期清理。通过Next生成器保留原有`next-env.d.ts`开发引用后，最终TypeScript和ESLint再次退出0。
- 剩余工作：跨刷新/再次点击key持久化、完整模型执行及跨进程恢复仍未实现；正式上传、完整前端证据视图和其他本节列明的未验收范围保持原状态。阶段4仍为进行中，不能以此次构建和限定环境浏览器检查代替整体验收。

### Composer 快捷键与最小化

状态：已完成。

- 先前取消 Enter 发送的需求已于 2026-10-13 被用户新要求替代：只在消息输入框获得焦点时，普通 Enter 提交；Shift+Enter 换行。输入法组合确认、229 兼容键码、长按重复、Command/Control/Alt 修饰键不触发发送。事件仅绑定 textarea，不注册全局发送快捷键。
- 保留显式发送按钮。最小化隐藏编辑和配置控件但保留草稿及待传附件，显示展开按钮；运行中仍可停止。展开后焦点回到输入框，再次提问也会展开输入框。
- 输入区已删除运行机制解释文案，执行模式、Agent 并行度、审核策略分布在左侧，模型和发送动作分布在右侧；原生下拉菜单替换为可访问的项目列表。
- Textarea 根据内容自动增高，达到 320px 后才使用内部滚动；会话底部间距根据 Composer 实际高度动态更新，长回复的 `message-actions` 始终可滚动到 Composer 上方。
- 保留空白输入拒绝、生成中禁用重复提交、发送按钮状态和现有幂等/恢复边界；没有改变请求合同或消息持久化顺序。

完成条件：仅聚焦输入框时 Enter 提交、Shift+Enter 换行；输入法确认不误发；最小化不清空草稿、附件或中止运行；空白文本与生成中重复提交仍被拒绝；最后回复的操作按钮可滚动到输入面板上方。当前失败轮刷新后显示明确失败占位，不将其标为已保存的模型回答。

## 新增项目导航与附件发送工作项（2026 年 9 月 5 日，未开始）

2026 年 9 月 12 日实施顺序：先补齐阶段2—4基础功能。正式上传已实现，下述项目文件页面仍未实现；旧的“附件必切 Agent”方案已被两种模式均可读附件的需求替代，不再作为未来默认实施要求，也不新增附件专用 Agent。当前本地选择显示待上传，发送时上传并解析；不新增文件工作流入口。

跨阶段数据、存储和权限合同以[文件上传与个人项目工作区设计](../architecture/platform-roadmap.md#文件上传与个人项目工作区设计)为准。项目与附件专用编排仍是设计，基础上传按文首当前实现运行；不恢复会话归档。

- 左侧顺序：新对话 → 项目分类（可折叠、创建项目、分页项目列表）→ 最近会话 → 设置/当前访问主体。项目行只显示名称与目录可用状态，折叠时不加载所有项目内会话。
- 点击项目进入 `/p/[projectId]` 项目工作台，包含项目会话、文件、运行产物三个视图；项目内新对话固定该 `project_id`，普通新对话不强制创建项目。已有 `/c/[sessionId]` 仍是对话稳定地址，从服务端 Session 恢复项目范围，不能靠左侧选中项重写历史归属。
- 项目文件页区分“上传资料”（MinIO 原件）、“工作目录”（本地主机文件）和“运行产物”，不能用一个无状态列表冒充自动同步。文件行使用类型图标、名称、大小/版本/状态和独立操作菜单；行距、细分隔线、键盘焦点及移动布局复用现有控件，不使用未设计的原生弹出列表或粗边框。
- Composer 单击文件选择后展示逐文件上传进度/失败重试/移除；服务端版本可用后才允许发送。文件已上传但未发送可以从后端列表恢复；只存在浏览器内的待上传 File 在刷新后需要重新选择，不能仅恢复文件名就称作已上传。
- 本轮附件不改变用户选择的快速回复／Agent 模式，两种模式都可使用服务端授权的只读工具；Agent 内部执行配置与产品模式分开。上传不自动发送，移除附件不改变模式。附件／解析失败保留草稿并阻止不完整提交，用户明确移除失败项后才发送剩余项；不恢复“有附件必须切 Agent”的旧设计。
- 项目切换不取消活动 Run，不改动证据检查器目标。草稿键扩展为 owner/project/session，并保留上传版本引用；跨用户/项目引用在服务端拒绝。项目内运行仅选取显式文件，不自动注入整个目录或其他会话历史。
- 文件详情显示版本、解析覆盖范围及页码/行号来源；工作目录离线、文件变化、删除或失权均显示对应状态。首次登录不出现他人或旧本地 owner 的项目；暂不提供共享、归档或任意绝对路径输入。
- 验收：桌面/移动导航、分页与空状态、首个带附件回复、部分失败、刷新恢复、有效模式与请求一致、跨项目切换、长文件名与列表样式、撤销/删除后的明确错误；模拟测试与真实 MinIO 浏览器验证分别记录。

## 测试设计

- 组件测试：空状态、消息角色、Markdown、代码块语言标签、复制原文、错误提示、生成中与完成状态。
- SSE 测试：sequence 顺序、重复事件、缺失终止、失败事件、取消和断线。
- 页面集成测试：新建会话、追加用户消息、普通响应、流式响应、保存 Assistant Message、打开 Attempt 详情。
- 会话可靠性测试：按Session隔离草稿、刷新恢复、持久化成功后清除、标题归属与长度校验、“再次提问”不提交、同会话多标签页互斥和完成后事实刷新。
- 上下文测试：第二轮快速对话、Agent消息锚点、当前输入单次出现、历史分页独立性、Prompt版本、Memory/Knowledge/Artifact/Agent来源门禁、信任标记、并发提交隔离、Context Build重放和必选来源超限不调用Provider。
- 上下文产品测试：对象型Message的允许类型与失败恢复、System Prompt名称/版本/变量/存在性均不进入普通浏览器、浏览器请求不携带Prompt引用、Artifact归属与状态变化、允许公开的Context证据，以及证据查询失败不遮挡对话。
- 安全测试：浏览器响应和静态资源不包含平台 API Key、内部 API Key、供应商凭据或 MinIO URI。
- 可访问性测试：聚焦输入框时 Enter 发送/Shift+Enter 换行、显式按钮发送、输入法组合、最小化与焦点恢复、屏幕阅读器状态、颜色对比和 reduced motion。
- 响应式测试：桌面侧栏、移动端覆盖层、长代码块和长单词不会破坏布局。
- 可复现浏览器测试：桌面与移动Chromium覆盖草稿、重命名、“再次提问”、只读模型目录、路由不存在边界、同会话多标签页互斥和100条消息恢复门槛；默认使用确定性BFF响应，不产生Provider费用。
- 恢复测试：刷新页面、SSE 中断、保存失败和重复提交不会重复产生计费调用。
- 阶段10测试：重复/乱序任务事件、有限重试、死信、Worker 崩溃、取消竞争和刷新恢复。
- 阶段7测试：工具提议与执行区分、允许/拒绝、批准过期、重复批准、工具超时、部分 Artifact 和敏感输出脱敏。
- 阶段3当前测试：八个接口合同、取消代理与跨用户拒绝、并行组、费用预留、SSE 解析、事件顺序/重复/缺口/有限回放、DeepSeek 结构化输出模式和生产构建已覆盖。真实 Provider 下的 JSON/SSE 创建、幂等回放、Run 冲突、11 类节点数据、审核策略、预算、失败/未知结果和刷新恢复继续作为端到端验证。
- 阶段8测试：workspace 越权、路径穿越、长行、二进制文件、索引过期和部分搜索结果。
- 阶段9测试：MCP 连接失败、认证过期、能力变化、调用超时、撤销和资源大小限制。
- 阶段6测试：无追踪、采样、部分/乱序 span、遥测延迟和敏感属性过滤。
- 阶段5测试：登录、退出、Cookie、跨站请求伪造防护、会话撤销、个人 API Key 一次展示、供应商凭据轮换和跨用户拒绝。

## 实施工作项

| 对应后端检查点 | 前端工作项 | 依赖 | 完成条件 | 状态 |
|---|---|---|---|---|
| 阶段1 | 会话外壳、完整消息分页、Run/Task/Attempt/Retry/Artifact 证据和允许动作 | 阶段1已完成的安全查询 API | 归属、分页、局部失败、刷新恢复和浏览器脱敏测试通过 | 进行中 |
| 阶段2 | Provider/Model、普通响应、SSE、共享Context Build及允许公开的Context/Evaluation证据；System Prompt不进入普通产品 | 阶段2已完成的LLM核心API及新增运行时Context关联 | 两轮会话、不同模型窗口、断流、费用、能力拒绝和证据版本测试通过；Context当前范围已实现，完整Evaluation视图后续 | 进行中 |
| 阶段2 + 阶段4 | 对象型Message受控读取与普通对话接入 | 阶段2对象读取合同、ObjectStorage受控访问和统一Context Builder | 当前明确拒绝测试通过；对象型 Message 的允许类型及恢复仍待实施，正式 UserFile 上传不能代替此检查 | 进行中 |
| 阶段4 | System Prompt前台隔离与Context证据抽屉 | 已实现的Prompt Release、`context_policy.v2`和脱敏Context证据接口 | 普通Web不请求或展示System Prompt候选/元数据、不提交Prompt引用；证据过滤System Prompt存在性；用户隔离、敏感正文隔离和局部失败测试通过 | 已完成 |
| 阶段4 | Artifact普通产品绑定 | 正式 UserFile 上传、原子轮次绑定、Run Artifact 与统一解析内容 | 基础上传、下载/删除、消息附件刷新恢复已验证；目标服务器文字对话通过；项目文件选择和分段引用未实现，PDF/Office 部署限制见部署记录 | 进行中 |
| 阶段2 + 阶段4 | 稳定模型目录、数据库模型实体、Provider Registry 交集和 `/models` 选择数据 | `llm_models`、`llm_model_catalog_versions`与Provider Registry | 空库/存量库回填、唯一约束、启用/禁用、能力缺失、稳定排序、BFF固定路由和前端不可静默切换测试通过 | 已完成 |
| 阶段3 | 已接入`model_only_v1`创建/发现/结果/节点/事件、Agent/Turn/Handoff、验证、审核、取消、并行组、费用预留、Session历史及协作类型化Context来源 | 后端八个接口、完整节点合同和阶段2共享Context Build；既有BFF归属保护，工具视图另等阶段7 | 不同角色模型Context、真实Provider JSON/SSE、幂等、失败/未知结果和刷新恢复测试通过后完成 | 进行中 |
| 阶段4 | Next.js、BFF、主题、响应式、标题重命名、标签页草稿、同会话提交互斥、再次提问、只读模型目录、路由边界、代码块工具栏、轮次幂等提交、仅按钮发送、可访问性和性能 | 后端接口与稳定开发环境 | 早期 106/117 项记录保留；正式上传后的 121 项前端测试和构建记录见文首及部署记录，目标 HTTPS 新回复与刷新保留通过。跨刷新 key、完整执行恢复及完整部署矩阵未完成，归档/Memory/真实 Provider 矩阵后置 | 进行中 |
| 阶段5 | 登录、会话安全、个人 API Key、供应商凭据与多用户授权迁移 | 阶段5身份/凭据 API 和资源授权完成 | Cookie/Bearer、安全存储、撤销、跨用户和公开发布端到端测试通过 | 进行中 |
| 阶段6 | 关联标识、耗时分解和内部诊断页 | 阶段6完成公开/内部遥测分层 | 业务事实不被追踪覆盖；敏感字段扫描和缺失追踪测试通过 | 未开始 |
| 工具能力（原阶段7索引） | 调用、批准、结果、创建/验证与保存界面 | 工具主计划按工作项同时实施后端与前端 | 按主计划统一验收；本表不另计完成状态 | 未开始 |
| 工具来源（原阶段8索引） | 代码搜索与可复核引用 | 工具主计划后续代码检索工作项 | 随该工具验收，不独立排期 | 未开始 |
| 工具来源（原阶段9索引） | MCP 连接与受控工具 | 工具主计划后续外部来源工作项 | 随连接及工具一起验收，不独立排期 | 未开始 |
| 阶段10 | 异步 Task 状态、Worker 时间线、有限重试、死信、取消与恢复 | 阶段10完成并提供公开状态/事件合同 | 故障窗口、乱序、重复事件和刷新恢复测试通过 | 未开始 |

阶段1和阶段2的后端状态已经是“已完成”，上表的“进行中”只表示对应前端集成尚未满足自己的完成条件，不更改后端阶段状态。

## 第一版完成标准

以下条件只表示阶段1、阶段2和阶段4前端基础形成可验证的内部对话版本，不表示阶段6～10能力已经存在，也不表示阶段5公开发布门禁已经通过。

- 用户可以创建和选择会话、查看消息历史，并通过同一界面完成普通或 SSE 流式模型调用。
- Provider 与模型列表来自后端注册事实；前端不硬编码供应商可用状态或可选模型。历史 Run 的 Provider/Model 只作为证据展示，不覆盖当前 Composer 选择。
- 页面刷新后能够从 Session、Run 和 Attempt 恢复已持久化状态。
- 失败、取消和断流不会被显示为完成，也不会自动产生第二次模型费用。
- 平台 API Key 和内部 API Key 只存在于 Next.js 服务端配置，不进入浏览器。
- 第一版不读取 Memory、不创建 Memory Packet、不读取 Knowledge，也不展示虚假的 Agent 或工具执行状态。
- 桌面和移动端关键流程通过组件、集成、可访问性和响应式验证。
- 文档、界面文案和实际 API 状态一致；尚未实现的 Agent/Tool 功能明确显示为未提供，而不是空白演示数据。
