# 新通用 Agent：Strata / MySQL / MinIO 本地验收

## 范围和执行约束

实现、测试与规划先提交为 376a7d4（codex/general-agent-tool-loop）。
本次用户授权真实 Strata 调用及阶段验证后提交；不使用 DeepSeek、不推送、不部署。
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

## 尚未验收的完整目标

继续完成内容修正后的真实交付、浏览器发起/在途停止及新后端完整实现，不能把上述样本扩大成整项目完成。
逐单元审核与来源查询已有代码、确定性测试和真实条件审核样本；任务笔记已有代码和确定性测试，真实调用因502尚未验证；
仍需补齐执行检查点/完整模型输入覆盖、按需委派及 Handoff、
真实存储/权限与恢复测试。用户已明确 Python/Shell 接入及跨进程续跑在 Agent 真实调测完成后再检查；
本批不提前实现，不影响继续补齐当前通用循环后端范围。
