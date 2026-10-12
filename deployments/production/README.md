# NexusPilot 首次生产部署

本目录提供单台 Linux 主机的 Docker Compose 部署，使用预置账号，只对外发布 Nginx。2026-10-04 已在目标 AMD64 主机启动首个实例，使用自签 HTTPS 8443；配置、实际检查和剩余限制见[服务器部署记录](../../docs/implementation/server-deployment-20261004.md)。部署准备来自 `codex/deployment`，前置功能提交为 `83d335e`；首次部署执行时使用获授权的未提交工作区快照，不能把该前置提交当成实际部署版本。部署验收后按用户要求提交并合入 `main`，后续开发以 `main` 为基础；服务器仍以记录中的源码快照及镜像 ID 标识，不随本地 Git 合并自动更新。

## 当前交付与验证范围

2026-10-04 本地 Docker 扩展预验证已完成：通过 HTTPS 模型流式、文件连续上下文和保留数据卷的冷重建测试；PDF 沙箱仍失败，生产 Office Socket 尚未验收。完整逐项结论见[本地部署预验证报告](../../docs/implementation/local-docker-preverification.md)。

### 当前 MinIO 镜像选择（2026-10-04）

用户已选择 `coollabsio/minio:RELEASE.2025-10-15T17-29-55Z`。生产 Compose 将它固定到多架构索引摘要 `sha256:69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9`，不使用浮动 `latest`。这是第三方 MinIO 构建，不是 MinIO 官方发布；本次没有完成供应链签名验证或漏洞扫描，运行兼容性通过不代表完成安全审计。

该镜像不包含 `curl`，不能沿用此前的 HTTP curl 健康检查。生产 Compose 只读挂载 `minio-healthcheck.sh`，使用自带 `mc ready` 检查本机服务，4 秒超时；凭据通过进程环境传入，不放在命令参数或检查输出中。账号、桶、存储卷和 API 存储接口保持不变。

替换验证结果：

- macOS Docker Desktop / Linux ARM64：真实 `bootstrap_minio.py` 连续执行两次通过；实际 `ObjectStorage` 上传、读取、哈希校验、列举、删除和重复删除通过；匿名读取、应用账号访问其他桶及创建桶均被拒绝；保留卷重建容器后，原账号和文件仍可使用。测试仅使用随机凭据及独立卷，结束后已删除测试容器、卷与凭据，保留镜像。
- 目标 Debian / AMD64：按同一索引摘要拉取成功；真实私有桶初始化、应用账号权限、HTTPS 文本上传／读取／删除，以及 API/Web 重建后的文件保留已通过，详见服务器部署记录。
- 配置测试 7 项通过；原镜像断言、缺失健康检查适配分别先确认失败再修改。复现命令见[存储镜像专项验证](../verification/README.md#存储镜像专项验证)。

本次只更新生产 Compose；开发 Compose 与本地已有 MinIO 数据不自动升级。此前本地完整 HTTPS 测试使用旧官方镜像，目标服务器本次使用所选镜像执行了单独验收，不将历史测试视为本次重新执行。

- 已提供生产 Compose、Web standalone 镜像、非 root API/Web、独立数据库运行／迁移账号、私有 MinIO 应用账号、HTTPS 入口和配置预检。
- JSON 归属检查覆盖大小写、`+json`、参数及缺少 Content-Type；原始文件字节按文件处理。生产拒绝已知示例、过短或缺失的服务秘密，且关键用途使用不同秘密。
- macOS Docker Desktop 的 Linux ARM64 上已构建并启动真实生产配置：迁移到 `20260916_0022`、MinIO 初始化、匿名请求拒绝、HTTPS 登录、双用户会话隔离、文件上传／下载／删除、退出后拒绝均通过。TLS 使用独立实验 CA，测试只绑定本机回环；未调用真实 Provider。
- API/Web 实测 UID 为 10001；数据库应用账号没有 DDL／授权权限，MinIO 应用账号只能访问指定 bucket。只有 Nginx 有宿主端口映射。
- **PDF/Office 依赖：** 当前开发代码要求专用解析 Socket，API 镜像不再安装 Poppler/bubblewrap。未配置执行器时两类文件均明确失败。线上仍为 2026-10-04 旧版本，PDF 嵌套 namespace 失败、Office 未部署的记录不变；2026-10-07 用户要求只做本地开发，本次不发布。文本／CSV／JSON 不依赖该服务。
- 目标 AMD64 实例已启动并完成基础验收；Office systemd 服务、证书自动续期、独立无代理端口扫描及备份恢复演练尚未验收。当前适用于预置账号的受限使用，不表示公开多用户验收全部完成。
- 当前准备版本的本地自动化检查：后端／模型 524 通过、8 项可选检查跳过；前端 121 通过，TypeScript、ESLint、Ruff 和差异检查通过。MySQL、API、Web 镜像构建、实际空库迁移、Nginx 语法及 HTTPS 冒烟另行验证。JSON 绕过和账号命令均先复现失败，再修复。

## 1. 准备环境与私有配置

需要 Linux Docker Engine、Docker Compose、Python 3.10+、受控 SSH 和匹配入口域名／IP 的 TLS 证书。自签证书仅适用于明确接受信任提示的受限使用；公开服务应使用受信任证书。先核对目标主机已有服务和数据，准备访问规则；首版不使用 `docker compose down -v` 更新服务。

从仓库根目录运行以下命令。替换示例域名、证书目录和版本标识：

```bash
python3 deployments/production/initialize.py \
  --origin https://chat.example.com \
  --certs /etc/nexuspilot/tls \
  --release YOUR_RELEASE_TAG
```

默认生成 `deployments/production/runtime/`（目录 0700、文件 0600，已忽略 Git），也可用 `--directory /etc/nexuspilot/config` 指定新目录。已有目录会拒绝覆盖，避免无意更换正在使用的密码。输出不打印秘密。生成文件如下：

| 文件 | 用途 |
|---|---|
| `.env` | Compose 的配置目录、域名、端口、镜像标签和证书路径 |
| `api.env` | API 的运行账号、独立服务秘密、认证设置、存储账号、Provider 密钥 |
| `migration.env` | 同一配置及专用数据库迁移账号；只供一次性迁移 |
| `web.env` | API 内部地址和部署 Key，不含数据库／Provider／内部 Key |
| `mysql.env` | 首次初始化数据库的账号凭据，root 只允许本地 Socket |
| `minio.env` | MinIO 管理凭据，仅给存储服务 |
| `storage-init.env` | 初始化私有 bucket 和限定应用账号的一次性运维凭据 |

只向 `api.env` 添加真实 Provider Key；Provider 地址仍在数据库中。不要把真实密钥写进 Compose、镜像或 `NEXT_PUBLIC_` 变量。初始生成的配置不包含可调用的 Provider Key。

证书目录需有 `fullchain.pem` 和 `privkey.pem`；挂载后必须能读取实际文件。若证书服务使用指向其他目录的符号链接，需要让目标文件也在正确挂载范围内，或安全复制到专用目录。私钥权限、可信证书链、域名、到期时间和续期后 Nginx reload 由运维确认。

Compose 只发布 Nginx 80/443，其他服务不发布宿主端口。`.env` 可设置测试端口和 `BIND_ADDRESS=127.0.0.1`；实际公网开放前核对主机和云网络规则、IPv4/IPv6以及 SSH 管理来源。

## 2. 预检与构建

下文以默认私有目录为例；自定义目录时替换所有 `--env-file` 路径。不要运行会打印完整展开秘密的 `docker compose config`，使用下面的预检或 `config --quiet`。

```bash
python3 deployments/production/check.py \
  --env-file deployments/production/runtime/.env

docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml build mysql api web
```

预检检查端口、网络、关键认证设置、私有文件权限及 TLS 文件存在，不替代证书信任、外部可达性或运行检查。镜像由当前目录构建；根 `.dockerignore` 排除配置、用户文件、Git、测试输出和本地虚拟环境。记录发布时构建出的镜像 ID；基础镜像标签与传递依赖仍需在发布流程中扫描、固定和保留。

## 3. 初始化存储并单独迁移

```bash
docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml up -d --wait mysql minio

docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml run --rm migrate

docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml run --rm storage-init
```

迁移失败时停止，不启动应用。运行中的 API 使用 DML 账号，不自动执行数据库迁移。MySQL 自定义初始化脚本打进镜像并以不可执行文件加载，避免 macOS bind mount 不可执行导致初始化跳过；它只在空数据卷首次运行。官方入口会 source 该脚本，因此严格 shell 选项只在子 shell 内生效；健康检查用迁移账号经 TCP 执行 `SELECT 1`，避免初始化临时 Socket 服务被误判为就绪。

从 macOS 打包源码时使用 `COPYFILE_DISABLE=1 tar --disable-copyfile --no-xattrs ...` 并排除所有环境文件、凭据和用户数据。根 `.dockerignore` 另外排除 `._*` AppleDouble 元数据，防止 Alembic 将 `._xxx.py` 当作迁移源码。不要通过删除数据库卷修复打包或迁移脚本问题。

`storage-init` 创建私有 bucket 和应用权限，不删除对象；重复执行会应用配置中的同一账号秘密和权限。已有部署更换密码需要明确轮换流程，不能重新运行初始化生成器或删除数据卷来解决。

## 4. 启动应用与预置账户

```bash
docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml up -d --wait api web

docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml exec api \
  python -m nexuspilot_api.commands.provision_local_user \
  --user-id operator --display-name Operator --email operator@example.com

docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml up -d nginx
```

密码由终端交互输入，不放在参数里。预置命令已修复独立进程启动时的 ORM 循环导入，并有 fresh-process 回归测试。历史本地会话的 owner 不会自动转移给新账号，迁移历史数据时需要另行确认归属。

迁移会准备已定义的 Provider endpoint，但不代表模型目录、能力和实际 Key 全部可用。通过受控运维接口核对模型登记与能力记录后，再验证首发 Provider；内部管理请求需部署 Key 加内部 Key且不带浏览器 Cookie。

Nginx 将 HTTPS Origin/Host 传到 Web；API 关闭通用代理头信任。入口设置登录频率、API 请求频率和连接数限制，并关闭代理响应缓存、缓冲和自动上游重试。后端仍保留账号失败限制；API 看到的是 Web 的地址，不将其当作真实公网来源 IP。

## 5. 可选 PDF／Office 执行器

2026-10-07 本地代码已增加固定 PDF 解析分支，API 不再启动嵌套沙箱；服务器安装与发布按用户要求暂停。新协议、旧 Office 兼容、发布/恢复顺序及目标验收以[阶段7解析部署与服务器验收计划](../../docs/implementation/phase-7-tool-runtime-plan.md#文件解析部署与服务器验收计划2026-年-10-月-6-日)为准；下列配置可供统一执行器部署使用，但本地测试不代替目标主机验收。任务工作目录和 Python／Shell 是后续经工具 Registry/权限检查的独立工作项，不通过该解析 Socket 接受任意命令。

先按[阶段7执行环境说明](../../docs/implementation/phase-7-tool-runtime-plan.md#linux-云服务器兼容与验收)在目标架构构建、检查镜像。不要把本机 ARM64 测试镜像直接当作 AMD64 云主机的验收结果。

`nexuspilot-file-runtime.service` 是 systemd 模板：

- 预先创建 `nexus-file-runtime` 用户与组，并授予其受控 Docker 访问权；该权限很高，只给管理员管理的执行器，不给 API/Web。
- 安装代码到模板指定的 `/opt/nexuspilot/current`，或明确修改服务路径。
- 在 `/etc/nexuspilot/file-runtime.env` 设置经过检查的 `FILE_RUNTIME_IMAGE`；服务会固定其实际 image ID。
- 启动后确认 `/run/nexuspilot-file-runtime` 为该用户拥有、0750，Socket 为组可连接。把组的数字 GID 写到私有 `.env` 的 `FILE_RUNTIME_GID`。
- 该服务在崩溃后不盲目重启或删除 Socket；检查是否仍有活进程再处理。API 只挂载受限 Socket 目录，不挂 Docker Socket。

```bash
docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml \
  -f deployments/compose/docker-compose.office.yml up -d api
```

未验证执行器前不宣称 PDF/Office 可用；默认配置不挂该目录。先检查兼容两种协议的执行器，再更新 API；新版 PDF 不再使用 API 容器内沙箱。

2026-10-11 可用性检查要求：先更新宿主执行器到支持 `file_capabilities.v1` 的版本，再更新 API/Web。旧执行器仍能接受原解析协议，但新版 API 无法确认其可用性时会拒绝新的 Office/PDF 上传；旧解析结果仍可经文件工具读取。通过登录后的 `/api/v1/files/capabilities`（浏览器为 `/api/nexus/files/capabilities`）确认 `executor_status=available`，再验收实际文件上传及 Agent“读取附件”。此接口不返回 Socket 路径、镜像 ID 或凭据，也不以探测成功代替真实解析验收。本次仅完成本地代码及测试，服务器未执行这些更新。

## 6. 发布验收、备份与更新

按[首次上线清单](../../docs/implementation/initial-release-checklist.md#发布验收清单)验收，重点包括：匿名数据请求拒绝、A/B 双向隔离、所有 JSON 变体、上传与删除、模型 SSE、登录退出、服务端密钥不进入浏览器、外部看不到后台端口。Web 页面返回 200 或服务健康不等于这些检查已通过。

备份和恢复需同时包含数据库、MinIO 对象及必要配置，存放到主机之外并加密。准备阶段未配置你的远程备份目的地或自动续期系统。首轮可在维护窗口暂停应用写入和清理任务，完成数据库一致性备份及对应对象副本；在新的隔离环境恢复并验证后再开放用户。

更新时先备份和保留旧镜像，再执行兼容迁移、启动新应用。不要将默认启动命令改成每次自动迁移，不执行 `down -v`。回退应用前确认新旧数据库兼容；不自动降级 schema 或删除对象。

文件精确清理命令可由受控定时任务执行：

```bash
docker compose --env-file deployments/production/runtime/.env \
  -f deployments/compose/docker-compose.production.yml exec -T api \
  python -m nexuspilot_api.services.user_file_cleanup --limit 100
```

定时调度、告警接收、外部备份和证书续期仍需结合服务器确定。当前同步模型／工作流请求不能在重启后自动恢复，更新前需处理运行中的请求。
