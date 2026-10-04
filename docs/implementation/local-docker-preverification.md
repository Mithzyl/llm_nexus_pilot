# 本地 Docker 部署预验证报告

完成日期：2026 年 10 月 4 日。测试于 9 月 30 日开始，中断后继续完成。

## 结论

**基础部署流程通过；文件解析仍有阻塞项，不能据此判定完整生产发布通过。**

已验证空库迁移、生产配置启动、HTTPS、身份与跨用户隔离、文本文件上传、流式转发、文件连续上下文，以及删除容器后重建的数据保留。API 容器的 PDF 沙箱仍失败；Office 镜像本身通过，但生产 API 到 Office 主机执行器的 Socket 接入未验证。

## 环境与范围

- macOS Docker Desktop，Docker Engine `29.6.2`，Compose `v5.3.1`，Linux ARM64 容器。
- 工作区分支 `codex/deployment`，基础提交 `83d335e`，使用当时未提交的部署准备配置；不能把结果归为该提交单独的验收。
- 一次性项目 `nexus-preverify-6s3pjy`，独立 MySQL/MinIO 卷、随机服务凭据、两个合成登录账号。
- 只有 Nginx 映射 `127.0.0.1:19480/19443`。Web、API、MySQL、MinIO、模拟 Provider 无宿主端口发布。
- 实际镜像 ID：API `8f488a254b7e…`，Web `455a0f9d8e3f…`，MySQL `c98dd0dcfe64…`；完整 ID 由本次构建输出记录。
- TLS 使用独立实验自签证书，客户端通过该证书验证连接，没有关闭 TLS 校验。中断期间短期证书过期，10月4日换发并核对服务端实际证书后继续。
- 模拟 Provider 使用 DeepSeek 兼容 HTTP/SSE 协议，API 出站网络在测试 overlay 中被设为内部网络。无真实供应商调用或费用。没有更换生产应用的身份、数据库或存储实现。

## 验证结果

| 对象 | 结果 | 证据及限制 |
|---|---|---|
| 镜像构建 | 通过 | 从当前工作区构建 MySQL、API、Web 镜像 |
| 配置预检 | 通过 | 私有配置权限、内部网络、认证开关、端口和 TLS 文件检查通过 |
| 空库迁移 | 通过 | 按一次性操作从空库迁移到 `20260916_0022`，API 日常启动未执行迁移 |
| 存储初始化 | 通过 | 私有 bucket 与独立应用账号初始化成功 |
| 服务健康与 Nginx 语法 | 通过 | 六个测试容器健康，`nginx -t` 通过 |
| 公网入口约束的本地配置 | 通过 | 检查实际容器绑定，只有 Nginx 的两个回环端口被发布；不替代云服务器外部探测 |
| 容器权限 | 通过 | API/Web UID 10001，只读根文件系统、drop ALL，无 Docker Socket 挂载 |
| 数据库权限 | 通过 | 应用账号的实际 GRANT 无 DDL、ALL PRIVILEGES 或 GRANT OPTION |
| MinIO 权限 | 通过 | 只能列出获准 bucket，创建其他 bucket 返回 AccessDenied |
| 匿名访问与直接 API 路径 | 通过 | HTTPS 登录页 200；未登录聊天 401；公网入口 `/api/v1` 返回 404；直接后台无 Key／只有服务 Key 均不能读取会话 |
| 登录 Cookie | 通过 | Session Cookie 含 Secure、HttpOnly、SameSite=Lax；实际 HTTPS 登录成功 |
| CSRF 与外部 Origin | 通过 | 缺 CSRF 或错误 Origin 的写入返回 403 |
| 双账号隔离 | 通过 | B 的列表不包含 A 的私有会话，读取 A 的会话／文件返回 404 |
| JSON 类型绕过回归 | 通过 | 在真实 API 容器检查普通 JSON、大小写、`+json`、参数及无 Content-Type，B 均不能创建 A 的 Context |
| 内部凭据与浏览器身份 | 通过 | 带浏览器 Cookie 的内部管理调用即使提供内部 Key 仍拒绝 |
| 文本文件上传／读取 | 通过 | 原件实际写入 MinIO，下载字节与上传完全一致，解析状态 ready |
| SSE 端到端转发 | 通过 | 模拟 Provider 的两段文字经 API→BFF→Nginx 到达客户端，实测间隔 1.257 秒；不是供应商性能数据 |
| 文件连续上下文 | 通过 | 后续轮次未重新上传，模拟 Provider 的实际请求仍包含仅存在于文件中的标记；输出验证标志成功 |
| Assistant 消息保存 | 通过 | 按前端现有流程在模型完成后提交消息；初始两轮得到 4 条 User/Assistant 消息。验证脚本最初遗漏此调用，补齐后通过；应用实现未因此更改 |
| 容器冷重建后的数据 | 通过 | 删除并重建全部容器和网络，保留卷及配置；消息 ID、原件字节、登录会话与第三轮文件上下文均保持可用 |
| 过期与撤销 | 通过 | 闲置超过有效期的旧 Cookie 返回 401；退出后即使重新使用退出前 Cookie 也返回 401 |
| 文件删除 | 通过 | 删除后文件下载返回 404 |
| 前端静态资源秘密检查 | 通过 | 实测扫描 20 个静态资源文件，没有运行时部署 Key 值 |
| API 容器 PDF 沙箱 | **失败** | bubblewrap 退出码 1：无权限创建所需 namespace；未关闭容器安全限制 |
| 专用文件运行镜像 | 通过 | `structure-test` 的隔离、DOCX、XLSX、PPTX、Poppler 和 Office parser 共 6 组检查通过；镜像 ID `e4964d7144c5…` |
| 生产 Office Socket 接入 | 未执行 | 本次生产 Compose 未启用主机执行器 overlay；镜像测试不等于 API 接入通过 |
| 实际 Provider／Agent 完整流程 | 未执行 | 本轮验证模拟普通回复和 SSE；未请求付费供应商或进行完整 Agent 浏览器流程 |
| 外部备份恢复、公网证书和云网络 | 未执行 | 本轮是保留卷的容器重建，不是异机备份恢复或目标服务器验收 |

## 剩余动作

1. 解决 PDF 的独立隔离解析执行路径，或在首版明确不开放 PDF；不得以提高 API 整体容器权限掩盖当前失败。
2. 在目标 Linux 主机验证 Office 执行器、Socket 组权限和 API 访问，并核对实际 CPU 架构。
3. 完成正式证书／续期、外部端口探测、真实 Provider 冒烟及异机备份恢复。证书更新后需要验证服务端实际呈现的新证书，不能只看 reload 命令退出码。

验证程序保存在 `deployments/verification/`，复现方式见[验证说明](../../deployments/verification/README.md)。生产运行步骤仍由[生产部署说明](../../deployments/production/README.md)维护。测试不修改现有开发数据库；本次不提交或推送代码。

收尾：本次六个测试容器、专属网络、两个数据卷以及临时 Cookie／证书／凭据已删除，构建镜像保留。非敏感机器可读结果保存于 `output/security/local-docker-preverify-20261004.json`；该文件不包含 Cookie、密码或服务 Key。测试数据可通过上述夹具重新生成。
