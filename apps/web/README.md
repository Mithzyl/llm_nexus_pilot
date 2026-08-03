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

- Provider 列表来自 `GET /api/v1/providers`，不在浏览器硬编码可用状态。
- 发送消息前创建或复用用户、Session 和 Run；响应通过 `POST /api/v1/responses` 的 SSE 事件增量渲染。
- 浏览器不保存 `X-API-Key`、供应商凭据、内部对象 URI 或原始供应商响应。
- 页面刷新从 Session、Message 和 Run/Attempt 事实恢复；SSE 中途断开不会被标记为已完成。
- Agent、工具和 Memory 仍是预留区域，不生成虚假执行状态。
