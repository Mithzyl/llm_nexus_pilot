# Personal Assistant 与 NexusPilot 本地接口及验收

## 当前结果（2026-10-23）

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
结构错误为remote_invalid_response，超时为remote_timeout。平台不自动重试，取消传播给HTTP请求。
App错误正文不交给模型；凭据不放入工具定义、输出、报告或浏览器响应。

## 验证与限制

离线/模拟测试结果（与下面的真实服务验证分开）：

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

复现入口：

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
未部署、未推送、未提交；临时服务和合成数据保留供继续联调，不能作为公网配置使用。
