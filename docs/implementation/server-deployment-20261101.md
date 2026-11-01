# 2026-11-01 服务器更新记录

状态：已完成本次提交推送、服务器更新与限定范围验收。用户授权检查远程仓库、提交推送最新改动并部署；本次没有付费模型调用。

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

## 实际发布与备份

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

## 当前运行限制

服务器保持 `workflow_execution_backend=inline`，未启用独立Worker；Python执行器未配置，自助注册未开启。已有Provider配置沿用，真实模型质量、付费调用、App双端页面、生产邮件与完整浏览器交互未在本次验收；真实本机MySQL/MinIO和固定模型结果不替代这些项目。

10月17日的未终结模型记录保留原状，未伪造完成或费用状态。数据库已经升级且新版本已产生合成验收数据；旧版本兼容性未证明，不能仅切回旧镜像、自动降级数据库或覆盖发布后的新增数据。
