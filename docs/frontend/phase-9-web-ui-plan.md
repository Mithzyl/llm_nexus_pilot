# 阶段9：NexusPilot Web 前端规划

**文档日期：** 2026 年 8 月 10 日
**文档状态：** 进行中（第一版前端工程与核心对话外壳已实现；阶段5 `model_only_v1` 第一版前端合同、代理、提交模式和节点时间线已实现，真实 Agent API 端到端验收待完成）
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)
**数据与控制平面依据：** [`phase-1-foundation.md`](../implementation/phase-1-foundation.md)
**LLM 核心能力依据：** [`phase-2-llm-core-capabilities.md`](../implementation/phase-2-llm-core-capabilities.md)
**异步执行依据：** [`phase-3-rabbitmq-task-execution-plan.md`](../implementation/phase-3-rabbitmq-task-execution-plan.md)
**身份与凭据依据：** [`phase-10-platform-identity-credentials.md`](../implementation/phase-10-platform-identity-credentials.md)
**Agent 工作流依据：** [`phase-5-agent-workflow-plan.md`](../implementation/phase-5-agent-workflow-plan.md) 及实际 Router、Schema 和测试
**UI 设计基线：** [NexusPilot 阶段 9 Sites 预览](https://nexuspilot-phase9-preview.ryngarzheng.chatgpt.site)

## 目标

- 建设以对话为主要入口的 Web 应用，采用类似 ChatGPT 的清晰布局与渐进式交互，但不复制其品牌、图标、文案或视觉资产。
- 第一版只消费已经验证的 User、Session、Message、Run、Task、Provider、Responses、Model Attempt 和 Artifact API。
- 普通生成与服务器发送事件（SSE）流式生成使用同一消息界面；用户可以看到生成中、完成、失败和取消状态。
- 为当前 `model_only_v1` 提供可解释的 Agent、步骤、Handoff、审核和验证轨迹；工具执行继续等待后续合同。
- 运行状态必须来自后端事实或事件，不根据前端定时器伪造“正在思考”“正在调用工具”等状态。

## 明确不处理

- 第一版不消费实验性 Memory 或 Knowledge 接口，不展示 Memory Profile、Memory Packet 或知识库检索结果。
- 第一版不实现 RabbitMQ、Worker、Agent Runtime、工具执行、MCP 或 OpenTelemetry；只为它们保留界面扩展点。
- 不把模型流中的 `response.tool_call.delta` 显示成“工具正在执行”。该事件只表示模型提出了工具调用参数；只有未来后端产生正式 Tool Call 执行事件后才能显示执行进度。
- 不在浏览器中保存平台内部 API Key、供应商 API Key、MinIO URI 或未脱敏原始供应商响应。
- 不在阶段9顺便建设项目看板、生活管理、邮件、日历或其他超级 App 功能。

## 当前后端事实与前端边界

当前公共认证是受信服务调用方级 API Key，不是最终用户登录。因此第一版前端只能作为开发/内部使用界面，采用后端为前端（Backend-for-Frontend，BFF）的 Next.js 服务端转发层保存平台 API Key：

```text
Browser
  ↓ same-origin request
Next.js Server / Backend-for-Frontend
  ↓ server-side X-API-Key
FastAPI
```

浏览器不能直接持有 `X-API-Key` 或 `X-Internal-API-Key`。正式面向多用户发布前，必须另行完成登录主体、Session Cookie、跨站请求伪造防护、用户与资源授权映射及退出失效策略。

阶段10规划的 `/auth/login`、`/auth/logout`、`/auth/me`、会话撤销、`/me/api-keys` 和 `/me/provider-credentials` 均不在阶段9当前内部开发版中提前实现。阶段9可以继续验证对话交互，但公开发布门禁要求阶段10完成后由 FastAPI 当前主体决定 `user_id`，Next.js 不再注入固定开发用户或部署级公共 key。

当前内部版已经确认的后端能力：

| 前端用途 | 后端资源 | 前端行为 |
|---|---|---|
| Provider 选择 | `GET /api/v1/providers` | 只展示服务端实际注册的 Provider；模型值来自配置或后续 Model Catalog |
| 会话列表与详情 | Session 查询接口 | 左侧栏分页加载，cursor 与筛选条件绑定 |
| 消息历史 | Session Message 接口 | 按不可变顺序读取，不在前端改写历史消息 |
| 创建运行与任务 | Run、Task 接口 | 每次需要可审计模型执行时创建对应 Run；任务信息进入详情抽屉 |
| 普通与流式生成 | `POST /api/v1/responses` | 发送显式输入；不请求 Memory、Knowledge 或 Context Preview 自动注入 |
| 调用详情 | Model Attempt 和 Retry 接口 | 展示 Provider、Model、状态、token、费用、时长与安全错误 |
| 生成产物 | Artifact 接口 | 展示元数据并通过受控内容接口下载，不暴露 MinIO 对象键 |
| 运行审计 | Run、Task、内部审计接口 | 仅内部开发/运维视图使用，不默认暴露给普通用户 |
| 模型 Agent 工作流 | Agent Workflow 创建、发现、结果、节点和事件回放接口 | 只在内部开发功能门禁下启用 `model_only_v1`；完整节点查询是权威事实，事件只驱动增量提示 |

Context Preview、Prompt/Model Catalog 和 Evaluation 在阶段2完成行为验收后，可以加入开发者抽屉；它们不应阻塞第一版聊天界面，也不能被前端自行组合成第二套调用链。

### 前端实施前必须处理的后端缺口

- 当前 Session Message、Run 和 Task 创建接口没有统一幂等键，浏览器超时后不能安全地自动重试整条“发送消息”编排。开始实施前应增加稳定的 `client_request_id`/幂等合同，或提供一个由后端事务协调的聊天提交接口。
- `/responses` 不会自动追加用户或 Assistant Message。第一版 BFF 可以显式编排，但必须记录每一步结果并处理部分失败，不能把多次 HTTP 调用描述为原子事务。
- 当前只有平台 API Key，没有最终用户登录和资源授权。公开部署前必须补齐认证；开发版只能由服务端持有密钥。
- 当前 `/responses` SSE 可以传输模型生成状态，但没有跨刷新恢复游标。第一版刷新后以 Model Attempt 最终状态为准；阶段5 Agent Workflow 另有持久化事件序号和有限回放，两种 SSE 合同不能混用。
- 当前 `response.tool_call.delta` 不是工具执行事实。正式工具时间线必须等待 Tool Call API 和运行事件合同。
- 阶段5当前没有独立取消、恢复、等待用户输入、进程重启恢复、并行 Agent、费用预留或大型节点结果 Artifact 降级接口。前端不能提前显示这些动作，也不能把有限事件回放描述为持续订阅。

## 后端阶段检查点驱动的前端交付设计

阶段9是一个持续消费后端稳定能力的前端阶段，不把每个后端阶段重新编号成新的前端阶段。后端阶段达到完成门禁，只表示对应前端工作可以开始；只有前端合同、交互、失败恢复、安全和浏览器验证也通过后，该前端检查点才算完成。

### 检查点使用规则

- 后端阶段状态以总体规划为准，前端不得因页面已有占位区域就声称后端能力可用。
- 后端阶段尚未完成时，对应导航和动作默认隐藏；需要内部联调时可以显示明确的“开发预览”，但不能使用模拟运行状态混入正常会话。
- 前端只消费稳定的公开查询、动作和事件合同，不读取数据库、RabbitMQ、内部对象存储地址或内部审计原始载荷拼装用户状态。
- 每个可增长历史都使用后端 cursor；每个实时状态都需要稳定事件顺序或可查询最终事实；刷新后不得依赖浏览器内存恢复最终状态。
- 后端状态枚举、允许动作、权限和错误分类是事实源。前端可以转换成人类可读文案，但不能发明第二套状态机。
- 阶段10完成前，阶段9只作为内部开发界面；任何最终用户公开发布都必须同时通过阶段10的身份、授权和凭据门禁。

### 总体映射

| 后端检查点 | 后端交付能力 | 对应前端交付 | 主要界面 | 前端启用条件 | 当前前端状态 |
|---|---|---|---|---|---|
| 阶段1：数据与控制平面 | User、Session、Message、Run、Task、Attempt、Retry、Artifact 和安全查询闭环 | 会话历史、运行证据、分页、取消/重试入口和产物元数据 | 会话侧栏、对话页、运行详情页 | 公开响应不含内部 URI；归属、cursor、允许动作和稳定错误合同通过 | 进行中 |
| 阶段2：LLM 核心能力 | Provider、Responses、SSE、Context Preview、Prompt/Model Catalog、Evaluation | 模型选择、普通/流式生成、上下文与评估证据开发视图 | Composer、模型选择器、证据抽屉 | 终止事件、用量、错误、能力目录和证据字段稳定；Memory/Knowledge 不进入调用链 | 进行中 |
| 阶段3：异步任务执行 | Outbox、RabbitMQ、Worker、幂等、有限重试、死信和恢复 | 排队任务状态、Worker 执行时间线、取消、重试和刷新恢复 | 任务时间线、运行详情、失败恢复操作 | Task 状态机、允许动作、公开事件和恢复 cursor 完成并通过故障窗口测试 | 未开始 |
| 阶段4：工具能力 | 工具契约、权限、文件、搜索、Shell、Git 和调用证据 | 工具提议、批准、执行结果、失败和 Artifact 展示 | Assistant 执行过程、批准对话框、工具详情 | 工具执行事实、风险等级、批准合同和脱敏结果稳定 | 未开始 |
| 阶段5：Agent Runtime 与工作流 | 当前已实现 `model_only_v1` 串行模型工作流、完整节点合同和持久化事件；并行、工具、外部取消/恢复仍未实现 | 当前先交付 Agent 分工、节点结果、Handoff、验证、审核和预算只读证据；后续再增加动作与工具 | Run 下的 Agent 时间线和工作流详情 | 当前查询/事件合同可开始内部接入；取消/恢复、并行、工具和公开发布分别等待对应后端门禁 | 未开始 |
| 阶段6：代码搜索增强 | 文件索引、ripgrep、Tree-sitter 和可选语言服务器协议（LSP）证据 | 文件/行引用、定义与引用关系、索引新鲜度 | 代码证据抽屉、搜索结果视图 | workspace 归属、安全路径、稳定定位和有界结果合同通过 | 未开始 |
| 阶段7：MCP Client | 连接、能力发现、工具、资源、认证、超时和权限 | MCP 连接管理、能力目录、资源选择和受控调用 | MCP 设置页、能力面板、调用详情 | 连接身份、权限、超时、撤销和审计合同稳定 | 未开始 |
| 阶段8：可观测性 | API、模型、消息、Worker、工具和 Agent 的关联链路 | 关联标识、耗时分解、健康状态和内部诊断 | 运行诊断页、内部运维视图 | 公开/内部字段分层完成；追踪不含密钥和完整敏感内容 | 未开始 |
| 阶段9：Web 前端 | BFF、对话外壳、响应式、主题、可访问性和前端验证体系 | 承载各后端检查点的统一交互面 | `apps/web` 全部页面 | 只启用已通过对应后端检查点的能力；浏览器边界和真实状态门禁持续通过 | 进行中 |
| 阶段10：身份、授权与凭据 | 登录主体、浏览器会话、个人 API Key、供应商凭据和资源授权 | 登录/退出、会话管理、密钥与供应商凭据设置，移除固定开发用户 | 登录页、账户安全、API Key 与 Provider 凭据页 | Cookie/Bearer、跨用户隔离、跨站请求伪造防护、撤销和加密门禁全部通过 | 未开始 |

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
- Context Preview、Prompt Render 和 Evaluation 进入独立开发者证据抽屉。Context Preview 只展示显式 instruction、安全 instruction 和 Session Message 来源；Evaluation 必须把执行状态与 verdict 分开。
- Memory 和 Knowledge 不出现在模型调用开关、来源列表或默认导航中；前端不得调用实验性 Memory 接口补充上下文。

**失败与恢复：**

- 供应商未配置、能力不支持、输入校验失败、超时和供应商暂不可用使用不同可操作文案，但不展示原始供应商响应。
- SSE 缺失终止事件、断线或取消时保留部分文本并标记未完成；只读取最终 Attempt 状态，不自动产生第二次计费请求。
- 模型响应完成与 Assistant Message 保存是两个状态；保存失败保留内容并指向 Run/Attempt，不把模型成功改写成整体失败。

**前端通过条件：**

- 普通响应与 SSE 具有一致的完成、失败、用量和 Attempt 关联测试。
- Context、Prompt、Model Catalog 和 Evaluation 视图只显示后端证据，历史版本和未知能力行为与阶段2合同一致。
- 长响应按批次刷新，不能每个 token 重新解析全部 Markdown；生成期间页面交互和滚动保持可用。

### 阶段3检查点：异步任务与可靠恢复

**前端范围：**

- 在同步模型调用之外增加异步 Task 提交与运行视图；界面区分“已接受”“等待 Worker”“执行中”“等待重试”“最终失败”“已取消”。
- 运行时间线消费公开任务事件或查询事实，展示任务编号、当前状态、已确认的尝试次数、下一步用户动作和公开错误摘要；不展示 RabbitMQ exchange、routing key 或消息原文。
- 用户取消只发起协作式取消请求；允许重试时调用后端受控动作，不在浏览器复制 Task 或重新提交执行规格。
- 刷新、断线和跨设备打开同一 Run 时，使用事件恢复 cursor 或 MySQL 查询事实恢复；浏览器不缓存未确认的 Worker 状态作为事实。

**后端合同门禁：**

- 必须提供稳定 Task 状态枚举、状态版本或更新时间、允许动作、最终错误分类和有界重试事实。
- 公开事件至少能够按 Run/Task 关联、按 sequence 排序并从 cursor 恢复；如果阶段3只提供查询而没有事件，前端使用有界退避刷新并明确这是查询状态，不伪装实时流。
- 死信和 Publisher 故障先转成可公开的任务失败/运维状态，浏览器不能直接消费 RabbitMQ 管理接口。

**前端通过条件：**

- 覆盖重复投递、乱序事件、Worker 崩溃、有限重试、死信、取消竞争和刷新恢复。
- 同一 Task 的重复事件不会重复追加时间线或重复触发用户通知。
- 后端阶段3未完成前，仅保留静态布局接口，不显示虚假的 Worker 进度。

### 阶段4检查点：工具调用、批准与产物

**前端范围：**

- 明确区分“模型提出工具调用”和“工具执行已经开始”。只有正式 Tool Call 执行事实才能进入运行时间线。
- 工具卡片展示人类可读名称、风险等级、有界输入摘要、批准状态、执行状态、持续时间、安全结果摘要和 Artifact 链接。
- 高风险文件、Shell、Git 或外部写操作进入统一批准对话框；对话框说明动作对象、影响范围、是否可恢复和拒绝后的运行行为。
- 文件内容、命令输出和搜索结果默认折叠并截断；用户主动展开时仍遵守权限、大小和脱敏上限。

**后端合同门禁：**

- Tool Call 必须具有稳定标识、所属 Run/Task/Agent、工具版本、风险、批准要求、输入摘要、状态和公开结果。
- 后端必须决定批准的并发与过期语义；前端不能用本地按钮状态代替一次性批准令牌或状态条件更新。
- 禁止目录、命令和权限拒绝由工具层保证；前端隐藏按钮不能作为安全控制。

**前端通过条件：**

- 覆盖允许、拒绝、批准过期、重复批准、执行超时、部分产物、取消和跨用户访问。
- 页面不显示完整 Shell 环境、secret、未脱敏命令输出或内部文件系统根路径。

### 阶段5检查点：Agent Runtime 与工作流

阶段5前端交付分成“当前已实现”和“后端补齐后启用”两个范围。当前代码已包含 `model_only_v1` 的串行模型工作流、完整节点结果、持久化事件及第一版前端消费；阶段5总体仍是进行中，尚未实现的动作和执行配置不得提前出现。

#### 当前可实施能力

- 在已有 `/runs/[runId]` 页面增加“Agent 工作流”页签，不创建独立的 Agent 管理页面。Run 与 Workflow 是一对一关系，先按 Run 发现 Workflow，再读取摘要、结果和节点。
- 在对话 Composer 增加受内部功能门禁保护的“Agent 工作流”提交模式；普通 `/responses` 模式保持不变，不能静默改用 Agent Workflow。
- 展示 Controller 规划、工作 Agent、Task、Agent Run、Agent Turn、Handoff、确定性验证、可选 Reviewer、最终汇总和完成节点；所有显示内容来自节点完整合同或事件公共投影。
- 展示节点累计 token、估算费用、模型调用次数、节点数、剩余模型调用、剩余节点和剩余总时长。费用字段是后端已记录事实，但当前没有费用预留，界面不得写成“保证不会超出预算”。
- 工作 Agent 当前按拓扑顺序串行执行；界面使用纵向时间线，不展示并行泳道。`max_parallel_agents` 固定显示为 `1`，不提供可编辑并发控件。
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

当前没有可调用的 `/cancel` 和 `/resume`，也没有提交等待用户输入的接口。前端只能在初始 POST SSE 仍连接时提供“停止本次流式请求”；该动作取消传输并等待后端收敛状态，不能描述为独立、可跨刷新恢复的取消命令。

#### 创建请求设计

- Agent Workflow 请求本身不包含用户目标；后端从所属 Run 的 `user_request` 读取目标。因此 Agent 提交模式必须先得到包含当前显式输入的 `run_id`，不能把 Composer 文本临时附加到 Workflow 请求或复用目标不一致的旧 Run。
- 当前 Message/Run 创建与 Workflow POST 不是一个端到端幂等事务。浏览器只有在拿到确定的 `run_id` 后，才能依赖 Workflow `idempotency_key` 安全回放；User Message 或 Run 创建发生超时且结果未知时不得自动重建，应保留输入并显示对应部分失败。
- Run 带有 `session_id` 时，阶段5后端会把最终 Assistant Message 与 `final_synthesis` 节点完成事实放在同一事务提交。Agent 模式不得沿用普通 `/responses` 流程再次保存 Assistant Message；前端在完成后读取 Result 和 Session Message 事实。
- `workflow_name`、`workflow_version` 和 `execution_profile` 固定为 `model_only`、`1.0.0` 和 `model_only_v1`，在内部高级设置中只读展示，不允许任意文本输入。
- 每次用户明确提交生成一个 `8`～`128` 字符的 `idempotency_key`，并在该提交恢复完成前持久保存在当前页面状态中。请求超时或断线后先发现原 Workflow；不能生成新 key 自动重试模型调用。
- `role_bindings` 只允许 `controller`、`planner`、`researcher`、`reviewer`、`verifier`。必须包含 Controller 和至少一个 Planner/Researcher；`review_policy` 不是 `never` 时必须包含 Reviewer。Verifier 当前不会创建模型节点，应隐藏在默认表单，只在开发者合同测试中保留类型支持。
- 每个角色绑定展示 Provider、Model、结构化输出模式、超时和最大输出 token。Provider/Model 只能来自阶段2已经验证的能力事实；不支持的结构化输出模式必须在提交前禁用并说明原因。
- `review_policy` 支持 `always`、`on_verification_failure`、`never`。`max_nodes` 按审核策略最小为 `10` 或 `11`、最大 `32`；`max_model_calls` 最小为 `3` 或 `4`、最大 `16`；`wall_time_limit_ms` 范围为 `1,000`～`600,000`；`max_parallel_agents` 固定为 `1`。
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

#### 完整节点结果展示

每个节点先使用统一信封渲染：`public_view`、状态、输入来源引用、转移结果、证据 ID、用量、剩余预算、时间、警告和安全错误。节点特定输出按 `output_type` 使用穷尽类型分支，未知 `output_type` 显示“当前前端不支持此合同版本”并保留通用信封，不能猜测字段。

| `output_type` | 默认展示 |
|---|---|
| `request_intake` | 规范化目标、任务复杂度、约束、验收条件、假设与不可用能力 |
| `context_assembly` | 纳入的 Message/Artifact/Handoff 引用、排除原因、字符数和是否截断 |
| `controller_plan` | 决策摘要、Task、依赖关系、角色、完成条件、风险和未知项 |
| `plan_validation` | 校验结论、检查项、拓扑顺序、能力缺口、依赖环和拒绝原因 |
| `agent_dispatch` | 已创建 Task、Agent Run、串行执行组、阻塞/跳过条目和角色模型绑定 |
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
- 当前稳定错误按用户动作分组：计划/合同类包括 `node_output_invalid`、`agent_capability_unavailable`、`agent_plan_invalid`、`agent_review_policy_mismatch`、`agent_dependency_cycle`；限制类包括 `agent_budget_exhausted`、`agent_node_limit_exceeded`、`agent_model_call_limit_exceeded`、`agent_workflow_timeout`；质量门禁类包括 `agent_verification_failed`、`agent_review_rejected`；终止/系统类包括 `agent_workflow_cancelled`、`agent_provider_outcome_unknown`、`agent_workflow_internal_error`。未知 Provider 错误代码使用通用安全文案，并继续服从 `is_retryable` 和 `outcome_is_known`，不能因前端未登记就改成可重试。
- `outcome_unknown` 表示 Provider 结果是否发生无法确认。当前相应错误会强制 `is_retryable=false`；前端只提供“重新读取证据”，不提供一键重新运行。
- `agent_budget_exhausted`、节点/模型调用上限、验证失败和审核拒绝分别显示其事实原因。当前 Reviewer 拒绝不会进入返工轮次，不能展示“正在修改”。
- 不提供取消、恢复、批准、用户输入、返工、重排 Task、提高上限或绕过能力拒绝的动作。相应接口实现并通过状态竞争测试后再增加按钮。

#### BFF 与授权边界

- Next.js 后端为前端（BFF）允许列表只增加上表七个路径和对应方法；仍使用流式请求/响应大小限制，不增加 Agent 通配代理。
- 阶段10身份未完成前只用于固定开发用户。以 Run ID 创建/发现时先校验 Run 属于固定开发用户；以 Workflow ID 查询时在服务端解析其 `run_id` 后再校验 Run 归属；Node 详情继续要求 Node 属于路径中的 Workflow。
- 浏览器不能提交或覆盖 `user_id`，不能看到平台 API Key、内部路径、数据库标识以外的内部存储位置、模型 Prompt 或原始 Provider 响应。
- FastAPI 当前仍是受信调用方边界；BFF 的开发用户校验不能替代阶段10最终主体授权。公开发布前必须将 Workflow、Node、Event 查询纳入服务端主体资源授权测试。

#### 当前前端实施任务

| 工作项 | 具体任务 | 预期结果 | 验证方式 |
|---|---|---|---|
| 合同类型 | 从 OpenAPI 生成或手写严格类型，覆盖 Create、Summary、Result、Node、Event 和 cursor page；对 `output_type` 做穷尽映射 | 编译期能发现字段、状态和节点输出版本变化 | 合同样本测试、TypeScript 穷尽检查、OpenAPI 差异检查 |
| BFF 代理 | 增加七个精确路由的方法/路径允许列表、Run 归属链校验、SSE 转发和大小限制 | 浏览器可安全访问接口且不能跨开发用户读取 Workflow | 允许/拒绝/缺少归属/跨用户/超限/流式代理测试 |
| 提交模式 | 增加显式 Agent 模式、Run `user_request` 关联、角色模型绑定、审核策略、限制设置和 Workflow 幂等 key；禁止重复保存最终 Assistant Message | 普通响应与 Agent Workflow 不混用；已知 Run 下的 Workflow 超时不重复计费 | Run 目标一致性、Message/Run 部分失败、最终消息单次保存、表单边界、幂等回放、`200`/`201`、`409` 恢复测试 |
| 事件存储 | 实现 sequence 去重、缺口检测、有限回放、终态 Result 收敛和版本保护 | SSE、刷新与断线都回到同一后端事实 | 重复、乱序、缺页、非法游标、断流、终态缺失测试 |
| Workflow 摘要 | 在 Run 详情展示阶段、活动节点、计数、耗时、聚合用量和终态错误 | 用户先看到当前事实，再按需展开节点 | 空、运行中、完成、失败、取消、未知结果组件测试 |
| 节点时间线 | 用统一信封和 11 个 `output_type` 渲染器展示完整节点结果 | Agent 分工、Handoff、验证和审核可复核，不展示隐藏推理 | 每种输出、未知版本、长内容、警告/错误、移动端测试 |
| 浏览器验收 | 使用真实 API 跑通新建 Run 下的 JSON 与 SSE Workflow、刷新恢复和失败状态 | 内部开发版可独立验证阶段5核心接口 | Playwright 关键流程、网络响应敏感字段扫描、性能检查 |

#### 后端补齐后再启用

- 外部取消、恢复和等待用户输入：等待幂等或状态条件接口、允许动作和刷新确认合同。
- 进程重启恢复：等待启动审计与遗留 `running` 状态收敛；此前不宣称崩溃可恢复。
- 并行只读 Agent：等待每个并行节点独立数据库会话、有界并发和并发预算保护；届时再从纵向时间线升级为并列泳道。
- Tool/Implementer/写隔离：等待阶段4恢复及新的 `tool_enabled_v1`，并复用同一节点信封增加工具输出，不能改变 `model_only_v1` 含义。
- 严格费用边界：等待原子费用预留；此前只显示已发生的估算费用和调用前预算检查。
- 大型节点结果：等待 Artifact 降级、hash、MIME、受控预览和内容读取合同。
- 持续事件订阅：等待 GET `/events` 具备长连接或明确轮询协议；此前使用 POST 实时流加有限回放。

#### 当前前端通过条件

- 七个当前接口的请求、响应、状态码、cursor、SSE ID/事件名和完整节点合同与阶段5 OpenAPI 一致。
- 单 Workflow 串行路径、Handoff、确定性验证、Reviewer 三种策略、预算耗尽、上限终止、验证失败、审核拒绝、取消传播和未知结果都能准确显示。
- 事件重复、乱序、缺页、断线和刷新不会重复节点、丢失终态或自动发起第二次模型调用。
- BFF 的固定开发用户归属、Workflow/Node 父子关系、路径/方法允许列表、请求大小和敏感字段测试通过。
- 时间线不展示隐藏推理、内部 Prompt、Memory、Knowledge、工具执行或尚未实现的并行/恢复动作。
- 真实浏览器完成 JSON 与 SSE 两条关键流程，并验证窄屏、长节点结果、键盘操作和长时间运行页面性能。

### 阶段6检查点：代码搜索与可复核引用

**前端范围：**

- 代码证据视图展示 workspace 内的相对文件路径、行号、匹配片段、符号定义、引用关系和检索方式。
- 搜索结果区分文本匹配、语法定义和语言服务结果；索引构建中、已过期、不可用和部分结果分别展示。
- Assistant 引用可以打开对应证据，但不能让模型生成的任意路径直接变成文件读取请求。

**后端合同门禁：**

- 每条结果需要稳定 workspace 归属、安全相对路径、行范围、内容摘要、来源工具和索引版本。
- 后端负责路径规范化、符号链接边界、文件大小、二进制文件、结果数量和超时限制；前端不重复实现安全路径判断。

**前端通过条件：**

- 覆盖越权 workspace、路径穿越、超大文件、长行、二进制文件、索引过期、无结果和部分失败。
- 文件链接在桌面与窄屏均可定位证据，复制引用不会包含服务器绝对路径。

### 阶段7检查点：MCP 连接与能力目录

**前端范围：**

- MCP 设置页展示连接名称、状态、服务器公开信息、工具/资源能力、认证状态、最近错误和启停操作。
- 会话或 Agent 只能选择后端已发现并允许的 MCP 能力；每次调用进入与本地工具一致的风险、批准和审计视图。
- OAuth 或其他认证材料由服务端管理；浏览器只参与必要跳转和状态确认，不读取或持久化 MCP access token。

**后端合同门禁：**

- 连接、能力发现、认证、超时、重连、权限和撤销必须具有稳定状态与公开错误；未知工具或资源默认拒绝。
- MCP 调用必须经过阶段4工具权限层，不能因为来源是 MCP 就绕过批准、路径或网络限制。

**前端通过条件：**

- 覆盖连接失败、认证过期、能力变化、调用超时、资源过大、撤销后调用、重复提交和敏感字段脱敏。
- 能力目录刷新时保留明确版本/时间，不把过期能力继续显示为可调用。

### 阶段8检查点：可观测性与内部诊断

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

### 阶段9检查点：前端平台自身

**前端范围：**

- 维护统一的三栏对话外壳、主题令牌、响应式行为、错误语言、可访问性、BFF 允许列表和浏览器安全边界。
- 按本节的后端检查点逐步增加功能；功能开关只能控制是否展示已经存在的后端能力，不能把未完成能力变成模拟成功。
- BFF 负责同源调用、浏览器会话转发、请求大小限制、路径与方法允许列表和公开响应映射，不承载后端业务状态机。

**前端通过条件：**

- 每次新增后端检查点集成，都同时通过类型检查、Lint、生产构建、合同测试、真实浏览器关键流程、响应式和安全扫描。
- 主要操作可用键盘完成；加载、空、失败、无权限、冲突、取消和部分成功均有明确状态。
- 前端性能门禁覆盖长会话、长流式响应、大量时间线事件和详情抽屉，避免按 token 重渲染全部页面。

### 阶段10检查点：最终用户身份与凭据

**前端范围：**

- 增加登录、退出、当前用户、登录会话列表、单会话撤销、全部会话撤销和密码管理入口。
- 增加 NexusPilot 个人 API Key 列表、创建、一次展示和撤销；密钥原文只在创建成功页出现一次，离开后不可恢复。
- 增加供应商凭据创建、命名、验证、默认项、轮换和撤销；列表始终掩码，验证可能产生费用时必须在提交前说明并确认。
- 移除固定开发用户和浏览器到部署级 API Key 的隐式映射；所有资源 owner 由 FastAPI 当前主体派生。

**失败与恢复：**

- 401 返回登录或重新认证；无权查看其他用户资源使用后端规定的 404/403，不在前端枚举资源存在性。
- 登录会话撤销后立即清除本地用户状态和敏感缓存；重复退出保持成功体验。
- 个人 API Key 创建响应丢失时提示撤销并重新创建，不能提供“再次显示”。
- 凭据验证超时、版本竞争、撤销和密钥管理服务不可用分别展示；不得自动回退到平台或其他用户凭据。

**前端通过条件：**

- 登录、退出、当前用户、跨站请求伪造防护、会话固定、到期、撤销、跨用户隔离和 Cookie 属性通过浏览器安全测试。
- 个人 API Key 和供应商凭据不进入 localStorage、sessionStorage、日志、URL、错误追踪、SSE 或静态资源。
- 阶段10与对应 Web 集成全部完成前，阶段9不得改为面向最终用户公开发布状态。

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
- 当前预览中的 Agent 与工具执行提示只是能力预留；正式事件合同完成前不得显示伪造步骤或进度。

### 页面与路由

| 页面 | 建议路由 | 作用 | 启用检查点 |
|---|---|---|---|
| 新对话 | `/` | 空状态、Provider/Model 选择和首次输入 | 阶段1、阶段2、阶段9当前内部版 |
| 会话 | `/c/[sessionId]` | 消息历史、流式响应和当前 Run 摘要 | 阶段1、阶段2 |
| 运行详情 | `/runs/[runId]` | 阶段1先展示 Task、Attempt、Retry、Artifact；阶段5当前增加 Agent Workflow 摘要、节点时间线和结果；其他阶段继续增量增加任务、工具、代码、MCP 和诊断证据 | 阶段1基线；阶段5只在内部功能门禁下启用当前七个接口 |
| 模型设置 | `/settings/models` | 查看实际注册 Provider 和已验证 Model Catalog | 阶段2 |
| MCP 连接 | `/settings/connections` | 管理 MCP 连接、认证状态和能力目录 | 阶段7 |
| 登录 | `/login` | 建立最终用户浏览器会话 | 阶段10 |
| 账户安全 | `/settings/security` | 登录会话、个人 API Key 和供应商凭据 | 阶段10 |
| 内部审计 | `/internal/audit` | 双密钥保护的开发/运维入口，不出现在普通用户导航中 | 阶段1内部能力；阶段10后继续与普通用户权限隔离 |
| 内部诊断 | `/internal/diagnostics` | 关联 API、模型、Worker、工具和 Agent 的安全诊断证据 | 阶段8 |

第一版只启用阶段1、阶段2和阶段9当前内部版所需页面，不创建 Memory、Knowledge、Project 看板或 Agent 管理页面。后续路由必须等待表中对应检查点，不提前创建可误解为空能力的普通用户入口。

## 已确认的视觉与交互原则

- 中央阅读列保持约 720～820 像素的舒适文本宽度，宽屏剩余空间用于留白或详情抽屉。
- 左侧栏使用低对比度背景，突出“新对话”和最近会话，不堆叠多层卡片。
- 消息主体以排版区分角色；避免每条消息都使用厚重边框和阴影。
- 输入框固定在主区底部，支持多行输入、Enter 发送、Shift+Enter 换行和生成中停止按钮。
- 流式文本直接追加到当前 Assistant 消息；每一帧不重新渲染完整 Markdown，按批次刷新以避免长响应卡顿。
- 代码块提供语言标识、复制按钮和横向滚动；表格、引用和列表遵循 Markdown 语义。
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
3. Next.js 服务端按现有 API 创建或复用 User、创建 Session，并追加用户 Message。
4. 创建可审计 Run；只有实际需要任务对象时才创建 Task，不伪造 Agent 任务。
5. 调用 `/api/v1/responses`，请求内容只来自用户当前显式输入和明确填写的 instructions。
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

`model_only_v1` 接入后，每条由 Agent Workflow 产生的 Assistant 响应下方增加可折叠的“执行过程”。默认展示 Workflow 的 `current_stage` 和活动节点，展开后按 `node_sequence` 展示经过脱敏的串行时间线：

```text
Controller：已形成任务计划
  ├── Planner：已提交 Handoff
  ├── Researcher：正在调用模型
  ├── 确定性验证：等待上游结果
  └── Reviewer：按审核策略决定是否执行
```

当前用户可见状态只映射已实现事实：等待执行、正在运行、正在调用模型、正在审核、正在验证、已完成、失败、已取消和结果未知。规划中但当前不可用的“正在等待工具”“正在执行工具”“等待用户输入”“等待用户批准”和“正在并行执行”不能进入正常文案枚举。

阶段4恢复并新增 `tool_enabled_v1` 后，才在相同执行过程内增加 Implementer 与 Tool Call 子节点。每个工具调用最多展示人类可理解名称、风险、开始时间、持续时间、状态、有界输入摘要、安全结果或 Artifact、批准状态和稳定错误；仍不展示完整 Shell 环境、密钥、内部对象 URI、模型隐藏推理、完整私人文件或未脱敏输出。

阶段5当前事件合同已经确定 `event_id`、`event_sequence`、Workflow/Run/Node 标识、事件类型、Workflow/Node 状态、发生时间、公共摘要、公共载荷和可选 trace ID。前端直接使用该合同和节点详情路径，不再设计第二套通用运行事件字段；阶段8只补充遥测关联，不替代 MySQL 业务事实。

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
- `task-execution`：阶段3的排队、Worker、重试、取消与恢复状态。
- `tool-execution`：阶段4的工具提议、批准、执行结果与 Artifact。
- `agent-workflow`：阶段5当前的创建表单、Workflow 摘要、完整节点类型渲染、Handoff、验证、审核和 Result 收敛；等待用户动作只在后端接口实现后增加。
- `code-evidence`：阶段6的文件定位、符号关系和索引状态。
- `mcp-connections`：阶段7的连接、能力目录、认证状态和受控调用。
- `observability`：阶段8的公开关联标识与内部诊断视图。
- `auth`：阶段10的当前主体、登录会话、个人 API Key 和供应商凭据。
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
| SSE 中途断开 | 保留部分文本并标记未完成；读取 Attempt 最终状态，不自动重新计费 |
| 用户主动停止 | 取消上游请求并等待后端最终状态；不能只在前端停止渲染 |
| Message 保存失败 | 保留响应并指向 Run/Attempt；在 Message 幂等能力完成前不自动重试保存，更不能重复调用模型 |
| Run/Attempt 查询失败 | 正文仍可阅读，详情抽屉显示依赖暂不可用并允许手动重试 |
| cursor 失效 | 清空对应分页窗口后从第一页重新加载，不混合新旧筛选结果 |
| 异步 Task 暂无 Worker | 显示已接受或等待执行的后端事实，不显示虚假的执行百分比；允许动作由后端返回 |
| Agent 模式的 User Message 或 Run 创建结果未知 | 保留输入并标记提交尚未确认；在统一幂等提交合同完成前不自动创建第二条 Message、第二个 Run 或 Workflow |
| Agent Workflow POST 断流或超时 | 保留原 `idempotency_key`，先按 Run 发现 Workflow、回放已提交事件并读取 Summary/Result；不生成新 key 自动重试 |
| Agent Workflow 事件重复或缺口 | 按 `event_sequence` 去重；缺口时停止实时标识，执行有限回放并以查询快照收敛 |
| Agent Workflow 结果未知 | 展示安全错误和已提交节点证据，只允许重新读取；不提供自动重跑或“继续执行” |
| Tool/Agent 等待批准或用户输入 | 当前阶段5不显示该动作；后端接口实现后保持 Run 可恢复并使用幂等/状态条件合同提交 |
| MCP 连接或认证失效 | 禁用该连接的新调用并保留已有运行证据；返回连接设置入口，不自动切换其他连接 |
| 遥测或追踪缺失 | 只标记诊断证据不可用，不把业务 Run 改为失败 |
| 未授权或凭据失效 | 清除前端应用会话并返回登录/配置入口，不显示内部错误正文 |

## 第一版实施记录

第一版实现位于 `apps/web`，以本文件前面确认的 Sites 预览为界面基线，当前已经具备以下能力：

- Next.js 服务端转发层：浏览器通过同源 `/api/nexus/*` 访问 FastAPI，平台 API Key 只在服务端读取。
- 三栏工作区：会话侧栏、中央对话与输入区、运行证据检查器；桌面端默认展示详情，窄屏改为左右覆盖层。
- 会话数据流：读取 Session 与最新消息窗口，服务端通过单次完整消息分页合同返回正文，并用游标继续加载更早消息；首次恢复同时读取 Session 最新 RunDetail 与 Attempt 事实。
- 模型调用：从后端 Provider 注册事实选择 Provider，模型名称可编辑；通过 `POST /api/v1/responses` 接收 `response.text.delta`、`response.usage`、`response.completed` 和 `response.failed`。
- 流式安全行为：按 sequence 去重；停止生成会中止浏览器请求并保留运行详情入口；部分文本不会被标记为完成。
- 服务端代理边界：仅开放前端需要的固定路由，并强制校验固定开发用户的 Session、Message 和 Run 归属；请求体在流式读取中受大小上限约束。
- 主题与响应式：亮色白底黑字、暗色黑底白字；支持本地主题偏好、键盘发送、移动端覆盖层和 reduced motion。
- Agent Workflow：提供显式 `model_only_v1` 模式、审核策略、七个接口客户端、POST SSE 与有限事件回放、事件缺口检测、完整节点类型、11 类节点证据时间线和 Result 恢复；DeepSeek 根据已注册能力使用 prompted JSON。
- Agent 代理边界：BFF 精确开放当前七个路径，以 Run 校验 Workflow 和 Node 的固定开发用户归属；嵌套 Workflow POST 不再被 Run 创建规则误判。

当前明确不宣称完成的内容：最终用户登录与授权、正式 Message/Run 端到端幂等合同、Run/Task 完整状态编排、Artifact 详情页、Markdown/代码块渲染测试、Context/Prompt/Evaluation 开发视图、真实 Agent Provider 端到端验收，以及外部取消/恢复、并行和工具时间线。它们仍按下方工作项和后端能力依赖继续推进。

## 测试设计

- 组件测试：空状态、消息角色、Markdown、代码块、错误提示、生成中与完成状态。
- SSE 测试：sequence 顺序、重复事件、缺失终止、失败事件、取消和断线。
- 页面集成测试：新建会话、追加用户消息、普通响应、流式响应、保存 Assistant Message、打开 Attempt 详情。
- 安全测试：浏览器响应和静态资源不包含平台 API Key、内部 API Key、供应商凭据或 MinIO URI。
- 可访问性测试：键盘发送/换行、焦点管理、屏幕阅读器状态、颜色对比和 reduced motion。
- 响应式测试：桌面侧栏、移动端覆盖层、长代码块和长单词不会破坏布局。
- 恢复测试：刷新页面、SSE 中断、保存失败和重复提交不会重复产生计费调用。
- 阶段3测试：重复/乱序任务事件、有限重试、死信、Worker 崩溃、取消竞争和刷新恢复。
- 阶段4测试：工具提议与执行区分、允许/拒绝、批准过期、重复批准、工具超时、部分 Artifact 和敏感输出脱敏。
- 阶段5当前测试：七个接口合同、代理允许列表与跨用户拒绝、SSE 解析、事件顺序/重复/缺口/有限回放、DeepSeek 结构化输出模式和生产构建已覆盖；真实 Provider 下的 JSON/SSE 创建、相同幂等回放、Run 冲突、11 类节点数据、审核策略、预算耗尽、验证失败、断流取消、未知结果和刷新恢复仍需端到端验证。并行、写隔离、等待用户输入、外部取消/恢复和崩溃恢复在相应后端能力实现后再加入。
- 阶段6测试：workspace 越权、路径穿越、长行、二进制文件、索引过期和部分搜索结果。
- 阶段7测试：MCP 连接失败、认证过期、能力变化、调用超时、撤销和资源大小限制。
- 阶段8测试：无追踪、采样、部分/乱序 span、遥测延迟和敏感属性过滤。
- 阶段10测试：登录、退出、Cookie、跨站请求伪造防护、会话撤销、个人 API Key 一次展示、供应商凭据轮换和跨用户拒绝。

## 实施工作项

| 对应后端检查点 | 前端工作项 | 依赖 | 完成条件 | 状态 |
|---|---|---|---|---|
| 阶段1 | 会话外壳、完整消息分页、Run/Task/Attempt/Retry/Artifact 证据和允许动作 | 阶段1已完成的安全查询 API | 归属、分页、局部失败、刷新恢复和浏览器脱敏测试通过 | 进行中 |
| 阶段2 | Provider/Model、普通响应、SSE、Context/Prompt/Evaluation 开发证据 | 阶段2已完成的 LLM 核心 API | 真实 API 端到端、断流、费用、能力拒绝和证据版本测试通过 | 进行中 |
| 阶段3 | 异步 Task 状态、Worker 时间线、有限重试、死信、取消与恢复 | 阶段3完成并提供公开状态/事件合同 | 故障窗口、乱序、重复事件和刷新恢复测试通过 | 未开始 |
| 阶段4 | Tool Call、风险提示、批准、工具结果与 Artifact | 阶段4完成并提供工具权限/批准合同 | 提议与执行不混淆；权限、批准竞争、超时和脱敏测试通过 | 未开始 |
| 阶段5 | 当前先接入 `model_only_v1` 创建/发现/结果/节点/事件、Agent/Turn/Handoff、验证、审核与预算只读证据；动作、并行和工具后置 | 当前七个接口和完整节点合同；BFF 归属保护已同步实现；工具视图另等阶段4门禁 | 合同、代理、事件恢复、完整节点 UI 和响应式已实现；真实 Provider 下的 JSON/SSE、幂等、失败/未知结果和刷新恢复端到端测试通过后完成 | 进行中 |
| 阶段6 | 代码搜索、文件/行引用、定义引用和索引状态 | 阶段6完成并提供安全定位合同 | 路径、workspace、大小、索引和引用准确性测试通过 | 未开始 |
| 阶段7 | MCP 连接、能力目录、认证状态、资源和调用证据 | 阶段7完成且调用经过阶段4权限层 | 连接、认证、撤销、超时、能力变化和资源限制测试通过 | 未开始 |
| 阶段8 | 关联标识、耗时分解和内部诊断页 | 阶段8完成公开/内部遥测分层 | 业务事实不被追踪覆盖；敏感字段扫描和缺失追踪测试通过 | 未开始 |
| 阶段9 | Next.js、BFF、主题、响应式、可访问性、性能和检查点门禁 | 已验证的后端接口与稳定开发环境 | 构建、Lint、类型、浏览器、安全和性能门禁持续通过 | 进行中 |
| 阶段10 | 登录、会话安全、个人 API Key、供应商凭据与多用户授权迁移 | 阶段10身份/凭据 API 和资源授权完成 | Cookie/Bearer、安全存储、撤销、跨用户和公开发布端到端测试通过 | 未开始 |

阶段1和阶段2的后端状态已经是“已完成”，上表的“进行中”只表示对应前端集成尚未满足自己的完成条件，不更改后端阶段状态。

## 第一版完成标准

以下条件只表示阶段1、阶段2和阶段9前端基础形成可验证的内部对话版本，不表示阶段3～8能力已经存在，也不表示阶段10公开发布门禁已经通过。

- 用户可以创建和选择会话、查看消息历史，并通过同一界面完成普通或 SSE 流式模型调用。
- Provider 列表来自后端注册事实；前端不硬编码供应商可用状态。
- 页面刷新后能够从 Session、Run 和 Attempt 恢复已持久化状态。
- 失败、取消和断流不会被显示为完成，也不会自动产生第二次模型费用。
- 平台 API Key 和内部 API Key 只存在于 Next.js 服务端配置，不进入浏览器。
- 第一版不读取 Memory、不创建 Memory Packet、不读取 Knowledge，也不展示虚假的 Agent 或工具执行状态。
- 桌面和移动端关键流程通过组件、集成、可访问性和响应式验证。
- 文档、界面文案和实际 API 状态一致；尚未实现的 Agent/Tool 功能明确显示为未提供，而不是空白演示数据。
