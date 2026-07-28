# 第一阶段：基础服务实施说明

## 实施边界

当前完成原规划“阶段 1：基础服务”，提供可持久化的运行、任务、模型调用和产物接口。当前不会请求真实模型，不会发布 RabbitMQ 消息，也不会启动工作模型；相关表只是为后续阶段保留稳定的业务事实结构。

## 数据与运行行为

- MySQL 是运行、任务、调用、费用、错误和产物元数据的权威来源。
- MinIO 保存文件内容；MySQL 只保存 SHA-256、大小、类型和 `minio://` 位置。
- `POST /api/v1/users` 创建基础用户身份；运行只能归属于已存在且启用的用户。
- `POST /api/v1/runs` 创建状态为 `pending` 的用户请求。
- `POST /api/v1/runs/{run_id}/tasks` 校验父任务及依赖必须属于同一运行；存在依赖时初始状态为 `waiting_for_dependency`。
- `POST /api/v1/runs/{run_id}/attempts` 保存模型调用记录，并在同一事务中累加运行费用。
- `GET /api/v1/runs/{run_id}` 返回当前任务、模型调用和产物历史。
- `POST /api/v1/runs/{run_id}/artifacts` 限制上传大小，清理路径型文件名，将内容写入 MinIO 后保存元数据。

## 认证

业务接口使用 `X-API-Key`。服务通过常量时间比较验证 `NEXUSPILOT_API_KEY`；`/health` 不要求认证。该方案用于第一阶段服务边界，不替代后续用户登录、密钥轮换和细粒度授权。

## 本地依赖

后端虚拟环境位于 `apps/api/.venv`。MySQL 和 MinIO 定义在 `deployments/compose/docker-compose.yml`。当前机器没有 Docker 时，仍可在虚拟环境中运行基于临时 SQLite 和内存对象存储替身的 API 测试，但不能完成真实 MySQL/MinIO 集成验证。

## 下一阶段入口

多供应商模型接口应在新增的独立 `packages/models` 职责中实现，并通过当前 attempts 和 artifacts 服务保存统一调用记录及原始响应；不要让业务路由直接依赖厂商 SDK。

阶段 2 已实施，接口契约、Provider 分层、步骤和验收结果记录在 [`phase-2-multi-provider-model-plan.md`](./phase-2-multi-provider-model-plan.md)。
