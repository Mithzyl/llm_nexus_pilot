# 2026-10-18 发布与 Strata 附件验证记录

## 当日追加发布：20 MiB 上传与服务器 Strata（当前版本）

下方 `0c84e72` 为当日首次发布历史。用户随后明确授权提高上传大小、服务器配置 Strata、提交推送部署，并允许维护重启可能中断一条长时间未终结的 DeepSeek 请求。

- 当前源码目录 `/opt/nexuspilot/releases/20261018-c8f6665`。上传及 Strata 登记实现 `88dbc2d`，解析临时文件修复 `c8f6665`，均已推送 `codex/workflow-contract-repair`，未合并 main。
- 前端、Web 代理、API、解析原件、Nginx 上限均为 20 MiB；解析 Socket 请求允许 base64 膨胀及协议字段。结果 4 MiB、Office 解压 20 MiB、块数/页数/超时等限制保持。不是无限大小或任意复杂文件都能解析。
- 第一次线上 20 MiB PDF 上传返回 422，发现解析进程 `RLIMIT_FSIZE` 仍为 4 MiB，写临时 PDF 原件失败；修正为允许 20 MiB 原件，输出仍独立限制为 4 MiB。补充回归测试后更新解析镜像。执行器重启后第二次验收在能力检查即停止，因 Socket 目录重新创建需要刷新 API 挂载；重建 API 后最终验收通过。前两次均未调用 Strata。
- API/Web 镜像业务内容仍为 `88dbc2d`；新 `c8f6665` 标签复用相同 API/Web/MySQL 镜像，不重新改变依赖。解析镜像为 `nexuspilot-file-runtime:20261018-c8f6665`，ID `sha256:c8b90bed93bc72d3b67798f45d5f6fa2ac6e385df88a2d41a364913bc08939d6`。API 已重建，Web 原进程继续使用相同业务内容。
- Strata endpoint `https://mithfusion.site/v1`，模型 `tiel-coder-35b-a3b-ud-q4_k_xl`。Key 经 SSH 私密传输到 `/etc/nexuspilot/config/api.env`，不进入 Git、SQL 或响应；暂存 Key 文件安装后删除。登记脚本 `deployments/production/register_strata.py` 可重复运行，发现已启用地址冲突则停止，不覆盖。工具/流式/缓存记录有真实调用依据；结构化输出仍标为未确认支持，不宣称 Agent 已验收。
- 更新前保存 `/opt/nexuspilot/backups/20261018-88dbc2d`：匹配的数据库、MinIO、私有配置/证书及 SHA256SUMS。校验和、gzip 完整性通过。同机备份，未做异地或恢复演练。解析热修复前另保存运行配置到 `20261018-c8f6665-parser`。

验证结果：

| 检查 | 实际结果 |
|---|---|
| 后端与模型完整回归 | 877 通过、32 项可选环境测试跳过；不把跳过项计为通过 |
| 临时文件修复后专项回归 | 28 通过、2 项可选 Docker 测试跳过；目标主机真实解析镜像检查另通过 |
| 前端测试及 TypeScript | 142 通过；类型检查通过 |
| 目标 AMD64 API/Web 构建、受限解析镜像检查 | 通过 |
| 线上恰好 20 MiB PDF 上传、下载校验、解析对象读取 | 通过，原件 20,971,520 字节，解析 1 块 |
| 大于 20 MiB 的文件预留 | 422 拒绝 |
| DOCX、TXT 上传及持久解析 | 通过，分别 4 块、1 块 |
| 登录、CSRF、跨用户会话/消息/文件读取拒绝、退出 | 通过；合成测试账号已停用 |
| Strata 模型目录可选、实际文件工具调用、答案重新读取 | 通过 |

20 MiB PDF 是在已有合成单页 PDF 后增加空白构造的大小边界样本，用于验证传输和原件资源限制，不证明 20 MiB 密集正文或扫描文档可解析。最终服务器测试 Session `b8a5236f-3ca0-439c-8416-79cce271d5d0`、Run `5c2490f4-24ef-41b1-8137-e04b0e547de3`。Strata 两次模型请求、一次实际 `read_file_content`，返回 PDF 内的英文原句并恢复消息正文；耗时 11.398 秒，输入 4,551、输出 236，共 4,787 token，缓存命中 1,732。没有已配置价格，不推算费用。验收脚本 `/tmp/nexus-smoke-upload-20261018.py`，镜像修复步骤 `/tmp/nexus-parser-fix.sh`；脚本 report 的 `release=88dbc2d` 指业务版本，实际执行器修复为 `c8f6665`。

维护前的 DeepSeek Attempt `920b2d67-5b18-4aaa-a018-3bc2e8500b40` 在本轮前已超过 10 分钟仍为 STARTED、Run 为 PENDING；用户允许重启后继续发布。没有伪造其成功、取消或费用状态，数据库仍保留该未终结记录，需要另行诊断恢复。此问题不是本次 Strata 验收请求。未做额外浏览器人工交互或真实 Agent 验收。

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
