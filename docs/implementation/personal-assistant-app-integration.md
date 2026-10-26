# Personal Assistant 与 NexusPilot 本地接口及验收

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
