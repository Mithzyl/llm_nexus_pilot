# Personal Assistant 与 NexusPilot 本地接口及验收

## 本机新增兼容 API 配置（2026-11-06）

按本机管理员提供的地址新增 `openai_compatible` 端点
`https://mithfusion.site/v1`，模型为 `tiel-coder-35b-a3b-ud-q4_k_xl`。
使用独立服务器密钥，仅保存在 Git 忽略的 Nexus 私有 `.env`，不替换 App 入站密钥或
Assistant 回调密钥。原 OpenCode Go 地址、模型和密钥保留。
远端认证 `/v1/models` 返回 HTTP 200，声明 `meta.n_ctx=163840`，据此登记上下文窗口。
工具、结构化输出、图片、缓存及流式能力尚未进行生成验证，目录中的能力标记保持保守值，
不能把模型目录可读等同于实际 Agent 任务通过。

Nexus API/Worker 已在无活动任务时平滑重载；健康检查和在线模型目录均 HTTP 200，
新模型 `registry_available=true`、`selectable=true`。没有调用生成模型或迁移 Nexus 架构。
经管理员授权，已重新备份两端私有配置、Nexus 数据库及 Assistant SQLite，
执行 Assistant `0026_agent_response_receipts` / `0027_browser_push` 增量迁移。
原有表的记录数量和主键保持一致，Assistant `.env` 未改动。
Nexus 的 `personal_assistant.response_processing_path` 已配置为
`/internal/nexus/agent-responses`，其余 App 配置及双向凭据保留且匹配。
两端 API 和 Worker 已重载，Assistant scheduler 已重启并有新心跳；
Assistant 健康、OpenAPI 和受保护的工具目录读取均 HTTP 200，新正文回调路由已加载。
正常 Assistant 进程现已加载新 controller 绑定，通勤自动 AI、自动天气和浏览器推送仍关闭。
本次启用未调用模型；真实生成和完整 Agent 业务任务尚未验证。

首次登记兼容模型发现默认空推理配置被误判为损坏的 medium 配置，已将兼容网关与
现有无推理控制网关同样处理：省略控制时保持供应商默认，显式未配置控制仍拒绝。
定向用例 `tests/test_model_reasoning_policy.py::test_compatible_gateway_default_does_not_invent_reasoning_controls`
先复现失败，修复后 1 项通过；未运行额外回归或哈希检查。
本机备份及不含密钥的执行记录位置见
`/private/tmp/nexus-mithfusion-config-20261106/configuration-result.json`，原账号和业务记录保留。
迁移启用记录位于 `/private/tmp/assistant-agent-enable-20261106/`；
备份目录为本机私有运行目录下 `backups/agent-enable-20261106-165848`。

## 平台处理正文协议规划

### 调用平台显式选择工具入口（2026-11-06）

状态：代码已修改，固定输出定向检查通过，修复后的真实任务已验证正文续接；
完整路线和卡片任务仍未通过，未部署。
当日真实 OpenCode Go `deepseek-v4.1-flash` 可用性检查返回 `NEXUS_READY`。
随后由隔离 Assistant 网页提交的路线任务使用独立 API、Worker、SQLite 和本地 MinIO，
主 Agent 首次通过正文协议查询通勤成功，随后改用 Nexus 原生 `search_tools`，最后发出
占位 `request_user_input`，未完成待办保存和路线规划；4 次模型调用后取消，保留记录。
这说明此前仅凭应用文字约束原生工具不足，不能将前日固定输出结果当成真实任务已通过。

新增可选创建字段 `application_native_tools_enabled`，默认 `true`，保留已有协议：
原生调用仍正常分派，没有原生调用的正文才交给调用平台。
`false` 仅允许与 `caller_processed` 一起使用；该主 Agent 不接收原生工具定义，
意外返回的原生调用在执行或创建人工追问之前失败。应用仍通过原正文处理连接执行业务，
不会将 Assistant 操作名称加入 Nexus 静态分支。此选项不改变审核 Agent 的原有工具行为。
Assistant 业务工作流显式传 `false`；允许附件等 Nexus 原生能力的其他调用可保持默认。
策略创建后固定，相同请求省略默认值仍复用原运行，切换策略返回 409，不续建任务。

定向检查：`tests/test_application_body_processing.py::test_body_result_continues_original_agent`、
`::test_caller_native_call_is_rejected_before_tools_or_human_wait` 及
`tests/test_application_tool_loop.py::test_app_run_reuses_loop_and_remote_result`，
明确以测试进程 `NEXUSPILOT_WORKFLOW_EXECUTION_BACKEND=inline` 执行，11 项通过；
补充的两种策略重复请求/策略变更检查 2 项通过。
模型为固定输出，仅本地 HTTP，不增加真实模型调用。
本机证据位于 `/private/tmp/assistant-ecosystem-20261106/` 的
`nexus-protocol-checks.xml` 与 `nexus-policy-replay-checks.xml`。
后续部署须先加载支持该字段的 Nexus API 与 Worker，再启用发送该字段的 Assistant。

追加授权的一次真实复验使用 `application_native_tools_enabled=false`，正式 App Run
`b855cc20-01f0-4b84-91f4-25d045fd7eda`、Workflow
`24329c5c-65a0-4dff-a5c1-58abbc09e5e5`，独立 API/Worker、真实本地 MinIO，
两服务均为隔离 SQLite。12 次真实 OpenCode Go `deepseek-v4.1-flash` 调用，
原生工具调用 0、供应商重试 0，业务回调 12 次。通过正文协议自主查询、搜索地点及保存
两条待办，前 11 次完整提议和实际结果均进入原 Agent 后续消息；固定说明和原消息前缀保留。
第 12 份业务结果已存，但达到调用上限，没有第 13 轮读取。
调用平台当前高德适配器不支持任务选择的步行模式，模型改用驾车取得两段路线；
运行最终因 `agent_model_call_limit_exceeded` 失败，第三段与终答时间线卡片未完成。
这次记录证明工具入口选择及原 Agent 续接有效，不能代表 Assistant 综合功能通过。
现场证据：`/private/tmp/assistant-ecosystem-recheck-20261106/verification-summary.json`；
业务说明见 Assistant 的 `docs/backend-integration/27-接口复核与AI业务调用说明.md`。

### 工具指引适用范围与定向验证（2026-11-05）

上下文组装现为：Nexus基础规则→应用协议/本次目标→条件式通用工具指引。指引依据当前授权目录
生成，不宣称未配置的委派、笔记或文件能力已经开放；实际每轮工具Schema及服务端权限仍是调用依据。
目录描述能力，不要求例行调用。只有当前目标或缺失必要证据需要时使用；应用协议约束任务范围，
通用指引不能扩大范围，应用协议也不能扩大服务端授权。caller_processed明确区分调用平台的业务
接口说明与Nexus search_tools/describe_tools，后两者不会自动发现调用平台业务接口。

文件指引不再无条件要求Call these directly；必要且获允许、Schema已加载时才直接调用。
list_run_files仅用于依赖附件且缺少编号的任务，不为探测附件而例行调用。分页、邻近原文、引用及
阅读覆盖要求限定为文件阅读任务，不覆盖应用终答JSON格式。简单业务不要求笔记，不重复抄写消息
及平台结果；只有需要额外跨步骤工作状态才使用。阅读计划只用于文件任务，委派须任务需要且已授权。
本次没有改变Agent循环、平台回调、消息追加、工具权限或新增审批状态。

定向命令：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_agent_tool_guidance.py -q`，
4通过（2.29秒）。三个流程场景及一个未开放能力不被宣称的检查：

| 场景 | 模型调用 | 原生工具调用 | 平台HTTP处理 | 结果 |
|---|---:|---:|---:|---|
| Nexus明确要求核对本次附件 | 2 | 1 | 0 | list_run_files真实返回后完成回复 |
| Assistant协议要求本次不使用原生工具 | 2 | 0 | 2 | 提议→平台结果→JSON终答，同一Workflow |
| Assistant本次允许且要求先核对附件 | 3 | 1 | 2 | 原生结果成功返回后继续平台流程，同一Workflow |

检查实际Provider请求的system_instruction：无无条件Call these directly，基础规则/协议/条件指引
顺序正确，单个流程的系统说明保持不变，JSON终答未受自然语言要求覆盖。原生tool结果检查completed
及tool_call_id，平台结果仍沿用原追加机制。初次两个App场景因旧指引冲突失败；Native测试初次误用
已有fixture参数，改用实际用户/会话/Run接口创建，不降低断言。
证据为固定模型、SQLite/内存对象存储和本地HTTP模拟平台；没有付费模型、真实Assistant或浏览器验收。
不据此保证真实模型必然不多调工具；本次仅修正提示词，不把应用文字升级成新的服务端禁用权限。
后续真实模型定向验证仍需另行授权。此记录随实现提交；提交与推送状态以Git记录为准，未部署。

**2026-11-05，状态：进行中；下列创建字段及通用处理协议已首批实现，完整验收范围见下节实施记录。**
设计主定义见[阶段7](phase-7-tool-runtime-plan.md#调用平台处理主agent正文2026-11-05)。
本节仅定义Nexus与已认证调用平台的通用消息协议，不登记create_todo等静态业务工具，
不新增`/continue`或平台结果提交接口；原静态只读工具协议及普通运行保持兼容。

### 创建配置与固定输入

在现有`POST /api/v1/app/runs/{run_id}/agent-workflows`增加以下可选字段，
与现有general_agent版本、role_bindings、review_policy及幂等键一起使用：

```json
{
  "application_response_mode": "caller_processed",
  "application_native_tools_enabled": false,
  "application_protocol_instructions": "本应用的中间调用提议格式、业务操作说明和最终回答规则",
  "final_output_schema": {"type": "object"}
}
```

字段名与首批实现一致。省略模式时保持原行为；
只有general_agent主Agent支持caller_processed，且服务端必须批准此App使用通用处理连接。
application_protocol_instructions仅由已认证App服务端提交，不作为平台最高优先级指令；
调用说明描述中间业务提议，final_output_schema可省略，仅约束候选终答，不约束每次中间正文。
Schema拒绝外部引用；首批只支持type/properties/required/items/enum/additionalProperties，
最大序列化16000字符、8层嵌套、每层最多100属性；省略时仍需平台确认终答。
这不等于向供应商传response_format，不保证模型一次生成合法JSON。
创建时固定这些值，重复启动参数变化返回冲突，恢复不读取后来修改的说明。
执行地址、认证凭据及连接许可仅来自App私有配置，不接受请求正文/模型指定地址或用户身份。

### Nexus发送给平台

POST到已批准的通用正文处理地址，独立服务端Bearer凭据、application/json，禁止重定向。
不是现有按工具名查询STATIC_APP_TOOLS的请求，不能把本信封发到旧只读工具路由冒充兼容。

```json
{
  "protocol_version": "1",
  "application_id": "personal_assistant",
  "external_user_id": "运行映射中的用户",
  "run_id": "原Run编号",
  "workflow_id": "原Workflow编号",
  "response_id": "本轮已保存模型响应编号",
  "execution_id": "平台处理稳定编号",
  "content": "{\"decision\":\"call\",\"call\":{\"operation_id\":\"create_todo\",\"body\":{},\"summary\":\"...\"}}"
}
```

所有身份与编号来自Nexus记录；content是原始完整模型正文，不改参数、不摘要化、不取局部流式JSON。
只有正常完整响应可发送；截断/取消/流异常不能因部分JSON可解析而执行。平台解析decision/call和
业务参数，校验业务访问权限及用户批准；Nexus不理解operation_id。平台必须保存execution_id与
实际请求及执行结果，相同编号相同请求最多一次业务影响，不同请求返回协议冲突。
新编号下的语义重复创建仍需平台业务规则判断，不能仅靠网络请求去重解决。

### 平台同步HTTP返回

正常成功响应携带相同protocol_version、execution_id；通用外层采用严格字段校验，result为
有界业务JSON对象。HTTP处理结果与模型最终回答分开，不能把业务操作成功当成用户目标完成。

```json
{
  "protocol_version": "1",
  "execution_id": "原处理编号",
  "disposition": "continue",
  "execution_status": "succeeded",
  "result": {
    "operation_id": "create_todo",
    "executed_arguments": {},
    "data": {}
  },
  "error": null
}
```

result内部字段由平台定义，上述operation_id/executed_arguments/data只是示例，不成为Nexus固定业务Schema。
模型已经产生有效终答时：

```json
{
  "protocol_version": "1",
  "execution_id": "原处理编号",
  "disposition": "complete"
}
```

complete不得伴随业务执行结果，表示认可该response_id对应原正文为候选终答，不替换正文；
如本次真正执行了业务操作，返回continue，让模型读取实际结果再生成终答。
Nexus继续执行已保存的审核策略和最终结构校验，不能直接跳过Reviewer。审核后修改的正文必须重新确认。

| disposition / execution_status | 要求及Nexus下一步 |
|---|---|
| continue / succeeded | result为实际结果，error=null；追加消息，继续主Agent |
| continue / failed | error包含安全code/message、retryable；仅用于平台确认未产生业务影响的失败；retryable=false时停止自动执行 |
| continue / rejected | error包含安全code/message、retryable；可修正输入可为true，身份/权限/用户批准拒绝必须false；不得通过换参数规避 |
| stop / unknown | 结果或副作用不确定，停止自动执行，等待用户到平台确认；不作为普通错误让模型再创建 |
| stop / partially_succeeded | result记录已确认的部分副作用，error说明未完成部分；停止自动执行，由平台处理，不自动补偿或重试 |
| complete / 不出现 | 不带result/error/execution_status，认可当前正文，进入原审核/最终保存流程 |

unknown必须使用stop，result可为空或只含已知事实，error说明未知原因；其他未列字段组合拒绝。
failed/rejected不带成功result，不能把部分执行当作未执行；已确认的部分副作用使用partially_succeeded。
error.retryable只是平台建议且须符合上表，不覆盖Nexus取消、期限、权限和无进展判断。
平台不得回传凭据或不属于该用户的数据。Nexus只检查通用格式/关联/大小，不认证业务事实正确性。

### HTTP异常、取消与恢复

- 确定在发送前的本地配置/输入拒绝：未执行。不得用一次普通连接异常推断未执行。
- 请求可能发出后超时、断连、HTTP错误、响应过大、非法JSON、execution_id不符：结果未知。
  不因HTTP500、429或返回非法正文自动重试；已有只读适配器的错误分类不能直接套用。
- 平台明确未执行的业务错误应以正常HTTP响应和上述通用结构表达，不依赖HTTP错误推断副作用。
- 显式取消传播到HTTP等待，停止后续调用；不承诺平台撤销。迟到确定结果可保存事实，但不能复活CANCELLED。
- 处理结果先保存再追加原消息。事件/结果读取和重连均只读；结果未知只展示需要到平台核实，
  本批不增加结果提交、业务查询或自动继续入口。
- 预算和期限沿用原任务，不因等待/恢复重置。平台去重与Nexus执行所有权都必须验证，
  不把“稳定编号”宣称为已实现恰好一次。

### 两端职责与验收交接

Nexus负责可信运行身份、原正文保存、一次处理记录、HTTP传输、结果追加、恢复/取消及审核。
App负责业务说明、业务解析与权限/批准、稳定编号去重、实际结果和有效终答判断。
application_result只是内部来源记录；适配器不得发送虚构供应商角色或无对应调用的role:tool。
后续模型保留原目标、说明、消息级私有续接状态和已发生事实；不得新建第二个Agent接续。
只保存代码或本地模拟成功不能宣称真实App业务接入完成。必要验收逐项采用阶段7清单，分别
记录单元/模拟HTTP、真实数据库与对象存储、固定模型浏览器和真实调用平台验证，不混同证据。

### 平台正文处理首批实施与验证

2026-11-05本地实现，未提交、未推送、未部署。私有NEXUSPILOT_APP_TOOLS_JSON中按App增加
response_processing_path（例如/internal/agent/body），沿用base_url/service_token及显式本地HTTP许可。
可以operations={}仅启用正文处理，不需注册业务operation；没有response_processing_path不允许该模式。
完整信封和结果各上限1MiB，HTTP等待使用当前任务剩余期限，不新增固定轮数或业务内容哈希。
迁移0051新增application_response_executions，只保存执行/Run/模型响应关联、状态、目的地址及对象引用；
全文保存在MinIO。原默认工作流请求身份保持兼容，没有增加/continue或结果提交接口。

验证脚本：`apps/api/tests/test_application_body_processing.py`。本地模拟平台保存合成待办结果，
固定模型第一轮返回完整create_todo提议，第二轮检查原始参数、summary、关联execution_id/response_id、
todo-C/open及提醒事实。两轮system_instruction逐字相等，保留历史A/B和本次请求，最终同一Run/Workflow。
三种审核策略均通过，Reviewer未调用平台；重复启动不再调用模型或平台。相同成功提议再次出现会在
发送前拒绝；非法平台响应进入outcome_unknown而非普通失败；在途取消后没有下一次模型或平台调用。
首批已保存结果的读取复用不等同于真实进程崩溃验收；后续四项专项已完成，见下节。
连接现在在创建时固定，审核修正及Schema异常也有专项证据。取消不会撤销模拟平台已保存的待办。
未修改Assistant。

实际命令与证据：
- `NEXUSPILOT_WORKFLOW_EXECUTION_BACKEND=inline apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_application_body_processing.py apps/api/tests/test_remote_tool_adapter.py apps/api/tests/test_general_agent.py -q`：40通过（20.72秒）。
- 设置既有NEXUSPILOT_TEST_AUTH_MYSQL_URL及MinIO测试变量、KEEP_DATABASE=true后，同一新测试文件：
  6通过（28.48秒）。使用真实MySQL/MinIO，但模型和业务平台仍为本地固定模拟，不是真实Assistant业务。
  保留数据库authz_audit_e25895a7c416425a97efbfad79fb4981、authz_audit_6db9e1b571c846778be04934a6b3628e、
  authz_audit_72bac8a9364d40ac95d1001907e2ca08、authz_audit_b024da50c2aa49e6b96c6155bb129765、
  authz_audit_df33f90dbfca42b29748fefff8e9a252、authz_audit_1340126d128749669cb9a7eea96b693f。
- 使用现有隔离数据库准备函数执行`alembic upgrade head`：退出0，数据库identity_acceptance_322df8739f07；
  正常本地库和服务器库未升级。ORM建表测试不作为迁移通过证据。
- 新测试文件加`apps/api/tests/test_application_tool_loop.py`：28通过（23.17秒），覆盖原静态只读App流程兼容。
  最后扩充固定头部及历史A/B的origin/backend_evidence/current_request输入，保留全部调用参数和summary，
  新测试文件再次6通过（5.29秒，SQLite/内存对象存储）。上方MySQL记录针对此前较简短的同等两历史样例，
  不冒充扩充文本后的重新执行。测试使用固定时间和固定模型，不验证真实模型对“6点10分”的语义判断。
- 首次红测显示未支持的新App配置使创建失败；实施过程中修正新表外键真实名称llm_attempts，
  固定模型检查system_instruction而非错误读取messages；Reviewer测试说明由空串改为现有要求的非空值。
  unknown终态测试先失败（错误地为failed），修正后通过。没有删除原有效断言。

目前不宣称整份平台处理规划完成；尚无真实App业务与浏览器验收、无付费模型测试。

### 四项定向验收（2026-11-05）

用户限定本次只验Worker故障/竞争、Reviewer修正、已声明Schema子集、创建时连接固定；
不执行Assistant验收或旧功能回归，不增加内容哈希。以下为本次实际执行，不与上节旧测试累计计数。

**实现修正：** 0052新增application_response_bindings，在工作流创建事务中保存Run、mapping_id和
非秘密连接字段（protocol_version/base_url/path/allow_local_http）。恢复及主Agent首次调用前
核对当前配置与固定字段，变化或缺少绑定均拒绝；凭据不入表，私有凭据轮换不改变目的地址。
不为0051旧caller工作流猜测绑定，旧记录缺少绑定时拒绝执行；普通模式不受影响。
Schema拒绝空子Schema、重复required、非有限数值等非法输入；枚举比较区分布尔与数字，
整数接受数学意义为整数的1.0。整个最终JSON先排除NaN/Infinity/溢出数值，再检查子集。
Nexus本地Schema错误明确标记来源nexus并保留平台complete事实，不冒充平台业务执行失败。
恢复发现dispatching且没有保存结果时，将处理记录写为unknown，不重发。

| 真实进程故障位置 | 恢复终态 | 模型调用数 | 平台HTTP请求数 |
|---|---|---:|---:|
| prepared提交后、发送前 | completed | 2 | 2 |
| dispatching提交后、HTTP发出前 | outcome_unknown | 1 | 0 |
| 平台已执行且HTTP返回、Nexus尚未保存结果 | outcome_unknown | 1 | 1 |
| 平台结果已保存、消息尚未追加 | completed | 2 | 2 |
| 消息已追加到内存、消息检查点提交前 | completed | 2 | 2 |
| 消息检查点提交后 | completed | 2 | 2 |

成功流程的2次HTTP分别为一次业务提议处理和一次候选终答确认，不是重复创建。每个故障进程真实
os._exit(86)，确认其退出后等待真实5秒租约自然到期，再启动新Worker；没有手动改过期时间。
两个Worker通过同一启动屏障竞争，只有一个取得执行权；最终2次模型调用/2次HTTP。
首调前更换目的路径的真实Worker验证：failed，0次模型调用/0次HTTP，没有转投新地址。
测试专用退出包装只存在于子进程fixture，生产代码没有故障开关。所有模型为固定实现，平台为本地HTTP
合成写服务，SQL/对象存储及Worker进程为真实本地服务；不代表真实Assistant业务接入或所有可能崩溃位置。

**命令与结果：**
- `apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_application_body_validation.py -q`：
  36通过，16.41秒。包含Reviewer先revise后supported，主Agent原提议/初稿/修订稿共3次平台确认、
  2次审核；最终保存修订原文。6类最终正文错误在同一Agent纠正成功；13类非法Schema、11类匹配语义、
  大小/层数限制、3类隐藏非JSON数值及创建绑定检查。
- 复用现有AUTH_MYSQL_URL/MinIO测试变量，KEEP_DATABASE=true，额外设置
  `NEXUSPILOT_TEST_CALLER_WORKER=1 NEXUSPILOT_TEST_EVIDENCE_BATCH=final-four-requirements`，执行
  `apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_application_worker_faults.py -q -s --tb=short -x`：
  最终8通过，71.97秒。逐项Run/Workflow/数据库/计数见
  [Worker验收记录](../../output/security/application-body-worker-20261105.json)中final-four-requirements批次。
  初轮7通过是修正前记录，不重复计入最终8项。
- 在新隔离库identity_acceptance_8504e8c1e7a0依次`alembic upgrade 20261105_0051`与
  `alembic upgrade 20261105_0052`，均退出0；正常开发库与服务器库未迁移。
- 新定向测试最初10失败/16通过，暴露Schema细节和缺失创建绑定参数，修正后通过。
  只执行本次相关检查，未执行Assistant、浏览器或全套回归。合成数据库/对象保留，不自动删除。

## App运行诊断接口（2026-10-30）

`GET /api/v1/app/runs/{run_id}/diagnostics`使用既有App服务端凭据和外部用户身份，
要求Run保存的mapping_id完全一致。浏览器Cookie、个人Key不能代替App认证；不增加BFF代理。
响应`Cache-Control: no-store`，只查询SQL结构化列，不加载MinIO正文：

- `application_id`、`run`（ID、状态、开始/结束时间）；`workflow`（工作流ID、状态、时间、错误码），尚未创建时为null。
- `attempts.items`：attempt_id、task_id、context_build_id、provider/model、agent_role、状态、时间、latency_ms、input/output/cached_tokens、错误码。未知用量保持null，不伪造为0。
- `tools.items`：tool_call_id、attempt_id、名称、版本、状态、时间和错误码；记忆读写工具也使用该关联记录。
- `limit`默认50、最大100，分别限制两组明细；`after_attempt_id`和`after_tool_call_id`为各组独立游标。
  按started_at及ID升序，`next_cursor`为本页最后ID或null；游标不属于此Run时404。

不返回用户问题、system prompt、模型/工具输入输出、记忆正文、外部用户ID、凭据、错误全文或对象存储地址。
不替代最终结果和事件接口；客户端仍通过原result/events取得自己有权访问的业务内容。
真实MySQL及HTTP测试见[诊断记录](../../output/security/app-diagnostics-20261030.json)。
Assistant目前只读，本批只交付Nexus接口，不声称Assistant诊断页面已经实现。

## 统一身份后的App运行隔离（2026-10-30）

App认证会在当前数据库会话记录认证mapping_id。对Run及其工作流、节点、交互、模型调用等
资源引用，除owner一致外，还检查保存的`run_application_context.mapping_id`与当前映射一致。
因此不同App即使通过统一身份使用同一owner，也不能读取、回放或取消另一App的运行；
原App的调用保持兼容。绑定/共享记忆不等于共享运行管理权限。
回归先复现另一App读取摘要返回200，修复后摘要、结果、事件和两种取消均404，原App取消成功。
真实MySQL限定验证见[记录](../../output/security/app-run-scope-20261030.json)。无模型调用、无Assistant代码修改。

## 统一登录与绑定当前状态（2026-10-31）

Assistant已由另一线程实现可选Nexus登录、关联入口及真实授权码兑换；原密码登录/注册保留。
已有隔离双端浏览器验收记录，详见下方“可选双端登录接入”。本线程复核了源码和该记录，
没有把另一线程的测试说成本轮重跑。正常环境默认关闭，本线程仍对Assistant只读。
App端必须由真实用户明确发起，再以已认证外部用户ID申请ticket，Nexus明确确认；
后台模型调用不代表同意。不按邮箱合并账号，不迁移历史owner，不以账号关联自动授权记忆或指令。

## 可选应用场景与用户指令（2026-10-29，Nexus后端已修改）

POST `/api/v1/app/runs`新增可选scene_id，省略保持兼容：只选择当前应用登记的default，
未登记不新增场景提示词。场景由内部接口绑定已启用的静态Prompt模板，App不能传任意模板或
借scene_id访问其他入口。Run固定模板版本；显式场景未批准时返回错误，不默默换用其他场景。
GET/POST `/api/v1/app/me/instructions`管理本入口用户偏好；App不能修改global偏好。
请求体为scope:"entry"、text、enabled、expected_version，allowed_entries保持空数组。
新增或修改指令只影响之后创建的Run；现有Run恢复使用原版本。Assistant已在19777602接入entry
指令页面，本线程复核读取及刷新一致；这不表示所有App场景已默认登记。登录验收独立见下方记录。

## 记忆管理接口扩展（2026-10-29起，当前状态见10月31日复核）

### 当前实现合同（2026-10-31提交前核对）

下表描述提交4958820时的兼容接口；本批新增行为见后面的自动记忆实施说明。App服务端沿用应用凭据及
X-Application-Id、X-External-User-Id；owner和入口由保存的映射确定，不由模型或浏览器指定。

| 操作 | 当前要求与结果 |
|---|---|
| 无Run查询 | GET `/api/v1/app/me/user-memories`，offset默认0、limit默认20且最多50；返回items/next_offset，不创建对话任务 |
| 无Run设置 | GET `/api/v1/app/me/memory-settings`；POST记忆管理路径，action=settings、key、expected_version、enabled |
| 保存/候选/纠正 | POST `/api/v1/app/me/user-memories`；action=save/propose/correct，key与非空text必填；纠正需entry_id和expected_version |
| 确认/忘记 | 同一POST，action=confirm/forget、key、entry_id、expected_version；忘记不删除历史聊天或App独立日历 |
| 类型化字段 | typed_data遵守后端类型Schema，类型写入需confirmed_type；敏感保存需confirmed_sensitive；这些字段不代替共享或本次Run使用授权 |
| 共享管理 | GET `/api/v1/app/me/memory-shares?entry_id=...`恢复已有许可；POST同路径传entry_id、expected_version、target_entry、fields、automatic；POST `/revoke`传grant_id、expected_version |
| 接收共享 | GET `/api/v1/app/me/shared-memories`分页读取获准字段，不代表可修改来源App原记忆 |
| 敏感共享披露 | POST `/api/v1/app/runs/{run_id}/shared-memory-disclosures`，grant_id、expected_version、confirmed=true、key；仅批准指定Run当前版本 |

无Run管理scope只能personal，不接受App项目映射。旧Run路径继续兼容对话内管理和disclose；
无Run管理不能用disclose绕过本次任务授权。key标识一次操作，重试保留同一key和参数；
版本过期、未授权或结果未知不能在页面显示成功，应保留用户输入并读取当前状态。
settings是范围设置，不是任意单条记忆enabled字段；自动合并未实现。撤销及显式跨端使用开关见下节。
类型字段精确定义以`features/memory/schemas/typed_memory.py`为准，旧app_data仍兼容，不能替代typed_data。

### Assistant自动记忆与Nexus统一管理（2026-10-31，本地实现）

2026-10-31已本地提交c4944fe（未推送、未部署）：新增0049版本引用表，当前正文和撤销后版本保持
递增；undo按entry_id/expected_version仅撤销当前版本，旧记录第一次变更前保存现状，
不伪造更早历史。Assistant自身敏感记忆按需读取及工具历史恢复已修改，native/retail仍保留
原披露规则。save_memory已开始支持当前用户消息source_quote及查询所得目标版本的自动写入，
不接受模型确认字段；关闭记忆时自动纠正也被拒绝。
后续已接入App Agent工具循环，并新增Nexus来源应用切换、版本记录、写入后的撤销按钮及
自动使用开关。0049/0050已在隔离MySQL及正常本地库迁移，MinIO加密正文保留。
Assistant页面由另一线程实现，当前复核及剩余限制见下方记录。

- Nexus的GET/POST `/api/v1/me/user-memories`及GET `/me/memory-settings`接受entry，默认native；
  personal_assistant/retail只允许当前用户有效关联的App账号，不接受owner参数；项目路径禁止切换入口。
- GET记忆列表附加history_entry_id读取该条已保留版本，使用offset/limit分页；不返回对象地址。
  POST action=undo需entry_id、expected_version、key。响应applied_version是该操作实际产生的版本，
  version可能是重试时的当前版本；二者不同不能撤销后来修改。遗忘可在同一管理会话立即撤销，
  历史密文不自动删除；旧数据未曾保存的更早版本不能重建。
- POST同一路径、entry=personal_assistant、action=automatic_sharing、enabled、expected_version、key，
  由真实Nexus用户设置本端按需读取许可，默认关闭；不是模型工具操作。scope级许可包含后续新增/更新，
  绑定目标owner及关联版本，解绑后失效，重新关联不自动恢复。关闭不撤销历史回答，也不撤销独立逐条授权。
- query_memory的automatic_items与next_automatic_offset表示此许可下的独立分页；执行记录只保留引用，
  生成前再次检查开关、关联、条目状态与版本，才读取原加密正文。管理可见不等于模型可用。
- Assistant save_memory可带当前用户消息中实际存在的source_quote，无需“记住”关键词；typed_data可选，
  更新需先查询并带entry_id/expected_version。模型不能提交确认权限字段。不是另起一个后台模型每次
  扫描全部聊天，也不保证模型每次都能识别值得保存的事实；不按文字哈希自动合并不同条目。
- 普通Nexus/Retail保存与披露规则继续兼容。Assistant本端可以按需读取自己的有效敏感记忆，
  不代表跨端或任意外部工具获得相同权限。

用户提出后续取消日常保存的逐条阻断确认、Assistant本端自动召回、Nexus统一管理关联入口记忆，
以及显式开启跨端自动使用。当前已实现范围以上方实施说明为准；Assistant不得将此扩大为任意跨端权限。
正式实施需同步Nexus规则、版本/来源记录及App体验，不把绑定账号本身当成已启用模型跨端使用。

- 自动提取区分长期事实、临时安排与已发生经历；未来事项不冒充已发生experience。
- 新事实带生效时间：例如“下个月搬家”不能立即覆盖当前有效地址；“不坐公交”不能推断用户偏好驾车。
- 自动更新需明确对应旧条目及来源，保留版本；撤销须精确对应一次写入，不能覆盖之后的新修改。
- 本端召回表示从有效记忆中按任务相关性选择，不是把全部active正文无条件塞进每个模型请求。
- Nexus跨端管理权与模型使用权分别定义；关联失效、关闭、忘记后的行为需两端一致，不能泄露未授权字段。
- 轻量“已记住”提示只在持久保存成功后显示；日历/地图动作与记忆保存分别报告结果。

上述实现已提交；无需额外后台模型调用。自动语义归并的质量和未来生效时间调度
不由固定模型工具流程证明，不承诺所有聊天都会自动形成正确记忆。

既有Authorization应用凭据、X-Application-Id和X-External-User-Id保持不变。新增：

- GET `/api/v1/app/me/user-memories?offset=0&limit=20`：当前App用户记忆，返回items/next_offset。
- POST `/api/v1/app/me/user-memories`：复用MemoryCommand的保存、候选确认、纠正、忘记和设置操作。
- GET `/api/v1/app/me/memory-settings`：当前入口启停状态。没有Run的管理请求不能批准敏感披露。

用户与入口来自认证映射，不接受请求正文指定owner或entry；scope只能personal，不映射App项目。
typed_data支持place/commute_preference/time_preference/experience，类型定义见阶段2。
类型保存需用户明确confirmed_type和confirmed_sensitive；值仍加密保存，不创建日历或地图动作。
共享管理新增POST `/api/v1/app/me/memory-shares`（entry_id、expected_version、target_entry、
fields字段路径列表、automatic），POST其`/revoke`（grant_id、expected_version），以及GET
`/api/v1/app/me/shared-memories`。身份仍来自应用映射；跨owner共享需要有效双向账号绑定和逐项
共享许可，绑定本身不产生共享授权。读取不附带未授权摘要；query_memory返回shared_items并在恢复时重查授权。
POST `/api/v1/app/runs/{run_id}/shared-memory-disclosures`接受grant_id、expected_version、
confirmed:true、key，仅为该Run批准当前共享字段，不能授权转发给网络工具。
2026-10-31重新只读核对Assistant提交19777602：local_run_id已可省略，设置页面调用无Run接口，
typed_data/confirmed_type、undo、逐条共享及entry指令均已接入。其README记录另一线程真实MySQL/MinIO、
固定模型及双端浏览器验收，不计作本线程重跑。本线程在仍存活的隔离3308/18308与3309/18309服务
重新登录合成账号：列表与指令HTTP200，5条记忆含1条类型化记录，刷新后ID/版本及指令文本一致。
未修改Assistant或测试业务数据，未调用模型。候选记忆列表只有纠正/忘记/共享，缺少候选确认按钮；
409冲突分支清空编辑内容而不是保留草稿，需Assistant线程处理。旧通勤确认组件不代替普通候选确认。

本批提交新增App版本历史读取：GET `/api/v1/app/me/user-memories?history_entry_id=<entry_id>&offset=0&limit=20`。
复用原生历史服务，返回entry_id、current_version、items（version/status及解密正文）、next_offset，
不返回MinIO对象地址；其他用户或其他入口返回404。省略history_entry_id保持原列表响应。
App可据此接入历史页面，不需要伪造Run；现有undo仍只撤销当前版本，不支持任意旧版本覆盖。
当前App尚未转发该参数。自动写入结果投影在本批Nexus提交中补齐，Assistant尚未接入：
GET `/api/v1/app/runs/{run_id}/diagnostics` 的tools分页中，有已提交记忆操作的
save_memory/propose_memory/forget_memory条目新增可选memory_write：
`{entry_id, applied_version, current_version, status, can_undo}`。
只关联当前Run实际工具ID、当前App用户及个人范围的操作记录，不读取工具原始输出、记忆正文或对象地址。
status表示该次操作结果，applied_version永远表示该次写入，不随当前版本更新。can_undo仅为当前
版本仍匹配的提示；点击撤销仍须POST action=undo、expected_version=applied_version及独立key，
后端重新校验版本、范围状态及历史是否存在，冲突不能强行重试覆盖。
没有memory_write不表示“没保存”；操作可能仍在执行，App应继续按游标读取或重新读取该工具页。
有已提交操作时，即使外层工具后来取消，也保留写入事实；工具失败或取消本身不撤销已提交记忆。
App仅对active写入显示保存成功，candidate明确为待确认；不得仅凭工具成功状态构造保存提示。

自动写入结果回归：固定模型App循环及诊断权限2项通过；真实MySQL/MinIO运行
`NEXUSPILOT_WORKFLOW_EXECUTION_BACKEND=inline apps/api/.venv/bin/python deployments/verification/product_memory_check.py --automatic-memory`
2项通过（7.80秒），保留数据库authz_audit_dbc9e55ebe154595a2b5d87d382ce45a及
authz_audit_3d9ffa9d742e42f790d7538db5721402。确认自动保存版本1、后续纠正版本2后原写入仍为1且
can_undo=false；诊断不含测试记忆正文。首次执行受正常worker配置影响返回pending，测试现明确
使用inline；随后手动纠正敏感旧条目按要求加入合成用户确认，不放宽生产确认规则。
本次未重启其他线程的隔离服务，新增投影尚无Assistant页面验收，不计作付费模型或真实供应商结果。

同日追加真实存储验收：复用product_memory_check.container_environment读取本地容器配置，
设置现有NEXUSPILOT_TEST_AUTH_MYSQL_URL及MINIO测试变量、KEEP_DATABASE=true后运行
`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml apps/api/tests/test_product_memory.py::test_app_memory_isolation_and_project_rejection -q -s --tb=short`。
1项通过（3.04秒），保留authz_audit_10043aca7ed04ecfa698b98dd4d29968。覆盖历史两页版本、
结束游标、跨用户及跨App返回404、普通列表兼容，不使用模型或内存对象存储替代MinIO。
Assistant仍为19777602，未发现history_entry_id或memory_write接入；其页面验收继续由所属线程完成。

本次必要回归命令：`apps/api/.venv/bin/python -m pytest -c apps/api/pyproject.toml
apps/api/tests/test_product_memory.py::test_app_memory_isolation_and_project_rejection
apps/api/tests/test_memory_versions.py apps/api/tests/test_unified_memory_management.py -q`。
新增历史断言在修改前因缺少current_version失败，修改后4项通过（SQLite及内存对象存储，非真实MinIO验收）。
相关Ruff和git diff --check通过；历史参数已追加上述真实存储验证，但尚未重启服务进行双端浏览器验收，前述浏览器结果仅证明
已运行版本的列表和指令刷新。上述验证当时未提交、未推送、未部署，没有增加模型请求或内容哈希。
2026-11-01提交推送与服务器部署状态见[本次发布记录](server-deployment-20261101.md)。
新增接口及权限定向检查25项通过（本地测试，不是App真实页面或付费模型联调）。
下方2026-10-26的11条路由数量是历史验证范围，当前完整清单以test_resource_policy.py为准。

## 权限分类回归更新（2026-10-26，无付费调用）

`tests/test_resource_policy.py`现在明确核对App的11条路由及个人Key管理的3条路由；它们分别
保留应用认证和浏览器认证，不合并到通用用户资源认证。测试检查清单完整/互斥及实际依赖挂载，
并验证部署Key/内部Key不替代这两类身份、浏览器身份不能进入App接口、有效个人Key不能管理Key。
既有CSRF、Key归属、App跨用户/跨应用结果读取、事件回放和取消测试一起执行。

本次仅修改测试和文档，不改变接口协议、权限或业务实现。联合回归62项通过，详细命令与边界见
[路线图验证记录](../architecture/platform-roadmap.md#已保存项目文件附加到对话2026-10-24)。
使用SQLite、内存对象存储、固定模型响应及本地模拟HTTP；不代表新的真实供应商或生产验证。

## 本次状态检查（2026-10-23，无付费调用）

只读核对本地 MySQL：迁移仍为 `20261023_0026`，`personal_assistant` 已启用；
私有配置的三个操作均指向 `http://127.0.0.1:8010/internal/nexus/tools`，两种凭据已配置。
数据库启用的 opencode 地址仍为 `https://opencode.ai/inference/go/openai/v1`。
没有更新数据库、用户映射或 App 业务数据，也没有重新调用模型。

新增回归测试发现：App Run 取消接口只更新 Run/Task，工作流结果当时仍显示 `running`。
现已复用工作流取消服务：先取消 Run 阻止新启动，再结束对应工作流、节点并保存取消事件。
未启动的 Run 仍可直接取消；重复取消返回原取消状态。迟到模型响应只按原规则补记用量，
不会产生最终答案或把 `cancelled` 改回 `completed`。不新增费用规则。

本次实际验证（App取消修复版本）：

```bash
cd apps/api
.venv/bin/pytest -q tests/test_application_identity.py tests/test_application_tool_loop.py \
 tests/test_remote_tool_adapter.py tests/test_workflow_execution_tasks.py \
 tests/test_general_agent.py tests/test_api_import.py --tb=short
```

**74 项通过，32.88 秒**。数据库为测试隔离 SQLite，对象存储为内存替身，模型为固定响应，
远程回调为 loopback HTTP 模拟服务，不是本次真实 Go/App 联调。
随后提交前使用同一命令复验，**74 项通过，33.66 秒**；未增加真实模型调用。
修改前先运行已有四组相关测试，38 项通过；新增终态测试在修复前明确复现
`run_cancel` 结果仍为 `running`（1 失败、3 通过），修复后通过。

本次补充验证：运行中和完成后同键启动不重复调用、换参数返回 409；两种取消入口、
取消前尚未启动、重复取消、迟到回答和用量；完成/失败/取消的结果与最后事件一致；
中间游标两种写法返回完整后缀；跨用户 Run 取消拒绝；回调结构错误/503/超时作为工具错误
进入后续模型消息。沿用并执行既有 ASGI 断开测试、权限/工具身份、大小限制和通用三种审核策略测试。
Ruff（修改的两个 Python 文件）与 `git diff --check` 通过。

本次未执行真实 MySQL 并发争抢启动测试、真实供应商复验或普通 App 启动验收。
上述 74 项不能替代这些检查；已有 Go 成功记录在下节单独保留。

## 已有真实联调记录（2026-10-23，提交 326fbc4）

本机MySQL为`20261023_0026 (head)`；`applications`、`application_user_mappings`、
`run_application_context`均存在，不需要再执行迁移。NexusPilot8000/8001已重启加载Go地址，
本次联调实例为127.0.0.1:8001，真实assistant-api为127.0.0.1:8010。
应用配置位于忽略的`apps/api/.env`的`NEXUSPILOT_APP_TOOLS_JSON`；账号和App服务运行设置
位于仅当前用户可读的`/private/tmp/nexus-personal-assistant-20261023`，不提交这些文件。
使用独立SQLite合成账号/数据，不读取App现有私密任务或笔记，不修改superapp工作区。

本次开始时App回调是X-NexusPilot请求头格式；检查过程中App代码被其他工作更新为下面的JSON信封。
因此撤回了本次临时请求头适配，沿用NexusPilot现有RemoteToolAdapter，不保留两套PA现行协议。
本文记录当前实际调用定义；旧请求头方案不再适用。

当前PA角色绑定为`opencode/deepseek-v4.1-flash`。用户确认Key属于Go后，将数据库中opencode
地址改为`https://opencode.ai/inference/go/openai/v1`；原`/inference/openai/v1`是按量入口。
Go端点见[官方说明](https://opencode.ai/v2/docs/console/go/#endpoints)。客户端发送真实
User-Agent NexusPilot/0.1及工作流内稳定的x-opencode-session。Go面向编码Agent，通用个人助手的长期生产用途应向服务方确认。

真实Go联调已通过，报告见[OpenCode Go App验证](../../output/security/personal-assistant-opencode-go-20261023.json)：

| 场景 | 耗时 | 输入token | 输出token | 缓存输入token | 模型调用 | 终态 |
|---|---:|---:|---:|---:|---:|---|
| 普通回答 | 6.282秒 | 2100 | 82 | 0 | 1 | completed |
| 任务、笔记、日历读取 | 22.791秒 | 16363 | 1043 | 10752 | 5 | completed |

两轮均取得App和Nexus最终回答，分别回放8/18条事件，验证中间Last-Event-ID后的后缀及末尾空回放，
跨用户结果/事件/取消和App请求读取均拒绝。第二轮三种业务读取各一次，receipt均completed且用户正确，
对应三条工具消息ID出现在最终模型请求的effective_messages中；不把答案内容的正确性当作本次验收条件。
总工具调用10次包含search_tools3、describe_tools3、list_run_files1和远程业务读取3，不是重复读取10次业务数据。
还验证了本人取消新pending Run。token为供应商返回累计值，缓存输入是输入的一部分，不重复相加；
estimated_cost字段不是供应商实际账单，本次未修改费用逻辑。
此前官方DeepSeek402及误用OpenCode按量端点402记录保留，不代表Go账户无可用额度。

## App调用NexusPilot

业务后端认证登录用户后发送：

```http
Authorization: Bearer <仅服务端持有的入站凭据>
X-Application-Id: personal_assistant
X-External-User-Id: <App已认证用户ID>
POST /api/v1/app/runs
Content-Type: application/json

{"user_request":"用户的问题"}
```

NexusPilot将App与外部用户映射到独立owner；不接受正文user_id。
随后POST `/api/v1/app/runs/{run_id}/agent-workflows`：

```json
{
  "workflow_name": "general_agent",
  "workflow_version": "1.0.0",
  "execution_profile": "general_tools_v1",
  "idempotency_key": "唯一请求标识",
  "role_bindings": {"controller": {"provider": "opencode", "model": "deepseek-v4.1-flash"}},
  "review_policy": "never",
  "stream": true
}
```

本次never是服务端测试配置，不改变平台三种审核策略；其他策略按实际schema提供reviewer绑定。
查询当前workflow：GET `/api/v1/app/runs/{run_id}/agent-workflow`。
结果：GET `/api/v1/app/agent-workflows/{workflow_id}/result`，正文在`final_output.final_text`。
事件：GET `/api/v1/app/agent-workflows/{workflow_id}/events`，是已持久化事件的有限回放，
支持`Last-Event-ID`或`after_sequence`，不是永久长连接。
取消：POST `/api/v1/app/agent-workflows/{workflow_id}/cancel`或`/api/v1/app/runs/{run_id}/cancel`。
所有请求必须重复携带同一服务身份和已认证外部用户；跨用户结果、事件和取消返回404。

App 客户端必须遵守：

- 网络中断后保留原 Run ID、幂等键和请求参数。相同启动返回 HTTP 200 和已保存结果/事件，
  不重新执行；相同键换参数返回 409。POST `/runs` 本身不提供幂等键，重复创建会是不同 Run。
- `stream=true` 启动由进程中的后台任务执行，关闭 SSE 只分离观察者；恢复时查询结果并回放事件。
  `stream=false` 是同步等待结果，不作为 App 断线恢复的启动方式。API 重启后的跨进程续跑不在此保证内。
- GET 事件每次最多 1000 条，按 `event_sequence` 升序，严格大于游标；query `after_sequence`
  优先于 `Last-Event-ID`。客户端处理成功后保存 SSE `id`，按 workflow ID 加 sequence 去重，
  用最后处理的 ID 再取下一批。空回放只表示当时没有新事件，不代表完成。
- 用结果 `status` 判断终态；完成后从 `final_output.final_text` 读取文本。
  `failed`、`cancelled`、`outcome_unknown` 不应展示成成功，需处理对应错误或未知状态。
  Run 和 Workflow 两个取消入口都会停止对应工作流；已完成/失败后取消返回 409，
  已取消后再次取消返回 200。终态事件保存后可回放，迟到用量可能继续更新结果快照。
- 回调失败是工具失败，不等于整个工作流一定失败；模型仍可基于明确的工具错误生成说明。
  工具没有成功时，客户端不能把该工具呈现为“已读取到空数据”。

## 唯一的PA工具回调格式

POST `/internal/nexus/tools`，Authorization为独立回调凭据；不发送旧X-NexusPilot身份请求头。

```json
{
  "tool_execution_id": "平台llm_tool_calls.tool_call_id",
  "application_id": "personal_assistant",
  "external_user_id": "从已保存Run映射解析的App用户ID",
  "tool_name": "personal_assistant.list_tasks",
  "tool_version": "1.0.0",
  "arguments": {"limit": 10, "cursor": null}
}
```

| 模型可调用名称 | 回调tool_name | 参数 |
|---|---|---|
| personal_assistant_list_tasks | personal_assistant.list_tasks | limit 1—20，cursor可空 |
| personal_assistant_search_notes | personal_assistant.search_notes | query 1—1000字符，limit 1—20，cursor可空 |
| personal_assistant_list_calendar_events | personal_assistant.list_calendar_events | start_date/end_date，limit 1—20，cursor可空；平台拒绝反向或超过366天区间 |

上述名称映射由静态RegisteredTool维护；App服务地址和路径只在服务端配置，模型不能修改。
成功统一返回`{"items":[对象],"next_cursor":null}`，最多20条，平台校验结构和64KiB结果上限。
当前App不提供真正分页实现，next_cursor为null，不应虚构后续页。
App回调按认证凭据、application_id、启用用户、固定工具版本及参数检查访问。
同调用ID同用户同工具返回已保存receipt；同ID换用户/工具拒绝。
调用ID不是供应商tool_call_id，不是Run ID；Run关联保存在Nexus的Attempt/Tool Call关系中。
当前回调信封没有run_id，App的receipt.nexus_run_id可为空，核对执行应使用tool_execution_id。

App非2xx错误统一由适配器转换：401/403为remote_access_denied，404为remote_resource_not_found，
其他普通4xx为remote_request_rejected，429/5xx为remote_service_unavailable；不把失败返回为空items。
结构错误为remote_invalid_response；适配器超时为remote_timeout，外层工具执行期限先到时为tool_timeout。
平台适配器不自动重试，取消传播给HTTP请求；模型后续是否再次请求工具由既有循环决定。
App错误正文不交给模型；凭据不放入工具定义、输出、报告或浏览器响应。

## 验证与限制

此前离线/模拟测试结果（不是本次重新执行的结果）：

```bash
PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q \
 apps/api/tests/test_application_identity.py apps/api/tests/test_application_tool_loop.py \
 apps/api/tests/test_remote_tool_adapter.py apps/api/tests/test_api_import.py --tb=short
```

24项通过，15.17秒。包括身份单元检查、本地模拟HTTP及模拟Provider驱动的PA/Retail工具循环、
结构/超时/错误响应检查、事件回放、跨用户结果/事件/取消拒绝、在途取消后不再调用模型。
这不表示DeepSeek成功执行了工具。Ruff与git diff --check通过。

App仓库使用其已有虚拟环境执行
`python -m pytest -q tests/test_tool_callback.py tests/test_current_app_api_contract.py tests/test_sse_resume.py tests/test_run_flow.py`，
28项通过，6.03秒，使用隔离测试环境；未修改该仓库文件。

- [真实回调记录](../../output/security/personal-assistant-callbacks-20261023.json)：任务/笔记各1条合成记录，日历为空。
  三工具均经过实际RemoteToolAdapter和真实App HTTP响应结构校验；重复回放、跨用户调用ID拒绝、额外user_id拒绝，
  新App Run持久owner一致及本人取消通过。这不是模型工具决策验收。
- [真实DeepSeek记录](../../output/security/personal-assistant-deepseek-20261023.json)：
  deepseek-v4-flash，Run `3dcf6a51-5aba-4365-872c-0cdf8e6b0d13`，
  workflow `8ed4516d-f5f6-4ebf-a9cc-a4bedeea790e`，4.942秒，failed，无最终文本。
  启用的实际供应商地址为https://api.deepseek.com。供应商HTTP402；Attempt输入/输出token为NULL，供应商未返回用量，不能把汇总0解释为实际0消耗。
  4条已持久化事件和尾部回放、跨用户拒绝已检查；因402未继续第二次需要工具的付费工作流。
  该官方DeepSeek失败记录不改写；后续Go成功验证见文首，二者为不同供应商入口。

## 普通本地开发与临时联调的区别

NexusPilot 使用 `apps/api/.env` 和已有 MySQL/MinIO，`make api-run` 先执行项目迁移再启动 API。
App 凭据不依赖 Nexus 浏览器登录；App 必须自己验证用户登录后设置外部用户 ID。
在服务端私有配置中合并下面的条目，不覆盖其他已登记应用，替换占位凭据（入站至少 32 字符，
回调为两端均接受的至少 24 字符）。不要提交真实值：

```dotenv
NEXUSPILOT_APP_TOOLS_JSON='{"personal_assistant":{"inbound_key":"<独立应用入站凭据>","service_token":"<独立工具回调凭据>","base_url":"http://127.0.0.1:8010","allow_local_http":true,"operations":{"personal_assistant_list_tasks":"/internal/nexus/tools","personal_assistant_search_notes":"/internal/nexus/tools","personal_assistant_list_calendar_events":"/internal/nexus/tools"}}}'
```

仅 development 允许显式开启本地 HTTP。模型 Key 留在 Nexus 私有配置中；Provider 地址来自数据库，
不是 App 配置。App 服务端的角色绑定必须显式设置为 opencode，避免使用其默认 openai 模型。

只读检查 App 当前 `Settings`：默认读取 **`services/assistant-api/src/.env`**，
而不是该仓库 README 示例中的服务根目录 `.env`。目前前者不存在；普通启动不会自动继承
临时联调脚本的模型、凭据和 SQLite 地址。本次不修改 App 仓库，App 维护者应在其已有私有配置
或进程环境中设置这些值（可直接导出进程环境，优先于 env 文件）：

```dotenv
ASSISTANT_DATABASE_URL=<App已有数据库的绝对地址，不填临时联调库>
ASSISTANT_ALLOW_SCHEMA_CREATE=false
ASSISTANT_NEXUS_BASE_URL=http://127.0.0.1:8000
ASSISTANT_NEXUS_APPLICATION_ID=personal_assistant
ASSISTANT_NEXUS_APPLICATION_TOKEN=<与Nexus inbound_key相同>
ASSISTANT_TOOL_CALLBACK_TOKEN=<与Nexus service_token相同>
ASSISTANT_NEXUS_ROLE_BINDINGS_JSON='{"controller":{"provider":"opencode","model":"deepseek-v4.1-flash"}}'
ASSISTANT_NEXUS_REVIEW_POLICY=never
```

App 使用原有数据库初始化/迁移流程；不要为接入 Nexus 新建用户库或重建映射。
在 `superapp-next/services/assistant-api` 工作目录，加载上述私有环境后使用其已有启动入口：

```bash
.venv/bin/python -m uvicorn assistant_api.main:app --host 127.0.0.1 --port 8010
```

修改私有配置后需由操作者重启对应服务。本次没有重启正在监听的 8000/8001/8010 进程，
也没有把临时数据库迁移为普通开发库；这些启动说明是根据当前代码核对，未另起普通 App 实例验收。

以下仅为**隔离合成数据的历史联调入口**：`check_personal_assistant_app.py` 显式关闭 env 文件读取，
指定 `/private/tmp/nexus-personal-assistant-20261023/app.sqlite`、8001 和 opencode 角色。
`prepare/configure` 会写入临时数据或 Nexus 私有应用配置，不应当作普通启动命令运行。

```bash
# 配置/启动使用App已有虚拟环境及其src，脚本不修改App源码
PYTHONPATH=/Users/mith/Desktop/project/superapp-next/services/assistant-api/src \
 /Users/mith/Desktop/project/superapp-next/services/assistant-api/.venv/bin/python \
 deployments/verification/check_personal_assistant_app.py serve
# 不调用模型的真实回调检查（现有报告存在则拒绝覆盖）
PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python \
 deployments/verification/check_personal_assistant_callbacks.py
```

`check_personal_assistant_app.py verify --report 新路径.json`会发起真实付费调用，不能当普通离线测试运行。
以上真实联调代码和记录已随 326fbc4 提交；本次状态修复与回归测试随后独立提交，提交号以Git历史为准，未推送、未部署。
临时服务和合成数据不能作为普通开发或公网配置使用。
# 可选双端登录接入（2026-10-31）

Assistant 原密码登录/注册保留，新增关闭默认值的 Nexus 登录及“我的”页面关联入口。
现有用户从 Assistant 发起关联并在 Nexus 明确确认后，可通过 Nexus 授权登录原 Assistant 账号；
未关联时不自动注册、不按邮箱合并、不迁移历史 owner。应用凭据仅在 Assistant 服务端读取。
授权码兑换的实际路径是 `POST /api/v1/auth/app/exchange`，不是 `/api/v1/app/exchange`。

新客户端在 `/authorize` 使用 `response_mode=fragment`，将一次性 code/state 传入批准的
`/nexus-callback`；前端立即清除 fragment，BFF 核对短期 HttpOnly state/PKCE 后调用本地
`POST /api/v1/auth/nexus/exchange`，由 API 兑换并签发 Assistant 自身会话。
Nexus 旧客户端不传 response_mode 时仍使用 query 回调。不得把码或验证码写入日志、文档或持久浏览器存储。

真实双端本地验收已完成原登录、两端确认关联、退出后 Nexus 登录、解除后新登录拒绝和原密码登录保留。
使用隔离 MySQL/SQLite、实际两端 API/BFF，无回调拦截或付费模型；见
[本次记录](../../output/security/assistant-unified-login-20261031.json)。这不是正常开发/生产配置已启用，
也不代表本轮重新验收了跨端记忆/指令页面。解除关联不撤销既有 Assistant 独立会话，用户需按原机制退出。
