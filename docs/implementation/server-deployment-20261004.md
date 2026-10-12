# 2026-10-04 服务器部署记录

> 本文为首次部署的历史记录。当前版本已于 2026-10-12 更新，发布目录、解析执行器和验收结果见[最新部署记录](server-deployment-20261012.md)。

## 状态与范围

**实例部署已完成，基础验收通过；公开多用户发布的全部验收仍未完成。** 用户授权使用 Docker、自签证书和现有 DeepSeek Key。当前仅供预置账号受限使用，没有开放注册。没有复制本地数据库、历史聊天、上传文件或其他 Provider 凭据。

| 对象 | 实际配置 |
|---|---|
| 入口 | `https://43.134.11.254:8443`；HTTP 80 返回 308 跳转到此入口 |
| 主机 | Debian 13 / AMD64，2 CPU，约 3.6 GiB 内存 |
| 容器 | Nginx、Web、API、MySQL、MinIO，由 Compose 项目 `nexuspilot` 管理 |
| 发布目录 | `/opt/nexuspilot/releases/20261004-selfsigned` |
| 当前目录 | `/opt/nexuspilot/current` 指向该发布目录 |
| 私有配置 | `/etc/nexuspilot/config`；目录 0700、文件 0600 |
| TLS | `/etc/nexuspilot/tls/fullchain.pem`、`privkey.pem`；RSA 3072，自签，SAN 包含入口 IP |
| 数据 | 新建 `nexuspilot_mysql-data`、`nexuspilot_minio-data` 持久卷 |
| 数据库版本 | `20260916_0022`，由独立迁移账号执行；运行账号只有 DML 权限 |
| 正式登录 | `operator@example.com`；随机初始密码仅保存于服务器私有文件 |
| 模型 | `deepseek-flash`、`deepseek-v4-pro`；均已登记且前端可选择 |

原有 x-ui/Xray 继续运行，443 和其他原有端口没有变更。**本次仅核对本项目的端口与权限，没有对原有服务做完整安全审计。**

## 版本与镜像

首次部署来自分支 `codex/deployment`，当时 HEAD 为 `83d335e`，使用尚未提交的工作区源码，不声称该提交已经包含部署修改。部署执行时未进行 Git 提交或推送；随后用户要求将部署成果提交并合入 `main`，作为后续开发基础。Git 合并不会改变本次服务器镜像和源码快照；远端推送及重新发布需分别获得授权。

无凭据源码快照保存在服务器发布目录的 `source-final.tgz`，SHA-256：

```text
b7e5239bd6138b4da5003d1cb1bc275bce298e098d0f9fb1fafa52ed85939ce8
```

快照排除了环境文件、私钥、node_modules、本地虚拟环境、运行目录和 macOS AppleDouble 元数据。秘密和业务数据不在镜像或源码快照中。

| 服务 | 实际镜像 ID（SHA-256） |
|---|---|
| MySQL | `37af68fc2eff23a93e8f9292995433e2488188962dc5a0b0d873189de5646373` |
| API | `8ff7a40047169a2dbe4f3803c0645d4b56ac46a2cda8fb7c313a6b289bd0c53a` |
| Web | `a52319ec776b1222aa6d47d75c6d341a6af2842ba977bb309e3d65e54a3e0894` |
| MinIO | `e198a3d710c982c7b1f31dbd43f330204c6cf0527eb04ab906076970c5ec815d` |
| Nginx | `dc73b49f5124cf2ee538dfbdfbd121f0b4ccdcb20fea30f3a81bd477c02e2bb5` |

MinIO 使用用户指定的 `coollabsio/minio:RELEASE.2025-10-15T17-29-55Z`，Compose 固定多架构索引摘要 `69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9`。该第三方构建的供应链签名和完整镜像漏洞扫描未执行。

## 本次发现并修复的问题

1. **MySQL 初始化影响父级 shell。** 官方入口 source 自定义脚本，脚本的 `set -u` 意外作用于官方入口，触发 `MYSQL_ONETIME_PASSWORD: unbound variable`。将严格选项限制在子 shell；回归测试先失败再通过。
2. **MySQL 健康检查过早就绪。** `mysqladmin ping -h localhost` 使用 Socket，初始化临时实例尚未开放 TCP 就报告成功，迁移收到连接拒绝。改成迁移账号经 TCP 执行 `SELECT 1`；不删除卷、不重置密码。
3. **macOS 归档迁移元数据。** `._xxx.py` AppleDouble 文件被 Alembic 误当作源码，报 `source code string cannot contain null bytes`。构建忽略这些文件，最终归档关闭资源扩展；重新构建后真实迁移完成。
4. **前端运行依赖安全更新。** Next.js 与 `eslint-config-next` 从 16.2.6 更新至 16.3.8，更新锁文件中的关联运行依赖。原运行依赖审计有严重／高危条目；更新后官方 npm 运行依赖审计为 0 项。开发依赖审计仍有 7 项高危，未通过强制升级全部工具解决；这些开发依赖不作为 standalone 运行依赖部署。没有因此宣称所有容器或整个应用已完成安全审计。

相关命令：

```bash
apps/api/.venv/bin/pytest apps/api/tests/test_deployment_config.py -q
# 8 passed；修改前新增的两条断言失败

cd apps/web
npm test
npm run lint
npm run typecheck
npm audit --omit=dev --registry=https://registry.npmjs.org
# 121 tests passed；ESLint、TypeScript 通过；运行依赖 0 项审计漏洞
```

实际服务器已构建 AMD64 MySQL/API/Web；Web 生产构建通过。预检七项、`nginx -t`、5 个容器健康检查通过。API/Web UID 为 10001。启动时容器内存观测约为 MySQL 492 MiB、MinIO 168 MiB、API 101 MiB、Web 37 MiB、Nginx 11 MiB；这是一次空闲观测，不是并发容量结论。

## 真实验收结果

HTTPS API 检查使用证书文件验证 TLS，不使用 `--insecure`。Playwright 使用独立 Chrome 测试会话，仅在该测试上下文允许自签证书，没有修改系统或用户浏览器的信任设置。

| 检查 | 结果与范围 |
|---|---|
| HTTPS 与 HTTP 跳转 | 通过：外部 HTTPS 返回 200，HTTP 返回 308 到 8443 |
| 登录与 Cookie | 通过：两个合成账号登录成功，登录 Cookie 带 Secure、HttpOnly、SameSite=Lax |
| 未登录及内部接口 | 通过：公开代理会话请求 401；原始 API、docs、openapi 路径 404；容器内直接请求无服务 Key／仅服务 Key 均不能访问聊天 |
| CSRF | 通过：缺少 CSRF 或错误 Origin 的会话创建返回 403 |
| 跨用户 | 通过：B 看不到 A 的会话列表项，会话详情、文件读取／删除返回 404；不将此冒烟测试当作所有资源双向矩阵重新执行 |
| 文件与上下文 | 通过：文本上传解析 ready，下载字节正确；DeepSeek 两轮均读出附件随机校验码，第二轮没有再次上传或携带文件 ID |
| 真实 SSE | 通过：两轮分别收到 9 个文字增量和完成事件，增量跨度约 0.090 / 0.083 秒，消息保存成功；这不是模型性能压测 |
| 模型列表 | 通过：两个登记模型 `registry_available=true`、`selectable=true`；本次实际生成仅调用 Flash，未验证 Pro 生成 |
| 重建持久化 | 通过：仅重建 API/Web、保留 MySQL/MinIO 后，原 Cookie、4 条消息、上传文件仍可读取；并非整机灾难恢复或进行中工作流恢复 |
| 删除与退出 | 通过：删除本次合成文件后读取 404；B 退出后重用原 Cookie 返回 401 |
| 浏览器新对话 | 通过：界面发送后实际回复自动显示并保存，刷新后仍显示。模型未按提示输出指定“验证成功”句子，而是返回拒绝性解释；本项只证明调用／渲染／保存正常，不把模型自述当作测试通过证据 |
| 后台存储权限 | 通过：应用数据库账号无 CREATE/DROP/ALTER/GRANT；MinIO 应用账号只看到指定桶，创建其他桶被 AccessDenied 拒绝 |
| 端口发布 | 服务器侧通过：Compose、Docker NAT、监听均只发布本项目 80/8443；3000/8000/3306/9000/9001 没有宿主发布；无全局 IPv6 地址 |
| 独立外部端口扫描 | **证据不足**：本机经 `utun1500` / `198.18.0.1` 代理路由，TCP 探测全部报告连接成功；对 8000 的 HTTP 请求 5 秒无响应。不能把此结果当作端口暴露，也不能宣称无代理外网扫描通过。需另一个无代理网络复核 |
| PDF | **失败**：真实服务器 `bwrap` 报不允许创建 namespace。没有改成 privileged 或关闭隔离；当前不能宣称 PDF 可解析 |
| Office／OCR | **未执行／未启用**：独立 Office 执行器未部署，OCR 不属于此次可用能力 |

HTTPS 测试入口：[check_release.py](../../deployments/verification/check_release.py)，执行方式见[验证说明](../../deployments/verification/README.md#已授权服务器的-https-验收)。浏览器截图为本地 `output/playwright/server-release-20261004.png`。独立内部权限检查与重建后只读检查在本次私有临时目录中执行，关键结果已记录在上表，不把临时程序当作长期维护入口。

本次专用账号 `deploy-check-a`、`deploy-check-b` 已停用并撤销登录会话；合成消息／审计记录保留在停用账号下，不属于正式 operator。测试上传文件已通过正式删除接口删除。

### 模型记录依据

已使用获授权 Key 从服务器成功查询 `https://api.deepseek.com/models`，实际返回 `deepseek-flash` 与 `deepseek-v4-pro`，窗口 1,048,576、最大输出 393,216。平台既有单次默认输出上限保持 65,536，没有趁部署更改产品设置。

[官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)说明旧 Flash 名称现在由新 Flash 服务处理；本次新库直接登记实际返回的名称，不迁移本地已有登记记录。JSON、工具、缓存和思考策略依据官方目录与[思考说明](https://api-docs.deepseek.com/guides/thinking_mode/)。原生视觉在本平台 DeepSeek 文件输入中未启用，能力记录对此保守标记；文件仍由平台解析后提供文字。目录来源和版本为 `deployment-20261004`。

## 访问与维护

入口为 `https://43.134.11.254:8443`。首次浏览器访问出现自签证书警告是预期现象，不是网站已使用受信任证书。核对 IP 和指纹后仅对该站点接受证书，不建议关闭整个浏览器的 TLS 校验。

证书 SHA-256 指纹：

```text
4F:B3:F8:86:1A:0F:24:71:30:AB:38:B3:AC:62:44:85:E0:8D:C0:9D:54:A5:59:23:11:AB:E9:F5:0A:70:24:0D
```

证书到期：**2027-01-02 18:32（Asia/Shanghai）**，未配置自动续期。

在服务器可信终端读取首次登录密码，不把输出粘贴到仓库、工单或聊天中：

```bash
sudo cat /etc/nexuspilot/config/operator-login.json
```

读取并保存到密码管理器后，可通过已有认证接口修改初始密码；当前前端没有密码修改入口，不能假设页面上已经提供该操作。这个 JSON 不会随密码修改自动更新。它不是 SSH 密码，也不是 DeepSeek Key。

查看服务状态：

```bash
cd /opt/nexuspilot/current
docker compose --env-file /etc/nexuspilot/config/.env \
  -f deployments/compose/docker-compose.production.yml ps
```

更新前保存数据和配置，保留旧镜像并确认数据库兼容；不运行 `down -v`，不使用重新生成全部凭据的方法更新配置。本次没有旧正式实例可回退，数据仍需要单独备份。

## 剩余事项

- **未执行：** 异机备份／恢复演练、自动备份、证书续期、告警、无代理外部端口扫描、云安全组独立审核。
- **未执行：** 目标环境完整全资源双向越权矩阵、错误／超时、爆破与容量压测、Pro 和 Agent Workflow 真实完整冒烟。已有代码／本地测试不代替这些检查。
- **不可用：** 当前 PDF 沙箱、Office 解析执行器、OCR；文本文件读取及连续上下文已验证。
- **产品限制：** 工作流进行中请求不能保证跨进程重启恢复；当前实际模型费用没有配置价格表，界面 `0.000000` 不能解释为 Provider 免费。页面仍有硬编码“开发环境”提示，但 API 实际使用 production、强制认证和 Secure Cookie；该文案问题没有在部署任务中顺带改动。
- **凭据维护：** SSH 密码曾出现在对话中，建议用户在保留可用管理入口的前提下轮换并配置 SSH 公钥。未未经授权更改 SSH 登录方式或关闭既有服务。
