# Docker 部署验证

本目录的测试程序不包含在生产镜像中。下方本地预验证使用 `docker-compose.fixture.yml` 显式挂载程序并提供内部模拟 Provider，所有 Provider 出站网络改为内部网络，避免调用真实模型。末节 `check_release.py` 则是明确授权后的目标服务器验收，会调用真实模型；不要混用两者。

测试对象为实际生产 Compose、MySQL、MinIO、非 root API/Web 和 HTTPS Nginx。模拟 Provider 只验证 HTTP 协议、SSE 转发及模型请求是否确实包含提取的文件文字，不证明真实模型质量或供应商连通性。

## 复现顺序

1. 使用 `mktemp -d /tmp/nexus-preverify.XXXXXX` 创建新的私有工作目录。用生产 `initialize.py` 生成其 `config/`，Origin 设为 `https://chat.localhost:19443`，证书目录设为该工作目录下的 `tls/`。
2. 在生成的 `.env` 中将 `BIND_ADDRESS` 改为 `127.0.0.1`、`HTTP_PORT` 改为 `19480`；HTTPS 端口保持 `19443`。为 `chat.localhost` 生成仅供本次测试的证书和私钥，不修改系统信任设置。客户端通过 `--cacert` 验证证书，不使用 `--insecure`。
3. 每条 Compose 命令同时使用生产 Compose 和本目录 fixture overlay，并通过 `-p nexus-preverify-唯一后缀` 选定一个全新的测试项目。先构建 `mysql api web`，再启动 `mysql minio fixture-provider` 并等待健康；执行 `migrate` 和 `storage-init`。
4. 以 `run --rm -T --no-deps api python /verification/seed_fixture.py` 运行夹具，从 stdin 传入 `{"password":"local-preverify-only-password"}`。脚本只接受尚无用户的库，创建两个测试账号，并将 DeepSeek 地址指向内部模拟服务。此密码只属于合成测试账号，不能用于正式用户。
5. 启动 `api web nginx` 并等待健康，再执行下面的第一阶段检查。

```bash
# VERIFY_DIR 为刚才创建的私有工作目录；命令从仓库根目录执行。
python3 deployments/verification/check_https.py "$VERIFY_DIR" before
```

该脚本保存 HTTP 客户端 Cookie、会话标识及非敏感结果到私有目录。它执行与当前前端一致的流程：模型完成后显式调用消息保存接口。仅调用 Responses 并不会自动完成前端的 Assistant 消息保存步骤。

6. 对**同一测试项目**执行 Compose `down`（不带 `--volumes`），再 `up -d --wait mysql minio fixture-provider api web nginx`。不要重新生成配置或重新 seed。执行：

```bash
python3 deployments/verification/check_https.py "$VERIFY_DIR" after
```

此步骤验证 Cookie 和已保存数据跨容器重建，而不是工作流执行中断恢复。若隔了超过登录闲置期限再继续，应先验证旧 Cookie 被拒绝、重新登录，再进行重建；不能把正常会话过期记成数据丢失。

7. `check_internal.py` 在该 API 容器内运行，以 stdin 接收测试 B 的 Cookie/CSRF 及 A 的 session_id，检查直接 API 和资源权限。只输出状态，不输出凭据。`check_https.Client.cookies()` 可从私有 Cookie jar 提取测试值；不要将它们放进命令行参数或日志。
8. 单独运行 `manage_runtime.py check --image nexuspilot-file-runtime:unified-local` 可验证新版文件镜像；这不代表生产 API 已连接 PDF/Office Socket。新版 API 不再包含本地 PDF 沙箱，按下节单独验证 Socket 与文件 Context。
9. 保存去除秘密的验证报告后，对该测试项目执行 `down --volumes`，删除它的临时配置、Cookie、证书和测试数据。该删除仅适用于自己创建的一次性项目；不对实际开发／生产项目执行。

本次结果见[本地部署预验证报告](../../docs/implementation/local-docker-preverification.md)。

## 本地统一文件解析验收

2026-10-07 按用户要求只在本地开发，不连接或更新服务器。以下命令不调用真实 Provider，也不读取开发数据库或用户文件。需要 macOS Docker Desktop 或 Linux Docker Engine、API `.venv`，以及用于**合成样本生成**的独立开发 Python（预装 reportlab、python-docx、openpyxl、python-pptx）。这些样本依赖不装入 API；执行任务时不运行 pip。

```bash
python3 deployments/file-runtime/manage_runtime.py build --image nexuspilot-file-runtime:unified-local
python3 deployments/file-runtime/manage_runtime.py check --image nexuspilot-file-runtime:unified-local

# 使用已包含上述样本生成依赖的 Python；目标目录必须不存在，避免覆盖。
python3 deployments/verification/create_file_fixtures.py /tmp/nexus-synthetic-file-fixtures

NEXUSPILOT_TEST_FILE_RUNTIME=1 \
FILE_RUNTIME_TEST_IMAGE=nexuspilot-file-runtime:unified-local \
NEXUSPILOT_TEST_FILE_FIXTURES=/tmp/nexus-synthetic-file-fixtures \
apps/api/.venv/bin/pytest -c apps/api/pyproject.toml \
  apps/api/tests/test_office_runtime_executor.py \
  apps/api/tests/test_file_runtime_context.py -q
```

镜像检查实际运行非 root、无网络、只读根文件系统、资源受限的临时 Linux 容器。测试启动自己的本地 Socket，结束后关闭；只删除自己创建的解析容器，不改用户 `.env`，不替换默认 `:local` 镜像。

`test_file_runtime_context.py` 检查 PDF/DOCX/XLSX/PPTX 的真实提取、HTTP 上传/下载、两种模式首轮和后续轮的实际模型输入、解析不重复执行、Context 来源/版本及重新读取；另检查中文 PDF、空白第二页和加密 PDF 上传失败。该部分使用 SQLite 和内存对象存储、固定 Provider 响应，**不是 MySQL/MinIO、真实 Provider 或浏览器验收**。只有显式开启环境变量才运行，默认跳过不计作通过。真实存储验证仍使用 `test_user_file_infrastructure.py` 的独立 MySQL/MinIO 配置；不得连接开发或生产库代替。

本机应用需要长期使用解析服务时，先通过上述检查，再按[Socket 启动说明](../../docs/implementation/phase-7-tool-runtime-plan.md#office-执行器启动与权限)显式启动执行器，并将该服务路径配置到 API。单次测试结束不会留下常驻服务；新 API 对 PDF/Office 缺少 Socket 会明确失败，不回退宿主解析。

## 存储镜像专项验证


`check_storage_image.py` 用于更换 MinIO 镜像后的专项验证。需要 Docker Engine、已下载的目标镜像和项目 API 虚拟环境依赖；不需要数据库、Provider Key 或完整 Web 服务。从仓库根目录运行：

```bash
apps/api/.venv/bin/python deployments/verification/check_storage_image.py \
  --image coollabsio/minio:RELEASE.2025-10-15T17-29-55Z@sha256:69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9
```

脚本创建随机命名的容器和数据卷，端口只绑定本机回环随机端口；临时凭据文件为 0600，不读取开发 `.env`。它复用生产健康检查脚本和桶／账号初始化脚本，通过实际 API `ObjectStorage` 验证读写、权限、保留卷重建和删除。结束时删除自己创建的容器、卷及临时凭据，输出不含秘密的 JSON 结果。异常时返回失败和代码位置，不打印可能包含凭据的服务响应。

这不是目标服务器的完整应用验收，也不测试原有数据升级。已有开发／生产存储不能挂入此脚本。

## 已授权服务器的 HTTPS 验收

`check_release.py` 需要 API 虚拟环境中的 `httpx`、已迁移且启用认证的 HTTPS 实例、已登记模型和两个独立预置测试账号。它创建合成会话与文本文件，执行两次最多 64 输出 token、关闭思考的真实模型调用；只有获得部署／测试和 Provider 费用授权后才能执行。不读取正式用户会话。

在 0700 私有目录中准备服务器公开证书 `fullchain.pem`，以及 `deploy-check-a-login.json`、`deploy-check-b-login.json`；每个账号文件包含 `email`、`password`，设为 0600，不加入仓库。执行：

```bash
apps/api/.venv/bin/python deployments/verification/check_release.py \
  --origin https://YOUR_HOST:8443 --directory /absolute/private-check-directory \
  --model YOUR_REGISTERED_MODEL
```

脚本校验 TLS、匿名拒绝、Cookie 属性、CSRF、A/B 会话和文件隔离、文本上传／下载、文件追问上下文、真实 SSE 和退出后旧令牌拒绝。输出 `release-result.json` 不包含秘密；`release-browser-state.json` 含有效登录 Cookie，必须按凭据保护。`release-state.json` 记录自己创建的资源供重建检查和清理。测试失败时退出非零，不将已完成的局部检查当作整体验收。

脚本不自动重启生产服务，也不删除数据。仅在没有用户正在运行任务的已授权窗口重建 API/Web，再核对这些资源和 Cookie；验收结束撤销专用账号及会话，删除自己创建的上传文件和临时凭据。不要对正式账号执行该清理。目标服务器本次执行记录见[2026-10-04 部署报告](../../docs/implementation/server-deployment-20261004.md)。
