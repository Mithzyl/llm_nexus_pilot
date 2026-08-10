# 阶段10：平台身份、授权与凭据管理规划

**文档日期：** 2026 年 8 月 7 日
**文档状态：** 规划中，尚未实现
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

> 本文只固定公共接口、数据边界、阶段依赖和验收门禁。本轮不创建数据库表、迁移、Router、Service、前端页面或密钥加密代码。

## 总体目标

- 系统最终需要具备的能力：识别当前登录用户，为浏览器和程序调用分别建立可撤销身份，并让用户安全管理 NexusPilot 个人 API Key 与模型供应商凭据。
- 系统最终需要具备的能力：所有 User、Session、Run、Task、Attempt、Artifact、Memory 和后续 Knowledge/MCP 资源都从已认证主体派生归属，不再信任请求正文或查询参数自行声明的 `user_id`。
- 本阶段明确不处理：公开注册、组织、多租户 Membership、企业单点登录、模型供应商计费代理、密钥共享、管理员控制台和供应商账户代购。
- 本阶段不回退阶段1、阶段2的完成状态；它是最终用户公开访问和用户自带供应商密钥的新增能力。

## 当前事实

- 当前 `users` 表及 `User` API 表示业务资源所有者，不包含登录身份、密码、登录会话或授权声明。
- 当前公共 API 使用部署级 `X-API-Key`，内部接口另加部署级 `X-Internal-API-Key`。它们只能证明调用方是受信服务，不能识别最终用户。
- 当前请求中的 `user_id` 由调用方传入。Service 会校验资源关系，但不能证明调用方就是该用户。
- 当前模型供应商密钥来自服务端环境变量，并在应用启动时注册 Provider。数据库没有用户级供应商凭据。
- 阶段2～6可以在内部开发模式下使用测试 fixture、受控 seed、假 Provider 和环境变量完成验证，不应为了测试增加绕过认证的公开接口。
- 待确认事项：生产部署是否只允许管理员预置账户；是否需要公开注册；密码找回邮件由哪个服务发送；生产密钥加密使用哪一种密钥管理服务。

## 路线图调整说明

- 原路线图的问题：只说明“公开前需要最终用户认证”，没有给出独立阶段、接口合同、凭据存储边界或阶段2～6的测试身份方案。
- 新发现的能力缺口：平台登录、当前用户识别、个人 API Key、模型供应商凭据和撤销审计构成一个可独立交付的安全边界，不适合作为 RabbitMQ、Agent 或前端的附带工作项。
- 该缺口属于新阶段：新增阶段10，保持既有阶段1～9编号不变。阶段9内部开发界面不依赖阶段10；任何最终用户公开发布必须同时通过阶段10门禁。
- 保持不变的已完成阶段：阶段1和阶段2仍按当时的受信调用方边界保持“已完成”。
- 需要调整的未开始阶段：阶段3～6补充测试身份和凭据引用约束，但不把完整登录 API 作为其完成条件。

## 核心身份模型

### 当前认证主体

所有最终用户路由统一注入“已认证主体”（代码建议名称：`AuthenticatedPrincipal`）：

```text
AuthenticatedPrincipal
├── user_id
├── authentication_method     # browser_session | personal_api_key
├── login_session_id          # 浏览器登录时存在
├── user_api_key_id           # 个人 API Key 调用时存在
└── scopes                    # 本次请求的授权范围
```

- 浏览器使用带 `HttpOnly`、`Secure` 和适当 `SameSite` 属性的不透明 Session Cookie；服务端只保存会话令牌的不可逆校验值。
- 程序调用使用 `Authorization: Bearer np_...`；不得把用户个人密钥继续放入 `X-API-Key`。
- 同一请求同时携带 Cookie 与 Bearer 身份时默认拒绝，避免身份优先级含糊。
- Router 只接收 `AuthenticatedPrincipal`；Service 从其中取得 `user_id`，创建资源时忽略或拒绝正文中的 owner 字段。
- 当前部署级 `X-API-Key` 保留给内部迁移期受信服务，不能转换为任意最终用户主体；内部管理接口仍需独立内部凭据或后续服务身份。
- `/me/api-keys`、`/me/provider-credentials`、密码修改和会话管理属于敏感操作，第一版只允许近期重新认证的浏览器会话调用。个人 API Key 即使具有普通模型调用 scope，也不能创建更多密钥、读取凭据元数据或轮换供应商 secret。
- 当前 `/api/v1` Router 整体依赖部署级 `X-API-Key`。实施时必须把登录入口从该全局依赖中分离，并为公开认证路由、最终用户路由和内部服务路由分别装配依赖；不能要求未登录用户先持有部署密钥才能调用 `/auth/login`。

### 持久化对象

建议在实施时增加以下职责明确的表；字段需在编码前通过迁移设计复核：

| 对象 | 作用 | 敏感数据规则 |
|---|---|---|
| `user_auth_identities` | 将登录邮箱或后续 OpenID Connect（OIDC）主体绑定到现有 `users.user_id` | 邮箱规范化后唯一；不保存第三方访问令牌 |
| `user_password_credentials` | 保存本地登录密码校验材料 | 只保存 Argon2id 哈希、参数版本和变更时间 |
| `user_login_sessions` | 保存浏览器登录会话、到期、最后使用和撤销事实 | 只保存会话令牌哈希，不保存 Cookie 原文 |
| `user_api_keys` | 保存 NexusPilot 个人 API Key 元数据、范围和撤销状态 | 只保存 key ID、前缀、不可逆校验值和末尾掩码 |
| `user_provider_credentials` | 保存用户自己的 OpenAI、DeepSeek、Anthropic、Gemini 等供应商凭据 | 保存信封加密密文、密钥版本、指纹和末尾掩码；不能保存明文 |
| `authentication_audit_events` | 保存登录、失败、创建、验证、轮换和撤销证据 | 不记录密码、完整令牌、完整供应商错误正文或凭据明文 |

现有 `users` 继续作为所有业务资源的所有者根，不再创建第二个含义相同的“账户用户”表。一个 User 可以有多个登录方式、多个登录会话、多个个人 API Key 和每家供应商的多个命名凭据。

## 接口优先级结论

### 第一组：当前用户身份的最小公共合同

```http
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
POST /api/v1/auth/sessions/revoke-all
```

| 接口 | 是否为公开用户 API 前置 | 合同与失败行为 |
|---|---|---|
| `POST /auth/login` | 必须 | 校验已预置账户的邮箱和密码，创建不透明会话并设置 Cookie；统一返回认证失败，不泄露账户是否存在；受速率限制和失败审计保护 |
| `POST /auth/logout` | 必须 | 撤销当前会话并清除 Cookie；重复退出保持幂等，不能撤销其他用户会话 |
| `GET /auth/me` | 必须 | 返回当前 `user_id`、显示信息、认证方式和公开 scopes；Cookie/Bearer 缺失或失效返回 401 |
| `POST /auth/sessions/revoke-all` | 必须 | 撤销该用户全部登录会话；默认包括当前会话，响应后浏览器必须回到未登录状态 |

只有这四个接口仍不足以让本地密码登录达到公开可运维状态。实施本地密码认证时还必须规划：

```http
GET    /api/v1/auth/sessions
DELETE /api/v1/auth/sessions/{login_session_id}
POST   /api/v1/auth/password/change
POST   /api/v1/auth/password/forgot
POST   /api/v1/auth/password/reset
```

- 会话列表与单会话撤销用于识别和关闭异常设备，不能只提供“一次撤销全部”。
- 已登录密码变更必须验证当前密码，并撤销其他会话。
- 找回与重置必须使用短时、一次性、不可逆存储的令牌；响应不能泄露邮箱是否存在。
- 初始阶段不提供公开注册。开发环境通过测试 fixture，私有部署通过受控 bootstrap 命令预置首个账户。若要求公开注册或邀请，再单独增加验证、滥用防护和管理员规则，不能复用 `POST /users` 直接创建可登录账户。
- 采用服务端不透明 Session 后不需要公开 `refresh` 接口；服务端可以在绝对过期时间内受控滚动空闲到期时间。

### 第二组：NexusPilot 个人 API Key

```http
GET    /api/v1/me/api-keys
POST   /api/v1/me/api-keys
DELETE /api/v1/me/api-keys/{api_key_id}
```

| 接口 | 是否为阶段2～6前置 | 合同与失败行为 |
|---|---|---|
| `GET /me/api-keys` | 否 | 只返回名称、key ID、前缀/末尾掩码、scopes、到期、最后使用和撤销时间，不返回密钥 |
| `POST /me/api-keys` | 否；程序化最终用户访问前必须 | 接收名称、受允许 scopes 和可选到期时间；密钥原文只在成功响应中展示一次 |
| `DELETE /me/api-keys/{api_key_id}` | 否；个人密钥启用前必须 | 语义为不可恢复撤销而非物理删除审计事实；重复撤销幂等；跨用户 ID 返回 404，避免资源枚举 |

建议密钥格式把可索引 `api_key_id` 与高熵 secret 分开。数据库使用服务端 pepper 的消息认证码或等价不可逆校验值，并采用常量时间比较；日志、追踪、错误、队列和数据库原始请求不得出现完整密钥。`last_used_at` 更新不能让每个请求都成为认证瓶颈，可以限频或异步聚合，但撤销检查必须实时生效。

### 第三组：模型供应商凭据

```http
GET    /api/v1/me/provider-credentials
POST   /api/v1/me/provider-credentials
PATCH  /api/v1/me/provider-credentials/{credential_id}
POST   /api/v1/me/provider-credentials/{credential_id}/verify
DELETE /api/v1/me/provider-credentials/{credential_id}
```

| 接口 | 是否为阶段2～6前置 | 合同与失败行为 |
|---|---|---|
| `GET /me/provider-credentials` | 否 | 返回 provider、名称、状态、末尾掩码、创建/更新/验证时间和允许的模型范围；不返回密文、明文或可复原定位信息 |
| `POST /me/provider-credentials` | 否；用户自带密钥前必须 | 接收 provider、显示名称和一次明文 secret；先做格式/大小检查，再信封加密；创建后状态为 `pending_verification` |
| `PATCH /me/provider-credentials/{credential_id}` | 否；轮换能力启用前必须 | 允许修改名称、允许模型和轮换 secret；secret 轮换生成新版本并使旧版本不能再被新任务解析；使用版本条件防止并发覆盖 |
| `POST /me/provider-credentials/{credential_id}/verify` | 否；用户凭据进入模型调用链前必须 | 在数据库事务外执行有超时的最小供应商校验；按凭据版本写验证结果，旧请求不能覆盖新版本；不得返回供应商原始敏感错误 |
| `DELETE /me/provider-credentials/{credential_id}` | 否；用户凭据进入模型调用链前必须 | 立即禁止新解析并撤销/加密擦除密文；已发出的外部请求不能保证停止；跨用户访问返回 404 |

供应商凭据与 NexusPilot 个人 API Key 是两类不同对象：前者必须在调用模型时解密，后者只用于认证 NexusPilot 请求且永不需要恢复明文。两者不能共表、不能共用加密方式，也不能在 API 中统称为 `api_key`。

`verify` 的实际网络动作由供应商适配器定义：优先使用不计费的身份或模型可见性端点；供应商没有可靠的免费端点时，必须在界面明确说明可能产生最小费用并取得用户确认，不能暗中发起生成请求。验证通过只证明当时可认证，不保证后续余额、模型权限或网络持续可用。

同一用户可以为同一 provider 保存多个命名凭据，但调用选择必须确定：请求显式提供本人的 `credential_id`，或使用该用户为该 provider 设置的唯一默认凭据；没有默认项或默认项冲突时明确失败，不能按创建时间随机选择。`POST`/`PATCH` 可以设置 `is_default`，数据库约束或事务锁必须保证每个 `(user_id, provider)` 最多一个有效默认项。

## 阶段2～6的测试用户与密钥方案

完整用户认证不是阶段2～6内部开发的前置条件。各阶段使用以下隔离方案，不增加 `/auth/test-login`、固定后门用户或仓库内明文密钥：

| 阶段 | 测试身份 | 平台认证 | 供应商凭据 | 必须提前固定的合同 |
|---|---|---|---|---|
| 阶段2：LLM 核心能力 | 测试事务内由 fixture 创建唯一 User/Session/Run | ASGI 测试注入运行时生成的部署级测试 key | 单元/契约测试使用假 Provider；真实冒烟只读取显式环境变量 | 不把测试密钥写入数据库、日志或仓库；测试后删除真实基础设施数据 |
| 阶段3：异步任务 | fixture/受控 seed 创建专用 User、Run 和 Task | API 使用测试部署 key；Worker 使用独立服务身份配置 | 可靠性测试只用假 Provider；真实冒烟可使用部署环境密钥 | `execution_spec_json` 只保存 `credential_reference`，RabbitMQ 消息不含任何 secret |
| 阶段4：工具能力 | fixture 创建 owner、Run 和 Task | 工具执行器只接受已验证的内部执行上下文 | 模型供应商密钥不是工具权限测试前提 | 工具权限从 Run/Task owner 和策略派生，不能信任工具参数中的 `user_id` |
| 阶段5：Agent Runtime | fixture 创建 Controller/Agent 所属 Run | Agent 进程使用服务身份，用户归属从 Run 读取 | 默认使用假 Provider；可选真实测试使用部署环境密钥 | Agent/Task 只携带凭据 ID 或部署凭据选择器；不复制明文到 L0/L2 Memory 或 Handoff |
| 阶段6：代码搜索 | fixture 创建用户、项目和隔离 workspace | 搜索服务使用内部执行上下文 | 不需要供应商密钥；与 Agent 联调时沿用阶段5选择器 | 路径和结果按 workspace/owner 限定，测试用户不能搜索其他 fixture 的目录 |

### 测试数据供给规则

- 快速测试：每个测试创建不可预测的 `user_id` 和对象 ID，并在事务回滚或 fixture teardown 中清理；不依赖固定数据库行。
- 真实 MySQL/RabbitMQ/MinIO 集成：使用带本次运行后缀的专用测试用户，记录创建对象并按外键顺序清理；清理失败必须报告残留 ID。
- 浏览器端到端：在测试环境启动时通过受控 seed/bootstrap 创建一个不可用于生产的账户；凭据由测试运行注入，不写入源码、快照或测试报告。
- NexusPilot 测试 API Key：每次运行动态生成，公共 key 与内部 key 必须不同；不得沿用配置默认值验证安全测试。
- 供应商测试密钥：普通测试使用假值和拦截传输；真实测试必须显式开启、从进程环境读取、限制预算和模型，并在日志中只记录是否配置。
- 生产启动必须拒绝测试环境标记、测试账户 seed 和已知测试密钥前缀，不能依赖运维人员记得关闭后门。

## 阶段3凭据引用合同

阶段3在尚未实现用户供应商凭据时也要避免形成不兼容执行载荷。建议在版本化执行规格中使用：

```json
{
  "credential_reference": {
    "source": "deployment_config",
    "credential_id": null
  }
}
```

阶段10完成并允许用户自带密钥后，扩展为：

```json
{
  "credential_reference": {
    "source": "user_provider_credential",
    "credential_id": "credential UUID"
  }
}
```

- Worker 根据 Task → Run → User 关系重新校验凭据归属，不能只相信 `credential_id`。
- 队列消息只携带 `task_id` 等定位字段，不携带 `credential_reference` 或 secret；Worker 从 MySQL 读取版本化执行规格。
- Model Attempt 保存 `provider_credential_id` 和凭据版本 ID 作为审计引用，但不保存密文、明文或末尾掩码以外的信息。
- 凭据在任务排队后、执行前被撤销时，任务以不可重试的 `provider_credential_unavailable` 失败；不得回退到另一用户或平台密钥。
- 第一版的 `deployment_config` 仍由现有环境配置解析，不伪装成用户凭据，也不通过 `/me/provider-credentials` 管理。

## 接口输入输出边界

### 登录与当前用户

- `POST /auth/login` 请求包含规范化邮箱、密码和可选设备名称；成功仅通过安全 Cookie 建立浏览器会话，响应返回与 `GET /auth/me` 一致的公开主体摘要。
- `GET /auth/me` 不返回密码状态、会话令牌、个人 API Key、供应商凭据或内部权限实现细节。
- Cookie 认证的所有状态修改请求必须校验 Origin 和跨站请求伪造防护令牌；Bearer 调用不使用 Cookie 身份。
- 登录、重置和验证均有独立限流，限流键不能只依赖可伪造邮箱；公开错误保持统一。

### 个人 API Key

- 创建请求至少包含 `name`、`scopes` 和可选 `expires_at`；服务端拒绝未知、空或调用方无权授予的 scope。
- 创建响应中的 `secret` 只出现一次；提交成功后任何列表、详情或审计接口都无法再次读取。
- 到期、用户停用、显式撤销和全局安全撤销均使认证立即失败。

### 供应商凭据

- 创建和轮换请求中的 secret 设置严格字节上限，进入日志与异常处理前完成敏感字段标记。
- 加密使用数据密钥加密 secret、主密钥加密数据密钥的信封加密；生产主密钥不与数据库同存。开发环境可以使用独立环境密钥，但不得称为生产方案。
- API 只接受系统登记的 provider 枚举，不允许调用方提交任意 base URL。OpenAI-compatible 自定义端点属于单独的受控连接配置，需域名允许列表和服务器端请求伪造防护。

## 失败与恢复设计

- 登录事务提交前不能设置有效 Cookie；Cookie 设置失败不会产生无法撤销的明文会话令牌记录。
- 个人 API Key 创建响应丢失时不能再次读回 secret，用户需要撤销该 key ID 并重新创建；界面必须提前说明一次展示语义。
- 供应商 secret 加密成功但数据库写入失败时只存在内存临时密文，不写对象存储；数据库提交后才能返回成功。
- `verify` 不在持有数据库事务时访问外部网络；结果使用 `(credential_id, credential_version)` 条件更新，避免轮换竞争。
- 删除供应商凭据先原子禁止新解析，再执行可恢复的密钥材料清理；清理失败保留不可用状态和审计事件，由运维重试，不能重新开放凭据。
- Worker 解密失败、密钥版本不可用、用户停用或凭据撤销均不可自动换用平台凭据；错误对用户可操作但不包含密钥内容。

## 当前阶段任务

本轮仅完成规划。未来实施顺序如下：

| 工作项 | 具体任务 | 预期结果 | 测试或验证方式 |
|---|---|---|---|
| 身份数据与迁移 | 增加登录身份、密码、会话、个人 API Key、供应商凭据和审计表 | 唯一性、外键、撤销和版本约束可由数据库保证 | 空库/存量库迁移、降级与 MySQL 约束测试 |
| 当前主体依赖注入 | Cookie/Bearer 解析为 `AuthenticatedPrincipal` | Router 不再信任正文 `user_id` | 缺失身份、双身份、停用用户、跨用户测试 |
| 登录与会话 | 实现登录、退出、当前用户、会话查看和撤销 | 会话到期和撤销立即生效 | 密码失败、枚举防护、会话固定、跨站请求伪造测试 |
| 个人 API Key | 实现一次展示、scope、到期和撤销 | 程序调用可识别当前用户 | 创建/丢失响应/到期/撤销/越权测试 |
| 供应商凭据 | 实现信封加密、轮换、验证和禁用 | 用户凭据能安全解析给 Model Gateway | 加密、密钥管理服务故障、竞争轮换、验证超时、删除测试 |
| 资源授权迁移 | 将现有最终用户路由改为 principal owner | 请求不能声明其他用户身份 | 每类资源跨用户 404/403 契约测试 |
| Worker 集成 | 按 Run owner 解析凭据引用和版本 | 排队任务不携带 secret，撤销实时生效 | 重投、排队后撤销、用户停用和解密失败测试 |
| Web 集成 | 登录页、会话 Cookie、个人 key 和供应商凭据设置页 | 浏览器不持有部署级或供应商明文密钥 | 端到端、安全 Header、浏览器存储扫描 |

## 测试设计

必须先写权限与安全测试，再实现：

- 认证：允许、密码错误、账户不存在统一响应、缺少身份、过期会话、撤销会话、停用用户、Cookie/Bearer 冲突。
- 授权：User、Session、Run、Task、Attempt、Artifact、Memory 每类至少覆盖当前用户允许和跨用户拒绝。
- 个人 API Key：只展示一次、未知 scope、到期、撤销、重复撤销、用户停用、并发最后使用时间更新。
- 供应商凭据：密文不可直接解密、日志无 secret、版本轮换、旧验证结果竞争、验证超时、删除后 Worker 拒绝、KMS 暂时不可用。
- 会话安全：令牌固定、Cookie 属性、Origin/跨站请求伪造、绝对与空闲到期、密码变更后其他会话失效。
- 恢复：数据库提交失败、验证响应丢失、Worker 领取后凭据撤销和审计写入失败的确定行为。

## 风险与停止条件

- 未确定本地密码账户如何预置和恢复时，可以实现内部数据模型，但不能声称登录能力可公开使用。
- 生产密钥管理服务、密钥轮换和灾难恢复未确定时，不保存真实用户供应商凭据。
- 现有资源 Router 尚未改为当前主体派生 owner 时，不移除受信调用方边界或直接公开接口。
- 阶段3执行规格若把 secret、环境变量名或可跨用户复用的凭据放入队列，必须停止实现并修订合同。
- 用户要求自定义 Provider base URL 时，必须先增加网络出口允许列表、DNS/IP 复核和重定向限制，不能当作普通字符串保存。

## 完成标准

- 浏览器登录、退出、当前用户和会话撤销均通过安全与端到端测试。
- 程序调用可以用个人 API Key 识别 `AuthenticatedPrincipal`，scope、到期、停用和撤销实时生效。
- 用户供应商凭据只以信封加密密文保存，API 永不回传明文，轮换、验证和删除具有版本与并发保护。
- 所有最终用户资源从 principal 派生归属，跨用户访问通过完整测试矩阵拒绝。
- RabbitMQ 消息、Task/Agent Memory、日志、追踪、错误和原始请求记录均不包含登录令牌、个人 API Key 或供应商 secret。
- 阶段3～6继续可以用 fixture、假 Provider 和部署环境凭据独立测试，不依赖公开后门或固定测试账户。
- 迁移、快速测试、真实 MySQL、浏览器安全和 Worker 凭据撤销测试全部通过后，阶段状态才能改为“已完成”。
