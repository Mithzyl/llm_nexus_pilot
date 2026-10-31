# 阶段5：平台身份、授权与凭据管理规划

## Assistant可选统一登录已接入（2026-10-31）

另一线程已实现真实Assistant回调、PKCE/state校验、服务端兑换和自身会话签发，保留原登录/注册。
隔离双端本地验收记录见[登录验收](../../output/security/assistant-unified-login-20261031.json)；
本线程随后只读核对实际代码和记录，未重跑该流程。关联后登录原App用户、刷新、解绑拒绝新登录、
原密码登录保留均有该记录支持。功能默认关闭，不按邮箱合并、不迁移历史数据；解绑不是跨应用单点登出。
此前“Assistant尚无回调”的描述仅适用于10月30日及之前的历史验收，不再是当前登录阻塞。
App记忆/指令管理页面仍需独立接入与验收，本线程对Assistant继续只读。

## 跨端运行诊断（2026-10-30，进行中）

新增只读App Run诊断接口，将当前App运行与Workflow、Model Attempt、Tool Call和记忆工具
调用的ID/状态/时间关联。查询使用明确SQL列，不读取MinIO或输出业务正文、错误全文、凭据。
两组明细独立游标，跨用户、跨App和其他Run游标均拒绝。接口字段和调用方式由
[App接入说明](personal-assistant-app-integration.md#app运行诊断接口2026-10-30)维护。
定向HTTP与真实MySQL验证通过；App页面仍受只读限制，未实现。缓存字段仅展示已记录值，
不把固定测试token当作真实供应商缓存收益。

## 同owner多App运行权限检查（2026-10-30）

映射约束允许同一owner在多个App登记后，owner相等不能单独证明App可以访问Run。
已补充App认证mapping_id到通用资源父引用检查，Run归属的保存映射必须完全一致；
摘要、结果、事件、取消均适用，原App仍可访问。回归复现修复前跨App摘要200，修复后404；
真实MySQL验证通过，见[App运行隔离记录](../../output/security/app-run-scope-20261030.json)。
不把此检查解释为Assistant统一登录已接入。

## Nexus会话管理页面（2026-10-30）

个人设置账户页复用现有 GET `/api/v1/auth/sessions` 和 DELETE
`/api/v1/auth/sessions/{login_session_id}`，显示当前、可用、过期和已退出会话及最近使用时间。
只允许明确确认后退出其他有效会话；当前会话使用原“退出当前账户”。本机无登录模式不显示管理列表。
读取失败显示错误而非空列表，撤销结果未知时提示刷新检查，不声称已成功。
不展示令牌，不新增数据库结构，不撤销个人Key或关联App会话。
独立真实MySQL/API/浏览器已验证保留、撤销、刷新及被撤销会话401/当前会话200；
390px窄屏截图已查看。记录见[会话验收](../../output/security/login-sessions-20261030.json)。
此处是浏览器会话管理，不提供可信硬件设备识别；近期重新认证仍未完成。

## 密码找回当前实现（2026-10-30）

Nexus 已新增邮箱密码重置和登录页“忘记密码”。`NEXUSPILOT_PASSWORD_RESET_ENABLED`
默认 false，与注册开关独立；启用还要求认证开启、现有私有
`registration_verification_key` 和 SMTP 配置可用。注册可保持关闭。
迁移0048新增 `password_reset_challenges`，只在独立验收数据库升级，未升级正常开发库。

接口为 GET `/api/v1/auth/password-reset` 查询可用性，POST 同前缀 `/request` 提交 email，
POST `/verify` 提交 email、code、new_password。申请响应不区分账号是否存在；邮件失败记录
脱敏审计但不声称已投递。证明15分钟有效，每邮箱60秒内不能重发，同客户端15分钟最多申请5次，
最多5次错误验证。使用现有加密密钥保存一次性证明及当时密码校验值；密码变更即使发生在同一
数据库时间精度内，旧证明也不能用于重置。不新增业务内容哈希。

验证成功在同一事务中修改密码、消费证明、撤销该用户全部浏览器会话；不会自动登录，
不会撤销个人 API Key，也不会变更 App 账号关联或历史资源 owner。密码登录与修改锁定相关
凭据记录，避免使用旧凭据完成登录；尚未做真实并发登录与重置竞争验收。

必要验证：密码重置及已有认证测试14项通过；独立真实MySQL重置测试2项通过；本地SMTP与
浏览器验证申请、重置、旧会话/旧密码拒绝及新密码登录。浏览器验证后又增加同时间密码变更
用例并修正证明绑定，后续真实MySQL测试通过；未再次进行完整浏览器流程。生产邮件尚未验收。
详见[验证记录](../../output/security/password-reset-20261030.json)。下方历史未实施说明以本节为准；
会话管理页面当前验收见文首；近期重新认证未完成，真实Assistant登录接入见文首10月31日更新。

## 三端账号互通（2026-10-29，进行中）

当前本线程授权范围：Assistant继续只读；另一线程的双端登录交付见文首，不能将旧Nexus接口测试称为双端验收。
用户已允许标准PKCE/S256和现有认证令牌校验，不新增文件、网页或记忆内容哈希。
Nexus新增application_login_grants（0041）及五分钟一次性代码，绑定批准的浏览器会话、App、
精确回调和PKCE；注销/过期检查不依赖浏览器仍然打开。复用现有认证摘要函数，不保存原始代码。
POST /api/v1/me/application-login须真实浏览器登录及CSRF；输入application_id/redirect_uri/
code_challenge/state，返回一次性code、原state及批准的redirect_uri。Nexus的`/authorize`页面
通过GET同路径预览已批准应用及回调地址，用户明确确认后才发码。
App服务端以既有独立应用凭据调用POST /api/v1/auth/app/exchange（code/redirect_uri/code_verifier），
返回subject、display_name和已有external_user_id；不返回Nexus Cookie，不发送用户密码。
没有映射时附带短时registration_ticket，App创建独立账号后通过POST /api/v1/auth/app/complete-registration
提交ticket/external_user_id。只允许新账号关联；已有不同owner的账号拒绝自动接管，须使用下述独立绑定流程。
私有App配置新增login_redirect_uris，默认空即关闭登录；HTTPS或显式loopback HTTP，严格匹配，
不接受用户信息、query或fragment。App必须自行核对state并建立自己的会话；Nexus不能代替该检查。
定向服务及实际ASGI接口验证PKCE拒绝、单次兑换、登记重放和改变外部身份拒绝；连同路由检查25项
通过。后续补验授权码过期、批准登录被撤销后均拒绝兑换且不标记已消费，仍为25项定向测试通过。
Nexus确认页面已增加登录状态检查、服务端回调预览和显式确认；TypeScript与定向ESLint通过。
0041只在隔离MySQL执行。2026-10-30 Nexus确认页面已用真实浏览器及独立MySQL验证登录、
明确确认、回调state、未批准地址拒绝和刷新不额外发码；桌面/手机截图及限制见
[浏览器记录](../../output/security/sso-browser-20261030.json)。App回调由Playwright拦截，未兑换，
未建立Assistant会话；双向绑定页面及端到端验收仍未完成，不能据此宣称Assistant统一登录已完成。
协议依据：[RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)。不声称实现通用OIDC服务器。

### 存量账号绑定（2026-10-30，进行中）

迁移0042增加`account_link_requests`和`ecosystem_account_links`，只在独立验收数据库
`identity_acceptance_69ffe918f1d1`升级通过，不改历史资源owner。当前新增接口：

- App服务端在用户明确同意后，以既有App凭据和已认证`X-External-User-Id`调用
  `POST /api/v1/app/me/account-link-requests`，返回十分钟一次性`ticket`。
  Nexus根据现有mapping保存App侧身份；不得由请求正文指定Nexus owner。
- Nexus浏览器登录后POST `/api/v1/me/account-links/preview`（ticket）查看目标应用和外部账号，
  明确同意后POST同前缀`/confirm`（ticket）。需要真实浏览器登录及CSRF。
- GET `/api/v1/me/account-links`只返回当前账号关联的ID、状态和版本。
- POST `/api/v1/me/account-links/revoke`（link_id）或App端
  `/api/v1/app/me/account-links/revoke`允许双方撤销；App调用还要求精确mapping一致。
  绑定不自动共享任何业务内容；解绑同步撤销依赖该link_id的记忆共享记录。
- 登录兑换优先查原同owner映射，没有时才查有效绑定；返回原App external_user_id，
  不创建第二个App账号，不将App历史资源转移到Nexus账号。

必要测试包含App请求未确认时不生效、单次确认、原owner保留、关联账号登录解析、解绑、
外部用户越权拒绝、mapping限定和过期拒绝。绑定服务与ASGI接口测试使用合成数据；
真实MySQL只验证迁移，不将其称为MySQL并发或真实Assistant绑定验收。
Nexus新增`/account-links`确认/管理页面，个人设置账户栏提供入口；App应将短时ticket置于
URL片段`#ticket=...`，不放query、日志或浏览器持久存储。用户确认后清除片段。
2026-10-30已用真实浏览器和独立MySQL验证确认、刷新、失效ticket仍显示已有记录及解绑恢复；
修复页面已经打开时不处理新片段的问题。数据库确认原App owner不变，见
[浏览器记录](../../output/security/account-link-browser-20261030.json)。App发起侧仍为合成服务数据。
跨owner记忆共享已按阶段2接入有效关联及两端撤权，真实MySQL/MinIO服务检查通过；
真实MySQL已补三种并发确认：同ticket、同App账号两个ticket、同Nexus账号确认两个同App账号；
每种均只有一成功、一拒绝且一条关联，见[并发记录](../../output/security/account-link-concurrency-20261030.json)。
不声称覆盖所有跨App锁竞争。真实App双端流程仍未执行。
Assistant仍只读，未新增Assistant入口或替用户发起真实绑定。

当前战略入口为 native、personal_assistant、retail。既有应用身份自动创建资源 owner，
不等于完成两个独立登录账号的绑定。按总计划五批顺序：Step 2准备映射约束，第四批实现统一
登录、双向绑定/解绑及密码找回；不提前改写存量owner。Step 2跨不同owner的产品共享在绑定
完成前不可用，测试关联记录不等同于真实用户授权。设备/会话管理列入第四批，MFA与第三方登录暂缓。

- 映射约束从 pilot_principal_id 全局唯一改为 `(application_id, pilot_principal_id)` 唯一，
  保留 `(application_id, external_user_id)` 唯一。迁移先核查重复和实际约束名；不得重写旧迁移。
- 新账号统一认证应使用短时、一次性、绑定客户端与回调地址的授权码，由 App 服务端兑换；
  校验 state/PKCE、目标应用和有效期。各端保留独立会话，不共享浏览器 Cookie 或传递用户密码。
  应用入站凭据本身不能选择任意主 owner；只有用户授权的身份流程才能建立同主身份映射。
- 存量绑定新增 ecosystem_account_links：link_id、primary_principal_id、linked_principal_id、
  mapping_id、status、双方验证时间、verified_at、revoked_at、version。双方身份须独立验证；
  禁止按同邮箱绑定，禁止自绑定、循环绑定和未经用户确认的主账号切换。
- 绑定不修改历史 Run/项目/记忆 owner，不自动授予跨端业务、文件或记忆读取。解绑立即阻止
  后续跨端访问，相关授权失效；已提交给模型的内容不能声称可以撤回。
- 记忆共享规则由阶段2定义。账号关联只证明用户关系，不替代每条记忆的共享与敏感披露。

验收：三端同主身份映射、同应用重复映射拒绝、双向验证/过期/重放/解绑、旧数据可读取但未授权
跨端请求被拒绝。先固定身份服务测试，再分别执行两个 App 的协议验收，不能以修改唯一约束
替代“互相登录”完整验收。

## 当前最小用户后端范围（2026-10-23）

### 当前状态与新增账号能力（2026-10-29）

状态：进行中。已实现登录、退出、密码修改、会话撤销、个人Key管理后端和原生个人设置入口。
2026-10-28合成Assistant映射用户通过登录创建Key、一次OpenCode Go生成、跨用户拒绝与撤销验证，
见[实际记录](../../output/security/assistant-personal-key-live-20261028.json)。映射由测试准备显式建立，
不是自助账号绑定。当前主本地API关闭登录，不支持个人Key；验收临时登录实例已停止。
注册及邮件验证本地实现进行中，当前证据见下节；密码找回及Nexus账号关联当前实现见文首，真实App侧接入未完成。下列新增规划替代历史“公开注册不在范围”
的永久排除含义；原先最小登录阶段完成记录保持，不追溯改成未完成。

### Nexus注册与App账号关联（注册进行中，App关联暂停）

2026-10-29：用户确认使用通用SMTP、默认关闭注册、先用本地邮件服务验收。已新增：

- `GET /api/v1/auth/registration`只返回enabled；`POST .../registration/request`接收email；
  `POST .../registration/verify`接收email、code、display_name、password。三个接口不接收owner或权限字段。
- 请求验证码不创建账号，也不保存密码。验证正确后在一个事务内创建User、已验证登录身份、
  现有Argon2id密码记录，并消费验证码；注册不会自动登录，继续使用原login/Cookie/CSRF接口。
- 迁移`20261029_0031`只增加registration_challenges，验证码使用独立私有Fernet密钥加密保存，
  15分钟有效、最多5次错误；每邮箱60秒内不能重发，同客户端15分钟最多申请5封。
  客户端计数复用原认证审计标识算法，不新增内容哈希、哈希字段或账号关联。
- SMTP失败或结果未知不能激活账号；不自动重发。已有账号不被注册覆盖，也不发送重置密码邮件。
  注册请求与验证均检查Origin；只有这两个登录前动作无需登录Cookie，其他操作权限保持。
- 登录页按后端开关显示注册入口，验证码和密码只在组件状态中保存，不进入URL或浏览器持久存储。

配置均为服务端`apps/api/.env`，示例已更新：`NEXUSPILOT_REGISTRATION_ENABLED=false`、
`NEXUSPILOT_REGISTRATION_VERIFICATION_KEY`（独立Fernet密钥）、`REGISTRATION_SMTP_HOST/PORT/SENDER/USERNAME/PASSWORD/STARTTLS`
（后六项同样加`NEXUSPILOT_`前缀）。非loopback SMTP必须STARTTLS；本地协议测试才允许明文。
启用前按项目流程执行`cd apps/api && .venv/bin/alembic upgrade head`，配置邮件及密钥，并启用原有登录。
不要把真实凭据放入示例或报告。本批未升级现用数据库、未启用公开注册或真实外部邮件服务。

验证：新增测试先因缺少服务失败，随后覆盖未验证不能登录、一次性验证、过期、错误次数、
请求频率、Origin拒绝、投递失败、验证后登录。真实loopback SMTP测试使用标准SMTP客户端和
合成邮箱，不是外部邮箱投递通过；BFF定向37项通过，TypeScript和相关ESLint通过。
实际命令：`cd apps/api && .venv/bin/pytest tests/test_registration.py tests/test_resource_policy.py -q`，
30项通过；其中迁移在临时SQLite中验证不修改已有owner。Ruff通过。
Playwright固定BFF响应验证申请验证码、填写后注册、返回登录及390px窄屏；检查合成验证码和
密码未进入URL/localStorage/sessionStorage。截图`output/playwright/registration-mobile.png`。
固定响应不是浏览器与真实后端联合验收。随后使用现有`resource_infrastructure`隔离库流程，
在本机MySQL运行`tests/test_registration.py -q --tb=short`，6项通过（含实际迁移、SMTP协议与登录）；
测试库按原测试流程释放，没有修改现用nexuspilot库。随后独立实例从空库迁移至0031，浏览器
实际申请本地SMTP验证码、验证注册并登录通过；固定模型替换所有供应商调用，没有使用BFF mock。
记录见[注册与记忆浏览器联合验收](../../output/security/identity-memory-browser-20261029.json)。
公网邮件投递未验收；默认注册开关保持关闭，不将本地SMTP通过当作公网邮件可投递。

**2026-10-29最新范围：** P1只实施Nexus注册／验证／登录；下文App双端确认、关联和解除设计
保留为暂停方案，本期不实施，不是注册的前置。现有App服务凭据和用户映射继续工作，不改owner。

本地只读核对：personal_assistant共有12条映射，10条映射及owner启用，均没有Nexus登录身份；
另2条有登录身份的映射属于10月28日合成个人Key实验，映射与用户均已停用。因此现有有效映射
证明App用户有Nexus资源owner，不证明已绑定可登录的Nexus账号。数据库包含历史测试映射，
不能仅凭数量将某条认定为用户当前Assistant账号；具体账号需以App当前external_user_id核对。
本次未修改映射、注册用户、签发凭据或调用模型，仅验证本地库，不代表服务器状态。

后续同日只读核对当前Assistant进程：8010服务实际打开
`services/assistant-api/data/air-commute-20261028/assistant.db`，不是默认assistant-dev.db。
该库3个有效用户均匹配启用的personal_assistant映射；24条已有Nexus Run的owner、
run_application_context.mapping_id及workflow.run_id逐条一致（24/24），没有重新执行模型。
Assistant的nexus_identity_links为空、24条本地nexus_principal_id均为空：当前run_service
明确初始化该字段为None，并将认证user.user_id作为X-External-User-Id发送，由Nexus解析owner。
所以该缓存为空不代表App身份映射缺失；它也不代表已绑定Nexus登录账号。
这是当前运行库所有账号的保存记录核查，未读取登录令牌，不能指认浏览器此刻登录其中哪一个。
App源码/数据库未修改；只记录本地事实，不扩展为新调用、服务器或完整权限矩阵验收。

**交付目标与依赖。** 用户可注册Nexus账号，再明确关联其已登录的Personal Assistant或Retail身份，
理解关联后可以使用什么功能。复用单MySQL现有users/认证身份/会话/个人Key和App映射；不分库，
不复制App业务数据，不实现App项目映射。邮件服务、允许的App回调地址及凭据须在实施前确定；
本次不登记真实外部邮件服务；已实现的注册路由见上节，App关联路由仍未定义或实施。

**注册第一项交付。** 注册默认关闭，由部署明确启用；建议首发使用管理员邀请配合邮箱验证，
开放注册由部署策略明确选择。账号在完成验证前不能调用付费模型或创建Key。复用现有密码与
Cookie/CSRF处理，新增邮箱验证、重复注册处理、过期/重复使用拒绝和注册/邮件发送频率限制。
邮箱投递失败不得显示已激活；重发不创建多个用户。密码找回以验证邮箱为依赖，不能靠知道邮箱
直接重置。上线前必须确定滥用控制及付费资源额度政策，不能默认无限公共注册调用。
凭据/验证值不写浏览器持久存储、URL日志或报告；复用已有认证存储方式，本规划不新增哈希方案。

**App关联第二项交付。** Nexus登录用户发起关联，选定App并看到能力范围；App服务端验证其当前
登录用户，通过受控回调证明该App身份，Nexus再让用户确认两端身份和申请能力。关联请求必须
短时有效、一次使用、绑定发起登录会话及App；模型参数、浏览器提交的external_user_id或邮箱
相同不能证明身份。关联完成后可列出、撤销；失败/取消不产生半完成的可用关系，重复回调只生效一次。

**历史身份兼容是必需实施项。** 当前application_user_mappings为App创建独立owner，且
pilot_principal_id有唯一约束；不能把关联直接实现成修改owner或覆盖已有映射。首批新增单独的
账号关联关系（表名和迁移字段实施时定义），保留原映射、历史Run/会话/文件/记忆归属。关联只
证明两个账号由同一用户控制；它本身不赋予一个账号读取另一个owner全部资源的权限。跨入口访问
必须有明确能力授权与服务端范围检查。未来合并历史数据是独立迁移项目，不在首批静默执行。

**个人Key与两类调用保持分开。** 已有个人Key代表签发它的Nexus用户，通过Nexus登录页面创建，
不是App入站凭据或模型Provider Key。App代用户调用继续使用App凭据和已认证外部用户映射；
关联不能让App凭据创建/读取个人Key。若用户把自己的Key配置到Assistant，应仅在Assistant服务端
保存并按个人Key身份调用普通接口，不同时发送App身份头；该外部凭据保管和界面需App另行授权。
关联后“无需用户粘贴Key”的委托调用属于后续独立授权设计，不能宣称当前read/execute已支持。

**解除与权限撤销。** 解除后拒绝新的跨入口授权访问，不删除两端账号或历史数据，不撤销用户其他
独立个人Key。已发出的调用不能宣称已撤回；在途行为必须沿用当前任务权限复查和显式取消规则。
重新关联不能自动恢复旧共享授权。记忆仍按owner/入口/项目隔离；共享记忆和敏感向Provider披露
需要分别确认，不因账号关联自动开启。家址、日历等敏感业务信息不进入关联元数据。

实施位置：identity认证服务/路由/模型与迁移、App身份服务、资源权限检查；Web个人设置的账户与
关联入口；App仅按后续授权修改。后端先定义协议，不能单方面改现有App Run或远程工具JSON信封。

验收：注册未验证/重复/过期/邮件失败；双端身份证明、错误会话/重放/跨用户拒绝；并发关联与
解除；旧App映射及owner不变；一个App用户被其他账号占用时明确拒绝；解除后拒绝新访问；普通
个人Key和App调用不混用；记忆不自动跨入口共享。先本地模拟及真实MySQL测试，再按授权做两端
联合验收，不为身份测试默认增加付费模型调用。

2026-10-24规划补充：个人项目/文件工作区、必要MCP/联网工具、动态工具及Assistant用户记忆
按路线图提高优先级。现有Cookie、个人Key、App映射继续作为身份来源，不因工具发现或模型
参数增加权限。项目文件、工具定义/连接和记忆都需在服务端检查owner及适用App范围；新增
产品接口在实施时审查现有read/execute权限，不能假设execute已经授权所有未来敏感管理动作。
MCP连接凭据只存服务端受控配置/凭据引用，不送模型或浏览器；用户Provider凭据仍暂停。
2026-10-27记忆规则替代旧范围：个人与项目记忆均属首批规划，产品开关按owner及当前入口默认
开启；本批已实现个人与 Nexus 项目后端，两个 App 项目映射与设置/确认页面暂不实施。
普通明确保存可直接执行；模型自提候选及敏感保存仍需相应用户确认。
模型可传达用户明确的纠正、忘记和开关操作，但不能自授授权。跨App共享需显式身份关联与授权。
当前入口为Nexus原生、personal_assistant和retail；原生不是已登记App ID，App映射及Run关系
继续提供真实身份。设置写入、记忆修改、第三方披露需分别检查，read/execute不能概括授权所有动作。
主规则与待确认App项目映射见[Phase 2](phase-2-llm-core-capabilities.md#assistant用户记忆规划2026-10-24)，
工具方案见[Phase 7](phase-7-tool-runtime-plan.md#个人与项目记忆工具规划2026-10-27)。本批身份解析
为新 Run 保存记忆工具许可。个人 Key 不获得新增写权，也不能启动浏览器创建的带记忆许可 Run；
仍能创建不带记忆工具的新 Run。App 用户和入口从已保存映射读取，模型参数不能改变。
新增权限/撤销/恢复验证见 Phase 2 产品记忆记录，不将已有个人Key验收计作本次结果。

2026-10-26联网工具：继续使用现有Cookie/本地身份/个人`np_` Key解析Run owner和read/execute权限，
另由服务端联网用户名单授权。DeepSeek/Tavily统一凭据只放API私有配置。后续确认的读取策略为
DeepSeek Search→本机Fetch，普通失败可调用一次Tavily；权限拒绝、非法目标或取消不得触发备用服务。
本次明确不新增个人供应商Key管理、存储表或映射设计；现有个人Key的创建、撤销与认证规则不变。
联网默认与当前生成模型无关，App Run暂不开放。实现及限定验收见[工具主计划](phase-7-tool-runtime-plan.md#联网工具本地实现与待验事项2026-10-26)。

本节替代后文早期设计中个人 Key 的未开始状态和近期重新认证前置要求；历史验证记录仍只表示当时结果。
本批最小后端已实现；本地运行库已升级并完成真实HTTP检查，生产验收未执行，整个Phase5仍为进行中。

- 已有浏览器登录、退出、会话撤销和密码修改保持不变。已登录用户通过
  `POST/GET /api/v1/me/api-keys` 创建/分页列出自己的 Key，通过
  `DELETE /api/v1/me/api-keys/{api_key_id}` 撤销。写操作需要 Origin 和会话绑定的 CSRF。
- 创建响应使用 `key` 字段仅返回一次原文；列表仅返回名称、ID、掩码、scope、创建/到期/使用/撤销时间。
  `user_api_keys` 保存 256-bit 随机 secret 所组成令牌的 SHA-256 不可逆校验值；不保存原文或供应商凭据。
- `read` 允许既有业务 GET/HEAD；`execute` 允许已有业务写操作，包括创建、上传、模型执行及取消。
  通常程序同时申请两者。两者均不能管理 Key、改密码或访问内部管理接口。
- Bearer 从 Key 解析真实 owner，复用资源授权；Cookie、个人 Key、App 身份不混用，认证失败不回退。
  Key 到期、撤销或用户停用拒绝新请求。已接受任务不因此自动取消，停止沿用显式取消及 owner 检查。
- 模型仍依据现有登记/启用规则选择平台 Provider Registry；凭据仍由服务端环境配置读取，
  不复制到用户表、Key 表、响应或模型消息。没有新增逐用户供应商权限或凭据系统。
- 原10月23日最小批次未实施找回密码、公开注册、第三方登录、MFA、近期重新认证、全局个人Key撤销及账户设置页面；账户/Key页面随后已接入，注册已纳入上方新增规划，其他扩展仍未实现。
  用户 Provider 凭据继续暂停。每次成功个人 Key 认证更新 last_used_at；限频更新属于后续性能工作。
- 迁移 `20261023_0026` 依赖 App 迁移 `20261023_0025`，只新增 Key 表及索引，不改历史 owner，
  不创建默认账号/Key。不拆库，不新增用户 bucket。具体启用步骤见仓库 README 的个人 Key 说明。

验收使用真实认证依赖、隔离测试数据库/对象存储和模拟 Provider，不以模拟结果宣称真实供应商或部署验收。
剩余真实 MySQL 并发、完整部署矩阵及长期账号管理扩展不包含在本批最小后端完成条件中。

### 本批验证记录

测试命令（仓库根目录）：

```bash
PYTHONPATH=apps/api/src:packages/models/src apps/api/.venv/bin/python -m pytest -q \
  apps/api/tests/test_personal_api_keys.py apps/api/tests/test_personal_api_key_migration.py \
  apps/api/tests/test_authentication.py apps/api/tests/test_application_tool_loop.py \
  apps/api/tests/test_workflow_execution_tasks.py --tb=short
```

2026-10-23：46项通过，18.36秒。范围包括Key一次展示/列表、read拒绝写入、CSRF、到期/撤销/停用、
双用户会话/项目/文件/Run隔离、本人的Run取消、带工具调用的Agent结果读取及跨用户拒绝、
App与个人身份冲突、已有浏览器认证、工作流断开与取消，以及隔离SQLite迁移保留旧用户/会话/Run。
普通回复与Agent使用同一预配置模拟Provider；检查请求消息和返回不含个人Key或服务端凭据标记。
开发期间初始Key接口测试因404失败；补验工具调用时曾因测试响应缺少raw_response失败，修正测试后通过。
Ruff及git diff --check通过；`alembic upgrade 20261023_0025:20261023_0026 --sql`通过MySQL离线SQL生成。
随后将不可用模型检查改为独立新Run，个人Key专项再次9项通过、3.93秒，避免终态Run干扰该检查。
未调用付费模型，未运行真实浏览器或升级开发/服务器数据库；模拟Provider和隔离存储不能证明生产服务可用。

### 本地运行环境验收（2026-10-23，后续授权）

本机localhost:3306/nexuspilot先完成私有临时目录备份，再由0024升级到0026；未改服务器。
API使用进程环境 `NEXUSPILOT_AUTHENTICATION_ENABLED=true` 启动在127.0.0.1:8000，
未将此开关写入.env；后续普通启动如需个人Key，应在.env持久启用登录。
独立启动暴露App路由优先导入导致的循环依赖，现改为先装配资源路由再加载复用路由的App入口。
新增独立Python进程导入测试，修复前失败、修复后通过，避免主测试进程预加载掩盖问题。

使用`deployments/verification/check_local_personal_keys.py`创建两个临时真实密码账号，
通过真实localhost HTTP、MySQL和MinIO验证42项通过：登录、Key创建/脱敏列表、Bearer身份、
会话/Run/文件创建及本人读取、双向跨用户拒绝、本人取消/跨用户取消拒绝、身份混合拒绝和撤销后读写401。
没有访问responses或agent-workflows生成入口，模型请求为0；不验证模型答案。
结束后只停用两测试账号、撤销其Key/登录会话，保留合成会话、已取消Run和文本附件供核对；原账号数据不变。
报告：[真实本地个人Key检查](../../output/security/local-personal-keys-20261023.json)。
这是HTTP/API验证，不是浏览器页面或服务器部署验收。
启动修复后的独立导入、个人Key、浏览器认证及App工具回归31项通过（16.42秒），Ruff和差异检查通过。

## 通用 Agent 的授权沿用规则

### 多业务App身份扩展（2026-10-23，进行中）

首批已实现applications、application_user_mappings、run_application_context及App服务认证入口，
复用users资源归属；未生成用户登录身份。迁移20261023_0025已完成隔离SQLite升降级和MySQL离线SQL生成，
尚未在运行数据库执行。跨App/用户结果读取、停用映射、身份字段拒绝及原工具循环集成通过本地测试。
首批工具授权由服务端静态配置和Run固定版本决定，细分Agent/用户授权管理、跨App账号关联仍未实现。
下列原设计中的拟新增基础身份对象已有实现，运行说明统一见阶段7第一批实现。

外部Retail/Personal Assistant用户无需平台登录账号。拟新增应用认证、
`application_user_mappings`及Run应用身份关联，将(application_id, external_user_id)
映射到既有users.user_id资源owner；不同App默认不合并，不用邮箱猜测关联。
服务凭据证明业务后端身份，external_user_id只能由该受信后端声明，模型不得指定或修改执行身份。
创建/查询/SSE/取消及对象读取同时校验调用App与owner；本地关闭登录的共享owner路径不能用于App集成。
工具归属App不等于使用授权，原生Agent跨App使用需另行用户关联和授权，第一版不开放。
用户/应用/工具停用在每次调用前复查；已有Run的固定版本不能扩大权限。

身份实现归本阶段，工具定义、服务凭据引用、远程协议、确认及实施顺序统一见
[多业务App工具规划](phase-7-tool-runtime-plan.md#多业务-app-工具与隔离执行接入规划2026-10-23)。
基础 App 表/API 已实现，细分授权管理仍待实施；不改变已完成的本地认证与通用Agent身份隔离验收；
业务方认证和委托规则未确定前不启用远程业务访问，不恢复暂停的用户Provider凭据保存工作。

**2026-10-19 更新；状态：进行中（新循环授权验证）。** 首批复用资源授权，补充新工作流跨用户查询在读取对象前拒绝、文件撤销后不进入 Reviewer 的确定性测试。真实多用户部署、委派与新写工具尚未验收。[阶段7通用循环](phase-7-tool-runtime-plan.md#首批实现与调用方式2026-10-19)不改变登录、CSRF 和凭据规则，也不恢复暂停的用户 Provider Key 存储。

- 工具发现、加载、执行、来源解引用、历史重读、产物访问和委派分别使用真实 Run owner 重新校验；模型角色名称或短来源编号不授予权限。
- 子任务仅继承明确允许的资源、工具及作用域，不能因主 Agent 已知其他资源 ID 就访问。Reviewer 的读取权限不高于本次任务授权，也不能通过“审核通过”允许已拒绝的操作。
- 生成代码、任务工具、第三方工具说明和文件内容均是未可信输入。任务工具注册/发布、网络、目录写入和外部操作需要各自权限或批准，不能通过自主发现绕过。
- 执行环境不得获得部署凭据、API Key 或 Docker Socket；后端使用受控资源引用执行。原文访问权撤销后，短引用和缓存结果不能作为绕过路径，恢复运行也重新检查。

验收包含两用户交叉读取/委派/引用、已加载工具撤销、文件删除、恢复时身份停用、生成工具冒用作用域和来源面板泄露。保留现有权限实现，只在新增入口补充必要检查和回归，不新增平行身份系统。

**文档日期：** 2026 年 10 月 5 日
**文档状态：** 进行中。身份、会话、资源授权及本批个人Key后端已实现；完整部署矩阵、找回密码和账户安全页面未完成；用户Provider凭据暂停。本批范围与验证以文首为准，历史目标环境证据见[部署记录](server-deployment-20261004.md)。
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

> 本文同时记录当前实现事实与后续完成门禁。阶段5尚未完成，任何未列为已实现的凭据或授权能力都不能按规划文字视为可用。

> 2026 年 8 月 19 日路线图重排：本能力由原阶段10调整为阶段5，作为内部 Web 走向最终用户可用服务的安全前置。

## 总体目标

2026 年 9 月 16 日文件授权实现：新增 `user_files` 私有上传预留、原件／解析对象引用、额度、状态和单次 Run 绑定。文件 API 从当前主体确定 owner，不接受客户端指定；Cookie 写入校验 Origin/CSRF，下载／删除校验归属。新私有文件经旧 Run、Artifact、Context 和模型入口使用时增加主体检查。双存储失败保留精确清理键，提供显式清理命令，不自动扫描或删除整桶。限制和运行方法统一见[阶段2正式文件上传](phase-2-llm-core-capabilities.md#正式文件上传2026-年-9-月-16-日)。这不代表 FastAPI 其他全部资源已完成主体迁移；项目文件授权、逻辑文件历史、共享授权和公网部署仍未完成。关闭登录时所有访问者仍共用固定本地 owner，只能用于可信本机。

- 系统最终需要具备的能力：识别当前登录用户，为浏览器和程序调用分别建立可撤销身份，并让用户安全管理 NexusPilot 个人 API Key 与模型供应商凭据。
- 系统最终需要具备的能力：所有 User、Session、Run、Task、Attempt、Artifact、Memory 和后续 Knowledge/MCP 资源都从已认证主体派生归属，不再信任请求正文或查询参数自行声明的 `user_id`。
- 早期最小范围不处理组织、多租户 Membership、企业单点登录、模型供应商计费代理、密钥共享、管理员控制台和供应商账户代购。
- 本阶段不回退阶段1、阶段2的完成状态；它是最终用户公开访问和用户自带供应商密钥的新增能力。
- 阶段2现有 Prompt 是平台 System Prompt，不是用户资源，不进入阶段5用户授权接口。若未来提出用户自定义 Prompt，必须建立独立表、当前用户归属、共享范围和跨用户授权测试，不能把 `llm_prompt_templates` 或内部 Prompt 管理接口直接开放给最终用户。

## 当前事实

- 当前 `users` 表继续表示业务资源所有者；`user_auth_identities`、`user_password_credentials`、`user_login_sessions` 和 `authentication_audit_events` 已增加登录身份、密码校验、浏览器会话和脱敏认证审计。
- 当前公共 API 使用部署级 `X-API-Key`，内部接口另加部署级 `X-Internal-API-Key`。它们只能证明调用方是受信服务，不能识别最终用户。
- Web 业务请求先通过 `/auth/me` 解析后端选定的访问主体：开发环境关闭登录时为固定本地 owner，启用登录后为会话用户。BFF 校验归属，FastAPI 公共业务 Router 同时挂载 `authorize_resource_request`，不再仅依靠客户端 `user_id`。部署级 Key 作为服务认证继续保留，不能单独代替生产登录主体。
- 当前模型供应商密钥来自 `apps/api/.env`，Base URL 来自部署级 `llm_provider_endpoints`；应用启动时组合“启用地址 + 部署凭据”注册 Provider。地址表不保存密钥，数据库仍没有用户级供应商凭据。
- 阶段2、阶段3及后续内部能力可以使用测试 fixture、受控 seed、假 Provider 和环境变量完成验证，不应为了测试增加绕过认证的公开接口。
- 可信终端预置账户保持可用；10月29日新增默认关闭的SMTP注册实现及限定测试，当前状态和配置见文首。
  外部邮箱投递尚未验收，找回密码未实施；用户Provider凭据管理仍暂停。

### 数据库边界决定

当前不拆分身份数据库。身份表与现有 `users`、Session、Run 及后续资源授权存在强外键和事务关系；放在同一个 MySQL 数据库可以让账户预置、用户停用、会话撤销和资源所有权保持一致提交，也避免在尚未形成独立身份服务前引入分布式事务与跨库补偿。

身份表不使用 `llm_` 前缀，并放在独立 `features/identity` 模块，保持逻辑职责边界。只有出现以下真实条件时才重新评估分库：身份服务需要独立部署和扩缩容、合规要求物理隔离、不同团队需要独立数据权限，或跨产品复用统一身份服务。届时先稳定不可变 `user_id` 和服务间认证合同，再迁移数据；不能只因为现有业务表多数带 `llm_` 前缀就提前拆库。

### 已实现的第一条身份链路

- 可信终端命令原子创建现有 `User`、规范化邮箱身份和 Argon2id 密码校验值；密码不接受命令行参数。
- `/auth/login`、`/auth/logout`、`/auth/me`、会话列表、单会话撤销、全部撤销和已登录密码修改已实现。
- 浏览器会话使用高熵不透明 Cookie；数据库只保存会话令牌和跨站请求伪造防护令牌摘要。状态修改要求精确 Origin 与会话绑定的防护令牌。
- 未知邮箱与错误密码返回相同结果；失败按邮箱和客户端的服务端指纹限流，审计不保存密码、令牌或原始邮箱。
- Web 不再自行创建用户。`NEXUSPILOT_AUTHENTICATION_ENABLED=false` 为本地默认值，后端创建/复用历史 `nexuspilot-web` owner，返回 `local_deployment`，不设置登录 Cookie；Web 直接进入工作台且不显示退出。启用后返回 `browser_session`，未登录时展示登录门禁；两种模式均由 BFF 从服务端主体派生资源归属，认证失败不得降级。
- 阶段5仍未全部完成：个人 Bearer API Key 本批状态见文首；找回/重置密码和账户安全设置页未实现，用户 Provider 凭据暂停。目标服务器基础冒烟不代替完整部署验收。

### 运行与迁移

- 登录开关只在 `apps/api/.env` 定义，Web 不维护第二个开关；更改后重启 API。关闭登录时 `/auth/me` 必须有部署级 `X-API-Key`，登录和账号管理路由返回 403；只有开发/测试环境允许免登录，生产启动拒绝关闭登录。该模式代表所有访问者共享一份个人本地数据，仅允许可信本机回环访问。
- 本地 owner 停用或已被绑定为密码身份时拒绝免登录访问；不自动接管已认证账号。启用登录后，历史本地数据不自动转移给第一个登录者；本轮不做归属迁移或数据库结构变更。

- Alembic 修订 `20260905_0020` 依赖 `20260831_0019`，只增加身份四表；通过现有 `make api-migrate` 升级。迁移不创建默认账号，不搬移已有对话，也不改变已有模型和能力表。
- 账户通过 `python -m nexuspilot_api.commands.provision_local_user --user-id ... --display-name ... --email ...` 预置，命令读取两次隐藏密码输入；已存在的用户或邮箱会明确拒绝，不自动接管历史开发用户。新账号只能读取自己的资源。
- 开发环境默认允许 `http://localhost:3000` 和 `http://127.0.0.1:3000`；其他入口必须加入后端 `NEXUSPILOT_AUTHENTICATION_ALLOWED_ORIGINS`。生产要求 HTTPS 和 Secure Cookie，反向代理需要正确传递公开请求的协议及主机。
- 会话默认空闲 12 小时、绝对存活 30 天，每隔至少 5 分钟按访问更新空闲期限；密码修改会撤销其他会话，用户停用、到期及撤销使后续身份解析失败。
- 登录失败限流目前基于已提交审计记录的邮箱与客户端组合计数；尚非严格并发额度控制，也未完成多实例入口/IP防滥用及审计保留清理门禁。这些与近期重新认证、并发密码变更/撤权验证一起属于公开发布前必须完成的任务。
- BFF 的写请求检查 Origin 和显式 CSRF 请求头，身份服务确认防护令牌与会话绑定；不能由代理直接从 Cookie 自动补出防护请求头。所有代理响应使用 `Cache-Control: no-store`，认证格式错误不回显密码字段输入。

上述 CSRF 令牌要求用于 `browser_session`；免登录写请求没有登录 Cookie，但仍必须通过 BFF 精确同源检查及后端选定本地 owner，不能因为缺少令牌就任意选择用户。API 不可用或主体格式无效时保持失败，不回退为匿名工作台。

### 文件与项目授权工作项（进行中）

基础私有上传、下载、删除和消息附件归属已有实现；逻辑文件版本、项目文件页面及工作目录接入尚未完成。下列规则同时约束现有资源和后续扩展，不能将整个工作项标为未开始。

统一设计见[文件上传与个人项目工作区设计](../architecture/platform-roadmap.md#文件上传与个人项目工作区设计)。本阶段负责上传前/后、版本引用、下载、删除和项目列表的最终用户授权；不负责文件解析模型或本地工具执行。

- 复用 `users`、`llm_projects.owner_user_id` 与 Workspace 关联；不为每个用户建数据库或默认创建独立 MinIO bucket。多账号每人独占项目，不实现团队 Membership 或跨用户共享。
- 新文件/版本/消息引用的 owner 只能来自后端主体。项目级附件不得通过猜 ID 进入其他项目；当前资源授权继续生效，后续新增版本及 Artifact 产品接口必须按同一主体检查，不能仅凭对象 ID 或部署 Key 放行。
- 私有上传需要持久额度预留、实际大小校验、提交失败补偿和明确删除状态。它们是文件可用性前置，不受“Memory 补偿暂停”影响；用户 Provider 凭据仍暂停，不随文件设计恢复。
- 开启登录后的本地项目迁移必须由可信管理员显式选择归属并预览影响，不自动改 owner、不复制成每个人都能访问；本轮没有实施迁移命令。
- 验收必须覆盖缺少主体、跨用户/跨项目读写、猜测下载版本、登录模式切换、撤销后读取、并发额度和删除竞争。普通文件接口不得仅靠浏览器过滤或部署级 key 代表多用户隔离。

## 路线图排序说明

- 原路线图的问题：只说明“公开前需要最终用户认证”，没有给出独立阶段、接口合同、凭据存储边界或相关内部阶段的测试身份方案。
- 新发现的能力缺口：平台登录、当前用户识别、个人 API Key、模型供应商凭据和撤销审计构成一个可独立交付的安全边界，不适合作为 RabbitMQ、Agent 或前端的附带工作项。
- 2026 年 8 月 19 日路线图重排后，本能力由原阶段10前移为阶段5，位于内部 Web 之后、可观测性和代码能力之前。
- 阶段4内部开发界面不依赖阶段5；任何最终用户公开发布必须通过阶段5门禁。
- 阶段1～3仍按当时的受信调用方边界保持“已完成”。阶段7～10补充测试身份和凭据引用约束，但不把完整登录 API 作为其内部开发完成条件。

## 核心身份模型

### 认证主体：当前实现与后续扩展

当前支持浏览器会话、后端固定本地主体及本批个人 Bearer/scope；下方通用主体示意是设计说明，个人主体实际使用 PersonalKeyPrincipal/api_key_id。近期重新认证留待后续。

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
- `/me/api-keys` 只允许已登录浏览器管理，写操作校验 Origin/CSRF；本批不增加近期重新认证。个人 Key 不能管理凭据或密码；尚未实施的 Provider 凭据管理继续暂停。
- 当前登录入口与资源授权已分离，公共业务路由检查用户主体，内部管理路由保留独立服务凭据；不得按旧的全局 Key 描述让未登录用户持有部署密钥才能登录。

### 持久化对象

以下表表达身份和凭据的职责边界。登录身份四表已有实现，个人 API Key 表及增量迁移已编写；用户 Provider 凭据表仍暂停：

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
GET    /api/v1/auth/password-reset
POST   /api/v1/auth/password-reset/request
POST   /api/v1/auth/password-reset/verify
```

- 会话列表与单会话撤销用于识别和关闭异常设备，不能只提供“一次撤销全部”。
- 已登录密码变更必须验证当前密码，并撤销其他会话。
- 找回与重置使用短时、一次性、加密保存且绑定原密码校验值的证明；响应不能泄露邮箱是否存在。
- 初始阶段不提供公开注册。开发环境通过测试 fixture，私有部署通过受控 bootstrap 命令预置首个账户。若要求公开注册或邀请，再单独增加验证、滥用防护和管理员规则，不能复用 `POST /users` 直接创建可登录账户。
- 采用服务端不透明 Session 后不需要公开 `refresh` 接口；服务端可以在绝对过期时间内受控滚动空闲到期时间。

### 第二组：NexusPilot 个人 API Key

```http
GET    /api/v1/me/api-keys
POST   /api/v1/me/api-keys
DELETE /api/v1/me/api-keys/{api_key_id}
```

| 接口 | 是否为内部能力开发前置 | 合同与失败行为 |
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

| 接口 | 是否为内部能力开发前置 | 合同与失败行为 |
|---|---|---|
| `GET /me/provider-credentials` | 否 | 返回 provider、名称、状态、末尾掩码、创建/更新/验证时间和允许的模型范围；不返回密文、明文或可复原定位信息 |
| `POST /me/provider-credentials` | 否；用户自带密钥前必须 | 接收 provider、显示名称和一次明文 secret；先做格式/大小检查，再信封加密；创建后状态为 `pending_verification` |
| `PATCH /me/provider-credentials/{credential_id}` | 否；轮换能力启用前必须 | 允许修改名称、允许模型和轮换 secret；secret 轮换生成新版本并使旧版本不能再被新任务解析；使用版本条件防止并发覆盖 |
| `POST /me/provider-credentials/{credential_id}/verify` | 否；用户凭据进入模型调用链前必须 | 在数据库事务外执行有超时的最小供应商校验；按凭据版本写验证结果，旧请求不能覆盖新版本；不得返回供应商原始敏感错误 |
| `DELETE /me/provider-credentials/{credential_id}` | 否；用户凭据进入模型调用链前必须 | 立即禁止新解析并撤销/加密擦除密文；已发出的外部请求不能保证停止；跨用户访问返回 404 |

供应商凭据与 NexusPilot 个人 API Key 是两类不同对象：前者必须在调用模型时恢复原文并发送给供应商，因此不能只保存哈希；后者只用于认证 NexusPilot 请求且永不需要恢复明文，可以保存不可逆校验值。两者不能共表、不能共用加密方式，也不能在 API 中统称为 `api_key`。

`verify` 的实际网络动作由供应商适配器定义：优先使用不计费的身份或模型可见性端点；供应商没有可靠的免费端点时，必须在界面明确说明可能产生最小费用并取得用户确认，不能暗中发起生成请求。验证通过只证明当时可认证，不保证后续余额、模型权限或网络持续可用。

同一用户可以为同一 provider 保存多个命名凭据，但调用选择必须确定：请求显式提供本人的 `credential_id`，或使用该用户为该 provider 设置的唯一默认凭据；没有默认项或默认项冲突时明确失败，不能按创建时间随机选择。`POST`/`PATCH` 可以设置 `is_default`，数据库约束或事务锁必须保证每个 `(user_id, provider)` 最多一个有效默认项。

### 2026 年 8 月 31 日多用户密钥保存研究结论

需要先区分两类同样常被称作“API Key”的对象：

| 对象 | 运行时是否需要恢复原文 | 保存方式 | 原因 |
|---|---:|---|---|
| NexusPilot 个人 API Key | 否 | `key_id` 定位 + 服务端独立 pepper 参与的 HMAC-SHA-256 校验值 | 平台只需验证调用方提交的 secret，泄露数据库不能直接得到可用 key |
| 用户的 Provider API Key | 是 | 托管 Secret Manager 的不透明引用，或 KMS/HSM 支持的信封加密密文 | 调用 OpenAI、DeepSeek 等服务时必须发送原始 secret，单向哈希无法完成调用 |

Provider API Key 不能采用“只保存哈希”的方案。哈希只能回答“用户提交的值是否相同”，不能恢复供应商请求所需的 Authorization 值。普通 SHA-256 指纹也不能代替密文：它既无法恢复 secret，也会让拿到数据库的人离线验证候选值。指纹只用于重复检测和审计定位，必须使用数据库之外的服务端密钥生成 HMAC，并与加密密钥分离。

生产环境优先使用托管 Secret Manager：数据库只保存 `credential_id`、owner、provider、secret resource/version 引用、状态和审计元数据；模型调用服务以最小权限按版本读取。若采用应用层保存，则每个凭据版本使用独立数据加密密钥（DEK）和 AES-256-GCM，加密附加数据绑定 `credential_id + user_id + provider + version`；密钥加密密钥（KEK）留在 KMS 或硬件安全模块中，数据库只保存密文、随机数、被包裹的 DEK、算法及 KEK 版本。Google Cloud KMS 的[信封加密说明](https://docs.cloud.google.com/kms/docs/envelope-encryption)明确给出了 DEK/KEK 分层、每次写入使用新 DEK、AES-GCM 和不保存明文 DEK 的做法；AWS Secrets Manager 的[加密过程](https://docs.aws.amazon.com/secretsmanager/latest/userguide/security-encryption.html)也采用每个 secret 版本的数据密钥与 KMS 包裹模型。

调用路径必须在解密前重新校验 owner、provider、状态、默认选择和版本；明文及明文 DEK 使用后立即从可控内存对象释放，不进入队列、日志、追踪、错误或持久缓存。访问权限、读取审计、轮换、撤销、到期、备份恢复和密钥管理服务不可用时的明确失败都是完成条件。该生命周期要求与 [OWASP Secrets Management 指南](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)、[NIST SP 800-57 Part 1 Rev.5](https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final)及 Google Secret Manager 的[最小权限建议](https://docs.cloud.google.com/secret-manager/docs/best-practices)一致。

建议的数据边界不是把所有内容放进单表：`user_provider_credentials` 保存当前选择、owner、provider、状态和默认项；不可变 `user_provider_credential_versions` 保存 Secret Manager 版本引用或信封加密材料、HMAC 指纹和创建证据。轮换先写并验证新版本，再以条件更新切换当前版本；旧版本只在受控回退窗口可用，随后撤销和销毁。当前部署级 `.env` 密钥仍属于 `deployment_config`，不迁入这两张用户表。

## 相关阶段的测试用户与密钥方案

完整用户认证不是相关能力内部开发的前置条件。各阶段使用以下隔离方案，不增加 `/auth/test-login`、固定后门用户或仓库内明文密钥：

| 阶段 | 测试身份 | 平台认证 | 供应商凭据 | 必须提前固定的合同 |
|---|---|---|---|---|
| 阶段2：LLM 核心能力 | 测试事务内由 fixture 创建唯一 User/Session/Run | ASGI 测试注入运行时生成的部署级测试 key | 单元/契约测试使用假 Provider；真实冒烟只读取显式环境变量 | 不把测试密钥写入数据库、日志或仓库；测试后删除真实基础设施数据 |
| 阶段10：异步任务 | fixture/受控 seed 创建专用 User、Run 和 Task | API 使用测试部署 key；Worker 使用独立服务身份配置 | 可靠性测试只用假 Provider；真实冒烟可使用部署环境密钥 | `execution_spec_json` 只保存 `credential_reference`，RabbitMQ 消息不含任何 secret |
| 阶段7：工具能力 | fixture 创建 owner、Run 和 Task | 工具执行器只接受已验证的内部执行上下文 | 模型供应商密钥不是工具权限测试前提 | 工具权限从 Run/Task owner 和策略派生，不能信任工具参数中的 `user_id` |
| 阶段3：Agent Runtime | fixture 创建 Controller/Agent 所属 Run | Agent 进程使用服务身份，用户归属从 Run 读取 | 默认使用假 Provider；可选真实测试使用部署环境密钥 | Agent/Task 只携带凭据 ID 或部署凭据选择器；不复制明文到 L0/L2 Memory 或 Handoff |
| 阶段8：代码搜索 | fixture 创建用户、项目和隔离 workspace | 搜索服务使用内部执行上下文 | 不需要供应商密钥；与 Agent 联调时沿用阶段3选择器 | 路径和结果按 workspace/owner 限定，测试用户不能搜索其他 fixture 的目录 |

### 测试数据供给规则

- 快速测试：每个测试创建不可预测的 `user_id` 和对象 ID，并在事务回滚或 fixture teardown 中清理；不依赖固定数据库行。
- 真实 MySQL/RabbitMQ/MinIO 集成：使用带本次运行后缀的专用测试用户，记录创建对象并按外键顺序清理；清理失败必须报告残留 ID。
- 浏览器端到端：在测试环境启动时通过受控 seed/bootstrap 创建一个不可用于生产的账户；凭据由测试运行注入，不写入源码、快照或测试报告。
- NexusPilot 测试 API Key：每次运行动态生成，公共 key 与内部 key 必须不同；不得沿用配置默认值验证安全测试。
- 供应商测试密钥：普通测试使用假值和拦截传输；真实测试必须显式开启、从进程环境读取、限制预算和模型，并在日志中只记录是否配置。
- 生产启动必须拒绝测试环境标记、测试账户 seed 和已知测试密钥前缀，不能依赖运维人员记得关闭后门。

## 阶段10凭据引用合同

阶段10在尚未实现用户供应商凭据时也要避免形成不兼容执行载荷。建议在版本化执行规格中使用：

```json
{
  "credential_reference": {
    "source": "deployment_config",
    "credential_id": null
  }
}
```

阶段5完成并允许用户自带密钥后，扩展为：

```json
{
  "credential_reference": {
    "source": "user_provider_credential",
    "credential_id": "credential UUID"
  }
}
```

- 首版 Worker 根据 Workflow → Run → User 检查归属；后续独立 Task handler 才沿 Task → Run → User。不能只相信 `credential_id`，用户凭据解密仍暂停。
- 首版消息只携带 `workflow_execution_id` 等定位字段，不携带 `credential_reference` 或 secret；Worker 从 MySQL 读取固定执行配置。内部 Task 不独立入队，执行所有权以阶段10为准。
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
- 创建响应中的 `key` 只出现一次；提交成功后任何列表、详情或审计接口都无法再次读取。
- 到期、用户停用和显式撤销使后续认证失败；全局个人 Key 撤销不是本批接口。

### 供应商凭据

- 创建和轮换请求中的 secret 设置严格字节上限，进入日志与异常处理前完成敏感字段标记。
- 加密使用数据密钥加密 secret、主密钥加密数据密钥的信封加密；生产主密钥不与数据库同存。开发环境可以使用独立环境密钥，但不得称为生产方案。
- 供应商 secret 使用带认证的加密算法；数据库保存密文、随机数、算法版本、被主密钥包裹的数据密钥和主密钥版本。另存服务端密钥参与的 HMAC 指纹用于重复检测或定位，不能用普通哈希替代密文，也不能依赖指纹恢复 secret。
- 解密只发生在经过 owner、Provider、状态和版本校验后的实际调用边界；明文只短时存在内存，不进入队列、缓存、日志、追踪、Attempt、Artifact 或接口响应。生产主密钥由密钥管理服务或硬件安全模块托管，应用数据库备份本身不能解密凭据。
- API 只接受系统登记的 provider 枚举，不允许调用方提交任意 base URL。OpenAI-compatible 自定义端点属于单独的受控连接配置，需域名允许列表和服务器端请求伪造防护。

## 失败与恢复设计

- 登录事务提交前不能设置有效 Cookie；Cookie 设置失败不会产生无法撤销的明文会话令牌记录。
- 个人 API Key 创建响应丢失时不能再次读回 secret，用户需要撤销该 key ID 并重新创建；界面必须提前说明一次展示语义。
- 供应商 secret 加密成功但数据库写入失败时只存在内存临时密文，不写对象存储；数据库提交后才能返回成功。
- `verify` 不在持有数据库事务时访问外部网络；结果使用 `(credential_id, credential_version)` 条件更新，避免轮换竞争。
- 删除供应商凭据先原子禁止新解析，再执行可恢复的密钥材料清理；清理失败保留不可用状态和审计事件，由运维重试，不能重新开放凭据。
- Worker 解密失败、密钥版本不可用、用户停用或凭据撤销均不可自动换用平台凭据；错误对用户可操作但不包含密钥内容。

## 当前阶段任务

| 工作项 | 具体任务 | 预期结果 | 测试或验证方式 | 状态 |
|---|---|---|---|---|
| 身份数据与迁移 | 登录身份、密码、会话、个人 Key 和审计表 | 保留旧数据与归属 | 隔离迁移测试、MySQL离线SQL及本地运行库检查 | 本地运行库已升级0026；本批最小身份数据已实现，Provider凭据仍暂停 |
| 当前主体依赖注入 | Cookie/个人Bearer分别解析真实用户 | Router 不信任正文 owner | 双身份、失效和跨用户测试 | 已实现本批个人Bearer；完整部署验收另行执行 |
| 登录与会话 | 实现登录、退出、当前用户、会话查看和撤销 | 会话到期和撤销立即生效 | 密码失败、枚举防护、会话固定、跨站请求伪造测试 | 进行中：核心接口和密码修改已验证；找回/重置、近期重新认证及真实 MySQL 验证未完成 |
| 个人 API Key | 一次展示、read/execute、到期和撤销 | 程序调用识别真实用户 | 创建、到期、撤销、归属及模型入口测试 | 已完成本批最小后端及本地运行环境验收；结果见文首，管理页面未实现 |
| 供应商凭据 | 实现信封加密、轮换、验证和禁用 | 用户凭据能安全解析给 Model Gateway | 加密、密钥管理服务故障、竞争轮换、验证超时、删除测试 | 暂停：按用户要求后续再确定保存方案，不因部署级 Key 已配置而恢复实施 |
| 资源授权迁移 | 当前主体、资源归属、列表过滤、内部权限分类及一致的 JSON 解析检查 | 不依赖 BFF 拒绝跨用户读取、写入和模型调用 | 原双向审计及新增 Content-Type 变体、跨用户和副作用检查 | 进行中：JSON 类型绕过已修复，6 类 JSON 双向回归和本机 HTTPS 通过；目标服务器部分资源冒烟通过，完整部署矩阵未完成 |
| Worker 集成 | 按Run owner及固定执行版本恢复，服务端解析部署凭据 | 执行记录不携带secret；恢复仍检查授权 | 重投、取消、用户停用及陈旧所有者拒绝；用户凭据解密另行验收 | 进行中：所有权与限定恢复已有实现/测试，完整授权恢复矩阵待补；用户Provider凭据解密仍暂停 |
| Web 集成 | 登录页、会话 Cookie、个人 key 和供应商凭据设置页 | 浏览器不持有部署级或供应商明文密钥 | 端到端、安全 Header、浏览器存储扫描 | 进行中：登录、退出和主体门禁已实现；账户安全、个人 key 和 Provider 凭据页面未开始 |

## 全资源鉴权修复（2026 年 9 月 17 日）

归属：本阶段既有“资源授权迁移”工作项，不新增阶段，不变更历史验收范围。9月30日部署分支已修复新增 JSON 绕过，按框架媒体类型及路由正文类型解析：既检查所有有效 JSON 参数，又不把上传文件中的 user_id 当成资源引用。原回归与新增变体均通过。本机生产镜像 HTTPS 检查通过，实际目标服务器验收未执行，阶段仍为进行中。

2026-09-30 验证补充：大小写、`+json`、带参数及缺少 Content-Type 的请求均执行归属检查；A→B、B→A 读取／写入拒绝，本人请求成功，原始 JSON 文件上传也成功。生产还新增秘密独立性、示例值与最低长度检查，由部署生成器提供随机配置。复现及修复结果见[最新鉴权报告](cross-user-authorization-audit.md)。

### 本次实现与调用方兼容说明

- `features/identity/resource_policy.py` 显式登记 124 个业务方法：43 个内部管理、3 个共享模型目录读取、78 个本人资源接口；其余 7 个认证方法和 health 沿用专用检查。未登记业务方法默认拒绝，默认测试校验分类与 OpenAPI 一致。
- `resource_access.py` 在业务操作前解析真实当前主体、校验写请求 Origin/CSRF，并沿数据库父级关系检查路径、查询和结构化正文引用；普通部署 Key 不再代替最终用户身份。外部传入 owner 只能与当前主体一致，不能冒填其他用户。User 创建和修改不作为普通用户自助管理入口。
- 顶层列表在 SQL 中按 owner 过滤，Task／Attempt／Artifact 通过所属 Run 过滤；分页签名绑定当前 owner，跨用户 cursor 按无效游标拒绝。已有 cursor 可能失效，需要重读第一页。子资源及嵌套来源在读取、下载、模型调用、重试或写入前检查归属，拒绝用户直接指定对象存储 URI。
- 原先仅针对新文件的生产检查已被通用授权替代；原逻辑移到测试夹具，不再被应用导入。授权状态只保存在请求专用数据库 Session，不使用进程级可变当前用户。
- 内部管理方法需要 `X-API-Key` 和 `X-Internal-API-Key`，不接受浏览器会话或 Authorization 头；不存在“普通业务方法多带内部 Key 就能跨用户”的通用豁免。兼容 Attempt 记录写入、User 创建／更新、Prompt 列表／渲染也属于管理方法。脚本应改走明确内部身份或现有预置账号命令，不能把内部 Key 放进浏览器。
- 默认本地模式同样只允许固定 owner。未开启个人 API Key/Bearer、未引入新权限角色或数据库迁移；原有数据 owner 不变，不自动重新分配历史数据。
- 用户停用、会话撤销在下一次业务请求重新检查；不承诺立即终止撤销前已启动的模型请求或后台执行，后者仍属执行控制的后续范围。

### 本次验证记录

- 原审计 57 项在 A→B、B→A 两个方向均通过，确认被拒绝的跨用户标题修改、消息插入和轮次创建未落库。
- 默认测试新增 `test_resource_authorization.py` 与 `test_resource_policy.py`：全部业务方法的缺身份拒绝、内部 Key 不提升浏览器权限、所有用户写方法 CSRF、SQL 列表过滤、游标隔离、嵌套来源、重放、副作用计数、停用／撤销及并发独立主体检查。
- SQLite 权限验证通过；相同权限测试在独立 MySQL/MinIO 验证通过，包含多连接并发列表；旧上传预留并发／额度／下载删除专项另行通过 1 项。
- 完整后端及模型测试 `493 passed, 8 skipped`；8 项未启用的可选基础设施检查不计通过。前端 121 项测试、TypeScript、ESLint 通过。Ruff 和差异检查通过。
- Playwright 使用真实 BFF/FastAPI、独立 MySQL 和真实登录 Cookie：A、B 均正常发送并收到模拟流式回复；B 打开 A 的链接和请求 A 的消息被拒绝，未显示 A 标题／正文；A 刷新正常。未调用付费 Provider，未部署到公网。
- 旧业务测试使用显式受信测试夹具以保持原有造数据及业务规则验证；**不能将这些测试视为授权验收**。新增 `resource_client` 不替换真实授权依赖；上述权限及浏览器检查使用实际身份解析，只有数据库、存储及模型按测试环境隔离。

仍未验证：目标服务器 HTTPS／代理 Cookie 和网络隔离、所有内部管理动作的生产运维流程、既有长请求在撤销瞬间的停止行为。不因此宣称整个 Phase 5 或公网安全验收完成。

### 事实依据与目标

[跨用户测试报告](cross-user-authorization-audit.md)记录了 57 项代表性检查中 41 项失败：实际登录的 B 携带部署 Key，可读取或修改 A 的会话、消息、运行等资源，并冒填 A 的 `user_id` 创建轮次。新私有文件专项检查有效，但没有保护所有会话和列表。目标是让 FastAPI 独立识别调用者并授权，而不是要求所有安全性都由 BFF 保证。

### 修复范围与顺序

1. **分类全部入口。** 以审计盘点的 113 路径／132 方法组合为起点，维护逐方法的身份要求、资源 owner 来源、操作类型和测试对应关系。区分最终用户私有资源、允许共享的模型目录和内部管理接口；新增路由同样必须分类，未分类入口不能默认授权。
2. **统一当前用户检查。** 复用现有 Cookie 会话、用户状态、过期／撤销与 CSRF 逻辑。启用登录后，部署 Key 不能替代用户身份；缺失或失效身份返回 401。请求体／查询参数中的 `user_id`、`owner_user_id` 不得授予权限：本人创建从当前主体派生，兼容字段必须验证与当前主体一致。Bearer 仍未实现，不借本次修复隐式启用。
3. **修复本人资源读写。** 覆盖 User、Session、Message、Run、Task、Attempt、Artifact、Context、Workflow／Node／Event、Evaluation，以及仍挂载的 Project、Memory、Knowledge 接口。子资源沿真实父级关系确认 owner；创建、读取、修改、删除、取消、重试、轮次重放、文件下载和模型调用均检查，不能仅保护详情接口。用户不得通过 User 接口自行创建其他账号、停用他人或扩大管理权限。
4. **修复列表与嵌套引用。** 从当前主体过滤分页查询；伪造 owner、父资源、cursor、Context／续接引用或混合多个用户的资源 ID，不能读出、组合或写入他人数据。跨用户单资源返回统一 404；列表只返回本人允许资源。统计、聚合详情、消息附件元数据、事件回放也必须执行同样过滤。
5. **明确受信内部调用。** 需要跨用户操作的运维／内部入口继续独立服务授权，不通过普通部署 Key、请求自报角色或忽略 Cookie 绕过用户检查；管理动作与最终用户操作分开。迁移现有受信调用方前列明兼容变化，不能为了让旧测试通过而留下用户入口豁免。
6. **保持既有功能。** BFF 的允许接口列表、当前主体与 CSRF 检查保留；新文件归属和删除撤销保持有效。本地免登录模式仍只服务固定本地 owner，不得默许请求切换成任意用户；测试数据通过明确的内部／夹具路径准备。不开启暂停中的 Memory、项目工具或用户 Provider 凭据功能。

实施位置：`features/identity` 维护主体与共享授权逻辑，业务 Router／Service 在查询及写入前使用；SQL 查询携带 owner 条件，避免仅在前端隐藏数据。复用现有 owner 外键，首选不修改表结构；若发现无 owner 可追溯的历史记录，明确报告并制定迁移方案，不默认将全部记录归给当前用户。

### 拒绝行为与副作用要求

- 授权失败必须发生在模型请求、文件读取、对象存储写入、任务调度及数据库业务写入之前；不因被拒请求产生费用或新运行事实。
- 不以 200 空正文、业务 409、参数 422 或未找到错误路由的 404 冒充鉴权成功。测试先确认 owner 自己可访问资源，再验证他人被拒。
- 涉及读取后写入的操作需防止检查与写入间归属变化；用户停用、会话撤销与并发请求的行为必须明确并测试。失败不回退本地 owner，不返回秘密或其他用户的资源详情。

### 验证及完成条件

- [ ] 保留当前已复现的拒绝／过滤期望，修复后 57 项全部满足；审计脚本迁入默认测试发现范围或接入项目实际自动检查，不能只口头要求运行。
- [ ] 补齐路由分类矩阵：每个方法有适用身份、owner 推导规则及测试；管理／共享资源明确例外原因，不将无需个人 owner 的平台目录当作漏洞，也不将用户私有资源误归共享。
- [ ] 两个真实登录身份、无 Cookie／失效 Cookie／撤销会话／停用用户、仅部署 Key、无 Key、缺 CSRF、内部 Key 隔离均测试；A→B 与 B→A 双向验证。
- [ ] 列表、分页、嵌套引用、伪造 owner、批量混合资源、重试／幂等重放、SSE／事件回放均不泄露他人内容；拒绝请求的数据库和模型调用计数不变。
- [ ] 在独立 MySQL 验证真实外键、并发与事务行为；文件使用独立 MinIO 验证下载和拒绝路径，不使用生产数据。
- [ ] 登录／文件现有回归、BFF 权限测试及真实浏览器双账号操作通过；再在目标反向代理／HTTPS 环境验证 Cookie 和内部服务不可直接公开。
- [ ] 更新审计报告中的当前修复结果，保留修复前失败事实，记录未测范围；全部必需项通过前，不标记全资源鉴权完成或公开多用户上线通过。

范围外：个人 API Key、用户 Provider 凭据、找回密码、工作流角色优化及后台 Worker 不因本次修复自动恢复实施。此工作项完成也不代表整个 Phase 5 的其他规划均已完成。

## 测试设计

### 当前验证记录（2026 年 9 月 5 日）

- API 全套测试：297 项通过、4 项真实基础设施测试按配置跳过；新增认证测试 10 项另行复验通过，覆盖密码失败统一响应、缺少/过期/冲突身份、停用、撤销、密码变更、越权、Origin、会话绑定的 CSRF、失败限流及认证参数脱敏。
- Web 单元和代理测试：94 项通过，覆盖当前主体派生、跨用户拒绝、Cookie转发、CSRF/来源拒绝、认证服务不可用和跨账号草稿隔离。
- 浏览器回归：生产构建上桌面/移动 20 项通过；扩展的错误密码、刷新恢复、退出失败与成功退出流程在桌面和移动各 1 项复验通过。浏览器使用确定性BFF响应，未调用真实Provider，也未以真实账号执行完整浏览器到MySQL链路。
- 静态验证：Ruff、TypeScript、ESLint、Next.js生产构建及差异空白检查通过。
- 数据迁移：SQLite升级/回退测试通过，MySQL离线SQL生成通过；本机 `localhost:3306/nexuspilot` 已从 `20260831_0019` 升级为 `20260905_0020`。未在真实库执行降级，未创建或自动接管真实用户账号。
- 尚未验证：真实MySQL并发登录/密码变更/撤权竞争、真实账号浏览器全链路、生产反向代理与Cookie配置、找回密码、个人API Key及用户Provider凭据。

### 登录默认关闭的验证

2026 年 9 月 5 日访问开关后续验证：先行测试复现默认免登录尚不可用、生产缺少开关约束及无 Cookie 写请求被拒绝，再实现开关。API 全套 300 项通过、4 项真实基础设施测试按配置跳过；认证/配置 17 项再次通过。Web 单元/代理/入口组件 98 项通过，Ruff、TypeScript、ESLint、生产构建与差异检查通过。Playwright CLI 在临时生产 Web + 真实本机 API/MySQL 上确认默认工作台可见、历史本地会话可见且登录/退出入口隐藏；另模拟身份查询 503，确认显示不可用而非登录表单或匿名工作台，移除模拟并刷新后恢复。没有发送模型请求、创建登录账号、执行迁移或改变历史资源归属；这不替代真实账号登录或文件上传验收。

### 后续测试要求

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
- 阶段10执行规格若把 secret、环境变量名或可跨用户复用的凭据放入队列，必须停止实现并修订合同。
- 用户要求自定义 Provider base URL 时，必须先增加网络出口允许列表、DNS/IP 复核和重定向限制，不能当作普通字符串保存。

## 完成标准

以下区分当前身份／资源授权与暂停的用户凭据扩展。阶段5仍为进行中；暂停项保留需求但不阻塞已定义的部署凭据后台执行。恢复用户凭据工作前须再次确认保存方案，不能把信封加密研究结论视为实施授权。

- 浏览器登录、退出、当前用户和会话撤销均通过安全与端到端测试。
- 程序调用可以用个人 API Key 识别 `AuthenticatedPrincipal`，scope、到期、停用和撤销实时生效。
- 用户供应商凭据扩展（暂停）：恢复实施后按确认方案验收加密、轮换、验证和删除，API 不回传明文；当前不把该能力列为已完成。
- 所有最终用户资源从 principal 派生归属，跨用户访问通过完整测试矩阵拒绝。
- RabbitMQ 消息、Task/Agent Memory、日志、追踪、错误和原始请求记录均不包含登录令牌、个人 API Key 或供应商 secret。
- 阶段7～10继续可以用 fixture、假 Provider 和部署环境凭据独立测试，不依赖公开后门或固定测试账户。
- 当前身份范围要求迁移、快速测试、真实 MySQL、浏览器及完整资源授权验证；后台服务凭据／用户停用检查随阶段10验收。用户凭据撤销及解密测试在暂停解除后单独验收，不阻塞部署凭据 Worker。未完成与暂停项逐项保留，不能宣布整个阶段已完成。
