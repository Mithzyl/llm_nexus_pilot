# 第二阶段：LLM 核心能力规划与实施说明

## 通用 Agent 的模型输入与结果职责

**2026-10-19 更新；状态：进行中（新循环适配）。** 首批通过共享模型服务发送普通正文/原生工具请求，不改变原显式 JSON API；复用对象化正文和引用存储，无数据库迁移。原阶段完成记录保留。已增加最终组装的 system、适配器输入参数、受保护续接父记录及显式原文输入清单；这不是实际 HTTP wire 抓包，真实 Provider 完整验收仍待完成。范围见[阶段7首批实现](phase-7-tool-runtime-plan.md#首批实现与调用方式2026-10-19)。

- 保留 Provider Registry、端点/能力目录、推理配置、私有续接、SSE、取消、失败用量、文件解析/快照、MinIO 正文和跨用户来源校验。支持同一 Agent 连续观察工具结果，不因任务从读取转为分析而强制换角色。
- 目标接口区分普通正文、小型工具参数、明确要求的结构化决定。新通用 Worker/最终回答不默认绑定 JSON 模式；Controller/审核决定/显式结构化 API 仍按所选能力校验。不能全局删除 `response_format`、放宽工具参数或替已有 API 自动补 JSON。
- 基础 Context、持久完整历史、最终有效请求分别记录。最终请求必须可追溯实际 system 指令及其顺序、公开消息、工具定义/版本、输出要求、模型选项和结果引用；私有续接只保存受保护引用。已有 `effective_messages` 不包含最终 system 本身，不能单凭它宣称已保存完整 wire 请求；补足这一点不重复付费请求或公开内部提示词。
- 工具历史管理保留调用/结果配对、消息级续接和原始对象；省略旧正文时明确标记并保留重新读取入口。任务内工作笔记/计划不能替代原文，也不等同于启用暂停中的 Memory Packet 或跨会话记忆。
- 文件默认只提供清单和工具；实际读取是否全文由用户目标及模型动作决定。工具分页、全文覆盖、短来源编号由阶段7定义。阶段2提供固定快照、真实范围、截断/解析遗漏及有效输入记录，不把“工具返回”自动记为“已理解”。
- 原生协议事件不增加工具自主权限。日志分别区分 Provider 原始 SSE、规范化拼接结果和最终保存结果；现有 `raw-response.json` 的 `normalized_stream_aggregate` 不是原始 SSE。后续受控诊断可记录有限尾部、长度/哈希及请求关联，必须脱敏和限定访问/保留范围，不默认保存私有推理或全部原始网络流。

验收：同模型普通正文和工具续接、显式 JSON API 兼容；最终有效请求与实际发送参数相符；旧消息和 MinIO 对象可读；窗口不足不静默丢掉最新工具配对；跨用户/删文件/撤权拒绝；停止中断在途请求且迟到用量不恢复执行。读取型、计算型和产物型 Agent 用同一 Gateway，模型能力不足必须记录失败而非伪造支持。真实 Provider 调用需单独授权。

## 2026-10-18 附件快速回复超时诊断

后续工作（2026-10-20，未开始）：补齐共享模型调用的流式进度和中断部分结果保存；完整正文与未完成草稿分开，供应商未知用量不补零，私有推理不复制到公开结果。Strata 的内部 SSE 聚合目前不等于 Agent 中断内容已持久保存。实施范围、存储兼容和验收统一见[阶段7流式中断与续接规划](phase-7-tool-runtime-plan.md#流式生成进度中断结果保存与续接2026-10-20)，本次仅更新规划。

本地历史 Run `e22e5586-0ef4-4b1f-8dc9-631bf0740249` 的 Strata 附件工具循环在北京时间 12:03:46 开始，前五次模型调用已完成，第六次从 12:04:44 到 12:04:46 即超时。前端将 60 秒传入整轮流程，`ResponseFileToolService` 每轮使用剩余时间，因此该错误不能解释为上传解析失败或供应商单次响应等待了 60 秒。最后一次的用量未知，不能推断未计费。

按用户后续要求，取消曾设置的 300 秒上限：Web 快速回复显式传入 `timeout_seconds: null`，API、模型请求、普通生成、SSE 和附件工具循环均不设置模型生成整轮截止时间。省略字段仍默认 60 秒，显式有限值仍接受 1–600 秒，以兼容其他调用方。HTTP 模型响应读取不限时，但连接等待保留 10 秒、请求写入和连接池等待保留 30 秒。随后同日取消快速回复固定 8 次模型调用和 32 次工具调用限制，按成功新增原文、重复请求与取消决定是否继续；单批最多 8 个工具请求、单次工具资源限制仍保留。当前调用策略由[阶段7](phase-7-tool-runtime-plan.md#持续工具调用策略2026-10-18)维护，不承诺消除供应商或代理自身超时。

显式取消仍传播到供应商响应读取与工具执行；取消不重试、不启动下一模型轮次，Run 保存 cancelled，供应商未确认的结果与费用仍保持未知。同一标签页内切走再切回保留原执行控制器；若停止发生在准备阶段而 Run 随后才创建，则通过已有 Run 取消接口结束未发起模型请求的 Run。BFF 此入口检查当前用户归属与 CSRF，不用 Run 状态修改代替已发出请求的连接取消。快速回复恢复时持续只读跟进非终态，处理断开与服务端取消提交的时间差。关闭标签页、进程重启后仍不具备跨进程执行控制器恢复。

验证：API/附件/Run 状态/供应商及独立 JSON/SSE 传输取消共 126 项测试通过，确认无限读取可取消、关闭响应体且不重试；前端 151 项测试通过，包括 BFF 传递 null 和 AbortSignal、响应体取消及跨用户拒绝。Playwright 固定响应验证发送 null、切走返回仍能停止、取消后可继续发送且只有一次模型请求，截图 `output/playwright/unbounded-stop-after-return.png`；准备阶段停止后只调用一次 Run 取消、零模型请求，截图 `output/playwright/prepared-run-stop.png`。没有重新调用 Strata/DeepSeek，真实文件解读与供应商远端停止计费未验证。未部署。

## 2026-10-18 快速回复整轮状态修复

`ConversationResponseService` 在 HTTP Responses 入口管理绑定用户消息的快速回复 Run。校验消息属于该 Run 和会话后，条件更新 pending → running，拒绝重复执行或已由 Task/Workflow 管理的 Run。普通模型服务仍只管理 Attempt；自动文件工具的多个模型轮次结束前，不将整个 Run 标为完成。整轮响应完成、失败、客户端取消及无终止事件退出，分别保存 Run 终态；终态先提交，再发送 SSE 终止事件。无当前用户消息、带 Task 或外部 tools 的通用调用保持原有职责，不擅自结束其 Run。

Run 的 completed 表示本轮生成结束，不保证前端保存 Assistant 消息成功；保存失败仍须单独显示。cancelled 表示本地执行已停止，不代表供应商已停止计费，Attempt 的 outcome_unknown 和迟到用量规则保持不变。进程硬退出的自动恢复未在本次实现，不得仅凭超时把未知调用标记成功。

验证：先新增回归测试，五条 SSE 路径均复现原 Run 留在 pending；修改后，Responses、自动文件工具、文件上下文和整轮状态测试共 73 项通过。覆盖 JSON/SSE 成功、失败、取消、异常、缺少终止事件、无效消息、重复执行拒绝、文件工具中间轮次保持 running 及取消后不再调用模型。使用隔离 SQLite 与脚本 Provider，无真实模型调用；历史本地 MySQL 的有限记录修复及浏览器续聊结果见阶段4文首。未部署。

**文档日期：** 2026 年 10 月 17 日
**文档状态：** 原阶段已完成；新增文件输入工作项进行中（浏览器私有上传、不可变原件与解析结果保存、同会话连续文件上下文及逐轮快照已实现；逻辑文件版本历史、跨文件解析缓存及 OCR 尚未完成；模型工具能力统一由工具主计划安排）

> 2026-10-08 规划归并：本文维护模型编解码、Context 和文件解析/来源规则；新增工具参数适配、结果消息与续接测试作为[工具主计划](phase-7-tool-runtime-plan.md)同一交付中的实施任务，不再在阶段2单列工具完成条件。基础上传当前状态以下节为准，早期带日期的基线不代表当前待办。

## 模型接口复查与失败语义（2026-10-17）

2026-10-17 后端后续修复（已完成本地限定验证）：OpenAI Responses／Anthropic SSE 的流内错误改为固定公开文案和允许字段诊断；OpenAI 非流式响应必须具有 `completed`／`incomplete` 状态且没有 error，失败、取消、排队、运行中、未知和缺失状态不再默认为 stop。已报告的流用量在后续错误、EOF、网络失败或平台流期限到期时保存到失败 Attempt，累计快照取最后有效值、不相加。中断用量在私有审计中标记 `usage_complete=false`，不据此估算最终费用；没有收到的计数保持未知。Strata 收集式调用复用同一处理。正常结束后 JSON 校验失败的完整用量及既有费用保存保持不变。后端和模型完整回归 856 通过、32 条件跳过，详细范围见阶段3“后端执行观察分离”。本次只进行本地离线测试，不调用真实供应商、不部署。

最新 Strata 续验：此前错误与验证记录先提交为 `6b082e5`；后续受控 Agent 样本真实完成一次计划修正，最终回答、运行中刷新持续跟进及完成后恢复通过。共享供应商错误诊断现仅保存允许的错误类别／参数、HTTP 状态、原消息长度和 SHA-256，不保存任意错误正文、请求回显或凭据；公开错误保持固定安全文案。HTTP 非 2xx 和 Chat HTTP 200 错误对象共用诊断规则，测试覆盖两种流消费方式及 API 私有存储／公开响应分离。真实样本未触发供应商错误，不能据此解释历史未知失败。完整耗时、Token、测试和限制见[阶段3修复验收](phase-3-agent-workflow-plan.md#刷新跟进执行事实与脱敏诊断修复2026-10-17)，不与历史小型 JSON 样本混算。

本次状态：进行中。共享 Chat Completions 和四家协议的流结束校验、非法 JSON 用量与费用保存已修改并完成离线回归。此前直接适配器 JSON 样例失败，随后完成实际请求体单变量对照及无 system 指令时的格式引导修复，DeepSeek 普通生成与 SSE JSON 复测通过；Strata 小型 JSON 两种消费方式及受控工作流有限修正现已真实通过，详见阶段3本日续验。未部署。既有阶段验收记录保留，不等于所有厂商的当前真实兼容性已验收。

| 检查对象 | 当前职责与本次结论 |
|---|---|
| `POST /responses` 的 JSON/SSE 两种入口 | 继续共用 ModelInvocationService、Context、Attempt 和自动附件工具服务；不新增 Strata 专属业务入口。ASGI 回归验证错误事件不产生 completed，并保存 failed Attempt。 |
| `/providers`、`/models`、`/model-capabilities` 及内部模型登记 | Registry 需数据库启用地址和环境凭据；目录支持/不支持/未知不等于真实模型测试通过。不因兼容 Chat Completions 自动声明严格 Schema、工具、视觉或思考等级。 |
| Context、Prompt、Evaluation、Attempt 和 Transport Attempt 接口 | 保留现有资源授权、内部写入与对象存储规则；本次无数据库迁移或公开字段变化，相关既有回归随后端测试执行。 |
| Chat Completions（DeepSeek、compatible、Strata） | 区分 HTTP 200 错误对象、refusal、content_filter、缺失 finish reason 和空答案；usage-only 的空 choices 合法。不得用默认 stop 替代未知结束状态。DeepSeek 关闭思考不再强制要求不存在的 reasoning 续接状态。 |
| OpenAI Responses | 必须收到 terminal response 事件，不再把断流前的正文直接构造为成功。 |
| Anthropic Messages | 必须观察到 message_stop 和 stop_reason，不能把 EOF 当成 end_turn。 |
| Gemini GenerateContent | 流必须观察到 finishReason；错误 envelope 和 promptFeedback 拦截单独失败。 |
| Agent 节点结构校验 | 属于阶段3；与 HTTP/SSE 完成不同。空输出不能进入已有 JSON 字段修正；修正结果必须是节点对象，而不是 output_repair 输入包装。 |

### 当前规则

- HTTP 传输成功、生成正常结束、JSON 可解析、节点字段合法是不同检查，不能互相替代。`json_object` 不保证符合节点 Schema；保留后端字段校验，不给未知能力自动开启 `json_schema`。
- HTTP/SSE 已开始输出后的读取超时、断连分别记录 timeout/network_error，不自动重放已开始的生成；连接前重试策略保持不变。离线模拟两类失败先复现，再修正。
- 流式 `raw_response` 是聚合结果，不是原始 SSE 抓包。Chat 聚合结果新增 `record_kind=normalized_stream_aggregate`。此前 Strata 两条空聚合记录不能证明上游确实发送空正文。
- 已正常结束但 JSON 解析失败的生成，通过 `ModelProviderError.response` 携带标准化用量、耗时、供应商请求 ID 和传输记录；共享 Chat、OpenAI Responses、Anthropic、Gemini 共用这项处理。普通 `/responses` 仍返回失败，Attempt 保存 `failed/response_parse_error`，已知费用在同一数据库事务计入 Run，复用既有提交失败重试及提交状态核对，不重复计费。未配置价格或供应商未返回完整用量时，费用保持 null，不能推断免费。
- 失败正文按既有私有审计路径保存到 MinIO；写入失败时保留数据库用量，raw_response_uri 为空。JSON 异常携带的未投影推理块和续接状态不作为公共展示或可复用续接保存；原始错误审计继续删除私有推理与签名字段，公共错误只含固定说明。
- 本次修正：工作流经内部调用参数延后 JSON 结构校验，先保存正常结束的生成正文和供应商用量，再由节点解析、记录失败及有限修正。该参数不是 HTTP 字段，也不发送给供应商；普通 `/responses` 保持严格解析。节点未通过校验不能提交结果，模型 Attempt 完成不等于节点完成。空输出、拒绝和断流不适用该策略。
- 已完成单变量续验：实际 HTTP 请求体相同的 API 与适配器均成功；保留完整消息只改变模型标识也成功，删除 system 消息时两个标识均返回非法 JSON。共享 Chat 编码仅在 `json_object_output=true` 且 system 指令缺失或为空白时补充明确 JSON 格式指令，不覆盖已有 system 指令、不改普通文本、不把非法正文改写成 JSON、不增加自动付费重试。供应商内部为什么在这种消息结构下生成异常内容尚不可知；新增引导不代替严格解析和失败记账。
- 真实厂商矩阵仍未执行；Anthropic/Gemini/OpenAI 本次只有离线协议测试。Strata 受控工作流的验证范围见阶段3，禁止以 DeepSeek 结果替代其他 Strata 场景。

### DeepSeek 真实验证

入口：`apps/api/.venv/bin/python deployments/verification/check_live_completion_protocol.py --cases json_object stream_json_object --output output/playwright/NEW_REPORT.json`（输出路径必须不存在）。
仅从本地隔离 MySQL 13306 读取官方 DeepSeek 地址，凭据来自 `apps/api/.env`，不修改数据库；模型 `deepseek-flash`，显式关闭思考。1024 输出 Token 仅为该合成测试上限，不改变产品配置。未上传文件、未调用生产服务。

| 样例 | 结果 | 秒 | 输入/输出 Token |
|---|---|---:|---:|
| 非流式正文 | 通过 | 0.613 | 10/4 |
| SSE 正文 | 通过 | 0.849 | 10/4 |
| JSON 对象第一次 | 失败：已解析值不符合指定样例 | 未记录 | 未记录 |
| JSON 对象复测 | 失败：非法 JSON；随后停止重试 | 未记录 | 未记录 |
| 工具请求 | 通过 | 0.972 | 279/32 |
| 关闭思考后的工具结果续接 | 通过 | 0.806 | 333/11（缓存读取128） |

两次 JSON 失败发生在补齐失败证据保存之前，无法从已有终端记录恢复完整返回值或用量；未知不等于零。没有价格依据，不报告货币金额。此脚本验证 Provider completion，不等于完整工作流或浏览器验收。

2026-10-17 续验（保留上表历史失败，不覆盖）：

| 当前样例 | 结果 | 秒 | 输入 / 输出 Token | 证据 |
|---|---|---:|---:|---|
| 直接适配器，`deepseek-flash`，最小 JSON 提示 | 失败：返回 stop，但 JSON 值位置混入未转义代码，不是合法 JSON | 4.858 | 36 / 1019 | `output/playwright/deepseek-json-accounting-20261017.json` |
| 同配置 SSE | 失败：返回 stop，但 JSON 值位置为未加引号的自然语言 | 1.236 | 36 / 70 | 同上 |
| 本地 API，`deepseek-v4-flash`，system 中明确 JSON 示例 | 通过：`{"color":"blue"}`；MySQL 用量与 MinIO 原始响应相同 | 0.858 | 92 / 5 | `output/playwright/deepseek-json-system-example-20261017.json` |
| 本地 API，相同模型，最小 JSON 提示 | 通过：`{"ok":true}`；MySQL 用量与 MinIO 原始响应相同 | 0.662 | 63 / 5 | `output/playwright/deepseek-json-minimal-api-20261017.json` |

后两项执行 `check_live_json_accounting.py --output NEW_REPORT.json`，最小提示额外传 `--minimal`。依赖本地 API 18000、MySQL 13306、MinIO 19000，只创建合成测试会话，不迁移或修改真实业务记录。DeepSeek Key 从现有环境读取。JSON 示例设计参考 [DeepSeek JSON Output 官方说明](https://api-docs.deepseek.com/guides/json_mode/)；本次失败的最小提示也包含 JSON 字样和示例，不能简单归因为完全没给格式指令。

所有当前 JSON 样例显式关闭思考，1024 为测试请求上限，不修改产品默认。四次共输入 227、输出 1099 Token。价格未配置，已持久化两次 API 调用 estimated_cost=null，不能把费用算作 0。真实失败的两项发生在直接适配器验证中，其标准化失败用量已记录到报告；真实 API 的两项均成功，因此“真实非法 JSON 落库”本轮未触发，该分支由 ASGI + SQL 回归验证，不混称真实通过。

### 离线验证记录

修复前 `test_completion_failures.py`：11 失败、1 通过，复现流缺失终态、refusal 和空输出被当成完成。新增 ASGI 回归检查 failed Attempt，保留已有 usage-only、工具参数、私有续接和幂等测试。
全后端与模型测试首次运行：771 通过、10 失败、32 跳过；10 项失败全部为本地 Unix socket 被沙箱禁止。获准重跑对应 Office/PDF 模块：24 通过、2 跳过（真实 Docker 解析未启用），这不代表启动了实际 Office 容器。

最终全量复验命令 `apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -q -rs`：793 通过、32 跳过，105.95 秒。跳过项为未显式启用的 MySQL/MinIO、Docker 文件/任务执行器检查，不计通过。之后新增传输读错误分类与 HTTP 内部选项拒绝断言，定向 `packages/models/tests apps/api/tests/test_responses.py`：118 通过；内部选项拒绝单项通过。Ruff 与 `git diff --check` 通过。测试产生的旧安全报告随机 ID 改动已移除，保留原报告。未执行浏览器或部署验收。

续修验证：新增非法 JSON ASGI 回归先失败，再修实现；最终覆盖非流式/SSE × 有价/无价 × 正常持久化/对象存储失败/提交成功但确认丢失/供应商缺失用量，共16项通过；重复请求不增加 Attempt 或费用，失败推理内容不进入公共响应。价格测试使用明确夹具费率，100输入（10缓存）+20输出应为 USD 0.001520，不能套用于真实 DeepSeek。
本次中途全后端与模型回归为811通过、32跳过；之后补充三家原生协议非法 JSON 标准化用量断言及失败推理不公开断言。最终 `apps/api/.venv/bin/pytest packages/models/tests apps/api/tests -q -rs`：814通过、32跳过，108.92秒。跳过项要求显式的独立数据库迁移环境、Docker 解析/任务镜像或专用集成开关；不能算通过，也不代替本节单独运行的真实 MySQL/MinIO 验证。Ruff、`git diff --check`、两份验证脚本 `--help` 均通过；测试生成的安全报告差异已恢复。临时 API 已停止，未提交、未推送、未部署。

### JSON 请求单变量对照与共享修复（2026-10-17 后续授权）

先将上述已验收改动提交为 `65b8054`，未推送。随后仅对当前合成任务做以下对照。
脚本 `check_live_json_parity.py` 在进程内通过真实 ASGI 路由、MySQL/MinIO 执行本地 API，
在 HTTP 发送前记录模型完整请求体，再让直接适配器生成相同请求体；不更换密钥、地址、
超时、思考开关、JSON 模式、输出上限或传输方式。请求 hook 在发送前检查内容相等。
API 自动加入的平台 system 指令为上下文保护说明，不含额外 JSON 样例。

| 修复前样例 | 唯一变化 / 比较对象 | 结果 | 秒 | 输入 / 输出 Token |
|---|---|---|---:|---:|
| API，v4，完整 system+user | 基准 | 通过 | 0.789 | 63 / 5 |
| 适配器，v4，完整 system+user | 仅执行入口不同 | 通过，实际请求体完全相同 | 0.905 | 63 / 5 |
| 适配器，flash，完整 system+user | 上一项仅 model 不同 | 通过 | 0.710 | 63 / 5 |
| 适配器，v4，仅 user | v4 完整消息仅删除 system | 失败，冒号后含 JSON 不允许的 U+00A0 | 0.699 | 36 / 6 |
| 适配器，flash，仅 user | flash 完整消息仅删除 system | 失败，JSON 值位置混入未引用普通文字 | 1.010 | 36 / 46 |

第一对请求体的 SHA-256 均为 `f0e9e068d92c4bcf9c9fb8e663157e64e9a7df5b9fe5a5e0423fb4246e195dc0`。
原始 HTTP JSON 与独立 `json.loads` 均证明非法内容已经来自供应商返回，而非平台文本拼接。
因此不能把此前差异归因为 API 路径有独立 JSON 修复，或只归因为模型名称不同。
当前证据定位到缺少 system 指令的请求组合；不是证明协议规范要求所有调用必须有 system。

修复位置为共享 `OpenAICompatibleChatProvider._build_payload`。当 JSON 模式没有有效 system 指令时，
加入“输出符合用户要求的合法 JSON 对象，不添加 Markdown 围栏或 JSON 外正文”的格式引导。
这项固定引导不覆盖调用方已有策略，不只针对 DeepSeek；新增请求内容与 Token 开销在验证报告中可见。
先写回归：3失败、5通过，复现缺失、空字符串、纯空白三种情况；实施后全部通过。

| 修复后样例 | 结果 | 秒 | 输入 / 输出 Token |
|---|---|---:|---:|
| v4 完整消息（已有 system 不变） | 通过 | 0.779 | 63 / 5 |
| flash 完整消息（已有 system 不变） | 通过 | 0.725 | 63 / 5 |
| v4 最小消息，自动格式引导 | 通过 | 0.689 | 59 / 5 |
| flash 最小消息，自动格式引导 | 通过 | 0.778 | 59 / 5 |
| flash 原失败非流式样例复测 | 通过 | 0.844 | 59 / 5 |
| flash 原失败 SSE 样例复测 | 通过 | 0.839 | 59 / 5 |

修复后检查只允许最小消息组增加一条 system 消息，其他字段与修复前相同。
DeepSeek 本轮共11次真实请求，输入623／输出97 Token，合计720；复用 API 基准记录不重复计入。
首次诊断脚本重建 system 消息时发生本地类型校验失败，未发出额外供应商请求；修正脚本后复用已捕获的基准，保留首次报告，不覆盖失败记录。

证据文件位于 `output/playwright/`：`deepseek-json-parity-20261017.json`（首次基准及诊断失败）、
`deepseek-json-parity-controls-20261017.json`（修复前对照）、
`deepseek-json-parity-fixed-20261017.json`（修复后对照）、
`deepseek-json-fixed-stream-20261017.json`（非流式/SSE复测）。
复现入口：`check_live_json_parity.py --output NEW_PATH`；复用基准增加 `--baseline BASELINE_PATH`，
验收新增 system 指令增加 `--verify-fix`；这些命令使用 `apps/api/.venv/bin/python`，会真实计费。
修复后的代码运行无 `--verify-fix` 的旧最小消息对照时，发送前相等检查会主动拒绝，不会静默改变对照条件。

在上述修复验证通过之后，按新增授权调用 Strata，模型 `tiel-coder-35b-a3b-ud-q4_k_xl`：

| Strata 消费方式 | 结果 | 首字节 / 总秒数 | 输入 / 输出 Token | 缓存输入 |
|---|---|---:|---:|---:|
| 增量 stream | 合法 `{"ok":true}`，stop | 2.708 / 3.140 | 288 / 19 | 0 |
| generate 收齐 SSE | 合法 `{"ok":true}`，stop | 1.041 / 1.394 | 288 / 19 | 287 |

Strata 共2次请求，输入576／输出38 Token，合计614；本轮两家合计1334 Token。
未配置价格，不报告货币费用；缓存 Token 已包含在输入中，不重复相加。
`check_live_strata_json.py --output NEW_PATH` 保存实际 SSE 字节，不以聚合记录替代抓包；
报告为 `output/playwright/strata-json-after-fix-20261017.json`。
验证范围仅为快速回复采用的增量消费与 Agent 采用的收集式模型调用，不是完整工作流或浏览器验收。
1024 Token 上限仅用于本次合成样例，不改产品配置。未部署、未推送。

本轮最终自动化验证：`apps/api/.venv/bin/pytest packages/models/tests apps/api/tests -q -rs`，
822通过、32跳过，109.06秒；跳过原因与前次相同，独立 Docker/迁移环境未启用，不计通过。
模型、Responses、Strata 定向回归151通过。Ruff 与 `git diff --check` 通过。
最后移除测试文件无关格式变化后，单独运行该文件未指定 API pytest 配置，35项异步测试因未启用 asyncio auto 而失败；显式执行 `apps/api/.venv/bin/pytest -c apps/api/pyproject.toml packages/models/tests/test_providers.py -q` 后50项通过。此问题为测试命令配置，不通过修改业务或断言解决。
本轮新增格式引导、测试和验证脚本保留为提交 `65b8054` 之后的未提交修改。

## 正式文件上传（2026 年 9 月 16 日）

本节是当前上传状态，替代下文早期基线中的“没有上传表／没有持久解析结果”描述；历史基线的测试范围不变。

2026-10-18 上传扩容：单文件原件上限由 1 MiB 提高到 20 MiB，前端、Web 代理、API、专用解析器及 Nginx 同步；Socket 请求计入 base64 膨胀。下文 1 MiB 仅为历史验收条件，不再是当前上传上限。解析输出、块数、PDF 页数、Office 解压大小和执行时间仍有限制，不承诺任意 20 MiB 文档都能提取。正文继续存 MinIO、工具按需读取；不提高模型上下文或输出 token 限制。目标服务器验证结果以部署记录为准。

2026-10-06 后续安排：先部署 Office 执行器，将 PDF 从 API 内嵌套隔离改接同一专用解析服务，再在目标服务器验收真实文件文字、两种模式输入和连续 Context。2026-10-07 本地代码已改为专用 Socket 统一解析，移除 API 嵌套沙箱；按用户要求不部署服务器，服务器验收暂停。调用协议兼容、发布顺序和测试统一见[阶段7解析部署与验收计划](phase-7-tool-runtime-plan.md#文件解析部署与服务器验收计划2026-年-10-月-6-日)。本阶段继续负责归属、统一解析结果和快照，不把固定解析请求当成模型 Tool Call；不更改历史原件、解析对象、上传限额或默认输出 token 设置。

- `POST /api/v1/files` 先保存身份归属、额度预留、文件大小、SHA-256 和幂等键；`PUT /files/{id}/content` 接收原始字节，验证后解析。成功时原件和完整 `ParsedFileDocument` 分别保存 MinIO，并回读校验；数据库 `user_files` 只有引用、状态与提取摘要，不保存文件正文。迁移为 `20260916_0022`。
- 仅接受非空且不超过 20 MiB 的 `.txt/.md/.py/.js/.ts/.css/.json/.csv/.tsv/.yaml/.yml/.xml/.pdf/.docx/.xlsx/.pptx`，每轮最多 10 个。文件名不能含路径或控制字符；解析器继续检查内容。图片、扫描 PDF OCR、旧 Office、宏文件、音视频及通用压缩包不开放。
- 2026-10-18 本地文件修复已完成：取消完整解析 JSON 的 55000 字节 Context 存储限制，完整解析结果只存 MinIO；本轮使用独立的 8 MiB 解析对象上限保护存储/进程资源，保持原件 1 MiB、解析文本 128 KiB、最多 10000 块等执行器限制，不承诺任意大文件。上传附件默认 Context 仅携带文件标识、版本哈希、块数、解析限制和工具读取说明，不自动注入正文。工具按 offset/limit 分页（最多 100 块），记录实际读取范围和 next_offset；已有显式选区/分页引用继续有独立的 Context 大小检查。尚无前端分段选择、大文件后台解析。
- `reserved → uploading → ready`；失败记录保留 `failed`，删除先变 `deleting`，两个精确对象键清理成功才变 `deleted`。接收字节限时 60 秒；预留／上传租约 2 小时。每用户默认 500 MiB、最多 500 条活记录；未完成对象按原件加 8 MiB 解析预留计算，已有解析大小按实际字节计算，失败对象未清理前保守预留。上传接受后不允许覆盖原件或改变解析结果。
- 用户消息提交通过 `file_ids` 原子绑定首次上传所在 Run，并保存附件名称。2026 年 9 月 26 日补齐同一会话连续追问，2026 年 10 月 18 日将自动注入正文改为文件清单：快速回复及 Controller、Worker、Reviewer、最终汇总复用当前用户、当前会话、当前 User Message 序号之前已提交的附件标识；需要正文时调用读取工具，不直接给 Provider 发送原件。后续轮次无需重新上传；文件本身不重复绑定、不复制原件。其他会话及之后上传的文件不会自动进入本轮，旧的普通 Artifact 仍禁止跨 Run 引用。项目内文件提交继续拒绝，等待项目授权实现。
- 默认附件上下文在 `llm_context_builds`／`llm_context_sources` 保存文件清单快照：文件 ID、原件/解析哈希、解析版本、块数、解析限制及尚未读取状态，不保存整份文件正文。读取工具记录实际返回页的范围和来源；旧 Context 正文快照继续可读，同一请求重放不重新解析，之后的新附件不改变旧消息锚点。删除原件不删除历史记录，但新生成拒绝已删除文件。完整对话正文、旧 Context 正文和工具结果迁往 MinIO 尚待独立迁移与兼容验收，不能宣称目前 MySQL 已没有任何正文。
- 会话自动携带文件清单最多 10 个；累计超过上限明确拒绝，需新建会话并只上传需要的文件。模型输入预算仍适用于选定摘录及工具历史，但不再因未读取文件的全文大小直接拒绝基础 Context。当前没有历史附件的逐轮选择／移除控件，也没有前端分段选择；不承诺无限附件或无损压缩。
- `GET /files`、`GET /files/{id}`、`GET /files/{id}/content` 和 `DELETE /files/{id}` 均校验当前用户；身份不取自上传请求。启用登录时写入还校验 Origin 和会话 CSRF。下载强制 attachment、nosniff、no-store，不向浏览器暴露对象地址。删除撤销后续读取，但不会删除已保存回答和 Context 摘录。
- 上传前先记录两个确定的对象键；对象存储或数据库失败可按记录补偿，不扫描整桶。运维从 `apps/api` 显式执行 `PYTHONPATH=src .venv/bin/python -m nexuspilot_api.services.user_file_cleanup --limit 100`，重试失败删除及过期预留。命令会再次原子检查状态，不删除已变为 ready 的文件；**没有自动定时任务**，需运维配置调度。请求进程崩溃不自动恢复解析。
- 固定解析对象复用于同一上传，避免每个模型节点重复解析；不是按文件哈希跨用户共享缓存，也不是逻辑文件版本历史。不新增附件专用 Agent 或强制模式切换；两种模式附件工具循环已由阶段7实现，后台恢复仍未实现。

验证：独立 MySQL 8.4 和 MinIO 的并发幂等预留、额度、真实上传下载删除已通过；SQLite 测试覆盖归属、CSRF、失败补偿、清理竞争、删除撤销和两种模式的内容传递。浏览器使用真实 BFF/API/MinIO 与 FakeProvider 验证上传发送和刷新附件，不代表真实 Provider 或公网部署验收。Office 范围与时间见[执行环境评估](phase-7-tool-runtime-plan.md#office-提取范围与执行时间2026-年-9-月-16-日)。

本次执行记录：`NEXUSPILOT_TEST_FILE_RUNTIME=1 FILE_RUNTIME_TEST_IMAGE=nexuspilot-file-runtime:structure-test apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -q` → 481 通过、8 跳过（未启用的可选基础设施检查，不计通过）；上传专项 `test_user_file_infrastructure.py` 在显式配置的独立 MySQL/MinIO 上另行通过 1 项。Ruff 通过。数据库提交前失败及提交成功后响应不确定两种故障均有测试，后者保留 ready 对象，不误删。此次未连接真实模型、未迁移开发数据库，也未启用自动清理调度。

2026-09-26 连续文件上下文验证：先复现后续轮次丢失文件文字及删除后错误地继续生成 Context，再修复。新增 `test_conversation_file_context.py` 验证真实授权路径上的后续文字传递、来源／哈希／内容快照保存与重读、重放稳定、未来附件排除、跨会话拒绝和删除撤销；已有 Agent 测试同时覆盖上传轮次及后续轮次。完整后端与模型测试 499 通过、8 跳过；独立 MySQL/MinIO 文件上下文专项 5 通过。Context 内容与来源逐字段一致；时间比较按既有 MySQL DATETIME 秒精度处理，不将数据库精度差异误判为文字快照变化。没有新增迁移、没有修改前端组件，也没有本轮浏览器验收或真实 Provider 调用。
### 2026-10-18 本地 PDF 上传与分页验收

文件路径 `/Users/mith/Downloads/FULLTEXT03 (1).pdf`，SHA-256 为 `3e6fe276084c0d44b45ce806576bc8092f8934147b6f5885c490030b35e4caec`。旧实现失败原因已复现：746073 字节完整解析 JSON 超过为 SQL Context 设置的 55000 字节上传限制，并非原件超过 1 MiB。已完成本轮文件修复；没有迁移历史对话正文。

| 检查 | 本地实际结果 |
| --- | --- |
| API 上传与持久解析 | 通过；原件 941686 字节，40 页，1542 块，提取文字 95827 字节；完整 JSON 746073 字节存 MinIO |
| 原件/解析对象完整性 | 通过；两个对象回读及哈希校验，不截断、不写整份解析正文到 Context 表 |
| 工具分页 | 通过；每次 50 块，共 31 次；最后一页 42 块，next_offset=null、has_more=false；拼接与完整解析 blocks 逐字段一致 |
| Context 保存/重放/续聊 | 通过；文件清单 1348 字节，returned_blocks=0；重新查询、同键重放及续聊内容一致，没有自动注入文件正文 |
| 解析限制 | partial；第 2、4、6、10 页无提取文字；未验证图片含义，未实现 OCR/复杂表格理解，不能宣称整份文档视觉内容已读取 |
| 时间 | 最终复验上传/解析/写入 0.487 秒；含 Context 和 31 次工具读取共 1.346 秒，仅本机已有镜像的单次结果 |

本地记录：File `98588bd8-fa36-4874-b25c-e31c3b74bee1`，Session `c37ad61f-0785-4bae-be66-6c189dcf8f88`，Run `79f20443-3f67-4e81-b94d-fff98c888979`，Context `c801325d-122f-4570-826e-e0934ef58c40`。完整解析 SHA-256 `afec3bdd6d152bb9bbab8bb0d6194f1a28c4467b0abfdb44ef09c455538007ef`；不同上传的 file_id 不同，因此解析 JSON 哈希不要求相同。执行器固定镜像 ID 为 `sha256:24c46a817590b76ae7651dee4325eb1df98046f2a65e5ee4ce3132c10e930c51`。

复现命令（须先启动本地专用解析 Socket；仅连接既有验证 MySQL 13306、MinIO 19000）：

```bash
apps/api/.venv/bin/python deployments/file-runtime/serve_runtime.py --socket /tmp/nexus-file-pagination-20261018/runtime.sock --image nexuspilot-file-runtime:unified-local --create-directory
PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python deployments/verification/check_pdf_paginated_upload.py --pdf '/Users/mith/Downloads/FULLTEXT03 (1).pdf' --socket /tmp/nexus-file-pagination-20261018/runtime.sock
apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -q
```

验证脚本手工驱动平台工具服务并保存逐次调用，不代表真实模型自行选择了 31 次读取；模型请求数 0、无模型 token 费用。SQL 的诊断 attempt 明确标记 `tool_verification`/`local_verification`，不冒充真实模型调用。样本文本只进入本地解析执行器和 MinIO，没有发给外部模型；本次没有浏览器或公网部署验收。

回归：先新增测试复现上传 422 和自动全文注入两项失败，再修改业务实现。全量后端/模型测试最终 **862 通过、32 跳过**；跳过项为未开启的可选真实基础设施/执行器检查，不计通过。第一次执行中 10 项 Unix Socket 测试被沙箱禁止绑定，另 3 项旧测试要求全文注入；更新为新需求的“清单可见、未读正文不可见”断言并允许本机 Socket 后复验通过。显式配置隔离 MySQL/MinIO 后，`test_user_file_infrastructure.py` **1 通过**，验证并发预留不会超额及真实对象回读。Ruff 和 `git diff --check` 通过。增加解析对象被替换时拒绝生成、实际解析大小计费预留、独立对象上限拒绝及快速回复首轮不带未读正文的检查。

### 完整对话正文对象化

**状态：已完成本地实现与下述验收，未部署。** MySQL 只保存消息/运行归属、顺序、标题、状态、用量、费用、对象 URI/大小/哈希等结构化信息；MinIO 保存完整消息正文、Context 正文快照和完整工具结果。2026-10-18 已实现应用持久化路径；历史未迁移行仍从 SQL 读取，不能把新增写入对象化表述为已经搬迁所有历史数据。

当前实现以 `infrastructure/conversation_body_fields.py` 的字段清单为准：消息正文及消息元数据、Run 用户请求、Task 目标、Context 来源正文、工具输入/结果、工作流请求/节点输入输出及展示正文、公开事件内容、可展示推理、Handoff 和 Run Snapshot/Packet。完整供应商请求/响应延续现有 MinIO 保存方式。身份、顺序、角色、时间、状态、token/费用、依赖、来源 ID、哈希仍留在 SQL；目录/System Prompt 配置和暂停的其他 Memory 功能不属于对话记录迁移，不因本次变更启用。

应用数据库 Session 在写入时先上传不可变 JSON 并回读校验，再保存 `body_ref_json`，旧正文列仅存 NULL/空占位；在授权的 ORM 读取时恢复完整字段，保持既有 HTTP 返回结构。对象包含表、记录 ID、版本、字段，引用包含版本、URI、大小、SHA-256；必须全部匹配。浏览器不接收内部对象引用。原始 SQL 和字段投影仅用于结构化查询/维护，不会自动还原正文；业务代码须通过应用 Session 读取完整实体，不可用批量 UPDATE 写正文。

兼容与维护：新增迁移 `20261018_0024` 只加可空引用，不重写旧记录。`body_ref_json IS NULL` 时保留旧 SQL 读取；有引用但对象缺失、损坏或记录绑定错误则报错，不回退为空白。后续修改旧记录时转为对象保存。单条正文对象最多 16 MiB，是对象读写资源限制，不是模型 token 限制，也不是附件完整解析上限。

历史分批迁移、恢复和写入记录核对使用 `python -m nexuspilot_api.services.conversation_body_maintenance`，操作说明见根 README。每次写对象前保存精确写入记录；SQL 回滚/提交结果不明确时不立即删对象。清理默认仅检查，显式执行删除需停止 API/后台写入，且仅处理超过 24 小时、数据库当前未引用的精确对象。此命令不是自动运行的后台清理系统，也不代替 MySQL 与 MinIO 的配套备份。

实施与验收条件：

1. 逐一核对消息、有效模型请求、工具结果和节点输出的读写方，新增版本化对象引用；完整正文不混入元数据字段。按用户/会话授权后由服务端读取，不向浏览器泄露内部对象地址。
2. 新写入先写不可变对象并校验哈希，再事务保存引用/统计；数据库失败记录精确对象补偿，缺失或损坏对象明确报错，不返回空正文冒充成功。
3. 保留历史数据库正文双读，分批迁移、回读验证、记录进度及恢复路径；未完成校验前不删除旧正文，不改已执行迁移。
4. 验证首轮、续聊、工具多轮、流式结束、取消、刷新恢复、跨用户拒绝、对象缺失和重放一致性；完成后再更改本节状态。该后续迁移不阻塞本轮 MinIO 文件解析与分页读取，也不自动授权部署。

本地验收记录（2026-10-18）：

- 测试先行：新增消息对象保存/旧数据兼容/对象不可读用例在修改前因没有引用列及没有对象写入失败，修改后通过。存储与迁移专项目前 13 项通过，另覆盖跨用户在打开对象之前拒绝、合法引用被移植到另一记录、SQL 回滚写入记录、写入失败不保存空消息、提交确认丢失时保留已提交正文、分批 backfill/restore、禁止带引用降级、保留仍被引用对象及其写入记录。
- 真实本地 MySQL `13306` 已完成 `20261008_0023 → 20261018_0024`，只增加可空列，未执行历史全量搬迁。真实 MySQL/MinIO 的 `test_resource_authorization.py` 以两个实际登录账号做双向权限检查，2 项通过；每项只创建/删除随机命名测试库。
- 浏览器经真实 Next BFF/API、MySQL 和 MinIO，模型使用脚本 Provider，不发送付费请求。会话 `4b6d33ea-5338-46ba-a10f-2e04e893fd4c` 的两轮快速回复都可见，第一轮刷新后续聊成功；核对第二个请求的实际 `effective_messages`，包含第一轮用户和 Assistant 的完整正文。
- Agent 首次浏览器样本因测试夹具固定 `researcher`、Web 实际绑定 `planner` 而被计划校验拒绝；仅修正夹具选取实际允许角色，未放宽业务校验。复测完成工作流 `a3accfc7-d92e-4556-8dba-b940b5a28b70`、Run `39b667d5-e792-4b0e-8f83-4669b27b65e3`，11 个节点、4 次脚本模型调用，最终答案及执行摘要在刷新后恢复；测试期间重启 API/Web 后，既有快速回复正文仍可读取。旧失败记录保留，不冒充成功。
- 对该会话实际 SQL 列与对象逐条核对：7 条消息、4 个 Run、2 个 Task、58 个 Context 来源、2 个工作流、15 个节点（含失败样本）、35 条工作流事件、6 条响应事件及 1 个 Handoff，正文列均为兼容空占位，130 个当前引用对象可校验并读回。该样本未产生工具调用/可展示推理/Run Snapshot/Memory Packet 记录，检查输出标记 `not_exercised`；不将零行计为真实基础设施覆盖。工具循环、推理和取消场景由自动化回归覆盖，Memory 功能仍暂停。
- 可复现命令和环境在 `deployments/verification/README.md` 的“本地对话正文对象存储验证”；浏览器截图 `output/playwright/conversation-body-refresh-20261018.png` 为本地验收附件，不包含凭据。最终执行 `apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -q`：**875 通过、32 跳过**（120.42 秒），跳过的是未启用的可选基础设施/执行器检查，不计通过。Ruff 与 `git diff --check` 通过。没有真实 Provider 质量评估、生产迁移、批量历史改写或公网部署。

**前置阶段：** `phase-1-foundation.md`
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

## 目标

- 提供可独立调用的 Model Gateway、Context Builder、System Prompt／模型能力目录和 Evaluation。Context Builder 统一选择与保存业务来源；Gateway 在基础构建上追加受控工具历史，形成最终有效请求。两者责任及未完成保存项见下节，不把基础快照等同于全部模型消息。
- Memory按L0 Agent Working、L1 Session、L2 Collaboration/Run、L3 Project和L4 User分层记录；Memory Service负责事实、权限、版本和检索，正式启用的结果必须作为类型化来源进入Context Builder，不能由`/responses`或Agent直接读取。
- Knowledge是独立知识库能力；Knowledge Service负责文档、版本、检索和权限，正式启用的检索片段必须由Context Builder统一选择、预算和记录来源。
- Agent 不自行检索或裁剪 Session 历史、Prompt、Memory、Knowledge、Artifact 和 Handoff；通过来源接口获取这些内容。工具循环可维护同节点按序消息，由 Gateway 校验及追加，不能把任意自由文本冒充授权工具结果。
- 业务调用方通过同一请求、响应和流式事件协议调用 OpenAI、DeepSeek、Anthropic、Gemini 和 OpenAI-compatible 服务。
- 每次模型调用、Context Build、Prompt渲染和Evaluation都具有可查询输入来源、结果、耗时或错误。尚未通过启用门禁的来源不会进入模型，但其未来接入点统一保留在Context Builder来源合同中。
- 明确不处理：RabbitMQ Worker、任务自动领取、总控与工作模型、工具执行循环、MCP、代码搜索、LangGraph、OpenTelemetry 和前端页面。
- 明确不处理：自训练模型选择器、默认多候选答案投票，以及对真实供应商发起默认批量测试请求。

## 当前事实

- 当前实现：阶段1已经完成 User、Session、Message、Run、Task、Model Attempt、Provider Transport Attempt、Run Artifact 以及内部审计资源的新增、查询和必要动作闭环。
- 当前实现：`llm_attempts` 已保存供应商、模型、token、费用、耗时、供应商请求编号、原始请求/响应 URI、错误、不含Prompt正文的稳定前缀指纹，以及最终生效的思考开关、统一等级、来源、策略版本和Provider映射值；阶段2及后续工作流应复用该表，不创建第二套调用记录。
- 当前实现：`ObjectStorage` 已能保存二进制内容并返回 SHA-256、大小和 `minio://` URI；原始供应商请求和响应应复用该能力。
- 当前实现：`ObjectStorage` 已增加受控删除、按前缀列举和内容 hash 复核；Memory 快照会在登记数据库记录前复核对象。自动孤儿对象巡检与补偿任务尚未实现。
- 当前实现：`POST /api/v1/runs/{run_id}/attempts` 是阶段1的记录写入接口，不具备模型调用能力。阶段2增加统一生成接口后，该接口暂时保留，用于内部迁移和兼容，不作为普通业务调用入口。
- 已验证：四家 Provider、OpenAI-compatible codec、统一 Responses、SSE、重试、费用与原始响应证据；DeepSeek 使用专用 Chat Completions 适配器完成最新模型、思考参数和流终止约束。
- 当前实现：带`current_user_message_id`的`POST /api/v1/responses`会在Provider调用前创建Run锚定的Context Build，并把`context_build_id`关联到Model Attempt；未带锚点的低层兼容调用仍只使用显式`input`，不宣称具有Session语义。
- 已验证：Memory 事实账本与确定性检索基线包含正式归属、来源、不可变内容版本、状态、逻辑删除、持久化幂等、并发冲突、词法排序、检索证据和 mutation 审计查询。
- 当前实现：平台已提供可选登录和 Cookie 会话，Web BFF 与 FastAPI 均校验资源主体归属；公共和内部服务 Key 不替代生产用户身份。现有资源授权已通过本地双向检查，目标服务器仅完成部分资源冒烟；不将这些结果解释为已暂停 Memory 能力可以正式启用。验证范围见[阶段5](phase-5-platform-identity-credentials.md)。
- 当前实现：L0 手动检查点、L1 Session State/Summary、L2 Handoff/Run Snapshot、L3 Project/Profile、L4 User Profile 及对应内部接口已经存在，并通过 SQLite/伪对象存储契约测试。这些代码只作为 Memory 规划验证和实验性准备保留；Memory Packet 没有创建流程或 Model Attempt 绑定，本阶段不补该链路。
- 已验证：Context Builder当前稳定请求接受平台安全指令、显式instruction、指定数量的Session Message，以及运行时引用的System Prompt Release、文本Artifact、Agent Handoff、已完成工作流节点和Evaluation；tokenizer/context window来自启用的Model Catalog，历史读取使用不可变内容快照。System Prompt引用只允许由后端策略或受信内部调用生成，普通浏览器不能选择、提交或查看。Memory、Knowledge 仍未正式启用；附件 Tool 结果由阶段7循环和 Gateway 追加，并非已登记为基础 Context 的通用 Tool 来源，详见下文。
- 已验证：Prompt Registry 强制 placeholder 与变量 Schema 一致、严格标量类型、不可变单调版本、显式启停和 MySQL 并发版本锁；Model Catalog 按创建证据选择最新启用快照，禁用后回退到前一个启用版本。
- 已验证：Evaluation/Guardrail 拒绝未知或非法规则，同一幂等键只允许相同输入重放，候选 Attempt 必须属于指定 Task，历史非法规则安全失败。
- 当前结论：阶段2稳定范围已满足完成条件。Memory 仍是规划和实验性准备代码，Knowledge 仍等待独立知识库规划；两者均不因阶段2完成而成为运行时能力。
- 当前约束：后端依赖必须安装在 `apps/api/.venv`，测试、迁移和静态检查均从该虚拟环境运行。
- 当前应以什么为准：用户目标和本计划优先；统一请求/响应结构、MySQL 事实边界和 MinIO 大对象边界以总技术方案为准；具体字段以实际 ORM 模型和迁移为准。
- 待确认事项：各环境可用 API Key、代理地址及费用表不写死在本计划中；模型 allowlist 通过环境配置提供，后续由 Model Catalog 保存有来源的能力快照。Memory 的正式启用阶段和 Knowledge 的独立路线图均待后续决策。

## 已确认的实施边界

### 身份与资源归属

- 当前公共业务接口同时检查访问主体和资源归属；平台级 API Key 仅为服务认证，不把请求中的 `owner_id` 当作身份。当前认证、免登录及 CSRF 规则统一见阶段5，原“仅受信调用方”的历史范围不适用于已开放的用户资源。
- 阶段2所有资源必须在 Service 和数据库查询中校验 User、Project、Session、Run、Task、Agent Run 的关系，防止受信调用方误把一个用户或项目的资源关联到另一个 scope。
- 直接面向最终用户的身份认证和授权不在阶段2实现范围。现有实验性 Memory/Knowledge 接口不得被前端视为最终用户能力；正式开放前必须先确定认证主体、权限声明和越权响应。
- 内部评估、规则管理和模型目录写入接口继续要求公共 API Key 与内部 API Key，不能仅依赖公共密钥。
- 阶段2的测试 User 由测试 fixture 在独立事务中创建，平台测试 key 在测试运行时注入；Provider 单元与契约测试使用假传输，真实供应商冒烟只在显式开启时从进程环境读取密钥。阶段2不需要也不得增加 `/auth/test-login`、固定后门用户或仓库内供应商密钥。完整最终用户接口进入独立的[阶段5身份与凭据规划](phase-5-platform-identity-credentials.md)，不改变阶段2完成状态。

### 删除与保留

- 阶段1的 Message 是不可变历史，不增加通用删除接口作为阶段2前置条件。
- 现有用户长期记忆事实必须提供逻辑删除；删除后不再参与检索，但保留最小审计字段、来源标识和删除时间。当前 Memory 正文仅存在 MySQL。规划中的 JSON/Markdown 快照还必须处理 MinIO 对象、当前快照指针和模型读取包，不能用当前单库删除行为代表五层 Memory 已经完成。
- Knowledge 的停用、版本替换、对象保留和清理策略留到知识库独立规划中确定；阶段2不固定这些公共合同。
- Context Build、模型调用和 Evaluation 是执行证据，不提供普通修改接口；其敏感大对象按统一保留策略处理。
- 物理删除、法定保留期和最终用户数据导出属于部署策略，不能由业务 Service 静默猜测默认值。

### 资源限制

- 所有列表继续使用签名 cursor，默认 `limit=20`，允许范围为 1～100；cursor 必须绑定完整查询条件。
- Context 请求必须设置平台允许范围内的 `token_budget`，第一版范围为 256～200000；实际可用预算还必须受模型目录中的 context window 和预留输出 token 限制。
- 当前实验性用户长期记忆事实正文只保存在 MySQL，单个不可变版本的 `content_text` 长度为 1～4000 个 Unicode 字符；超过上限的资料只能作为 Artifact 保存，不能用 `content_uri` 绕过限制。未来是否进入 Knowledge 必须等待知识库规划。L1～L4 快照和 L0 检查点规则只作为 Memory 设计合同保留。
- 每个 Memory 版本必须具有 1～20 个来源，正文最多保留 256 个不重复的哈希检索词；单次查询最多生成 64 个查询词。
- 实验性 Memory 检索查询长度为 1～2000 个字符，返回数量范围为 1～100，独立 token 估算预算范围为 1～20000；未来若允许 Context Builder 使用 Memory，必须使用目标模型 tokenizer 重新核算。
- Knowledge 的上传、分块、检索数量和资源上限不在阶段2承诺，后续知识库规划必须重新确定并验证。
- Prompt 变量数量、渲染结果长度、规则数量和 Evaluation 输入长度必须有配置上限；超过上限返回输入错误，不进行截断后伪装成功。

## 后续文件输入工作项（2026 年 9 月 5 日，进行中）

2026 年 9 月 12 日实施顺序：先补阶段2～4可独立验证的基础能力；附件专用 Agent、自动工作流路由及模型文件工具循环后置。程序解析和范围读取不依赖这些新增编排，也不代表正式用户上传、文件版本或持久解析缓存已经完成。

用户上传与本地项目统一设计见[文件上传与个人项目工作区设计](../architecture/platform-roadmap.md#文件上传与个人项目工作区设计)。这属于后续增强，不改变阶段2原有完成判断。当前受信 Run Artifact 上传不等于正式附件服务，现有跨 Run 禁止引用的规则不直接放宽。

- 区分发送前的用户文件版本、已存在 Run 产物和本地工作文件；Context Builder 增加明确的固定文件版本来源，不接受浏览器提供对象 URI。
- 解析输出携带源版本/哈希、解析器版本、页码或行号、覆盖与失败事实。按运行固定解析结果，替换文件或重新解析不修改历史上下文；对象型 Message 用正式文件引用衔接，不存临时下载 URL。
- 原文读取先验证主体和项目范围、实际大小/类型/哈希，再进入模型输入。不能只传摘要却宣称模型获得全文；超出模型输入能力时返回可解释覆盖或要求缩小范围，不静默截断必需来源。
- 小文本直接复用受控解析与 Context；大型文件分段引用在同一预算与证据合同内实现。此设计不新增模型输出 token 限制，不恢复 Memory/Knowledge 或整库向量检索。
- 新增测试先覆盖正常、跨用户/跨项目、损坏对象、错误 MIME/编码/哈希、版本替换、部分解析、删除失权和固定来源回放；真实 MinIO 与最终用户上传验收由阶段5/4协作完成。

### 统一解析合同与实施清单（2026 年 9 月 5 日）

当前有效设计为平台统一解析，再将文字/表格经 Context Builder 交给模型，不按 Provider 品牌分叉文件理解路径。跨阶段边界以[路线图](../architecture/platform-roadmap.md#统一文件解析与按需读取设计2026-年-9-月-5-日)为准。下列完整目标中，`FileParseRequest`、`FileParserDescriptor`、`ParsedFileDocument` 已有最小合同；固定上传解析结果已保存，逻辑版本历史和模型工具尚未完成。具体当前字段和接口以本节“本轮实现与运行说明”为准，不能把完整目标当作现有 HTTP 接口。

#### 输入、解析器与结果

- `FileParseRequest`：文件版本 ID、受控解析选项及可选页/工作表范围。身份、项目、Run 绑定由服务端确认，不接受任意 URL、绝对路径或模型声明的权限。复用 Artifact 的测试入口继续限定当前 Run，不放宽跨 Run 读取。
- `FileParserDescriptor`：解析器名称/版本、实际支持格式、正文/表格/OCR 等提取能力、选项模型、结果结构版本、依赖及资源策略。程序根据检测格式选择已启用解析器；首版预安装，不动态安装插件。专业引擎只在适配器内部使用，平台合同不直接依赖其全部类型。
- `ParsedFileDocument`：文件版本/源哈希、解析结果 ID/版本/哈希、解析器及配置版本、内容块/阅读顺序/层级、表格结构、来源位置、提取方式、覆盖及失败记录。结构化正文为正式结果，文本、Markdown 和表格 JSON 是视图。
- PDF 保留可用页码和坐标，文本保留行号，Excel 保留工作表/单元格，Word 保留段落/表格位置，PPT 保留幻灯片编号；无法可靠获得页码时不编造。表格区分公式与已有缓存值，不执行宏、不刷新外部引用、不声称重新计算公式。
- 提取方式区分原生文字、OCR 和模型生成描述；不确定置信度允许缺省，不伪造统一分数。解析完整/部分/失败与模型实际读取范围分别记录，空结果不默认成功；扫描页、损坏/加密文件及缺失区域提供原因。
- 原件与解析结果独立版本化。重新解析同一原件创建新结果，Run 固定所用版本；不得覆盖历史 Context。完整正文保存在 MinIO，数据库只管理引用和必要状态，具体持久化结构在实施前与阶段5文件版本合同共同确定，不在本次文档修改中增加迁移。
- 解析缓存限定授权范围，依据文件版本/哈希、引擎版本和配置复用，读取仍重新鉴权。解析缓存不是模型 token 缓存。对象丢失/替换明确失败，不用新内容填补旧快照。

#### 模型可见读取合同

文件工具定义已集中到[工具主计划的文件工具目录](phase-7-tool-runtime-plan.md#文件工具只是目录中的一组)，本文不再维护第二份工具名称/参数/执行待办。现有解析模块继续提供确定性正文、表格、目录和搜索服务，工具适配器调用它们，不在模型层执行 Shell。

本模块保证：Context 保存固定来源、哈希、实际读取范围和覆盖事实；模型/浏览器不能指定 owner、任意存储地址或权限。完整结果与模型输入分开，超出上下文容量时明确说明范围；不把摘要当全文，不新增统一低输出 token 上限。工具调用、Provider 能力预检、续接、运行权限与端到端测试统一在工具主计划交付。

#### 已实现与未实现（代码核对）

| 工作项 | 当前事实 | 后续设计 |
|---|---|---|
| 统一模型网关 | 已实现统一请求/响应、生成/SSE、Provider codec、重试和调用证据 | 保留，接收解析后的文字/表格，不把解析职责放到 Provider |
| 统一 Context Builder | 已实现消息锚点、预算、固定来源及历史回放；支持 System Prompt、文本 Artifact、上传文件解析来源、Handoff、节点和 Evaluation | 逻辑文件版本历史尚未实现；基础快照与最终有效请求分开记录，保存缺口见下文 |
| System Prompt 与模型目录 | 已实现版本、启停、渲染、模型能力和思考配置 | 保留 System Prompt 后端边界，工具循环依据逐模型工具能力，不依据原生附件能力 |
| Evaluation/Guardrail | 已实现规则校验、幂等和结果查询 | 复用错误与证据机制；解析质量和引用完整性需新样本验收 |
| 模型工具协议 | 已实现 `ToolDefinition`、`ToolCall` 和工具消息表达，两种模式已有附件只读循环 | 工具登记、权限、读取及模型适配由阶段7维护；通用代码工具尚未接入聊天 |
| 原生文件直传基线 | 新建 native Context 返回 409 并指向 `parsed_artifacts`；历史 Context 可只读回放，禁止用于新生成；低层 codec/合同保留兼容 | 不再作为产品输入路径，保留旧证据，不把原生测试计作解析验收 |
| 文件解析器及统一结果 | 已实现 UTF-8/CSV/TSV/JSON、隔离 PDF 正文、独立容器 DOCX/XLSX/PPTX 提取与统一结果转换；新版 Office 结果包含完整工作表/幻灯片位置清单 | OCR、结构化 PDF 表格、Word/PDF 完整章节目录和 Office 复杂视觉内容尚未实现 |
| 正式文件上下文 | 已实现上传、固定解析结果、范围读取、哈希及 Context 快照；两种模式工具可读取授权附件 | 逻辑版本历史及项目文件产品仍未实现，不把已上传的固定快照称为完整版本管理 |
| 解析复用及失败事实 | 已实现覆盖／遗漏、安全失败、原件变更复核和 PDF／Office 隔离；同一上传复用 MinIO 固定解析对象 | 跨文件缓存、独立解析版本管理和后台恢复未实现；不能把固定对象复用称为跨文件缓存 |

以上已按本轮代码与测试更新。Memory Packet、后台形成、跨存储 Memory 补偿、Memory 孤儿清理及 Knowledge 仍不属于原阶段2完成条件；上传/解析专用补偿不得因此省略。

#### 本轮实现与运行说明

- 入口仍是受信调用方 API，不是浏览器上传：`POST /api/v1/responses` 的 `context_source_references.parsed_artifacts` 或 `POST /api/v1/context-builds` 的 `source_references.parsed_artifacts` 接收 `[{"artifact_id":"当前 Run 的 Artifact ID","block_offset":0,"block_limit":20}]`。省略范围请求全部内容，超限明确要求选择范围，绝不静默只给前 20 块；非零 offset 必须同时指定 limit。每次最多 10 个引用，不能重复或与文本/native 路由同时引用同一对象。
- `FileParseRequest` 当前使用 `source_id/source_hash/filename/media_type` 固定已有 Artifact，暂无正式 `file_version_id`。`ParsedFileDocument` 记录引擎版本、原文块、行号、PDF 页码、CSV 行/列和覆盖；完整解析结果的哈希写入 Context `source_version`。本轮仅选中内容与提取/读取元数据保存到现有 Context，原件仍在 MinIO；没有新增表/字段或迁移，也没有把完整解析结果写成独立 MinIO 对象。未选部分后续仍需从原件重新解析，不宣称有持久解析缓存或跨升级的全结果分页快照。
- 支持 UTF-8 文本/Markdown/代码和部分文本型 MIME、CSV/TSV、JSON；JSON 保留原始数字与文本，拒绝重复 key、非标准数值和过深嵌套。CSV 原样保留单元格，不推断表头、不执行公式。XML/YAML 只作为原文读取，不执行实体、模板或语义加载。图片、OCR、音视频和旧版二进制 Office 不支持。
- 2026 年 9 月 7 日新增 DOCX/XLSX/PPTX：配置 `NEXUSPILOT_FILE_RUNTIME_SOCKET` 后，同一 `parsed_artifacts` 入口将已验证的文件字节交给独立执行器，每次创建固定镜像、无网络容器；API 不导入 Office 库、不访问 Docker daemon。缺配置、执行器离线/繁忙、解析失败均在模型调用前返回明确错误，不在宿主机尝试替代解析。详见[阶段7运行说明](phase-7-tool-runtime-plan.md#预装文件执行环境)。
- Office 来源结构复用 `parsed_file.v1` 并增加可空位置字段：Word 使用段落/表格编号和已识别的 Heading 样式级别，Excel 使用工作表、行和单元格范围，PPT 使用幻灯片/形状编号及备注标记。`line_start/line_end` 对 Office 为 null，不编造页码或原文行号；旧 Context 原样回放，新结果哈希允许因结构扩展变化。
- Excel 分别保存单元格字符串值、类型、数字格式、公式与缺失计算结果位置，并保留合并范围及隐藏工作表/行标记；不计算公式，不把缺失计算值写为零，不刷新外部链接。Word 合并格可能重复文本，嵌套表格、页眉页脚、修订、图片等不完整；PPT 保留形状文件顺序，不声称是视觉阅读顺序，组、图表和图片未解释。三类结果目前都标记 `partial`（部分提取）并附明确警告，不能视为完整文档理解。
- PDF 使用预安装的 Poppler `pdfinfo/pdftotext`，仅提取嵌入文字并保留页码；无文字页记录未覆盖，全部无文字则明确失败。PDF 表格仅布局文本，不声称恢复单元格或理解图片；加密/损坏 PDF 拒绝。2026-10-07 起 macOS/Linux API 均通过 `NEXUSPILOT_FILE_RUNTIME_SOCKET` 请求专用 Linux 解析容器，缺配置或执行器失败不退回宿主机。Poppler 与中文映射数据仅在解析镜像构建时安装，不由模型或 API 下载。
- PDF 子进程在专用无网络容器内使用净化环境、私有临时目录和 CPU/文件大小/墙钟时间约束；与 Office 共用非 root、只读根文件系统、内存/进程限制。API 不持有 Docker 权限，不挂载应用凭据到解析容器；本机 Linux ARM64 检查不替代目标服务器验收。
- 资源边界：本轮受信解析单文件 1 MiB、最多 10000 块、CSV 每行最多 1024 列、PDF 最多 200 页；解析并发每 API 进程 2。此为解析基线，不改变现有 Artifact 上传上限。读取函数每页最多 100 块/128 KiB并返回后续位置；现有 MySQL Context `TEXT` 限制下，每个序列化来源最多 60000 字节，超限选择更小块范围。不是模型输出 token 限制；未扩容数据库字段来掩盖过大输入。
- 已有四个程序读取函数：`get_file_outline` 返回块/页/表格行数、已识别标题、工作表/幻灯片位置和遗漏事实；其余为 `search_file_content`、`read_file_content`、`read_file_table`。新版 Office 结果的可选 `structure` 保存全部工作表（含 hidden/veryHidden）或全部幻灯片，按原件顺序记录各位置的已提取块数；空白或纯图片位置也保留，但不伪造正文。`structure_complete=true` 仅表示位置清单完整，不表示文字、图片或章节已完整理解，Office 结果仍为 `partial`。旧镜像没有清单时，只列有内容的位置并返回 `structure_complete=false`。这些函数尚未注册为模型工具，无 `list_run_files` 新工具/API；不得声称 Agent 已能自主查阅文件。

- 2026 年 9 月 12 日新增 `parsed_artifacts[].selection` 原生位置选择：`{"kind":"pdf_pages","page_start":2,"page_end":4}`、`{"kind":"word_table","table_number":2}`、`{"kind":"worksheet","sheet_name":"预算","cell_range":"B2:D10"}`、`{"kind":"slides","slide_start":2,"slide_end":3}`。完整解析后先筛选位置，再应用原有块分页；不是限制解析器只读这些页。工作表精确匹配，矩形选择同步裁切单元格值、公式、类型和格式，保留实际坐标及相交的原始合并范围。不支持的格式/混合字段/非法或空范围明确失败。PDF 不能超过已知总页数；新版 PPT 清单存在时也拒绝超过总幻灯片数的范围。旧结果仍只能按已提取页号匹配，不能据此推测总数。仅选择无可读块的位置明确失败，不发送空文件内容给模型。
- 2026 年 9 月 16 日新增结构清单校验：格式、位置唯一性、连续幻灯片编号及逐位置块数须与正文一致，否则拒绝执行器结果。清单随选定内容进入 Context 固定快照，即使只读部分单元格，也保留完整位置清单和遗漏说明。解析器版本为 `office-v2/依赖版本`；缺少清单的旧结果不新增 `structure:null` 到快照或文档哈希输入，保留旧序列化行为。整个文件均无可读块时仍明确失败，未新增纯图片理解。
- 范围快照保存原件的 `document_hash`、全文警告/遗漏、实际选择和分页事实，以 `selection_hash` 固定返回内容的来源版本；历史未指定选择的请求哈希和快照保持兼容。测试入口为 `apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/test_parsed_file_ranges.py -q`；它验证当前受信 Run Artifact 到 Context 的接入，不证明真实用户上传已实现。
- 新生成统一提供文字/表格，不要求 `native_input` 或视觉能力，四家 Provider 契约与 DeepSeek 流式入口已覆盖。Context 历史回放不重新解析；生成前复核原件归属、元数据和哈希，已替换/丢失对象不能利用旧快照继续发模型。旧 native Context 可以查询，但生成明确拒绝并要求建立新的解析输入。
- 验证入口：`apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/test_file_parsing.py apps/api/tests/test_parsed_file_context.py apps/api/tests/test_native_file_context.py -q`；真实 PDF/Office 验证改用 `NEXUSPILOT_TEST_FILE_RUNTIME=1 FILE_RUNTIME_TEST_IMAGE=nexuspilot-file-runtime:unified-local apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/test_office_runtime_executor.py -q`，先构建指定镜像。四格式两种模式与中文/加密样本检查见[本地文件验收说明](../../deployments/verification/README.md#本地统一文件解析验收)。默认跳过容器测试不计为通过；不调用真实 Provider，不把 SQLite/内存对象测试当作 MySQL/MinIO 验收。

#### 实施顺序与验收

2026 年 9 月 16 日位置清单扩展验证：新增清单测试在修改前 10 项失败，修改后相关解析/范围/Context 测试 57 项通过。使用独立测试镜像执行 `NEXUSPILOT_TEST_FILE_RUNTIME=1 FILE_RUNTIME_TEST_IMAGE=nexuspilot-file-runtime:structure-test apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -q`，466 项通过、7 项跳过（4 项真实基础设施、2 项 PDF 隔离、1 项显式 MySQL 轮次迁移测试，未设置相应环境）。之后补充旧结果快照字段与历史文档哈希断言，`test_parsed_file_context.py` 与 `test_file_structure.py` 合计 29 项通过。Linux ARM64 Docker 六项运行时检查通过，API/模型包/执行脚本 Ruff 与差异检查通过。本轮未调用真实 Provider、未修改开发数据库、未替换默认镜像；依赖复用验证不等于全新镜像或云端验证，部署要求见[阶段7](phase-7-tool-runtime-plan.md#office-执行器启动与权限)。

此前文本/PDF 实施验证：API 与模型包全量测试 393 项通过、6 项跳过（4 项原有真实基础设施测试及2项需显式开启的 PDF 隔离测试）；PDF 两项已在 macOS 单独开启并通过。Ruff 与差异检查通过。另补输出符号/硬链接拒绝、主进程不跟随解析器预置输出链接、跨 Provider 相同提取内容、DeepSeek SSE、Context 原文替换后拒绝生成与 MySQL TEXT 大小边界测试。没有进行数据库迁移或真实 Provider 调用。

2026 年 9 月 7 日 Office 执行器实施验证：`NEXUSPILOT_TEST_FILE_RUNTIME=1 apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -q` 最终运行 424 项通过、6 项跳过，耗时 49.58 秒。包含实际 Unix Socket → Docker → DOCX 提取 → API 块转换测试，以及四家 Provider 的受信 Artifact/Context 接口回归（Provider 和对象存储使用测试替身）。6 项跳过仍为上述原有基础设施/PDF 显式测试，本轮未重跑其真实环境。`make file-runtime-check` 的六项 Linux ARM64 容器检查通过，包括真实 DOCX/XLSX/PPTX 脚本；API 和执行脚本 Ruff、`git diff --check` 通过。未调用真实 Provider、未修改数据库或实际 `.env`；x86 构建失败与云端待验收项见阶段7运行说明。

2026 年 9 月 12 日范围读取与阶段2～4回归：上述全量命令同时设置 `NEXUSPILOT_TEST_TURN_DATABASE_URL` 指向已迁移至 head 的临时 MySQL 8.4 测试库，结果为 **456 项通过、6 项跳过**，耗时49.95秒。其中24项新增范围读取测试通过，实际 Office 执行器及独立连接的轮次提交去重测试通过；6项跳过仍为原有基础设施/PDF显式测试。API Ruff通过。未调用真实Provider，未修改开发数据库；轮次去重所需新表与迁移属于[轮次保存与重试](phase-1-foundation.md)，不是新增文件版本或解析缓存表。

| 顺序 | 阶段2具体任务 | 测试与完成条件 |
|---|---|---|
| 1 | 先建立文件解析/来源合同测试 | 正常/部分/失败、不同定位方式、空正文、重复/越权引用及旧文本请求兼容 |
| 2 | 实现文本与表格基线，再接文档适配器 | 编码/行号、表头/单元格、中文 PDF 与复杂表格固定样本；支持范围与结果相符 |
| 3 | 实现固定解析结果与 Context 转换 | 版本替换不污染历史、缓存不越权、分页连续且可追溯、预算不足不丢原文 |
| 4 | 替换新文件产品的原生直传路径 | OpenAI/Anthropic/Gemini/DeepSeek 使用相同提取内容；不依赖视觉能力，不静默换模型 |
| 5 | 联合阶段5/7/3验收 | 正式文件授权、对象缺失、隔离失败关闭、工具读取与流式回答；真实 MinIO 验收不能用假存储代替 |

代码替换前检查所有 `native_artifact_ids`/`attachments` 调用方、测试和历史 Context：保留必要的历史只读解释，不覆盖既有证据；新产品路径只有统一解析，不同时维护两套默认文件策略。迁移/公共接口兼容范围需按实际持久数据确认，不能删除用户未提交修改或静默改写历史。

暂时依赖失败才有界重试；权限/格式错误明确拒绝。解析结果写对象后提交数据库失败需可恢复补偿；沙箱、OCR/文档依赖及真实多用户授权未通过时，不开放对应格式或入口。独立后台恢复在阶段10完成前明确不可用。

### 原生文件输入基线（历史实现记录，已退出新生成路径）

早期基线使用受信 Run Artifact 验证；现已新增 `user_files` 和浏览器上传，范围及验收见文首当前实现。逻辑文件版本历史和模型工具仍未实现。

以下仅记录上一轮原生直传的实现与验证，不是当前新请求行为或继续建设方向。当前新 Context 拒绝 `native_artifact_ids`，旧 Context 仅允许查询；低层原生合同/codec 与旧对象校验保留兼容。当前使用方式以上文“本轮实现与运行说明”为准。

- 模型内部 `Message` 保留原有字符串 `content`，新增可选 `attachments`；`NativeFileContent` 只携带显示文件名、MIME、SHA-256 和验证后的 Base64 字节，不接受路径、URL、MinIO URI 或供应商 file ID。二进制输入仅允许在 user 消息中，文本与工具消息兼容原行为。
- 原生路由：OpenAI Responses 使用 `input_file`/`input_image`；Anthropic Messages 使用 `document`/`image`；Gemini GenerateContent 使用 `inlineData`。首批共同支持 PDF、PNG、JPEG、WebP；GIF 只在 OpenAI/Anthropic 的已确认模型策略中允许。这里是适配器可表达的格式集合，不代表这些供应商的任意模型均可用。
- DeepSeek、OpenAI-compatible 当前适配器没有原生文件输入实现，明确返回能力不支持，不丢弃文件后继续回答。不自动换模型、不自动 OCR、不调用 Shell 或外部工具。Word/Excel/PPT、音视频、HEIC/HEIF、图片输出及供应商 Files API 上传/缓存尚未实现；不能把支持保存附件等同于支持理解所有格式。
- 逐模型证据复用 `llm_model_catalog_versions.capabilities_json.native_input`，不复制到新表。目录创建接口严格校验保留字段；`GET /api/v1/model-capabilities` 返回 `native_input`。缺少策略返回 `null`（未知），仅 `supports_vision=true` 不会启用文件。启用原生 PDF/图片必须同时确认视觉能力、已实现的 Provider 格式及资源策略。没有批量猜测或修改现有模型记录。

以下只是测试策略示例，不是对任何真实模型能力或 token 用量的声明；实际值应在对应模型的能力快照中有依据地配置：

```json
{
  "capabilities_json": {
    "native_input": {
      "media_types": ["application/pdf", "image/png"],
      "max_file_bytes": 1048576,
      "max_request_bytes": 2097152,
      "max_files": 2,
      "input_token_reserve_per_file": 4096
    }
  }
}
```

- 受信 `POST /api/v1/responses` 通过 `context_source_references.native_artifact_ids` 引用**当前 Run** 的既有 Artifact，必须同时指定 `current_user_message_id`；Context 创建使用对应 `source_references.native_artifact_ids`。与文本 `artifact_ids` 分开，不允许同一对象双重引用或重复 ID。普通 HTTP `input` 内联二进制被拒绝；未来用户文件上传必须仍走正式轮次入口自动选择工作流，不能用本内部基线绕过该设计。
- Context Builder 先校验 Run 关系、模型策略、对象大小/类型、流实际长度、SHA-256 与格式签名，然后保存 `native_artifact.v1` 清单，包含源哈希、文件名、大小、MIME、原生处理模式及覆盖状态。不在数据库 Context 正文、Context 返回或模型调用的原始 HTTP 请求证据中复制 Base64；Attempt 通过 `context_build_id` 找到固定来源。
- Context 读取和幂等回放返回原清单，不重新解释文件；模型调用前根据同一模型快照重新校验当前 Run 归属、目录启用状态、Artifact 元数据及对象内容。对象丢失、被替换或能力撤销时明确失败，不偷换成最新内容。未携带原生文件的旧 Context 请求保持原幂等哈希算法结果。
- 清单记录 `coverage=provider_interpreted_not_locally_verified`：平台只验证字节与格式签名，不声称检查了全部页面、正确解码了所有图像或已完成本地解析；深层文件损坏、加密、页面/像素限制等仍由供应商校验，其失败沿用现有 Attempt 错误记录。页码/行号与解析覆盖证据待解析服务完成，不伪造。
- 保留原有 Context 输入预算。`input_token_reserve_per_file` 是明确配置的**原生输入估算预留**，加到清单文本估算中参与必选来源检查；不是实际计费或供应商保证，不把文件字节数当成页面/图像 token。实际 usage 以 Provider 返回为准；不足时不静默截断或删除文件。模型输出上限机制不变，仍允许既有 `65536` 设置。
- 当前内联传输的防御上限为单文件及整次请求解码字节各 20 MiB、总数 10，逐模型策略只能收紧；这些是本实现的内存边界，不是供应商上限声明。部署必须为 Base64/JSON 及其他文本留出供应商请求额度；大文件、按页计数/选择和供应商文件引用传输留待后续。

自动化验证涵盖原生内容合同、三家 Provider 的 PDF/图片字段映射和真实 HTTP 编码/SSE 解码（假传输）、未知能力、格式/哈希/大小错误、跨用户 Run 与同用户跨 Run、存储缺失、必需输入预算、固定清单回放及旧幂等兼容。没有调用真实供应商，也没有执行数据库迁移或改变现有模型能力记录。

上一轮原生基线验证（非当前统一解析验收）：API 与模型包全量测试 366 项通过、4 项跳过；原生定向测试 23 项通过；Ruff 与 `git diff --check` 通过。上述测试不替代真实供应商和 MinIO 端到端验收。

接口依据：[OpenAI 文件输入](https://developers.openai.com/api/docs/guides/file-inputs)、[OpenAI 图片输入](https://developers.openai.com/api/docs/guides/images-vision)、[Anthropic PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support)、[Anthropic 图片](https://platform.claude.com/docs/en/build-with-claude/vision)、[Gemini GenerateContent](https://ai.google.dev/api/generate-content)、[Gemini 图片格式](https://ai.google.dev/gemini-api/docs/image-understanding)。

## 阶段2核心能力范围

### Conversation 与 Context

Conversation Service 是读取和编排阶段1 Session/Message 的应用服务，不创建第二套会话或消息表。

第一版接口：

```text
POST /api/v1/context-builds/preview
GET  /api/v1/context-builds/{context_build_id}
```

请求至少包含 `user_id`、`session_id`、`provider`、`model`、`token_budget`，可以包含 `catalog_version`、`system_instruction`、预留输出 token 和最近消息数量。稳定合同不接受 Memory/Knowledge 开关，也不接受调用方指定 tokenizer。返回统一消息、总 token 估算、各来源记录以及未选中原因。

持久化事实至少包含：

```text
llm_context_builds
context_build_id, catalog_version_id, user_id, session_id, provider, model,
token_budget, reserved_output_tokens, recent_message_count,
tokenizer_name, tokenizer_version,
input_token_estimate, status, created_at

llm_context_sources
context_source_id, context_build_id, source_type, source_id,
source_version, source_order, token_estimate, selection_status,
exclusion_reason, content_hash, message_role, content_text
```

规则：

- 阶段2选择顺序固定为 system instruction、必要安全指令和最近不可分割消息；各来源使用独立预算，不能由单一长来源耗尽全部空间。Memory、Knowledge 和自动历史摘要注入留待对应能力正式启用后重新设计。
- 同一请求使用固定 tokenizer 名称、版本、来源版本和排序键；重复读取 `context_build_id` 必须能够解释当时输入，不使用当前最新内容覆盖历史证据。
- 单条消息超过该类预算时返回明确超限原因；不在 UTF-8 字符、工具调用结构或消息结构中间截断。
- 长对话摘要的形成和启用策略暂不纳入实际响应链路，原始 Message 始终保持不可变。
- Context预览与Provider调用分离，预览不发起模型请求、不产生供应商费用；运行时Responses和Agent调用使用同一来源选择服务创建独立Context Build。

### 统一模型上下文构建与阶段3、阶段4运行时集成设计

状态：进行中。Run与User Message原子提交、消息序号锚点、文本Session历史、幂等重放、Responses与Agent模型节点接入、System Prompt/Artifact引用合同、受限文本Artifact读取、Agent/Handoff/Evaluation类型化来源、Attempt/Node证据以及脱敏浏览器证据投影已经实现；普通Web已经停止读取Prompt目录、提交Prompt引用和显示Prompt证据，BFF也会拒绝浏览器伪造的Prompt引用。System Prompt具体选择策略仍需由后端按调用场景定义，当前快速回复不猜测默认版本。Memory、Knowledge、Tool来源、对象型Message生产读取和Artifact产品选择仍未实现。本工作项不改变阶段2已经完成的独立预览基线。

#### 2026年8月26日已实现基线

- `POST /api/v1/sessions/{session_id}/turns`在一个数据库事务中创建Run和带`run_id`的当前User Message；任一归属、状态或消息序号失败都会回滚二者。
- `POST /api/v1/context-builds`校验User、Session、Run与当前User Message关系，以当前消息序号排除稍后并发消息，并按最新完整User/Assistant轮次向前选择。当前文本、平台安全指令和受控节点输入属于必选来源。
- 运行时Context Build使用Model Catalog上下文窗口、tokenizer名称和版本，保存全部候选快照、选择状态、内容哈希和排除原因；相同幂等键与相同请求返回原构建，不重新读取Session。
- 快速Responses以Context Build消息替换单条Composer输入；Agent各模型节点在Session历史之后追加本节点类型化输入。Model Attempt、Agent Node Input和`context_assembly`分别保存`context_build_id`及实际选中的Message ID。
- 当前User Message若只有`content_uri`会在Provider调用前明确失败，不能转换为空文本。旧的对象消息仍记录为未支持来源；明确引用的Run Artifact已经支持受限文本读取，但Message对象内容尚未建立等价的类型、大小与归属合同。
- 当前兼容入口仍允许不带消息锚点的低层Responses请求，此时不会创建Context Build，也不会读取Session历史；Web普通对话已经迁移到原子轮次与消息锚点合同。
- `context_policy.v2`保存每个来源的类型、版本、内容哈希、信任级别、必选标记、token估算和纳入/排除结果。运行时只接受后端选择的System Prompt Release，以及明确引用且通过Run归属与完成状态校验的Artifact、Handoff、工作流节点和Evaluation；Artifact额外校验MIME、100000字节上限、对象大小、SHA-256和UTF-8完整性。
- Agent节点把已提交Handoff、确定性验证、独立审核和Evaluation作为独立来源交给Context Builder，不再同时复制进自由模型输入；当前User Message已经存在时也不重复附加`Run.user_request`。无Session锚点的低层兼容工作流继续使用原显式输入合同。

2026年8月28日验证结果：后端与模型适配层全量测试为`290 passed, 4 skipped`，Ruff全量检查通过；4项跳过仍是需要显式基础设施条件的测试。MySQL开发库已向前迁移到唯一head `20260828_0017`，现有`deepseek-v4-flash`和`deepseek-v4-pro`记录均已回填版本化思考策略；从`20260827_0016`生成MySQL离线迁移SQL成功。`alembic check`仍只报告Memory表三个归属外键在历史迁移中的`SET NULL`与当前ORM `RESTRICT`不一致；该漂移早于本次Context和思考策略迁移，因Memory仍暂停且改变删除语义不属于本工作项，本次没有顺带修改。

#### 对象型Message读取合同（阶段2合同补充，阶段4集成验收）

状态：进行中。当前只完成测试方式验证，不提供生产对象型Message读取。Web已提供浏览器本地文件选择、待上传展示和删除，但后端尚未提供上传及Artifact创建，因此选择文件后必须阻止发送。自动化测试仍通过内存对象存储夹具创建Run Artifact，验证普通Responses会把显式Artifact引用交给统一Context Builder；真实用户文件、对象型Message和上传后的产品选择等待正式上传合同。本工作项不更改阶段2原有稳定范围的完成状态。

- 对象型Message是正文保存在受控对象存储、Message只保存引用的消息，不等同于已经实现的Run Artifact。它必须复用Artifact读取时已经验证的归属、对象大小、内容哈希、UTF-8完整性和受控存储访问方式，不能让Context Builder直接读取任意`content_uri`。
- Message来源服务必须明确允许的内容类型、最大字节数、Message与User/Session/Run的归属关系、对象不存在或哈希不一致时的稳定错误，以及内容是否作为完整消息或有界片段进入上下文。未通过合同的对象类型继续在Provider调用前明确失败，不能退化为空文本。
- Context Build证据至少记录Message ID、对象内容类型、内容大小、内容哈希、读取结果、纳入或排除原因；浏览器和公开错误不得暴露MinIO对象键、签名地址或内部路径。
- 历史测试事实：ASGI测试使用`FakeObjectStorage`保存受限文本Artifact，普通`/responses`请求携带`context_source_references`后，Provider输入包含固定Prompt版本和Artifact正文各一次；跨Run Artifact仍明确拒绝。2026年8月31日确认Prompt属于System Prompt后，后续测试必须改为由后端选择Prompt，并断言普通浏览器不能提交或读取Prompt引用。对象型当前User Message继续返回`Current user message must contain readable text`，没有把`content_uri`转换为空文本。
- 正式实现前继续补齐合同测试：合法对象型Message、跨用户/Session/Run引用、对象缺失、大小超限、哈希不匹配、非UTF-8、未允许MIME、幂等重放和Provider未被调用。阶段4再覆盖上传、普通对话选择、错误可见性和刷新恢复。
- 阶段3不新增第二套读取器。Agent模型节点需要对象型Message时，必须通过同一个Context Builder和Message来源合同读取；合同未启用前继续明确拒绝。

#### 已解决问题与剩余目标行为

- 已解决：快速对话不再只发送单条Composer文本；Run锚定的Context Build会读取当前Message之前的完整有界Session轮次，并排除稍后并发消息。
- 已解决：`model_only_v1`的Controller、Worker、Reviewer和Final synthesis均按自己的Provider/Model创建Context Build，已提交协作来源不再藏在一个自由`request_input`中，当前`Run.user_request`也不会重复追加。
- 剩余目标：两种模式继续使用同一套来源归属、信任分级、消息锚点、预算选择和排除原因；Memory、Knowledge、Tool和对象Message只有通过各自门禁后才能增加来源Provider。
- Context Builder负责组装全部已授权且已启用的上下文。来源服务负责产生和校验自己的数据，Context Builder不能伪造Memory、执行Knowledge检索算法、修改Artifact或重写Handoff事实。

#### 全部上下文来源

| 来源类型 | 权威来源服务 | Context Builder职责 | 启用条件 |
|---|---|---|---|
| 平台安全与系统指令 | 平台策略、调用入口 | 作为最高优先级指令，单独映射为Provider系统指令 | 始终启用或由明确策略启用 |
| System Prompt Release | System Prompt Catalog | 固定模板版本、渲染变量和内容哈希 | 后端运行策略绑定已启用System Prompt版本；普通前端不可选择或感知 |
| 当前用户请求 | Run与当前User Message | 校验Run/Session/Message归属并作为必选来源 | 所有会话和Agent调用 |
| Session历史 | Session Message | 以当前Message序号为锚点，按完整轮次选择 | Session调用 |
| Session Summary与Working State | Session/Agent State Service | 作为带版本和覆盖范围的类型化摘要来源 | 对应能力通过启用门禁 |
| User、Project和执行Memory | Memory Service | 消费已授权检索结果或Memory Packet，保留层级、信任和版本 | Memory策略、权限、删除和预算门禁通过 |
| Knowledge片段 | Knowledge Service | 消费有界检索片段，保留文档版本、片段ID和外部不可信标记 | Knowledge权限、检索和引用门禁通过 |
| Artifact与对象消息 | Artifact/ObjectStorage Service | 受限读取、校验大小和哈希，按完整对象或片段选择 | 内容类型允许且调用主体有读取权限 |
| Agent任务与协作事实 | Agent Runtime | 组装Task、Node输出、Handoff、验证、Reviewer和Evaluation引用 | 对应节点合同已提交 |
| Tool结果 | Tool Runtime | 组装已批准、已执行和已脱敏的工具结果 | 阶段7权限和执行合同通过 |

Provider私有continuation不属于业务上下文来源，只用于同一Run、Task、Provider和Model下的加密推理或工具续接。

#### 基础上下文与最终有效请求

状态：基础构建及工具历史追加已有实现；2026-10-14 已修复无工具定义时的有效消息保存，相关本地回归 38 项通过。真实 Provider 多轮审计验收未执行。

- **基础 Context Build：** Context Builder 负责来源授权、版本／哈希、选择和排除、基础消息及预算快照。`context_build_id` 标识这个不可变构建，不代表之后每一条工具消息也已登记为 Context 来源。
- **工具历史：** 循环保存 Assistant 工具调用、Tool 结果及消息级 Provider 私有续接，Gateway 按原顺序追加；2026-10-18 已增加旧工具正文移出及来源保留的输入投影，仍检查总预算，不改原始对象或破坏调用／结果配对。具体实现限制由阶段7“长文件读取与工具历史”维护。
- **最终有效请求：** 是基础消息加实际追加历史及本轮 Schema／工具定义等配置。它应关联 Attempt、基础构建、来源和实际工具结果；由后端私有审计保存，不能直接把内部指令和私有续接公开给浏览器。Context 抽屉目前只展示基础构建，不宣称展示了全部有效消息。
- **已修复缺陷：** `ModelInvocationService._start_model_attempt` 现在始终保存实际 `request.messages` 的公开序列化到 `effective_messages`，不再依赖 `request.tools`。普通消息和携带工具历史但不再提供工具定义的请求均保存消息；消息级私有续接不进入明文审计。修复前已复现无工具定义的两个用例缺少该字段，修复后通过；不重复执行工具，不改变公共响应。
- **实际参数记录：** `effective_request.v1` 与 `effective_messages` 保存于同一 MinIO 请求对象，前者记录最终 system、工具定义/版本元数据、模型/输出/推理/超时等适配器输入参数，不重复复制消息。消息级私有续接只记录已加载状态的父 Attempt ID；请求顶层父 ID 明确只是待加载引用，不冒充加载成功。两者均不保存私有续接正文或 HTTP 认证 Header。它证明准备交给适配器的输入，不等于 HTTP 已发送或供应商已收到。
- **2026-10-19 原生文件工具输入清单：** 请求在历史投影后、供应商派发前生成 `tool_input_manifest.v1`，与 `effective_messages` 一同保存在该 Attempt 的 MinIO `raw-request.json`；MySQL 继续只保留 `raw_request_uri`，无迁移。清单记录消息位置/哈希、工具调用标识、文件快照、实际包含的块及文本哈希；已移出的正文只保留引用编号，不计为已输入。它是准备发送的输入证据，不是供应商收件证明或理解证明。只统计原生文件 Tool 消息，不解析用户文本来生成来源；Reviewer 单独注入的原文集合不在此版范围内。旧对象缺少清单时标记未知，不重写历史，也不能把旧记录的缺失解释为没有读过文件。测试与本地存储只读复验见[通用 Agent 验证记录](general-agent-strata-verification-20261019.md#实际请求的文件工具输入清单)。
- **独立原文集合：** `runtime_source_manifest.v1` 补充 Reviewer 的 original_sources、格式修正中的 candidate_context.original_sources、主 Agent 的 review_sources。仅由后端节点构建者显式指定字段路径，并核对完整运行输入确实存在于最终消息中；任意用户 JSON 不产生来源记录。清单保存消息位置、路径、读取调用/文件快照和原文块哈希，不复制原文。缺少原始输入或块与引用不一致时在派发前失败；原文权限仍由既有 resolver 检查。工具消息与独立原文集合分别计数，不能将出现次数相加当作不同块数、全文阅读或理解证明。任务级汇总和真正 wire 参数验证仍须继续。
- **验收：** 覆盖无工具、连续工具轮、最后一轮不提供工具、带历史的输出修正及幂等重放；比较实际发给 Provider 的业务消息与私有审计，检查顺序、来源、预算、私有字段保护和刷新后可追溯性。引用有效不等于内容正确，模型质量另行评估。

#### Context Builder职责边界

- 解析调用场景和目标模型，向已登记的来源Provider请求候选，不允许调用方把任意数据库ID或自由文本伪装成受信来源。
- 校验User、Project、Session、Run、Task、Message、Artifact、Memory、Knowledge、Agent和Tool结果的归属、权限、状态、版本和保留策略。
- 为每个候选记录来源类型、来源ID、版本、内容哈希、信任级别、选择优先级、token估算、选中状态和排除原因。
- 根据Model Catalog的上下文窗口和Context策略版本执行总预算及分来源预算；完整消息轮次、Memory事实、Knowledge片段、Artifact片段、Handoff和Tool结果不能从结构中间截断。
- 输出一个Provider中立Context Build：系统指令、按时间排序的消息、类型化资料区块、Agent节点输入和引用证据。Provider适配器只负责编码，不能再次选择或删除业务来源。
- 在模型调用前持久化基础 Context Build 和来源快照；Model Attempt 与 Agent Node 引用实际使用的 `context_build_id`，追加工具历史及最终有效请求按上节记录。预览复用基础构建引擎，不提前伪造尚未执行的工具结果。
- Context Builder不生成Session摘要、不批准Memory候选、不执行Knowledge排序算法、不修改Artifact/Handoff/Tool结果，也不决定模型业务答案。

#### 对话轮次与并发锚点

- 新增“提交对话轮次”的应用服务，在同一数据库事务中创建 Run 和当前 User Message，并返回 `run_id`、`message_id`、`message_sequence`。HTTP 可以在现有 Session 资源下增加专用轮次动作，但实现必须复用当前 Run、Message 表和归属校验，不能创建第二套会话记录。
- 当前 User Message 必须带 `run_id`。前端不能先创建一个无法关联 Run 的 Message，再通过相同正文猜测它属于哪个 Run。
- 运行时 Context Build 必须携带 `run_id` 和 `current_user_message_id`，并校验 User、Session、Run、Message、角色和消息序号关系。候选查询增加 `sequence <= current_user_message.sequence`，因此同一 Session 中稍后并发提交的消息不会进入当前 Run。
- 重放同一 Run 时复用已经提交的 Context Build，不根据 Session 的新状态重新构建历史。Context Build 在 Provider 调用前提交；构建失败时不创建可计费 Model Attempt。

#### 来源选择与预算规则

1. 必选来源依次为平台安全指令、调用方允许的系统指令和当前 User Message。任一必选来源无法完整放入模型上下文时明确失败，不能静默排除当前问题。
2. 旧消息按完整对话轮次分组，从最新轮次向更早轮次选择；一个轮次中的 User、Assistant 及其关联 Tool Message 必须整体纳入或整体排除，不能产生孤立 Assistant/Tool Message。
3. 选择完成后按原始消息序号恢复时间顺序，再生成 Provider 请求。稳定前缀保持“系统指令、较旧历史、较新历史、当前输入”的顺序，为支持前缀缓存的 Provider 提供可重复输入，但缓存命中不参与正确性判断。
4. `content_uri` 不能转换为空文本。允许的对象内容必须通过 ObjectStorage 受限读取、大小和哈希校验后成为一个完整来源；暂不支持的内容类型记录明确排除原因。当前 User Message 若不是可读取文本则直接拒绝生成。
5. 基础构建预算覆盖所选业务来源，不只覆盖 Session 历史；工具历史追加还须检查最终消息的总预算，不能只校验基础快照。预算预留不向 Provider 设置 `max_output_tokens`，不构成用户可见的生成长度限制。预算值和协议开销来自启用的 Model Catalog 或服务端策略版本，不能由 Web 写死；完整请求保存的缺口不等于当前没有工具历史预算检查。
6. 同一 Agent Workflow 如果角色绑定到不同 Provider 或模型，复用相同的消息锚点和候选来源，为每个不同的 `(provider, model, catalog_version)` 生成独立预算投影。节点必须记录实际使用的 `context_build_id`，不能用Controller模型的窗口假设替代Worker模型能力。

#### 接口和执行证据

- 用户可见的`/responses`执行必须先调用Context Builder：显式`input`也是一种类型化来源，不允许直接绕过构建器进入Provider。后端可以创建新的运行时Context Build，或在幂等重放时加载已提交的`context_build_id`；重放必须验证Run、Session、Provider、Model和策略版本归属，不能再次追加当前输入。
- Agent Workflow请求仍不携带用户正文。编排器向Context Builder提交当前节点需要的来源类型和已提交引用；Context Builder按节点、角色和模型构建或复用Context Build，并把Session、Prompt、Memory、Knowledge、Artifact、Agent和Tool来源证据交给`context_assembly`及模型节点。
- `llm_attempts`增加可查询的`context_build_id`关联；Agent Node Input填充`message_ids`和`context_build_id`。MinIO原始请求继续作为审计大对象，但不能代替数据库中的来源关联。
- 运行时Context Build增加幂等请求标识或等价唯一约束，至少绑定Run、当前Message、Provider、Model、目录版本和选择策略版本。相同输入重放返回原构建，不制造不同历史快照。

#### 错误处理和兼容

- Model Catalog缺失、消息归属冲突、锚点已失效、必选来源超限、对象内容不可读和角色序列不合法均在Provider调用前返回明确错误；不能退化为只发送当前输入并伪装完整上下文。
- 现有调用方迁移期间可以继续提交原`input`合同，但Responses Service必须在内部把它转换为`explicit_input`来源并创建Context Build。真正的低层Model Gateway只作为内部Provider调用端口，不作为绕过Context Builder的用户运行入口。
- 尚未通过启用门禁的来源记录为能力未启用或不进入候选集，不能由调用方绕过Context Builder直接读取后塞入Prompt。

#### 测试和完成条件

- 快速对话第二轮Provider请求包含第一轮User/Assistant和本轮User，且本轮User只出现一次。
- Agent Controller、Worker、Reviewer和Final synthesis都通过Context Builder获得各自所需的完整类型化上下文；相同来源引用保持一致，不同模型绑定使用各自Context Build并记录正确证据。
- 并发提交时，较晚Message不会进入较早Run；同一Run重放得到相同来源ID、顺序、哈希和排除原因。
- 预算不足时优先保留当前User和最新完整轮次，不出现孤立Assistant/Tool Message；必选来源超限时不调用Provider。
- 文本、暂不支持的对象消息、对象读取失败、跨Session/Run/用户引用和非法角色序列均有合同测试。
- OpenAI、DeepSeek、Anthropic、Gemini和OpenAI-compatible的Provider codec验证系统指令与消息角色映射一致；真实Provider验证保持显式开启，不默认产生费用。
- 内部Attempt、Agent Node和Context Build审计能够回答本次调用使用了哪些System Prompt、Message、Memory、Knowledge片段、Artifact、Handoff、Evaluation和Tool结果，排除了哪些来源，以及使用哪个模型目录和Context策略版本；普通前端证据必须过滤System Prompt的存在性和全部元数据。

### Memory：规划边界与实验性准备代码

Memory设计为独立、可查询的长期事实账本，不是Agent工作流内部的临时字典，也不是知识库的第二套文档库。仓库中已有Store、Policy、确定性Retrieval和L0～L4同步准备代码；当前实现仍是实验性准备，正式启用时必须通过统一Context Builder进入模型，不能单独接入`/responses`。

当前代码的 `memory_type` 只允许：

```text
working_context  必须绑定 Session、Run 或 Task 之一，并必须设置过期时间
session_episode  必须绑定 Session，表示会话中已发生事件的摘要
user_fact         有明确来源的用户事实
user_preference   用户明示或已确认的偏好
execution_lesson  必须绑定 Run 或 Task，表示已验证的处理约束或过程摘要
```

外部文档、长文本和通用资料属于 Knowledge，不得以 `memory_type` 扩展或 `content_uri` 的方式写入 Memory。五层机制实施时，`user_fact` 与 `user_preference` 继续由本账本承担；`working_context`、`session_episode` 和 `execution_lesson` 只为存量兼容保留，新的职责分别迁移到 Agent Working State/Session State、Session Summary 和 Lesson Candidate，不删除现有记录或枚举值。

第一版接口：

```text
POST   /api/v1/memories
GET    /api/v1/memories
GET    /api/v1/memories/{memory_id}
GET    /api/v1/memories/{memory_id}/versions
PATCH  /api/v1/memories/{memory_id}
DELETE /api/v1/memories/{memory_id}
POST   /api/v1/memory-retrievals
GET    /api/v1/memory-retrievals/{memory_retrieval_id}
```

`POST` 只接受具有来源的 Memory。受信调用方可以明确创建 `candidate` 或 `active`；模型生成内容必须以 `candidate` 写入，只有后续显式策略或受信调用方确认才能激活。创建、纠正、激活和检索请求都必须提供最大 128 字符的幂等键；同键同请求不会重复应用，写接口返回该稳定 Memory 的当前表示，检索接口返回原有检索证据；同键不同请求返回状态冲突。

#### 归属和范围

- `user_id` 始终必填，并通过 User 外键和 Service 校验表示受信调用方代表的资源所有者。
- `session_id`、`run_id`、`task_id` 是可建立真实外键的显式可选范围列，不使用 `scope_type` 或无法建立参照完整性的 `owner_type + owner_id`。它们可以同时表达一条 User → Session → Run → Task 关系链；外键使用 `RESTRICT`，禁止删除父资源后把受限 Memory 或历史检索证据静默扩大为更宽范围。
- Service 必须验证 Session 属于 User、Run 属于 User 且与已传 Session 一致、Task 属于已传 Run；任何链路关系不一致都返回资源冲突。
- 检索请求必须带 `user_id`，并可带当前 Session、Run、Task 链路；结果只能来自该 User，且 Memory 中任一非空下级范围都必须与检索链路的对应标识一致。

#### 持久化模型

```text
llm_memories
memory_id, user_id, session_id, run_id, task_id,
memory_type, status, current_version_number, semantic_key,
active_semantic_key_hash, supersedes_memory_id, expires_at, deleted_at,
creation_idempotency_key, creation_request_hash, created_at, updated_at

llm_memory_versions
memory_version_id, memory_id, version_number, content_text,
content_hash, normalized_content_hash, importance, confidence,
estimated_token_count, token_estimator_version, unique_search_term_count,
created_by_type, created_by_attempt_id, content_erased_at, created_at

llm_memory_mutations
memory_mutation_id, memory_id, idempotency_key, request_hash,
result_version_number, result_status, created_at

llm_memory_sources
memory_source_id, memory_version_id, source_type, source_resource_id,
source_content_hash, source_order, created_at

llm_memory_search_terms
memory_version_id, term_hash, term_frequency

llm_memory_retrievals
memory_retrieval_id, idempotency_key, user_id,
session_id, run_id, task_id, query_text, query_hash,
memory_types_json, result_limit, token_budget, candidate_method,
ranker_version, status, request_hash, created_at, completed_at

llm_memory_retrieval_results
memory_retrieval_result_id, memory_retrieval_id, memory_id,
memory_version_id, rank, total_score, lexical_score, scope_score,
importance_score, confidence_score, recency_score,
estimated_token_count, selected, exclusion_reason
```

- `memory_id` 是稳定的逻辑资源；正文、重要度或置信度变化时新增不可变的 `llm_memory_versions` 记录，并在同一事务将 `current_version_number` 增加一，不覆盖旧版本。
- 普通 `PATCH` 必须带 `expected_version_number`；Service 对 `llm_memories` 行加锁，版本已变化时返回冲突，防止并发纠正相互覆盖。
- 每个版本至少关联一个已校验来源；`source_type` 第一版只允许 `message | model_attempt | artifact | tool_call | trusted_request`。Message、Attempt、Artifact 和 Tool Call 来源必须属于同一 User；当 Memory 声明 Session/Run/Task 范围时，每一个来源还必须精确证明对应的非空范围，不能用一条合法来源掩盖另一条范围不符的来源。来源表保存标识、内容哈希和顺序，不复制敏感正文。
- `trusted_request` 表示受信服务边界提供的请求标识，只能用于 User 级 Memory；它没有 Session/Run/Task 归属，不能证明下级范围。来源哈希由该标识确定生成，但不代表已完成最终用户身份验证。`created_by_type=model_attempt` 必须带归属一致的 `created_by_attempt_id`，而且新 Memory 只能是 `candidate`。
- `llm_memory_search_terms` 由带版本的确定性规则从正文生成：Unicode NFKC、大小写与空白归一，拉丁词使用规范化 token，中文使用重叠二元词。表中只保存 SHA-256 词哈希和频次，每个版本/词哈希组合唯一，不保存明文检索词。未加盐的确定性哈希只减少偶然明文暴露，不是加密，数据库访问仍应视为可推断敏感内容的高权限边界。
- 创建幂等事实保存在 `llm_memories.creation_idempotency_key/creation_request_hash`；内容纠正和纯状态变更统一保存在 `llm_memory_mutations`，记录请求哈希及当次结果版本/状态，不用进程内缓存代替持久化幂等记录。重放只保证不重复应用；写接口返回当前资源表示，不伪称能够重建后续变更前的完整历史响应。
- `PATCH` 只有在创建新内容版本时才接受 `sources`；纯状态或元数据变更携带来源会在 Schema 层拒绝，避免证据被静默丢弃。`expires_at` 输入必须带时区并在写库前归一化为 UTC。
- 普通更新不能修改已存版本。逻辑删除是唯一的隐私擦除例外：删除事务将状态改为 `deleted`、清空所有版本正文和检索词，将 token/词数归零并记录 `content_erased_at`，仅保留内容哈希、来源标识/哈希和时间。这不会删除独立的原始 Message/Artifact。

#### 状态、幂等和并发

状态只允许：

```text
candidate -> active -> superseded
candidate -> rejected
candidate | active | rejected | superseded -> deleted
```

- `rejected | superseded` 不能重新激活，但仍允许执行隐私删除；`deleted` 是最终状态。非法转换返冲突，需恢复内容时创建新的稳定 Memory 并保留新来源。
- 一般纠正在同一 `memory_id` 下创建新版本；与已激活事实相冲突的新事实创建新 `memory_id`，并在同一事务将旧 Memory 标记为 `superseded`。
- `active_semantic_key_hash` 是 User/Session/Run/Task 范围、`memory_type` 和归一化 `semantic_key` 的唯一哈希，只在 `active` 状态有值。MySQL 唯一约束负责阻止两个并发事务激活同一语义槽位；未提供 `semantic_key` 时不猜测语义去重，唯一冲突转换成稳定的资源冲突错误。
- 创建 Memory、首版、来源和幂等记录在同一 MySQL 事务提交；纠正、冲突替换、激活和删除同样只有一个数据库事务边界。Memory 第一版不访问 MinIO，因此不存在 MySQL/MinIO 双写成功的虚假保证。
- `DELETE` 重复请求返回同一删除结果。删除与纠正通过同一 Memory 行锁串行化：删除先提交时纠正返回冲突；纠正先提交时随后删除仍可成功，最终以隐私删除为准，不错误承诺两个请求只能有一个成功。
- 过期不是持久化状态。检索和 Context 始终使用 `expires_at` 与当前时间过滤过期 Memory，不依赖尚未实现的 Worker 修改状态；过期记录仍可通过列表和详情接口查询审计，但不得参与检索或 Context。

#### 确定性检索与证据

- Retriever 先按 `user_id`、已提供的 Session/Run/Task 链路、`active`、未过期和可选 `memory_type` 过滤，再使用查询词哈希与当前 Memory 版本的词哈希计算匹配。已删除、拒绝、已替代或过期内容绝不进入排序。
- 查询与正文使用同一确定性分词规则。`memory_ranker_v1` 使用整数基点计算：60% 词集 Jaccard 相关度、15% scope 精确度、10% importance、10% confidence 和 5% recency；各分量与总分都保存在检索结果证据中。
- 最终稳定排序使用 `total_score DESC, updated_at DESC, memory_id ASC`。同一 User 下同一幂等键重试直接返回原检索记录和排序结果，不重算分数。
- 当前新建 Context 使用 `context_policy.v3` 与 `cl100k_base_proxy/v1`：由 `tiktoken==0.11.0` 的 `cl100k_base` 分词，加 20% 跨供应商估算余量及消息框架余量，不再把 UTF-8 字节数登记为 Token。它是输入选择估算，不是 DeepSeek/Strata 原生分词结果，也不是可保证的上界；费用和实际用量仍来自供应商 usage。旧 `utf8_bytes_upper_bound/v1` 模型目录仍兼容，历史 Context 不重写，新 Context 记录实际使用的估算器。单项超出剩余预算时记录 `token_budget_exceeded` 并继续检查后续项，不截断正文。工具历史使用同一分词函数；完整来源保存和按需读取规则见[阶段7长文件读取与工具历史](phase-7-tool-runtime-plan.md#长文件读取与工具历史2026-10-18)。
- 每次检索都写入请求哈希、查询文本/哈希、类型条件、候选/排序策略版本及有界结果的各分量。响应可返回同一 User 下被 token 预算排除的有界候选及原因，但不得返回跨 User、状态不可用、过期或已擦除的正文。

#### 为什么第一版不引入向量存储（Vector Store）

- 当前 Model Gateway 只有文本生成契约，没有供应商中立的向量嵌入（Embedding）请求、维度、费用、重试和调用证据契约；直接引入向量库会让新的外部副作用绕过现有记录边界。
- 尚无固定中英文检索样本证明语义检索能提升实际召回，也没有数据规模证据证明有界 MySQL 候选集无法满足时延。向量相似不等于事实正确，不能替代 scope、状态、过期和来源过滤。
- 第一版先用可解释排序建立评估基线，避免同时引入 Embedding 供应商、模型迁移、异步索引、删除一致性和新运维系统。
- 当前部署固定为 MySQL 8.4 Community。其官方 8.4 数据类型目录没有 Vector 类型；把浮点数组写进 JSON/BLOB 再由 API 进程全表计算距离既没有近似最近邻索引，也会形成无界内存和扫描成本，因此不作为在线检索的替代实现。

如果后续门禁证明 Memory 确实需要语义召回，条件性首选候选为：继续以 MySQL 为事实源时，优先评估 Qdrant 作为独立、可重建的向量投影；只有主事实数据库因独立业务原因迁移到 PostgreSQL 时才改评 pgvector；Milvus 只在数据规模和分布式吞吐已经证明需要时评估；FAISS 只用于离线正确性或召回基线，不承担多进程在线存储。Knowledge 可以复用 Embedding 和投影基础契约，但必须使用独立 profile、collection、评估集、排序权重和删除策略，不能用 Knowledge 的规模需求反向证明 Memory 需要向量库。

未来只有在以下门禁全部满足后才能增加向量投影（Vector Projection）：

1. 先证明当前词法基线无法满足已经声明的需求，例如固定样本 Recall/SLO 不达标，或 500 条候选上限、P95 时延和数据规模已经成为实测瓶颈；否则不增加新的在线基础设施。
2. 建立固定、去敏的多语言检索样本，在实验前冻结 Recall@k、nDCG@k、P95 时延、单次费用和跨 User/Scope 泄漏必须为 0 的阈值；向量或混合检索必须达到预定改善线，不得在看到结果后改验收条件。
3. 先实现供应商中立 Embedding 契约和调用证据，明确 provider、model、revision、dimension、distance metric、content hash、费用、超时、有限重试和不可重试错误；同时确定哪些 Memory/查询文本允许发送给哪个供应商、脱敏和保留规则，禁止默认外发敏感长期记忆。
4. 增加 `embedding_profiles` 与 `memory_vector_projections` 等可重建投影事实。前者至少记录 profile、供应商、模型修订、维度、距离度量和状态；后者以 `(memory_version_id, profile_id)` 唯一，记录确定性 point ID、内容哈希、投影代次、`pending | indexed | delete_pending | deleted | failed`、尝试次数、下次重试、最后错误和完成时间。MySQL 仍是唯一事实源，向量库只是可丢弃重建的派生投影。
5. 验证 owner/scope 前置过滤和返回后二次校验、重建、模型换代、重复投递、删除墓碑、索引落后和外部服务不可用；降级时响应必须明示实际使用的检索策略，不能静默改变语义。
6. 阶段10的 Outbox Publisher、RabbitMQ 和可重放投影 Worker 已经可用，并复用阶段1的 `llm_outbox_events` 事实表；Memory API 事务只提交事实、期望投影状态和 outbox 事件，绝不在数据库事务内同步调用 Embedding 服务或向量库。

未来只投影“当前版本、`active`、未过期且正文未擦除”的 Memory；`candidate/rejected/superseded/deleted` 和历史版本禁止进入投影。Worker 每次 upsert 前重新读取 MySQL，目标失效时只执行 delete。向量库只保存确定性 point ID、Memory/版本/Profile 标识、owner/scope 过滤字段、内容哈希、代次和过期时间，不复制 `content_text`，但向量本身仍按敏感派生数据保护。查询先在向量库按 owner/scope 过滤，再回 MySQL 批量复核 owner、范围、状态、当前版本、过期时间和内容哈希，正文始终只从 MySQL 读取。

删除先在 MySQL 中使事实立即不可检索并写入墓碑/outbox，投影记录进入可查询的 `delete_pending`；外部删除采用有限重试、最终失败告警和部署级清理时限，并以 `deleted_at` 证明物理投影清理完成。即使残留 point 也不能通过 MySQL 二次校验。模型换代使用新 collection 代次完整重建和验证后再原子切换别名，不在同一 collection 中混写不同维度。混合检索只能对有界词法/向量候选并集使用版本化融合算法，并持久化 lexical/vector/fusion 分数、`profile_id`、策略版本和实际降级原因，不能用单一向量相似度覆盖现有确定性证据。

### L0～L4 五层 Memory 目标机制与同步最小实现

Memory 的长期目标不是把所有历史文字塞回模型，而是按执行主体和生命周期提供五种边界明确、可版本化且可追溯的信息。`llm_memories` 的现有实验性代码作为长期事实账本候选保留；自动形成、后台压缩、定时擦除、Memory Packet 和模型调用集成都不属于当前阶段验收，未来启用时必须重新评审。

| 层级 | 名称 | 解决的问题 | 默认生命周期 | 写入者 | 默认读取者 | 不保存 |
|---|---|---|---|---|---|---|
| L0 | Agent Working Memory | 单个 Agent 当前执行到哪里、正在等待什么、下一步做什么 | 单个 Agent Run 运行期；终止后短期保留 | 该 Agent 的执行协调器 | 该 Agent；Controller 仅用于恢复和诊断 | 隐藏推理、完整消息、完整工具结果、其他 Agent 状态 |
| L1 | Session Memory | 当前会话正在讨论什么、已经确认什么、哪些旧消息仍相关 | Session 活跃期及部署保留期 | Session Memory Service；摘要可由受控模型生成 | 未来启用后的 Context Builder、当前 Session 内授权 Agent | 跨会话项目规则、全局用户偏好、隐藏推理 |
| L2 | Collaboration / Run Memory | 同一次 Run 内不同 Agent 已完成什么、证据在哪里、下一步必须遵守什么 | Run/Task 执行期及审计保留期 | Agent 执行协调器和 Run Memory Service | 下游 Agent、Controller、Reviewer、Verifier | 单个 Agent 尚未提交的临时发现、隐藏推理、整个工作目录副本 |
| L3 | Project Memory | 跨 Session/Run 持续存在的项目事实、决定、术语、约束和规则 | Project 有效期，直到纠正、替代、过期或删除 | Project Memory Service；Agent/模型只能提交候选 | 绑定同一 Project 的 Session、Run 和 Agent | 用户全局偏好、当前 Run 进度、未验证代码猜测、凭据 |
| L4 | User Memory | 用户跨全部 Project 保持稳定的身份、偏好和长期限制 | 直到纠正、替代、过期或删除 | 受信调用方；模型只能提交候选 | 未来启用后的 Context Builder、经同一 User scope 校验的 Agent | 项目专属约定、外部资料全文、未确认推断、凭据和密钥 |

外部方案校验结论：LangChain/LangGraph 的官方 Memory 概念把短期记忆限定在线程/会话范围，并把跨会话长期记忆区分为事实、经历和过程规则；它也明确区分 Profile 与事实集合，以及请求热路径写入和后台形成。本计划采用会话/长期事实、核心 Profile/细粒度集合和 M4 后台形成的边界，但不引入 LangGraph Store 作为平台事实源。Mem0 官方开源方案默认组合 LLM、Embedding、Vector Store 和历史库，近期算法与 Graph 支持也有变化；这证明它是可评估的抽取/检索组件，而不是当前 MySQL 审计模型的无成本替代。Qdrant 的 payload filter 可以辅助向量候选过滤，但近似检索和 payload 过滤不能代替 NexusPilot 的 User/scope/删除权限复核。因此本计划先完成可审计事实与确定性基线，再由 M5 评估向量组件。

规划原则：MySQL 保存业务事实、状态、版本、来源和当前指针；L1～L4 的持久快照可由 MinIO 保存不可变结构化 JSON 与确定性 Markdown 视图；JSON 是规范内容，Markdown 仅用于人工检查，不是第二事实源。L0 是不超过 800 token 的运行检查点，只写 MySQL。未来若启用模型读取，必须另行实现固定版本的 Memory Packet；当前 `/responses` 不创建或读取 Packet。

建议初始预算使用“目标值/硬上限”，而不是保证每层都会占满的配额：

| 内容 | 目标值 | 硬上限 | 超限处理 |
|---|---:|---:|---|
| L4 用户核心记忆 | 500 tokens | 800 tokens | 按已确认禁止、稳定偏好、身份事实选择，不截断单项 |
| L3 项目核心记忆 | 800 tokens | 1500 tokens | 按有效决定、项目约束、术语和稳定事实选择 |
| L1 会话摘要 | 800 tokens | 1500 tokens | 生成新摘要版本；原 Message 不变 |
| L2 Run 共享记忆 | 1000 tokens | 2000 tokens | 归并已完成 Handoff，排除可由引用恢复的大文本 |
| L2 直接前置 Handoff | 每条 500 tokens | 每条 1000 tokens | 默认最多 4 条且合计不超过 3000 tokens，更多内容通过 Artifact 引用 |
| L0 Agent Working State | 300 tokens | 800 tokens | 删除低价值搜索轨迹，保留恢复所需状态，不截断 JSON |

这些数字作为未来启用门禁的预算基线，届时必须进入Context Builder的分来源预算并记录目标模型tokenizer名称和版本。当前代码尚未给Memory分配运行时预算；正式启用后预算不足必须整项排除并记录原因，不能截断JSON、Handoff或事实正文。

#### 与当前 Memory 代码的兼容关系

- `user_fact` 和 `user_preference` 继续由现有 `llm_memories`、`llm_memory_versions`、`llm_memory_sources` 与检索证据表承担。
- 当前 `candidate` 对应候选状态，`active` 对应已批准且可使用状态，`rejected` 对应拒绝状态。阶段2计划增加显式 `expired` 状态用于候选审批生命周期；正式事实仍同时使用 `expires_at` 做读取时过滤，不能依赖后台任务及时改状态。
- `working_context`、`session_episode` 和 `execution_lesson` 为存量兼容保留；新的写入职责分别转移到 Agent Working State/Session State、Session Summary 和 Lesson Candidate。迁移前不得删除枚举或历史记录。
- L3 Project Memory 的细粒度事实继续复用 `llm_memories` 的不可变版本、来源、状态和检索证据，但必须增加 `project_id` 和项目专属类型；L4 User Profile 必须排除所有带 `project_id` 的事实，避免把项目约定升级成全局偏好。
- 不新建第二套用户事实表，也不增加独立 `llm_memory_candidates`。候选、批准、拒绝、替代和删除继续是稳定 `memory_id` 的生命周期，避免候选批准时复制正文并丢失来源。
- 当前 `trusted_request` 只证明受信服务传入了一个请求标识，不证明最终用户本人确认。用户确认来源必须等最终用户认证主体确定后才能声称成立。

#### 通用快照对象与 MinIO 规则

新增 `llm_memory_snapshot_objects`，统一登记 L1～L4 Memory 生成的不可变对象：

```text
memory_snapshot_object_id, memory_layer, user_id, project_id,
session_id, run_id, task_id,
object_type, schema_version, object_version, storage_uri, content_hash,
size_bytes, mime_type, status, created_at, deleted_at, cleanup_error
```

- `user_id` 必填，`project_id` 只用于 L3，其余范围字段按对象类型建立可验证关系；`storage_uri` 唯一。
- 继续使用现有 MinIO bucket，不为 Memory 创建第二套对象存储客户端。对象键固定为：

```text
memory/v1/{memory_layer}/{scope_id}/{object_type}/{yyyy}/{mm}/{dd}/
{utc_timestamp}_v{version:06d}_{object_id}_{sha256前8位}.{json|md}
```

- 对象键只含不透明 ID、UTC 时间、固定宽度版本和哈希，不含用户名、标题、提示词或其他个人信息。
- 同一版本的 JSON 和 Markdown 各有独立对象记录，并通过所属业务快照行关联；对象创建后不覆盖，也不使用 `latest.json` 或 `latest.md`。当前版本只能通过 MySQL 指针确定。
- 现有 `llm_artifacts` 要求 `run_id`，语义是 Run 产物，不适合承担 User/Session 快照。Run 内大型测试结果、补丁和报告继续使用 `llm_artifacts`；Memory 快照使用上述新表。
- `ObjectStorage` 需要在现有 `put/open` 基础上增加受控删除、按确定性前缀列举和内容哈希复核能力，用于孤儿对象清理和一致性巡检；不得让 API 端点接受任意对象键。

所有结构化对象使用统一信封：

```json
{
  "schema_version": "session_state.v1",
  "object_id": "opaque-id",
  "version": 3,
  "created_at": "2026-08-03T00:00:00Z",
  "source_ids": ["message-id-or-handoff-id"],
  "content": {}
}
```

`schema_version` 变化表示结构兼容边界，`version` 表示同一业务对象内容演进；读取端遇到未知结构版本必须明确失败，不能猜测字段或把未知字段静默丢弃。

#### L0 Agent Working Memory

Agent Working Memory 是单个 Agent Run 的可恢复操作状态，不是对话历史，也不是提供给其他 Agent 的交接结果。当前仓库已有最小 `llm_agent_runs`、`llm_agent_turns`、检查点模型和内部接口，用于验证版本、恢复和终止边界；它们不构成 Agent Runtime，实际自动写入、工具状态复核和执行循环仍由阶段3实现。

2026-10-19 新通用循环已增加[任务内笔记工具](phase-7-tool-runtime-plan.md#当前任务笔记工具2026-10-19)，复用 Tool Call 的 MinIO 正文保存模型主动提交的暂定记录，不写入此处 L0 检查点表，也不启用跨会话 Memory。笔记不是自动恢复状态；本节检查点接入与任务事实引用仍需后续实现，跨进程续跑按用户要求在真实 Agent 调测完成后检查。

不能只使用 L1 Session Memory：同一 Session 中可以并行存在 Controller、多个 Researcher、Planner 和 Reviewer，它们具有不同目标、文件读取进度、待处理工具和预算。如果共用一份 Session State，并发检查点会互相覆盖，Reviewer 还可能看到实现 Agent 尚未验证的临时发现。L1 只保存用户会话共享事实；每个 Agent 使用独立 L0，完成后再通过 L2 Handoff 发布可依赖结果。

在计划中的 `llm_agent_runs` 基础上新增：

```text
llm_agent_turns
agent_turn_id, agent_run_id, turn_sequence, turn_type, status,
model_attempt_id, started_at, completed_at, error_code, created_at

llm_agent_working_state_versions
agent_working_state_version_id, agent_run_id, agent_turn_id,
version, status, state_json, previous_version_id,
estimated_token_count, tokenizer_name, tokenizer_version,
idempotency_key, request_hash, expires_at, content_erased_at, created_at
```

`llm_agent_runs` 增加 `current_working_state_version_id` 和 `current_turn_sequence`。`AgentRun` 表示一个角色承担一个 Task 的完整执行；`AgentTurn` 表示该执行中的一次模型/工具循环步骤；Model Attempt 仍表示一次逻辑模型调用，Tool Call 仍表示真实工具调用。Working State 只能引用这些事实，不能复制原始请求、供应商响应、完整工具结果或费用。

`agent_working_state.v1` 至少包含：

```json
{
  "current_objective": "当前具体目标",
  "searched_queries": ["已执行的有价值搜索词"],
  "files_read": [{"file_reference": "workspace-relative-path", "content_hash": "..."}],
  "rejected_hypotheses": [{"hypothesis": "...", "evidence_ids": ["..."]}],
  "next_actions": ["下一步准备读取或验证什么"],
  "pending_tool_call_ids": ["..."],
  "remaining_budget": {"input_tokens": 0, "tool_calls": 0, "time_seconds": 0},
  "tentative_findings": [{"finding": "尚未提交的发现", "source_ids": ["..."]}],
  "blocked_on": []
}
```

- 在 Agent Run 启动前创建首版；每个完整 Agent Turn、进入工具等待、人工等待、暂停或可恢复重试之前创建检查点。逐 token、每个日志行和工具轮询不写版本。
- 采用期望版本和 Agent Run 行锁切换当前指针；崩溃后从最后一个 active 检查点恢复。恢复必须重新核对待处理 Tool Call 的实际状态，不能把状态 JSON 当成工具已完成证据。
- 只有该 Agent 执行协调器可以写入。该 Agent 及恢复协调器可以读取；其他工作 Agent 默认不可见。Controller 可以读取状态和阻塞原因用于取消/恢复，Reviewer 只能读 Handoff、Artifact 和测试证据。
- `status` 使用 `active | superseded | finalized | erased`。Agent Run 结束后立即停止注入，最终状态保留到可配置清理期；建议默认 7 天、允许 1～30 天，之后擦除 `state_json` 并保留版本、hash 和时间审计。最终保留期仍受部署策略约束。
- 结束时只有经来源校验的结论可以提升到 L2 Handoff；Working State 本身不会自动写入 L1、L3 或 L4。
- 内部接口为 `POST /api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints`、`GET /api/v1/internal/agent-runs/{agent_run_id}/working-memory` 和版本列表；必须同时校验公共/内部 API Key，外部业务调用方不直接修改。

与 Agent Runtime 不冲突的判断：L0 负责“这个 Agent 现在做到哪一步”，L2 Handoff 负责“其他 Agent 可以依赖什么结果”，Run/Task 负责正式执行状态，Attempt/Tool Call 负责真实调用事实。Memory Packet 仅作为未来“本轮模型实际读了什么”的设计对象；当前不创建。未来必须继续保持这些引用关系，避免形成第二套状态机、调用记录或共享进度。

#### L1 Session Memory

Session Memory 由原始 Message、版本化 Session State、版本化 Session Summary 和有界的相关旧消息组成。原始 `llm_messages` 保持不可变并继续作为最终证据；摘要不能覆盖或删除原消息。

新增表：

```text
llm_session_states
session_state_id, session_id, schema_version, version, status,
state_json, previous_state_id, state_through_message_id,
state_through_message_sequence, generation_attempt_id,
json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_session_summaries
session_summary_id, session_id, schema_version, version, status,
summary_json, previous_summary_id, summary_from_message_sequence,
summary_through_message_id, summary_through_message_sequence,
generation_attempt_id, json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code
```

`status` 使用 `generating | active | failed | superseded`。`llm_sessions` 增加可空的 `current_state_id` 和 `current_summary_id`；外键必须在快照表建立后再增加，且只能指向同一 Session 的 active 版本，Service 事务中再次校验该关系。

`session_state.v1` 至少包含：

```json
{
  "goal": "当前用户目标",
  "constraints": ["已确认限制"],
  "decisions": [{"decision": "...", "source_message_ids": ["..."]}],
  "open_questions": ["尚待确认的问题"],
  "active_entities": [{"kind": "project", "reference": "opaque-id"}]
}
```

`session_summary.v1` 至少包含摘要覆盖范围、已经确认的事实、主要决策、未解决事项和重要产物引用。生成规则如下：

- 默认在未摘要消息达到 20 条、估算输入达到 8000 token，或绑定该 Session 的 Run 结束时触发；这些值进入带上下限的配置，固定样本评估后才能调整。
- Context 默认携带当前摘要、最近 12 条完整消息和通过有界检索选出的相关旧消息；不默认重新加载完整会话。摘要目标为 800 token、硬上限为目标模型 tokenizer 的 1500 token。
- Worker 未实现前，只提供显式或内部同步生成入口；不得以 FastAPI `BackgroundTasks` 假装具备可恢复的后台形成能力。Worker 完成后再增加异步刷新。
- 增量摘要必须记录上一摘要和精确消息序列区间；累计 5 个增量版本后是否重建完整摘要由固定样本评估决定，不能无限递归拼接摘要。
- 摘要生成失败时旧 active 版本继续服务，新版本记录 `failed` 和安全错误；消息写入不因摘要失败而回滚。
- 第一版相关旧消息先评估 MySQL FULLTEXT。部署启用中文 `ngram` parser 的可用性和迁移行为必须用 MySQL 8.4 实测；不满足时复用有界确定性词项索引，禁止无范围的 `%LIKE%` 或全表载入。

#### L2 Collaboration / Run Memory

Collaboration Memory 规划服务于一次 Run 内的 Agent 交接。Agent Handoff 是不可变交接事实，Run Memory Snapshot 是对已完成 Handoff 的版本化归并；Memory Packet 是未来某次 Agent/模型调用实际收到的精确输入清单，本阶段不创建也不接入模型调用。

新增表：

```text
llm_agent_runs
agent_run_id, run_id, task_id, agent_role, status,
provider, model, started_at, completed_at, created_at

llm_agent_handoffs
agent_handoff_id, agent_run_id, run_id, task_id, schema_version, version,
status, handoff_json, supersedes_handoff_id,
json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at

llm_run_memory_snapshots
run_memory_snapshot_id, run_id, schema_version, version, status,
state_json, previous_snapshot_id, expected_previous_version,
json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_run_memory_snapshot_handoffs
run_memory_snapshot_id, agent_handoff_id, handoff_order

llm_memory_packets
memory_packet_id, agent_run_id, run_id, task_id, user_id,
schema_version, packet_json, user_profile_snapshot_id,
project_memory_profile_snapshot_id, session_state_id, session_summary_id,
run_memory_snapshot_id, agent_working_state_version_id,
estimated_token_count, tokenizer_name, tokenizer_version,
json_snapshot_object_id, created_at

llm_memory_packet_items
memory_packet_id, item_type, resource_id, resource_version,
item_order, selected_token_count, content_hash
```

现有实验性模型已经为 `llm_runs` 准备可空 `current_memory_snapshot_id`，并为 `llm_attempts` 预留可空 `memory_packet_id`；当前响应链路不得写入或依赖这些字段。未来若启用，Attempt 的原始请求仍保存动态消息、工具定义和最终厂商负载，Packet 只记录 Memory 输入及精确版本，两者不能互相替代。

`agent_handoff.v1` 至少包含：

```json
{
  "objective": "本 Agent 收到的完整目标",
  "status": "completed",
  "confirmed_facts": [],
  "decisions": [],
  "files_read": [],
  "files_changed": [],
  "artifacts": [],
  "tests": [],
  "remaining_work": [],
  "risks": [],
  "unknowns": [],
  "invariants_for_next_agent": []
}
```

- Agent 执行协调器负责提交，Collaboration Memory Service 校验 Task/Run/Agent Run 归属和 Schema。完成状态缺少必要字段时，Task 不能通过 Handoff 完成门禁；异常退出可以写 `partial` Handoff。
- Handoff 状态允许 `completed | partial | corrected | superseded`；内容不可覆盖，纠正创建新行并用 `supersedes_handoff_id` 指向旧版本。
- 下游 Agent 默认只读取依赖祖先 Task 的有效 Handoff、当前 Run Snapshot 和显式 Artifact，不读取整个 Run 的所有历史。Controller、Reviewer 和 Verifier 可以按职责申请更宽但仍有界的读取范围。
- Run Memory 合并时对 Run 当前指针加锁，校验 `expected_previous_version`，从确切 Handoff 集合生成新 JSON/Markdown，再原子切换指针。冲突事实写入 `conflicts`，由 Controller 或 Reviewer 明确裁决，不能按最后写入时间静默选择。
- `run_memory_state.v1` 至少包含目标、已完成工作、关键决策、变更文件、Artifact、测试证据、剩余工作、风险、冲突和必须保持的约束。
- Run Memory 目标为 1000 token、硬上限 2000 token。每条直接前置 Handoff 目标 500 token、硬上限 1000 token；默认最多选择 4 条且合计不超过 3000 token。超过范围的测试、补丁和日志只保留 Artifact 引用。
- `agent_memory_packet.v1` 固定角色、目标、预算、Agent Working/User/Project/Session/Run 版本以及选中的 Handoff、Memory、Message 和 Artifact 引用。Packet 创建后不可修改；供应商重试默认复用相同 Packet，除非上层明确创建新的逻辑 Attempt。

`RUN_MEMORY.md` 由 Run Snapshot 确定性渲染，固定为“目标、已完成、关键决策、文件与产物、测试、剩余工作、风险与冲突、后继约束”。它不包含 Agent 隐藏推理、完整终端输出或未引用的大型文件。

#### L3 Project / Workspace Memory

Project Memory 规划为跨 Session 和 Run 的稳定项目范围，不是工作台账或项目管理系统。仓库已经存在实验性的最小 Project 实体，但尚未作为正式产品能力验收；不能用仓库目录、Git remote、Session title 或任意字符串冒充权限 scope。未来正式启用时只保留必要归属和 Memory 边界，不增加看板、排期、负责人或业务项目管理字段。

建议新增：

```text
llm_projects
project_id, owner_user_id, project_name, status, memory_status,
memory_enabled_at, memory_enabled_by_actor_id,
current_memory_profile_snapshot_id, created_at, updated_at

llm_project_workspaces
project_workspace_id, project_id, workspace_type,
credential_free_locator, locator_hash, is_active, created_at, updated_at

llm_project_memory_profile_snapshots
project_memory_profile_snapshot_id, project_id, schema_version, version,
status, profile_json, estimated_token_count, tokenizer_name, tokenizer_version,
previous_snapshot_id, json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_project_memory_profile_items
project_memory_profile_snapshot_id, memory_id, memory_version_id,
field_path, item_order, content_hash
```

`llm_sessions` 和 `llm_runs` 增加可空 `project_id`；Session 绑定 Project 后，其下 Run 必须继承同一 Project，Task/Attempt/Artifact 继续从 Run 派生范围。现有 `llm_memories` 增加可空 `project_id`，以及 `project_fact | project_decision | project_rule | project_convention | project_environment` 类型。Project Memory 仍保留 `user_id` 作为当前 owner；多用户 Membership 在最终用户认证和授权模型确定前不实施。

`llm_project_workspaces` 只保存不含凭据的规范 locator，例如去除账号令牌的仓库地址或隔离工具提供的不透明 workspace reference；本地绝对路径、SSH 凭据和带 token URL 不进入 Memory。一个 Project 可以没有代码 Workspace，也可以绑定多个 Workspace。

##### 是否启用以及何时启用

- 默认 `memory_status=disabled`。只有受信调用方明确启用并提供 `owner_user_id`、Project 名称、保留策略和可用 token 预算后才能写入或注入；创建 Project 不自动开启记忆。
- 可以在创建 Project 时显式启用，也可以在第二次 Session/Run 前启用。系统可以在检测到同一 Project 出现多个 Session/Run 时提出建议，但不能自动开启、回填历史或产生模型费用。
- `enabled` 允许提交候选、审批和构建 Profile；`suspended` 停止新候选与模型注入，但保留查询和审计；`disabled` 表示从未启用或已经明确关闭。Project `archived` 时自动停止注入，但不物理删除事实。
- 只有显式绑定该 `project_id` 的 Session/Run 才能读取 Project Memory。没有 Project scope 的普通聊天只读取 L1/L4，不尝试按文本猜测项目。
- 当前认证只支持单一 owner 的受信调用方。若需要多个用户共享 Project Memory，必须停止并先实现可验证 Membership、角色、撤权和逐资源越权测试。

##### 记录内容

可以记录：

- 项目身份、目标、明确范围和稳定术语；
- 已接受的架构/产品决定、决定状态、理由摘要及证据；
- 经过验证的代码库入口、模块职责和不易变化的环境事实；
- 项目专属编码、测试、发布和审核约定；
- 技术与合规约束、明确禁止的方案；
- 有来源的长期风险、已知限制和纠正记录；
- 与 Workspace 的不含凭据关联，以及稳定 Artifact/文档引用。

不记录：当前 Agent 搜索进度、当前 Run 待办、未提交临时发现、分支瞬时状态、完整文件/网页、完整日志、密钥、个人全局偏好、模型隐藏推理或未经测试的架构猜测。文档正文属于 Knowledge，Run 进度属于 L2，用户跨项目偏好属于 L4。

模型、Agent Handoff、外部文档和工具结果只能产生 Project Memory Candidate；Project owner 或独立 Reviewer 基于来源和确定性检查批准后才变为 `active`。Project 决定发生变化时创建新版本或替代事实，不覆盖历史。Project Memory 不自动提升到 User Memory，也不因在多个项目重复出现就变成全局规则。

`project_profile.v1` 至少包含：

```json
{
  "project_identity": {"name": "...", "scope": "..."},
  "goals": [],
  "glossary": [],
  "architecture_decisions": [],
  "constraints": [],
  "conventions": [],
  "verified_environment": [],
  "known_risks": [],
  "explicit_prohibitions": []
}
```

Project Profile 目标为 800 token、硬上限 1500 token。`PROJECT.md` 由该 JSON 确定性生成，固定为项目范围、目标、术语、有效决定、约束、约定、环境、风险和来源版本；Markdown 不作为写入口。

接口规划：`POST/GET /api/v1/projects`、`GET/PATCH /api/v1/projects/{project_id}` 只管理最小 Project scope；`PATCH /api/v1/projects/{project_id}/memory-policy` 显式启用/暂停/关闭；候选列表与批准/拒绝位于 `/api/v1/projects/{project_id}/memory-candidates`；当前 Profile 与重建位于 `/api/v1/projects/{project_id}/memory` 和 `/memory/rebuild`。写接口在最终用户认证完成前同时要求公共和内部 API Key。

#### L4 User Memory

User Memory 包含现有细粒度事实账本和一个可快速注入的核心 Profile Snapshot。Profile 是正式事实的投影，不允许反向覆盖来源事实。

现有表计划补充：

```text
llm_memory_sources
+ trust_level

llm_memory_mutations
+ operation, actor_type, actor_id,
+ before_version_number, after_version_number,
+ before_status, after_status, change_json

llm_memories
+ approval_method, approved_at, approved_by_actor_id,
+ sensitivity_classification, is_core_profile_eligible
```

`trust_level` 固定为 `direct_user_statement | user_confirmed | model_inference | internal_system_result | external_untrusted`。信任级别由接收边界和来源类型决定，不能由模型正文自报。网页、文件、邮件、知识库和 MCP 返回均为 `external_untrusted`，只能形成候选，不能自动激活或进入 Profile。

新增表：

```text
llm_user_memory_profile_snapshots
user_memory_profile_snapshot_id, user_id, schema_version, version, status,
profile_json, estimated_token_count, tokenizer_name, tokenizer_version,
previous_snapshot_id, json_snapshot_object_id, markdown_snapshot_object_id,
idempotency_key, request_hash, created_at, activated_at, failed_at, error_code

llm_user_memory_profile_items
user_memory_profile_snapshot_id, memory_id, memory_version_id,
field_path, item_order, content_hash
```

`users` 增加可空 `current_memory_profile_snapshot_id`。`user_profile.v1` 使用以下可扩展结构：

```json
{
  "confirmed_preferences": [],
  "confirmed_facts": [],
  "ongoing_goals": [],
  "explicit_prohibitions": [],
  "environment_constraints": []
}
```

- 默认目标为 500 token、硬上限为 800 token，并记录实际 tokenizer 名称和版本。超限时按显式禁止项、稳定环境限制、已确认偏好、身份事实、长期目标的规则选择；不截断单个事实。
- 只有 `active`、未过期、`direct_user_statement | user_confirmed`、非敏感且 `is_core_profile_eligible=true` 的当前版本可以进入 Profile。其他有效事实只能按需检索。
- 修正正文创建新不可变版本；冲突事实使用新的 `memory_id` 替代旧事实；两者都会使旧 Profile 过时并触发重建。删除必须在同一事务使包含该版本的当前 Profile 失效或切走当前指针，确保之后创建的 Memory Packet 不再读取正文。
- 凭据、访问令牌、密码和私钥在候选创建前即拒绝保存；拒绝证据只保留规则编号和输入哈希。身份证件、财务和健康等敏感信息是否允许长期保存必须由部署保留策略明确授权，缺失策略时拒绝进入 Profile。
- 已经开始的供应商请求无法从外部模型撤回内容。Memory Packet 会固定使用过的版本和哈希，删除后保留无正文审计标识，但后续 Packet 不得再选中已删除内容。

`USER.md` 由 `user_profile.v1` 确定性渲染，固定为“确认偏好、确认事实、长期目标、明确禁止、环境限制、来源版本”六部分。它不包含候选、外部不可信文本、隐藏推理或完整来源正文；Markdown 丢失时从 JSON 重建，不反向解析 Markdown 更新事实。

#### Lesson Candidate 与污染防护

`execution_lesson` 不再承担“自动学习成通用规则”的新职责。新增 `llm_lesson_candidates`：

```text
lesson_candidate_id, run_id, task_id, agent_handoff_id,
evaluation_id, status, scope_json, lesson_json,
valid_conditions_json, invalid_conditions_json,
promoted_memory_id, created_at, reviewed_at
```

状态为 `candidate | verified | promoted | rejected | superseded`。只有包含确定性测试或 Evaluation 证据、明确适用范围、有效条件和失效条件的候选，经过独立 Reviewer 后才能提升为 L3 Project Memory、L4 User Memory 或后续 Skill 候选；阶段2不自动写 Skill，不让一次成功或一次失败变成项目或全局规则。

统一污染防护：

- 模型输出、外部资料中的“请记住”“忽略规则”等文字只作为数据，不得提升信任级别、扩大 scope 或触发批准。
- 用户直接陈述与用户粘贴/引用的外部文本必须由接口结构区分；不能仅根据自然语言猜测引用内容是用户事实。
- 写入前执行凭据与高敏感标识扫描，记录规则版本、结果和输入哈希；不得把被拒绝原文复制到错误信息、日志或审计 JSON。
- 来源校验、信任判定、候选审批和 Profile 选择都在 Service 中执行，Router 和 Provider Adapter 不复制策略。
- 任何模型生成内容都从 `model_inference` 或 `external_untrusted` 开始，不因模型置信度字段较高而自动激活。

#### 双存储事务与失败恢复

跨 MySQL/MinIO 的快照写入使用“旧版本持续可用、上传完成后才切换指针”的五步协议：

1. MySQL 事务创建 `generating` 业务版本和幂等事实，记录期望的当前版本后提交。
2. 在数据库事务外生成并校验规范 JSON、确定性 Markdown、token 估算和内容哈希。
3. 使用包含对象 ID 与哈希的唯一键上传 JSON/Markdown；重复上传必须得到相同内容，否则标记冲突。
4. 通过对象元数据重新核对大小和 SHA-256，并登记 `llm_memory_snapshot_objects`。
5. MySQL 事务锁定 User/Project/Session/Run 当前指针，重新校验期望版本，将新版本改为 `active`、旧版本改为 `superseded` 并切换指针。

失败处理：

- 第 2～4 步失败：业务版本标记 `failed`，旧指针不变；模型生成超时和对象存储不可用区分为可重试依赖错误，Schema/敏感内容错误不可重试。
- 上传成功而数据库登记或指针提交失败：对象成为待清理对象；能连接数据库时记录 `cleanup_error`，数据库不可用时由确定性前缀巡检找出无引用对象。清理失败不影响旧版本读取。
- 数据库指针只有在对象校验完成后才提交，因此不允许出现“当前版本已切换但对象尚未上传”的状态。
- 同一幂等键同一请求返回原生成记录；同键不同请求返回 409。并发生成时只有基于当前版本的事务可以切换指针，失败者重新读取并决定重算，不能覆盖胜者。
- 服务重启只读取 MySQL 当前指针，扫描并恢复超时的 `generating` 记录；绝不按 MinIO 文件名猜测最新版本。
- 删除与生成并发时锁定同一 scope 指针。删除先提交则生成不能激活；生成先提交则删除使新版本失效并安排对象清理。已创建 Packet 的无正文审计记录保留，但不可继续解析已删除正文。

#### 未来启用时的读取方案（当前不执行）

以下内容只保留为未来评审输入，不是阶段2运行流程。当前 `/responses` 不读取任何 L0～L4 Memory，也不创建 Memory Packet；Context Preview 代码虽然已有候选读取分支，但未通过验收，阶段2稳定合同要求禁用或移除。若未来决定正式启用，必须先重新验证权限、删除、预算、污染防护和版本固定，再考虑以下候选顺序：

1. 校验 User、可选 Project、Session、Run、Task、Agent Run 的完整归属链，确定 provider/model、tokenizer 版本和总预算；没有显式 `project_id` 时不读取 L3。
2. 读取 L0～L4 对应 MySQL 当前指针；L1～L4 按指针读取不可变 JSON，并校验对象 hash、schema version、状态和删除/过期条件；L0 直接读取有版本的 MySQL JSON。
3. 选择 L4 User 核心 Profile；需要细节时再检索不带 Project scope 的有效用户事实，不能把整个用户事实账本默认注入。
4. 若已绑定 Project 且 Memory 为 `enabled`，选择 L3 Project Profile；需要细节时只检索该 Project 的有效事实。Project 禁用/暂停时记录排除原因，不退回文本猜测。
5. 选择 L1 Session Summary、最近完整 Message 和有界相关旧消息；Summary 已经覆盖且没有新价值的旧消息不重复注入。
6. 对 Agent 调用选择 L2 当前 Run Snapshot、直接依赖祖先的有效 Handoff、显式 Artifact 和 Task 要求；普通会话模型调用跳过此层。
7. 对 Agent 调用读取该 Agent Run 的 L0 Working State，用于恢复当前目标、待处理工具和下一步；不得把其他 Agent Working State 注入。
8. 按安全与系统指令、当前明确请求、L0 当前目标、L2 Run/Handoff、L1 Session、L3 Project、L4 User 默认信息的类别预算组装；单个结构化对象不能从中间截断，排除项记录原因。
9. 在请求 Provider 前创建不可变 Memory Packet 和明细，随后把 `memory_packet_id` 绑定逻辑 Model Attempt。此步骤当前未实现，也不是阶段2工作项。

未来若启用，Memory 内容应以带层级和来源类型的数据区块进入 Prompt，不能与系统指令拼成同一自由文本；`external_untrusted` 内容必须标记为待分析资料。Context Builder 只能选择已通过 Policy 的内容，不能批准候选、提高信任级别或修改事实。这些规则当前只用于设计审查，不表示已有模型调用集成。

#### L0～L4 Memory 实验性接口合同

所有读取继续要求平台 API Key；新五层 Memory 的生成、审批、纠正、删除和重建在最终用户认证完成前同时要求内部 API Key。现有 `/memories` 写接口当前只要求平台 API Key，User/Project Memory 实施时必须以兼容迁移方式增加内部写边界并更新 OpenAPI/调用方测试，不能用文档提前声称已经生效。审批接口未来可以转为最终用户或 Project 成员动作，但当前不能宣称请求中的 `user_id` 等于已认证用户。

| 接口 | 用途与主要输入 | 正常输出 | 主要错误 |
|---|---|---|---|
| `POST /api/v1/internal/agent-runs/{agent_run_id}/working-memory/checkpoints` | 带期望版本保存 L0 检查点 | 新 Working State 版本和当前指针 | 401、404、409、422 |
| `GET /api/v1/internal/agent-runs/{agent_run_id}/working-memory` | 读取该 Agent 当前可恢复状态 | 当前版本、预算、待处理引用和过期时间 | 401、404 |
| `GET /api/v1/sessions/{session_id}/memory` | 读取当前 State、Summary、覆盖消息范围和版本 | 当前可用版本；生成失败时可返回旧版本并明确 `is_stale` | 401、404、409 |
| `POST /api/v1/sessions/{session_id}/summaries` | 使用期望消息序列、模型和幂等键生成摘要 | `generating/active/failed` 记录及版本 | 401、409、422、503 |
| `GET /api/v1/sessions/{session_id}/summaries` | 分页读取摘要版本 | 不可变版本和来源范围 | 401、404 |
| `PATCH /api/v1/projects/{project_id}/memory-policy` | 显式启用、暂停或关闭 L3 | Project Memory 状态和生效时间 | 401、404、409、422 |
| `GET /api/v1/projects/{project_id}/memory` | 读取当前 Project Profile | Profile JSON/Markdown、来源和 token 证据 | 401、404 |
| `GET /api/v1/projects/{project_id}/memory-candidates` | 分页读取项目候选 | 候选、信任来源和审批状态 | 401、404 |
| `POST /api/v1/projects/{project_id}/memory-candidates/{memory_id}/approve` | 批准 Project 候选 | active Project Memory；不隐式重建 Profile | 401、404、409、422 |
| `POST /api/v1/projects/{project_id}/memory/rebuild` | 重建 Project Profile | 生成记录及新版本 | 401、409、503 |
| `GET /api/v1/users/{user_id}/memory-profile` | 读取当前核心 Profile | Profile JSON/Markdown 版本和 token 证据 | 401、404 |
| `GET /api/v1/users/{user_id}/memory-candidates` | 分页读取候选 | 候选、来源信任和审批状态 | 401、404 |
| `POST /api/v1/users/{user_id}/memory-candidates/{memory_id}/approve` | 带期望版本批准候选 | active Memory；不隐式重建 Profile | 401、404、409、422 |
| `POST /api/v1/users/{user_id}/memory-candidates/{memory_id}/reject` | 带期望版本拒绝候选 | rejected Memory | 401、404、409 |
| `POST /api/v1/users/{user_id}/memory-profile/rebuild` | 以当前正式事实重建 Profile | 生成记录及新版本 | 401、409、503 |
| `POST /api/v1/internal/runs/{run_id}/agent-handoffs` | 提交完整或部分 Handoff | 不可变 Handoff 及快照对象版本 | 401、404、409、422、503 |
| `GET /api/v1/internal/runs/{run_id}/agent-handoffs` | 按 Task/Agent Run 分页读取 | Handoff 与替代关系 | 401、404 |
| `GET /api/v1/internal/runs/{run_id}/memory` | 读取当前 Run Memory | 当前状态、来源 Handoff 和版本 | 401、404 |
| `POST /api/v1/internal/runs/{run_id}/memory/rebuild` | 以期望版本与 Handoff 集合重建 | 生成记录及新当前版本 | 401、409、422、503 |
| `GET /api/v1/internal/agent-runs/{agent_run_id}/memory-packet` | 读取实际交给 Agent 的固定 Memory 输入 | Packet、每项版本、hash 与 token 证据 | 401、404 |

这些接口当前只供受信开发和设计验证使用，不纳入前端或 `/responses`。L0 和原始 L2 接口位于 `/api/v1/internal` 并要求双密钥；未来面向用户的 Run 进度接口只能返回经脱敏的共享状态，不暴露 Working State、完整 Handoff 或 Memory Packet。正式启用前还必须重新审核签名 cursor、写入 Schema、资源关系、错误分类和对象存储失败行为。

#### 向量存储决定

五层 Memory 默认不引入 Vector Store 或 Mem0：Agent Working State、User/Project 核心 Profile 直接读取，Collaboration Memory 使用 Run/Task 依赖与 Handoff 精确引用，Session 使用 Summary、最近消息和有界词法/全文检索。当前也没有供应商中立 Embedding 契约、异步索引 Worker、删除同步或固定检索评估集。

只有本节前述向量门禁全部满足后才允许新增投影：MySQL/MinIO 始终是事实源；Qdrant 或其他向量库只保存可重建向量和事实版本标识；查询后必须回 MySQL 复核 owner、scope、状态、过期和删除；使用 Outbox 驱动索引、tombstone 和全量重建；固定评估证明词法基线在真实样本上不足后再启用。Mem0 若未来评估，也只能作为可替换的编排组件，不能成为事实源或绕过现有审计与权限边界。

#### Memory 后续规划工作项

M1～M5 编号仅作为既有设计引用保留。它们不再属于阶段2实施顺序；在用户明确决定 Memory 正式启用阶段前，不继续补充代码或接入模型调用。

| 工作项 | 需要修改或新增的实际对象 | 测试与验证 | 完成条件 | 状态 |
|---|---|---|---|---|
| Agent Working Memory | `features/memory` 中已有最小 Agent Run、Turn、`agent_working_state.v1`、预算和内部检查点实验代码 | 已有部分快速测试；等待恢复、工具事实复核和定时擦除未验证 | 正式启用前重新确认与阶段3 Agent Runtime 的边界 | 暂停 |
| M1 Session Memory | 已有 State/Summary、版本指针、MinIO JSON/Markdown 快照实验代码 | 已有部分快速测试；真实 MySQL/MinIO 部分失败与孤儿清理未验证 | 正式启用前重新确认摘要形成、保留和失败恢复策略 | 暂停 |
| Project Memory | 已有最小 Project scope、策略、Workspace、候选和显式 Profile rebuild 实验代码 | 跨 Project、真实并发和双存储失败矩阵未验证 | 正式启用前重新确认 Project 权限和 Profile 生命周期 | 暂停 |
| M2 User Memory | 已有候选审批/拒绝、信任过滤、凭据拒绝和 Profile rebuild 实验代码 | 纠正后并发 rebuild、删除语义和真实存储失败未验证 | 正式启用前先确定最终用户认证、敏感信息和删除策略 | 暂停 |
| M3 Collaboration Memory | 已有 Handoff、Run Snapshot 实验代码；Packet 没有创建流程 | Packet 固定版本、并发指针和真实存储失败未验证 | 与 Agent Runtime 一起重新规划，不接入当前 Model Gateway | 暂停 |
| M4 后台形成与压缩 | 尚无正式实施安排 | 重复消息、乱序、崩溃重投、永久失败和状态恢复 | 只有 Memory 正式启用且阶段10 Worker 可用后才能排期 | 暂停 |
| M5 语义检索评估 | 固定中英文评估集、Embedding Provider 契约、Outbox 投影、候选向量库适配器；仅门禁通过后实施 | recall@k、精确率、延迟、费用、删除一致性、降级、重建和数据出境 | 评估证明相对词法基线有必要收益，且权限/删除/重建测试全部通过；否则保持不实施 | 暂停 |

当前实验性 Memory 迁移为 `20260803_0008_phase2_memory_layers.py`。该迁移已经在全新 MySQL 8.4 数据库完成 `0001 → 0009` 升级、`0009 → 0007` 降级、重新升级和 `alembic check`，真实 MySQL/MinIO 基础设施测试也已通过；这证明当前迁移链和已覆盖的持久化约束可执行。它仍不能代表五层 Memory 已具备生产运行能力：后台形成、Memory Packet、跨存储失败补偿、孤儿对象清理、最终用户权限和完整并发恢复矩阵尚未实施或验证，因此继续作为未来正式启用前的门禁，不计入阶段2完成条件。

当前不实施：Vector Store、Mem0、知识图谱、自动 Skill 提升、没有 Worker 的后台抽取、最终用户公开审批接口、多用户 Project Membership、项目看板/排期、隐藏思维链保存、覆盖式快照和从 `latest` 文件恢复。待确认：最终用户认证主体；摘要模型与费用上限；敏感信息保留/法定删除策略；MySQL 中文全文 parser；MinIO 生命周期与快照保留期；预算是否经固定样本调整；Agent Run 状态和 Task Handoff 完成门禁；Agent Working State 擦除期；Markdown 是否提供人工查看端点。

#### Memory 当前目录边界

Memory 已从全局 `models/routers/schemas/services` 平铺目录迁移到单一业务特性目录，同时保留 MVC 内部分层：

```text
apps/api/src/nexuspilot_api/features/memory/
├── models/       # L0～L4、事实账本、快照和 Project ORM
├── schemas/      # Memory、Session、Project、User 和协作接口契约
├── services/     # 策略、事务、检索、Profile、快照和 L0～L4 用例
└── routers/      # 公共及 internal Memory HTTP Controller
```

`nexuspilot_api.models` 继续作为 ORM 元数据统一登记入口，避免 Alembic 漏载模型；通用 `routers/common.py`、数据库、对象存储和错误类型仍留在全局基础设施层。Context、Knowledge、Prompt 和 Evaluation 不属于 Memory 内部实现，不因目录调整被搬入 Memory。

#### Memory 复杂度审查结论

当前实现决定仍是停止扩展Memory，只维护规划和已经存在的实验性准备代码，因此尚不进入实际模型响应链路。目标架构不变：L0～L4来源正式启用后统一由Context Builder读取；Memory Packet若保留，只是Memory Service提供的版本化候选包，不能成为绕过Context Build或Model Attempt关联的第二条输入链路。

暂不继续实现：自动记忆形成、定时压缩/擦除、Agent Runtime 自动检查点、Embedding、Vector Store、Mem0、知识图谱、后台 Profile 刷新和自动 Lesson 提升。它们分别依赖阶段10 Worker、阶段3 Agent Runtime 或固定检索评估门禁；现在加入只会扩大状态机和双存储恢复面，不能提高当前同步接口的可靠性。

### Knowledge：移出阶段2

- Knowledge 表达外部资料及其引用、版本、解析和检索，属于独立知识库，不属于 Memory，也不再属于阶段2。
- 仓库中现有 Knowledge ORM、Schema、Service 和 Router 是未完成的实验性准备代码，不作为稳定 API，不进入阶段2测试数量、完成状态或前端第一版功能。
- 后续必须先单独讨论资料来源、上传与 Artifact 关系、解析器、分块、检索质量、向量方案、引用展示、用户/项目权限、版本切换、删除和失败恢复，再建立知识库实施文档。
- 在独立规划批准前不启用Knowledge运行时来源；批准并完成权限、检索和引用门禁后，只能通过Context Builder接入`/responses`和Agent，前端不能直接把Knowledge结果拼入Prompt。

### System Prompt 与模型能力目录

- System Prompt Template 具有稳定名称、不可变版本、用途、变量 Schema、启用状态和变更记录；Model Catalog 记录 provider/model 的 context window、工具、结构化输出、视觉、缓存和流式能力。
- 内部管理接口负责新增版本、启用和停用；System Prompt渲染只供后端运行策略或受信内部调用使用。普通前端只读取有效模型能力，不读取Prompt候选、元数据或渲染结果。

```text
POST /api/v1/prompt-renders
GET  /api/v1/model-capabilities

POST  /api/v1/internal/prompt-templates
POST  /api/v1/internal/prompt-templates/{template_name}/versions
PATCH /api/v1/internal/prompt-templates/{template_name}/active-version
POST  /api/v1/internal/model-catalog-versions
PATCH /api/v1/internal/model-catalog-versions/{catalog_version}/status
```

- Prompt 渲染接口必须拒绝缺失变量、未知变量、类型不符和超长结果；渲染与模型调用分离，单独调用不会产生模型费用。
- 同一 Prompt 名称只能有一个启用版本；版本内容创建后不可修改。并发启用通过唯一约束和事务条件更新保证。
- 模型目录使用 `provider + model + catalog_version` 标识不可变能力快照，包含来源、确认时间和启用状态；未知能力表示 `unknown`，不能默认为支持。
- 路由策略只读取能力快照和显式配置，不根据模型名称硬编码业务分支。调用证据必须记录实际使用的 Prompt 和目录版本。

### Evaluation 与 Guardrail

- 复用并扩展现有 `llm_evaluations`，不创建语义重复的评估事实表；增加规则集版本、幂等键、输入证据 URI、状态和有界错误字段。
- 确定性 Schema、引用完整性、长度和安全检查优先于模型评分。重要结果可以调用独立 evaluator，但必须通过 Model Gateway 并记录 evaluator attempt。

```text
POST /api/v1/evaluations
GET  /api/v1/evaluations/{evaluation_id}

POST  /api/v1/internal/evaluation-rule-sets
PATCH /api/v1/internal/evaluation-rule-sets/{rule_set_id}/status
```

- Evaluation 接口接受 Run/Task、候选 Attempt 或 Artifact、规则集版本和幂等键；同一幂等键及同一输入版本重复提交返回原结果。
- 状态只允许 `pending -> running -> completed | failed | cancelled`；规则结论使用独立 `verdict`，不能用执行失败代替“不通过”。
- Guardrail 失败返回稳定错误类型、规则 ID 和可公开证据，不删除用户参数后继续请求，不泄露内部规则正文或敏感输入。
- Evaluation 单元可以脱离 Agent 单独调用；外部 evaluator 超时或失败时保留确定性检查结果并明确整体状态，不伪造完整评估成功。

## Model Gateway：统一接口契约（含 DeepSeek 最新 Chat 适配，已验证）

### 模型请求

统一请求对象（代码标识建议：`ModelRequest`）至少包含：

```text
provider
model
messages
system_instruction
tools
output_schema
reasoning
temperature
max_output_tokens
timeout_seconds
metadata
```

业务 HTTP 请求还必须包含 `run_id`，可以包含 `task_id`。服务必须校验任务属于对应运行，再创建模型调用记录。

约束：

- `provider` 使用受控枚举，不允许将任意 URL 当作供应商地址。
- HTTP `model` 最长 128 个字符，与 `llm_attempts.model` 的 MySQL 列宽一致；超长名称在创建 Attempt 前返回 `422`。
- `messages`、工具结构和输出结构在进入适配器前完成 Pydantic 校验。
- `timeout_seconds` 省略时为 60 秒，有限值限制为 1–600 秒；显式 null 表示不设置模型生成截止时间，但保留显式取消、调用次数和工具资源限制。
- `metadata` 只保存可检索的非敏感标识，不保存 API Key、完整私人文件或未脱敏凭据。
- `max_output_tokens` 保留为可选 Provider 参数，统一允许范围为 1～65536。调用方省略时，OpenAI、DeepSeek/OpenAI-Compatible 和 Gemini 适配器不发送该限制字段，避免平台主动缩短模型输出；Anthropic Messages 协议要求请求包含 `max_tokens`，因此省略时使用 65536 作为协议兼容值。后续若需要按模型细分上限，应通过 Model Catalog 能力合同设计，不在各业务入口散落不同阈值。
- `output_schema` 只表示期望的 JSON Schema；适配器必须根据供应商能力决定原生结构化输出或返回明确的“不支持”错误，不得静默忽略。
- `reasoning` 是可选的供应商中立思考参数；Provider 必须依据模型能力显式映射或返回 `unsupported_capability`，不得静默删除、改写强度或切换到另一种调用语义。

### 模型响应

统一响应对象（代码标识建议：`ModelResponse`）至少包含：

```text
text
tool_calls
structured_output
finish_reason
input_tokens
output_tokens
cached_tokens
estimated_cost
latency_ms
provider_request_id
raw_response
reasoning_tokens
reasoning_blocks
```

HTTP 默认响应不直接返回无限制的 `raw_response`。完整原始内容写入 MinIO，API 返回 `attempt_id` 和必要的统一字段；只有内部受控调用才能读取原始对象。

### 流式事件

流式接口使用 SSE（Server-Sent Events），事件类型固定为：

```text
response.started
response.text.delta
response.tool_call.delta
reasoning.started
reasoning.raw.delta
reasoning.summary.delta
reasoning.completed
reasoning.interrupted
response.usage
response.completed
response.failed
```

每个事件至少携带 `attempt_id` 和单调递增的 `sequence`。正常结束、供应商错误、超时、客户端断开都必须结束或更新对应 `llm_attempts`，不能长期保留无法解释的 `started` 状态。进入 Provider 适配器后发生取消且无法确认是否已经发出请求时，Attempt 使用 `outcome_unknown`，不得自动重新计费调用；Provider 响应已经返回时，响应审计终结会先完成再传播调用方取消。

2026 年 8 月 20 日为阶段4 Web 消费补充的兼容实现保持阶段2原完成判断不变：流事件升级为 `conversation-stream.v2`，增加全局事件标识、Attempt/Run/Step 关联、时间和公开事件持久化；`GET /attempts/{attempt_id}/events` 支持有限回放，`GET /attempts/{attempt_id}/reasoning-blocks` 返回最终公开快照。DeepSeek `reasoning_content` 只在 `provider-visible` 下映射为原始推理；OpenAI summary 始终保持摘要语义；只有加密内容或 reasoning token 时只生成无文本状态。最终 Message 通过 `source_model_attempt_id` 关联公开推理历史。

Provider 工具续接与公开展示相互独立。DeepSeek 实际工具调用所需的完整推理与调用参数、OpenAI 加密输出项会使用 AES-GCM 加密后写入对象存储，元数据按父 Attempt、Run、Task、Provider 和 Model 绑定并设置过期时间；生产环境必须使用独立续接加密密钥。每次新建续接状态前会有界清理一批过期密文和元数据，对象存储暂时失败则保留记录供后续重试，不影响当前响应。该状态不会进入普通数据传输对象、SSE、消息正文或公开原始响应，Provider response ID 在公共 Attempt/Response 中保持为空。

## Model Gateway 实施范围（原有基线已完成）

- 需要修改：后端依赖与配置，四家供应商凭据继续来自 API 环境文件，允许的基础 URL 由数据库独立保存；统一超时、重试和价格仍使用服务端配置。
- 需要修改：数据库模型与 Alembic 迁移，补足调用重试次数、统一错误类型及必要的幂等标识；不复制已有 token、费用、URI 字段。
- 需要新增：`packages/models` 独立 Python 包，承担统一类型、供应商协议、适配器注册、错误转换、重试、流式事件归一化和费用计算。
- 需要修改：FastAPI 增加统一 `POST /api/v1/responses` 入口，通过 `stream` 决定普通 JSON 或 SSE，并由应用服务协调调用记录与对象存储。
- 需要新增：供应商契约测试、服务测试、SSE 测试、超时和重试测试，以及可选的真实供应商冒烟测试入口。
- 需要修改：README、环境变量示例和当前实施说明。
- 明确不修改：任务调度状态机、outbox 发布逻辑、工具权限模型、Agent 角色和 Compose 中尚未启用的 RabbitMQ。

## 目录与职责

```text
packages/models/
├── pyproject.toml
├── src/nexuspilot_models/
│   ├── contracts.py          # 统一请求、响应、消息、工具调用和流事件
│   ├── errors.py             # 稳定的平台错误分类
│   ├── provider.py           # ModelProvider 抽象协议
│   ├── registry.py           # 供应商名称到适配器的显式登记
│   ├── pricing.py            # 基于显式版本价格表估算费用
│   ├── transport.py          # 通用 HTTP、SSE、超时、重试与物理请求证据
│   └── providers/
│       ├── openai.py
│       ├── deepseek.py          # DeepSeek Chat Completions 专用约束
│       ├── openai_compatible.py # 可配置兼容服务及共享 Chat codec
│       ├── anthropic.py
│       └── gemini.py
└── tests/
    └── test_providers.py

apps/api/src/nexuspilot_api/
├── main.py                   # FastAPI 应用组装与资源生命周期
├── core/
│   ├── config.py             # 凭据、超时及功能开关；不保存 Provider 地址
│   ├── security.py           # API 认证
│   └── dependencies.py       # Provider 与业务服务依赖注入
├── routers/                  # 按业务资源拆分的 HTTP Controller
│   ├── responses.py          # 统一生成和 SSE 路由
│   ├── providers.py          # Provider 发现
│   ├── users.py
│   ├── sessions.py
│   ├── runs.py
│   ├── tasks.py
│   ├── model_attempts.py
│   ├── run_artifacts.py
│   └── internal_audit.py
├── services/                 # 事务边界和业务规则
│   ├── model_response_service.py
│   ├── user_service.py
│   ├── run_service.py
│   ├── task_service.py
│   ├── model_attempt_service.py
│   └── run_artifact_service.py
├── models/                   # 按身份、执行、调用和证据拆分的 ORM Model
├── schemas/                  # 按资源拆分的 Pydantic 请求/响应结构
└── infrastructure/          # SQLAlchemy、MinIO 和 Provider 组装
```

`packages/models` 不直接访问 FastAPI、SQLAlchemy、MySQL 或 MinIO。`routers` 只处理 HTTP 输入输出，`services` 负责业务规则和事务，`models` 负责持久化映射，`infrastructure` 封装外部系统。`apps/api` 通过这些分层把独立模型包接入平台事实记录，避免供应商代码反向依赖业务数据库。

## Model Gateway 实施步骤（原有基线已完成）

| 步骤 | 修改对象 | 预期结果 | 验证方式 |
|---|---|---|---|
| 1 | `packages/models` 包结构与统一类型 | 普通生成、工具调用、结构化输出和流事件都有稳定类型；所有新增或实质修改函数带说明注释 | Ruff、类型导入检查、Pydantic 边界测试 |
| 2 | `ModelProvider`、registry、错误类型 | 业务按 `provider` 获取适配器；未知供应商和不支持能力返回稳定错误 | registry 单元测试、错误映射参数化测试 |
| 3 | OpenAI 与 DeepSeek 适配器 | 两家供应商通过相同调用入口完成普通生成、SSE、token 和请求编号归一化 | 使用录制/伪造 HTTP 响应的适配器契约测试，不产生费用 |
| 4 | Anthropic 与 Gemini 适配器 | 将消息、工具调用、结构化输出和结束原因映射到同一协议 | 与步骤 3 相同的契约测试矩阵 |
| 5 | 超时、重试和错误转换 | 仅对限流、短暂网络故障和部分 5xx 重试；认证、参数错误和明确不支持不重试 | 超时、退避、最大次数、不可重试错误测试 |
| 6 | token 与费用估算 | 优先记录供应商返回 usage；费用按带生效日期和币种的显式价格配置计算，未知模型返回 `estimated_cost=null` | 已知/未知模型、缓存 token、舍入边界测试 |
| 7 | `services/model_response_service.py` 和数据库迁移 | 调用开始即产生 attempt；成功、失败、超时和断流均写入最终状态；原始内容写入 MinIO | 服务事务测试、迁移升级与 `alembic check` |
| 8 | FastAPI 普通生成接口 | `POST /api/v1/responses` 校验运行和任务后返回统一结果及 `attempt_id` | ASGI 集成测试、鉴权和跨运行 task 拒绝测试 |
| 9 | FastAPI SSE 接口 | 同一接口设置 `stream=true` 后按固定事件协议输出并正确处理断开 | SSE 顺序、结束事件、错误事件、客户端取消测试 |
| 10 | 文档与可选真实冒烟测试 | 环境变量、调用示例、供应商能力差异和测试方式可复现 | README 命令审查；仅在显式开关及凭据存在时运行真实测试 |

## 错误与重试规则

统一错误至少区分：

```text
authentication_error
invalid_request
unsupported_capability
rate_limited
provider_unavailable
timeout
network_error
response_parse_error
response_audit_failed
response_persistence_error
cancelled
internal_error
```

- 认证失败、请求无效、不支持能力和响应结构确定性错误默认不重试。
- 限流、网络中断和供应商暂时不可用可以在总超时内按指数退避重试。
- 每次物理供应商请求仍应有独立证据。若继续复用一个 `attempt_id`，必须新增可查询的 retry 明细；不能只覆盖最后一次错误。
- Provider 已返回但 MinIO 原始响应保存失败时，统一调用返回 `response_audit_failed`，Attempt 记为 `failed`，但 token、费用、延迟、供应商请求编号和物理请求证据必须一并保存。完成事务出现一次暂时或不明确失败时先按持久化状态重新读取并安全重试；仍无法确认时记为 `outcome_unknown`，保留已知计费事实，不能再次调用 Provider。
- 日志、错误文本、原始请求和原始响应在持久化前必须移除 Authorization、API Key 和供应商凭据。
- 未配置某供应商凭据或数据库中没有启用地址时，启动可以继续，但该 Provider 不注册，调用必须返回明确配置错误。非法数据库地址属于启动配置错误，不允许静默回退环境变量。

## 兼容策略

- 保留阶段1的运行、任务、attempt 和 artifact 标识，不修改现有公开标识语义。
- `POST /api/v1/runs/{run_id}/attempts` 在阶段2暂时保留；新业务调用使用统一模型生成接口。确认没有外部调用方后，才能在后续版本弃用。
- 新数据库字段通过向前迁移添加，不重写或删除已有记录。
- 供应商原始结束原因保存在原始响应，公共 `finish_reason` 使用平台枚举，避免业务依赖厂商字符串。
- 某供应商不支持工具或结构化输出时返回明确能力错误，不通过删除参数后继续请求来伪造成功。

## 风险与停止条件

- 风险：四家供应商对 system message、工具参数、JSON Schema、缓存 token 和流结束事件的语义不同；适配器契约测试必须覆盖差异，不能假设 OpenAI 格式适用于全部供应商。
- 风险：SSE 客户端断开可能发生在供应商仍输出时；实现必须取消上游请求并最终记录 attempt 状态。
- 风险：价格会变化；价格配置必须注明来源、生效时间和币种，未知价格不允许按 0 计费。
- 风险：原始响应可能包含用户敏感内容；只写入受控对象存储，不写普通日志或 OpenTelemetry 属性。
- 风险：Memory 可能把模型推测固化为长期事实，因此当前禁止将 Memory 自动接入 `/responses`；未来启用时必须先验证来源、置信度、纠正和删除机制。
- 风险：不同用户、Session 或项目的数据可能错误关联；当前受信调用方边界内所有读写必须在 Service 和查询层验证 owner scope。直接面向最终用户前必须另行完成用户认证授权。
- 风险：实验性 L1～L4 Memory 快照同时写入 MySQL/MinIO 时可能部分成功；当前不把这些接口声明为生产能力。未来正式启用时必须验证版本记录、指针切换和孤儿清理。
- 风险：Prompt、模型目录、解析器、tokenizer 或评估规则升级后可能无法复现历史结果；执行证据必须记录实际使用的不可变版本。
- 风险：并发切换 Prompt 版本可能产生多个有效版本；必须使用唯一约束、状态条件更新和事务冲突响应。Memory/Knowledge 并发规则留到各自正式启用规划中复核。
- 需要停止并确认的情况：实施要求使用未提供且无法通过安全配置表达的私有供应商协议。
- 需要停止并确认的情况：需要执行会产生明显费用的批量真实模型测试。
- 需要停止并确认的情况：现有 `llm_attempts` 无法表达每次物理重试且必须进行破坏性数据迁移。
- 需要停止并确认的情况：供应商能力与验收要求冲突，例如指定模型明确不支持工具调用但要求静默兼容。
- 需要停止并确认的情况：要求把受信调用方接口直接开放给最终用户，但没有确定可验证的认证主体和权限声明。
- 需要停止并确认的情况：要求对阶段1不可变 Message 做物理级联删除，或要求使用尚未确定的法定保留策略。

## Model Gateway 原有基线完成标准（已达到）

- OpenAI、DeepSeek、Anthropic 和 Gemini 均实现 `ModelProvider` 契约并通过同一参数化契约测试。
- 至少 OpenAI 和 DeepSeek 可在配置启用地址及对应凭据后通过同一业务接口切换调用。
- 普通生成和 SSE 流式输出均返回统一协议。
- 工具调用和结构化 JSON 输出在供应商支持时能被统一解析；不支持时返回明确错误。
- 成功、失败、超时、重试和客户端取消都有与实际行为一致的 attempt 记录。
- token、缓存 token、费用、耗时、供应商请求编号和原始响应位置可以通过 `run_id` 查询。
- API Key 和供应商凭据不出现在数据库原始内容、错误响应和普通日志中。
- Ruff、定向单元测试、ASGI 集成测试、迁移升级和 `alembic check` 全部通过。
- 真实供应商测试若未运行，文档明确标为“未运行”，不以伪造响应测试代替真实连通性结论。
- README、`.env.example` 和当前实施文档与最终运行方式一致，不保留相互冲突的阶段2描述。

## Model Gateway 原有基线实施结果

- `provider` 使用稳定枚举；`model` 保留供应商原始字符串，并由对应 Provider 的可选 allowlist 校验组合。
- OpenAI 使用 Responses API codec；DeepSeek 使用专用 Chat Completions 策略适配器并复用通用 Chat codec；Anthropic 使用 Messages codec；Gemini 使用 GenerateContent codec。
- 公共 HTTP 层封装为 `POST /api/v1/responses`，业务方不直接接触任何供应商 payload。
- `HttpTransport` 统一负责 JSON、SSE、连接超时、HTTP 状态分类和指数退避，Provider 只负责编解码。
- 每个逻辑调用写入 `llm_attempts`；每个物理网络请求写入 `llm_attempt_retries`。
- API Key 仅从服务端 `SecretStr` 配置进入认证 Header，不进入 Provider 地址表、公共请求、数据库原始请求或错误响应。
- 价格不内置可能过期的数字，只读取带供应商/模型键的显式 JSON 配置；未知价格保持 `null`。
- Provider Registry 启动时读取 `llm_provider_endpoints` 的启用地址，不再读取 `*_BASE_URL` 环境变量；未配置启用地址或必要凭据的 Provider 不注册，服务仍可启动，调用时返回 `provider_not_configured`。
- `llm_provider_endpoints` 允许每个 Provider 保留停用历史，但数据库约束保证最多一个启用地址；Base URL 只允许 HTTP(S)、禁止用户名和密码、允许显式端口和路径。迁移为 DeepSeek 创建 `https://api.deepseek.com` 启用记录，既有 `llm_models` 和 `llm_model_catalog_versions` 保持不变。
- 单元、Provider 契约、SSE、ASGI、迁移一致性和静态检查均为阶段2验收项。
- 真实供应商连通性测试：未运行。本阶段规划修订没有读取供应商凭据，也没有产生模型费用。

## DeepSeek 官方接口复核与实施结果

以 2026 年 8 月 1 日的 [DeepSeek Chat Completions 官方文档](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/) 为兼容依据。以下约束已经通过 Provider 契约和 FastAPI 集成测试；真实 DeepSeek 连通性仍未运行：

- NexusPilot 只调用 DeepSeek `POST /chat/completions`。DeepSeek 的 Responses 端点只作为协议设计参考，本阶段不实现；平台自有 `POST /api/v1/responses` 门面仍由 DeepSeek Chat Completions 适配器完成转换。
- 官方当前模型值为 `deepseek-v4-flash` 和 `deepseek-v4-pro`。平台仍保留可配置 allowlist，不把模型名散落在业务分支中；后续由 Model Catalog 记录模型能力快照。
- 统一请求的供应商中立 `reasoning: {enabled, effort}` 已支持 `minimal | low | medium | high | xhigh | max`，且禁用reasoning时不允许同时传effort。对已登记模型，即使请求未显式提供reasoning，服务端也会解析为该模型的版本化默认；当前产品默认固定为“启用 + medium”。
- DeepSeek Chat Completions原生接受`low | high | max`；平台当前为两种DeepSeek V4模型登记`minimal/low → low`、`medium/high/xhigh → high`、`max → max`，并把最终原生值写入Attempt。该转换来自逐模型策略记录，不依赖Provider在收到不匹配值后静默改写。
- reasoning 启用时 DeepSeek 不支持 `temperature`；平台返回 `invalid_request`，不删除参数后重试。reasoning 显式禁用时才允许传递已验证的 `temperature`。
- DeepSeek 流式响应必须收到 `data: [DONE]` 才视为完整结束。缺失终止标记或响应提前断开都按 `response_parse_error` 处理，生成 `response.failed` 并将 attempt 结束为失败，不把已收到的部分文本伪装成成功。
- 官方已标记 `frequency_penalty` 和 `presence_penalty` 弃用且无效，统一 HTTP Schema 不引入这两个参数，未知字段继续由 `extra="forbid"` 拒绝。官方 `user_id` 也不在本次实施范围。
- 工具或结构化输出中的 `strict` 只能在 Model Catalog 对当前模型明确记录支持时传递。未声明或未验证时必须返回 `unsupported_capability`，不得删除 `strict`、放宽 Schema 或改用普通文本后继续请求。本计划不声称 DeepSeek beta strict 已经完成真实供应商验证。
- 已验证新参数Schema边界、逐模型映射、reasoning/temperature冲突、`[DONE]`完整/缺失、展示策略裁剪、工具续接完整回放、失败Attempt落库、弃用字段拒绝、strict能力失败和原有Chat Completions回归。`reasoning_content`只在调用方明确选择且模型目录允许`provider-visible`时进入公开统一块；默认策略不会暴露。真实请求未运行，不能据此声称供应商凭据、网络或账户权限已经可用。

## 跨 Provider 思考程度控制实施结果

状态：已完成。`ModelReasoningCapabilities`继续只描述推理内容如何展示、流式传输、续接和计量；用户可见的思考控制策略已经记录在稳定模型实体`llm_models`。Model Catalog和Provider适配器继续保存、验证供应商能力事实；`llm_models`保存平台实际向用户开放的等级、默认值和显式映射，两者职责不互相替代。

迁移`20260828_0017`已为`llm_models`增加`reasoning_supported_efforts_json`、`reasoning_supports_disable`、`reasoning_default_enabled`、`reasoning_default_effort`、`reasoning_effort_mapping_json`和`reasoning_profile_version`，并为`llm_attempts`增加对应的最终解析证据字段。所有可提交模型的产品默认必须解析为`reasoning.enabled=true`、`reasoning.effort=medium`；缺少原生`medium`时，只能使用该模型记录中经过验证、带版本的显式映射。无法解析`medium`的模型由`GET /models`报告为`reasoning_profile_invalid`。

- 统一程度枚举扩展为 `minimal | low | medium | high | xhigh | max`。`none` 不作为 effort，而由 `enabled=false` 表示，继续保持“是否启用”和“启用后强度”两个正交字段。
- OpenAI Responses把映射结果写入`reasoning.effort`，显式关闭写为`none`；DeepSeek写入`thinking.type`和`reasoning_effort`；Anthropic写入`thinking.type=adaptive`与`output_config.effort`；Gemini按模型记录写入`thinkingConfig.thinkingLevel`或数值`thinkingBudget`。
- Provider 适配器不得把未登记或不支持的程度向最近等级取整。即使供应商兼容层会接受并改写，平台也必须在网络调用前返回稳定 `unsupported_capability`，让 Attempt 和用户选择保持一致。
- `/models`返回稳定模型的有效等级、关闭能力、中等默认和策略版本，不向前端暴露Provider映射表。普通Responses请求未显式提供配置时，服务端解析稳定模型默认；未登记模型仅为旧内部调用保留原始低层合同，不获得产品默认语义。
- Model Attempt 必须保存用户选择或稳定模型默认形成的统一配置、思考策略版本和最终解析结果；历史 Attempt 不因 `llm_models` 后续更新而改变解释。
- OpenAI-compatible自定义端点仍不能从协议名称推断思考字段；在实现专用codec前，内部模型管理接口拒绝为其写入思考映射，其模型不会因伪造策略进入可提交状态。

Agent角色透传属于阶段3集成项；Composer选择器、模型切换行为及浏览器门禁属于阶段4集成项，记录在[`phase-4-web-ui-plan.md`](../frontend/phase-4-web-ui-plan.md#思考程度选择与跨-provider-对齐)。

### 2026 年 8 月 28 日思考程度合同验收

- 统一请求、逐模型策略解析、四家Provider传输映射、Model Attempt证据和模型目录读取均已纳入自动化测试；全量结果为`290 passed, 4 skipped`。
- 本地MySQL已从`20260827_0016`迁移到`20260828_0017 (head)`；两条现有DeepSeek模型记录均默认启用`medium`，支持的统一等级、关闭能力、Provider映射和`reasoning.v1`策略版本已实际回填。
- Ruff全量检查通过，`20260827_0016 → 20260828_0017`的MySQL离线迁移SQL生成通过。
- `alembic check`仍报告实验性Memory表历史外键删除策略与当前ORM不一致。该已知漂移不涉及`llm_models`、`llm_attempts`或本次运行路径，且修正会改变已暂停Memory能力的数据删除语义，因此不并入阶段2思考合同修改。
- 未发送真实供应商请求，未读取供应商凭据，也未产生模型费用；当前验收证明平台合同、持久化和Provider请求编码，不证明供应商账户与公网链路可用。

## 2026 年 8 月 3 日历史审查与验证记录

- Memory 已迁入 `features/memory`，内部继续按 Model、Schema、Service、Router 分层；架构测试同时检查全局 Router 和 Memory Router 不直接导入 ORM/SQLAlchemy。
- 快速测试共收集 194 项，其中 190 项通过，4 项真实基础设施测试按默认配置跳过；这 4 项随后在本地 MySQL 8.4 与 MinIO 上显式启用并全部通过。Ruff 全量静态检查通过。
- 本轮修复了 Session 当前指针先于版本插入导致的外键冲突、Profile 快照/明细/指针写入顺序、L2 合并遗漏 confirmed facts、L0 Schema 接受未知隐藏字段，以及 Agent Run 终止参数和非终止状态校验。
- Memory 审批与 Profile rebuild 已拆成两个显式操作。审批只在 MySQL 中转换事实状态；调用方按当前 Profile 版本单独重建快照，MinIO 失败不会被吞掉或伪装成审批已完整刷新。
- 当时的真实 MySQL 验证先发现并修复了唯一 URI 索引过长、MySQL 标识符超过 64 字符，以及外键/索引降级顺序错误；该记录只描述2026年8月3日的迁移状态，不覆盖上方2026年8月28日记录的已知Memory外键漂移。
- Context、Prompt/Model Catalog 和 Evaluation 已补齐行为、失败、幂等、历史证据和真实 MySQL 并发测试。Knowledge 已移出阶段2；Memory 只保留规划和实验性准备代码，不进入稳定调用链。
- 上传文件名同时清理 POSIX 和 Windows 风格路径片段。
- `get_session()` 在请求异常时显式 rollback，并由异步上下文保证 session 关闭。
- 真实 DeepSeek 请求未运行，没有读取供应商凭据，也没有产生模型费用；当前结论只证明本地契约与集成行为。
- RabbitMQ、Worker 和任务自动执行仍未实现，不计入阶段2完成范围。

## 阶段2实施顺序与当前状态

以下内容属于同一个阶段2，不拆分为新的阶段编号。Memory 与 Knowledge 不是本阶段实施或集成门禁；稳定范围已经完成验证：

| 顺序 | 工作项 | 具体任务 | 失败与恢复设计 | 验证门禁 | 状态 |
|---|---|---|---|---|---|
| 1 | Model Gateway 回归 | 保持四家 Provider、统一 Responses、SSE、重试、费用和原始证据行为 | 供应商差异显式失败，不静默降级 | 既有契约和 API 测试持续通过 | 已完成 |
| 2 | Prompt Registry 与 Model Catalog | 验证不可变 Prompt 版本、内部管理/渲染和模型能力目录 | 并发版本单调；未知能力明确拒绝；禁用不影响历史证据 | 变量、并发、能力和版本行为测试通过 | 已完成 |
| 3 | Conversation Context | 收敛为独立预览能力，稳定选择显式指令、安全指令和 Session Message | 来源缺失明确失败；超预算返回排除证据；历史证据可重读 | owner、预算、目录版本、快照重读和错误行为测试通过；未接入 Memory/Knowledge 或 `/responses` | 已完成 |
| 4 | Evaluation 与 Guardrail | 验证规则集、确定性检查和评估接口 | 未知规则安全失败；同键异请求冲突；执行状态与 verdict 分离；敏感输入不泄露 | 状态、幂等、归属、脱敏和失败测试通过 | 已完成 |
| 5 | 阶段2验收 | 验证上述单元可独立调用，并保持 `/responses` 直接使用显式请求输入 | 任一独立单元失败不影响或伪造 Model Gateway 成功；没有隐式 Memory/Knowledge 读取 | Ruff、完整快速测试、迁移循环、OpenAPI、鉴权、敏感信息和真实基础设施测试通过 | 已完成 |

## 逐能力接口和错误契约

所有新增接口沿用阶段1的错误响应结构，至少稳定区分：输入错误、未认证、资源不存在、资源关系不匹配、状态冲突、依赖暂时不可用和内部错误。

| 能力 | 必要输入 | 正常输出 | 不允许的隐式行为 |
|---|---|---|---|
| Context Build | User、Session、模型、总预算和显式指令 | 构建 ID、统一消息、token 估算、选中与排除来源 | 不自动调用模型；不读取 Memory/Knowledge；不无限读取会话；不静默丢弃完整对象 |
| Prompt Render | Prompt 名称/版本和变量 | 渲染文本及版本证据 | 不忽略未知变量；不因渲染而发起模型调用 |
| Model Capability | provider、model 和目录版本 | 明确支持、不支持或未知 | 不把未知能力当作支持 |
| Evaluation | 输入事实、规则版本和幂等键 | 执行状态、verdict、规则证据 | 不把执行失败写成通过或不通过结论 |

## 测试设计

### Memory 未来启用门禁（不属于阶段2完成条件）

以下 Memory 测试矩阵作为未来正式启用时的最低门禁保留，不因现有实验性路由或快速测试而缩减，也不计入当前阶段2完成状态：

| Memory 范围 | 必须覆盖的场景 | 验收结果 |
|---|---|---|
| Schema 与上限 | 1/4000/4001 字符、1/20/21 个来源、置信度/重要度边界、无时区过期时间、未知字段 | 边界内接受，超限和含糊时间在数据库之前被拒绝 |
| 类型与范围 | 五种 MemoryType，User/Session/Run/Task 正常链路，跨 User、跨 Session、跨 Run 和跨 Task | 只接受归属一致的显式范围；每种类型的必要范围和过期规则生效 |
| 来源与创建者 | 五种 source type，来源不存在、跨 owner、重复来源，模型创建 active 或缺少 attempt | 来源标识/哈希可追溯；不可验证来源和模型直接激活均被拒绝 |
| 状态转换 | candidate 激活/拒绝、active 被替代、所有非删除状态删除、终止状态重新激活 | 合法转换成功；非法转换返回 409 并完整回滚 |
| 幂等 | 创建、纠正和检索的同键同请求/同键异请求，重复删除 | 写请求不重复应用并返回当前 Memory 表示；检索返回原证据；改变请求返回 409；不产生重复事实 |
| 版本与并发 | 纠正创建新版本、旧版本不变、stale `expected_version_number`、并发纠正/激活/替代 | 版本号单调递增，只有一个并发修改成功，同一语义槽位只有一个 active Memory |
| 过期与删除 | `expires_at` 之前/之后检索，删除后的列表/详情/检索，所有版本正文和词哈希清理 | 过期和删除内容不参与检索；删除幂等；仅保留无正文审计证据 |
| 检索排序 | 英文归一、中文二元词、owner/scope/status/type 过滤、同分 tie-break、limit 和 token budget | `lexical_hash_v1` 候选和 `memory_ranker_v1` 分数可复现；不截断 Memory；超预算原因可查询 |
| 检索证据与隐私 | 证据详情重读、幂等重放、token 预算排除项、删除后历史证据 | 分数、版本、选中状态和排除原因不变；响应不泄露跨 User、不可用或已擦除正文 |
| 数据库与迁移 | SQLite 快速契约、MySQL 唯一约束/行锁并发、空库与阶段1升级、`alembic check` | 测试与生产数据库差异被显式验证，迁移无漂移 |

L0 Agent Working Memory 在未来与阶段3 Agent Runtime 集成前必须建立以下测试：

| 场景 | 通过条件 |
|---|---|
| Agent 私有范围 | 只能读写相同 `agent_run_id`；其他 Agent、跨 Task/Run 请求被拒绝 |
| Turn 与调用事实 | Working State 只引用 Turn/Attempt/Tool Call；不复制原始请求、响应、费用和完整工具结果 |
| 检查点并发与幂等 | 同键同请求复用；stale 期望版本返回 409；当前指针只有一个 |
| 等待、暂停和崩溃恢复 | 从最近 active 版本恢复，并重新核对待处理工具真实状态 |
| 完成和擦除 | Agent Run 终止后不再注入；清理期后正文擦除，版本/hash 审计仍存在 |
| 隐藏推理边界 | Schema 不接受思维链字段；临时发现必须带来源且只在 Handoff 后对其他 Agent 可见 |

M1 Session Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 正常摘要与长度边界 | JSON Schema、消息起止序号和硬 token 上限均可验证，不截断结构 |
| 重复请求与并发生成 | 同键同请求复用，同键异请求 409，同一 Session 只有一个当前版本 |
| 生成或 MinIO 失败 | 新版本为 `failed`，旧 State/Summary 仍可读取且明确 `is_stale` |
| 会话归属与历史搜索 | 跨 User/Session 被拒绝；相同版本和查询得到稳定排序 |
| 外部工具文本污染 | 外部文本保持不可信数据，不能变成系统指令、决策或正式用户事实 |
| 重启恢复 | 只由 MySQL 指针确定当前版本，不读取对象键猜测最新快照 |

L3 Project Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 默认关闭与显式启用 | 创建 Project 不自动写入/注入；启用需要 owner、策略、预算和双密钥 |
| Session/Run 绑定 | 只有显式同一 `project_id` 可读取；Run 与 Session Project 不一致返回 409 |
| 候选与跨层污染 | Agent/外部内容只能形成 Project 候选；项目事实不进入 User Profile |
| Profile 容量 | 使用记录 tokenizer，目标 800、硬上限 1500 token；单项不截断 |
| Workspace 安全 | locator 不含 token、用户信息或本地敏感路径；没有 Workspace 的非代码项目仍可使用 |
| 暂停、归档、删除 | 停止新写入与注入但保留审计；删除后新 Packet 不再读取正文 |
| 跨 Project/用户隔离 | 相同词项也不能跨 scope 召回；共享项目前不得声称 Membership 已实现 |
| 并发和双存储失败 | Profile 只有一个当前版本；上传或提交失败时旧版本继续服务 |

M2 User Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 明确写入、模型推断和外部来源 | 用户明确来源按策略进入候选/正式事实；模型和外部来源只能形成低信任候选 |
| 候选批准、拒绝和过期 | 状态转换、期望版本和审计字段完整，非法重复操作返回 409 |
| 同一语义冲突、纠正和删除 | 不产生两个 active 语义槽；旧版本不变；删除后新 Profile/Packet 不再注入正文 |
| Profile 容量 | 目标与硬 token 上限使用记录的 tokenizer；单项不被截断；选择原因可解释 |
| 敏感信息和污染 | 凭据拒绝且错误不复制原文；外部指令不能自升信任或扩大 scope |
| 并发与双存储失败 | 只有一个 Profile 指针切换成功；上传/提交任一失败时旧 Profile 继续服务 |
| 归属和权限 | 受信调用方关系校验通过；缺少公共/内部密钥拒绝；不虚构最终用户身份隔离 |

M3 Collaboration Memory 必须先建立以下测试：

| 场景 | 通过条件 |
|---|---|
| 完整与部分 Handoff | 完成记录缺少必要字段时拒绝；异常路径可保存可识别的 `partial` 版本 |
| 并行只读 Agent | 两个 Handoff 可并行创建，Run Snapshot 通过期望版本串行归并且不丢来源 |
| 冲突事实 | 合并结果显式列出冲突并等待 Controller/Reviewer 裁决，不静默最后写入胜出 |
| 下游选择 | 只选择依赖祖先和显式引用，Reviewer 不读取实现模型隐藏推理 |
| Memory Packet | 记录实际读取的每个版本、hash、token 和顺序；供应商重试复用相同 Packet |
| MinIO 对象 | 键不含敏感文本，JSON/Markdown hash 与数据库登记一致，孤儿对象可识别和清理 |

五层共同的真实基础设施测试未来必须在 MySQL 8.4 执行范围外键、行锁、并发版本、UTC 时间和当前指针恢复；L1～L4 还必须在 MinIO 验证对象上传/读取/删除、数据库回滚后的孤儿对象和对象键脱敏。快速 SQLite 测试只验证契约和普通 Service 行为，不替代这些结论。

### 阶段2必须先行的测试

- Prompt 和 Evaluation 的状态转换、重复执行和并发冲突。
- Context 的 owner scope、关系不匹配、消息数量、token 预算、稳定排序和历史重读。
- 缺少公共认证、缺少内部认证和资源关系不匹配；最终用户级越权测试必须等认证主体确定后才能声称覆盖。
- Prompt 变量 Schema、模板注入文本、并发启用和不可变版本。
- Model Catalog 未知能力、禁用版本、重复版本和 Provider 配置不一致。
- Evaluation 的幂等请求哈希、未知规则拒绝、规则版本、执行失败和敏感证据脱敏。
- `/responses` 回归测试必须证明请求只使用调用方显式输入，不会隐式读取实验性 Memory 或 Knowledge。

其他验证：

- Router/Service/ORM 依赖边界架构测试。
- 阶段2新增或修改表的 Alembic 空库升级、已有数据库向前升级和 `alembic check`；实验性 Memory/Knowledge 表的生产验收留到其正式启用阶段。
- OpenAPI 路由、Schema 示例、错误结构和内部接口双密钥检查。
- Model Gateway 既有 Provider 契约与 SSE 回归测试。

## 阶段2总体完成标准

- Model Gateway 保持四家 Provider、普通响应、流式响应、工具调用、结构化输出、重试、费用和调用证据测试通过。
- Conversation Context 可以在固定 token 预算下对显式指令、安全指令和 Session Message 生成可复现预览，并记录选中与排除证据；它不自动调用模型。
- Prompt Template 具有不可变版本、严格变量合同、并发安全的当前版本切换和可查询变更证据。
- Model Catalog 对 provider/model 能力给出明确支持、不支持或未知结论，并保留实际能力快照版本。
- Evaluation 与 Guardrail 可以脱离 Agent 单独执行；未知规则安全失败，同键异请求返回冲突，执行状态与 verdict 分离，敏感输入不进入公开证据。
- `/responses` 继续只使用调用方显式请求内容，不读取 Memory、不创建 Memory Packet、不读取 Knowledge，也不隐式使用 Context Preview。
- Memory 只保留规划和实验性准备代码，不以现有路由、表或快速测试声称已成为模型运行时能力；它的真实 MySQL/MinIO、形成、删除、Packet 和 Agent 集成留到未来正式启用规划。
- Knowledge 不属于阶段2，现有实验性接口不作为稳定合同，也不进入阶段2或阶段4前端第一版。
- 阶段2范围内能力通过单元测试、数据库集成、鉴权、并发、OpenAPI 和敏感信息测试；未运行验证必须明确记录。
- 当前文档、迁移、Schema、环境配置和运行说明与代码事实一致，不保留相互冲突的阶段状态。
- 以上条件未满足前，阶段2状态保持“进行中”。

当前结论：上述阶段2稳定范围条件已经满足，阶段2状态为“已完成”。后续新增的 Memory、Knowledge、异步任务或前端需求不回退本阶段状态，分别进入其所属规划。

## 官方接口依据

- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)
- [DeepSeek Responses API 指南（仅作设计参考，不实施该端点）](https://api-docs.deepseek.com/zh-cn/guides/responses_api)
- [Anthropic Messages streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Gemini GenerateContent](https://ai.google.dev/api/generate-content)
- [MySQL 8.4 数据类型](https://dev.mysql.com/doc/refman/8.4/en/data-types.html)
- [LangChain/LangGraph Memory 概念](https://docs.langchain.com/oss/python/concepts/memory)
- [LangChain Deep Agents Memory 与后台归并](https://docs.langchain.com/oss/python/deepagents/memory)
- [Mem0 Open Source 概览与默认组件](https://docs.mem0.ai/open-source/overview)
- [Mem0 新 Memory 算法与 Graph 变化](https://docs.mem0.ai/platform/features/graph-memory)
- [Qdrant Fundamentals：过滤与近似检索约束](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)
- [Qdrant Points](https://qdrant.tech/documentation/manage-data/points/)
- [Qdrant Payload](https://qdrant.tech/documentation/concepts/payload/)
- [Qdrant Collections](https://qdrant.tech/documentation/manage-data/collections/)
