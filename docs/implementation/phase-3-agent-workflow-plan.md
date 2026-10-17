# 阶段3：Agent Runtime 与完整工作流节点结果规划

**文档日期：** 2026 年 10 月 17 日
**文档状态：** 已完成（`model_only_v1`同步Agent Runtime、统一Context Builder后端接入、稳定模型思考配置透传、用量一致性门禁、自动缓存诊断、“审核约束回答但不吞掉回答”及版本化公开活动摘要均已实现并通过自动化验证；Composer产品入口属于阶段4）
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

2026-10-17 当前补充：Controller 角色提示与任务额度校验已修复，真实 DeepSeek 的计划修正成功及两轮执行已验证。后续 Strata 样本已完成一次真实计划修正，最终汇总获得平台执行事实，运行中刷新后自动跟进和完成后恢复通过。供应商安全诊断通过离线异常路径测试，历史未知失败不能追溯补回。具体范围见“刷新跟进、执行事实与脱敏诊断修复”；此前失败／未执行记录保留为历史，不代表当前缺陷状态，也不代表全部模型和工具场景通过。

2026-10-12 工具扩展：存在当前回合可访问的附件时，服务端自动选择 `tool_enabled_v1`，不再要求 Web 手动启用。Worker 可多轮调用五类已上传附件只读工具；工具请求、结果、累计用量和进度持久保存，模型继续后才提交节点输出。无附件的默认 `model_only_v1` 和历史请求回放保持原行为。快速回复的独立工具循环也归[工具主计划及本地验证范围](phase-7-tool-runtime-plan.md#首批附件工具实现与运行2026-10-09)，不在本阶段重复维护工具待办；尚未提供独立后台恢复、Python/Shell 或动态工具。

2026 年 9 月 16 日文件接入：上传文件绑定当前 Run 后，各模型节点通过统一 Context Builder 读取同一固定解析结果。测试检查 Controller、Worker、Reviewer 和最终汇总的实际模型请求均含附件文字；不新增 File Agent，不自动切换模式，不开放 Shell 或模型文件工具。这里只是既有 `model_only_v1` 的输入扩展，附件驱动编排与独立后台恢复仍后置。具体上传限制见[阶段2正式文件上传](phase-2-llm-core-capabilities.md#正式文件上传2026-年-9-月-16-日)。
**前端事件依据：** [`phase-4-web-ui-plan.md`](../frontend/phase-4-web-ui-plan.md)

2026 年 9 月 26 日：同会话后续 Run 的模型节点也复用此前附件的固定文字／表格解析结果，来源限定在本轮已保存 User Message 的序号之前。每个节点仍保存自己的 Context 快照；不新增文件 Agent、自动路由或自主工具循环。

> 本文记录阶段3的最终实施事实。`model_only_v1` 已具备同步执行、最多两个无依赖工作 Agent 并行、显式取消、费用预留、实时服务器发送事件（SSE）、持久化事件回放和完整节点查询。工具循环属于阶段7恢复后的新执行配置，跨进程恢复属于阶段10，遥测装配属于阶段6，不再作为阶段3完成条件。

> 2026 年 8 月 19 日路线图重排：本能力由原阶段5调整为阶段3；实现范围和完成状态不变。

> 2026年8月26日新增的统一Context Builder要求不推翻阶段3原完成条件。Agent节点消费统一上下文的后端运行时接入已经完成；Prompt/Artifact产品绑定、对象型Message产品链路和前端证据入口继续由阶段4验收。

> 2026年9月2日完成“公开活动摘要”：在不公开模型隐藏推理的前提下，用已提交节点事实说明当前正在规划、执行、交接、验证、审核或汇总什么。该能力是阶段3公共节点合同的兼容增强，前端呈现由阶段4负责，不改变阶段3原完成判断。

本文统一使用以下口径：

- **当前实现**：以当前 Schema、迁移、Service、Router 和自动化测试为依据；历史验证记录按各自日期保留，不代表本次重新执行过全部环境验证。
- **目标态**：阶段3完整验收或后续 `tool_enabled_v1` 需要增加的行为；目标态字段和接口不得被描述为当前已可用。

## 后续工具能力的统一归属

2026-10-14 新增兼容工作流版本 `1.1.0`：保留原节点职责，新增有限输出修正和 Reviewer 返回 Controller 的第二轮规划。当前规则统一维护在下节；工具输入、原文引用与工具循环由阶段7维护，不重复定义外层节点流转。旧 `1.0.0` 继续单轮执行，原完成记录不变。新版本本地实现与确定性回归已完成；2026-10-16 真实两轮样本通过，2026-10-17 已补验 DeepSeek 和 Strata 计划修正成功分支，见本文件真实联调记录。历史命令与结果见[验收记录](phase-7-tool-runtime-plan.md#工作流整体修正与两轮-refinement2026-10-14)。

2026-10-12 新增工作项，2026-10-13 进行中：节点 JSON 局部修正、Worker 逐条 `claims` 关联原文、Handoff 保留引用、固定文件快照和读取范围检查、Reviewer 原文输入与有限补读均已有本地实现及确定性测试。随后本地真实 DeepSeek + MySQL/MinIO 的两段 TXT 样例通过：Worker 首段引用保存，Reviewer 实际补读最终记录并区分初步/最终型号；服务器未更新，不以单样例代替全部模型质量评估。用户随后确认前端设计，已新增结论与来源面板及只读授权原文接口；最终答案逐句引用仍后置。唯一详细设计和验收维护在[节点结果修正与结论证据传递](phase-7-tool-runtime-plan.md#节点结果修正与结论证据传递)。新工具工作流采用 `worker_evidence.v2` / `agent_handoff.v2`，历史 v1 不补造引用；来源校验通过不等于结论正确。

2026-10-08 起，工具循环、按需发现、代码组合、能力缺项判断、自主创建工具、批准交互和工具任务验收统一维护在[工具主计划](phase-7-tool-runtime-plan.md)。代码继续复用本模块的 Agent、节点、Handoff 和持久活动，不另建 Agent Runtime；本文的原 `model_only_v1` 完成记录保持不变，不复制新的工具待办。

通用后台 Worker/租约机制见阶段10；工具循环如何保存与恢复由工具主计划验收。Controller/Worker 是执行职责，不要求每类工具配一个新 Agent。本文目标态中关于工具的预留字段是兼容背景，不是已实现功能。

## 当前节点职责、版本与修正规则

### 本地实施与委派评估（2026-10-14）

历史实施安排（2026-10-14）：当时真实流式小任务已通过，按批次继续实施；第 1～7 批后来已完成本地确定性测试，真实验收范围见下方 2026-10-16／17 记录。开发分支 `codex/workflow-contract-repair`。当时 Qwen 只修改每批明确授权的代码及测试文件，规划文档由主代理维护；该历史授权不是后续任务自动委派的指令。服务超时或输出截断没有产生代码提交，不属于代码返修失败。

实施顺序：

1. 请求审计：先复现未提供工具定义时丢失 `effective_messages`，再修复并检查私有续接不泄漏。
2. Controller 计划业务修正：先定义重复任务、依赖、审核策略、额度与不可修正错误的用例，再实现一次修正及独立持久记录。
3. 统计和事件：区分计划修正、输出修正、业务返工；业务轮次不能按 Controller 节点数量计算，前端接收新增事件但不增加无用途展示。
4. 组合验收：计划修正加两轮业务返工、刷新／幂等读取、用量汇总和失败状态；真实 Provider 验证与确定性测试分别报告。

评估以实际返回的一批修改为单位：首次审核和必需检查均通过的批数／已完成首次验收的批数为首次通过率；至少一次因代码缺陷要求返修的批数／已完成首次验收的批数为返工率。测试先行的预期失败、计划内的测试到实现两次委派、环境缺失不计返工；另记每批返修次数及具体缺陷。小样本不推断模型整体准确率。Qwen Token 使用仅采用服务返回的 usage；未返回则记“未知”，不把字符数作为 Token 数。主代理没有本轮精确 Token 计量时同样记“未知”，不以账号共享额度差推算本任务消耗。

本次调用记录（时长按服务返回的 `updated-created`，包含排队和请求处理，不能解释为纯模型推理时间）：

| 任务 ID | 内容 | 时长 | 结果 |
|---|---|---|---|
| `b6e8e6fff3434c89aa89f5c21ab9b1b1` | 请求审计回归测试 | 124.6 秒 | 输出截断；无生成结果、无项目写入 |
| `8ba1665ae66f47c0bb3b1b6b39e0ecd1` | 计划业务修正回归测试 | 127.6 秒 | HTTP 524；无项目写入 |
| `ddb09a46252b406586221aa0de6ffde4` | 缩小为单个请求审计用例 | 126.2 秒 | HTTP 524；无项目写入 |

调用交付成功率为 0/3；完成代码审核的批数为 0，代码准确性、首次通过率和代码返工率均无法评估，不写成 0% 准确率或 0% 返工率。针对交付失败缩小范围重试 1 次。服务没有返回 usage，三次调用的 Qwen Token、主代理本轮精确 Token 和费用均未知；失败不代表未计费。当次委派未修改服务配置，未继续付费重试。

后续按用户授权修改本地共享 Qwen 工具（项目仓库外）：`max_output_tokens=null` 不再发送客户端 Token 上限，`request_timeout_seconds=null` 关闭本地 HTTP 超时与响应体读取期限；文件权限、大小和冲突检查保留。38 项离线工具测试通过，尚未真实重试；模型服务默认额度及代理 524 仍不受本地配置控制，因此不能据此将委派交付状态改为通过。关闭超时可能使不响应的请求长期占用 worker，逻辑取消不保证远端停止计费。

随后按用户要求将该共享工具改为 `stream=true`：增量读取 SSE 文本，成功结束且完整 JSON 校验后才交付代码，断流及截断不应用、不自动重试。工具回归扩展为 44 项通过，包含分片中文、心跳、私有推理隔离、缺失结束标记及响应大小限制；尚未发起真实模型请求，不能据此确认代理 524 已消除。详细配置和使用限制由工具安装目录 README 维护，项目工作流代码不因此改变。

真实流式复测：新增 provider usage 白名单计量与 `invocation-metrics.json`（共享工具 45 项离线测试通过）后，调用记录如下。Token 均来自 Provider 的 usage，不由字符推算；输出是否包含内部推理按服务端计数，不能等同于生成代码的 Token。

| 任务 ID | 内容 | 输入／输出／总 Token | 耗时 | 审核与测试 |
|---|---|---|---|---|
| `d165cbd31bac4396b8bf930eef498cdb` | JSON 序列化小任务 | 3219／3657／6876 | 51.099 秒 | SSE 应用成功；根目录 2 项通过，但 apps/api 目录导入失败，要求返修 |
| `249e677bab0a400d9091e5df65067e9a` | 修正包导入路径 | 1002／2355／3357 | 30.507 秒 | 仅改一行；apps/api 下 2 项通过，Ruff 通过 |

小任务累计 10233 Token，交付成功 2/2，首次验收通过 0/1，需返修批次 1/1，返修 1 次；样本不足以推断整体准确性。此后正式请求审计测试任务 `1e0a601485d4421da714a4cba49165e7`、Controller 计划业务修正测试任务 `27d6189f36b94cf9b725bb2d69049cdd` 已委派，等待实际交付及主代理审核；未将测试先行的目标失败计入返工。

正式批次进展：

| 任务 ID | 内容 | 输入／输出／总 Token | 结果 |
|---|---|---|---|
| `1e0a601485d4421da714a4cba49165e7` | 请求审计测试首发 | 未返回 | 本地 2 MB SSE 传输阈值截断；无写入，非代码返修 |
| `27d6189f36b94cf9b725bb2d69049cdd` | 计划测试首发 | 未返回 | 同一 SSE 阈值失败；无写入 |
| `6863f9f7d8b1446db2b4194fb9591161` | 请求审计测试重发 | 11015／8021／19036 | 不存在的模块、错误 Mock 和导入；首次审核失败 |
| `f5e181b871384fd2ac6a4682ade7e5cc` | 请求审计第一次返修 | 16160／7930／24090 | 已复现目标 KeyError，但普通消息样例仍重复且静态检查失败 |
| `00d82dfe536444fb8f33b5b31d8bfb7a` | 请求审计第二次返修及实现补丁 | 1769／10056／11825 | 用例修正，补丁格式和上下文错误、静态检查仍失败；主代理按连续三次规则接管 |
| `2179890174584afeb5c1f98b097d41ce` | 计划测试重发 | 6088／9564／15652 | persistent 样例误变合法、修正次数错误；首次审核失败 |
| `e314311663734df9a8d963e46717f41e` | 计划测试第一次返修 | 4506／9945／14451 | 修复样例，但引用了不存在的单数 source 字段；要求再次修正 |

SSE 工具已将传输包装与生成内容分别计数：传输最多 32 MB，单事件及生成正文仍最多 2 MB，原文件写入限制不变，46 项离线测试通过。此前服务不返回 usage 的失败费用未知，不计为零。以上已知累计 Token 为 95237，不包括之后尚未完成的任务。

请求审计最终实现由主代理移除 `request.tools` 条件、更新函数说明，并格式化 Qwen 测试；移除了本次生成且无法应用的临时补丁（原稿仍保存在工具任务目录）。修复前 2 个目标用例失败、1 个通过；修复后请求审计／快速回复工具／Responses／私有续接测试共 38 项通过（9.92 秒），Ruff 通过。不是 Qwen 独立完成，应单列为主代理接管批次。Controller 实现补丁任务 `a3586bd692f24035a56ef34fb5ad8e64` 和限制／两轮组合测试 `a41127d24162476c927004d2785ceb9b` 已按用户要求取消；旧 worker 一直阻塞，核实进程后终止本地进程，两项状态均为 `cancelled`，`project_modified=false`。没有应用这两个任务的代码。远端停止生成与计费未确认，最终 usage 未取得，不能计为零。

共享 Qwen 工具新增独立 `progress.py`：每两秒保存无正文的流进度，区分等待响应、流连接、推理输出和正文输出；记录字节数、事件数及时间。120 秒无事件、300 秒仅推理、固定窗口重复仅提示疑似异常，不自动取消或重试。52 项离线测试通过（含解析器与状态接口集成）；真实 Provider 进度尚未验证。旧 MCP 服务需重新加载才能返回新增 progress 字段，新 worker 可直接写入进度文件；具体限制由工具 README 维护。

后续委派改为串行小修改，每批完成差异审核与对应测试后才提交下一批，不同时排队大任务：

| 独立修改 | 修改范围与验收 |
|---|---|
| 1. 计划修正资格规则 | 单独纯函数和单元测试：仅允许列出的业务校验错误、最多一次、额度不足与能力越权不得修正；先失败测试再实现 |
| 2. 节点转换允许关系 | 仅 registry 与对应测试：1.1.0 允许 plan_validation 返回 controller_planning，1.0 不变 |
| 3. Controller 单次修正执行 | 仅执行流程相关函数及聚焦测试：保留原节点，同轮新增节点，二次验证通过才 dispatch；只携带必要函数和直接依赖，不返回整份 orchestrator |
| 4. 修正事件 | 事件产生位置和测试：requested、completed、skipped 的节点关联及成功时机真实，不将模型返回合法 JSON 等同于业务验证通过 |
| 5. 后端统计 | schema、统计函数和测试：按实际 attempt 计数，按不同 workflow_round 计轮数；失败、未知与未执行区分 |
| 6. 前端类型与事件消费 | 仅事件白名单、类型和对应测试，保持现有展示，不新增无用途页面内容 |
| 7. 组合验收 | 前六项通过后再编写并执行计划修正加两次 workflow round 的固定样本测试，不提前让模型猜测尚未实现的接口 |

这些是现有工作项内的实施批次，不新增 Phase。第 1～7 批已完成本地确定性测试：资格判断、节点转换、单次 Controller 业务计划修正、专用事件、统计、前端类型兼容及两轮组合测试已接入。2026-10-16 真实 DeepSeek 两轮执行及浏览器刷新通过，但计划修正成功分支未触发，且前两次请求暴露角色规划与任务额度问题，见下方真实联调记录。后续若工具仍仅支持完整文件返回，大文件局部修改应使用准确匹配的独立补丁并先检查，不把整份文件作为默认交付。

真实小任务进度验证（2026-10-14）：三次均观察到 progress.json 从 reasoning_only 到 generating_content，正常结束，无重复窗口警告，未触发 300 秒仅推理提醒。未保存或人工阅读推理原文；只能说明此次固定窗口检测未报警，不能证明没有语义重复。当前长驻 MCP 服务未重新加载，状态接口不返回新增字段，核查直接读取进度文件。

| 任务 | Provider 输入／输出／总 Token | 耗时／首正文等待 | 审核与修正 |
|---|---|---|---|
| `8736f20e5c304196895f4f9d6eb44870` 资格规则测试 | 537／14576／15113 | 210.865 秒／188.799 秒 | 初始导入缺失为预期测试先行失败；遗漏负数预留覆盖、长行是必须返修项 |
| `82771fc2b9804e1181d510565cd7a3bb` 规则实现及测试修正 | 2121／9616／11737 | 143.635 秒／121.916 秒 | 18 项通过，但两处 Ruff 长行且遗漏部分覆盖；再次委派被审批拒绝，主代理补全并格式化，24 项通过 |
| `49cd8fec4ad94eec8553b008ac5074a7` 节点转换 | 2933／9109／12042 | 129.536 秒／95.541 秒 | 主代理先写测试，修复前 2 失败／6 通过；Qwen 业务修改正确但说明超长，主代理缩短说明；联合规则与原有返工回归 40 项通过 |

本次三次真实调用合计 38892 Token。另一次返修委派在工具审批阶段被拒绝，没有新任务或 Provider 调用，不计为 Qwen 交付或用量。资格规则作为独立纯函数已验收，但尚无生产调用方；节点转换仅增加 1.1.0 的合法关系，不自行发起模型调用。规则测试首次不通过审核，第二次仍需修正；节点转换首次行为通过但静态检查失败。主代理接管情况分别记录，不将其算成 Qwen 独立完成，也不将测试先行的预期失败计作返工。

验证命令：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_workflow_definition_registry.py apps/api/tests/test_controller_plan_repair_policy.py apps/api/tests/test_workflow_refinement.py -q`，本地脚本化 Provider／SQLite 条件，40 项通过。新规则、registry 和对应测试的 Ruff 通过；未进行 DeepSeek 业务联调、浏览器检查或部署。下一批应把资格判断接入实际 Controller 执行流程并验证真实剩余资源、节点持久化及事件时机。

委派评估以主 Codex 从任务拆解、审核到测试返工的消耗为重点；Qwen usage 单独列示，不作为 Codex 节省量。当前没有同等任务直接实现的实测对照，也没有精确的本次主代理消耗统计，因此不得声称节省百分比。长时间无交付和反复返工是实际额外成本；小任务首遍通过率、主代理接管次数与审核范围需继续记录。

第三批执行接入已完成本地确定性验收：主代理修正旧测试草稿的单数来源字段，暂不断言尚未实现的专用修正统计字段（该验收保留到第五批），先执行得到 5 失败／4 通过，复现业务修正缺失；另加精确资源边界、免审核和修正超时测试。Qwen 仅接收必要代码摘录并生成待审核补丁，不直接改写大文件。

| 第三批请求 | 用量与进度 | 结果 |
|---|---|---|
| `7936011a38b34258a633a9ed85fab09a` | 约 217 秒仍无正文；最终 usage 未取得 | 用户要求取消，已确认 cancelled；未交付代码，不计返工；远端计费停止未确认 |
| `52109a13ac6c4d739187203ef325a276` | 输入 4084／输出 16035／合计 20119 Token；237.276 秒，约 207 秒首正文 | 首份补丁审核不通过：导入及版本字段错误、剩余资源计算错误、修正证据没有传给实际模型请求、补丁格式错误；未应用 |
| `88ee8b3be21c41ccab4bd7bd1f9aad47` | 输入 6247／输出 15176／合计 21423 Token；222.706 秒，约 192 秒首正文 | 第二次交付仍失败：补丁格式损坏、遗漏转换及完整输入保存；未应用 |
| `0f8a9ed7a3ac4c0587bdeaf1adcfe8e2` | 输入 4078／输出 15954／合计 20032 Token；227.556 秒，约 197.5 秒首正文 | 第三次交付有可用结构，但存在未赋值节点引用、额度字段覆盖和行宽问题；主代理按约定接管 |

以上进度未出现固定窗口重复警告，不代表没有语义重复。三次完成调用合计 61574 Token，另有取消请求的未知用量；连续三次代码交付仍有必须修复问题，返修请求两次，最终由主代理接管。当前不能证明委派节省主代理消耗；主代理承担测试编写、代码摘录、补丁逐项审核、最终修正与验证成本，不能把本批算作 Qwen 独立完成。

第三批最终实现保留 Qwen 第三份代码的单次修正结构，由主代理修正导入、真实剩余额度与可分配给 Worker 的额度分离、节点创建异常处理、转换记录、函数说明及格式。修正前后模型输入、Controller 和校验节点独立保存；二次校验失败不再重试；超时保留 outcome_unknown。workflow_round_count/refinement_count 改按不同 workflow_round 计数，避免同轮修正被误算成业务返工；当时未新增的专用计划修正计数已在下方第五批完成。

验证：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_controller_plan_repair.py apps/api/tests/test_controller_plan_repair_policy.py apps/api/tests/test_workflow_definition_registry.py apps/api/tests/test_workflow_refinement.py apps/api/tests/test_agent_workflows.py -q`，112 项通过（17.85 秒）；其中本批修正执行测试 12 项。相关修改 Ruff、`git diff --check` 通过。环境为本地 SQLite／测试对象存储／脚本化 Provider；没有运行真实 DeepSeek 业务请求、浏览器验收、服务器部署或 Git 提交。

历史修改前回归：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_workflow_refinement.py apps/api/tests/test_response_file_tools.py apps/api/tests/test_provider_continuation_store.py -q`，20 项通过（4.71 秒），仅说明修改前行为。当前 Controller 执行、事件、统计及多轮组合测试已完成本地回归，不再恢复已取消的大任务。

2026-10-14 验收核查：当前相关后端测试合并运行 152 项通过（27.31 秒），共享 Qwen 工具 52 项、前端 Composer 定向测试 5 项通过；相关 Ruff 与差异格式检查通过。无运行中或排队的委派。历史 ready 候选未应用，不等同业务实现。真实 DeepSeek／MySQL／MinIO 下的本批修正联调尚未执行。真实 Qwen 进度文件已验证，长驻 MCP 状态接口的新字段尚未验证。

本节是外层工作流的当前规则定义。下文带日期的验证及旧 JSON 示例保留历史含义，不覆盖本节；尚未实现的行为在本节明确标注。文档更新本身不改变运行代码或部署版本。

2026-10-14 第四批事件验收：先修正文档状态并以 `e6487d4` 提交此前实现，再由主代理直接实现事件，没有新增 Qwen 请求。事件修改已于后续以 `ea3bec1` 提交。新增 `test_controller_plan_repair_events.py`：修改前 6 项失败／2 项通过；实现后另加流式与回放对比，共 9 项通过。测试覆盖成功时机、持续非法、能力越权、调用额度／节点额度不足、无须修正、1.0.0 兼容、超时未知结果，以及重复提交和游标回放不新增事件。

本批验证命令：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_controller_plan_repair_events.py apps/api/tests/test_controller_plan_repair.py apps/api/tests/test_controller_plan_repair_policy.py apps/api/tests/test_workflow_definition_registry.py apps/api/tests/test_workflow_refinement.py apps/api/tests/test_agent_workflows.py -q`，121 项通过（21.37 秒）。相关 Ruff 检查、格式检查及 `git diff --check` 通过；前端 `tsx --test tests/agent-workflow.test.ts` 7 项、`npm run typecheck` 通过。环境为 SQLite／测试存储／脚本化 Provider，不是 DeepSeek／MySQL／MinIO 或浏览器验收，没有部署。

新增流式测试最初误将创建接口的 HTTP 201 写为 200，按既有路由定义修正；随后确认 SQLite 读回 `occurred_at` 缺少实时事件的 UTC `Z` 后缀，测试明确比较 UTC 时间值及其余全部字段。事件时间戳的实时／回放格式仍有此既有差异，本批未更改公共时间序列化。不得将当前通过结果表述为所有响应字节完全一致。

2026-10-15 第五批统计及第七批组合测试（已完成本地确定性验证）：先提交此前改动 `ea3bec1`，再直接实现统计，无新增 Qwen 请求、付费模型请求、迁移或部署。本批新增 `test_plan_repair_statistics.py`：初始 11 项因缺少统计字段失败；实现后补充中间状态和第二轮失败，共 18 项通过。中间状态／取消统计使用合成节点状态与真实保存的事件、Attempt 验证，不代表已执行真实取消传播测试。测试草稿曾误读取未公开的原始输入，已按公开来源节点 ID 识别修正节点，未扩大结果接口暴露内容。

组合用例涵盖首轮修正、第二轮修正、两轮均修正、修正内再发生 JSON 格式修正、第二轮持续非法及第二轮超时。核对两轮节点独立保存、同轮修正不增加业务轮数、实际 Attempt 与 Token／费用汇总、重新读取和重复提交不增加调用；第二轮失败保留首轮成功记录，不伪造最终回答。

验证：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_plan_repair_statistics.py apps/api/tests/test_controller_plan_repair_events.py apps/api/tests/test_controller_plan_repair.py apps/api/tests/test_controller_plan_repair_policy.py apps/api/tests/test_workflow_definition_registry.py apps/api/tests/test_workflow_refinement.py apps/api/tests/test_agent_workflows.py -q`，139 项通过（26.42 秒）；前端 `npm run typecheck` 通过。环境为 SQLite／测试存储／脚本化 Provider；当时未执行真实 Provider 和浏览器验收。统计与组合测试已于后续提交为 `5f63ecc`，服务器未部署。

### 真实两轮与浏览器联调（2026-10-16）

本次先提交 `5f63ecc`，再使用本地 API 18000、Web 3100、独立 MySQL 13306／MinIO 19000，模型为 `deepseek-v4-flash`，登记端点核对为 `https://api.deepseek.com`。凭据只从本地 API `.env` 加载，没有复制到报告；未迁移、推送或部署。浏览器上传合成两行 TXT：旧型号 X100／待复核，批准型号 X200／已批准。输入明确要求分阶段核验，是受控验收样本，不代表普通问题自动产生两轮的概率。

| 请求 | Run / Workflow | 结果及证据 |
|---|---|---|
| 首次 | Run `ab854703-7fd9-4c4b-91fc-9a174e487b3f`；Workflow `ec283bd7-c6aa-46b6-a515-e0d9c358b389` | 失败，1 次模型调用。Controller 将 worker/reviewer/controller 列入 tasks，超出可调度角色，`agent_capability_unavailable`；`plan_repair_skipped_count=1`、修正调用 0，原因 unrepairable_checks。没有绕过权限或将越权变成可修正错误。 |
| 明确角色后 | Run `74c9a495-ee83-475b-9dc2-d3f79de75520`；Workflow `846ca37f-2379-4d27-84c4-b402dda65a8d` | 失败，2 次模型调用。Controller 仅分配 `max_model_calls=1`，读取任务不能完成工具请求与结果生成，`agent_budget_exhausted`；工具实际调用 0。计划通过上限校验，尚未提前识别工具任务的最低调用需求。 |
| 明确任务额度 3 后 | Run `7f9e8682-4036-4f0b-b0ea-e99c19406b83`；Workflow `fee4f760-8cf0-4dd5-8f45-44855d0d5d35` | 完成，18 节点、11 次模型调用、6 次工具调用。首轮 Reviewer 请求重新规划，平台创建第二轮 Controller／Worker／Reviewer；最终回复 X200／已批准，不采用被替代的 X100。 |

共同会话：`bd5fadb1-af33-4f0a-b579-5ccd307a89fd`。成功请求的 `workflow_round_count=2`、`refinement_count=1`，第二轮小计 5 次模型调用／3 次工具调用；`plan_repair_count=0`，没有发生计划业务修正或 JSON 输出修正。此结果验证了两轮业务返工，不是“真实计划修正成功”的证据；后者仍需独立固定样本验收，不盲目反复付费碰撞。

浏览器 Playwright 验证：第二轮执行期间打开前一次失败的运行证据，当前工作流继续并实时显示最终回答，不需刷新；随后刷新会话，最终回答、完成摘要、来源入口恢复。展开批准型号结论，实际加载 Worker 第 2 行原文及 Reviewer 补读原文。浏览器同源结果接口确认刷新前后业务轮数 2、返工 1、模型调用 11、计划修正调用 0，首个失败记录仍为 skipped 1。当前没有统计面板，统计验收通过浏览器实际读取的 API 数据完成，不声称页面显示了不存在的计数器。控制台 0 error／0 warning。

存储核对：上传文件 `458f6b6e-8fb9-42af-a506-91d1ddda56ba` 的 MinIO 原件和解析对象哈希均通过；成功请求的 11 个模型响应对象可读取。三次请求共 14 条 MySQL Attempt，输入 102785、输出 22699 Token，均与结果接口汇总一致；费用字段为 0，仅是当前登记计价结果，不表示真实调用免费。没有另行重跑付费请求。

证据存于本机忽略目录 `output/playwright/`：`workflow-20261016-{workflow_id}.json`、`workflow-20261016-first-failure.png`、`workflow-20261016-history-during-round2.png`、`workflow-20261016-completed-before-refresh.png`、`workflow-20261016-restored-sources.png`。核查脚本 `inspect_workflow_20261016.py` 只读指定测试记录；不是部署脚本或自动验收系统。

待处理：Controller 提示与允许角色的约束表达、工具任务最低模型调用额度的校验、错误文案仍将 tool_enabled_v1 写为 model_only_v1。最终回答包含较多内部字段，用户回答与技术证据的措辞分离也需改进。本次只做验证与记录，不更改上述业务行为，不将三个请求全部记为通过。Office/PDF、真实取消传播、跨用户、服务重启恢复未纳入本次验收。

### Controller 修正与 Strata 真实验证（2026-10-17）

#### 后端执行观察分离（2026-10-17，已完成本地限定验证）

POST SSE 创建成功后，执行任务由进程级 `WorkflowExecutionTasks` 持有，使用独立数据库会话；在返回响应前启动，不等浏览器开始消费响应体。观察连接结束只解除观察，不取消执行。观察队列最多 256 条，慢消费者不阻塞持久执行，遗漏的事件通过既有序号与 GET 回放读取。任务结束释放进程引用并消费异常；日志仅记录 Workflow ID，不打印任意异常正文。

应用正常关闭时先等待执行最多 30 秒，再取消剩余任务并等待既有取消／未知结果保存，随后关闭 HTTP 客户端和数据库。用户显式 `/cancel` 仍保存取消事实并阻止后续节点；不能把供应商已接收的调用视为已撤销或免费。同 key 重放保持只读，不创建第二个执行任务。

本次实现针对浏览器观察连接断开，不是 RabbitMQ、跨进程领取或服务崩溃后续跑。非流式同步 JSON 创建仍遵循原请求生命周期；跨进程恢复继续由阶段10实施，不能将进程内任务表当作持久队列。测试覆盖 ASGI `http.disconnect` 后真实 SQLite 执行完成、显式取消、相同请求重放、响应体未消费及关闭时清理；真实浏览器／公网服务本次未执行。

验证命令：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -q -rs`，最终 **856 passed，32 skipped，107.52 秒**。允许本地 Unix socket 后执行；未启用真实供应商调用开关。32 项跳过需要显式隔离 MySQL、MinIO 或 Docker 解析／工具镜像及样本，不计通过。定向生命周期 6 项通过，包含关闭时 `outcome_unknown` 及 `outcome_is_known=false`；协议失败测试包含增量／收集式 Strata 的错误、EOF 和网络失败，不请求真实 Strata。API 测试核对公开错误、失败 Attempt、私有安全诊断和累计用量不重复求和。Ruff 定向检查和 `git diff --check` 通过。

修复前 8 项新协议测试失败；已知用量保存测试最初读到 null。期间一次全量检查的 10 项失败均为沙箱禁止本地 Unix socket，授权后相关 24 项通过；关闭测试初稿曾错误期待 failed 和不存在的 code 字段，按现有 `outcome_unknown/error_code` 合同修正后通过，未修改实现来掩盖结果不确定性。全量测试产生的两份历史安全报告随机 ID 差异已清理，不覆盖原审计记录。修复、测试和本节记录随本次提交保存；未推送、未部署。

#### 刷新跟进、执行事实与脱敏诊断修复（2026-10-17）

状态：本次三项修改已完成限定环境验证，修复、测试和本节记录随同一提交保存。上一批记录为 `6b082e5`，下方原始失败记录保留为历史；本次没有调用 DeepSeek、推送或部署。

- 前端恢复非终态 Workflow 后串行读取结果和增量事件，正常间隔 1.5 秒，错误退避最多 30 秒，单次读取最长 15 秒。终态停止；会话切换／卸载取消读取，不取消服务端执行、不重复创建任务。权限错误停止读取。历史证据抽屉仍独立于当前运行。
- 最终汇总输入新增 `execution_facts`，从已保存节点和事件计算 `loop_statistics`，范围为最终汇总开始之前。指令明确平台统计才是执行次数依据，用户要求修正、上游模型声称修正都不能代替实际事件；普通答案不复述内部过程。节点职责、严格 JSON 校验保持不变。零次／一次真实记录与上游虚构“17 次”相冲突的测试均通过。此处是给模型提供可核对事实及约束，不是所有自然语言断言的确定性真实性验证器。
- HTTP 错误与 Chat 错误对象共用安全诊断：允许的类别和参数、状态码、消息长度、消息摘要哈希。任意消息、未知字段及请求回显不持久化、不发送浏览器；未知类别明确记录 unknown。旧记录没有的信息无法追溯补回。

真实环境：本地 API 18000／Web 3100、MySQL 13306／MinIO 19000，Strata 模型 `tiel-coder-35b-a3b-ud-q4_k_xl`，使用原供应商默认参数。唯一新增付费样本由既有脚本创建受控重复任务键请求：

```bash
apps/api/.venv/bin/python deployments/verification/check_live_strata_plan_repair.py --mode strata-repair --output output/playwright/strata-recovery-fix-20261017.json
```

Session `8889361d-ae80-47da-b478-c093b6e94f48`；Run `35ea0ec4-e0d3-46df-a268-fb498e56906d`；Workflow `15424c3a-514a-4b9b-9ffc-f5edda9a9ca5`。

| 调用 | 输入 Token | 输出 Token | Attempt 耗时（秒） |
|---|---:|---:|---:|
| 首次 Controller | 1454 | 5320 | 74.248 |
| 计划修正 | 1899 | 632 | 12.675 |
| Worker 1 | 987 | 6663 | 88.596 |
| Worker 2 | 984 | 1351 | 21.488 |
| Reviewer | 2310 | 5759 | 82.967 |
| 最终汇总 | 6138 | 613 | 15.989 |
| 合计 | 13772 | 20338 | 295.963 |

请求至完整结果 297.024 秒；服务端工作流 297.335 秒（测量起止点不同）。共 34110 Token、6 次模型调用、15 个节点，1 业务轮。首次 `unique_task_keys` 校验失败；计划修正 requested/count/succeeded 各 1，failed 为 0；JSON 输出修正和业务返工均 0。数据库 Attempt、结果汇总及六个 MinIO 响应对象核对通过。没有登记价格，金额未知，聚合费用 0 不能解释为免费。快速回复本次没有追加调用，历史结果不混入本次用量。

Playwright 在运行中打开并刷新会话，观察节点继续推进；最终前端代码加载后的刷新时间为 14:02:22 UTC，服务端 14:02:44 完成后页面自动显示最终答案，无需第二次刷新。DOM 正文与结果接口 `final_output.final_text` 一致；随后完成态刷新再次核对相同正文、6 次调用和 1 次成功修正。最终答案解释列表与元组，没有声称未发生的修正过程。此样本不证明模型所有业务内容准确：Reviewer 仍指出第一个 Worker 的任务目标和内容不一致，最终输出保留该警告。

证据：`output/playwright/strata-recovery-fix-20261017.json`、`strata-fixed-final-code-refresh.yml`、`strata-fixed-final-check.yml`、`strata-fixed-restored.yml`、`strata-fixed-auto-completed.png`。它们是本机忽略目录中的验收产物，不含密钥。截图已查看，最终内容和完成摘要可见；同源只读核对未增加模型调用。

自动检查（当前改动）：

- `apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml packages/models/tests apps/api/tests/test_responses.py apps/api/tests/test_plan_repair_statistics.py apps/api/tests/test_agent_workflows.py -q`：231 通过。包括非法 JSON 既有行为、HTTP／流错误脱敏、私有诊断存储和公开响应分离。
- `cd apps/web && npm run test`：141 通过；`npm run typecheck`、`npm run lint` 通过。
- `cd apps/web && PLAYWRIGHT_BASE_URL=http://127.0.0.1:3100 npm run test:e2e -- --grep 'refreshing a running workflow'`：桌面和移动端 2 项通过，模拟后端推进，断言不重新 POST 执行。
- Ruff 定向检查和 `git diff --check` 通过。修复前已记录缺少恢复模块、缺少最终执行事实和 HTTP 错误缺少安全诊断的失败测试；旧历史消息测试同步新增实际执行事实输入，未删除历史隔离断言。

限制：真实工作流未再次触发供应商错误，脱敏异常路径由离线协议／API 测试验证；共享 HTTP 错误重构在此运行启动后完成，不声称此次成功样本运行了新 HTTP 错误路径。未重验附件、工具、跨进程恢复、全厂商矩阵或生产环境。下方失败记录为修复前事实，不作为当前缺陷状态。

#### Strata 完整流程与刷新续验（提交 8ad539e 后）

本节为修复前的历史记录；当前状态以上一节修复验收为准，下面的待修复描述不再作为当前待办。

状态：进行中。先提交共享 JSON 指令修复及既有验证文档为 `8ad539e`，然后在本地
API 18000、Web 3100、MySQL 13306、MinIO 19000 真实调用 Strata；未推送、未部署。
模型均为 `tiel-coder-35b-a3b-ud-q4_k_xl`，保留供应商默认思考行为，无服务端模型参数修改。
以下是本次新增调用，不与此前小型 JSON 验证合并统计。

| 样本 | 结果 | 耗时 | 输入 / 输出 Token |
|---|---|---:|---:|
| 浏览器快速回复 | 最终回答已保存，完成后刷新仍可见 | 模型调用 4.491 秒 | 237 / 145 |
| 浏览器普通 Agent | Controller 收到供应商错误对象，未生成最终回答 | Run 起止时间差约 10 秒（数据库秒级精度） | 未返回，不能记为 0 |
| API 受控重复任务键样本，浏览器观察 | 13 节点、5 次调用完成，最终回答保存；计划修正目标分支未触发 | 请求至完整结果 179.154 秒；服务端工作流 178.770 秒 | 11687 / 12242 |

成功 Agent 的逐次调用：Controller 1454/1983、Worker 987/4560、Worker 984/1280、
Reviewer 2448/3141、最终汇总 5814/1278。总计 23929 Token，均已保存对应 MinIO 响应。
本批已知总量输入 11924、输出 12387、合计 24311 Token，另有一次失败用量未知。
所有 Attempt 均没有价格依据；界面费用 0 不能解释为免费。

验证结论分开记录：

- **最终输出与保存通过**：受控样本 Run `17660868-078c-4d6d-80a6-587e6207f343`，
  Workflow `b1b6a336-073d-4814-a0cd-107781fa72b0`，Assistant
  `45e414de-5657-49ca-a6d9-bd229dfe61fd`。完成后浏览器刷新可见回答；只读复查确认
  Workflow ID、Assistant ID、正文、修正统计及五个 Attempt 均与完成时一致。
- **有限修正真实验收失败（目标分支未触发）**：模型首次已生成 `compare_list` / `compare_tuple`，
  没有提交重复键。计划修正、输出修正及业务返工均为 0，业务轮次为 1。
  脚本的修正成功断言正确失败，不能将工作流 `completed` 当成该断言通过。
- **回答内容准确性未通过**：首次 Controller 的摘要及最终答案声称“先被平台拒绝，再经过 plan_repair”，
  但数据库事件没有这段过程。最终汇总沿用了模型描述，不能用模型自述验证平台事件。
  后续应将平台执行事实与模型描述分开提供、核对；本轮只记录问题，未改业务代码。
- **运行中刷新后自动跟进失败**：13:36:07 UTC 刷新后，页面停留在 Worker 执行中；
  后端已于约 13:37:56 完成，13:38:13 的页面仍显示旧进度。13:38:39 再次刷新才恢复最终答案。
  代码核对：`apps/web/app/page.tsx` 的 `restoreAgentWorkflowForRun()` 只执行一次事件回放和
  一次 result 查询；`GET /agent-workflows/{id}/events` 明确只返回已有事件，不持续订阅。
  因此后端未被此次刷新截断，但前端缺少恢复后的持续读取。阶段4需补充非终态查询／事件续读，
  验收应覆盖“不再次刷新也显示最终结果”，不能仅验证已完成会话的恢复。
- **普通 Agent 首次失败原因仍不完整**：Run `6ef6b295-c522-4eeb-ba52-edff074c0670`，
  `provider_unavailable / Chat provider returned an error envelope.`；持久诊断只保存
  `error_envelope=true`，没有供应商具体错误内容，无法进一步判定原因。失败状态可从会话恢复。
  后续受控样本使用仅本地进程的脱敏错误观察，但未再次触发错误，不能据此声称根因已修复。

复现入口：`deployments/verification/check_live_strata_plan_repair.py --mode strata-repair --output NEW_REPORT.json`；
`--mode inspect --run-id RUN_ID --output NEW_REPORT.json` 只读本地记录，不调用模型。
脚本原 `repair` 仍使用 DeepSeek，不能混用。浏览器快速回复及普通 Agent 使用真实界面发送，
受控 Agent 由 API 创建，浏览器在运行中打开、刷新并观察；未伪造模型返回或修改校验结果。

证据位于 `output/playwright/`：`strata-quick-ui-20261017.json`、`strata-agent-ui-20261017.json`、
`strata-plan-repair-20261017.json`、`strata-agent-restored-20261017.json`；
`strata-quick-after-refresh.yml`、`strata-repair-after-running-refresh.yml`、
`strata-agent-final-before-refresh.yml`、`strata-agent-final-after-refresh.yml` 及同名截图。
这些本地生成证据不包含密钥，不作为部署数据。

本轮执行 `apps/api/.venv/bin/pytest -c apps/api/pyproject.toml
apps/api/tests/test_controller_plan_repair.py apps/api/tests/test_controller_plan_repair_events.py
apps/api/tests/test_plan_repair_statistics.py -q`：**41 passed**。
这是确定性修正、事件、统计和两轮组合回归，不代替 Strata 未触发的真实修正分支。

后续状态补充：共享 JSON 格式引导修复后，获新授权的 Strata 小型 JSON 请求在增量流式和
收集式调用均通过；这不是完整 Agent 工作流，本节历史 Strata 工作流失败记录不变。
单变量对照、调用用量与验证范围统一见[阶段2 JSON 对照](phase-2-llm-core-capabilities.md#json-请求单变量对照与共享修复2026-10-17-后续授权)。

本日续验（仅 DeepSeek，不调用 Strata、不部署）：使用本地 MySQL/MinIO 与 API，执行
`check_live_strata_plan_repair.py --mode repair --output output/playwright/deepseek-bounded-repair-20261017.json`。
该脚本仅 `repair` 分支选择 DeepSeek，本次未执行 seed/quick/agent 分支。
Run `826b3a7b-1024-4e84-928b-d32c6c0a5c59` 完成，51.194秒，12次实际调用，
输入51568／输出9225 Token（合计60793）；12份响应对象均从 MinIO 读取成功。

- 受控首次计划重复 task_key，`unique_task_keys` 未通过；请求计划修正1次、成功1次、失败0次。
  修正调用输入1823／输出296 Token，已经包含在总量中。
- Reviewer 第一次输出含 Schema 不允许的额外字段（`extra_forbidden`），触发节点输出修正1次并成功；
  修正调用输入3850／输出911 Token，同样包含在总量中。不是解析器拼接生成的修正结果。
- 业务执行两轮、返工1次；第二轮再次要求返工时以 `round_limit` 停止继续循环，并生成带限制说明的最终回答，
  Assistant ID `53a93bbf-e5d0-4c62-824e-05fef3ab5c64`。
- 最终正文正确说明列表/元组，但也声明上游解释未充分交付、模型无法确认内部计划修正经过。
  因此通过的是“实际计划修正、字段修正、两轮上限和最终答案保存”，不是上游内容质量全部通过；
  不用最后一段模型自述代替真实平台事件。
- 本地没有配置 DeepSeek 价格，Attempt estimated_cost 为 null；现有工作流统计中费用0是已知费用求和，
  本次不能用它推导真实调用免费。费用未知的表达后续需单独完善；非法 JSON 的失败记账规则见阶段2。

先提交此前五份验证文档为 `6cedce7`。本批代码在该提交之后修改，未推送、未部署。
使用本地 API 18000／Web 3100、MySQL 13306／MinIO 19000，保留历史数据；Strata Key 只写入
被 Git 忽略的 `apps/api/.env`，没有进入数据库、报告或前端。

当前规则：Controller 的 tasks 只允许 `allowed_roles` 中的 planner/researcher，不把
controller、worker、reviewer、handoff 或最终汇总当作额外调度角色。纯文本模式每任务
`max_model_calls` 必须为 1；工具模式声明 read/tool 的任务至少为 2（工具请求、结果生成），
先发现文件再读取至少需要 3，更多顺序读取仍需要额外额度。最低值不是任务完成保证。
数值不符进入 `workflow_limits`，1.1.0 可在现有剩余额度规则内修正一次；角色未授权或
能力越权仍不修正。错误文案使用真实 execution_profile。测试先复现单次工具任务被误接受、
纯文本数值错误被误分类为工具权限错误，再修复；不取消上限、权限或二次校验。

DeepSeek 三次真实请求（模型均 `deepseek-v4-flash`）：

| 样本 / Run | 耗时 | 输入 / 输出 Token | 结果 |
|---|---:|---:|---|
| 自然语言重复键指令 `2651a724-381b-471f-b227-aea456b0102d` | 209.943 秒 | 38543 / 43277 | 工作流完成，但模型初次给出合法键，修正 0；目标分支验收失败 |
| 明确 JSON 样本 `39044362-3b5b-4b7c-b007-924eb7e02c50` | 3.513 秒 | 3071 / 482 | 触发修正，修正计划把文本任务额度改为 2，二次校验失败；促成本批提示与数值错误分类修复 |
| 修复后同一明确样本 `38c32957-50b8-4dfb-8bbc-7b10c75e4a8f` | 39.850 秒 | 38375 / 7441 | 完成：计划修正 1、成功 1、失败 0；业务轮数 2、返工 1、实际调用 10 |

后两次仅对该验收请求设置 reasoning.enabled=false，不改变产品中等思考默认设置。
成功 Workflow `7db5f265-189f-4aec-955f-56baebf7197d`：三份计划校验依次为
false（unique_task_keys）、true、true。计划修正用量输入 1823／输出 298，已包含在总量，
不能再次相加。独立 GET result 复读确认统计和 Assistant ID
`2d78b26b-652c-4af4-acc1-036176680ff8` 保留，10 个响应对象从 MinIO 读取成功。
这是受控故障输入，不代表自然请求的触发概率；没有伪造模型输出或修改校验结果。
最终回答解释了列表／元组，但因模型未获得完整内部修正记录，仍称测试过程证据不足。
本次以真实节点、事件和 Attempt 判定修正成功，不将模型对测试本身的描述计为通过。

Strata 地址 `https://mithfusion.site/v1`，实际模型目录确认
`tiel-coder-35b-a3b-ud-q4_k_xl` 和 `meta.n_ctx=163840`。独立 `strata` 标识复用
Chat Completions，密钥和白名单走环境配置，地址走数据库。Strata 暂不支持思考参数映射，
空配置使用服务默认，Web 不发送虚构 effort；完整 JSON 仍由节点验证，不放松 schema。

同一问题“用两句话解释 Python 列表与元组的区别”的实际比较：

| 模式 / Run | 耗时 | 已知输入 / 输出 Token | 结果 |
|---|---:|---:|---|
| 快速回复 SSE `8b1facf0-9720-4e15-bf26-24332027c740` | 4.405 秒，首正文 3.344 秒 | 237 / 105，完整用量 | 通过 |
| Agent 非流式 `4d9256a2-2404-46b1-b2b5-baddbcaf2b28` | 10.546 秒 | 未知 | Controller HTTP 502 |
| Agent 非流式复测 `0c92d5a5-77d2-4bec-8b68-1033fc4ec5c5` | 8.585 秒 | 未知 | Controller HTTP 502 |
| Agent 改为 SSE `2a122592-9ae8-4814-9ad1-c4490038d934` | 67.233 秒 | 至少 3589 / 1941，非完整总量 | 5 次调用；Controller、Worker 完成，Reviewer 修正响应形状错误，最终失败 |

SSE Agent 中两次调用的本地聚合记录没有正文、工具或 usage，另外三次返回上述已知用量。
2026-10-17 复查更正：聚合记录不是原始 SSE，且旧共享解析器会忽略异常并默认 stop，
因此不能据此认定上游返回空正文。共享解析与真实 DeepSeek 复查结果见
[阶段2模型接口复查](phase-2-llm-core-capabilities.md#模型接口复查与失败语义2026-10-17)。
Reviewer 最后返回 `output_repair` 包装字段而非 Reviewer 输出字段。随后新增空流拒绝测试和
适配器检查，避免再将空流记为成功；该检查完成离线验证，没有再次发起付费工作流。
本次进一步要求修正只返回节点对象，明确禁止把修正输入包装当输出；空结果在字段修正前失败。
工作流内部调用现将非法 JSON 的结构校验延后到节点：先保存正常结束的生成及用量，
再进入既有有限修正；合法 JSON 的字段错误与非法 JSON 均不能绕过节点校验。
普通 HTTP 调用仍严格解析，拒绝、空响应和断流不进入字段修正。
不能仅凭这些结果断言 502 根因，也不能宣称 Strata 完整 Agent 可用。最小 JSON-object
诊断请求另耗时 3.115 秒、输入 265／输出 57，不计入两种模式的同题比较。
旧失败报告中 0 或小计须结合 usage_complete=false 解读，未知不等于零；脚本已改为
用量不完整时总量 null、已知部分单列。没有价格依据，不计算货币费用。

验证入口 `deployments/verification/check_live_strata_plan_repair.py`；证据在忽略目录
`output/playwright/strata-*-20261017.json`、`deepseek-plan-repair*-20261017.json`。
Playwright 已检查 Strata 可选、选择后不显示无效思考等级，截图
`output/playwright/strata-picker-20261017.png`。没有新增统计面板或在浏览器再次发送付费请求。
本批未验收 Office/PDF、Strata 工具调用、视觉、后台进程恢复或生产部署。

最终自动化验证：后端／模型适配 253 项通过（33.66 秒），命令如下；包括新增额度规则、
修正失败后不 dispatch、Strata 注册条件、SSE 收集、缺少 DONE／空流拒绝和思考参数拒绝。

```bash
apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_strata_provider.py apps/api/tests/test_controller_tool_plan_policy.py apps/api/tests/test_plan_repair_statistics.py apps/api/tests/test_controller_plan_repair_events.py apps/api/tests/test_controller_plan_repair.py apps/api/tests/test_controller_plan_repair_policy.py apps/api/tests/test_workflow_definition_registry.py apps/api/tests/test_workflow_refinement.py apps/api/tests/test_agent_workflows.py apps/api/tests/test_model_reasoning_policy.py apps/api/tests/test_provider_endpoint_configuration.py apps/api/tests/test_prompt_catalog.py packages/models/tests -q
```

`apps/web` 下 `npm run typecheck` 通过；`npx tsx --test tests/reasoning-settings.test.ts tests/agent-workflow.test.ts tests/model-directory-view.test.ts` 14 项通过。相关 Python Ruff 和 `git diff --check` 通过。自动化结果不替代上表的真实 Provider 失败记录。

### 节点职责

| 节点 | 输入与职责 | 输出及后续处理 |
|---|---|---|
| 请求归一化／来源清单 | 程序固定用户目标、消息锚点、允许来源和执行配置；`context_assembly` 的 `assembly_scope=run_source_inventory`，不是每个角色的完整模型请求 | 保存来源清单；早于最近 100 条候选的消息明确记录排除及截断，各模型调用再构建实际输入 |
| Controller 规划 | 模型读取目标、可用能力、实际剩余额度；返工时读取上轮结论、审核及授权原文 | 提交类型化任务计划，不自行赋予工具权限或写执行状态 |
| 计划校验／分派 | 程序检查任务键、依赖、角色、能力、审核策略和剩余资源 | 通过后创建 Task／Agent Run；不替模型编写业务计划 |
| Worker | 模型按任务分析；工具配置下可以连续读取授权附件 | 提交业务结果；文件结论携带逐条 claims 与来源，推测和未解决事项单列 |
| Handoff | 程序从已提交 Worker 输出生成不可变交接 | 保留完整结构化结果、引用、任务与调用关联；不让模型重新生成平台事实，不压掉原文引用 |
| 确定性验证 | 程序检查字段、记录归属、文件快照、实际读取块及范围 | 只能证明结构和来源有效，不能证明自然语言结论正确 |
| Reviewer | 模型对照结论与授权原文独立审核，工具配置下可有限补读 | 提交既有审核 JSON／逐条 assessment；不覆盖 Worker，不把多个工具结果的 ID 直接拼接成新引用 |
| Controller 最终汇总／完成 | 模型依据候选、审核和未执行返工原因生成回答；程序保存最终消息与完成事实 | 明确结论限制，不将审核拒绝等同于不回答；真实执行失败仍保存失败状态，不伪造成功回答 |

### 版本与输出结构

| 维度 | 当前支持与兼容规则 |
|---|---|
| 工作流名称／版本 | 名称保留 `model_only`；新请求默认 `1.1.0`，显式 `1.0.0` 仍可调用和读取，保留其请求哈希和单轮规则 |
| 执行配置 | `model_only_v1` 不执行工具；`tool_enabled_v1` 允许授权的 Worker 与 Reviewer 附件只读循环。快速回复保留独立 Responses 循环，不因有附件自动变为 Agent |
| 节点信封 | 继续使用 `agent_workflow_node_result.v1`，与业务输出结构版本分开；工作流版本不等于所有节点 Schema 的版本 |
| Worker／Handoff | 纯模型配置保留旧结构；当前新工具执行采用 `worker_evidence.v2`／`agent_handoff.v2`。历史已保存 v1 只读兼容，不补造 claims。不能仅按工作流版本猜输出结构，应读取已保存的 Schema 标识 |
| Reviewer | 工具执行要求逐条 `claim_assessments`，支持／矛盾结论需真实来源；纯模型及历史结果按其原结构读取。保留 Reviewer 原职责，不新增替 Worker 改写的角色 |
| 工具轮／输出修正／工作流轮次 | 是三种行为：同一节点工具轮追加 Attempt 与 Tool Call；`1.1.0` 节点输出最多修正一次；工作流 `max_workflow_rounds` 为 1～2，默认 2，包含首轮。`1.0.0` 工具配置保留旧的有限 JSON／字段修正，但不修正引用语义及 `value_error`，纯模型配置不修正，两个配置均不进入第二轮 |

`1.1.0` 只有有效 Reviewer 结果明确设置 `requires_replan` 或 `requires_retry`，且剩余资源允许时，才返回 Controller；`needs_revision` 单独出现不触发第二轮。新轮创建新的节点、Task、Agent Run、Handoff 和 Evaluation，不覆盖首轮。第二轮 Reviewer 按第二轮 Task 逐条审核，不能仅凭相同 `claim_key` 混合两轮；可以读取授权的上游证据。

轮次耗尽或资源不够时保存 `refinement.skipped` 和具体原因，进入带限制的最终汇总。已授权原文在每次使用前重新检查访问权限。模型调用、节点、总时间和费用不因修正／返工重置；计划校验扣除已消耗调用，并预留最终汇总及审核调用。工具 Reviewer 最多三次模型调用（最多两轮带工具，最后一轮提交 JSON），额外输出修正仍单独记账。

### 错误处理与尚未实现的修正

| 条件 | 当前行为 | 规划及限制 |
|---|---|---|
| JSON／字段错误；已授权来源的错误块范围、重复块或审核缺项 | `1.1.0` 原节点有限修正一次，保留工具历史与私有续接；修正不再次执行工具，之后重新完整校验 | 已实现本地确定性回归；持续错误仍失败，不用程序替模型编造合法业务输出 |
| JSON 合法但计划依赖、重复任务、策略或额度分配不合法 | 1.1.0 在资格及资源检查通过后，返回 Controller 修正一次并重新校验；1.0.0 仍直接失败 | 执行已通过本地确定性验收。原计划、校验错误、允许能力与真实剩余额度传入 Controller；创建独立节点，不覆盖被拒绝计划，计入总预算。能力越权、额度不足或修正仍非法时失败；专用事件及统计已实现 |
| 有效审核要求重新规划／重试 | `1.1.0` 按上文条件最多进入第二轮；`1.0.0` 交给最终汇总说明限制 | 业务返工不是格式修正；Reviewer 不负责执行新任务 |
| 跨用户／跨任务非法引用、访问撤销、错误文件快照或对象损坏 | 终止相应执行，不作为格式错误重试 | 不通过修改引用或扩大权限绕过；权限类错误不能进入计划修正 |
| Provider 是否已执行或结果是否已保存无法确认 | `outcome_unknown`，不自动重复调用 | 不将未知结果当作可修正 JSON；后台恢复规则由阶段10定义 |
| 取消、资源耗尽、保存失败 | 保存真实节点及工作流结果；仅在符合现有分支时进入有限制的最终汇总 | 不保证任何失败都能生成模型答案；阶段4从已保存状态恢复失败呈现，不能伪造 Assistant 成功消息 |

计划业务修正已用专门测试验证：重复任务、自依赖、策略不符、超额计划被纠正；持续非法、无剩余额度、能力越权不继续重试；计划校验及实际调用分别保存，精确资源边界、免审核、修正超时和结果重新读取通过。专用统计与两轮组合测试已通过，不能把这些确定性结果当作真实模型完成率。

### 流转统计与验收

- 当前 `result.loop_statistics` 从既有 Node、Event、Attempt 恢复：区分修正请求、实际调用、成功／失败／未结束／未知结果以及未执行返工原因。刷新、幂等重放不增加统计。
- `agent.node.output_repair.requested/completed` 关联原 Attempt、错误字段和修正 Attempt；`agent.workflow.refinement.requested/skipped` 关联来源审核、Evaluation、前后轮次及停止原因。重复节点键使用递增 `node_attempt`，`input.workflow_round` 单独记录业务轮次；模型消息不能自行声明这些平台统计事实。
- 工具轮、节点修正、工作流返工分别计数。实际工作流轮次数按已创建 Controller 节点的不同 `input.workflow_round` 统计，同轮计划修正不增加轮数；包含已开始但未完成的轮次，不等于成功轮数。
- 修正用量按对应 Attempt 小计；refinement 用量是第二轮规划到审核的小计，不包含最终汇总。第二轮的修正会同时属于这两个小计，不能相加当总量；总量按全部 Attempt 只汇总一次。
- 计划业务修正已保存原计划、校验节点、修正输入和 Attempt。`agent.controller.plan_repair.requested` 在修正节点创建后、模型调用前保存；`completed` 仅在新计划通过第二次确定性校验后保存；`skipped` 表示本次不修正或已修正失败后不再追加修正，必须结合 `stop_reason` 和修正节点 ID 区分。超时等未知结果只有 requested 和节点失败事实，不伪造 completed。
- 三类事件关联业务轮次、原 Controller、原校验、修正节点和失败检查编号；二次校验后还关联新校验节点和模型 Attempt。公共事件不携带原始计划或提示词。事件提交后通知 SSE，回放与重复提交不新增事件；1.0.0 不增加这些事件。
- `loop_statistics.plan_repair_requested_count` 按请求修正的节点计数；`plan_repair_count` 按该节点实际保存的逻辑模型 Attempt 计数，包括节点内的 JSON 格式修正，不是 HTTP 重试次数。`plan_repair_attempt_ids` 提供对应 ID，`plan_repair_usage` 累加已记录用量，未知 Token／费用不是已证实的免费调用。
- `plan_repair_succeeded_count` 仅来自二次校验通过的完成事件；`failed_count`、`cancelled_count`、`unknown_count`、`pending_count`（均带 `plan_repair_` 前缀）按尚无成功事件的修正节点和工作流状态区分。模型节点完成但校验尚未完成仍计 pending；后续其他节点失败不改写已有计划修正成功。
- `plan_repair_skipped_count` 仅统计没有创建修正节点的未执行事件；修正后仍非法导致停止追加修正计 failed，不再重复计 skipped。旧 1.0.0 或无计划修正返回零值与空列表，不新增数据库表或迁移。
- 计划修正用量与格式修正、第二轮返工小计可能重叠，不能相加。工作流总量仍按所有实际 Attempt 汇总一次；同轮计划修正不增加业务轮数，重复读取不写入统计。
- 新版本地确定性回归已完成；2026-10-16 真实 DeepSeek／MySQL／MinIO 两轮、同会话失败后续聊及浏览器恢复通过一个受控样本，返回 Controller 和两轮记录真实保存。2026-10-17 已另行验证 DeepSeek 和 Strata 的计划业务修正成功，不能将业务返工计为计划修正成功；其他真实输入稳定性与成功回答后的新附件续聊仍需另验。跨进程重启恢复属于阶段10，当前不承诺。

## 目标

- 要实现的结果：使用普通异步 Python 编排 Controller、工作 Agent、确定性验证、独立审核和最终汇总，不以 LangGraph Graph API 作为平台核心。
- 要实现的结果：每一个工作流节点都返回版本化、类型明确、字段完整、可持久化、可查询的 `AgentWorkflowNodeResult`，而不是只返回一个自由格式字符串。
- 要实现的结果：同步请求和结果查询返回整个工作流及全部节点结果；SSE 只发送同一持久化事实的有界公共投影、节点 ID 和详情路径，前端通过查询接口读取完整节点合同，不从事件文案猜测状态。
- 要实现的结果：模型调用、Agent Run、Agent Turn、Task、Handoff、Evaluation、Artifact 和工作流节点之间具有稳定引用关系；费用、token、错误和证据可聚合但不重复保存原始事实。
- 要实现的结果：循环、节点数、并发、token、费用、时长、上下文和输出均有硬上限；取消、失败和未知结果具有确定状态。
- 明确不处理：RabbitMQ、后台 Worker、服务重启自动恢复、无人确认的长期后台任务、自动发布代码、Memory Packet、L1～L4 自动注入和 Knowledge。
- 明确不处理：在节点结果中保存模型隐藏推理、完整思维链、完整系统提示词、供应商密钥、未脱敏原始响应或无限制工具输出。

## 当前事实

- 阶段2 Model Gateway、Context Builder、Prompt/Model Catalog 和 Evaluation 可以独立调用。当前工作流已复用 Model Gateway 的 `ModelInvocationService`、Model Attempt 与费用事实；所有有Session锚点的模型节点均调用Context Builder，已提交Handoff、确定性验证、独立审核和Evaluation通过类型化引用进入后续节点。工作流当前没有配置Prompt Release绑定，仍使用版本化代码指令。
- `llm_runs`、`llm_tasks`、`llm_attempts`、`llm_evaluations` 和 `llm_artifacts` 已保存主要业务事实。
- Memory 实验性代码已有 `llm_agent_runs`、`llm_agent_turns`、L0 Working State、L2 Handoff 和 Run Snapshot。阶段3已新增实际 Agent 执行协调器、工作流/节点/事件持久化表和统一节点结果合同；Memory Packet 与 Knowledge 仍不参与工作流上下文组装。
- `AgentRun` 表示一个角色对一个 Task 的完整执行；`AgentTurn` 只表示其中一次模型或工具循环。Controller 规划、计划校验、任务分派、验证、审核和汇总不能全部冒充 Agent Turn。
- 阶段10 独立后台执行尚未实施。阶段7已实现附件只读工具、内置目录发现和 Worker 多轮工具调用；任务目录与 Python/Shell 专用执行器仅有本地实现，尚未接入聊天 Agent。`model_only_v1` 仍不执行工具；`tool_enabled_v1` 可执行授权的附件工具，不表示已开放 Shell、Git、MCP 或外部写入。
- 阶段4已实现第一版 `model_only_v1` 接入：前端消费 Workflow 创建/发现/结果、Node 和事件有限回放接口，按事件序号恢复并通过节点查询显示完整结果；显式取消、并行组和费用预留字段的前端适配已经完成。历史记录包含单个 Provider 的真实流程验证，完整真实 Provider 浏览器矩阵仍后置；工具结论／来源及调用视图已有本地实现；新版两轮真实浏览器验收仍待执行，详见阶段4及工具验证记录。
- 阶段6 OpenTelemetry 尚未实施，但优先级已提高。阶段3必须提前固定 trace/span 关联字段，用户可见运行事实仍保存在 MySQL，不能由遥测数据反推业务状态。

## 历史实施结果（核对至 2026 年 9 月 12 日）

下表是原同步纯模型范围的验收记录，不代表新增工具、引用修正或两轮流程均已真实验收；当前行为见文首版本表。

| 能力 | 当前状态 | 已验证行为与边界 |
|---|---|---|
| `model_only_v1` 主流程 | 已实现 | 请求归一化、上下文组装、Controller 规划、计划校验、Task/Agent Run 分派、工作 Agent、Handoff、确定性验证、可选 Reviewer、最终汇总和完成节点按普通异步 Python 顺序执行 |
| 完整节点结果 | 已实现 | 所有节点使用 `agent_workflow_node_result.v1` 信封，包含输入引用、类型化输出、分支、预算、证据、错误和生命周期；详情与完整结果可查询 |
| 数据库事实 | 已实现 | 新增 Workflow、Node、Event 表；补充 Agent Run/Turn 到 Workflow/Node 的引用；Run 目前最多创建一个 Workflow，节点键和节点尝试具有数据库唯一约束 |
| 依赖与交接 | 已实现 | Controller 返回的有向无环任务图先由程序校验；无依赖的工作 Agent 最多两个并行，每个 Provider 调用使用独立数据库会话；依赖 Task 的 Worker 输出与 Handoff 会显式传给下游 Agent |
| 审核约束 | 已实现 | 确定性验证失败不能被 Reviewer 覆盖；有效的 Reviewer 输出始终进入最终汇总。`fail`、错误级发现、重新规划或重试要求仍使 Evaluation 失败，但只约束最终回答不得宣称被拒绝内容已确认，并要求明确限制、缺失输入、未完成工作和安全下一步，不再吞掉用户回答；Evaluation 保留候选 Attempt 与 Task 的正确归属 |
| 最终消息一致性 | 已实现 | Assistant Message 与最终汇总节点的完成事件使用同一事务；节点合同或持久化失败会回滚尚未提交的消息，避免失败工作流留下成功答案 |
| HTTP 与 SSE | 已实现 | POST 支持同步 JSON 或提交后实时 SSE；GET 支持 Workflow、Result、Node 列表/详情和按序号回放已持久化事件；显式取消接口可阻止后续节点；GET `/events` 按设计只做有限回放 |
| 事件观察者隔离 | 已实现 | 事件先提交到数据库再通知请求级实时观察者；观察者断开或回调异常只记录警告并解除该观察者，不回滚事实、不使工作流失败；任务取消仍按原取消语义传播 |
| 幂等和失败收敛 | 已实现核心范围 | 完全相同的幂等请求返回原 Workflow；不同请求或同一 Run 的第二个 Workflow 返回冲突；超时、取消和模型结果校验失败会终结已经启动的 Node、Task、Agent Run 与 Agent Turn，避免遗留虚假的 `running` 状态 |
| 用量一致性 | 已实现 | 创建 Workflow Completion 前，按工作流模型调用幂等键读取 Model Attempt，独立汇总输入、输出、缓存读取、费用和调用次数，并与已提交节点用量比较；不一致时明确失败，不发布错误聚合 |
| 自动缓存诊断 | 已实现离线范围 | Attempt 保存 Agent 角色、稳定前缀指纹、缓存读取、缓存写入、缓存未命中、首个可见Token时间和总延迟；内部接口可按 Run、工作流版本、Provider、Model、角色和指纹比较观察窗口内首个与后续请求；Anthropic 输入已归一化为普通输入、缓存读取与缓存创建之和 |
| 资源限制 | 已实现 | Run 请求最长 100,000 字符；节点数、模型调用数、工作流总时长、单次模型超时和单节点输出大小均有限制；`max_parallel_agents` 范围为 `1`～`2`；并发调用使用原子费用预留避免超卖 |
| 公开活动摘要 | 已实现 | `public_view.activity`和事件`public_payload.activity`使用`node_activity_summary.v1`；11类节点具有确定性中文模板、来源标记及运行中/完成/失败/取消/结果未知摘要，历史记录继续回退`summary` |
| 工具、Memory 与 Knowledge | 明确禁用 | `implementer`、工具、文件写入、命令、网络及外部副作用在 `model_only_v1` 计划校验时被拒绝；Memory 和 Knowledge 不自动注入 |

阶段3边界说明：

- `POST /agent-workflows/{workflow_execution_id}/cancel` 是协作式取消：立即持久化取消事实并阻止后续节点；已经进入供应商网络边界的请求不会伪装成从未发送，返回后只做事实收敛。
- `model_only_v1` 没有等待用户输入的分支，因此不提供没有实际状态来源的 `resume` 接口。需要请求结束后继续、进程重启恢复或跨进程执行时恢复阶段10。
- 类型化节点公共结果保持 64 KiB 硬上限，超限明确失败。模型网关已经把原始供应商请求和响应保存到 MinIO；不会把公共节点合同静默改成另一种 Artifact 形状。
- POST SSE 用于当前请求的实时事件；GET `/events` 按设计提供有限、可重复的持久化回放，前端刷新后再读取 Summary/Result，不增加第二套持续订阅协议。
- Agent Runtime 已完成执行职责划分：`workflow_execution_service.py` 负责创建工作流、管理同步或 SSE 使用的数据库会话并返回结果；`model_only_workflow_orchestrator.py` 决定 `model_only_v1` 的节点执行顺序和审核分支；Provider 调用和类型化输出校验由 `agent_model_node_service.py` 负责；Workflow、Node、Event、预算、Task、Agent Run 和 Agent Turn 的状态写入，以及成功、失败、取消时的相关状态收尾，均由 `workflow_execution_state_service.py` 负责。
- Workflow Completion 节点、Workflow/Run 成功终态及两个完成事件使用同一数据库事务；实时观察者只能在该事务提交后收到这两个事件。
- OpenTelemetry 仍由阶段6装配；阶段3只保留稳定关联字段。真实供应商冒烟测试需要部署环境密钥，是可选验证，不作为离线自动化完成条件。

## 后续附件驱动工作流（2026 年 9 月 5 日，未开始）

保留标题供旧链接定位；2026-10-08 将该工作项并入[工具主计划](phase-7-tool-runtime-plan.md#agent-持续调用与失败处理)，不再作为阶段3独立待办。2026-10-12 已实现附件只读工具和 Agent 执行配置自动选择；快速回复仍保留独立循环，不强制切换为 Agent。未新增专用 File Agent；2026-10-13 逐结论证据传递和审核补读已有本地实现，验收范围见上文。

工具启用仍使用既有规划的 `tool_enabled_v1`，保持 `model_only_v1` 无工具语义。文件是工具输入的一类，不增加必经 File Agent；Handoff 继续使用真实阶段结果、来源与覆盖，公开活动只显示已提交事实。具体循环、错误处理、代码组合、生成工具和前端验收只维护在主计划。

## 阶段3首版能力边界

### `model_only_v1` 执行配置

阶段3当前交付不依赖模型工具的完整工作流；阶段7专用文件解析执行器的存在不改变以下执行配置：

```text
用户请求
  ↓
请求归一化（确定性）
  ↓
上下文组装（以当前User Message锚定，由Context Builder组装本节点已启用来源）
  ↓
Controller 规划（模型）
  ↓
计划校验（确定性）
  ↓
创建 Task / Agent Run 并分派
  ↓
      工作 Agent 执行（模型；无依赖任务最多两个并行）
  ↓
Handoff 校验与提交
  ↓
确定性验证
  ↓
必要时独立 Reviewer
  ↓
Controller 最终汇总
  ↓
完整 AgentWorkflowResult
```

首版角色：

| 角色 | 首版能力 | 明确限制 |
|---|---|---|
| `controller` | 目标理解、是否拆解、任务依赖、汇总 | 不亲自读取仓库或执行工具 |
| `planner` | 根据显式上下文形成实施或研究计划 | 不能声称检查了未提供的文件 |
| `researcher` | 分析请求中明确提供的文本、Artifact 摘要和证据 | 无代码搜索工具，不允许生成虚假文件证据 |
| `reviewer` | 独立读取目标、候选结果、确定性检查和证据引用 | 不继承实现模型隐藏上下文，不修改结果 |
| `verifier` | 预留角色，当前不创建模型节点 | 首版由确定性程序节点直接验证 Handoff 和持久化引用，不把程序检查交给模型 |
| `implementer` | 不启用 | 阶段7写入、命令、Git 和隔离 Workspace 恢复前拒绝创建该角色任务 |

- Controller 输出包含工具或代码修改要求时，计划校验当前返回 `agent_capability_unavailable` 并使工作流失败；未来实现等待输入后才允许进入 `waiting_for_input`。不能创建一个假 Implementer 后返回空成功。
- 阶段7恢复后新增 `tool_enabled_v1` 工作流定义，复用相同节点结果信封，并增加工具节点输出；不能在 `model_only_v1` 中静默改变行为。
- 阶段3以 `model_only_v1` 作为可独立交付能力；`tool_enabled_v1` 的模型循环、写隔离与测试按工具主计划统一交付，代码复用本模块，不回退阶段3原完成状态。

### 稳定模型思考默认与 Agent 透传（阶段2、阶段4新增集成项）

- 当前状态：已完成阶段3后端集成。`AgentWorkflowCreate.reasoning`是唯一工作流级选择；省略时所有模型节点分别解析各自登记模型的“启用 + medium”默认，显式选择则在所有角色间保持同一个统一配置。
- Agent Workflow 不定义第二套思考等级。每个角色绑定继续使用阶段2统一的 `ReasoningConfiguration`，有效等级和默认值来自该角色所选 `llm_models` 记录。
- 调用方未为本次工作流显式选择思考配置时，Controller、Worker、Reviewer和Final synthesis都解析为各自稳定模型的默认策略；当前产品默认固定为 `enabled=true`、`effort=medium`，不能省略字段后退回供应商原生默认。
- 第一版Composer只提供工作流级选择，并把同一用户选择提交为`AgentWorkflowCreate.reasoning`。后端在创建新工作流事实前预检所有已提供角色绑定；角色使用不同模型时，每个模型都必须能把该统一等级解析为已登记的原生值。任一角色未登记、已禁用或不支持时，分别返回`model_not_allowed`或`unsupported_capability`，不会先调用部分角色再失败。
- 每个模型节点的Model Attempt保存统一等级、稳定模型思考策略版本和Provider实际解析结果；刷新后的节点证据必须能够说明本次调用使用了用户选择还是模型默认。
- `reasoning`进入工作流请求JSON和幂等请求哈希；同一幂等键改变思考配置会返回冲突。已经完成的精确幂等回放使用原工作流结果，不因模型策略之后变更而重新验证或再次调用模型。
- 角色级独立调节不在第一版实施范围；后续若开放，仍必须逐角色校验对应稳定模型，而不能把Controller配置无条件复制为不可执行参数。

## 核心实体边界

```text
Run
└── AgentWorkflowExecution
    ├── AgentWorkflowNodeExecution（编排节点事实）
    │   ├── Task（需要执行的业务工作）
    │   ├── AgentRun（一个角色执行一个 Task）
    │   │   └── AgentTurn（模型/工具循环步骤）
    │   │       ├── ModelAttempt
    │   │       └── ToolCall（阶段7恢复后）
    │   ├── AgentHandoff（跨 Agent 可依赖结果）
    │   ├── Evaluation（审核或确定性检查事实）
    │   └── Artifact（大型输出或证据）
    └── AgentWorkflowEvent（用户可恢复的有序业务事件）
```

- `AgentWorkflowExecution` 是一次 Run 使用某个版本化工作流定义的执行事实，不代替 Run。
- `AgentWorkflowNodeExecution` 记录编排步骤的输入引用、完整类型化输出、分支和生命周期，不代替 Agent Turn、Attempt、Tool Call 或 Evaluation。
- `AgentRun`、`AgentTurn` 的表和含义继续保留，但代码职责应从 Memory 实验目录迁移到 Agent Runtime；L0 Working State 仍由 Memory 模块引用，不复制表或创建第二套状态。
- Handoff 继续是不可变的 L2 协作事实。阶段3可以显式把依赖 Task 的 Handoff 作为下一节点输入，但不启用 Memory Packet，也不自动注入 L1、L3、L4 Memory。
- 用户可见事件属于 MySQL 业务事实；OpenTelemetry span 属于阶段6遥测。两者通过 ID 关联，但任何一方都不替代另一方。

## 工作流定义

### 当前实现

- 当前版本、执行配置及节点输出以[当前规则](#当前节点职责版本与修正规则)为准；两种配置复用原节点分工，工具配置允许 Worker 执行与 Reviewer 补读。
- 协调器使用普通异步 Python 的 `if`/`for` 和明确函数调用按顺序执行，不使用 LangGraph Graph API。
- 节点输出由 `NODE_OUTPUT_MODELS` 按 `output_type` 映射到唯一 Pydantic 类型，写入前和读取后都会重新校验。
- `workflow_definition_registry.py` 登记支持的版本／执行配置、固定节点和按任务生成的节点，应用导入时检查重复定义、未知输出类型和未知后续节点；新旧版本不共用含义不明的返工开关。
- 创建工作流时必须找到完全匹配的名称、版本和执行配置；节点开始前校验 `node_key`、`node_type` 和 `output_type`，节点完成前校验实际后续节点及跳过节点是否属于允许连接。
- `model_only_workflow_orchestrator.py` 使用普通异步 Python 的 `if`、`for` 和明确函数调用决定节点顺序、计划拒绝、是否审核，以及审核意见如何约束最终汇总。审核不是回答投递开关；只有无可信类型化审核结果、确定性验证失败或其他执行错误才会按对应错误合同终止。

### 当前执行顺序与后续扩展

基础顺序为请求归一化 → 来源清单 → Controller 计划 → 程序计划校验 → 任务分派 → 按依赖执行 Worker/Handoff → 程序确定性验证 → 可选 Reviewer → Controller 最终汇总 → 完成。各模型调用构建各自输入。Worker 为动态任务，外层流程并非由模型任意生成；具体返工、停止和统计规则见本文当前规则，以下接口草图为后续扩展设计，不表示已存在通用 Graph API。

工作流定义使用代码注册表和普通 Python 控制流：

```python
class AgentWorkflowNode(Protocol):
    async def execute(
        self,
        context: AgentWorkflowNodeContext,
    ) -> AgentWorkflowNodeResult:
        ...
```

- 节点使用稳定 `node_key`、`node_type` 和 `node_version` 注册；函数重命名不能改变已持久化语义。
- 工作流定义登记表拒绝重复的工作流名称、版本和执行配置组合，并检查节点输出类型和允许连接。
- 工作流定义版本固定节点集合、允许转移和输出 Schema；执行开始后不能切换到 `latest`。
- Python `if`、`for`、`asyncio.TaskGroup` 和明确函数调用表达分支与并行，不把状态隐藏在字符串边关系中。
- 节点函数只返回结果，不自行决定 HTTP 表现；协调器负责状态转换，状态持久化组件负责事务和事件。

## 完整节点返回合同

### 通用信封：`AgentWorkflowNodeResult`

每个节点通过 POST JSON、Node 详情和 Result 查询返回以下全部字段。字段不适用时返回 `null`、空数组或空对象，序列化时不得省略；数据库保存同一有界事实，SSE 只发送其公共投影、节点 ID 和详情路径：

```json
{
  "schema_version": "agent_workflow_node_result.v1",
  "workflow_execution_id": "uuid",
  "workflow_name": "model_only",
  "workflow_version": "1.0.0",
  "node_execution_id": "uuid",
  "node_key": "controller_planning",
  "node_type": "model",
  "node_version": "1.0.0",
  "node_sequence": 3,
  "node_attempt": 1,
  "parent_node_execution_id": null,
  "run_id": "uuid",
  "task_id": "uuid-or-null",
  "agent_run_id": "uuid-or-null",
  "agent_turn_id": "uuid-or-null",
  "agent_role": "controller-or-null",
  "status": "completed",
  "input": {
    "schema_version": "controller_planning_input.v1",
    "input_hash": "sha256-hex",
    "source_node_execution_ids": [],
    "message_ids": [],
    "artifact_ids": [],
    "handoff_ids": [],
    "context_build_id": null,
    "prompt_release_id": null
  },
  "output_type": "controller_plan",
  "output_schema_version": "controller_plan_output.v1",
  "output": {},
  "transition": {
    "selected_transition": "validate_plan",
    "next_node_keys": ["plan_validation"],
    "skipped_node_keys": [],
    "condition_summary": "Plan is valid and requires decomposition"
  },
  "evidence": {
    "model_attempt_ids": [],
    "tool_call_ids": [],
    "evaluation_ids": [],
    "artifact_ids": [],
    "handoff_ids": []
  },
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cached_tokens": 0,
    "estimated_cost": "0",
    "model_call_count": 0,
    "tool_call_count": 0
  },
  "budget": {
    "cost_limit": null,
    "cost_used_before": "0",
    "cost_used_after": "0",
    "remaining_cost": null,
    "remaining_model_calls": 0,
    "remaining_nodes": 0,
    "remaining_wall_time_ms": 0
  },
  "timing": {
    "started_at": "RFC3339",
    "completed_at": "RFC3339-or-null",
    "duration_ms": 0
  },
  "public_view": {
    "status_label": "正在规划",
    "summary": "Controller created a validated task plan",
    "progress_current": 3,
    "progress_total": 11
  },
  "observability": {
    "trace_id": "hex-or-null",
    "span_id": "hex-or-null"
  },
  "warnings": [],
  "error": null,
  "created_at": "RFC3339"
}
```

### 合同约束

- `output` 必须是按 `output_type + output_schema_version` 判别的 Pydantic 类型，不允许任意 `dict` 直接穿透。
- `input` 只保存精确资源引用、版本和 hash，不复制完整 Prompt、消息、Handoff 或文件内容。
- `evidence` 只保存既有业务事实 ID；token、费用和时长聚合自 Attempt/Tool Call，不代替原表。
- `transition.condition_summary` 允许保存简短、可公开的规则依据，不保存模型逐步推理或隐藏思维链。
- `public_view` 由后端根据节点事实确定性生成，不能让模型自行宣称进度或成功。
- `error` 固定包含 `error_code`、`error_type`、`public_message`、`is_retryable`、`outcome_is_known` 和 `details_reference_id`；成功时为 `null`。
- 当前`64 KiB`检查对校验后的节点`output` JSON做UTF-8字节计数，超限会明确失败；其余信封字段分别受固定Schema、列表数量、字符串长度、最大32个节点和有界分页约束，因此完整HTTP结果具有组合上限，但不错误复用单节点的64 KiB阈值。SSE事件只包含状态、节点ID、类型、详情路径和有界错误投影，不内联节点输出。后续工具生成的大型正文、完整报告和大量证据属于阶段7 Artifact工作项，应让`output`返回Artifact ID、hash、MIME和安全预览。“完整参数”不等于无限内联内容。
- 内部与授权 API 返回所有合同字段；敏感值从形成时就不进入合同，而不是读取时临时删除。

### 公开活动摘要（已实现）

#### 目标和边界

这项能力回答“工作流当前正在做什么、刚完成了什么”，但不回答“模型逐步想了什么”。产品名称统一为**公开活动摘要**（代码对象规划为`NodeActivitySummary`），不能命名为`reasoning_summary`，避免与Provider返回的推理摘要或隐藏思维链混淆。

- 运行中摘要只能使用调用前已经确定并已授权公开的输入事实，例如节点类型、任务标题、角色和任务数量。
- 完成摘要只能在节点类型化输出校验并持久化后生成；允许引用已通过公共合同校验的短字段，但不能直接展示完整模型响应。
- 单次Provider调用期间没有更细的可靠进度时，保持“{角色}正在执行某项工作”，不能编造百分比、内部步骤或预计完成时间。
- Worker节点完成只表示一个Agent提交了供下游使用的阶段结果，不表示用户目标或整个任务已经完成；只有最终汇总和工作流完成事实可以使用“最终答案已生成/已提交”。
- 摘要不包含模型隐藏推理、思维链、System Prompt、密钥、内部对象URI、完整用户输入、完整Handoff、完整文件内容或未脱敏Provider响应。
- 当前`public_view.summary`继续作为兼容字段；历史节点不回填、不猜测。新节点写入版本化活动对象后，兼容字段必须镜像同一`text`，不能形成两套不同文案事实。

#### 合同设计

`NodePublicViewRead`已增加可选`activity`对象；该字段对历史读取可选，对功能发布后的新节点写入为必填：

```json
{
  "public_view": {
    "status_label": "正在规划",
    "summary": "Controller 正在规划执行步骤",
    "progress_current": 3,
    "progress_total": 11,
    "activity": {
      "schema_version": "node_activity_summary.v1",
      "phase": "running",
      "text": "Controller 正在规划执行步骤",
      "source": "deterministic_input"
    }
  }
}
```

字段语义：

| 字段 | 允许值或约束 | 含义 |
|---|---|---|
| `schema_version` | 固定`node_activity_summary.v1` | 允许未来修改模板而不重解释历史记录 |
| `phase` | `running`、`completed`、`failed`、`cancelled`、`outcome_unknown` | 与已持久化节点状态一一对应，不由文案推断 |
| `text` | 纯文本，1～240个Unicode字符 | 用户可见的一句话活动说明；去除控制字符并合并多余空白，不渲染Markdown或HTML |
| `source` | `deterministic_input`、`typed_output`、`error_contract` | 说明摘要来自调用前确定事实、已验证节点输出或安全错误合同 |

持久化和事件规则：

1. 节点开始事务先把`phase=running`活动摘要写入`public_view_json`，再提交`agent.node.started`；只有提交成功后才能调用可能计费的Provider。
2. 节点完成时，先校验类型化`output`，再由确定性模板生成`phase=completed`摘要，并与节点完成事实同一事务保存。
3. 失败、取消和结果未知分别从`AgentWorkflowErrorRead.public_message`及稳定终态生成`error_contract`摘要，不能把内部异常文本直接带到活动摘要。
4. SSE事件`public_payload.activity`携带与数据库`public_view.activity`完全相同的对象；`public_summary`仅镜像`activity.text`以兼容现有消费者。前端不得解析两者来决定状态。
5. 同一节点只在耐久生命周期检查点更新活动摘要，不为模型Token流创建高频数据库事件。事件去重身份继续使用`event_id`和`event_sequence`。

#### 各节点输出文本

模板中的标题、目标和摘要必须先规范空白并截断；缺少安全字段时使用不带占位值的通用句，不能显示`None`、空引号或内部ID。

| 节点 | 运行中文案 | 完成文案 | 完成文案的数据来源 |
|---|---|---|---|
| 请求归一化 | 正在确认请求目标与能力边界 | 已确认请求目标与能力边界 | 确定性节点结果；默认不复述完整`normalized_objective` |
| 上下文组装 | 正在准备本次运行所需上下文 | 已准备上下文：纳入{已纳入数量}项，排除{已排除数量}项 | `included_*_ids`和`excluded_sources`计数 |
| Controller计划 | Controller正在规划执行步骤 | Controller已规划：“{计划摘要}” | `decision_summary`的有界纯文本投影；任务详情继续在完整节点输出中查看 |
| 计划校验 | 正在检查计划依赖与能力边界 | 通过时：计划检查通过，{任务数量}项任务可执行；失败时：计划不可执行，发现{问题数量}项问题 | `is_valid`、`total_task_count`和失败检查计数 |
| Agent分派 | 正在分配{任务数量}项工作任务 | 已分配{任务数量}项任务，形成{执行组数量}个执行组 | `created_tasks`和`dispatch_groups`数量 |
| 工作Agent模型节点 | Agent（{角色}）正在执行：“{任务标题}” | Agent（{角色}）已提交“{任务标题}”的阶段结果：{Worker摘要短句} | 调用前角色和任务标题；完成后`structured_output.summary`的有界纯文本投影；不得表述为任务完成 |
| Handoff提交 | 正在整理“{任务标题}”的交接结果 | 已接收Agent（{角色}）的阶段结果：“{交接目标}” | 已验证并持久化的`agent_role`与`AgentHandoffOutput.objective` |
| 确定性验证 | 正在核对交接结构、引用与完成状态 | 通过时：校验通过，已确认{已验证数量}项；未通过时：校验发现{阻塞数量}项阻塞问题 | `verdict`、`verified_claims`和`blocking_findings`数量 |
| 独立审核 | 正在独立审核候选结果与证据 | 通过时：审核通过；需修订时：审核建议修订，发现{发现数量}项问题；失败时：审核未通过，发现{错误数量}项错误 | `verdict`和`findings`，不直接展示审核模型思维链 |
| 最终汇总 | 正在规划最终答案 | 最终答案已生成：覆盖{完成项数量}项目标，保留{未解决数量}项未解决事项 | `completed_objectives`与`unresolved_items`数量 |
| 工作流完成 | 正在提交工作流完成事实 | 最终答案已提交：{节点数量}个节点，{模型调用数量}次模型调用 | `node_count`和`model_attempt_ids`数量 |

失败终态使用统一文案：`执行失败：{安全错误摘要}`、`执行已取消`、`模型调用结果暂无法确认`。节点失败后不得保留“正在……”；审核给出`needs_revision`或`fail`时仍按现有规则进入受约束的最终回答，不把“审核未通过”再次解释成工作流异常。

#### 前端消费边界

- 对话中的活动区默认折叠，只显示`event_sequence`最大的最新活动；用户展开后按事件序号查看全部已提交活动，完整节点参数仍在独立运行证据抽屉中查看。
- 并行Worker按`node_execution_id`分别显示；同一节点的开始活动和阶段结果是两条独立生命周期事实，后到结果不能覆盖此前的“正在执行”。事件只按`event_sequence`去重，乱序或缺口时以Result快照收敛。
- 最终Assistant Message出现后仍保留折叠活动区，用户可以在回答旁展开本次运行的简单工作摘要；切换运行后不把旧运行活动混入当前回答。
- 最新活动使用`aria-live="polite"`供辅助技术播报；不得播报Token级变化，也不得把纯文本活动摘要当Markdown渲染。
- Provider已有真正的公开推理摘要时，继续放在现有Reasoning区域；公开活动摘要放在“执行过程”区域，两者不能合并或互相替代。

#### 测试与完成条件

- 后端测试先覆盖每类节点的运行中和完成模板、无可用安全字段时的通用句、240字符边界、控制字符清理和敏感内容不进入摘要。
- 事务测试验证开始摘要提交后才调用Provider，完成摘要只在类型化输出校验后提交，失败摘要不会泄露原始异常，并且事件活动对象与节点快照逐字段一致。
- 兼容测试验证旧`public_view_json`缺少`activity`时仍可读取；新写入必须包含`node_activity_summary.v1`，`summary`与`activity.text`必须相同。
- 前端测试覆盖事件重复、乱序、缺口恢复、两个并行Worker、同一Worker开始与阶段结果并存、折叠态最新活动、展开态完整瀑布流、刷新后Result恢复和辅助技术播报。
- 完成条件是用户能够看到节点级“正在做什么”和“刚完成什么”，同时无法从该接口取得隐藏推理、完整模型输出或未提交事实。

## 各节点完整输出参数

以下列出纯模型配置的基础字段与历史兼容结构，不是工具证据输出的另一份定义。当前工具 Worker／Handoff／Reviewer 扩展分别见对应小节说明，完整类型以 Schema 为依据，原文引用字段及规则统一维护在阶段7。

### 1. `request_intake` → `RequestIntakeOutput`

```text
normalized_objective
request_type
complexity
requires_decomposition
constraints[]
acceptance_criteria[]
explicit_assumptions[]
clarification_questions[]
required_capabilities[]
unavailable_capabilities[]
requested_output_format
language
```

- 当前这是确定性节点：使用 Run 请求构造结果，`complexity` 固定为 `complex`、`requires_decomposition` 固定为 `true`，不调用小模型；用户原文仍由 Run 保存。
- `explicit_assumptions` 只能列出协调器允许继续使用的公开假设，不能保存隐藏推理。
- 目标态在存在阻塞性问题时转移到 `waiting_for_input`；当前还没有该分支或恢复接口。

### 2. `context_assembly` → `ContextAssemblyOutput`

当前 Schema 字段：

```text
assembly_scope = run_source_inventory
included_run_id
included_message_ids[]
included_artifact_ids[]
included_handoff_ids[]
excluded_sources[] {source_type, source_id, reason}
input_character_count
is_truncated
memory_packet_id
```

- 当前实现已用Run关联的当前User Message锚定Session文本历史；Controller、Worker、Reviewer和Final synthesis分别按自己的Provider/Model创建Context Build，Model Attempt与Node Input保存`context_build_id`和实际Message ID。
- `context_assembly` 只登记锚点之前的有界候选 Message ID；节点键没有改名，`assembly_scope` 标明清单用途。Context Builder 读取已启用的业务来源；附件工具历史由 Gateway 按阶段2规则追加，并非清单已保存的来源。Memory、Knowledge 和对象 Message 产品读取仍未启用，`memory_packet_id` 保持 `null`。
- 对象型Message的受控读取合同归入阶段2统一上下文来源补充，并在阶段4产品链路验收；阶段3只消费Context Builder返回的类型化来源，不自行读取`content_uri`，也不把该合同列为阶段3原完成条件。
- 目标态中单个结构化来源不能从中间截断；排除或裁剪必须返回证据。
- 2026年8月26日确认的统一上下文接入以[阶段2统一模型上下文设计](phase-2-llm-core-capabilities.md#统一模型上下文构建与阶段3阶段4运行时集成设计)为准：工作流按Run关联的当前User Message建立并发锚点；Controller、Worker、Reviewer和Final synthesis都向Context Builder声明本节点需要的来源类型，由它统一组装Session、Prompt、Memory、Knowledge、Artifact、Agent/Handoff、验证/Evaluation及未来Tool结果。不同角色模型使用各自Context Build，不能重复追加当前目标，也不能在工作流内部另写历史或节点结果裁剪逻辑。

### 3. `controller_planning` → `ControllerPlanOutput`

当前 Schema 字段：

```text
decision_summary
tasks[]
  task_key
  task_type
  title
  objective
  assigned_role
  required_capabilities[]
  expected_output_type
  completion_criteria[]
  priority
  timeout_seconds
  max_model_calls
dependencies[] {task_key, depends_on_task_key, dependency_type}
review_policy
known_risks[]
unknowns[]
```

- 当前计划没有 `plan_id`、直达模式、并行组、独立预算或 `verification_plan`。
- Controller 只能使用请求已绑定的 `planner`/`researcher`；`review_policy` 必须与请求一致；`model_only_v1` 出现 write、execute、network 或外部副作用能力时计划不得通过。
- `decision_summary` 是简短决定依据，不要求模型输出思维链。

### 4. `plan_validation` → `PlanValidationOutput`

当前 Schema 字段：

```text
is_valid
checks[] {check_id, status, error_code, message, related_task_keys[]}
topological_task_order[]
cycle_paths[]
total_task_count
total_model_call_limit
capability_gaps[]
relationship_errors[]
rejected_plan_reason
```

当前确定性检查覆盖重复 Task Key、审核策略篡改、角色/能力不可用、未知或自依赖、DAG 环、最大依赖深度 6、Task 数、模型调用数与节点数。输出类型和完成条件由 Pydantic Schema 校验；工作 Agent 执行组再按依赖完成情况和 `max_parallel_agents` 形成。

### 5. `agent_dispatch` → `AgentDispatchOutput`

```text
created_tasks[] {task_key, task_id, status}
created_agent_runs[] {task_id, agent_run_id, agent_role, provider, model, status}
dispatch_groups[] {group_id, agent_run_ids[], concurrency_limit}
blocked_tasks[] {task_key, task_id, reason_code, summary}
skipped_tasks[] {task_key, reason_code}
role_model_bindings[] {agent_role, provider, model}
```

- Task、Agent Run 和依赖关系必须在明确事务边界创建；重复 idempotency key 返回原有映射。
- 当前按有向无环任务图生成一个或多个 `worker-group-{sequence}`。同组无依赖 Task 的 `concurrency_limit` 最大为 `2`；依赖 Task 先保存为 `waiting_for_dependency`，开始前再次校验所有前置 Task 已完成。
- 父级工作流会话只负责依次提交 Task、Agent Run、Node、Turn 和 Handoff 事实；每个并行 Provider 调用通过数据库 session factory 获取独立 `AsyncSession`，不会把一个会话交给多个协程。

### 6. `agent_model_execution` → `AgentModelExecutionOutput`

```text
agent_turn_id
model_attempt_id
provider
model
response_kind
text_preview
text_artifact_id
structured_output
requested_tool_calls[]
finish_reason
provider_request_id
input_tokens
output_tokens
cached_tokens
estimated_cost
latency_ms
transport_attempt_count
capability_warnings[]
```

- 最终节点结果的 `response_kind` 为 `structured`。纯模型 `structured_output` 包含 `summary`、`confirmed_facts[]`、`decisions[]`、`remaining_work[]`、`risks[]` 和 `unknowns[]`；新工具执行使用 `worker_evidence.v2`，逐条 claims 引用及推测／未解决事项见阶段7，不能只提交旧的 confirmed_facts 替代引用。
- 中间工具轮不作为最终节点结果提交，真实 Tool Call 及结果分别保存；最终输出不能把未执行的工具请求当成事实。`model_only_v1` 不执行工具，`text_artifact_id` 仍为预留，不表示已自动生成大文本 Artifact。
- 原始供应商响应继续由 Attempt 的对象引用保存，节点结果只返回标准化字段和引用。

### 7. `handoff_submission` → `AgentHandoffOutput`

```text
agent_handoff_id
schema_version
status
objective
confirmed_facts[]
decisions[]
files_read[]
files_changed[]
artifacts[]
tests[]
remaining_work[]
risks[]
unknowns[]
invariants_for_next_agent[]
supersedes_handoff_id
```

- 纯模型和历史记录复用 `agent_handoff.v1`；当前工具执行使用 `agent_handoff.v2` 原样保存 Worker evidence。二者仍是同一 Handoff 实体，不创建第二套表；不丢弃引用，不给历史结果补造来源。
- `agent_handoff.v1`不再设置独立字节或Token上限。工作流把Worker的完整类型化`confirmed_facts`、`decisions`、`remaining_work`、`risks`和`unknowns`原样写入Handoff，不再生成限长投影。Worker模型节点本身仍受64 KiB类型化输出上限约束，因此工作流路径不会产生无限内联结果；内部独立Handoff接口继续执行固定字段、状态、归属和禁止隐藏推理校验。
- 首版没有工具时 `files_read`、`files_changed` 和真实测试证据通常为空；模型不能仅凭描述填入不存在的文件或测试。

### 8. `deterministic_verification` → `DeterministicVerificationOutput`

```text
verdict
checks[]
  check_id
  check_type
  status
  expected
  actual
  evidence_refs[]
  error_code
blocking_findings[]
non_blocking_findings[]
verified_claims[]
unverified_claims[]
coverage_summary
requires_independent_review
```

- 程序检查不调用模型；没有真实工具和测试结果时必须把相关声明放入 `unverified_claims`。
- 任何阻塞检查失败都不能被后续模型 Reviewer 改写成程序验证通过。

### 9. `independent_review` → `IndependentReviewOutput`

```text
verdict
score
findings[]
  finding_id
  severity
  category
  description
  evidence_refs[]
  required_action
accepted_claims[]
rejected_claims[]
missing_evidence[]
requires_replan
requires_retry
review_summary
evaluation_ids[]
reviewer_task_id
reviewer_agent_run_id
reviewer_model_attempt_id
```

- Reviewer 只读取用户目标、任务要求、候选结果、确定性检查和明确证据，不读取实现模型隐藏消息或 Working State。
- 工具配置的 Reviewer 还提交 `claim_assessments`，逐条核对当前候选 Task 的 claim，允许读取授权原文及有限补读；完整引用 Schema 与覆盖要求见阶段7。程序检查字段／归属／范围，模型判断原文是否支持结论，两个职责不能互相替代。
- 当前为每个候选 Worker Attempt 创建一条 Evaluation，因此返回 `evaluation_ids[]`，不是单个 `evaluation_id`。
- Reviewer 输出不能覆盖候选结果。有效审核结果约束后续执行与最终汇总；错误级发现、`requires_replan=true` 或 `requires_retry=true` 会使对应 Evaluation 失败。`1.1.0` 在本文当前规则允许时创建第二轮 Task、Attempt 与 Node；`1.0.0` 或不满足返工条件时进入有限制的最终汇总，说明被拒绝主张、缺失输入、未完成工作及安全下一步。有效拒绝本身不能吞掉回答，真正的执行错误则按错误规则终止。

### 10. `final_synthesis` → `FinalSynthesisOutput`

```text
answer_type
final_text
completed_objectives[]
unresolved_items[]
warnings[]
recommended_next_actions[]
source_agent_run_ids[]
source_handoff_ids[]
source_artifact_ids[]
source_evaluation_ids[]
assistant_message_id
```

- 最终文本必须区分已验证、模型判断和未确认事项。
- 当前没有 `final_text_artifact_id`；文本最长 16,000 字符，超限时输出合同失败，`source_artifact_ids` 在 model-only 路径为空。
- Run 有 `session_id` 时，Assistant Message 会先以 `commit=False` 暂存，再与 `final_synthesis` 节点完成事件同一事务提交。当前 Message 暂存失败会使工作流失败，尚未降级为“节点成功但 Message 带警告”。

### 11. `workflow_completion` → `WorkflowCompletionOutput`

```text
workflow_status
final_node_execution_id
node_count
node_counts_by_status
task_ids[]
agent_run_ids[]
model_attempt_ids[]
tool_call_ids[]
evaluation_ids[]
artifact_ids[]
handoff_ids[]
total_input_tokens
total_output_tokens
total_cached_tokens
total_estimated_cost
total_duration_ms
remaining_model_calls
final_output_reference
warnings[]
errors[]
```

该节点只聚合既有节点证据和使用量，不重新生成业务内容。当前 Schema 不包含 `remaining_budget`，只返回 `remaining_model_calls`；费用剩余量由节点 `budget` 投影表达。创建该节点前，服务会按工作流调用键独立汇总 Model Attempt，并校验输入、输出、缓存读取、费用和模型调用数与节点聚合一致；不一致时终止完成流程。

### 阶段7恢复后的 `tool_execution`

未来节点的 `output` 必须直接采用阶段7 `ToolExecutionResult`：工具名/版本、权限决定、状态、结构化输出、Artifact、stdout/stderr 摘要、退出码、耗时、截断和稳定错误。阶段3不复制工具权限或执行实现。

## 工作流级返回合同

同步完成或最终结果查询返回 `AgentWorkflowResult`：

```text
workflow_execution_id
run_id
workflow_name
workflow_version
execution_profile
status
version
snapshot_version                 # 本次完整快照版本
current_stage
primary_node_execution_id
active_node_execution_ids[]
model_call_count
max_model_calls
node_count
max_nodes
started_at
completed_at
created_at
updated_at
nodes[]                         # 完整 AgentWorkflowNodeResult，按 sequence 排序
final_output                    # FinalSynthesisOutput 或 null
completion                     # WorkflowCompletionOutput 或 null
aggregate_usage
warnings[]
error
trace_id
```

- `POST` 同步执行成功时返回全部节点结果；最大节点数受硬上限约束。
- `GET /agent-workflows/{id}/result` 是刷新和审计时获取完整节点参数的权威入口；运行中也返回已经提交的完整节点及当前状态。
- 节点列表接口支持 cursor 分页，但 result 接口在节点硬上限内一次返回整个执行快照和 `snapshot_version`，避免客户端拼出不一致版本。
- 节点 `output` JSON 以 64 KiB 上限保持有界，超限时明确失败，不截断结构化 JSON，也不把公共返回合同静默替换成另一种 Artifact 形状。原始供应商请求和响应继续由 Model Attempt 引用对象存储中的审计对象。

## 状态机

### 当前工作流状态转换

```text
创建成功 → running
running → completed | failed | cancelled | outcome_unknown
```

### 当前节点状态转换

```text
创建成功 → running
running → completed | failed | cancelled | outcome_unknown
```

- `pending`、`waiting_for_input`、`skipped` 和 `blocked` 已在枚举中保留，但当前执行路径不会生成这些事实。
- Provider 超时、网络/连接错误或无法确认的内部 Provider 错误会使当前节点和 Workflow 进入 `outcome_unknown`；父 Run 因现有 `RunStatus` 没有未知态，当前仍记为 `failed`。
- POST SSE 的执行任务在响应生成器结束时会传播取消，并收敛已启动的 Node、Task、Agent Run 和 Agent Turn；独立 HTTP 取消接口可以在连接之外持久化取消终态并阻止后续节点。
- API 重启后不会自动恢复运行中工作流；启动审计和显式恢复也尚未实现，因此遗留 `running` 事实是当前已知缺口，不能声称已经转成 `outcome_unknown`。

### 目标态补充

- `waiting_for_input` 必须保存等待原因、允许操作和版本，由带 `expected_workflow_version` 的显式恢复请求转回 `running`。
- `skipped` 必须返回分支原因和选中的替代转移；`blocked` 表示依赖、能力或用户输入尚不满足，不得当作内部错误。
- 模型已接收请求但响应未持久化时，节点和 Attempt 必须保留未知结果证据，不得自动重新计费调用。

## 持久化规划

### `llm_agent_workflow_executions`

```text
workflow_execution_id
run_id
workflow_name
workflow_version
execution_profile
status
version
snapshot_version
current_stage
primary_node_execution_id
active_node_execution_ids_json
role_bindings_json
request_json
review_policy
idempotency_key
request_hash
max_nodes
max_model_calls
model_call_count
max_parallel_agents
wall_time_limit_ms
event_count
total_estimated_cost
trace_id
error_code
error_json
started_at
completed_at
created_at
updated_at
```

- 当前数据库对 `run_id` 建立唯一约束，因此一个 Run 最多对应一个 Workflow Execution；幂等 key 和请求 hash 用于判断是原请求回放还是冲突。

### `llm_agent_workflow_node_executions`

保存完整节点信封的可查询索引和有界 JSON：

```text
node_execution_id
workflow_execution_id
run_id
node_key
node_type
node_version
node_sequence
node_attempt
parent_node_execution_id
task_id
agent_run_id
agent_turn_id
agent_role
status
input_schema_version
input_hash
input_json
output_type
output_schema_version
output_json
transition_json
evidence_json
usage_json
budget_json
public_view_json
warnings_json
error_code
error_json
trace_id
span_id
started_at
completed_at
duration_ms
created_at
```

- 当前同时约束 `(workflow_execution_id, node_sequence)` 和 `(workflow_execution_id, node_key, node_attempt)` 唯一；`parent_node_execution_id`、`task_id`、`agent_run_id` 和 `agent_turn_id` 均已有外键。

### `llm_agent_workflow_events`

用户界面恢复使用追加式业务事件：

```text
event_id
workflow_execution_id
run_id
node_execution_id
event_sequence
event_type
workflow_status
node_status
public_payload_json
occurred_at
public_summary
trace_id
```

- `(workflow_execution_id, event_sequence)` 唯一；事件保存和对应状态转换在同一 MySQL 事务提交。
- 事件只包含节点结果的有界公共投影或节点 ID；当前 `agent.node.completed` 返回节点详情查询路径，不内联完整节点信封，查询接口始终是权威事实。
- OpenTelemetry exporter 故障不能阻止业务事件提交；MySQL 事件也不能被当成遥测 span 导出队列。

### 现有实体调整

- `llm_agent_runs` 已增加 `workflow_execution_id`，并继续使用现有 provider/model 和生命周期字段；角色配置版本、独立预算和错误摘要尚未增加。`(task_id, agent_role)` 唯一约束保留；`1.1.0` 返工创建新的 Task 与 Agent Run，不在同一 Task 下违反唯一约束追加同角色 Run。
- `llm_agent_turns` 已增加 `node_execution_id`，仍只记录模型/工具 Turn；输入/输出引用继续由 Node 与 Model Attempt 保存，Turn 当前没有独立的 `cancelled` 或 `outcome_unknown` 状态。
- `llm_attempts`、`llm_tool_calls`、`llm_evaluations` 和 `llm_artifacts` 通过 node evidence 引用，不复制原始结果。
- 迁移复用现有 Agent 表，不创建 `agent_runs_v2`、`workflow_tasks` 或第二套 Handoff。

## HTTP 与 SSE 接口

当前已实现：

```http
POST /api/v1/runs/{run_id}/agent-workflows
GET  /api/v1/runs/{run_id}/agent-workflow
GET  /api/v1/agent-workflows/{workflow_execution_id}
GET  /api/v1/agent-workflows/{workflow_execution_id}/result
GET  /api/v1/agent-workflows/{workflow_execution_id}/nodes
GET  /api/v1/agent-workflows/{workflow_execution_id}/nodes/{node_execution_id}
GET  /api/v1/agent-workflows/{workflow_execution_id}/events
POST /api/v1/agent-workflows/{workflow_execution_id}/cancel
```

当前不提供：

```http
POST /api/v1/agent-workflows/{workflow_execution_id}/resume
```

`resume` 只有在新的版本化工作流定义真正进入 `waiting_for_input` 时才加入，不能为当前没有等待分支的执行配置提供空动作。

### 创建与同步执行

当前请求合同包含：

```text
workflow_name
workflow_version
execution_profile
idempotency_key
role_bindings（必须包含 controller、至少一个 planner/researcher；review_policy 不是 never 时必须包含 reviewer）
reasoning（可选；省略时逐角色使用登记模型的启用 + medium 默认）
review_policy
max_nodes
max_model_calls
max_parallel_agents（范围 1～2，默认 1）
wall_time_limit_ms
stream
```

其中只有 `idempotency_key` 和 `role_bindings` 没有默认值；`reasoning` 省略时由每个角色的登记模型解析版本化默认。名称保留 `model_only`，版本默认 `1.1.0` 且接受显式 `1.0.0`；执行配置支持 `model_only_v1`／`tool_enabled_v1`，附件授权选择规则见阶段7。`max_workflow_rounds` 为 1～2，默认 2，旧版本仍单轮。`review_policy` 默认 `always`；策略非 `never` 时 `reviewer` 必填，`verifier` 可以绑定但不创建模型验证节点。`max_nodes` 默认／最大 32，按审核策略最小 10 或 11；`max_model_calls` 默认／最大 16，不审核最小 3，普通审核最小 4，`1.1.0` 工具审核最小 6。`wall_time_limit_ms` 允许 1,000～600,000，默认 600,000。最小值只保证入口请求有效，不保证每个计划都能执行两轮。没有 `expected_run_version` 或通用 `budget_overrides`；Run 唯一约束阻止同一 Run 创建第二个 Workflow。

- `idempotency_key` 长度必须为 `8`～`128`；`stream` 默认 `false`。
- `role_bindings` 只接受 `controller`、`planner`、`researcher`、`reviewer` 和 `verifier` 键。每个绑定包含 `provider`、`model`、`structured_output_mode`、`timeout_seconds` 和可选的 `max_output_tokens`；结构化输出模式默认为 `native_schema`，超时默认 `60` 秒且范围为 `1`～`600`。平台不提供默认业务输出上限，字段缺省时不主动缩短输出；显式 `max_output_tokens` 的统一允许范围为 `1`～`65536`。具有费用预算的 Run 为了在 Provider 调用前完成保守费用预留，仍必须显式提供 `max_output_tokens` 和模型价格；无预算 Run 可以保持缺省。

- `stream=false` 已在同一 HTTP 生命周期同步运行并返回完整 `AgentWorkflowResult`。
- `stream=true` 已使用 SSE 实时发送提交后的业务事件；完成事件提供权威结果查询路径，节点事件提供节点 ID、类型和详情查询路径。完整、有界 `AgentWorkflowNodeResult` 由 Node 详情或 Result 接口返回。
- GET `/runs/{run_id}/agent-workflow` 用于发现该 Run 唯一关联的 Workflow；创建前查询返回资源不存在，不创建隐式工作流。
- GET `/nodes` 使用绑定 Workflow 的 sequence cursor 分页，`limit` 默认 `50`，允许 `1`～`100`；其他 Workflow 的 cursor 会被拒绝。
- GET `/events` 使用 `after_sequence` 或合法的 `Last-Event-ID` 回放已经持久化的后续事件；非法游标返回输入错误，不会静默从头回放。
- 新建 Workflow 的 POST 返回 HTTP `201`，即使业务结果是 `failed` 或 `outcome_unknown`；完全相同的幂等回放返回 `200`，请求校验、资源不存在和冲突分别沿用 `422`、`404` 和 `409`。
- `stream` 是传输选择，不参与请求 hash；相同幂等 key 可以在 JSON 与 SSE 表现之间回放同一个持久化 Workflow，不会再次调用模型。
- 创建请求超时后不能无条件自动创建新请求；调用方应使用原 idempotency key 重试或通过 Run 发现接口查询原 Workflow。
- 当前 BFF 与 FastAPI 均检查访问主体及 Run／Workflow／Node 归属，服务 Key 不替代最终用户身份；认证开关、CSRF、免登录限制及目标环境验证范围统一见阶段5。新增节点来源与未来恢复接口必须继续校验用户和文件权限，不能恢复旧的“仅靠部署 Key”规则。

### 事件枚举

当前执行路径实际产生：

```text
agent.workflow.started
agent.workflow.completed
agent.workflow.failed
agent.workflow.cancelled
agent.workflow.outcome_unknown
agent.node.started
agent.node.completed
agent.node.failed
agent.node.cancelled
agent.node.outcome_unknown
agent.node.progress
agent.node.output_repair.requested
agent.node.output_repair.completed
agent.workflow.refinement.requested
agent.workflow.refinement.skipped
```

目标态已经预留但当前不会产生：

```text
agent.workflow.waiting_for_input
agent.node.skipped
agent.node.blocked
agent.budget.updated
```

事件合同共同包含：

```text
event_id
event_sequence
workflow_execution_id
run_id
node_execution_id
event_type
workflow_status
node_status
occurred_at
public_summary
public_payload
trace_id
```

`agent.node.completed` 通过节点 ID 和查询路径引用完整结果。`waiting_for_input`、`skipped`、`blocked` 和独立预算更新事件必须在相应行为实现后才允许产生。超过节点结果内联上限时明确失败，不能截断 JSON、自动改变节点合同或用 Artifact 引用伪装成完整结构化结果；原始供应商响应继续由 Model Attempt 指向 MinIO 审计对象。

## 并行和数据库会话

### 当前实现

- `max_parallel_agents` 允许 `1`～`2`。程序先根据已验证依赖图形成执行组；只有无未完成依赖且处于同组的工作 Agent 才并行。
- 非流式执行使用 FastAPI `yield` 注入的请求级 `AsyncSession`；流式执行在响应体生命周期内创建执行级 `AsyncSession`。并行工作 Agent 的 Provider 调用各自再创建独立短生命周期数据库会话。
- 节点开始事实与开始事件在调用 Provider 前提交；模型 HTTP 调用不包在数据库事务中。Model Attempt、Handoff 和节点状态仍按现有 Service 的多个持久化检查点提交。
- 同组调用使用 `asyncio.TaskGroup`。已经进入供应商边界的同组调用会分别记录结果或错误，程序先持久化可确认的成功事实，再用失败节点终止工作流，不把已计费成功结果丢成无证据状态。
- 父级验证节点只有在全部必需工作 Agent 和 Handoff 到达终态后开始。

## 预算与循环限制

当前资源限制；旧版本差异见本文版本表：

| 限制 | 当前值 | 作用与剩余要求 |
|---|---:|---|
| 单工作流节点数 | 默认/最大 32；最小 10 或 11 | 不审核时最小 10，其他审核策略最小 11；防止无限分支 |
| Controller 生成 Task 数 | 8 | 防止任务爆炸 |
| 最大依赖深度 | 6 | 防止递归编排 |
| 并行 Agent 数 | 1～2 | 只并行无依赖的模型工作节点，每个调用使用独立数据库会话 |
| 每 Worker 计划调用额度 | 纯模型计划为 1；工具计划接受 1～16，受工作流剩余额度约束 | 工具轮与有限输出修正都记实际调用；可执行额度按当前剩余资源及审核／汇总预留检查，不承诺每节点均可用满 16 次 |
| 单工作流模型调用 | 默认／最大 16；最小 3、4 或 6 | 不审核最小 3，普通审核最小 4，`1.1.0` 工具审核最小 6；格式修正和返工不重置总额度 |
| 工作流业务轮次 | `1.1.0` 默认／最大 2，包含首轮；`1.0.0` 为 1 | 只有 Reviewer 明确请求且资源允许才返工；拒绝或资源不足时交最终汇总说明限制 |
| 同步总时长 | 默认/最大 600 秒；最小 1 秒 | 匹配当前无 Worker 边界 |
| 单节点 `output` JSON | 64 KiB | 类型化输出超限明确失败；公共合同不自动切换为 Artifact |
| 单事件 public payload | 固定小型投影 | 事件只包含节点 ID、类型、状态、错误摘要和权威查询路径，不内联节点完整输出或供应商响应 |

- Run 存在 `budget_limit` 时，每次调用按 UTF-8 输入字节上界、结构化输出 Schema、消息框架余量和最大输出 token 计算保守费用；模型没有显式价格配置时在进入 Provider 前拒绝。
- 并行节点通过数据库条件更新原子增加 `model_call_count` 和 `reserved_estimated_cost`。只有当前实际费用加全部在途预留仍不超过 Run 上限时才能调用；完成或失败后释放临时预留，`cost_used` 只保留已确认实际估算费用。
- 返回 Controller 前资源不足时保存未执行原因并进入有限制的汇总；其他资源耗尽按对应错误终止，不能让模型自行申请绕过，也不保证任何失败都可再调用汇总模型。

## 错误合同

### 当前实现

当前执行路径和计划校验会产生以下稳定代码：

```text
node_output_invalid
agent_capability_unavailable
agent_plan_invalid
agent_review_policy_mismatch
agent_dependency_cycle
agent_budget_exhausted
agent_budget_output_limit_required
agent_budget_price_unavailable
agent_node_limit_exceeded
agent_model_call_limit_exceeded
agent_verification_failed
agent_workflow_timeout
agent_workflow_cancelled
agent_provider_outcome_unknown
agent_workflow_internal_error
```

- Provider 错误保留 Model Gateway 的 `error_type` 作为 `error_code`；`timeout`、`network_error`、`connection_error`、`internal_error` 和 `response_persistence_error` 当前会让 Node 与 Workflow 进入 `outcome_unknown`，并强制 `is_retryable=false`。`response_audit_failed` 表示 Provider 响应及费用事实已确认、但原始响应审计对象未保存，因此进入已知 `failed`；其他 Provider 错误也进入 `failed`。
- Handoff 的确定性检查项可以使用 `agent_handoff_invalid`，但当前工作流终态错误代码是 `agent_verification_failed`，不能把前者描述为工作流对外终态错误。
- Run 预算无法覆盖保守费用预留时会在 Provider 调用前返回 `agent_budget_exhausted`；预算 Run 没有显式 `max_output_tokens`、因而无法计算最大费用时返回 `agent_budget_output_limit_required`；模型缺少显式价格时返回 `agent_budget_price_unavailable`。三种情况都不会进入 Provider 调用。
- 请求字面值、必填角色、范围和未知字段由 Pydantic 返回 HTTP `422`；资源不存在和幂等/Run 状态冲突沿用平台现有 HTTP `404`/`409` 错误合同，不转换为节点错误。
- 节点输出不符合 Schema 时按本文版本和错误表判断能否有限修正；持续非法或不允许修正时保留 Attempt 并失败。计划业务校验当前仍直接终止，其有限修正是未开始工作项；不会把自由文本强制塞入结构化字段。

### 目标态补充

以下代码只有在相应 Registry、等待/恢复、费用预留或持久化恢复能力实现后才能加入当前合同：

```text
workflow_not_registered
workflow_version_not_available
workflow_profile_not_available
workflow_state_conflict
node_contract_invalid
node_input_unavailable
agent_role_not_registered
agent_waiting_for_input
agent_result_persistence_failed
```

- Schema、能力、预算和依赖错误不可重试；Provider 临时错误只沿用 Model Gateway 的有限重试，逻辑 Agent 节点不能再套一层无界重试。
- 模型结果已经产生但节点持久化结果无法确认时，目标态必须进入 `outcome_unknown` 并禁止自动重复计费。

## 阶段6可观测性协同

阶段6优先级已经提高，但独立阶段规划尚未开始。本阶段只提前固定不可逆的关联边界，使后续阶段6能够装配遥测而不改写业务合同；业务正确性不等待 exporter：

```text
agent.workflow              # 根 span
├── agent.node              # 每个 Node Execution
│   ├── model.generate      # Model Attempt
│   ├── evaluation.execute
│   └── tool.execute        # 阶段7恢复后
└── mysql.* / object_store.*
```

- `workflow_execution_id`、`node_execution_id`、`run_id`、`task_id`、`agent_run_id`、`attempt_id` 作为低风险关联标识；完整 Prompt、Node output、工具参数、Handoff 和用户文件不进入 span attribute。
- trace/span ID 写入节点事实仅用于跳转关联，不作为状态、顺序或恢复依据。
- OpenTelemetry Generative AI 语义约定仍可能变化；阶段6必须固定所采用的语义约定版本，并用平台稳定字段映射，不能把实验性属性名变成数据库公共合同。
- 当前代码尚未提供 `WorkflowTelemetry` 或等价端口，只有可空的 trace/span 关联字段。目标态先抽象无操作遥测端口，再由阶段6装配 SDK、exporter、采样、指标和跨服务传播。
- 关键指标规划：工作流/节点数量与耗时、各状态数量、模型调用和 token/费用、预算终止、等待输入、审核拒绝、未知结果和事件恢复缺口。

OpenTelemetry 官方将 traces、metrics、logs 作为不同信号，并说明 Python traces/metrics 稳定而 logs 仍在开发；阶段6首批优先 trace 和 metrics。Generative AI 语义属性包含潜在敏感内容警告，因此阶段3节点正文只留在受控业务存储。

## 项目目录现状与后续调整

```text
apps/api/src/nexuspilot_api/features/agent_runtime/
├── models/
│   └── agent_workflow.py
├── routers/
│   └── agent_workflows.py
├── schemas/
│   └── agent_workflows.py
├── services/
│   ├── agent_model_node_service.py
│   ├── model_only_workflow_orchestrator.py
│   ├── workflow_definition_registry.py
│   ├── workflow_execution_service.py
│   ├── workflow_execution_state_service.py
│   ├── workflow_policy.py
│   └── workflow_query_service.py
└── dependencies.py
```

- 当前先按业务功能保留在 API 的 `agent_runtime` feature 中。Router 只处理 HTTP；查询服务组装读取结果；策略模块校验 Controller 计划；工作流定义登记表校验节点名称、类型和允许连接；状态服务负责数据库事务；模型节点服务负责 Provider 调用；执行服务目前仍决定节点顺序和审核分支。
- 暂不为了未来可能的第二个消费者创建 `packages/agents` 平行实现。出现 API 之外的真实消费者后，再提取不依赖 FastAPI、SQLAlchemy、具体 Provider SDK 或 Memory ORM 的纯工作流包。
- 现有 Agent Run/Turn 表仍由 Memory feature 定义，但已经通过外键关联 Workflow/Node。后续迁移职责时必须保持表名和外部关系，不创建第二套 Agent Run/Turn。

### 工作流执行职责调整结果

调整前 `workflow_execution_service.py` 为 2,335 行。2026 年 8 月 13 日完成职责调整后，该文件为 178 行，只负责工作流创建、请求或 SSE 数据库会话以及结果读取；`model_only_workflow_orchestrator.py` 负责当前模型工作流的节点顺序、各节点业务动作和审核分支。状态服务负责提交工作流状态以及成功、失败和取消时的相关状态收尾，模型节点服务负责 Provider 调用和类型化输出校验。阶段7恢复后的工具工作流必须使用独立执行配置，不能把工具循环继续写入 `model_only_workflow_orchestrator.py`。

最小拆分边界：

```text
Router
  ↓
workflow_execution_service.py            # 接收创建和执行请求，并管理一次请求使用的数据库会话
  ↓
model_only_workflow_orchestrator.py       # 使用普通 Python 决定节点执行顺序和分支
  ├── workflow_execution_state_service.py # Workflow/Node/Event/Task/Turn/Evaluation/Handoff 状态与事务
  └── agent_model_node_service.py         # Provider 调用、结构化输出校验和模型节点结果
             ↓
      workflow_execution_state_service.py # 只提供状态读写，不反向依赖工作流编排服务
```

拆分必须保持以下运行语义：

- 工作流状态、节点和 Handoff 仍由一个父级执行会话按顺序提交；只有同组并行 Provider 调用各自创建独立 `AsyncSession`，不会把父级会话交给多个协程。
- Node started 状态和 `agent.node.started` 事件必须先提交，再调用可能计费的 Provider；事件只能在对应数据库事务提交后推送给实时 SSE observer。
- `ModelInvocationService` 和 Handoff 提交仍保留现有耐久检查点；上层主要传递 ID，跨提交边界后按需重新读取 ORM，不长期持有过期实体。
- 当前执行 Service 为请求/执行级对象，因此其可变 `event_sink` 尚不会跨请求共享；拆分后仍必须把它作为每次执行的构造参数或执行上下文，禁止提升为单例或让并行 Workflow 相互覆盖。此项必须在提高并行度前完成。
- `workflow_execution_state_service.py` 不依赖模型节点服务或工作流编排服务；`agent_model_node_service.py` 可以调用状态服务，但状态服务不得反向调用 Provider。

调整过程保持小步验证：阶段3定向测试已经包含事件“提交后通知”、Provider“开始事件提交后调用”、终态事务可见性、原子费用预留、并行数据库会话和显式取消；状态写入、工作流结束时的状态收尾、模型调用、版本化工作流定义登记、运行时节点连接检查、节点顺序和审核分支均已具有明确负责文件。后续扩展新工作流定义时继续运行 Agent Workflow 测试、完整 API 测试和 Ruff，不为每个只包含一次简单调用的节点创建一个 Service 文件。

## 2026 年 8 月 24 日工作流 Token 缓存优化规划

本工作项是阶段3完成后的性能与费用优化，不更改阶段3“已完成”状态，也不改变模型输出、证据合同或审核语义。当前代码已经从 OpenAI、DeepSeek、OpenAI兼容、Anthropic和Gemini响应中归一化读取缓存用量，并聚合到Workflow用量；平台生成的Agent输入与Schema使用确定性JSON序列化，`llm_attempts.prompt_prefix_fingerprint`保存不含Prompt正文的稳定前缀SHA-256指纹。迁移`20260829_0018`增加Agent角色、缓存写入和缓存未命中事实；`GET /api/v1/internal/model-cache-diagnostics?run_id=...`可按Provider、Model、Agent角色和指纹返回观察窗口内首个请求与后续请求的命中基线。DeepSeek和OpenAI兼容调用继续依赖供应商自动前缀缓存；Anthropic适配器归一化读取`input_tokens`、`cache_read_input_tokens`和`cache_creation_input_tokens`，但不发送`cache_control`；Gemini适配器读取`cachedContentTokenCount`，但不创建或引用显式缓存对象。

供应商事实以官方文档为准：

- [DeepSeek 上下文缓存](https://api-docs.deepseek.com/guides/kv_cache/)默认启用并按重复前缀命中，响应提供命中与未命中 Token；命中属于尽力而为，不能作为业务正确性的前置条件。
- [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)要求精确前缀匹配，建议把稳定指令、示例、工具和结构化输出 Schema 放在前面，把用户输入、请求标识和时间戳放在后面；不同模型存在最低可缓存前缀长度。
- [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)支持顶层自动缓存和内容块显式断点，缓存前缀顺序为 tools、system、messages；默认生存时间为 5 分钟，显式断点放在变化内容之后会持续写入却无法复用。
- [Gemini Context Caching](https://ai.google.dev/gemini-api/docs/caching)对 Gemini 2.5 及更新模型默认启用隐式缓存；显式缓存依赖具体 API，并非所有 Gemini 调用接口都支持。

### 当前需要先验证的基线

- 以一次模型调用为最小统计单位，记录 Provider、Model、工作流定义版本、节点角色、输入 Token、缓存命中 Token、首 Token 延迟和总耗时；不得用跨模型、跨节点角色的单一平均值判断优化效果。
- 统一基础命中率口径为“缓存读取 Token / 供应商归一化后的总输入 Token”，同时保留供应商原始用量证据。OpenAI 兼容与 Gemini 可从总 Prompt Token 中计算；Anthropic 的 `input_tokens`、缓存读取和缓存创建字段语义不同，必须先将三者归一化后再计算，不能直接使用当前 `cached_tokens / input_tokens`。供应商提供缓存写入或未命中 Token 时，先增加兼容读取与诊断，不修改既有 `cached_tokens` 含义。
- 对同一模型和节点角色执行冷请求、等待首个响应开始后的相同稳定前缀请求、仅改变动态后缀请求三组样本；缓存未达到供应商最低长度、缓存尚未建立或已过期时必须单独标记，不能归类为序列化缺陷。
- 生成安全的前缀指纹用于比较相邻请求首次分歧位置。指纹不得保存 Prompt 正文、用户内容、密钥或可逆缓存键，原始 Provider 证据继续遵循现有 MinIO 和脱敏边界。

### 稳定前缀与请求合同设计

- 保持稳定的系统指令、节点输出合同、示例、工具定义和共享参考材料在动态内容之前；用户输入、Workflow/Run/Task/Attempt 标识、时间戳和节点结果等变化内容放在后缀。仅用于日志的标识移出 Prompt，不以改变模型语义为代价追求缓存。
- 对提示式 JSON 中由平台生成的 Schema 投影和结构化模型输入使用确定性序列化：键顺序、数组顺序、空白和数字表现保持稳定。不得重排有语义的对话消息、证据列表或用户文本。
- 为稳定指令与 Schema 建立明确版本；内容修改应有意生成新版本和新指纹，禁止为了维持命中而继续发送已经失效的合同。
- 先用现有自动缓存验证稳定前缀收益。只有供应商能力目录声明支持且真实样本证明有收益时，才扩展 Provider 无关的缓存策略合同，并分别映射 Anthropic 缓存断点或 Gemini 显式缓存；工作流层不得按 Provider 名称拼接私有字段。
- 显式缓存必须定义作用域、生存时间、费用、失效和删除责任。缓存写入失败或未命中只能回退为普通模型输入，不能改变节点成功条件、权限判断、审核结论或最终回答。

### 测试与完成条件

- 先新增失败基线测试：语义相同但平台生成 JSON 键顺序不同会产生不同前缀指纹；实现确定性序列化后，稳定输入生成相同前缀，动态后缀变化不会改变稳定前缀指纹。
- Provider 合同测试覆盖：自动缓存不增加私有请求字段；能力不支持时不发送显式缓存配置；支持时只由适配器映射；命中、未命中、缓存创建和缺失用量都不会被误报为零成本或业务失败。
- 工作流回归覆盖 Controller、Worker、Reviewer 和最终汇总的输出合同、用量聚合、审核与最终消息，证明缓存优化不改变语义和持久化事实。
- 使用至少一个自动缓存 Provider 完成真实重复请求对照，记录稳定前缀长度、命中 Token 和首 Token 延迟；Anthropic 或 Gemini 显式缓存只有在对应凭据和已注册模型能力可用时作为可选集成验证。
- 完成条件：能够定位低命中来自前缀过短、前缀变化、TTL、供应商路由或能力不支持中的哪一类；相同 Provider/Model/节点角色的稳定前缀命中率可查询；优化前后语义回归通过；没有新增敏感 Prompt 日志。未获得可重复收益时保留自动缓存和诊断能力，不引入显式缓存的资源生命周期复杂度。

实施顺序：

| 步骤 | 修改对象 | 预期结果 | 验证方式 | 状态 |
|---|---|---|---|---|
| 1 | 现有 Attempt/Workflow 用量读取与诊断 | 得到按 Provider、Model、节点角色分组的首个/后续请求基线，并纠正跨供应商总输入口径 | 固定样本单元测试、原始用量对照和本地查询 | 已完成：新增Run范围内部诊断、Agent角色、缓存写入/未命中事实和Anthropic总输入归一化 |
| 2 | Agent 稳定前缀和平台生成 JSON 序列化 | 静态指令与 Schema 在前，动态内容在后；相同语义的稳定前缀字节一致 | 先行失败测试、前缀指纹快照和工作流语义回归 | 已完成：映射键和空白确定性序列化，动态消息不参与稳定前缀指纹 |
| 3 | Model Gateway 能力目录与 Provider 适配器 | 只在能力明确且有收益时增加 Provider 无关缓存策略，由适配器映射供应商字段 | Provider 请求合同、能力拒绝和用量归一化测试 | 条件未满足：当前只保留自动缓存和统一用量诊断；在步骤4证明收益前不增加显式缓存请求合同 |
| 4 | 真实供应商对照与启用决定 | 记录命中、延迟、费用和失效行为；无可重复收益时不启用显式缓存 | 重复请求集成测试、敏感字段扫描和结果等价评估 | 暂停：需要部署凭据、可能产生费用的真实调用授权和已登记模型；不属于阶段3离线完成条件 |

2026年8月27日完成步骤2及步骤1的指纹证据部分：Agent节点对平台生成的模型输入、提示式JSON Schema和原生输出Schema使用统一确定性序列化；Model Gateway根据实际系统指令、工具和输出合同生成稳定前缀指纹，并在Provider调用前保存到Model Attempt。指纹不包含动态消息或Prompt正文。

2026年8月29日完成步骤1剩余离线范围：平台统一`input_tokens`为完整有效输入，Anthropic以普通输入、缓存读取和缓存创建之和计数；`cached_tokens`保持缓存读取的既有含义，新增`cache_write_tokens`和`cache_miss_tokens`。Agent调用把角色保存到Attempt，内部缓存诊断按Run、工作流版本、Provider、Model、角色和稳定前缀分组，并分别返回观察窗口内首个和后续请求的Token命中、首个可见Token延迟和总延迟统计。价格目录可选配置`cache_write_input_per_million`，使缓存创建不再按普通输入或缓存读取重复计价。本地MySQL已从`20260828_0017`向前迁移到唯一head`20260829_0018`。真实供应商缓存收益和显式缓存请求策略仍未验证或启用；这是有意遵守“先证明收益再增加生命周期”的停止条件。

## 测试设计

公共节点合同、状态、预算、并发和幂等必须测试先行。

### 当前已验证

2026 年 9 月 12 日在 `apps/api` 目录重新运行 `.venv/bin/pytest tests/test_agent_workflows.py tests/test_agent_runtime_architecture.py tests/test_workflow_definition_registry.py -q`，结果为 `68 passed`。本次使用隔离 SQLite、假 Provider 和内存对象存储，核对同步工作流、审核约束回答、上下文与思考配置透传、Handoff、并行、幂等、取消、持久化和定义登记；没有调用真实 Provider、MySQL、MinIO 或浏览器，也未验证跨进程恢复。以下为历史实施及验证记录。

2026 年 8 月 13 日已在 `apps/api` 目录运行 `.venv/bin/pytest tests/test_agent_workflows.py tests/test_agent_runtime_architecture.py tests/test_workflow_definition_registry.py -q`，结果为 `39 passed`。当前定向测试覆盖：完整串行主流程和持久化节点、真实并发信号、并行会话隔离、Run 到 Workflow 发现、幂等回放与冲突、能力拒绝、调用前预算和价格拒绝、并发费用预留、有序事件回放、依赖 Handoff、Evaluation 归属、非法 Worker 输出清理、实时 SSE、显式取消及重复取消、Reviewer 绑定/跳过/拒绝/重试、完成原因校验、最终消息事务回滚、终态事务可见性、Provider 超时与请求中取消的未知结果、非法事件游标、OpenAPI JSON/SSE 合同、未注册输出版本、Handoff 所属 Task 校验，以及节点开始/事件事实先提交后调用 Provider 或通知 SSE observer 的事务顺序。

工作流定义登记另有 4 项单元测试，覆盖固定节点和按任务生成的节点、重复工作流定义、未知输出类型、未知连接目标、运行时未知节点、节点类型不一致和非法节点连接。

2026 年 8 月 13 日运行完整后端测试为 `212 passed, 4 skipped`，Model Provider 包测试为 `28 passed`；4 项跳过项是需要真实 MySQL/MinIO 环境变量的基础设施测试。`ruff check` 与本阶段修改文件的格式检查均通过。Alembic 当前唯一 head 为 `20260813_0011`，面向 MySQL 的 `alembic upgrade head --sql` 离线迁移生成通过，并包含工作流费用预留字段。真实供应商调用需要部署环境密钥，保持为可选冒烟验证，不把外部供应商可用性作为离线自动化完成条件。

2026年8月20日的真实运行曾产生3352字节Handoff，并因当时的1000字节合同在`handoff_submission`节点失败；之后一度通过限长投影规避。2026年8月29日按当前产品决定移除了Handoff独立输出上限和投影逻辑：新工作流会把完整Worker结构化输出写入Handoff，内部独立Handoff接口也不再按字节数拒绝；原失败Workflow仍作为不可变历史事实保留。固定字段、状态、归属、隐藏推理禁入以及Worker节点64 KiB输出合同保持不变。

2026 年 8 月 20 日补充回归确认：DeepSeek prompted JSON 曾仅依赖 Schema 提示词，真实 Worker 响应虽以 `finish_reason=stop` 完成，却因末尾数组缺少 `]` 而产生 `node_output_invalid`。当前 Agent 调用使用独立的 `json_object_output` 合同，DeepSeek 适配器映射为官方 `response_format: {"type":"json_object"}` 并在 Provider 边界解析；节点层继续执行原有 Pydantic Schema 校验。普通 JSON object 与原生 JSON Schema 仍是两个能力，不允许静默降级。真实浏览器回归已完成 10 个节点和 3 次模型调用，刷新后最终消息与节点事实均可恢复。

2026 年 8 月 20 日补充合同修复：后续真实 Worker 响应已经是完整 JSON，却把 Schema 元数据 `additionalProperties` 误写成业务字段 `additional_properties`，因严格合同禁止额外字段而失败。修复位于 Provider 无关的 `prompted_json` 指令构造层：提示明确列出唯一允许的顶层响应字段，声明 Schema 关键字不是响应字段，并从面向模型的 Schema 投影中移除 `additionalProperties` 与 `title` 元数据；原始 Pydantic 合同及 `extra="forbid"` 校验保持不变。该行为不按 Provider 名称分支，所有不具备原生 JSON Schema、使用提示式 JSON 的模型共享同一协议。Agent 工作流 38 项定向测试通过，其中新增回归覆盖字段白名单、Schema/响应边界、元数据剔除和普通解释任务的知识边界。

2026 年 8 月 21 日补充知识边界修复：统一 Agent 指令原先要求 `Use only the supplied content`，导致普通解释问题也被错误当成必须提供外部资料的证据任务；Worker 只能报告资料缺失，Reviewer 因目标未回答而拒绝。当前 Provider 无关策略将已提供内容视为权威上下文，并允许普通解释问题使用模型已有的通用知识；任务明确限制来源时仍必须遵守，且任何模型都不得伪造工具、文件、测试、网络请求或外部验证事实。Playwright 使用真实 DeepSeek 对同一问题完成 15 个节点和 6 次模型调用，三个 Worker、Reviewer、最终汇总和消息关联全部成功，最终回答无需刷新即可见。后端与模型适配层全量验证为 `264 passed, 4 skipped`，Ruff 全量检查通过。

2026 年 8 月 24 日曾把 Reviewer 作为最终汇总门禁：只有不存在错误级发现、重试或重新规划要求的 `needs_revision` 才能生成最终消息。2026 年 9 月 2 日根据真实故障修正该合同：有效 Reviewer 输出只约束答案的可信范围，不再控制用户是否收到回答；`fail`、错误级发现、重试和重新规划要求均进入 Final synthesis，并要求明确限制、缺失证据和未完成工作。Evaluation 仍准确记录失败，确定性验证仍不能被 Reviewer 覆盖。新增三项回归与既有 Reviewer 合同合计 `7 passed`。

2026年8月29日补充稳定模型思考合同：工作流创建请求新增统一`reasoning`字段，Controller、Worker、Reviewer和Final synthesis均透传同一选择；省略时由各角色登记模型解析为“启用 + medium”。新工作流在写入工作流、事件或可计费调用前预检全部角色模型，未登记、已禁用、不支持等级或不支持关闭均明确失败；精确幂等回放不因模型策略后来变化而重跑。每个Attempt继续保存统一配置、来源、模型策略版本和Provider原生值。Agent定向测试为`55 passed`，后端与模型包全量测试为`295 passed, 4 skipped`，Ruff全量检查通过。

2026年8月29日阶段3收敛验证：新增实时事件observer失败隔离、Workflow节点用量与Attempt明细对账、跨Provider缓存用量归一化、Run范围缓存诊断和`20260829_0018`迁移。后端与模型适配层全量测试为`301 passed, 4 skipped`，4项跳过仍是需要显式真实MySQL/MinIO条件的基础设施测试；Ruff全量检查和迁移离线SQL通过。本地MySQL当前为`20260829_0018 (head)`。`alembic check`只继续报告实验性Memory表历史迁移的`SET NULL`与当前ORM `RESTRICT`差异，没有报告本次Attempt字段或索引漂移；该既有差异不属于阶段3，继续按阶段2Memory暂停边界保留。

以下各节保留阶段3回归边界和后续执行配置扩展时需要补充的测试方向；阶段10的进程恢复、阶段7的工具执行、阶段6的遥测装配和阶段5的用户权限测试不属于阶段3未完成项。

### 节点合同

- 每个 `output_type` 正常、空列表、nullable 字段、额外字段、缺失字段、错误类型和字节上限。
- 所有节点返回通用信封全部字段；POST、GET node 和 GET result 使用同一完整节点类型。SSE 只需验证其投影 ID、状态和详情路径指向同一持久化节点，不要求内联完整信封。
- 当前节点 `output` 超过 64 KiB 时明确失败，不截断 JSON，也不自动转换为 Artifact；未来引入大型结果引用时，再增加引用完整性与读取失败测试。
- 检查禁止字段：chain-of-thought、scratchpad、完整 prompt、Authorization、API Key 和原始凭据不得进入节点结果。

### 状态、幂等和恢复

- 当前同步范围：合法/非法状态转换、重复创建、重复节点完成、取消竞争和终态保护。
- 同一 idempotency key 同内容返回原结果，不同内容返回 409。
- 当前验证模型调用和持久化失败的已知/未知结果处理；没有显式恢复接口，不承诺重试会恢复不可确认节点。
- 阶段10后续测试（未实现）：等待输入版本冲突、显式恢复不重复计费调用，以及 API 重启后的运行中节点审计。当前进程中断仍可能留下 `running`，不能将目标行为写成已经验证。

### 计划和分派

- 目标态的简单任务直达，以及复杂任务 DAG、未知角色、依赖环、任务上限、深度上限、能力不可用和预算超分配。当前 Request Intake 固定按复杂任务拆解，不存在直达分支。
- `model_only_v1` 拒绝 implementer、tool、write、execute、network 和 external side effect。
- Task/Agent Run 创建部分失败全部回滚；并行节点各自使用独立数据库会话。

### 验证、审核和汇总

- 程序验证失败不能被 Reviewer 改成通过；无证据声明进入 unverified。
- Reviewer 只读明确输入，不读取 Worker Working State；所有有效审核结果保留 Evaluation。`1.0.0` 直接交给 Final synthesis，不自动返工；`1.1.0` 的返回 Controller 和有限节点修正按本文当前规则验收，不能跳过字段、权限和来源检查。
- Final synthesis 引用正确 Handoff/Evaluation/Artifact；Message 保存失败不再次调用模型。
- 汇总 totals 与 Attempt 明细不一致时明确失败，不使用默认零值。

### API、SSE 和前端恢复

- 同步返回所有完整节点；节点 sequence 稳定；结果快照版本一致。
- SSE 乱序、重复、断开、事件过大降级为查询引用、最终事件缺失和 after-sequence 恢复。
- 浏览器刷新后从 MySQL 事件和 result 恢复，不从计时器猜测进度。
- 当前工作流及阶段5测试分别检查内部引用关系与访问主体归属；新增两轮节点、原文查询和未来恢复接口继续要求跨用户查询、创建、取消拒绝测试。已实现 FastAPI 主体授权，目标部署完整矩阵仍待验证，不能用本地引用检查替代。

### 可观测性

- trace/span ID 关联节点、Attempt 和 Evaluation；exporter 故障不改变工作流结果。
- span attribute 扫描不含 Prompt、完整输出、工具参数、用户文件或 secret。
- 采样不会影响 MySQL 业务事件完整性。

## 实施步骤

| 步骤 | 修改对象 | 当前状态 | 预期结果 | 验证方式 |
|---|---|---|---|---|
| 1 | Node Result、各节点 Output 和 Workflow Result 合同测试 | 已完成 | 类型化输出、未注册版本拒绝、API 序列化和大小限制已验证；不为每个无独立规则的字段机械复制测试 | Pydantic、API 序列化和大小限制测试 |
| 2 | 工作流/节点/事件状态与迁移设计 | 已完成离线门禁 | 状态、唯一性、引用、版本约束和费用预留字段可执行；真实 MySQL 作为部署前环境验证 | Alembic head、MySQL 离线 SQL、SQLite 集成测试和可选真实 MySQL 升级 |
| 3 | 工作流定义登记、预算和节点编排职责调整 | 已完成 | 状态服务、模型节点服务、成功/失败/取消状态收尾、版本化工作流定义、原子费用预留和节点顺序职责均已实现 | 架构依赖、工作流定义、工作流执行、预算并发测试和 Ruff 检查 |
| 4 | 请求、上下文、Controller plan 和 plan validation | 已完成当前执行配置 | Controller 统一产生可验证任务图；简单请求由一个工作 Agent 执行，不增加绕过节点记录的隐藏直达路径 | 假 Provider、任务图、能力和预算测试 |
| 5 | Task/Agent Run 分派与 model-only 工作 Agent | 已完成 | 依赖分组、最多两个工作 Agent 并行、每个 Provider 调用独立会话及 Attempt 关联已实现 | 分派、依赖输入、Attempt、真实并发信号和费用预留冲突测试 |
| 6 | Handoff、确定性验证和 Reviewer | 已完成 | 下游只消费有效证据；确定性验证失败阻止汇总，有效 Reviewer 结果约束回答但不吞掉回答 | Handoff/Evaluation 归属、审核约束最终回答和非法输出失败测试 |
| 7 | Final synthesis、completion 和 Assistant Message | 已完成 | 最终消息与汇总节点一致提交；完成节点、Workflow/Run 终态和两个完成事件使用一个事务；全量聚合可查询 | 消息回滚、终态原子可见性和汇总查询测试 |
| 8 | HTTP/SSE、result 快照和恢复事件 | 已完成 | POST 实时事件、完整结果、持久事件回放和幂等显式取消可用；当前工作流没有等待状态，因此不提供空 `resume` 动作 | ASGI、SSE sequence、实时流、游标、非法游标、取消竞争与 OpenAPI 合同测试 |
| 9 | 阶段6遥测关联边界 | 已完成本阶段边界 | trace/span 字段和敏感内容边界已固定；实际 exporter 由阶段6装配 | Schema 和持久化字段检查 |
| 10 | `model_only_v1` 基础设施与供应商验证 | 已完成离线门禁 | 快速测试、迁移链和 MySQL 离线 SQL 可验证；真实供应商调用必须使用部署环境密钥，保持可选 | 完整后端测试、Alembic 检查、可选供应商冒烟 |
| 11 | 阶段7恢复后的 Tool Runtime 集成 | 不属于阶段3完成条件 | 新 `tool_enabled_v1` 复用结果合同和隔离边界 | 阶段7工具循环、写隔离、取消和未知结果测试 |
| 12 | 统一Context Builder后端接入 | 已完成阶段3范围 | 已实现当前Message锚点、Session文本历史、每个角色模型的独立Context Build、Prompt Release、受限文本Artifact、Agent/Handoff/Evaluation类型化来源、去重和Attempt/Node证据；Memory、Knowledge、Tool和阶段4产品入口不属于本工作项 | 第二轮会话、并发锚点、完整轮次、Prompt/Artifact、Agent协作来源、当前目标单次出现、对象失败和节点证据测试 |
| 13 | 稳定模型思考配置透传 | 已完成 | 工作流级选择或逐模型medium默认进入所有模型角色；新工作流预检全部角色；Attempt保存最终解析证据；幂等哈希包含思考选择 | 显式等级、默认、关闭、角色不支持、未登记模型、Attempt证据和幂等冲突测试 |
| 14 | 版本化公开活动摘要 | 已完成 | 后端从确定性输入、类型化输出和安全错误合同生成节点活动摘要；快照与事件使用同一事实；前端显示模型所在工作阶段但不暴露隐藏推理 | 模板、边界、敏感信息、事务一致性、历史兼容、事件恢复、并行节点和前端可访问性测试 |

## 失败与恢复设计

### 当前实现

- 节点开始事实和开始事件提交成功后才调用 Provider；提交失败时不进入模型调用。
- Provider 的超时、网络/连接错误、无法确认的内部错误或请求中取消会使当前 Node 与 Workflow 进入 `outcome_unknown`；已启动的 Task、Agent Run 和 Agent Turn 被收敛到其现有可表达的终态。
- 节点输出校验失败、计划拒绝、确定性验证失败或其他执行错误会终止当前 Workflow。有效 Reviewer 输出中的明确失败、错误级发现、重新规划或重试要求不单独终止工作流：它们使候选 Evaluation 失败，并进入最终汇总，要求回答如实说明限制与未完成工作。
- SSE observer 在事件提交后收到通知；通知失败会记录工作流、事件和序号后解除当前请求级observer，已提交事实和后续工作流执行不受影响。`asyncio.CancelledError`不被该隔离逻辑吞掉，仍进入既有取消收敛。
- Final synthesis 的 Assistant Message 与最终汇总节点完成事件共用事务；测试已验证最终节点无法持久化时不会遗留成功消息。
- 当前没有跨请求继续、等待输入、resume 或启动审计。同步请求所在进程意外退出仍可能留下 `running` 事实；该问题需要阶段10的可靠执行与恢复机制，不能由 API 进程内后台任务安全解决。

### 目标态

- 模型返回成功但节点结果持久化无法确认时，保存可确认的 Attempt 关联并进入 `outcome_unknown`，不得自动重试计费调用。
- 并行组部分失败时，已进入供应商边界的调用分别收敛；成功结果先保存 Attempt、Node 与 Handoff，随后由失败节点终止工作流。父级验证不会在必要子节点结束前开始。
- Context、Prompt、Evaluation 或 Artifact 服务接入后不可用时，返回明确依赖错误，不用空数据伪装完整上下文。
- 未来版本若新增等待用户输入，必须同时定义 `expected_workflow_version`、输入幂等 key 和恢复测试；当前版本不预留一个没有运行语义的接口。
- 阶段10恢复后，启动审计必须识别进程中断遗留的 `running` 节点，并依据是否存在不可确认外部调用决定失败、取消或未知结果；不得伪造恢复完成。

## 风险与停止条件

- 节点返回合同若仍允许任意自由 `dict`、省略公共字段或把大型文本无限内联，停止实现并先修正 Schema。
- 要求展示模型隐藏思维链、系统 Prompt、完整 Provider 原始响应或敏感工具参数时拒绝实施；改为可公开决定摘要和证据引用。
- 当前 `model_only_v1` 要求 Implementer、文件修改、Shell、Git 或 MCP 时，返回能力不可用；阶段7专用解析执行器不授予这些模型能力，不能在 API 进程临时执行命令绕过工具授权。
- 共享一个 `AsyncSession` 给并行 Agent、在模型 HTTP 调用期间持有数据库事务或允许并行超卖预算时，停止实现。
- 需要请求结束后继续、自动崩溃恢复或跨进程 Agent 时，必须重新评估阶段10，不使用 `BackgroundTasks` 假装可靠执行。
- OpenTelemetry 实验性 GenAI 属性发生变化时，只调整阶段6映射，不改变节点数据库合同。

## 完成标准

### `model_only_v1` 与阶段3

当前状态：已完成。自动化测试已经验证主流程、完整节点合同、核心 HTTP/SSE、幂等、有界并行、原子费用预留、显式取消、审核证据约束、最终消息回滚、终态一致提交、超时和未知结果。

- Controller、Planner/Researcher、确定性验证、Reviewer 和最终汇总可在同步请求内完成。
- 每个执行节点返回完整 `AgentWorkflowNodeResult`，所有节点特有输出通过判别联合校验。
- POST、节点详情和 result 查询返回一致的完整节点合同；SSE 返回可恢复公共投影和权威详情路径；result 可以返回该工作流全部节点参数。
- 计划、并行、预算、固定单轮 Worker 限制、取消、失败和未知结果通过测试；当前定义没有等待输入或恢复分支。
- 不读取 Memory Packet/Knowledge，不执行工具，不伪造文件、测试或外部证据。
- 业务事件保存在 MySQL，trace/span 关联字段已经预留；阶段6装配 exporter 时必须验证故障不影响运行。
- 两个无依赖模型工作 Agent 可以有界并行。写任务、工具调用和独立 Workspace 仍明确不可用，等待阶段7以新的版本化执行配置实现。
- Model Attempt、Handoff、Evaluation 和 Node Result 均能从 `run_id` 查询并相互关联；阶段7加入 Tool Call、Artifact 和程序测试证据时沿用同一引用方式。
- 工作流级思考选择会一致传入Controller、Worker、Reviewer和Final synthesis；省略时每个角色都使用自身登记模型的版本化medium默认。任一角色不支持时在创建工作流事实和调用Provider前失败，Attempt能够重读最终统一等级、来源、策略版本和Provider映射值。
- Workflow Completion创建前会把全部节点用量与该工作流Model Attempt明细独立对账；缓存诊断可以在不读取Prompt正文的情况下按Run、Provider、Model、Agent角色和稳定前缀查询。

新增公开活动摘要是已完成的兼容增强，其单独完成条件以上文“测试与完成条件”为准；它不改变阶段3原完成条件。

## 官方依据

- [OpenTelemetry Signals](https://opentelemetry.io/docs/concepts/signals/)：区分 traces、metrics、logs 和 baggage，阶段3据此不把业务事件与遥测混为一体。
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)：当前 Python traces 和 metrics 稳定，logs 仍在开发，阶段6首批优先 traces/metrics。
- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)：确认手工创建嵌套 span、属性、事件、错误和指标的装配方式。
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)：语义约定提供公共命名，但实验性约定可能变化，数据库合同不能直接依赖不稳定字段名。
- [OpenTelemetry GenAI attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)：工具参数和结果等属性可能包含敏感信息，不默认写入 span。
