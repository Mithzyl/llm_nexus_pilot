# NexusPilot Web

阶段 9 第一版对话式前端，采用三栏黑白界面：会话侧栏、中央对话工作区和运行证据检查器。浏览器只访问同源 `/api/nexus/*`，由 Next.js 服务端转发层携带 FastAPI 的 API Key。

## 本地运行

在项目根目录先启动 FastAPI，然后在本目录安装依赖并运行：

```bash
npm install
NEXUSPILOT_API_URL=http://127.0.0.1:8000 \
NEXUSPILOT_API_KEY=local-development-key-change-me \
npm run dev
```

打开 `http://localhost:3000`。如果 FastAPI 未运行，界面仍会显示完整空状态和布局，但会在侧栏明确标记 API 未连接，不能发送模型请求。

## 设计边界

- Provider 和模型选项来自 `GET /api/v1/providers` 的 `providers` 与 `models_by_provider`，不在浏览器硬编码可用状态。Composer 使用自定义可搜索选择器；某个 Provider 的模型列表为空时，按服务端合同允许输入自定义模型标识。
- `/api/nexus/*` 不是通用 API 代理，只允许当前页面所需的 Provider、User、Session、Message、Run、Response 和七个 Agent Workflow 路径；Session、Message、Run、Workflow 和 Node 会沿所属 Run 在服务端按固定开发用户校验归属。正式多用户部署前必须替换为登录身份和资源授权。
- 发送消息前创建或复用用户、Session 和 Run；响应通过 `POST /api/v1/responses` 的 SSE 事件增量渲染。
- 浏览器不保存 `X-API-Key`、供应商凭据、内部对象 URI 或原始供应商响应。
- 页面刷新从最新消息窗口、最新 RunDetail 和 Attempt 事实恢复；完整消息分页由服务端单次批量返回正文，用户可通过游标按钮继续加载更早页；SSE 中途断开不会被标记为已完成。
- 模型响应完成和 Assistant Message 保存是两个独立状态；只有写入成功才显示“响应已保存”，保存失败时保留文本并明确提示。
- Composer 可以显式切换“快速回复”和 `model_only_v1` Agent Workflow。Agent 模式使用当前 Provider/Model 绑定 Controller、Planner 和按策略启用的 Reviewer；DeepSeek 使用 prompted JSON，其他当前 Provider 使用原生 Schema。
- Agent Workflow 通过 POST SSE 接收实时持久化事件，按 `event_sequence` 去重和检测缺口；刷新或断线后使用 Run 发现、有限事件回放和 Result 快照恢复，不把 GET `/events` 描述为持续订阅。
- 运行检查器展示 Workflow 摘要、模型调用/节点上限、11 类完整节点结果、Handoff、确定性验证和 Reviewer 证据。阶段 5 后端负责保存最终 Assistant Message，前端不会重复写入。
- 桌面端默认打开运行详情，窄屏默认收起但保留顶部“运行详情”入口。
- 桌面端输入区保持为对话主区约 80% 宽度，Provider/Model 选择位于输入框右下角；三栏共享同一动态视口高度，避免侧栏、对话区和运行详情底部错位。
- 外部取消/恢复、等待用户输入、并行 Agent、工具、Memory、Knowledge、费用预留和大型节点 Artifact 降级仍未开放；界面不生成相应按钮或虚假执行状态。
