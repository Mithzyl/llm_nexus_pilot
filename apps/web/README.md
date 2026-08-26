# NexusPilot Web

阶段4第一版对话式前端，采用三栏黑白界面：会话侧栏、中央对话工作区和运行证据检查器。浏览器只访问同源 `/api/nexus/*`，由 Next.js 服务端转发层携带 FastAPI 的 API Key。

## 本地运行

在项目根目录先启动 FastAPI，并从示例创建前端专用配置：

```bash
cp apps/web/.env.example apps/web/.env.local
cd apps/web
npm install
cd ../..
make web-run
```

`apps/web/.env.local` 只保存 Next.js 服务端转发所需的 `NEXUSPILOT_API_URL` 和
`NEXUSPILOT_API_KEY`。API Key 必须与 `apps/api/.env` 一致；Provider、数据库和对象存储
密钥只属于后端，不得放入前端配置。Next.js 读取环境变量发生在启动时，修改后需要重启。
开发服务器使用 `.next-dev`，生产构建使用 `.next`，两者可以先后验证而不会覆盖正在运行的
客户端产物。

打开 `http://localhost:3000`。如果 FastAPI 未运行，界面仍会显示完整空状态和布局，但会在侧栏明确标记 API 未连接，不能发送模型请求。

## 设计边界

- Provider 和模型选项来自 `GET /api/v1/providers` 的 `providers` 与 `models_by_provider`，不在浏览器硬编码可用状态。Composer 使用自定义可搜索选择器；某个 Provider 的模型列表为空时，按服务端合同允许输入自定义模型标识。
- `/api/nexus/*` 不是通用 API 代理，只允许当前页面所需的 Provider、User、Session、Message、Run、Response、Attempt Reasoning/事件和八个 Agent Workflow 路径；Session、Message、Run、Attempt、Workflow 和 Node 会沿所属 Run 在服务端按固定开发用户校验归属。正式多用户部署前必须替换为登录身份和资源授权。
- 发送消息前创建或复用用户、Session 和 Run；响应通过 `POST /api/v1/responses` 的 SSE 事件增量渲染。
- Provider Catalog 同时返回逐模型推理能力。页面只渲染服务端统一的 `reasoning.raw`、`reasoning.summary` 或 `reasoning.status`，不会根据 Provider 名称推断内容；公开推理默认折叠，隐藏推理在 Chat 完成后移除空状态，在运行轨迹中保留状态与指标。
- Responses 事件使用 `conversation-stream.v2` 和单调 `sequence`。已取得 Attempt 标识后发生断线或序号缺口时，页面通过受归属检查的 `/attempts/{attempt_id}/events` 从最后确认序号回放；缺失事件不会被跳过，用户主动停止不会触发自动恢复。
- Assistant Message 保存 `source_model_attempt_id`，历史窗口一次批量返回对应公开 Reasoning Block；加密推理、DeepSeek 工具续接原文和 Provider response ID 始终留在后端。
- 流式响应和历史 Assistant Message 使用同一安全 Markdown 渲染入口，支持 GitHub Flavored Markdown 的标题、强调、列表、引用、表格与代码；不执行原始 HTML，不允许危险协议链接，也不由消息内容自动加载远程图片。
- 浏览器不保存 `X-API-Key`、供应商凭据、内部对象 URI 或原始供应商响应。
- 会话使用 `/c/[sessionId]`，运行证据使用 `/runs/[runId]`；刷新、前进和后退会从消息窗口与对应 RunDetail/Attempt 事实恢复，运行地址不会被会话的最新 Run 替换。完整消息分页由服务端单次批量返回正文，用户可通过游标按钮继续加载更早页；SSE 中途断开不会被标记为已完成。
- 模型响应完成和 Assistant Message 保存是两个独立状态；只有写入成功才显示“响应已保存”，保存失败时保留文本并明确提示。
- Composer 可以显式切换“快速回复”和 `model_only_v1` Agent Workflow。Agent 模式使用当前 Provider/Model 绑定 Controller、Planner 和按策略启用的 Reviewer，并允许选择串行或最多两个无依赖工作 Agent 并行；DeepSeek 使用 prompted JSON，其他当前 Provider 使用原生 Schema。
- Agent Workflow 通过 POST SSE 接收实时持久化事件，按 `event_sequence` 去重和检测缺口；刷新或断线后使用 Run 发现、有限事件回放和 Result 快照恢复，不把 GET `/events` 描述为持续订阅。运行中可以通过独立 `/cancel` 动作持久取消后续节点，关闭 SSE 不会被误当成服务器端取消。
- 运行检查器展示 Workflow 摘要、模型调用/节点上限、11 类完整节点结果、Handoff、确定性验证、Reviewer、执行组、已消费费用和当前费用预留。阶段3后端负责保存最终 Assistant Message，前端不会重复写入。
- 桌面端默认打开运行详情，窄屏默认收起但保留顶部“运行详情”入口。
- 桌面端输入区保持为对话主区约 80% 宽度，Provider/Model 选择位于输入框右下角；三栏共享同一动态视口高度，避免侧栏、对话区和运行详情底部错位。
- 恢复、等待用户输入、工具、Memory、Knowledge 和大型节点 Artifact 降级仍未开放；界面不生成相应按钮或虚假执行状态。
