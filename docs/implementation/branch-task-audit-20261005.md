# 2026-10-05 分支与未完成任务核对

## 范围与结论

按用户要求提交现有改动、核对分支和计划、清理本地旧分支。检查基础为 `main` 的 `5bb81dd`，包含部署提交 `ba41e74`。本次不实施新增功能、不推送、不删除远端分支、不连接或重新部署服务器。

现有改动已提交为 `5bb81dd`：保存前端当前文件状态以及三份跨用户审计 JSON、一份本地 Docker 预验证 JSON。报告是历史检查记录，不是本次重新运行安全测试的结果。

分支核对通过：13 个旧本地分支是 `main` 的祖先；迁移修复分支的唯一提交 `b7a4a22` 与已合入的 `dc074f7` 补丁等价。未发现只留在旧分支中的独立功能实现。几个名字含 RabbitMQ、工具运行时的分支共同指向 `0e2cd0b`，分支名称不能证明这些功能已实现。

清理状态：已完成。删除下表 14 个本地旧分支引用，仅保留本地 `main`；远端引用和工作区文件未删除。三个分支因其远端落后，普通删除被 Git 拒绝；再次确认它们是 `main` 的祖先后才删除本地引用，不丢弃独立提交。

## 未完成任务与阶段归属

阶段1～3原验收结论保留；后续扩展和部署缺陷单独记录。以下是核对结果摘要，详细设计仍以链接中的现有阶段文档为准。

| 归属 | 已有事实 | 剩余工作与状态 |
|---|---|---|
| [阶段2：文件输入](phase-2-llm-core-capabilities.md) | 正式上传、MinIO 原件和固定解析结果、下载/删除、两种模式文字输入、同会话后续轮次快照已有实现 | 扩展进行中：逻辑版本历史、跨文件/版本解析缓存、大文件分段选择、OCR/扫描 PDF、PDF 结构化表格与完整章节目录未实现。固定上传结果复用不等于跨文件缓存 |
| [阶段2/7：解析运行环境](phase-7-tool-runtime-plan.md) | 本机文本、PDF 和 Office 提取有历史验证，Office 专用受控执行器已有代码 | 目标部署未完成：PDF 的 namespace 创建失败；Office 执行器未部署。先修复目标隔离环境并验证，不通过关闭隔离来宣称解析可用 |
| [阶段3：附件编排](phase-3-agent-workflow-plan.md) | `model_only_v1`、完整节点结果、Handoff、审核与最终汇总、活动摘要已有实现；附件文字可进入节点 | 附件专用流程未开始：自动模式选择、文件准备/可选分析角色、模型按需读取循环、Handoff 覆盖与引用；Reviewer 新工作轮次仍为 0，未来有界返工后置 |
| [阶段4：产品体验](../frontend/phase-4-web-ui-plan.md) | 正式上传/文件列表、独立证据抽屉、代码块工具栏/精确复制/横向滚动已实现 | 进行中：跨刷新及再次点击的提交 key、上传重试 key、项目导航与文件页面、分段引用、完整 Evaluation 视图、代码语法高亮未完成。前端不能单独完成后台执行恢复 |
| [阶段5：身份与授权](phase-5-platform-identity-credentials.md) | 浏览器身份、密码修改/会话撤销后端接口、FastAPI 主体检查、JSON 绕过修复、本地双向隔离已有；服务器基础冒烟通过 | 进行中：个人 Bearer API Key/scope、找回密码、近期重新认证、账户安全页面、项目文件授权与完整目标资源矩阵仍缺；严格并发登录限流/多实例行为仍需验证和设计 |
| [阶段3缓存 / 阶段6遥测](phase-3-agent-workflow-plan.md) | 稳定前缀序列化、缓存用量归一化及 Run 范围内部诊断已实现 | 真实缓存收益对照暂停；显式缓存策略需先证明收益。阶段6 trace、metrics、导出与安全诊断未开始，不能说缓存机制全部未做 |
| [阶段7：模型工具](phase-7-tool-runtime-plan.md) | Provider 工具调用结构和 Tool Call 只读查询已有，Office 程序解析不授予模型工具权限 | 通用工具注册/授权/执行审计、五类模型文件读取工具、本地 Workspace 尚未实现；Shell/Git 暂停 |
| 阶段8 / 阶段9 | 设计归属已确定 | 代码索引/语义定位、MCP Client 尚未开始，依赖阶段7权限与工具能力 |
| [阶段10：后台执行](phase-10-rabbitmq-task-execution-plan.md) | 运行、节点、事件和 Outbox 记录已有 | 未开始：Publisher、RabbitMQ 消费 Worker、执行规格、领取租约、服务端防重、断线继续及重启恢复。当前 POST SSE 的执行任务仍随流结束取消，保存事件不代表独立后台执行 |
| [部署运维](initial-release-checklist.md) | 自签 HTTPS、预置账号、文本文件对话、Flash SSE、API/Web 重建保留数据通过基础验收 | 未完成：备份/恢复演练、证书续期安排、独立无代理网络端口复核、完整身份/失败矩阵、镜像供应链及完整漏洞扫描。Pro 与完整 Agent 目标服务器生成未验证 |

另有两个既有上下文工作项仍需补充：仅有 `content_uri` 的对象型 Message 读取与产品恢复（当前 Context 标记 `unsupported_object_content`）；快速回复的后端默认 System Prompt 场景选择策略（不恢复普通前端 Prompt 入口）。正式 UserFile/Artifact 上传不是对象型 Message 读取的替代证明。

保持暂停或后置：会话归档、Memory Packet/后台形成/通用孤儿对象清理、Knowledge、用户 Provider 凭据、真实 Provider 浏览器矩阵。上传专用的失败补偿和清理已有实现，不与暂停的 Memory 工程混淆。完整清单不是本次实施授权。

优先顺序建议：先处理已部署 PDF/Office 可用性和运维验证；然后实现阶段10执行所有权与观察连接分离，并配套阶段4跨刷新提交恢复；项目文件和模型工具按既有依赖另行实施。工作流质量优化继续允许上线后安排。

## 代码和验证依据

- `apps/api/src/nexuspilot_api/routers/__init__.py` 的公共 Router 同时挂载服务 Key 与 `authorize_resource_request`；`features/identity/resource_access.py` 分类路由、派生主体、校验归属及 JSON 正文。旧文档“FastAPI 尚未做资源主体授权”已更正。
- `apps/api/src/nexuspilot_api/models/user_file.py` 保存不可变上传及 `parsed_uri`、哈希、大小；`services/user_file_service.py` 和 `services/user_file_cleanup.py` 负责上传与失败清理。旧文档“文件只在浏览器本地选择”已更正。
- `apps/api/src/nexuspilot_api/features/agent_runtime/routers/agent_workflows.py` 的 `live_event_source` 创建执行任务，`finally` 取消未结束任务；不是独立 Worker。
- `apps/web/lib/api.ts` 的轮次请求可携带 key；完整跨刷新策略仍未实现。`components/markdown/MarkdownContent.tsx` 使用现有代码块组件，未增加语法高亮器。
- `git fetch origin`、全部本地分支祖先检查、`git cherry main codex/fix-runtime-context-migration-phase4`、工作区及 worktree 检查通过。
- 远端仅有两个非祖先引用：上述补丁等价修复分支，以及 `origin/codex/phase5-agent-workflow-plan` 的 `472a0a3` 合并提交。后者两个父提交都在 `main`，与第二父提交的树差异为空；没有额外实现需要抢救。远端引用全部保留。
- 本次亲自运行 `cd apps/web && npm run lint`、`npm run typecheck`：通过。提交差异与秘密检查通过；7 份文档的 75 个本地 Markdown 链接文件目标均存在（不包括标题锚点校验），`git diff --check` 通过。
- 后端测试、完整前端测试、Playwright 和目标服务器检查本次未执行：本次没有业务实现改动；既有运行结果以[部署记录](server-deployment-20261004.md)及各阶段的带日期记录为准。

## 本地分支恢复位置

删除分支名不删除 `main` 已包含的提交。需要恢复时使用 `git branch <原分支名> <下表提交>`。等价修复的原提交也仍由远端跟踪引用保留。本次不运行 prune 或 GC，不删除任何工作目录。

| 本地分支 | 清理前提交 |
|---|---|
| `codex/attempt-retry-artifact-queries` | `0e2cd0bea2e53919fcd5bf57320e48e8f4e24c12` |
| `codex/deployment` | `ba41e7476dc915bb1c11c1d3b1517f8ab56cae8a` |
| `codex/fix-provider-model-list` | `102b95d82cbefdb002dd769872830c716525f703` |
| `codex/fix-runtime-context-migration` | `dc074f71de4472a1cf1ebb6ba73e3273b11453b1` |
| `codex/fix-runtime-context-migration-phase4` | `b7a4a2226de67e5f21728552e2a6c79e9a5f5395` |
| `codex/phase3-rabbitmq-worker` | `0e2cd0bea2e53919fcd5bf57320e48e8f4e24c12` |
| `codex/phase4-context-completion` | `546d527725968495199b929ea3c815da5e02fbe6` |
| `codex/phase4-reasoning-stream` | `9d81bcc1a1e8ae1d1bc1b76fe07eabc06d11426b` |
| `codex/phase4-tool-runtime-plan` | `0e2cd0bea2e53919fcd5bf57320e48e8f4e24c12` |
| `codex/phase5-agent-workflow-plan` | `df79f0808760d05e5798f28c4941e268ea240e3c` |
| `codex/phase5-identity-auth` | `83d335e9eb49b15b1fbc8747e16289563f1c1536` |
| `codex/phase9-phase5-browser-adaptation` | `bab0878cc3906891ae437db478d0e07a59de38d2` |
| `codex/web-ui-layout-performance` | `0e2cd0bea2e53919fcd5bf57320e48e8f4e24c12` |
| `frontend` | `6b927cb53b75f93104b7b5b59454bc5fde0ce970` |
