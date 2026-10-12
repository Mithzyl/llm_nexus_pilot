# 2026-10-12 文件工具与解析执行器部署记录

## 当前状态

已将分支 `codex/file-runtime-deployment-plan` 的提交 `2f069628d739b817acbc9574583d35fdedf69194` 推送至 origin，并部署到 `https://43.134.11.254:8443`。未合并或改写 main。API、Web、Nginx、MySQL、MinIO 健康；Office/PDF 专用解析执行器已启用。快速回复首轮 DOCX、续聊 PDF 的 HTTPS 真实模型检查通过。**Agent 两次检查均发生 `node_output_invalid`，服务器 Agent 验收未通过**，没有将本地成功结果替代服务器结果。部署操作已完成，新版本保持运行；该业务限制向用户明确报告，不声称全部功能已验收。

本记录更新 10 月 4 日首次部署之后的实际状态；[首次部署记录](server-deployment-20261004.md)保留历史。既有账号、聊天、文件、Provider Key 和证书保留，没有复制本地开发数据库或用户文件。

## 发布对象与备份

| 对象 | 实际值 |
|---|---|
| 新发布目录 | `/opt/nexuspilot/releases/20261012-2f06962` |
| 当前目录 | `/opt/nexuspilot/current` 指向新发布目录 |
| 保留的旧发布 | `/opt/nexuspilot/releases/20261004-selfsigned`，旧 API/Web/MySQL 镜像未删除 |
| 数据库迁移 | `20260916_0022` → `20261008_0023`；给工具调用增加四个可空字段和唯一约束，不改写历史模型登记与用户记录 |
| 更新前备份 | `/opt/nexuspilot/backups/20261012-2f06962`，0700；含配置、TLS、`database.sql`、`minio-data.tgz` |
| 解析服务 | `nexuspilot-file-runtime.service`，已启用开机启动，专用用户 `nexus-file-runtime` |
| Socket | `/run/nexuspilot-file-runtime/executor.sock`，0660；父目录 0750，专用组 GID 989 |
| 私有配置 | `/etc/nexuspilot/config/.env` 更新 RELEASE_TAG、FILE_RUNTIME_GID、COMPOSE_FILE；其他秘密不变 |

源码使用 `git archive` 从已提交 HEAD 生成，不包含 `.env`、本地数据、虚拟环境或未提交文件。归档 SHA-256：`4f65f3b4893b044a6477ae95632087c0c243c245e44441ec3dbf76743610bd1d`。

备份前确认无运行中模型请求或工作流，停止 Nginx/Web/API 写入；MySQL 做一致性逻辑导出，MinIO 停止后复制整个对象卷。数据库及对象备份均非空，对象归档压缩完整性通过。此为同机更新前备份，**没有完成异机备份或恢复演练**。

- 数据库备份 SHA-256：`da907cde31d10add0b83ac965cf5c22646c663ee09c06983de84aba1cceef791`。
- 对象备份 SHA-256：`79a57103ba61f4cd10bbdb9832c1035af8d91844b92b9c6ac1ca8164c2ee1ab4`。

| 镜像 | SHA-256 |
|---|---|
| `nexuspilot-api:20261012-2f06962` | `52008388a3a67316aab39896f30d1113e80a591ad37c981d8361c3a8e8a1fa16` |
| `nexuspilot-web:20261012-2f06962` | `865eb7524c91c49fd6c94bc8e295115dcae97c9f902ec0e16f41cbfab80d070b` |
| `nexuspilot-file-runtime:20261012-2f06962` | `cc4663d4539f8e8fb4443f7a6d586ca24e3140844cb423d54bafc5c941550f2b` |

三个镜像均在目标 AMD64 主机构建，未把本机 ARM64 镜像当作服务器版本。MySQL/MinIO 不升级镜像；为保持现有 Compose 共用 RELEASE_TAG 的兼容，为旧 MySQL 镜像增加新标签，但运行的数据库容器未重建。

## 验证结果

- 提交前相关后端、模型、部署配置和续接存储测试：109 通过，Ruff、差异空白检查通过。此前本地真实 DeepSeek 四个文件场景的记录见[工具主计划](phase-7-tool-runtime-plan.md#首批附件工具实现与运行2026-10-09)，不能代替下方服务器检查。
- 服务器生产配置预检七项通过；API/Web 构建、迁移通过；`nginx -t` 通过；五个容器健康。
- `manage_runtime.py check --image nexuspilot-file-runtime:20261012-2f06962`：非 root、只读根文件系统、无网络、无额外 capability、临时目录与资源限制通过；DOCX/XLSX/PPTX/PDF 及实际解析协议检查通过。OCR 未增加。
- API 与 Web 均为 UID 10001；API 增加 Socket 专用组，不加入 Docker 组，不挂载 Docker Socket。只有受管理员管理的解析执行器具有 Docker 访问权。
- 外部本机 `curl --cacert`：HTTPS 首页 200、匿名 `/api/nexus/sessions` 401；未关闭 TLS 校验。仍是原有自签证书，指纹不变，到期时间为 2027-01-02 18:32（Asia/Shanghai）。
- 实际 HTTPS Web 代理检查：登录 Secure/HttpOnly/SameSite Cookie、缺少 CSRF 返回 403、匿名会话拒绝、直接 API/docs/openapi 路径 404、测试 B 读取测试 A 会话或文件返回 404。不是全资源双向权限矩阵重跑。
- 已登录文件能力接口报告 `executor_status=available`。检查使用专用临时账号和合成文件，最终退出并停用测试账号，保留合成审计记录，不读取正式用户会话。

| 服务器场景 | Run ID | 实际结果 |
|---|---|---|
| 快速回复首轮 DOCX | `ffdb9561-4f5f-4347-b315-b873a3000f20` | 通过：实际 list/read 工具成功，最终回答保存且含正确文件标记 |
| 快速回复续聊新增 PDF | `3a03e5bd-d534-458a-9130-dda092700a5f` | 通过：实际读取新 PDF，最终回答保存且含正确文件标记 |
| Agent 首次 DOCX | `1611ac2b-fb96-43d2-923a-92b9b8446da7` | 失败：`worker_execution.read-sample-docx` 与 Handoff 完成，随后 `worker_execution.extract-marker` 返回 `node_output_invalid`，未生成最终回答 |
| Agent 相同条件复测 DOCX | `c0ed5bf2-8294-47cf-bdbf-149fa9e47e48` | 失败：`controller_planning` 返回 `node_output_invalid`，尚未开始文件工具；停止继续付费重试 |

Agent 的第二轮 PDF 在两次首轮失败后均未执行。未修改生产业务代码、关闭审核或结构校验，也没有更换模型以制造通过。已定位到上述节点，尚未确定具体不符合要求的输出字段；需另行检查模型返回与节点 Schema。所有本次 `release-*` 验收账号已停用（数据库确认 active 数量为 0），已登录会话已退出。

服务器沿用已登记的 `deepseek-flash`，没有擅自更换模型名称、模型能力、密钥或推理策略。检查脚本通过实际 HTTPS/SSE 上传及提问，再核对本次 Run 的数据库工具记录、文件 ID、提取标记、Context 和最终消息。没有手工提交 tools 或 execution_profile。

检查脚本保留于服务器 `/tmp/nexus-verify-release-2f06962.py`，并复制到上述备份目录，不含凭据；运行依赖 API 镜像内的应用包、httpx、SQLAlchemy，以及 `/tmp` 的合成文件和公开证书。日志保存在上述备份目录的 `verification-2.log`、`verification-agent-retry.log`；首次脚本在异常时打印的整体 `running` 不代表成功，应结合失败 traceback 与本记录判断，复测脚本已明确输出 `failed`。初次准备检查因只读容器不允许 `docker cp` 写入而未启动业务测试，随后通过非 root 进程写入已允许的 `/tmp` 完成准备，没有关闭只读限制。

## 后续维护与限制

未来 Compose 更新必须包含解析 overlay：

```bash
cd /opt/nexuspilot/current
docker compose --env-file /etc/nexuspilot/config/.env \
  -f deployments/compose/docker-compose.production.yml \
  -f deployments/compose/docker-compose.office.yml ps
```

私有 `.env` 已配置指向这两份文件的 `COMPOSE_FILE`；如果命令显式提供 `-f`，必须同时提供两份，不能仅使用旧生产文件重建 API，否则解析 Socket 挂载会丢失。文件执行器只接受固定解析协议，不是聊天模型可调用的任意 Shell/Python 服务。

如需恢复旧应用，先处理正在进行的请求，保留新数据库和对象记录，恢复备份中的 Compose 配置并让 current 指回旧发布，再启动旧镜像。此次迁移只增加可空字段；不自动降级数据库或删除新工具记录。该恢复步骤尚未实际演练，不能当作已验证回滚能力。

Agent 审核未认可工具证据的已知问题仍未修复；此次服务器还出现节点输出结构校验失败，需保留失败事实。本次没有关闭审核、跳过结构校验或降低测试条件。未执行真实浏览器页面验收、全资源权限矩阵、负载测试、完整供应链审计、异机恢复与证书自动续期。
