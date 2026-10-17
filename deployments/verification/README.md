# Docker 部署验证

## Strata 真实附件调用（2026-10-18）

`check_live_strata_file.py` 对本机 MySQL/MinIO 与真实 Strata 执行一次快速回复。仅上传随机核验编号的合成 TXT，不更改模型参数、不模拟工具、不自动重复付费测试。使用下文既有本地隔离服务与 `apps/api/.env` 中的私有 Key：

```bash
PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python deployments/verification/check_live_strata_file.py --output /tmp/strata-file-NEW.json
```

在 `9d90baf` 业务代码上通过，Run `07bd95b7-bf8b-4893-8c73-f77f5db2c4be`：上传到最终回答重新读取耗时 12.628 秒；模型真实调用 `read_file_content` 并返回附件核验编号和设备型号。两次模型请求输入 4,352、输出 283，共 4,635 token，其中缓存命中 1,605 token；费用字段为空，不能当作零费用。初始上下文未包含文件全文，实际工具结果含核验编号；Assistant SQL 正文为空、有对象引用，从 MinIO 重新读取正文一致。

这是快速回复 TXT 工具与存储验证，不是 Agent、PDF 或全部格式重新验收。原始脱敏报告保存于执行机器 `/tmp/strata-file-20261018-1.json`。发布前另执行前端测试 142 项通过、TypeScript 检查通过。

## 本地对话正文对象存储验证（2026-10-18）

`serve_body_storage_fixture.py` 只绑定本机 `18005/3305`，复用本目录下文已有隔离 MySQL `13306`、MinIO `19000` 和已登记模型目录。先对该隔离库执行迁移到 head；它不会自行迁移、清空数据库或部署服务器。所有 Provider 实现替换为脚本响应，页面模型名称仅复用目录；没有真实 DeepSeek/Strata 请求，也不以脚本 token 计数说明真实计费。

```bash
PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python deployments/verification/serve_body_storage_fixture.py
```

用浏览器打开 `http://127.0.0.1:3305`，新建会话，依次发送以 `BODY_USER_ONE`、`BODY_USER_TWO` 开头的两轮快速回复；刷新后确认两轮正文。切换 Agent 发送第三轮，等待完成后再次刷新，检查最终答案和执行摘要。脚本 Provider 按实际允许的工作角色产生计划，不调用网络模型。Ctrl-C 停止本次 API/Web，保留 MySQL/MinIO 验证数据供核对。再次启动可检查服务重启后的历史读取。

完成后使用同一脚本只读检查（传入实际会话 ID）：

```bash
PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python deployments/verification/serve_body_storage_fixture.py --inspect-session SESSION_ID
```

检查读取实际 SQL 占位、逐条校验 MinIO 对象，以及第二个请求的 `effective_messages` 是否包含第一轮双方正文；必须存在完成的 Agent 工作流。没有生成记录的表报告 `not_exercised`，不记作通过。只输出 ID、计数和状态，不导出消息/凭据。实现记录与实测范围见[阶段2正文对象化](../../docs/implementation/phase-2-llm-core-capabilities.md#完整对话正文对象化)。

跨用户检查复用 `test_resource_authorization.py`：设置 `NEXUSPILOT_TEST_AUTH_MYSQL_URL` 为本机 `authz_audit` 测试管理库（测试账号需有建库权限），以及 `NEXUSPILOT_TEST_AUTH_MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY`。每项测试创建随机后缀数据库并仅删除自己的库，MinIO 使用 `authorization-audit` 桶；不得配置生产凭据。此模式通过实际登录 Cookie 和实际应用 Session 读写 MinIO，不只替换文件上传存储。

```bash
apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/test_resource_authorization.py -q
```

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

## 本地真实 DeepSeek 文件工具检查

`check_live_file_tools.py` 是需要明确费用授权的真实模型检查，不是默认测试，也不部署服务器。使用独立 Compose 项目 `nexus-deepseek-tools-20261012`，叠加 `deployments/verification/docker-compose.deepseek.yml`，只启动 `mysql minio`；环境变量分别设置 `NEXUSPILOT_MYSQL_HOST_PORT=127.0.0.1:13306`、`NEXUSPILOT_MINIO_API_HOST_PORT=127.0.0.1:19000`、`NEXUSPILOT_MINIO_CONSOLE_HOST_PORT=127.0.0.1:19001`。现有开发卷不挂入该项目。

1. 在 `apps/api` 目录用 `NEXUSPILOT_DATABASE_URL=mysql+aiomysql://nexuspilot:nexuspilot@127.0.0.1:13306/nexuspilot .venv/bin/alembic upgrade head` 迁移新库。只允许用于这个新建本地项目，示例凭据不能用于上线。
2. 运行检查脚本的 `--seed`：同时设置上述数据库 URL、`NEXUSPILOT_MINIO_ENDPOINT=127.0.0.1:19000`。脚本拒绝含用户或模型的库；保留迁移预置的 DeepSeek 地址，仅创建本地已配置模型的验证记录。Context 数值是本次输入预算，不作为模型真实最大窗口的声明。
3. 按前文生成合成样本。启动已构建的专用文件执行器，Socket 例如 `/tmp/nexus-ds-live-20261012/executor.sock`。
4. 用当前 API 虚拟环境启动 `uvicorn nexuspilot_api.main:app --app-dir apps/api/src --host 127.0.0.1 --port 18000`，设置独立数据库 URL、MinIO 地址、Compose 中的测试存储凭据、独立 bucket、`NEXUSPILOT_FILE_RUNTIME_SOCKET`、`NEXUSPILOT_MODEL_MAX_RETRIES=0`。DeepSeek Key 继续从 `apps/api/.env` 读取，不复制进脚本/日志。保持 development、登录关闭，仅本机访问。
5. 使用同样的数据库 URL 和 MinIO 地址分别执行 `apps/api/.venv/bin/python deployments/verification/check_live_file_tools.py --mode quick --fixtures /absolute/synthetic-fixtures --output /absolute/new-quick-report.json` 和 `--mode agent`（使用新的输出路径）。每种模式计划首轮 DOCX、续聊 PDF；真实调用失败时停止该模式，退出非零，保留已经完成的 SQL/MinIO 记录和无秘密报告。请求明确要求使用文件读取工具核对原文，但不传 tools 或 execution_profile；验收检查实际工具调用、标记、Context 和最终答案。不是单凭回答包含标记判为通过。
6. 验收后停止自己启动的 API、执行器及该 Compose 项目；使用 `stop` 保留测试卷供调查，不执行针对现有开发项目的删除。需要重做种子时应创建另一个新项目，不清空已有库。报告不包含 Key、Cookie 或私有推理正文。

2026-10-12 首次结果为失败：两模式自动文件工具均开始执行，但共享适配器拒绝消息级 DeepSeek 私有续接，最终答案和第二轮测试未完成。同日修复并复测后，快速回复首轮 DOCX、续聊新增 PDF 均通过；Agent 首轮 DOCX 通过，续聊 PDF 在 Worker 请求时收到 DeepSeek `Insufficient Balance`，当次停止付费检查。用户恢复余额后，使用新会话重跑 Agent 两轮，首轮 DOCX、续聊新增 PDF 均有实际读取记录和已保存最终回答，程序退出码为 0。`quick-2.json`、`agent-2.json` 及补验 `agent-3.json` 保留于 `/tmp/nexus-deepseek-tools-results-20261012/`，原失败记录未覆盖。Agent 审核文本仍未认可已成功执行的工具证据，未在本次修复；本次仅确认读取和生成保存行为，不是浏览器或完整审核质量验收。定位、测试范围和 Run ID 见[工具主计划](../../docs/implementation/phase-7-tool-runtime-plan.md#首批附件工具实现与运行2026-10-09)。

## 本地 Worker 引用与 Reviewer 补读检查

`check_live_worker_evidence.py` 需要明确费用授权，复用上述独立测试 MySQL/MinIO 及已登记 DeepSeek 模型，自行启动并停止 `127.0.0.1:18000` API。不得同时运行其他占用该端口的 API。脚本创建合成两段 TXT，验证 Worker 首段引用、Handoff 保存、MinIO 解析哈希、持久 Reviewer 请求的原文及实际补读第二段。它不迁移/清空数据库、不启动远程服务，失败不自动重跑付费工作流。

```bash
apps/api/.venv/bin/python deployments/verification/check_live_worker_evidence.py \
  --output /absolute/private-new-directory/live-evidence.json
```

输出路径必须不存在。报告仅保存合成任务业务内容及关联 ID，不保存 Key 或私有推理；目录按私有目录创建。实际 2026-10-13 结果及范围见[工具主计划](../../docs/implementation/phase-7-tool-runtime-plan.md#本地真实-deepseekmysqlminio-联调2026-10-13)。测试后停止自己启动的独立容器，保留卷供只读调查。

## 已授权服务器的 HTTPS 验收（独立流程）

`check_release.py` 需要 API 虚拟环境中的 `httpx`、已迁移且启用认证的 HTTPS 实例、已登记模型和两个独立预置测试账号。它创建合成会话与文本文件，执行两次最多 64 输出 token、关闭思考的真实模型调用；只有获得部署／测试和 Provider 费用授权后才能执行。不读取正式用户会话。

在 0700 私有目录中准备服务器公开证书 `fullchain.pem`，以及 `deploy-check-a-login.json`、`deploy-check-b-login.json`；每个账号文件包含 `email`、`password`，设为 0600，不加入仓库。执行：

```bash
apps/api/.venv/bin/python deployments/verification/check_release.py \
  --origin https://YOUR_HOST:8443 --directory /absolute/private-check-directory \
  --model YOUR_REGISTERED_MODEL
```

脚本校验 TLS、匿名拒绝、Cookie 属性、CSRF、A/B 会话和文件隔离、文本上传／下载、文件追问上下文、真实 SSE 和退出后旧令牌拒绝。输出 `release-result.json` 不包含秘密；`release-browser-state.json` 含有效登录 Cookie，必须按凭据保护。`release-state.json` 记录自己创建的资源供重建检查和清理。测试失败时退出非零，不将已完成的局部检查当作整体验收。

脚本不自动重启生产服务，也不删除数据。仅在没有用户正在运行任务的已授权窗口重建 API/Web，再核对这些资源和 Cookie；验收结束撤销专用账号及会话，删除自己创建的上传文件和临时凭据。不要对正式账号执行该清理。目标服务器本次执行记录见[2026-10-04 部署报告](../../docs/implementation/server-deployment-20261004.md)。
