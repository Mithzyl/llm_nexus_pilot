# 新通用 Agent：Strata / MySQL / MinIO 本地验收

## 范围和执行约束

实现、测试与规划先提交为 376a7d4（codex/general-agent-tool-loop）。
初始用户授权真实 Strata 调用及阶段验证后提交；最初不使用 DeepSeek、不推送、不部署。
2026-10-19 后续明确授权“暂时用deepseek替代strata完成请求测试”，以下 DeepSeek 测试据此执行；不部署限制保持。
前端仅允许必要 API 对接，不更改样式、文案或布局。

- API：127.0.0.1:8000；MySQL：localhost:3306/nexuspilot；MinIO：localhost:9000。
- 当前 Alembic 版本为 20261018_0024，与代码 head 相同；新循环没有新增迁移。
- Strata：tiel-coder-35b-a3b-ud-q4_k_xl，163,840 Token 登记窗口，支持工具。
- 文件执行器：已有本地 arm64 镜像，专用 Unix socket，固定隔离解析。
- 附件：此前用户提供的 FULLTEXT03.pdf，941,686 字节。独立用 Poppler 提取并查看第3页摘要，核对研究对象为 IKEA 客户支持自动化分析；第24页方法原文用于核对 Prompt Chaining 用途。未把答案预先塞进模型输入。
- 启动前只读取模型目录和迁移，没有调用 DeepSeek。历史 RUNNING 工作流未被人工改写。

执行脚本：deployments/verification/check_live_general_agent.py。使用 API 虚拟环境与
PYTHONPATH=apps/api/src:packages/models/src。每个策略创建独立会话、文件和 Run；
SSE 收到持久 Workflow ID 后主动断开观察，再通过只读查询跟进。报告存在时必须使用
--observe，不自动重发模型请求。取消只操作本脚本新建的 Run。

## 已验证结果

| 策略/场景 | 运行结果 | 秒数（不含上传） | 模型请求 | 输入 Token | 输出 Token | 缓存输入 Token |
|---|---|---:|---:|---:|---:|---:|
| never，不审核 | 已完成并恢复正文 | 64.651 | 3 | 36,719 | 1,796 | 12,997 |
| always，始终审核 | 已完成；审核格式修正一次 | 215.017 | 6 | 93,448 | 9,530 | 31,794 |
| on_verification_failure | 已完成；无错误，不触发审核 | 61.504 | 3 | 36,797 | 1,747 | 13,029 |
| 内容修正后交付 | 未执行真实样本 | — | — | — | — | — |
| 在途取消 | 已取消；上游结果未知 | 6.367 | 1 | 未知 | 未知 | 未知 |
| 浏览器恢复/取消 | 已完成结果与取消终态恢复；浏览器发起/停止未执行 | — | 0 | — | — | — |
| v2 段落/读取范围，不审核 | 已完成并恢复正文及来源记录 | 58.531 | 3 | 36,582 | 1,644 | 12,951 |
| v2 逐单元审核，首次 | 失败；批次范围及枚举不符 | 423.391 | 5 | 78,325 | 21,045 | 13,003 |
| v2 审核输入修复，条件审核 | 已完成；13 单元、3 批格式修正 | 410.409 | 11 | 198,012 | 16,303 | 51,715 |

缓存是输入的子集，不额外相加。reasoning Token 未报告，货币费用配置/真实账单尚不可用，
不得将 Node 汇总的默认 0 当作实际免费。耗时包含本地执行和轮询确认，不是纯模型解码时间。

### 不审核分支

- Run：28feda8b-28a1-4e6b-9103-88964919e988。
- Workflow：eaaed009-8574-4f9c-ab65-49c9ef2c48a8。
- 详细报告：/tmp/general-agent-never-20261019.json；持久事实保存在该 Run 的 MySQL/MinIO 记录。
- 5 次工具全部成功：首部 offset=0/limit=30、全文搜索 Prompt Chaining、
  block_id=page:16:line:34、page:24:line:26、page:33:line:38 的定点读取。
- 没有额外调用工具目录来加载已提供工具，没有从首页逐页扫描。
- 最终回答正确说明 IKEA 客户支持分析主题、Prompt Chaining 拆分多步骤分析的用途，
  引用 [S1]/[S3]/[S4]/[S5]，明确未通读和解析缺页。不将单例主要事实正确扩大成完整内容准确率。
- 3 个节点、2 条消息的正文均在对象存储中；直接 SQL 检查对应正文字段为空，
  body_ref_json 存在，5 个对象的实际内容哈希与引用一致。
- GET 消息与再次 GET 工作流返回的最终正文一致。SSE 观察断开后服务端继续完成。
- Reviewer 调用数为 0，业务轮次为 1，修正次数为 0；不能把本例当作审核或修正分支通过。

### 始终审核和条件审核

- always Run：80a656cd-8e41-40ec-a339-f23483ed0ba5；Workflow：3eaa3905-26cd-4f71-9bb0-3aee17e2f90b。
- 详细报告：/tmp/general-agent-always-20261019.json。首部读取、全文搜索、三个定点补读成功。
  Reviewer 额外执行一次 describe_tools，但没有追加原文读取；不能将它记为有效补读。
- Reviewer 首次正文格式不合格，一次格式修正后返回 supported；output_repair_count=1，
  repair_usage 为输入 18,682 / 输出 1,922 Token。没有内容 refine，不能把格式修正当作内容修正通过。
  审核反馈同时建议澄清 PDF 页码和印刷页码，候选未被改写；该样本仍有语义判断局限。
- on_verification_failure Run：04001336-a11a-4f9c-854f-ecd5939caa03；Workflow：90957c0d-5d79-4749-a135-0cdec09c5bb7。
  报告 /tmp/general-agent-onfailure-20261019.json。五次文件操作成功，没有未知来源引用，故按策略不调用 Reviewer；
  不能把此例当作“验证失败后触发审核”真实分支验收。
- 两次运行的 GET 消息/结果恢复一致。直接 SQL 检查共 11 条消息/节点记录正文为空且对象引用存在，
  对应 11 个 MinIO 对象哈希均通过。没有新增数据库表或复制文件全文到 SQL。

### 在途取消与浏览器检查

- Run：88ebccbd-9837-4d07-9235-016b5ca0449c；Workflow：53df47a8-afaf-4690-bdff-7e9ef85ef020。
  报告 /tmp/general-agent-cancel-20261019.json。观察到 Strata Attempt 已开始后发出取消，确认耗时 0.100 秒。
- Workflow 为 cancelled，唯一 Attempt 为 outcome_unknown/provider_outcome_unknown，5 秒后无新增调用、无 started Attempt。
  不宣称远端模型停止生成或停止计费；用量均未知，不能按零计算。取消没有生成最终回答。
- 使用 Playwright CLI（general-agent-check）打开 always 会话并刷新；回答正文恢复。
  首次切换“运行过程”实际复现 Cannot read properties of undefined (reading 'map')：前端类型/映射没有接新节点。
  先增组件回归确认失败，再补 general_candidate/general_review 的类型和详情映射，复用已有标签，未改 CSS、布局或入口默认工作流。
- 修复后相同会话刷新、切换运行详情通过；节点时间线显示主 Agent、独立审核、最终汇总和完成记录。
  截图 output/playwright/general-agent-restored.png，快照 general-agent-restored.yml。
- 取消会话在浏览器恢复为“已取消”；输入未发送草稿后发送按钮可用，记录在
  output/playwright/general-agent-cancelled-input.yml。没有以浏览器检查名义重新调用模型。
- 前端检查：npx tsx --test tests/agent-workflow-view.test.ts tests/agent-workflow.test.ts tests/claim-evidence.test.ts tests/workflow-recovery.test.ts，
  28 通过；npm run typecheck、修改文件 ESLint 通过。脚本 Ruff、git diff --check 通过。

### 候选段落与实际返回范围（后续本地修改）

此前验证与必要节点映射已提交 c465ce4。本项在该提交之上新增 general_candidate.v2，
验证脚本记录工作区是否有修改及 Agent Runtime 各源码 SHA-256，不能仅用报告的 HEAD 指代未提交代码。

- Run：1e940e11-6683-43e7-a2d0-90afb01d04f1；Workflow：5f700c18-5b5a-4e61-bc5e-a2beb994dfbd。
  报告 /tmp/general-agent-units-20261019.json。使用同一 PDF、问题、模型，策略 never；没有增加用于重写结构的模型请求。
- 后端产生 14 个标题/段落/条目单元，保存原始正文位置和短来源关联；5 个正文单元没有直接引用，
  如实标记 missing，不自动继承邻段引用，也不直接判断其内容错误。该次验证时 Reviewer 仍整篇审核；后续逐单元实现见下节。
- 文件共 1542 个解析块，实际工具返回去重后 90 个，未返回 1452 个；
  all_parsed_blocks_returned=false、model_understanding_verified=false。保留无 OCR、图形/表格未解释等解析警告。
  这是实际返回范围，不是当前模型窗口包含范围或全文理解证明。
- 2 条消息、3 个节点原始 SQL 正文为空；5 个 MinIO 对象引用、内容哈希均通过。API 再读最终正文一致。
- Playwright 打开该会话恢复为“工作流已完成”，正文可读；快照 output/playwright/general-agent-units-restored.yml。
- 测试先行：新增纯组装测试先因实现模块不存在失败；新增 12,000 字符中文答案在附带单元记录后
  复现旧 64 KiB 节点保存限制导致失败，随后仅为新节点增加有界对象记录容量，旧节点限制未变。
- 相关回归：general_candidate、general_agent、workflow_execution_tasks、agent_workflows、worker_evidence、
  conversation_body_storage 共 127 通过。之后增加历史 v1、损坏偏移/遗漏正文和对象恢复断言，
  最终 general_candidate + general_agent 为 25 通过。两组重叠，不相加作为测试数量。
- Ruff 与 git diff --check 通过，Web 类型检查通过；没有新迁移或对历史对象重写。

### 逐单元审核真实失败及修复依据

- Run：0069517c-a2c4-4f54-b0e2-6d237328bfce；Workflow：22f90982-7a50-4655-b791-3bc0fb1cbe27。
  报告 /tmp/general-agent-unit-review-20261019.json。主 Agent 三次调用完成，Reviewer 首次及一次修正后仍失败。
- 只读 MinIO 检查证明两次审核正文均为合法 JSON，finish_reason=stop；不是 JSON 语法错误或正文丢失。
  首次正文 2400 字符，SHA-256 bcb9c06b72e33521025acd449fb04475fb4853c39f44f67c813f00d7fc570a56，
  返回全部16个单元，超过请求的 u1–u6。修正正文 872 字符，SHA-256
  7891103fdeeccece4b4b59a66372aba7623389e3a98ce9f39c945e64db4036bb，范围改为6个，
  但 u2/u4 使用 not_applicable 而不是允许的 supported/revise/unconfirmed。
- 原输入同时列出全部 candidate_units 和当前 review_units，还携带 citation_status=not_applicable。
  这些是可消除的输入歧义，不能简单归因模型能力。已改为完整候选正文 + 本批单元的编号/类型/正文/来源；
  不再向模型给出其他批次编号及平台引用状态。对纯结构标题明确解释适用决定，不扩大合法 verdict 或猜成通过。
- 确定性测试还先发现同一节点第二批复用模型请求 key，以及多个格式修正被按节点合并统计。
  现已保持工具循环请求序号，并使用节点+批次区分修正；测试包含两批各修正一次、保留已校验决定、对象恢复。
- 修复后的 Run：de861fd3-827f-42c8-ba49-943748d7bb54；Workflow：9cc6caa1-f1b7-4aa5-aa95-d21657a57013。
  报告 /tmp/general-agent-unit-review-corrected-20261019.json；最终 completed，共13个单元全部保存审核决定，
  总体 supported。5个无引用段落实际触发 on_verification_failure；没有发生内容 refine，不能计作内容修正验收。
- 三个批次的初次回复均带有解释性前导正文或代码围栏，严格解析拒绝；每批一次局部格式修正后通过。
  output_repair_requested_count/succeeded_count 均为3，repair_usage 为输入59,918、输出1,541 Token。
  首次通过率为 0/3，修正成功为 3/3；不因最终成功隐瞒额外耗时与用量。后续应减少首轮输出格式偏差。
- 共7次工具操作：主 Agent 的5次首部/搜索/定点读取，加 Reviewer 的 list_run_files、再次搜索 Prompt Chaining。
  Reviewer 没有追加连续分页或原文段落补读，不能把目录查询和搜索算成已验证定点补读。
- 结果/消息再次读取一致；原始 SQL 正文为空，MinIO 哈希全部通过。格式修正是模型调用，按实际 Attempt 计数，不作为内容轮次。
- Playwright 已验证失败会话恢复为“工作流失败”；输入草稿后按钮状态见
  output/playwright/general-agent-unit-review-failed-input.yml；没有重新发送该问题。
- 在该成功运行执行中刷新浏览器，随后没有再次刷新，页面自动变为“工作流已完成”并呈现最终正文：
  output/playwright/general-agent-unit-review-reloaded.yml、general-agent-unit-review-completed.yml/png。
- 浏览器揭示旧来源面板只认识 worker_evidence.v2，虽然新后端有记录仍显示没有来源。
  已作必要接口适配：仅选择最新候选，按 candidate_node_id + unit_id 匹配审核；revise 不假称原文矛盾。
  原 claim-sources 路由增加新单元读取，仍重查授权/文件快照，按真正读取的 Attempt 角色标注来源。
  没有改 CSS、布局或现有页面文案。
- 真实浏览器展开方法段落，可见 Reviewer 说明、FULLTEXT03.pdf 第24页20个原文块、分页和解析警告，
  证据 output/playwright/general-agent-unit-originals.yml、general-agent-unit-originals-visible.png。
  单元/来源后端回归包含跨用户拒绝、删除后拒绝、Reviewer 旧读取在修正候选中的准确归属；
  前端相关29项通过，类型与 ESLint 通过。
- 最终后端相关检查122项通过（62.37秒）：PYTHONPATH=apps/api/src:packages/models/src，
  apps/api/.venv/bin/python -m pytest -q 后跟 apps/api/tests/ 下的 test_general_review.py、
  test_general_agent.py、test_general_candidate.py、test_workflow_execution_tasks.py、test_agent_file_tools.py、
  test_agent_output_repair.py、test_evidence_output_repair.py、test_claim_sources.py、
  test_workflow_refinement.py、test_plan_repair_statistics.py。Ruff、git diff --check 通过。

### 实际请求的文件工具输入清单

本批新增 `services/model_input_manifest.py`，在 `ModelInvocationService` 保存请求时记录
历史裁剪之后的原生文件 Tool 消息。清单和公开消息序列都存入原有 MinIO 请求对象，SQL 仍只保存 URI；
不增加表或改写旧对象，不向供应商额外发送清单，不改变前端或启动新的付费模型请求。

- 新增4个测试先因模块尚不存在失败，随后实现通过。测试区分搜索范围与真正出现的块、原文与
  已省略正文、用户伪造 JSON、失败/损坏历史、不同文件快照和完整块哈希。
- 现有文件工具集成测试核对持久清单与脚本 Provider 实际收到的消息完全一致，并检查联调脚本能区分
  `verified`、`historical_manifest_absent`、`mismatch`，且不重写旧对象。
- 文件工具/输入清单/历史投影检查命令：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_agent_file_tools.py apps/api/tests/test_model_input_manifest.py apps/api/tests/test_tool_context.py --tb=short`，最终28通过（11.14秒）；Ruff 和 `git diff --check` 通过。
- 新通用 Agent、模型请求和正文对象存储回归：同一 PYTHONPATH 和 pytest 命令，文件为
  `test_general_agent.py test_responses.py test_conversation_body_storage.py`，82通过（28.53秒）。
- 本地只读复验调用联调脚本 `model_input_evidence('de861fd3-827f-42c8-ba49-943748d7bb54')`，
  从 MySQL 现有 Attempt 引用打开11份真实 Strata MinIO 请求。历史对象均无新清单，正确标记缺失而不是通过。
  主 Agent 的3份请求分别有0、33、93个原生文件块出现次数；后者包含重复块，不能说读了93个不同块。
  Reviewer 的8份请求分别为0、0、0、0、5、5、5、5；Reviewer 输入还带独立原文集合，**不在此项统计中**，
  因此不能将前四份的0解释为 Reviewer 没有拿到原文。
- 此次只读脚本最初使用错误表名/时间字段及异步遍历同步对象流，检查失败；核对实际 ORM 和
  ObjectContent 类型后改为模型字段查询与线程内同步读取，复验成功，并纳入上述集成回归。
- 无新模型请求，新增供应商 token 消耗为0。没有声称已用新代码完成真实供应商写入验收；
  后续真实调测自动附带 `model_input_evidence`，检验新请求是否有与实际消息一致的清单。

这项实现只提供逐请求的原生工具输入事实，不等于任务级完整阅读覆盖。Reviewer 原文集合、
工作笔记、最终完整请求参数审计与后续覆盖汇总仍须继续实现。

### 任务内笔记与真实请求失败记录

新增 `task_note_tools.py`，通过共用工具执行记录保存不可变笔记和真实来源引用；当前 Task 读取/分页查询，
终态/取消中的任务拒绝写入，旧工具目录不变。笔记正文/来源仍使用工具结果的 MinIO 对象存储，MySQL 不保存正文。
这不代表执行检查点、跨角色共享 Memory 或跨进程续跑已完成。

- 新增9项测试：保存/读回及 SQL 空正文、另一 Task/未知 ID 拒绝、虚构来源/额外身份参数拒绝、
  实际文件来源和撤销后停止发送历史、目录发现/加载、完成或请求取消后拒绝写入。
- 测试先行时先遇到测试目录登记 fixture 缺失；补齐后因工具尚未提供失败。后续 fixture 的 ModelResponse
  缺少必填耗时和 raw_response 已修正，不把测试配置错误记作业务实现缺陷。
- 最终相关回归63项通过（17.70秒）：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_agent_task_notes.py apps/api/tests/test_general_agent.py apps/api/tests/test_agent_file_tools.py apps/api/tests/test_agent_tool_discovery.py apps/api/tests/test_workflow_execution_tasks.py --tb=short`。
  Ruff 与 `git diff --check` 通过。
- 本地 API 已重启加载代码。重启前查到的两个 DeepSeek STARTED 记录始于9月1日/4日；当前进程10月19日启动，
  代码没有跨进程恢复，且无供应商连接。没有修改历史记录，没有调用 DeepSeek。
- 真实样本：Run `a8aa3781-fdf2-485a-8324-783ca986c197`，Workflow `c2f85d04-d710-497a-a290-87ab8c718a29`，
  Session `589f38fc-4083-4a5c-aec3-a117a2f4ee18`。报告 `/tmp/general-agent-task-notes-20261019.json`。
  使用 FULLTEXT03.pdf，仅要求首部读取、保存阶段发现/未读范围、读回笔记及简短最终回答；审核策略 never。
- 结果 **失败**，耗时6.224秒；1次逻辑模型调用、3次HTTP尝试，全部502，耗时分别1471、492、500毫秒。
  未执行任何工具，未生成最终回答；输入/输出/缓存/推理 Token 和费用均未报告，保持未知，不能计作0。
  脱敏诊断为 HTTP502、非JSON错误信封、类别unknown，消息长度6410，
  SHA-256 `9d3e11a72cc8894c4cca921a7e3d929c6be91708268f25d30f71795a6752f0b1`。
- 随后只调用登记端点的 GET /models，也返回502、text/html、6410字节；没有再次发送生成请求。
  这支持当时端点不可用，不能仅凭此断言具体代理/模型服务根因，更不能认定笔记参数导致失败。
- 该真实请求包含新 `tool_input_manifest`，只读重建比较为 verified；它没有文件 Tool 消息，块数为0符合执行事实。
  失败节点的 SQL 正文为空且 MinIO 对象哈希通过。此前旧请求缺少清单的状态仍不改写。
- Playwright 打开并恢复该会话，显示“工作流失败”“未生成最终回答”；输入未发送草稿后发送按钮可用。
  快照 `output/playwright/general-agent-task-notes-failed.yml`、`general-agent-task-notes-failed-draft.yml`。
  截图 `output/playwright/general-agent-task-notes-failed.png` 已查看；测试草稿已清除。
  没有点击发送，没有改变页面样式/文案。真实笔记保存/读回成功及内容修正交付仍待服务恢复后验证。

### 独立原文输入与实际参数记录

- 新增 `effective_request.v1` 保存适配器实际收到的公开配置，包含已组装 system、模型参数、工具和推理映射；
  私有续接只记录父 Attempt 引用，不写入私有正文。不是 HTTP wire 抓包或上游收件证明。
- `runtime_source_manifest.v1` 记录后端显式 original_sources/review_sources 及格式修正嵌套原文。
  要求完整输入在实际消息中出现，块集合与引用一致；不能通过用户伪造 JSON 生成来源记录。
  先编写新增测试，观察因缺少实现而失败；实现过程中又复现修正历史在输入之后含有 User 反馈，
  因而不能假设运行输入永远是最后一条 User 消息。已改为完整输入精确匹配，未取消匹配检查。
- 集成测试对照脚本 Provider 收到的实际 system 与持久记录，并验证 Reviewer 的 S1 原文、
  内容修正返回主 Agent 的 S2 原文都被单独记录。
- 回归：model_input_manifest/general_agent/responses/agent_file_tools/tool_context，101通过（18.97秒）；
  response_file_tools/provider_continuation_store/agent_task_notes，29通过（6.29秒）；
  `PYTHONPATH=packages/models/src apps/api/.venv/bin/python -m pytest -q -o asyncio_mode=auto packages/models/tests --tb=short`，127通过（0.25秒）。
  Ruff 与 git diff --check 通过。没有前端改动或数据库迁移。

### 临时切换 DeepSeek 的首轮测试

- 用户允许暂时用 DeepSeek 后，核对本地目录与凭据：deepseek-v4-flash 已启用、Key 存在，地址为
  https://api.deepseek.com。没有修改目录、默认思考设置或线上服务器。
- 使用与 Strata 笔记样本相同的 PDF/问题/never 策略；报告 `/tmp/general-agent-task-notes-deepseek-20261019.json`。
  Run `1faccbcd-bd67-44f1-8425-827e8edd4793`；Workflow `8e2d29c8-73ef-4493-aac1-6e0bd1847538`；
  Session `634f01d1-fd87-4656-a2fe-35578e5c401c`。
- 结果失败，3.294秒：首轮成功生成 read_file_content，实际从1,542个解析块中返回28块，
  首轮已报告输入2,682、输出106、缓存0、推理0 Token，费用未提供。笔记工具尚未执行。
- 第二个逻辑 Attempt 在 HTTP 派发前被本地适配器拒绝：
  `DeepSeek tool continuation requires backend-preserved reasoning state.`
  两轮实际思考配置均为 enabled=true、medium 映射 high；首轮保存的原始消息字段只有 role/content/tool_calls，
  没有 reasoning_content，因此没有产生私有续接记录。这是观察到的协议差异，不是证明模型丢失了推理。
- 两份实际请求的原生工具清单、运行原文集合清单均校验一致；第二份记录实际包含28个文件块。
  联调脚本的总用量因失败 Attempt 缺少 usage 仍为 null；已报告首轮用量应单独保留，不能写成全部0或全部未知。
- [DeepSeek 官方思考文档](https://api-docs.deepseek.com/guides/thinking_mode/) 要求工具请求保留历史思考字段；
  初次检查时尚不能判断官方端点是否接受这一条没有返回思考字段的消息续接；后续授权诊断结果见下节，不补造推理正文。
- 准备进行一次历史第二轮完整请求直连诊断，但执行审批拒绝：需要明确授权外发已保存的 PDF 原文/工具结果与系统提示，
  并产生一次新的模型费用。首次被阻止时诊断请求未发送；随后用户明确批准完整重放一次。

### DeepSeek 缺少思考字段的单次续接诊断与修复

- 获授权后只重放失败 Attempt `1488feba-8b4d-4bb6-b13e-dd5e0f57105f` 对应的完整请求一次，
  包含平台提示及28个文件块；绕过本地校验但保留原消息和其他参数，未执行返回的新工具，未自动重试。
  报告 `/tmp/deepseek-missing-continuation-diagnostic-20261019.json`。
- 官方接口 HTTP200，1.350秒，finish_reason=tool_calls，请求 read_file_content；本轮返回 reasoning_content。
  输入7,993、输出186、合计8,179 Token；缓存输入2,688、推理70（各为输入/输出的子集）；费用未报告。
  请求 SHA-256：`2affeac06c8a16349ddcd1ab5242fa0ed7d5d0d80a887151ca70a37f381add41`；
  响应 SHA-256：`0ede6db8fe031f77d2661a1645be974b260ce918b8d55986b0c5734283249456`。
  本次直接诊断用量独立统计，未写成原失败工作流的费用或用量。
- 该样本证明本地“每条工具请求都必须有思考正文”的判断过严，不证明所有缺失状态均可忽略。
  修复允许无私有状态引用的显式 Assistant 工具消息按原样续接；保留已有思考正文，
  仍拒绝错误供应商状态、已有持久引用却缺失内容，以及无法重建 Assistant 调用的旧式 Tool 消息。
- 测试先行：流式/非流式新增用例均在旧实现下因 continuation 错误失败；修复后模型模块127项通过（0.24秒）。
  API continuation_store/task_notes/general_agent/response_file_tools 回归52项通过（11.82秒）。
  未修改数据库、模型默认配置或服务器。完整笔记工作流复测另行记录，不能以一次直接HTTP成功代替工作流验收。

### 修复后 DeepSeek 完整笔记工作流

- 新建测试 Run `508070d4-74c9-4cfe-835a-c25cc7004b9c`，Workflow `7e4d459a-0ffd-47ef-be76-0c2e5b6e85bd`，
  Session `04853bcc-2c20-4017-8ef9-c1892cdba3eb`；相同 PDF、问题、模型及 never 策略。
  报告 `/tmp/general-agent-task-notes-deepseek-fixed-20261019.json`。这是独立完整流程验证，不是再次重放历史请求。
- completed，12.470秒，5次模型请求全部完成；输入41,344、输出1,455、合计42,799 Token，
  缓存输入25,088、推理219为对应子集；费用未提供，不能将聚合默认0解释为免费。
- 工具依次成功：read_file_content（28块）、save_task_note（真实S1来源、未读范围、后续行动）、
  read_task_note（读取刚保存的ID）。笔记ID `435fc79f-ffdb-47f4-b5e8-f8864df8d3a3`。
  没有逐页扫描或重复文件读取。最终回答说明论文研究 IKEA 客户支持的大模型自动化分析，并说明未通读。
- 第一次候选使用 `[S1, page:1:line:14–17]`，不符合当前短引用语法，后端记录缺少可识别引用并返回修正。
  第二轮改为 `[S1]`，refinement_count=1、workflow_round_count=2，格式修复调用计数为0；
  修正用量输入10,777、输出494，包含在总量中。本例是来源格式修正，不是 Reviewer 内容纠错，Reviewer调用为0。
- 5次请求的原生工具/运行来源清单校验通过；后4次均携带28块原文。
  6条消息/节点记录 SQL 正文为空、MinIO对象哈希通过；消息/工作流重复GET最终正文一致。
- Playwright打开并刷新该会话后，最终正文与4条来源候选恢复；never策略对应“尚未审核”，不虚称已审核。
  快照 `output/playwright/general-agent-deepseek-notes-restored.yml`。没有从浏览器再次发送问题。
  npm启动快照命令曾延迟返回，最终成功；浏览器控制台仅开发模式提示，未出现error。

### 执行中读取进度工具真实验证

- 新工具 get_task_read_coverage 已按目录加载，仅读取当前任务的持久读取记录及授权解析清单；不加载全文到模型输入。
  初始集成测试先因工具未注册失败；实现后复现“新增读取之后相同进度参数仍被重复检查拒绝”，
  已将对应文件新增的实际块集合纳入该工具重复判断。未新增块的重复查询仍拒绝。
- 确定性测试覆盖未读/已读、空解析、重复读取去重、搜索范围不冒充已读、未读位置分页、伪造块/快照变化、
  当前任务隔离、无权访问的Run、撤销后不再发送历史、SQL正文为空及对象化恢复。
  一次测试误用了真实 ObjectStorage 而非测试依赖，被URI校验拒绝；已改用测试客户端同一存储，不计为业务缺陷。
- 回归命令：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_task_read_coverage.py apps/api/tests/test_general_agent.py apps/api/tests/test_agent_task_notes.py apps/api/tests/test_tool_context.py apps/api/tests/test_workflow_execution_tasks.py --tb=short`。
  58通过（11.42秒），修改范围Ruff和git diff --check通过。
- 按用户后续“暂时用deepseek替代strata完成请求测试”的授权，使用 deepseek-v4-flash、never、FULLTEXT03.pdf。
  Run `cf8668bb-222f-4bc4-a941-aeab8de240ce`；Workflow `4ad2d9dc-bdc3-4c88-ac2d-c05039d6c780`；
  Session `bf2caabd-d127-467e-b20c-e1fdbbdffb0c`；报告 `/tmp/general-agent-read-coverage-deepseek-20261019.json`。
- completed，9.440秒，5次模型请求；输入27,463、输出691、合计28,154 Token，缓存输入17,920、推理52为对应子集。
  费用未报告。没有调用Strata或部署，不将此样本计为Strata可用性验证。
- 工具顺序 describe_tools → get_task_read_coverage → read_file_content → get_task_read_coverage；全部成功。
  第一次查询0/1,542块已返回；read_file_content使用返回的block_id=page:1:line:1、before=0、limit=20；
  第二次查询20块已返回、1,522块未返回。没有继续顺序读取，没有从查询结果获得正文。
- 另用本地只读SQL/ORM及ObjectStorage.verify_object核对4条实际工具记录：SQL input/output正文均为空，
  body_ref_json存在，4个MinIO对象哈希通过；两个保存的进度结果分别为0/1,542与20/1,522，model_understanding_verified=false。
  5条消息/节点正文对象哈希也通过；重复GET的消息/工作流最终结果一致。
- Playwright打开会话、刷新、读取快照并查看截图；“工作流已完成”、研究对象、20/1,542/1,522及未通读说明均恢复。
  `output/playwright/general-agent-read-coverage-restored.yml`、同名png；控制台仅开发模式信息，无error。
  模型答案超过要求的两三句话并提到内部字段名，已记录为回答质量限制；本批未改前端文案/样式或强行删改模型正文。
  本次不是运行中刷新、用户主动取消、Reviewer审核或全文阅读的复测。

### 委派真实联调失败及审核原文累积修复（2026-10-20 核对）

- 既有报告 `/tmp/general-agent-delegation-deepseek-20261019.json`，Run
  `79c8fc71-d377-4b6b-90d1-037666f9db0a`，Workflow
  `d0f38f15-ced5-4fe7-9d88-1a4b1dd87deb`。该次调用仍依据此前 DeepSeek 替代授权执行，
  使用 FULLTEXT03.pdf、always 策略和 planner 绑定；本次原因核对没有重发历史请求。
- 结果 failed，150.220 秒，29 次模型请求完成；输入 533,982、输出 26,175，合计 560,157 Token；
  缓存输入 236,672、推理 17,014 为对应子集，金额未报告。一次真实委派、子任务读取和 Handoff 已发生，
  但多轮审核后没有最终回答，不能记为完整委派流程通过。工作流记录 7 轮、6 次修正。
- 最后 general_agent 节点没有创建新 Attempt。读取本地 MySQL/MinIO 已保存候选、审核与工具原文，
  重建其 objective、feedback、review_sources 后与节点 input_hash 完全一致：102,006 字符、12 个审核来源。
  用当前 ContextBuildCreate 实际校验，唯一错误为 additional_user_input / string_too_long，
  对应 100,000 字符上限。先前把每轮新增审核来源永久累加，导致在供应商请求之前失败；
  本例不是供应商 JSON 错误或末尾内容丢失。
- 本次修复仅改变下一次修正的原文选择：当前候选引用、本轮审核决定引用、本轮新增补读的并集。
  历史候选、全部来源映射、原始工具结果和 MinIO 对象保留；不删除历史证据、不修改旧工作流，
  不通过新增固定轮数或取消审核来绕过问题。对同一失败输入只读计算后为 44,576 字符、8 个来源。
  这证明该输入不再因历史审核原文累加超过此字段限制，不代表任意大输入或模型语义修正均已解决。
- 测试先行：新增三轮审核、两次内容修正用例在旧实现下失败，第三次输入仍为 S2/S3；
  修复后为当前候选 S1 与本轮 S3，旧 S2 仍从持久结果读回。首轮测试夹具漏填 raw_response，
  修正夹具后才取得上述业务失败，不将夹具错误计为实现缺陷。
- 回归命令：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_general_agent.py apps/api/tests/test_general_delegation.py apps/api/tests/test_general_review.py apps/api/tests/test_workflow_execution_tasks.py apps/api/tests/test_worker_evidence.py --tb=short`。
  76 通过（22.40 秒）；修改文件 Ruff 与 git diff --check 通过。该次使用测试依赖，不是新的真实模型验收。
- 开始修复前已 fetch 并确认本地/远端均为 282313c，包含用户更新的前端和 ee0d880 委派实现。
  本次没有修改前端文件，也没有部署。仍需在修复后执行真实委派/审核最终交付和浏览器验证。

### 修复后 Strata 委派、审核与浏览器恢复（2026-10-20）

- 代码提交 8453ddf，工作区干净。重启本地 API 加载修复前核对无近期活动工作流；
  两条九月历史 RUNNING 记录早于 API 进程启动，未更改其状态。没有部署服务器。
- 命令：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python deployments/verification/check_live_general_agent.py --provider strata --delegate-role planner --policy always --report /tmp/general-agent-delegation-strata-20261020.json --question '请将附件首部研究对象核对委派给 planner 一次：只读取少量开头原文，给出论文研究对象和真实短来源引用，不要通读全文。主 Agent 收到交接后对照其原文，用两三句话回答论文研究对象及未读范围，使用实际来源 [S1] 等，不把子任务意见当作已验证事实。不要重复委派，也不需要运行代码。'`。
- Run `fcd6742d-346d-47fe-987f-4734645bc4e2`，Workflow `48643f91-c3c5-47de-bf29-969a16d21e1a`，
  Session `3c4deed8-a482-4ee4-80cf-f93677cfbcaf`。completed，119.774 秒，6 次模型请求，
  输入 50,530、输出 5,428、合计 55,958 Token，缓存输入 17,718。推理用量未知，金额未报告。
  本次没有 DeepSeek 调用，也未重放此前失败请求。
- 实际工具为一次 delegate_task、Planner 首部 offset=0/limit=30 读取（返回28块）、主 Agent
  offset=13/limit=10 补读；三个工具完成。没有再次委派、顺序通读或 Python/Shell 执行。
  Reviewer 收到38个原文块次（可有重叠），一次返回审核决定；未自行补读，未触发内容修正或格式修正。
  因此该样本证明委派至最终交付，不作为多轮修正真实分支验收。
- Handoff `021fea60-bf26-42ff-8b4a-62471b8390dc` 为 agent_handoff.v3，保存子候选及 S1 引用。
  只读 SQL 验证 handoff_json 正文为空，关联 MinIO 快照哈希通过。另7条消息/节点正文为空且对象哈希全部通过。
  重复 GET 的消息及工作流结果一致。6份实际请求的现有输入清单核对通过；委派工具包内原文未纳入原生文件块统计，
  该已知缺口仍保留为待做项，不能因清单校验成功宣称计数完整。
- Playwright 在运行中打开并刷新会话；此后未刷新，页面自动显示“工作流已完成”及最终答案。
  再次刷新后正文仍在，实际查看截图；用户新增的“模型 Agent · Planner”节点也显示正常。
  证据：`output/playwright/general-agent-delegation-strata-running.yml`、
  `general-agent-delegation-strata-completed.yml`、`general-agent-delegation-strata-restored.yml/png`。
  浏览器控制台仅开发提示，无error；未通过浏览器重发问题，本例未测试主动取消。
- 回答指出 IKEA 客户支持中的大语言模型自动化分析及未读范围，但同时使用“未作推断”和“仅从标题推断”，
  并暴露 block 标识等技术细节，存在措辞/可读性问题。Reviewer 的 supported 不代表平台独立确认全文语义准确。
- 同步后的用户前端未改动：`npm run typecheck` 通过；
  `npx tsx --test tests/agent-workflow-view.test.ts tests/agent-workflow.test.ts tests/claim-evidence.test.ts tests/workflow-recovery.test.ts`
  34项通过。没有修改样式、布局或前端文案。

### 委派原文的实际输入记录（2026-10-20）

- 新增独立 `delegated_source_manifest.v1`，在供应商请求前、历史裁剪后与 effective_messages
  一起保存至原有 MinIO 请求对象。记录消息位置、供应商工具调用ID、Handoff/子任务/Agent Run、
  原始文件调用ID、快照、块编号及文本和块哈希；不复制原文到MySQL，不变更表或原有两种清单格式。
- 只统计 delegate_task 的成功原生 Tool 结果中 original_sources，模型候选和用户粘贴JSON不计入。
  不完整/失败/块引用不一致的历史结果保留 unknown 状态，不伪造零读取或有效来源；重复消息按出现次数记录。
  这些记录只证明准备发送的内容，不证明供应商接收或模型理解，权限仍由现有工具和来源校验负责。
- 先添加用例，旧代码因缺少新函数无法收集测试；实现后清单单元用例13项通过。
  三种审核策略的委派集成测试读取实际保存的请求对象，与测试Provider收到的完整消息逐项比较，
  确认父请求中已记录子任务原文、真实Handoff ID，且未修改正常调用数。
- 回归命令：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_model_input_manifest.py apps/api/tests/test_general_delegation.py apps/api/tests/test_general_agent.py apps/api/tests/test_response_file_tools.py apps/api/tests/test_tool_context.py --tb=short`。
  72通过（12.55秒）；修改范围Ruff通过。
- 只读上一轮Strata Run `fcd6742d-346d-47fe-987f-4734645bc4e2` 的真实MinIO请求，
  两个主Agent请求各重建出28块委派原文，其他4个请求为0。所有6个旧请求如实标为
  historical_manifest_absent，没有改写旧对象，也没有重复调用模型。
  新版本随真实请求保存该清单尚未验证；本项未重启服务、未改前端、未部署。

### 通用 Agent 长答案保存（2026-10-20）

- 新增40,000字符中文候选用例，旧实现失败为 agent_workflow_internal_error。
  核对发现通用循环复用了 FinalSynthesisModelOutput 的16,000字符限制，且后续消息保存还会经过
  MessageCreate 的相同HTTP输入限制；最终节点原先也按旧64KiB容量检查。
- 通用循环现直接组装 FinalSynthesisOutput，最终节点按已有通用节点1MiB序列化容量检查；
  内部生成的Assistant消息使用独立类型，沿用原会话归属检查、序列分配、事务和对象存储。
  FinalSynthesisModelOutput仍约束旧Controller生成；HTTP MessageCreate和用户消息限制未放宽。
  读取结果字段未更名，历史v1对象可读，没有数据库迁移、前端修改或全文写回SQL。
- 测试检查12,000和40,000字符中文答案均完整保存，消息GET和工作流result重新GET内容一致；
  另断言旧Controller超长JSON与公开超长消息仍拒绝，内部生成类型不能伪装为user角色。
- 回归：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_general_agent.py apps/api/tests/test_general_candidate.py apps/api/tests/test_agent_workflows.py apps/api/tests/test_conversation_body_storage.py --tb=short`，
  103通过（22.40秒）。Ruff首次报告新增测试导入排序，修正后复查；本次没有新增模型调用，
  未执行真实模型长答案生成或浏览器长答案验收。

### 浏览器发起通用循环与真实停止（2026-10-20）

- 核对发现页面仍创建 model_only/1.1.0，不能用脚本验收替代普通入口。此次仅修改请求构造和类型，
  显式选择general_agent/1.0.0/general_tools_v1，保留模型、思考、审核及Planner绑定，
  不发送旧max_workflow_rounds；服务端保留的缺省字段不用于通用循环限制。旧API和结果读取继续兼容。
- 三种策略的新增构造测试先因缺少新入口失败，接入后29项工作流/视图/恢复测试通过；
  `npm run typecheck` 与修改文件ESLint通过。无样式、布局、产品文案修改。
- Playwright从新对话页面选择Strata模型与Agent模式，只发送一次
  “浏览器停止验证20261020：请详细分析 Python 列表和元组的设计差异、复杂度与典型应用。不使用工具，不委派任务。”
  实际持久请求确认上述general定义，所有角色均为strata/tiel-coder-35b-a3b-ud-q4_k_xl，always策略；未调用DeepSeek。
- Run `26a70718-0b5a-4c01-a25d-09c76f446887`，Workflow `8cad1ecd-d959-4625-8058-606a89eec306`，
  Session `6f408253-f6f4-4648-9fa0-465e376275ad`。执行进入Reviewer时通过页面停止，取消POST返回200/cancelled。
  服务端开始至终态49秒（DATETIME秒精度，不是取消确认延迟）；3次模型请求，2次completed、1次outcome_unknown。
  两次已知调用输入6,497、输出2,729，已知合计9,226 Token，缓存输入4,096；第三次用量及总费用未知，
  全部推理用量未知，不能把已知子集当总量，也不能宣称远端停止生成或计费。
- 一分钟后只读核对仍为cancelled，Attempt仍3条、started为0。页面显示已取消/未生成最终回答，
  输入未发送草稿后发送按钮可用；重新打开持久会话链接仍恢复取消终态，没有重新发送问题。
- 证据：`output/playwright/general-agent-browser-inflight.yml`、`general-agent-browser-cancelled.yml`、
  `general-agent-browser-cancelled-input.yml`、`general-agent-browser-cancelled-restored.yml`。
  第一次点击停止因窄窗口详情遮挡而超时，正常关闭详情后取消成功，未使用强制点击；已在阶段4记录，不修改用户布局。
  浏览器在取消时记录流连接ERR_INCOMPLETE_CHUNKED_ENCODING，但服务端取消与页面恢复均成功；不隐去该错误。
- 新输入清单已随这3次真实请求存入MinIO且重建一致。该无附件样本的委派原文数为0，
  只验证新字段实际保存，不等同“含委派原文的新清单”真实验收。此前按user_request SQL筛选诊断未命中对象化正文，
  后改为真实Workflow/Run ID查询；未因此修改历史记录。

### 任务实际模型输入分页查询（2026-10-20）

- 新增服务 `task_input_coverage.py` 和目录工具get_task_input_coverage，复用Task/Attempt/工具调用记录及MinIO请求对象。
  当前任务每页至多20条请求，核对原生、委派、runtime来源清单，输出逐请求状态、是否完整审计、块次数及文件快照汇总。
  正文与私有推理不返回；无请求、历史未记录、损坏、撤销权限分别处理，不都写成0。
- 测试先行：新增用例在模块不存在时失败。实现后验证重复块去重及出现次数、三种输入渠道、旧清单缺失、消息被改写、
  工具目录加载和真实执行路径、请求分页、同Run不同Task隔离、错误Run拒绝、文件删除后历史不再传入模型。
  测试使用本地测试数据库/对象存储依赖，不调用外部Provider。
- 相关回归：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_task_input_coverage.py apps/api/tests/test_task_read_coverage.py apps/api/tests/test_agent_task_notes.py apps/api/tests/test_general_delegation.py apps/api/tests/test_general_agent.py apps/api/tests/test_model_input_manifest.py --tb=short`，
  68通过（16.29秒）。之后扩展runtime渠道和错误Run断言，单文件4项再通过；修改范围Ruff与git diff --check通过。
- 工具输出是请求页，不是全任务完成证明。模型未使用过的新工具真实调用、全任务累计检查点和恢复的一致快照仍待验证/实现。
  本次不改前端，不重启服务、不部署，没有新增付费模型调用。

## 尚未验收的完整目标

继续完成内容修正后的真实交付及新后端完整实现；浏览器发起/在途停止已有上述样本，不能扩大成整项目完成。
逐单元审核与来源查询已有代码、确定性测试和真实条件审核样本；任务笔记已有确定性测试及上述 DeepSeek 保存/读回成功样本；Strata笔记样本仍因502失败；
按需委派及 Handoff 已有实现、确定性测试及上述 Strata 单轮审核真实最终交付；
仍需补齐执行检查点/完整模型输入覆盖、多轮审核内容修正真实交付、
真实存储/权限与恢复测试。用户已明确 Python/Shell 接入及跨进程续跑在 Agent 真实调测完成后再检查；
本批不提前实现，不影响继续补齐当前通用循环后端范围。
