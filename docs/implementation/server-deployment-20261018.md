# 2026-10-18 发布与 Strata 附件验证记录

## 发布结果

用户授权：一次 Strata 含文件调用通过后，提交、推送并部署。业务代码包含 `9d90baf` 的完整对话正文对象存储修改；已发布源码提交为 `0c84e7213506ebc3f91831070924f0499a0e6bcf`，分支 `codex/workflow-contract-repair` 已推送，未合并 main。

服务器 `43.134.11.254` 的 `/opt/nexuspilot/current` 已指向 `/opt/nexuspilot/releases/20261018-0c84e72`。入口仍为 `https://43.134.11.254:8443`，沿用自签证书。API/Web/Nginx 健康，专用文件执行器 active，数据库由 `20261008_0023` 升到 `20261018_0024`。原有 `strata-proxy` 容器未修改、未重启。

服务器 Provider 配置仍只有原有 DeepSeek 地址及私有配置；Strata Key 未配置，未自动复制本地 Key 或登记线上 Strata 模型。因此本次 Strata 联调结果来自本地，不代表服务器已经启用 Strata。

## 本地发布前验证

- 真实 Strata 快速回复上传合成 TXT，模型实际调用一次 `read_file_content`，最终回答包含随机核验编号和设备型号；不是把工具结果预置到提示词中。
- Run：`07bd95b7-bf8b-4893-8c73-f77f5db2c4be`。两次模型请求输入 4,352、输出 283，总计 4,635 token，缓存命中 1,605 token；费用为空，不能报告零费用。上传至回答重新读取共 12.628 秒。
- 初始上下文无文件全文；SQL Assistant 正文为空且存在对象引用，API 从 MinIO 重新读取结果一致。
- 命令及适用限制见[验证说明](../../deployments/verification/README.md#strata-真实附件调用2026-10-18)。未追加 Agent 或其他付费调用。
- `npm test`：142 通过；`npm run typecheck`：通过。首次受限沙箱执行因 IPC socket 权限失败，获准在允许本地 socket 的环境重跑通过。
- 新验收脚本 Ruff、`git diff --check` 通过。上一提交的后端测试结果见阶段2记录，本次未重复整个后端测试集。

## 构建、备份与切换

使用已提交 HEAD 的 `git archive`，未上传本地环境文件、用户数据或虚拟环境。目标 AMD64 主机上构建 API/Web 与文件运行镜像；Next.js production build 通过。解析镜像检查通过 DOCX/XLSX/PPTX/PDF、只读根目录、无外网、无 capabilities、临时目录写入与资源限制。

| 镜像 | ID |
|---|---|
| API `20261018-0c84e72` | `sha256:8c1678e03ec0cbf84778323ef8163bfbe972e348d3c61f5c66175e26f892e785` |
| Web `20261018-0c84e72` | `sha256:73829a10db7468c313c12db199d97006b8ce2e50d568e35735c5885578ce292a` |
| 文件执行器 `20261018-0c84e72` | `sha256:cc4663d4539f8e8fb4443f7a6d586ca24e3140844cb423d54bafc5c941550f2b` |

停写前查询没有执行中的模型尝试或工作流。停止 Nginx/Web/API 后，使用 MySQL 一致性导出，再停止 MinIO 并备份数据卷，保存配置与证书。备份目录 `/opt/nexuspilot/backups/20261018-0c84e72` 权限 0700，包含：

- `database.sql`，约 5.7 MiB；
- `minio.tar.gz`，约 164 KiB；
- `config-tls.tar.gz`，约 8 KiB；
- `SHA256SUMS`。

随后恢复 MinIO，显式执行迁移，更新 current 与解析镜像设置，依次启动执行器、API、Web、Nginx。使用生产 Compose 与 Office overlay，不删除数据卷、不改用户账号和既有密钥。MySQL/MinIO 容器沿用既有配置，未重建数据库。

16 条历史 SQL 正文保留原状，不批量迁移。新正文写入 MinIO，SQL 存引用。旧版本不能读取这种新正文：不能仅切回旧镜像；需要维护停写并保存新数据，再按正文恢复流程或经授权恢复匹配备份。此备份在同一主机，不是异地灾备；本次未执行完整备份恢复演练。

## 线上无模型调用验收

通过实际公网 HTTPS Web 代理测试，客户端使用服务器公开证书校验 TLS，没有关闭证书校验。两名随机测试用户的凭据只在内存中；测试结束退出会话并停用账号。保留合成审计数据，没有读取已有用户对话正文。

| 检查 | 结果 |
|---|---|
| 首页可达；匿名会话请求 401；直接 API/文档路径 404 | 通过 |
| 登录 Cookie 的 Secure/HttpOnly/SameSite；缺 CSRF 写请求 403 | 通过 |
| TXT、DOCX、PDF 上传、下载字节一致 | 通过 |
| 专用执行器可用；MinIO 已保存解析结果包含预期文字 | 通过：DOCX 4 块，PDF 1 块，TXT 1 块 |
| 三条新消息保存后经独立 GET/消息列表恢复 | 通过：SQL 正文为空、对象引用存在 |
| 第二名用户读取会话、消息、文件及原始下载 | 通过：均返回 404 |
| 退出后会话不可用，测试账号停用 | 通过 |
| Nginx 配置与容器健康 | 通过 |

验收会话 `110b94bd-881a-4c84-bc39-f4f8964d2abb`。服务器脚本 `/tmp/nexus-smoke-20261018.py`，合成样本 `/tmp/nexus-fixtures-20261018.json`。首次 `docker cp` 因根文件系统只读被拒绝，随后经 `docker exec` stdin 将文件写入已有 `/tmp` tmpfs；未放宽容器权限。

本次服务器模型请求数为 0。没有重新做浏览器人工交互、真实 Agent 工作流或全部格式复杂文件验收，不把 HTTPS 消息读取等同于完整浏览器刷新测试。外部发布端口仍仅 Nginx 的 80/8443；MySQL、MinIO、API 和 Web 未发布宿主端口。

## 后续操作

如需在公网界面使用 Strata，另配置服务器私有 Key、Provider endpoint 与模型目录后验证；当前线上仍沿用 DeepSeek。公开可信证书、异地备份、恢复演练和原有 Agent 模型结构输出问题，不因本次发布自动视为已解决。
