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

### 通用循环执行观察快照（2026-10-20）

- 新增 `task_execution_checkpoint.py`，新循环通过可选回调在模型返回及工具返回后自动保存快照；
  旧工作流未传回调，不额外写快照。复用现有对象登记表及MinIO JSON/Markdown协议，没有数据库迁移。
  记录实际Attempt/工具/笔记引用、目标、可用工具版本及前一快照，不宣称自动续跑或模型理解。
- 接入前的集成测试复现“完成模型调用但快照0条”；接入后无工具一条、一次工具调用三条快照，
  版本链正确。重复同一观察不增加对象，错误Run读取拒绝；损坏对象哈希拒绝。
  模拟对象校验失败时，工作流失败、无最终答案、只发生一次模型调用，SQL没有成功快照登记。
- 回归：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_task_execution_checkpoint.py apps/api/tests/test_general_agent.py apps/api/tests/test_general_delegation.py apps/api/tests/test_workflow_execution_tasks.py apps/api/tests/test_agent_task_notes.py apps/api/tests/test_task_input_coverage.py --tb=short`，
  66通过（27.82秒）。之后收紧快照工具版本为实际可用版本，检查点单文件3项再通过。
  修改范围Ruff及git diff --check通过。
- 未执行真实模型/本地MySQL-MinIO快照写入、并发竞争或跨进程恢复验收；下一步应先验证真实对象和固定历史集合查询。
  本次没有新增Provider调用、没有前端修改或部署，Python/Shell与跨进程续跑继续暂缓。

### 检查点与输入查询真实存储、委派期限错误修复（2026-10-20）

- 代码64b4fc8，启动时工作区干净；检查无近期活动任务后重启本地API，旧九月记录保持原样。
  使用Strata、FULLTEXT03.pdf、Planner绑定和on_verification_failure，只发起一次新请求。
  问题为“请将附件首部论文主题核对委派给 planner 一次，只读开头少量原文，要求真实短引用，不要通读。
  主 Agent 收到交接后，通过工具目录加载 get_task_input_coverage 并查询当前任务实际输入，然后依据交接原文用两句话说明论文研究对象与未读限制，引用 [S1] 等实际来源。
  不要把输入记录等同于理解，不要重复委派，不需要运行代码。”
- 报告 `/tmp/general-agent-checkpoints-strata-20261020.json`；Run `44701934-f28f-4719-85df-11204657aa7f`，
  Workflow `630300f1-69c3-4d09-a83a-af96b845a1f8`。失败，601.975秒，7次请求，6次completed、最后一次outcome_unknown。
  已知输入37,298、输出4,216、合计41,514 Token，缓存输入17,794；最后请求用量、全部推理用量及总费用未知。
  一直观察同一脚本/工作流，没有因等待而重发；第7次请求长时间无返回，不能断言是重复思考或供应商排队。
- 主Agent读取20块并委派；子任务额外多次搜索工具目录后加载和调用输入查询。该查询成功返回当前子任务4个请求，
  每次20块原文、audit_complete均为true。原文来自显式交接输入，不把目录查询或文件搜索当作额外阅读。
  多余目录搜索是效率问题，不是重复文件读取；子任务没有提交结果，Handoff/最终回答均未完成。
- 真实MySQL/MinIO登记28个快照对象（14份JSON及对应Markdown），全部哈希通过；逐Task版本链与Attempt归属核对通过。
  快照只证明当时执行事实已保存，不代表存在完成终态快照、自动恢复或成功回答。
  7份实际请求清单重建一致；本例仍没有成功返回的委派工具包，不能作为非空delegated_source_manifest的真实验收。
- 运行在600秒任务期限到达时中断了子供应商请求，Attempt正确记录provider_outcome_unknown，
  但外层TimeoutError被错误转换成agent_workflow_internal_error/outcome_is_known=true。
  新回归在2秒期限下复现；最初1秒夹具先被最低剩余时间拒绝，调整为2秒后才复现真实缺陷。
  修复检查持久Attempt的未知结果，不再仅凭外层异常类判定已知；不修改历史失败记录。
- 回归：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_general_delegation.py apps/api/tests/test_general_agent.py apps/api/tests/test_workflow_execution_tasks.py apps/api/tests/test_task_execution_checkpoint.py --tb=short`，
  54通过（23.87秒）；Ruff及git diff --check通过。未为修复额外发起真实模型请求。
- 验证脚本新增检查点对象/链/归属核对，并保留首次测得耗时。对本报告执行--observe仅只读补证据，
  确认耗时仍为601.975秒，没有把事后核对时间加入执行耗时。无前端修改、无推送部署、无DeepSeek调用。

## 第7次请求中断后的只读核查（2026-10-20）

- Attempt `d0722f6b-9d04-4701-865a-3cf78608d997`：北京时间09:31:37开始、09:40:09结束，约512秒；受整个任务600秒期限影响，不是该请求单独运行600秒。
- 本地 ORM 只读核查：`OUTCOME_UNKNOWN` / `provider_outcome_unknown`，请求对象引用存在、响应对象引用不存在；首个可见Token时间、输入/输出/推理用量、延迟与供应商请求ID均为空。没有重新调用模型，没有修改历史记录。
- 代码核查：Strata `generate()` 仅提取 COMPLETED；适配器的正文/工具片段在内存聚合。模型服务收到 CancelledError 后记录未知结果，现有 `preserve_stream_usage` 只捕获 ModelProviderError，不覆盖本次取消。缺少持久部分正文与进度，无法确认是长输出、等待或停顿；“无完整返回”不能表述为“没有收到任何 SSE”。
- 新增[流式进度、中断保存与续接规划](phase-7-tool-runtime-plan.md#流式生成进度中断结果保存与续接2026-10-20)，状态未开始。后续必须区分用户停止和任务到期，未完成正文不能直接视为最终答案；本次没有实现或执行相关新回归测试。

## 流式中断正文保存首步实现（2026-10-20）

- 先提交规划 `4b14c88`，然后补充共享 SSE 与模型服务中断保存。本批只运行离线协议及 API/工作流测试，未调用 Strata/DeepSeek，未重启服务、未推送部署。
- 适配器通过 `model_stream_progress.v1` 保留正文、不可执行的工具参数片段、正文哈希、接收时间、推理字符数及最新已知用量；不复制私有推理。取消沿用原异常，单请求期限仍正确转换成超时。聚合调用与直接流式消费使用同一包装器。
- 模型服务将草稿写入私有对象，使用既有 Attempt 响应引用；取消保持 outcome_unknown，超时保持 timed_out。未知输出用量和最终费用仍为空。对象存储失败时不填写 URI，不抹去已知用量。失败原有供应商脱敏诊断继续保留。没有新表或迁移。
- 测试先行：适配器两种消费方式均在原实现因缺少 progress 失败；API 取消路径因没有响应 URI 失败。第一次包测试未启用 asyncio_mode，只是测试配置失败；启用 auto 后才复现目标缺陷。实现期间发现替换为 CancelledError 子类会破坏 Python 3.11 timeout 的精确类型判断，已改为保留原异常；API 超时预期按现有 504 校正。回归发现原错误诊断被草稿包装覆盖，已修正并保留原断言。
- 验证命令：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q -o asyncio_mode=auto packages/models/tests apps/api/tests/test_responses.py apps/api/tests/test_general_agent.py apps/api/tests/test_general_delegation.py apps/api/tests/test_workflow_execution_tasks.py --tb=short`。233通过，24.54秒。包含实际 SSE 解码的取消片段、没有输出/仅推理、API 取消与期限、存储失败、正文哈希、未知用量、既有三策略/委派/停止回归。SQLite与内存对象存储不替代真实MySQL/MinIO验收。相关文件Ruff及git diff --check通过。
- 最后核查补充保留失败异常携带的供应商请求ID/HTTP尝试信息，重新运行 `apps/api/tests/test_responses.py`，51通过（10.26秒）；Ruff及差异格式检查再次通过。
- 剩余：生成中分批持久化、原始结束信号记录、已保存草稿的受控续接、真实供应商与存储验收。当前保存发生在可处理的中断路径，不保证进程崩溃恢复；不会因有草稿自动执行工具或产生最终答案。历史第7次正文仍不可恢复。

## 分批保存后的Strata真实联调（2026-10-20）

业务代码为 `fe431c8`，验证期间仅调整观察脚本/测试；本地API重启加载代码，未修改服务器。用户确认Strata恢复后，只使用Strata与本地MySQL/MinIO；没有DeepSeek调用。重启前发现两条9月历史残留Attempt/Workflow早于当前API启动，未修改这些记录。

### 委派、审核与任务期限

- 报告 `/tmp/general-agent-stream-progress-strata-20261020.json`；Run `29e77b4b-57e5-4995-a5ee-fb739a10694d`，Workflow `3f594255-2c49-4b91-bc05-cc397c44aa53`，Session `ffa37dc5-89fc-4ad4-9195-8d5dc8c46425`。复用此前首部核对/一次Planner委派/输入覆盖查询的完整问题，未降低审核策略，未重置任务期限。
- **最终回答验收失败**：状态 `outcome_unknown`，约604.022秒，19次模型请求（18 completed、最后1次timed_out），最终错误timeout且outcome_is_known=false。已知输入166,596、输出35,012、合计201,608 Token，缓存输入71,804；最后请求用量、各请求独立推理用量和费用未知。不能把known_usage的推理0或修正费用小计0解释成实际免费/零推理。
- 两轮工作流、一次内容修正、一次格式修正（请求/成功均1，失败0），但内容修正方向错误，不能计为“修正后成功交付”。第10、18次已完成请求分别报告输出10,410和16,129 Token；不能在缺少独立推理用量时将这些计数全解释为可见正文。
- 已观察18个Attempt的在途/中断对象，正文长度/哈希核对通过。最后一次中断保存273个规范化事件、940个推理字符的计数，正文0字符、无工具参数、用量未知；没有保存私有推理正文。这次能确认在途有接收进度，但没有可直接交付的末轮正文，而不是再次把“未保存”当成“上游没有生成”。
- 委派完成，Handoff `f1a0434c-af39-4992-beba-1c40f657e771` 的SQL正文为空、MinIO对象存在且哈希通过。19份输入审计清单重建一致；5份主任务请求各含20块委派原文且delegated_sources_status=verified。68个执行观察对象哈希及JSON版本链/Attempt归属通过；消息/节点正文对象验证通过。存储通过不等于最终结果成功。
- **已定位审核对象错误**：主任务首个Attempt `08e3ef0c…` 没有文件输入，这是实际请求记录；Reviewer查到的 `75ad04cc…`、`3a9af3c8…`、`4305fd65…` 都属于Reviewer自己的Task，各有20块运行时原文。它据此错误断言主任务首个ID“不存在”、要求全部改成20块。模型输入审计中的角色与来源计数确认它比较了不同Task；主Agent随后坚持自己的记录，产生不必要修正。下一步需固定被审Task/请求集合及读取授权，不靠重复付费或放宽期限解决。
- Playwright运行中进入会话并刷新后，原工作流继续从委派推进到审核/修正；终态自动显示失败，刷新后仍恢复该状态，未伪称完成。证据：`output/playwright/general-agent-stream-progress-{running,refreshed,terminal,restored}.yml`。没有最终答案，因此最终答案恢复未通过；本次没有修改前端。

### 已收到输出后的显式停止

- 单独短测试报告 `/tmp/general-agent-stream-cancel-strata-20261020.json`；Run `45a95845-708b-404c-b145-fa06431c05ca`，Workflow `a689429a-9dd6-48d2-bcb2-57962418e32b`。使用新增验证选项 `--cancel --cancel-after-progress`，仅在观察到持久接收进度后停止该测试Run，不重试上面的完整工作流。
- 结果cancelled，总观察9.618秒；取消接口确认0.165秒，之后5秒没有新Attempt，也没有STARTED残留。唯一Attempt为outcome_unknown，输入/输出/缓存/推理Token和费用均未知；无法保证上游停止计费。
- 中断对象保存86个事件、249个推理字符计数、1个未完成工具参数片段和空正文，哈希核对通过；不将片段执行为新工具，不生成最终回答。
- 浏览器打开取消会话恢复“已取消”，输入框可编辑，填写测试草稿后发送按钮可用，随后已清空且未发送。证据：`output/playwright/general-agent-stream-cancel-{restored,input}.yml`。实际停止由验证脚本调用API，不冒充浏览器点击停止验收。
- 观察脚本增加Handoff对象核查、推理字符计数及“看到已保存进度后再取消”选项；仅对终态执行只读`--observe`补证据，保持原604.022秒耗时，没有重发模型。新增“无新增事件不重复写对象”断言后，4项分批保存回归通过（0.91秒）；Ruff和差异检查通过。

## 固定审核输入快照修复（2026-10-20）

- 针对上一节真实发现的错误，新增主/子候选的 `task_input_snapshot.v1`，通过已有快照登记表和MinIO协议保存固定请求ID、状态和请求对象哈希；候选与Handoff保留可选引用。无数据库迁移，无前端修改。
- Reviewer得到平台授予的被审快照，并自动加载 `read_task_input_snapshot`；调用旧当前Task查询会明确拒绝，不把当前Task静默替换成被审Task。分页返回固定Task和请求总数，不暴露提示词/原文/私有推理/URI。原请求对象、快照哈希、Run/Task/用户归属和当前文件访问都校验；历史重用也重新校验。
- 测试先行：缺少候选固定引用导致原场景失败；实现后相同场景可核对主任务首轮0块、后轮1块，而Reviewer自身带原文不改变主任务记录。另发现旧Handoff缺少新可选字段时被严格dict比较拒绝，先复现再修正为仅比较时补None，原对象和幂等哈希不变。
- 其他回归：Reviewer误查旧工具后改读正确快照；新增更早排序请求不改变已捕获页面；重复保存不新增对象；无授予、错误Run、另一用户归属拒绝；快照/请求损坏拒绝；文件撤权拒绝；快照上传校验失败不发布引用、不调用Reviewer。
- 命令：`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_task_input_coverage.py apps/api/tests/test_general_agent.py apps/api/tests/test_general_delegation.py apps/api/tests/test_task_execution_checkpoint.py apps/api/tests/test_agent_task_notes.py apps/api/tests/test_workflow_execution_tasks.py apps/api/tests/test_agent_workflows.py apps/api/tests/test_general_candidate.py apps/api/tests/test_general_review.py apps/api/tests/test_tool_catalog.py apps/api/tests/test_task_read_coverage.py --tb=short`，177通过（38.18秒）。使用SQLite/内存对象存储和脚本化Provider，不是新的真实模型验收。
- 提交 `019dc66` 时尚未重启API或发起付费请求；加载该提交后的真实结果见下一节。跨进程恢复及Python/Shell接入继续后置。

## 固定快照的真实保存与审核使用缺口（2026-10-20）

本地API重启加载 `019dc66`，使用相同的首部核对、一次Planner委派、主任务输入覆盖查询问题与
`on_verification_failure` 策略。仅发起一次Strata工作流，后续 `--observe` 只读补充证据；
未调用DeepSeek，未修改前端，未推送或部署。

- Run `202c2ca8-265d-4e78-8523-caa2c3c63696`，Workflow `58274874-5016-41da-9d2c-373810f40069`，
  Session `123a4526-2e26-43c1-b717-fd210cbfe58d`。
  本地报告 `/tmp/general-agent-fixed-input-strata-20261020.json`；
  [浏览器会话](http://localhost:3000/c/123a4526-2e26-43c1-b717-fd210cbfe58d)。
- 执行结果 `completed`，449.962秒，13次模型请求全部完成；输入181,391、输出21,785、
  合计203,176 Token，缓存输入83,898（已包含在输入中）。独立推理Token和实际费用未知。
  Reviewer一次请求报告输出16,339 Token；不得将其全解释为可见审核正文。
  业务轮次1、内容修正0、格式修正0，不是“修正后交付”通过。
- 首次委派因 `unknown_delegated_source` 被拒绝，模型随后使用合法参数完成一次委派；
  Planner三次读取首部共60块，未通读1542块全文。Handoff
  `1e1dbbd3-6233-40f4-a2b5-39f09add3867` 已持久保存。
- Planner快照 `1a92e1d1-00c3-41d5-a6f8-c1fbe5611083` 与主任务快照
  `1e5a229d-157b-465a-9707-96904a35c2b7` 各固定6次请求。
  两份MinIO快照的归属/哈希、12份原始请求的Task归属/URI/字节哈希核对通过。
  8条消息/节点/Handoff记录的SQL正文为空，对象存在且哈希通过；
  48个执行观察对象的哈希及JSON链/Attempt归属通过，13份实际输入清单重建一致。
- **审核使用未通过**：实际Reviewer请求 `0df0a651-7e52-4e12-9fe2-3daa525c3002`
  包含主任务 `candidate_input_snapshot`，工具列表包含 `read_task_input_snapshot`；
  但Reviewer没有调用它，直接将输入统计标为 `unconfirmed`，理由是“缺乏可核实来源”。
  这不是本次请求漏传工具/快照，也不是再次误读自己Task；只能证明平台提供了读取能力，
  尚未证明真实模型使用它完成核对。不得把 `completed` 等同于审核目标达成。
- 内容质量仍有问题：主答案将 `provider_receipt_verified=false` 同时解释为“输入被送达”；
  把首部读取描述为“第1–3页”，但第2页无提取文字。Reviewer指出页码表述不准仍判该单元supported，
  对输入统计返回unconfirmed；后端按既有策略追加未确认说明并交付原文。
  因此本例不能证明审核后答案全部准确或内容修正有效。
- Playwright先在运行中刷新，后观察页面自动显示完成，再刷新恢复同一正文及未确认说明；
  API的消息正文与最终正文一致，再次读取工作流结果一致。证据：
  `output/playwright/general-agent-fixed-input-{running,refreshed,reviewing,completed,restored}.yml`。
  没有为了浏览器检查再次发送消息。
- 验证脚本新增候选快照与原始请求哈希核查，保留空/未知与失败区别。
  `test_live_snapshot_report_detects_changed_request_bytes` 确认即使快照哈希合法，
  原始请求字节变化也会被报告为失败。`test_task_input_coverage.py` 15项通过（4.78秒）；
  两个修改Python文件Ruff及 `git diff --check` 通过。

### 审核首分页输入修复

已为Reviewer新增 `candidate_input_evidence`，由既有 `read_task_input_snapshot` 读取固定前10条请求，
执行同样的Run/Task授权、快照/请求哈希和文件访问检查。返回请求总数、下一页位置与明确的
prepared-input统计，不复制原文或私有推理，不由模型生成统计，也不改变审核策略或自动批准内容。
Reviewer提示说明这些记录本身就是平台证据，不必附PDF短来源；后续页保留按需工具读取。
老记录不回写，无迁移、无前端修改。

测试先增加首轮Reviewer必须获得统计的断言，原实现两项失败（脚本化Provider断言被API映射成
outcome_unknown）；实现后通过，并增加不调用任何审核工具也能获得正确首轮0块/后轮1块记录的分支。
执行与前节相同11个测试文件，179项通过（38.94秒）。修改Python文件Ruff及diff检查通过。
提交 `ba77d78` 时以上仅为离线测试；随后加载本地API的Strata验证见下一节。

下一步验证模型是否正确使用这些实际统计，并完成内容修正和最终答案验收。
不以重复相同付费请求代替修复；原始结束信号与受控草稿续接等完整目标仍保留。

## 审核首分页的Strata复验（2026-10-20）

- 业务代码 `ba77d78`，本地API重启后发起一次相同问题、相同附件、相同条件审核策略的工作流；
  未调用DeepSeek，无前端修改、推送或部署。报告
  `/tmp/general-agent-review-evidence-strata-20261020.json`。
  Run `d7960fc6-e1ca-439d-bf74-6e7ff636ef37`，Workflow
  `90f2ecf0-d471-4ef9-97b9-72c52e9d35c9`，Session
  `9360103c-f1ca-4c17-92eb-526051df3690`。
- **最终交付失败**：603.050秒，16次请求，前15次completed、末次timed_out，
  Workflow为outcome_unknown，无最终答案。已知输入176,900、输出30,290、合计207,190 Token，
  缓存输入86,304；最后请求输入/输出/缓存未知，全部请求独立推理用量与实际费用未知。
  第12/13次分别输出16,033/6,047 Token，不能全部解释为可见正文。
- 首分页已实际传入Reviewer请求 `bd4d3c75-537e-4b9e-85ac-c5fed5dfd999`：
  固定主任务 `1038e209-1d05-4c43-832a-9ea5d628b23b`、快照
  `cf4daeca-e07d-494d-ae9b-241a43b99bb0`，6次请求，block_occurrences依次
  0/28/56/56/56/56，next_offset为空。这些是原生工具与交接原文的出现次数，
  不是56个不同原文块，也不是模型理解量。
- Reviewer补查文件清单、保存一条审核笔记，第一批6个单元发生一次格式修正并成功；
  第二批尚未完成即达到任务总期限。业务轮次1、内容修正0，格式修正请求/成功各1；
  修正请求消耗输入11,777、输出480 Token。不能将其算作内容修正或最终交付通过。
  最后中断记录保留2032个事件、7330个推理字符计数，正文0字符，没有可交付的末轮草稿。
- 主/子候选快照分别固定6/5次请求，归属和请求哈希通过；5条消息/节点/Handoff的SQL正文为空，
  MinIO对象存在且哈希通过；60个执行观察对象哈希通过，16份实际输入清单一致。
- **新发现的角色执行问题**：主Agent实际成功调用delegate_task，但planner子任务在目录中搜索
  “planner”“planner plan”“delegate task planning assignment”，结果均为空；随后保存
  “planner工具不存在、无法委派”的笔记。子任务已收到单独objective及完整run_objective，
  现有提示只有“Complete only the delegated objective”，未明确解释原始问题中的委派步骤
  已由父任务完成、当前执行者就是被委派角色。后端未开放递归委派，权限检查没有失效；
  角色理解与不必要目录搜索仍需改进，不能声称本次发现或执行了新工具。
- 另一个观察范围风险：候选提到此前查询返回5次请求，候选完成快照随后含6次请求。
  Reviewer笔记把它视作数量不一致；两者观察时点不同，仅凭这两个总数不能认定候选错误。
  后续应明确“某次工具查询的固定返回”和“候选完成时固定集合”的区别，按实际记录核对，
  不把后产生的请求反算进此前查询结果。
- Playwright运行中刷新继续跟踪同一工作流，终态自动显示失败，刷新后仍恢复失败；
  无最终答案，因此本例不计为最终回答恢复通过。证据：
  `output/playwright/general-agent-review-evidence-{running,refreshed,terminal,restored}.yml`。

本轮未重复付费重跑。下节记录子任务身份、父/子目标用途及统计观察时点的修复；
既定结束信号/中断结果处理仍待继续。Strata已能运行真实工具、委派和
部分审核，但该复杂场景仍不能在现有任务期限内稳定交付；不把单纯延长期限当作问题已修复。

### 子任务身份与统计观察时点修复

- 子任务输入新增execution_assignment，由后端提供真实role/task_id/parent_task_id、
  delegation_already_started=true、can_delegate=false。指令明确当前就是已被委派的角色，
  只执行objective，原始run_objective用于理解父任务目标；用户约束保留，但不重做父级调度。
  不增加递归权限、不新增planner工具，子任务仍返回原有自然语言候选及Handoff。
- 实时输入查询新增observation_kind=live_query_page与UTC request_list_observed_at；
  时间在选定请求列表后记录，不把后续MinIO读取时间当作查询时点。
  候选快照保存自己的列表观察时间，读取时返回candidate_request_set类型；
  旧快照没有字段时为null，重读/重复保存不改历史时间或请求集合。
- Reviewer说明要求按相同Attempt和观察范围比较，不把较晚快照中新增的请求反算进此前查询。
  时间字段说明查询范围，不证明供应商收到或理解输入，也不构成新的恢复许可。
- 测试先行新增4项失败：子任务缺少明确身份（普通/长正文两个分支）、实时查询无时间、
  固定快照无观察类型。实现后验证新身份与真实父/子Task一致；先查3次、后查4次时旧结果不变；
  固定集合后续新增请求不改变页面/时间；旧对象缺少时间仍可读取且不补造时间。
- 前述11个相关测试文件180项通过（47.23秒）；补充显式offset与末页不等于全集的说明后，
  输入统计17项再次通过（5.00秒），委派身份2项再次通过（0.65秒）。
  修改Python文件Ruff与diff检查通过。
  此轮仅本地确定性测试，没有新的Strata/DeepSeek调用，也没有重启API或部署。
  提示说明只能改善输入，不能凭离线脚本模型断言真实模型一定遵守；真实角色理解与最终交付仍待验收。

## 角色修复后的Strata复验（2026-10-20）

- API加载 `d654960`，相同附件/完整问题/条件审核策略，只发起一次工作流。
  Run `24364a7a-f4c2-491e-8f64-729447b33343`，Workflow
  `c7c6dc17-79fe-4972-abfd-afcb62ed25e6`，Session `ac3157c6-6100-4950-b208-baee27224672`；
  报告 `/tmp/general-agent-role-scope-strata-20261020.json`。终态后只读 `--observe` 补充保存的观察时间。
  未调用DeepSeek，无前端修改、推送或部署。
- **角色路径改善，最终交付仍失败**：planner完成两次读取与一次笔记保存，没有再搜索planner/delegate工具；
  主Agent随后搜索/加载并查询输入统计。602.003秒后达到原任务期限，Workflow为outcome_unknown，
  11次请求中10次completed、末次timed_out，无最终回答。
- 已知输入131,182、输出36,345、合计167,527 Token，缓存输入44,298；
  末次输入/输出/缓存未知，全部独立推理用量和实际费用未知。两次已完成审核请求分别输出16,707、
  16,314 Token，不把它们全算作可见正文。
- 已读取第9次请求 `b3ae21f6-0ba8-4dc9-993c-cceb59beaab8` 的已保存正文：
  2293字符，finish_reason=stop；正文有说明段落、Markdown JSON代码块，并包含当前批次之外的u7等决定。
  这不是本次上游缺结束标识或本地丢末尾字符的证据；该回复不符合当前纯JSON/批次范围要求，
  后端因此执行一次格式修正。修正成功，但消耗输入14,380、输出16,314 Token；
  第二批审核在任务期限内未完成。业务轮次1、内容修正0，不能算内容修正后交付通过。
- Reviewer仍把先前查询的3次请求与较晚候选快照的4次请求视作矛盾；观察时间说明已进入实现，
  但这个样本说明仅增加说明尚不能保证模型正确解释。需要核对原查询的实际记录，
  不能把改用工具提交审核决定当作语义问题也已解决。
- 5条消息/节点/Handoff对象SQL正文为空，MinIO对象存在且哈希通过；32个执行观察对象哈希通过，
  11份实际输入清单一致。浏览器运行中刷新继续跟踪、终态自动显示失败、刷新后恢复失败；
  证据 `output/playwright/general-agent-role-scope-{running,refreshed,terminal,restored}.yml`。
- 验证脚本增加保存快照原观察时间（旧对象保持null）；单项回归通过（0.52秒），Ruff及diff检查通过。

本次停止重复相同付费样本。该记录提交时下一步为实现Reviewer的原生工具提交入口，复用当前已正常工作的工具
Schema、持久调用及字段校验，不再只靠提示词约束纯正文JSON；纯正文旧路径保留兼容。
该调整只能减少格式包装与记录组装问题，不能保证模型判断正确或缩短推理时间。
完整范围、失败行为与验收要求在阶段7维护；实现/离线验证后再做真实测试。

## 原生审核提交首批实现（2026-10-20）

状态：代码与离线回归已完成；`c53001e`首次真实验证因读流失败未交付，
随后`6dd063a`独立样本完成原生提交与最终回答。委派输入核对和内容修正后的完整验收仍未完成，详见后续记录。

- 新增 `submit_review` 1.0.0，仅General Agent当前Reviewer批次提供；主Agent、planner和快速回复
  没有该能力。单元ID枚举/决定数量/来源编号由当前服务端数据约束，模型不能提供Task、Run或候选ID。
- 执行时检查调用Attempt的Task归属、角色与运行状态、候选Run及完成状态、运行中审核节点的候选关联；
  与兼容JSON路径共用逐单元字段/缺少/重复/未知ID及来源校验。只读候选元数据作身份检查，
  不为拒绝另一个Run的目标而读取其候选正文。
- 有效调用通过已有Tool Call写入MinIO后，循环检查停止状态并结束本批。后端读取该已保存决定，
  组装原general_review.v2，不修改供应商原始正文、不创建额外模型Attempt。多批仍共用原任务期限。
  模型输出合法字段不是内容已被证明；三种策略及返工回到主Agent的职责保持不变。
- 非法参数先保留失败工具记录，再进入原有一次有限格式修正；本次修正仍使用兼容JSON正文且不提供工具，
  保留已校验决定并明确待修正单元，按原output_repair事件计数和计费。权限/归属/保存失败不走字段修正。
  与其他工具混在同一模型响应的提交在执行任何工具前失败，原模型响应保留；不会先写笔记再批准。
- 取消继续传播CancelledError，随后查询恢复cancelled终态；保存失败无最终答案。已完成提交随工作流
  幂等重读不再调用模型。历史JSON审核、Handoff及数据库结构不变，无迁移或前端改动。
- 测试先增加有效原生提交和错误单元修正两个场景，旧实现均因未提供工具失败。
  新测试共21项，验证有效提交无额外模型调用、7类正常/非法输入、混合调用不执行工具、保存失败/取消、
  主Agent拒绝、跨Task/Run/未关联候选拒绝、两批覆盖、内容修正按策略流转、文件工具返回引用及撤权拒绝。
  既有planner回归同时确认其工具列表没有submit_review。
- 多批次检查另复现消息顺序问题：上一批原生提交后的工具结果成为历史最后消息。
  已在其后追加下一批的候选ID/单元ID指令，不重复原文；相应测试先失败再通过。
  既有格式修正通过tool_history_before_input把新修正要求放在历史之后，保持此行为并增加断言，
  没有另加一份重复修正消息。
- 执行：
  `PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_review_submission.py apps/api/tests/test_task_input_coverage.py apps/api/tests/test_general_agent.py apps/api/tests/test_general_delegation.py apps/api/tests/test_task_execution_checkpoint.py apps/api/tests/test_agent_task_notes.py apps/api/tests/test_workflow_execution_tasks.py apps/api/tests/test_agent_workflows.py apps/api/tests/test_general_candidate.py apps/api/tests/test_general_review.py apps/api/tests/test_tool_catalog.py apps/api/tests/test_task_read_coverage.py apps/api/tests/test_agent_file_tools.py apps/api/tests/test_agent_output_repair.py --tb=short`
  ——最终239项通过（59.65秒）。Ruff及diff检查通过。

上述离线测试使用SQLite、内存对象存储和脚本化Provider，未产生Strata或DeepSeek调用。
下一步加载已提交代码再验证Strata是否实际使用原生提交、字段修正/内容返工、最终答案及刷新恢复；
不提前声称格式成本下降，也不将观察时点的语义误判视为已经解决。原始结束信号、受控草稿续接、
完整后端与真实隔离/恢复验收仍按既有未完成列表推进。

## 原生审核提交的首次 Strata 联调（2026-10-20）

- 本地 API 加载 `c53001e`，使用 Strata、MySQL 和 MinIO；相同附件 FULLTEXT03.pdf、
  planner 委派和输入查询要求，策略为 `on_verification_failure`。没有调用 DeepSeek、
  修改前端、推送或部署。Run `bba51c8c-e572-491c-90c1-e08f231c1b4d`，
  Workflow `debb72b8-15ac-4824-9f79-51dfff6003c4`，
  Session `1ca49197-495e-4db1-9679-91675146ba47`。
  报告 `/tmp/general-agent-native-review-strata-20261020.json`；终态后只读 `--observe`
  增补工具存储检查，没有重新提交请求。
- **最终交付失败，原生提交未验收**：436.771 秒后为 `outcome_unknown`，
  9 次模型请求中 8 次完成，末次审核请求因 `network_error` 失败，
  文案 `Model provider stream read failed.`。该请求耗时 184.566 秒，
  不是本次任务 600 秒期限到达，也没有进入字段修正；业务轮次 1、内容修正 0、格式修正 0。
- 已知输入 86,606、输出 13,846，合计 100,452 Token；缓存输入 50,132。
  最后一次输入/输出/缓存未知，9 次独立推理用量均未知，不能把汇总中的已知推理值 0 当作实际零用量；
  实际费用未知。不能据此宣称原生提交降低了审核成本。
- 末次 Attempt `4e808a19-14d1-42d8-8dfb-1e257b70b552` 的私有中断对象记录
  11,472 个事件、42,217 个推理字符（只计数）、正文 0 字符、工具片段 0、用量为空、
  `response_complete=false`。没有可续接的正文或有效审核决定，也没有执行 `submit_review`。
  现有保存信息不足以确定是供应商、代理还是网络哪一层断开；不得断言模型生成了错误 JSON。
- 已完成附件两次按需读取（offset 0/10，limit 10/20，共30个块），保留文件版本、
  页/块引用、1542个总块及未提取页限制；planner保存笔记并提交Handoff，
  主Agent查询实际输入并生成候选。没有把整份PDF自动加入提示词。
- 存储验证新增 `llm_tool_calls` 输入/输出检查：13条消息、节点、Handoff和工具记录
  SQL正文为空，MinIO对象存在且内容哈希通过；其中8条工具记录SQL输入也为空。
  32个执行观察对象和9份实际输入记录随报告保存。这些检查不代替跨用户隔离验收。
- Playwright运行中刷新后仍跟踪同一工作流，终态自动显示失败，刷新恢复相同结果；
  输入框填写未发送草稿后发送按钮可用，随后清空草稿，没有新模型调用。
  证据 `output/playwright/general-agent-native-review-{running,refreshed,terminal,restored,input-ready}.yml`。
- 验证脚本回归先确认旧报告没有工具记录而失败（预期1条、实际0条），再加入工具检查；
  另验证内存对象内容被修改时哈希检查失败。命令
  `PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_review_submission.py -k native_review_stops --tb=short`
  得到7项通过、14项未选择（2.34秒）；修改文件Ruff与`git diff --check`通过。

下一步继续检查流结束信号和失败诊断的记录、受控草稿续接，并完成真实原生审核、内容修正后的交付。
本次不因为没有最终答案而缩小整体验收范围，也不在未知原因下自动重放相同付费请求。

## 原生审核的独立成功样本（2026-10-20）

- API加载`6dd063a`，只使用本地Strata/MySQL/MinIO，附件仍为FULLTEXT03.pdf。
  本次独立问题要求读取首部少量原文、两句话说明主题和未读范围，不委派、不运行代码，
  策略`always`。它检查原生审核提交，不替代前述委派/输入核对失败样本或内容修正验收。
  Run `0eae3c8c-18ad-4c3d-86ad-3e177feaf217`，
  Workflow `c748d0f5-e665-4aa0-828e-56a9fcdb8d49`，
  Session `4d760a9e-be53-4b8d-bc23-1f27b6a5c8a0`。
  报告`/tmp/general-agent-native-review-focused-strata-20261020.json`。
- **完成本样本最终交付**：77.445秒，4次请求全部completed，输入32,708、
  输出3,063、合计35,771 Token，缓存输入3,020；独立推理Token和费用均未知。
  业务轮次1，内容修正0、格式修正0，不算修正后最终交付通过。
- 主Agent读取offset=0/limit=20；Reviewer独立重读相同20块，确认标题和读取限制后
  调用`submit_review`，为u1/u2分别提交supported及S2来源。
  两次同范围读取分别属于主Agent与Reviewer，并非单一节点无进展重读。
  提交工具保存成功后没有额外模型调用重写JSON；最终答案保存了论文主题、S1引用、
  未读范围和未启用OCR等限制。
- 9条消息/节点/工具正文记录SQL为空、MinIO对象存在且哈希通过，其中3条工具记录输入也为空。
  14个执行观察对象、4份实际输入清单及1份候选输入快照随报告核对。
  没有借此宣称跨用户隔离或跨进程恢复已经验收。
- Playwright观察到运行中的审核状态；刷新后的首次快照已显示完成，不能据此证明刷新完成瞬间
  工作流仍在运行。再次刷新后完整回答与“原文支持”决定恢复一致。
  记录`output/playwright/general-agent-native-focused-{running,refreshed,completed,restored}.yml`。
- 另外只读核查上次失败Attempt `4e808a19-14d1-42d8-8dfb-1e257b70b552`：
  HTTP 200，184,566ms，已保存错误类型为RemoteProtocolError。它支持“已连接后读流失败”，
  不足以确定Strata或代理哪个环节断开；没有为该核查重新发送模型请求。
- 本次没有代码/前端修改、DeepSeek调用、推送或部署。两个九月STARTED历史记录保持不变。
  后续仍需完成真实内容修正后的交付、委派输入核对、受控续接与完整存储/权限验收。

## 尚未验收的完整目标

### 来源批准规则的Strata复验（2026-10-23）

- Xcode许可阻塞解除后确认Git工作区干净、HEAD为06b4167。本地MySQL迁移为
  20261018_0024=head，未执行迁移；启动现有unified-local解析镜像的执行器，Socket为
  `/tmp/nexuspilot-general-agent-20261023/executor.sock`，重新启动API与前端。
  两条九月STARTED历史记录未修改。没有部署服务器或修改前端。
- 使用相同完整问题、FULLTEXT03.pdf、planner委派、条件审核策略，只发起一次Strata工作流。
  Run `d2f675a4-0ce6-48ae-a7cd-41b414dfebf6`，Workflow
  `769a6fa4-526e-4782-a564-15c2d4495a79`，Session
  `72c6d18e-25eb-44ec-aea9-df80f1bc35f8`；报告
  `/tmp/general-agent-citation-scope-strata-20261023.json`。
- 运行completed，206.542秒，6次请求。输入53,058、输出10,946、合计64,004 Token，
  缓存输入16,273；独立推理用量和实际费用未知。业务轮次1、内容修正0、格式修正0。
  本次仍未触发真实内容返工，不能计为修正后交付通过。
- 主Agent只读取前15块，planner使用传入原文完成Handoff；目录搜索/加载及实际输入查询完成。
  Reviewer通过submit_review显式为两个引用单元提交S1，过渡语和平台输入事实使用空来源，
  这次未发生漏填/替换引用。审核阶段的在途私有记录实际包含protocol_evidence，
  收到终态之前finish_reason=null、done_marker_received=false，没有把这些观察当作完成。
- 内容限制仍存在：回答将前15行的标题片段作为论文标题，但未补读第16行来核对完整标题。
  因此本次证明执行和批准范围规则可用，不证明完整标题/主题核对充分，也不排除模型误读。
  同一源PDF先前读取到的完整标题包含“using Large language models”，此次片段未涵盖该部分。
  后续应区分片段引用与完整字段提取，不靠后端自动补写标题冒充模型已经读取。
- 14条消息/节点/Handoff/工具正文SQL为空、MinIO对象和哈希通过；24个执行观察对象、
  6份实际输入清单、2份候选输入快照随报告核对。Playwright运行中刷新继续跟踪，
  完成及再次刷新后答案/审核记录恢复一致，证据
  `output/playwright/general-agent-citation-scope-20261023-{running,refreshed,completed,restored}.yml`。
- 本批只新增真实验证记录，未调用DeepSeek、未改业务代码、未推送或部署。
  不继续重复相同付费问题来等待随机返工；真实内容修正验收仍需要明确可复核的测试条件。

### 来源传递核查与批准范围检查（2026-10-22）

- 当前分支保留用户后续前端提交，开始时HEAD为8d6f8c9、工作区干净；本批不改前端。
  没有模型调用，只读查询2026-10-20完整委派样本的MySQL和MinIO。
- planner最终请求b546a794-8dc5-4a6e-99dc-c2b69227d1dd的original_sources.S1
  明确只有page:1:line:1–20；已读取工具消息中的source_alias为S2。
  Reviewer首个请求cbdf7ea8-2d30-411b-a393-062386257eac的original_sources.S1也仅20行。
  这排除了这两份请求中“后端把S1覆盖为第3页”的假设，不证明所有中间处理和模型判断都正确。
- 原生及兼容审核此前允许引用文件的单元返回supported但source_aliases为空。
  本次新增服务端候选来源映射：上述单元必须显式确认候选所引的相同集合；
  改用其他来源应返回revise，交由原Agent改正文。后端不自动替换引用、不编造结论。
  该检查只证明提交与候选引用一致，不能证明原文支持结论。
- 新增六项规则测试先因缺少候选来源检查接口失败，再实现检查；
  文件原生提交回归验证漏填来源被保存为failed/invalid_review_submission，再有限修正并保留计数。
  原有三轮测试曾批准“S1正文/S3审核”，现改为第三轮正文引用S3后再批准，未降低内容要求。
  伪造来源场景在有限字段修正后更早失败，更新实际请求次数，仍断言无最终答案。
- 初次扩大回归发现缺失tiktoken编码缓存，下载公开固定cl100k_base编码文件后重跑；
  未发送用户材料或调用模型。审核/原生提交/委派/通用Agent测试72项通过（19.09秒），
  候选/任务笔记/格式修正/输入覆盖/执行任务测试71项通过（19.06秒）。
  原生保存状态新增断言复验4项通过（1.51秒）。Ruff指出一处测试导入分组问题，
  修正后Ruff与diff检查通过。
- 本地API未重启，本次新增批准范围规则的真实Strata效果未验证；
  真实内容修正交付、语义审核准确性、受控续接及完整验收继续保留为未完成。

### 完整委派复验：完成运行但来源核对未通过（2026-10-20）

- 本地API仍为`6dd063a`，相同PDF、原完整问题、planner委派及条件审核策略；
  本次运行中修改的笔记错误反馈没有加载，不能算它的真实测试。
  Run `6d36a713-7385-4a47-843f-2f7415796ca0`，
  Workflow `c3515500-9ffc-4a45-9310-a799bb950c2e`，
  Session `07b7c661-ccd1-4514-b1cb-92179c80ad48`，
  报告`/tmp/general-agent-native-full-strata-20261020.json`。
- 运行completed，459.528秒，11次模型请求；输入151,616、输出23,082，
  合计174,698 Token，缓存输入47,715；独立推理用量和费用未知。
  完成一次委派/Handoff、实际输入查询、Reviewer三次搜索和一次补读、submit_review、
  最终回答保存。业务轮次1、内容修正0、格式修正0。
- **来源核对不通过，不能以completed宣称答案验收成功**：候选/最终回答用S1支持包含GPT-4、
  Gemini Pro和RCMP的论文研究对象。候选保存的S1对应第1页1–20行，
  S2才对应第1页21–22行和第3页1–18行；Reviewer却声称这些内容已由“S1第1页标题与第3页摘要”
  核实，提交supported且未填source_aliases。需要继续查Handoff、主Agent与Reviewer的输入、
  来源编号及审核要求；本次不能算内容修正后交付通过，也不能直接把错误归因于模型能力。
- planner两次save_task_note都因source_aliases[0]的string_pattern_mismatch失败。
  原工具只返回invalid_arguments，没有字段说明。Handoff仍实际保存，但不能据此声称笔记成功。
- 21条消息/节点/Handoff/工具正文SQL为空、MinIO对象及内容哈希通过；
  48个执行观察对象、11份实际输入清单、2份候选输入快照已核对。
  Playwright运行中刷新继续跟踪；完成回答与审核结果在刷新后恢复。
  证据`output/playwright/general-agent-native-full-{running,refreshed,completed,restored}.yml`。
  恢复通过不代表恢复的结论正确。
- 本次只用Strata，不用DeepSeek，不改前端，不推送或部署；终态后仅执行只读observe。

### 工具参数错误的可修正反馈（2026-10-20）

针对上述笔记失败，输入Schema校验现在返回最多8条已声明顶层字段、可选数组索引和固定错误类别。
不回显输入值、未知额外键名、异常消息或上下文；权限检查先于校验，内部分派异常不套用输入字段诊断。
错误状态仍是failed/invalid_arguments，不绕过来源格式和归属校验，不把失败调用改成成功。
该结果通过原Tool Call对象保存，沿用SQL正文为空、MinIO存正文方式，无迁移。

测试先确认旧实现没有字段反馈而失败；实现后检查敏感值/额外键不回显，
并用脚本化Provider验证失败后另发正确参数、保存成功、读回笔记，保留两次调用的不同状态。
后者初次测试夹具漏填ModelResponse必需字段，补齐夹具后通过，并非产品修复。
命令：
`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_agent_task_notes.py apps/api/tests/test_agent_file_tools.py apps/api/tests/test_review_submission.py apps/api/tests/test_tool_catalog.py --tb=short`
——68项通过（15.86秒），Ruff及diff检查通过。真实模型是否据此修正仍待验证。

### 协议观察报告兼容检查（2026-10-20）

验证脚本现在把私有进度对象中的protocol_evidence写入本地报告，不复制正文或私有推理。
旧对象缺少该字段时报告null，不能补成“结束标识未收到”。
保存层两种消费路径先因报告缺少字段失败，实现后
`PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_stream_progress_persistence.py --tb=short`
得到4项通过（0.85秒）；修改文件Ruff及diff检查通过。
该报告改动不影响正在执行的模型请求，也不会自动重试。

### Chat SSE结束信号的离线补充（2026-10-20）

新增私有协议观察，独立保存finish_reason与DONE是否收到，不改变当前成功/失败规则。
先运行新增三项缺结束信号用例；首次命令未启用异步测试模式，属于测试配置错误，
随后使用`-o asyncio_mode=auto`，三项均因缺少protocol_evidence失败，复现目标缺口。
实现后增加流读取异常、两种消费方式、请求复用及旧快照不变化测试。
模型包和相关API测试192项通过（12.17秒）；随后增加在途及取消对象保存断言，
保存层4项再次通过（0.88秒）。静态检查发现一处测试断言过长，拆行后重新检查。
没有新模型请求，没有重启API或部署；当前运行的API尚未加载这批改动。
该提交只验证Chat Completions SSE；随后按下节补充其他现有协议，受控草稿续接仍未完成。

### 其他现有SSE协议的结束观察（2026-10-20）

- Responses的completed/incomplete事件、Messages的stop_reason/message_stop、
  GenerateContent的finishReason已接入同一私有观察对象，不新增公开SSE事件或数据库字段。
  未知结束原因记unknown，不复制任意上游文本；不会因为收到结束事件就把随后读流失败转为成功。
- 测试先复现五种事件在断流后留下空协议观察；实现后再加入两种未知原因的脱敏检查。
  所有网络由httpx.MockTransport模拟，不调用任何供应商。
- 命令：
  `PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q -o asyncio_mode=auto packages/models/tests apps/api/tests/test_stream_progress_persistence.py apps/api/tests/test_responses.py --tb=short`
  ——199项通过（11.45秒）；修改文件Ruff与diff检查通过。
- 本地API未重启，新增协议观察尚未真实验收；未推送、未部署、未改前端。
  完整终态后的恢复判断、受控草稿续接和真实原生审核最终交付继续保留为未完成工作。

2026-10-20 分批保存的离线验证：新增 SQLite/内存对象存储测试先复现上游停止发片段后没有在途对象引用；已接入2秒检查、5秒写入期限及独立会话保存。新增流式 ASGI 取消测试发现重复取消会打断终态保存，已仅对写入任务收尾与终态保存增加取消保护，不保护模型执行。等待时保存、两种消费模式取消、写入失败中止及迟到进度不覆盖终态均通过；模型包与 Responses/通用Agent/委派/执行任务合计237项通过（25.70秒），Ruff及diff检查通过。这些离线检查没有模型调用；随后真实Strata验收结果见上节，不能以离线通过替代最终回答成功。

继续完成内容修正后的真实交付及新后端完整实现；浏览器发起/在途停止已有上述样本，不能扩大成整项目完成。
逐单元审核与来源查询已有代码、确定性测试和真实条件审核样本；任务笔记已有确定性测试及历史 DeepSeek 保存/读回成功样本；本轮Strata有save_task_note成功记录，但尚未验证后续读回及依此最终交付；
按需委派及 Handoff 已有实现、确定性测试及上述 Strata 单轮审核真实最终交付；
固定输入快照的真实保存/请求哈希已验证，Reviewer实际读取与核对仍未通过；还需补齐原始结束信号与受控草稿续接、多轮审核内容修正真实交付、
真实存储/权限与恢复测试。用户已明确 Python/Shell 接入及跨进程续跑在 Agent 真实调测完成后再检查；
本批不提前实现，不影响继续补齐当前通用循环后端范围。
