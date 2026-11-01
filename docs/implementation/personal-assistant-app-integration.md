# Personal Assistant 与 NexusPilot 本地接口及验收

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
