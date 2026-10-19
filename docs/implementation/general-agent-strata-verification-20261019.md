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

## 尚未验收的完整目标

继续完成触发式审核、内容修正后的交付、浏览器发起/在途停止及新后端完整实现，不能把上述样本扩大成整项目完成。
后续仍需补齐规划中的逐结论审核、工作记录/读取范围、按需委派及 Handoff、
真实存储/权限与恢复测试。用户已明确 Python/Shell 接入及跨进程续跑在 Agent 真实调测完成后再检查；
本批不提前实现，不影响继续补齐当前通用循环后端范围。
