# 2026-11-01 服务器更新记录

状态：已完成本次提交推送、服务器更新与限定范围验收。用户授权检查远程仓库、提交推送最新改动并部署；本次没有付费模型调用。

当前运行：API/独立Worker使用 `20261101-15dbeae`，current为 `/opt/nexuspilot/releases/20261101-15dbeae`，数据库保持 `20261031_0050`。Python执行器已启用，正式授权为用户确认时全部可登录原生账号的快照，实际1个账号；标识仅保存在服务器私有配置。本页前半部分保留当日首次发布记录，最新运行与直接验收见末尾追加记录。

## 发布前现状

- 远程为公开仓库 `Mithzyl/llm_nexus_pilot`，默认分支 `main`。实际 fetch 后，远程没有独立新增提交，本地领先161个提交；发布前另有6个未提交文件。
- 本次包含App记忆历史读取、记忆写入结果诊断及直接相关测试和文档。
- 服务器 `43.134.11.254` 的current实际指向 `/opt/nexuspilot/releases/20261018-c8f6665`；API/Web/Nginx/MySQL/MinIO健康，文件执行器active，数据库为 `20261018_0024`。
- 本地迁移最新为 `20261031_0050`。发布必须先停写、保存匹配数据库/MinIO/配置备份，再显式执行迁移；不自动降级或删除已有数据。
- 服务器有1条STARTED模型尝试，开始于2026-10-17 17:57:27（数据库记录时间），并非本次新请求；不把历史未终结记录伪造为完成。

## 本次实际验证

- App记忆、诊断和部署配置专项：42通过，使用inline后端、SQLite、内存对象存储及固定模型，不是生产业务验收。
- 前端：214项测试、TypeScript、ESLint、生产构建通过。
- 工作流及文件工具回归：76通过。完整回归首次发现共享测试模型仍禁止已有的合成节点流式调用，现改为通过同一固定响应返回完成事件。
- 笔记来源回归还发现比较未包含既有来源类型默认字段；预期保留全部原始快照字段，并明确核对 `source_kind=file`、`web_result_id=null`。
- 笔记来源11项、项目/场景/架构40项、本机HTTP与目录刷新19项通过。补齐提交恢复路由清单；把项目文件/会话查询、场景发布事务移到既有服务，权限条件、分页和返回值保持；目录刷新测试使用隔离数据库。
- 真实本机MySQL/MinIO自动记忆专项2项通过（7.67秒），数据库 `authz_audit_b8f781f58ca044d886449d6d6c0b757c` 与 `authz_audit_b9409c303b234840aa17d203921287b4` 按既有脚本保留；不是生产供应商验收。
- 完整回归进一步复现合成流式调用丢失私有供应商续接状态，导致Worker恢复失败；先修正固定模型遵守真实私有信封，再确认失败，最后将普通流式响应与合成流式响应的解码集中到同一函数。恢复/续接/普通响应/文件工具专项89通过、4项条件性跳过；Context预检/聊天终态/固定审核样本39通过。
- 固定审核样本补齐流式接口；大文件范围测试改为先调用Context预检，避免把已失败的保存轮次当成可以重用的Run。保留大小拒绝、无模型调用和范围读取断言，不更改聊天失败终态。
- Python修改文件Ruff及差异检查通过。最终完整回归命令为 `NEXUSPILOT_WORKFLOW_EXECUTION_BACKEND=inline apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests packages/models/tests -m 'not infrastructure' -q --tb=short -rs --maxfail=3`：1467通过、47跳过、4按基础设施标记未选择，306.46秒。跳过项需要显式Docker/MySQL/MinIO/地图凭据或不适用的固定审核条件，不计作通过。日志为执行机 `/tmp/nexus-release-api-tests-verified-20261101.log`。
- 前端实际命令为 `npm --prefix apps/web test`、`npm --prefix apps/web run typecheck`、`npm --prefix apps/web run lint`、`npm --prefix apps/web run build`。本机沙箱最初拒绝测试Socket，随后在允许本机Socket的环境重跑通过；没有修改私有开发配置。测试自动重写的两份审计JSON已恢复为执行前内容，未纳入发布提交。

## 当日首次发布与备份

本次新增代码提交为 `b4feccb`（App记忆历史和写入结果）、`73ac96d`（服务职责与测试兼容）及 `786c70f`（合成流私有续接修复），已普通推送至 `origin/main`。远端实际从 `38e1a00` 前进到 `786c70f`，包含161个此前本地提交及本次3个提交；没有强制推送或改写提交历史。

通过 `git archive` 仅上传已提交源码，在服务器AMD64架构构建API/Web。current现指向 `/opt/nexuspilot/releases/20261101-786c70f`，API/Web使用同名发布标签，数据库实际由 `20261018_0024` 升到 `20261031_0050`。API/Web/Nginx/MySQL/MinIO均健康，文件执行器active；既有文件执行器源码自上次发布以来无变化，因此保持其原进程和镜像。Strata代理未修改、未重启。

| 镜像 | 实际ID |
|---|---|
| API `20261101-786c70f` | `sha256:b026281734366f34de773fee85f08702af74c877fe087b3dc312582ca5d63aee` |
| Web `20261101-786c70f` | `sha256:a2bd89242496b4188534f6c9445b3481f6874046765509b9ff746912879d76cd` |

停写前确认最近一小时没有STARTED模型请求；停止Nginx/Web/API后导出MySQL，再停止MinIO并复制其数据卷，未删除或重建数据卷。备份目录 `/opt/nexuspilot/backups/20261101-786c70f` 为0700，文件为0600，包含 `database.sql`、`minio.tar.gz`、`config-tls-before.tar.gz`、`config-tls-after.tar.gz`、旧目录和镜像记录。压缩包完整性检查通过；这是同机备份，未做异地备份或恢复演练。

沿用服务器原有私有配置、用户数据及Provider密钥；本地环境文件、数据库和用户文件未上传。服务器此前没有记忆密钥，本次仅在服务器生成独立32字节密钥写入私有 `api.env`，并保存更新后的配置备份；没有把密钥值写入代码、日志或此记录。部署使用生产Compose及Office overlay，显式迁移完成后依次等待API/Web/Nginx健康；Nginx语法检查通过。MySQL容器继续运行旧镜像；为新发布标签创建相同镜像ID的别名，未重建数据库容器。

## 服务器HTTPS验收

入口仍为 `https://43.134.11.254:8443`，沿用既有自签证书。使用该服务器公开证书验证TLS，没有关闭证书校验。脚本为本次可审阅的 `/tmp/nexus_smoke_20261101.py`；未知内容的旧临时脚本未读取。执行命令为 `docker exec nexuspilot-api-1 python /tmp/nexus_smoke_20261101.py`；报告在服务器 `/tmp/nexus-smoke-result-786c70f.json`，部署日志为 `/tmp/nexus-deploy-786c70f.log`。

| 检查对象及通过条件 | 结果 |
|---|---|
| 首页200；匿名会话401；直接API、docs及OpenAPI路径404 | 通过 |
| 两个合成账号登录；安全Cookie；无CSRF写入403 | 通过 |
| 消息持久保存；按submission_key恢复相同Run和正文 | 通过 |
| TXT上传ready；下载字节一致；解析器能力available | 通过；未重新解析PDF/Office样本 |
| 记忆加密保存、纠正、历史读取、版本2撤销为版本3并恢复原正文 | 通过 |
| 项目文件/会话列表；另一账号读取会话、提交、文件、记忆历史及项目文件均404 | 通过 |
| 退出后会话请求401；测试结束停用合成账号并撤销残留会话 | 通过 |
| 模型请求数 | 0；本次没有调用供应商 |

合成Session为 `92820072-5e7e-41f7-a9b6-f9180f895d0b`，记忆条目为 `911836f8-441f-4546-a0fb-a60f514dc7d0`。未读取已有用户对话正文。运行容器OpenAPI另确认App历史选择参数及诊断路由存在；App真实页面和远程回调未在服务器重新联调。

## 当日首次发布时的运行限制

服务器保持 `workflow_execution_backend=inline`，未启用独立Worker；Python执行器未配置，自助注册未开启。已有Provider配置沿用，真实模型质量、付费调用、App双端页面、生产邮件与完整浏览器交互未在本次验收；真实本机MySQL/MinIO和固定模型结果不替代这些项目。

10月17日的未终结模型记录保留原状，未伪造完成或费用状态。数据库已经升级且新版本已产生合成验收数据；旧版本兼容性未证明，不能仅切回旧镜像、自动降级数据库或覆盖发布后的新增数据。

## 当日追加：独立Worker与Python执行器

状态：已完成部署启用及约定的直接验证。用户授权部署两项服务、完成相关验证并更新记录；明确不做多余测试、不做回归、不做哈希检查。用户进一步授权所有当前可登录的原生Nexus账号使用Python，并选择只做无付费验证。宿主账号加入Docker组曾被自动审批拒绝；用户随后明确同意 `nexus-python-runtime`（UID10001）获得该组权限，之后才执行安装，没有改用其他高权限账号绕过拒绝。

本次新增生产Worker profile、API/Worker共享的Python Socket overlay、Python执行器systemd单元及Socket客户端启动单元；Office Socket配置同时应用到API和Worker。宿主执行器UID10001与容器用户一致，目录0700、Socket0600，不扩大Socket权限，也不向API/Worker挂载Docker Socket。Worker启动前实际查询没有待执行Worker任务；旧inline失败记录不转换执行。

代码提交 `15dbeae` 已推送，API/Worker镜像使用同名发布标签，Web继续使用相同业务内容的 `20261101-786c70f` 镜像。Python镜像为 `nexuspilot-task-runtime:20261101-worker`，从服务器原有解析镜像离线构建，没有下载新依赖；本次没有数据库迁移。原有MySQL、MinIO、Nginx、Web、文件执行器和Strata代理未因验收重建或清空数据。

### 已启用的正式配置

- API与Worker均为 `workflow_execution_backend=worker`，共享同一私有 `api.env`、数据库、MinIO和记忆密钥。Worker实际命令为 `python -m nexuspilot_api.commands.workflow_worker`，容器为running，重启策略 `unless-stopped`，停止信号SIGINT。
- `nexuspilot-python-runtime.service` 为active/enabled，运行身份为独立宿主账号 `nexus-python-runtime`。该账号按用户明确授权加入Docker组；API/Worker容器没有Docker Socket。
- Python状态目录 `/var/lib/nexuspilot-python` 和Socket目录 `/run/nexuspilot-python-runtime` 为0700，Socket为0600；UID10001与容器用户一致。API和Worker均已实际看到可用Socket。
- `nexuspilot-runtime-clients.service` 为active/enabled，依赖文件/Python执行器之后重建API/Worker，以刷新重建的Socket目录挂载。实际安装、启动及配置重新加载通过；没有重启整台服务器进行演练。
- 正式Python授权只保留当时可登录的1个原生账号，不输出其用户ID；临时合成账号授权已撤销。以后新增账号不自动加入，App和快速回复不获得Python执行权限。自助注册和动态Python工具功能没有因本次部署自动开启。

### 本次直接验证

本次只执行合并Compose配置检查、修改文件Ruff/差异检查、脚本语法检查、systemd单元检查及下列实际部署验收。没有运行pytest、完整回归或新增哈希校验；运行时自身已有的数据格式和校验行为保持。

为避免付费调用，临时启动无凭据、无宿主发布端口的私有固定响应容器，登记专用 `deployment-fixed-runtime` 模型，不修改已有Strata/DeepSeek等真实供应商地址和密钥。最初临时模型推理配置不符合平台规则，之后固定响应服务又把快速回复当作Python Agent，验收失败记录保留；只调整临时协议和分流，没有修改产品规则或把失败写为通过。最终使用支持的Responses协议完成下列三项；这组验收共8次固定响应模型请求，真实供应商请求为0。

| 要求、执行对象和通过条件 | 实际结果 |
|---|---|
| 真实HTTPS快速回复进入数据库队列，由独立Worker领取租约并保存回复 | 通过：执行completed，执行owner为独立消费者，Assistant消息已保存 |
| 真实Worker执行两次Python，第二次读取第一次的中间文件，再保存并下载MinIO产物 | 通过：两个run_command均completed，下载 `answer.txt` 内容为 `42` |
| 在Python长调用实际进入Docker容器后从HTTPS发出停止请求，等待工作流取消并确认容器移除 | 通过：宿主观察器确认指定调用的容器running；随后工作流cancelled、执行器返回cancelled及container_removed，精确容器已不存在 |
| 撤销临时账号/模型配置，重新加载正常供应商，复核最终入口与服务 | 通过：临时端点及模型启用数均0，固定服务容器已移除；API健康、Worker运行、执行器active，公网TLS校验后HTTP200 |

完成工作流 `c4958a4b-bf54-4d3e-836c-2519f07714ce`；产物 `c58f8808-797d-478f-8f06-ba3b74f3a6b8`；两次Python调用 `34864c78-c0d9-42aa-8d9b-adf24de7291a`、`07cd58c2-3d26-4a90-b15c-b66a5834d179`。取消工作流 `c6fd18bd-7966-4486-84ab-b5a24c5d9e1d`，实际停止调用 `e0b8d6e0-c4a5-4c34-ab50-32560357f638`。这些均为本次合成验收数据，没有读取既有用户正文。

验收脚本为服务器 `/tmp/nexus_runtime_acceptance_20261101.py`，宿主在途观察脚本为 `/tmp/nexus_runtime_cancel_observer.py`。实际命令为 `docker exec nexuspilot-api-1 python /tmp/nexus_runtime_acceptance_20261101.py`，并行运行指定调用观察器。最终非敏感报告保存于 `/opt/nexuspilot/backups/20261101-15dbeae-runtime/acceptance-report.json`，部署日志为 `/tmp/nexus-runtime-enable-20261101.log`，清理及正常配置重新加载日志为 `/tmp/nexus-runtime-finish-20261101.log`。

Worker切换前私有配置备份在 `/opt/nexuspilot/backups/20261101-15dbeae-worker`；Python配置变更前后备份分别为 `/opt/nexuspilot/backups/20261101-15dbeae-runtime/config-before`、`config-final`。此前数据库/MinIO同机备份继续保留。本次没有做新的数据迁移、回归、备份恢复或整机重启演练。

临时合成账号已停用、会话已撤销、临时Python授权已移除；固定端点/模型已停用，临时假模型配置已恢复原值，临时凭据文件已删除。合成调用记录、产物和执行器状态保留，不自动清理历史目录。实际业务Provider质量、RabbitMQ调度和通用无人值守长期任务不属于这次无付费部署验收。
