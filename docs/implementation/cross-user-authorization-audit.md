# 后端跨用户访问测试报告

更新日期：2026 年 9 月 30 日。原审计与修复记录始于 2026 年 9 月 17 日；修复前基于 `7603b3a`，后续检查针对未提交工作区。

## 当前结果：JSON 类型绕过已修复，目标生产环境待验收

2026-09-30 `codex/deployment` 已修复以下媒体类型缺口：按 FastAPI 的解析规则处理缺少头、大小写、`application/*+json` 和参数，并检查路由实际是否接受 JSON 业务正文；原始上传文件字节不被误当成接口资源参数。新增 6 类 JSON 的双向读取／写入拒绝和本人请求成功检查，以及 3 类文件上传内容回归，均通过。生产启动现在拒绝示例／过短服务秘密、缺失签名秘密和示例数据库密码；配置生成器负责生成独立随机凭据。

生产 Compose 的本机 Linux ARM64 验证已通过 HTTPS 登录、跨用户会话拒绝、MinIO 文件上传下载删除和退出后拒绝；只有 Nginx 发布端口，API/Web 使用非 root。目标服务器、正式证书与外部网络验收仍未执行；默认 API 容器的 PDF 沙箱实测不可用。详情见[生产部署运行说明](../../deployments/production/README.md)。本次修复前的请求变体结果保留如下。

2026 年 9 月 26 日重新检查发现：`resource_access.py` 当时只在 Content-Type 字符串以 `application/json` 开头时验证正文资源归属，而 FastAPI 还接受其他 JSON 写法。以下是修复前真实密码登录、临时 SQLite、直接 ASGI 调用中的结果，仅使用合成账户和消息，不接触实际聊天数据：

| B 请求 A 的 Context Preview | HTTP 状态 | 响应包含 A 的聊天文字 |
|---|---|---|
| `application/json` | 404 | 否 |
| `application/vnd.api+json` | 201 | 是 |
| 缺少 Content-Type | 201 | 是 |
| `Application/JSON` | 201 | 是 |

前提：B 有有效登录 Cookie、Origin、CSRF 和部署 Key；这不是匿名公网访问复现，也不是已证明浏览器能拿到服务 Key。它证明后端权限检查可以被业务接口接受的请求格式绕过，即使部署将 API 放在内网，该应用缺陷仍须修复。同期既有 24 项认证／授权测试通过，说明原测试缺少这些请求变体，不能继续据此宣称后端全资源隔离完成。

修复要求：授权与业务处理使用一致的请求解析规则，覆盖框架接受的全部 JSON 类型和缺失头行为；更稳妥的实现是在业务参数解析后，对解析得到的类型化资源引用执行权限检查。新增双向请求变体、读取／写入与副作用回归。未登录 401、其他用户拒绝、本人合法请求可用均须验证；如决定统一限制请求类型，必须在进入业务处理前明确拒绝，不得跳过鉴权后继续解析。

部署凭据检查还确认：生产 Settings 可以接受代码里的默认部署 Key、内部 Key 和数据库连接凭据。没有读取或披露实际 `.env`，不能推断现有运行环境在使用这些默认值。凭据更换和端口隔离由生产部署配置落实；生产启动拒绝已知默认值属于应用防误配置检查，任务归属见[首次上线部署规划](initial-release-checklist.md)。

## 2026 年 9 月 17 日修复与本地验证记录（历史范围）

FastAPI 现独立校验当前用户、写请求 CSRF、路径／查询／正文引用的 owner，列表在 SQL 中过滤并将 owner 绑定到 cursor；内部管理凭据不能升级浏览器身份。具体实现和兼容变化见[Phase 5 修复记录](phase-5-platform-identity-credentials.md#本次实现与调用方兼容说明)。

- 原 57 项分别以 A→B、B→A 运行，均为 0 失败；被拒绝修改未落库。
- `test_resource_policy.py` 核对全部 124 个业务方法的分类与缺身份拒绝，并检查全部内部方法的 Cookie 拒绝、全部本人资源写方法的 CSRF、SQL 列表、跨用户游标、嵌套引用、轮次重放、业务记录计数、撤销／停用及并发请求。
- 相同权限测试在独立 MySQL 和 MinIO 验证通过；原上传并发与额度测试另行通过。完整后端／模型测试 493 通过、8 跳过；前端 121 通过，类型检查、ESLint、Ruff 通过。
- Playwright 真实浏览器 A/B 登录及正常流式回复通过；B 的列表不出现 A，访问 A 的消息返回 404，旧链接不展示 A 的正文；使用模拟模型，无真实 Provider 费用。
- 无数据库迁移、无现有数据修改、无远程发布；目标 HTTPS／反向代理及部署网络验收仍未执行。不能将本地通过直接写成公网发布通过。

当前复现命令：

```bash
apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/test_resource_authorization.py apps/api/tests/test_resource_policy.py -q
```

上述测试已进入默认 pytest 发现范围。旧受信服务业务测试夹具不用于证明用户授权；新的 `resource_client` 使用真实授权依赖。MySQL 可选运行配置与独立数据库清理约束见 `apps/api/tests/resource_infrastructure.py`。逐项结果生成到 `output/security/cross-user-access-audit-a.json` 和 `cross-user-access-audit-b.json`。

## 修复前结论：不通过（历史记录）

当前 FastAPI **没有完成对话和业务数据的最终用户隔离**。实际登录为 B 的调用方，在同时携带有效部署 API Key 时，可以读取、修改 A 的部分资源。无登录 Cookie、仅有部署 API Key 时，也能读取 A 的消息和 Context。

这不是“公网匿名者不需要任何凭据即可访问”的结论：无部署 Key 的对照请求返回 401。当前浏览器经 Next.js BFF 时另有归属检查，本轮代理单元测试通过。因此已证明的是**后端依赖受信代理，不能独立执行全资源用户授权**；不能将该结果夸大为已经复现浏览器绕过代理攻击，也不能因代理检查存在就宣称后端多用户隔离完成。

公开多用户上线验收不通过；应先修复鉴权并复测。个人受限部署仍需独立核对网络和访问限制，不从本报告推断部署已安全。

## 环境与范围

- 启用真实登录开关，预置 A、B 两个合成账户，执行密码登录并分别确认 `/auth/me` 的实际用户 ID；不覆盖当前主体解析依赖。
- 直接通过 HTTPX ASGI 请求 FastAPI，绕过 BFF；业务数据库为新建临时 SQLite，开启外键。对象存储使用内存夹具，模型使用 FakeProvider/ScriptedAgentProvider。
- A 创建会话、消息、Run、Task、Attempt、旧 Artifact、Context、Memory、Knowledge、Project、Evaluation、Workflow 及新私有上传；再切换真实 Cookie 为 B 进行访问。写操作只影响测试数据。
- 盘点 OpenAPI 的 **113 个路径、132 个方法组合**；执行 **57 项代表性访问检查，16 项符合预期、41 项不符合预期**。57 项含正向对照、无登录及无 Key 对照，不等于 57 个独立漏洞。
- 最终 41 项失败均为返回 200/201 或列表包含 A 的记录。错误路径造成的 405 已纠正为实际列表接口；不把 405、422 或业务状态冲突当作已证明的越权成功。
- 没有穷尽每个路由方法、每个角色、分页游标、流式断线和并发撤权组合；内部 Memory 多层版本接口、Provider 续接与完整生产授权矩阵仍待专项验收。
- 没有读取开发数据库、没有真实模型费用、没有执行生产迁移或远程部署。SQLite 结果证明应用层授权缺失，不替代真实 MySQL 并发和公网部署测试。

## 实测结果

| 对象与动作 | B 访问 A 的实际结果 | 判断 |
|---|---|---|
| 用户详情及更新 | GET/PATCH `/users/{A}` 返回 200 | 不通过 |
| 会话详情、列表和指定 A 的列表过滤 | 返回 200，列表包含 A 会话 | 不通过 |
| 消息列表、最新消息、最新 Run | 返回 200 | 不通过 |
| 修改 A 会话标题 | PATCH 返回 200，A 回读确认标题被修改 | 不通过 |
| 向 A 会话插入消息 | POST 返回 201，A 回读确认消息已保存 | 不通过 |
| B 冒填 A 的 user_id 提交轮次 | POST `/sessions/{A_session}/turns` 返回 201，A 回读确认轮次消息已保存 | 不通过 |
| 无私有上传的 Run、Task、Attempt、列表 | 返回 200；B 能创建 A 的 Task（201）、取消 A 的 pending Run（200） | 不通过 |
| 旧 Artifact 元数据、列表和正文 | 返回 200，包括下载正文 | 不通过 |
| Context 快照、证据及重建 | GET 返回 200；冒填 A 的 user_id 创建 Preview 返回 201 | 不通过 |
| Workflow 摘要、完整结果、节点、事件 | 全部返回 200 | 不通过 |
| 以 A 的 Run 发起 Responses | 返回 200，使用模拟模型完成请求 | 不通过 |
| Memory 详情、Session Memory、删除 | 返回 200 | 不通过 |
| Knowledge 元数据读取、停用 | 返回 200 | 不通过 |
| Project、Evaluation 详情 | 返回 200 | 不通过 |
| 新私有文件详情、下载、PUT、DELETE、旧 Artifact 路径读取 | 返回 404；A 文件仍可读取 | 本次检查通过 |
| 含私有上传的 Run 详情与取消 | 返回 404 | 本次检查通过，但不覆盖会话／全局列表泄露 |
| 新私有文件列表 | 返回 200，但不包含 A 的文件 | 本次检查通过 |
| B 撤销 A 登录会话 | 返回 404 | 本次检查通过 |
| 缺少内部 Key 的 Project 修改、模型目录内部写入 | 返回 401 | 本次检查通过，这是内部服务授权而非用户角色隔离 |
| 无 Cookie、只有部署 Key | `/auth/me` 与新文件读取返回 401；A 消息与 Context 返回 200 | 旧资源认证不完整 |
| 无 Cookie 且无部署 Key | 会话与文件请求返回 401 | 服务 Key 检查通过 |

新私有文件保护并不保护整个会话：同一测试中，A 上传文件后的会话消息仍能被 B 读取。不能据文件接口 404 推断消息、附件名称或历史文本已统一隔离。

## 原因与应修复位置

1. `apps/api/src/nexuspilot_api/routers/__init__.py:33` 全局主要依赖部署级 Key；`enforce_private_file_references` 只在发现新私有文件引用时检查当前用户，不是通用授权。
2. `services/session_service.py:78` 按 Session ID 取记录，没有当前用户参数；消息查询、更新等调用同样没有统一当前主体约束。
3. `services/session_service.py:208` 的轮次归属校验针对请求中的 `payload.user_id` 与资源关系。检查数据关系正确，不等于确认调用者就是该用户；B 可直接填 A 的 ID。
4. Run、Task、Attempt、Artifact、Context、Workflow、Memory、Knowledge、Project、Evaluation 等入口需要按统一规则区分本人资源、平台共享只读资源、内部管理动作，而不是逐个增加只针对文件的例外。
5. Next.js BFF 已有会话、消息、Run、Workflow 归属检查；这些检查不能取代 FastAPI 授权。部署 Key 必须留在服务端，内部服务也应受限访问。

## 验证命令与证据

```bash
apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/security_audits/cross_user_access.py -q -s --tb=no
apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/test_authentication.py apps/api/tests/test_user_files.py -q
```

- 第一条：审计断言失败，报告 57 项中 41 项不满足隔离要求；测试保留正确的拒绝／过滤要求，不改期望去适配漏洞。
- 第二条：25 项通过，说明现有登录及文件检查通过不代表其他资源已隔离。
- 在 `apps/web` 执行 `./node_modules/.bin/tsx --test tests/proxy-route.test.ts`：27 项通过；使用模拟上游，未执行真实浏览器攻击或反向代理部署验收。
- 新测试 Ruff 检查通过。显式审计文件不匹配默认 `test_*.py` 文件名，需单独运行；目前没有自动发布阻断配置，不能声称 CI 会自动拒绝发布。
- 逐项状态及路由清单由测试生成至 `output/security/cross-user-access.json`，仅含合成资源 ID、状态和布尔结论，不保存 Cookie、部署 Key 或响应正文。

以上是修复前的调查记录；当时未修改业务实现。后续修复及当前验证结果以文首为准。旧审计文件已迁入默认发现的 `test_resource_authorization.py`；保留旧命令作为历史记录，不作为当前执行入口。
