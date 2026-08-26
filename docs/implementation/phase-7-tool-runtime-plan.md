# 阶段7：受控工具运行能力规划

**文档日期：** 2026 年 8 月 19 日
**文档状态：** 暂停，规划已固定；等待阶段4～6服务可用闭环后恢复
**总体规划：** [`platform-roadmap.md`](../architecture/platform-roadmap.md)

> 本文固定阶段7的工具合同、权限、工作目录、执行隔离、审计和失败语义。本轮不新增依赖、数据库迁移、工具代码、执行接口或容器镜像。

> 2026 年 8 月 7 日优先级调整：当时的工具规划（现阶段7）暂停，先实施 Agent `model_only_v1`（现阶段3）并提高可观测性（现阶段6）优先级。阶段3不得因此绕过本规划执行文件、Shell、Git、MCP 或外部工具；需要工具时重新评估并恢复阶段7。

> 2026 年 8 月 19 日路线图重排：本能力由原阶段4调整为阶段7。恢复前先完成阶段4 Web 的真实服务验收、阶段5最终用户授权边界和阶段6可观测性；只读工具可以在边界清晰的内部验证中先行，但不能据此提前宣称阶段7完成。

## 目标

- 要实现的结果：为阶段3 Agent Runtime 提供可独立测试、同步执行、权限受控且完整审计的工具运行能力。
- 要实现的结果：模型只能调用注册工具；工具输入由明确 Schema 校验；文件、命令和 Git 操作只能发生在绑定的隔离 Workspace 内。
- 要实现的结果：超时、取消、输出过大、路径越界、权限拒绝、进程异常和结果保存失败都有稳定错误和审计事实。
- 明确不处理：RabbitMQ、后台 Worker、多 Agent 编排、模型工具循环、MCP、最终用户审批界面、网络访问和外部系统副作用。
- 明确不处理：把任意 Shell 字符串、宿主机目录、环境变量或 Docker Socket 直接暴露给模型。

## 当前事实

- `packages/models` 已有 Provider 无关的 `ToolDefinition`、`ToolCall` 和 Tool Message 合同；它们只表达“模型请求调用什么”，不负责执行、权限和审计。
- `llm_tool_calls` 已能保存 `attempt_id`、工具名称、单一风险等级、输入、状态、权限结论、结果 URI 和错误，但尚无写入执行流程、稳定状态枚举、幂等键、工具版本、输出摘要或执行时长。
- `GET /api/v1/internal/tool-calls` 及详情接口已经提供脱敏只读审计查询；当前没有工具注册表、权限判定器、Workspace 边界或执行 Service。
- 当前只有同步 `/responses`，阶段10 RabbitMQ 已降低优先级并暂停。因此阶段7采用同步调用，不承诺请求断开或 API 重启后继续执行。
- 当前最终用户身份尚未实现。阶段7只服务受信内部调用和后续 Agent 进程，不提供普通用户可直接提交 `run_command` 的 HTTP 接口。
- 待确认事项：生产隔离执行服务的部署方式、允许的基础镜像、镜像来源和签名策略、每类项目的命令允许列表，以及阶段3何时创建 Git worktree。

## 与阶段10、阶段3的边界

```text
阶段2 Model Gateway
  └── 生成标准 ToolCall，但不执行

阶段7 Tool Runtime
  ├── 注册工具
  ├── 校验参数
  ├── 判断权限
  ├── 在受控 Workspace 执行
  └── 保存 Tool Call 证据并返回 ToolExecutionResult

阶段3 Agent Runtime
  ├── 决定何时把哪些工具提供给模型
  ├── 接收 ToolCall
  ├── 调用阶段7 Tool Runtime
  ├── 把结果转换为 Tool Message
  └── 控制循环次数、token、费用和最终回答
```

- 阶段7不读取 RabbitMQ，也不要求 `apps/worker` 存在。
- 阶段7不自行再次请求模型；同一次模型调用后是否继续由阶段3决定。
- 阶段7执行 Service 设计为传输无关的异步 Python 接口，未来可由同步 API 进程或 Worker 复用。
- 阶段7完成不代表代码修改任务已经安全；阶段3仍需为每个写任务创建独立 Git worktree，并把该 Workspace 明确绑定到执行上下文。

## 核心合同

### 工具描述

工具注册表中的执行描述建议命名为 `RegisteredTool`，避免与 Provider 合同中的 `ToolDefinition` 混为一谈：

```python
class RegisteredTool:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    required_capabilities: set[ToolCapability]
    is_read_only: bool
    is_concurrency_safe: bool
    default_timeout_seconds: int
    maximum_timeout_seconds: int
```

- Provider `ToolDefinition` 由 `RegisteredTool.input_model` 生成 JSON Schema，不能手工维护第二份输入合同。
- Registry 拒绝重复 `(name, version)`、不符合命名规则的工具和没有输入/输出模型的执行器。
- 模型请求只看到本次任务被策略允许的工具子集，不把“注册成功”解释为“任何 Agent 都有权调用”。
- 工具版本进入审计记录；版本变化不能改变已经开始的 Tool Call 语义。

### 执行请求

建议内部请求命名为 `ToolExecutionRequest`：

```text
platform_tool_call_id
provider_tool_call_id
model_attempt_id
run_id
task_id
user_id
project_id
tool_name
tool_version
arguments
workspace_reference
idempotency_key
requested_timeout_seconds
```

这些 ID 由内部调用上下文和数据库关系确认。模型只能产生 `provider_tool_call_id`、工具名和 arguments，不能自行声明 `user_id`、Workspace、权限、超时上限或幂等键。

### 执行结果

建议返回 `ToolExecutionResult`：

```text
platform_tool_call_id
status
output
output_preview
result_artifact_id
stdout_preview
stderr_preview
exit_code
duration_ms
is_output_truncated
error_code
error_message
```

- `output` 必须符合工具输出模型，并具有总字节上限。
- 大输出保存为 Artifact，模型只接收有界摘要和 `artifact_id`，不接收 MinIO URI。
- stdout、stderr 和异常文本先脱敏再保存；凭据、Authorization、Cookie 和环境变量值不进入模型上下文。
- Tool Runtime 返回事实，不生成自然语言解释；如何向模型表达由阶段3统一处理。

## 工具目录和实施顺序

阶段7仍是一个阶段，不拆成新的阶段编号。按风险从低到高逐项启用：

| 工具 | 含义 | 初始能力 | 并发规则 | 主要限制 |
|---|---|---|---|---|
| `list_files` | 列出 Workspace 内目录与文件元数据 | `read` | 没有写工具运行时可并行 | 不跟随符号链接；限制深度、数量和隐藏目录 |
| `read_file` | 读取 Workspace 内文本文件的有界片段 | `read` | 没有同文件写入时可并行 | 拒绝二进制、超大文件和越界路径；支持行范围而非整库读取 |
| `search_text` | 使用 ripgrep 搜索文本并返回文件、行号和证据 | `read` | 可并行，但受每 Run 并发上限约束 | 固定参数模板、`--no-config`、不跟随链接、限制结果数/单行长度/总输出 |
| `git_status` | 返回隔离 Workspace 的稳定 Git 状态 | `read` | 与写工具互斥 | 使用 Porcelain v2；不执行 hook，不修改配置或索引 |
| `git_diff` | 返回受限差异及统计 | `read` | 与写工具互斥 | 禁止外部 diff 驱动；限制路径、大小和二进制内容 |
| `write_file` | 在可写隔离 Workspace 创建或完整替换单个文本文件 | `write` | 同一 Workspace 写操作串行 | 原子临时文件替换；禁止覆盖符号链接、超大文件和仓库外路径 |
| `apply_patch` | 应用可复核的文本补丁 | `write` | 同一 Workspace 写操作串行 | 先解析和预检全部目标，避免可预见的部分写入；意外中断时返回实际修改文件清单，不虚构全部回滚 |
| `run_command` | 在隔离执行后端运行结构化 argv | `execute` | 默认串行 | 不使用 Shell 字符串；命令允许列表；无网络；限制进程、CPU、内存、时间和输出 |

第一实施批次完成 `list_files`、`read_file`、`search_text`、`git_status` 和 `git_diff`。第二批次只有在路径竞争测试通过后才增加 `write_file`、`apply_patch`。`run_command` 必须等隔离执行后端通过真实 Docker 安全验证后才能启用；普通进程单元测试通过不代表该工具可用。

## 权限模型

### 能力类别

沿用总体规划中的五类能力：

```text
read
write
execute
network
external_side_effect
```

它们不是简单从低到高的单一枚举。例如命令可能同时需要 `execute + network`。现有 `risk_level` 继续保存便于查询的最高风险摘要，同时增加结构化 `required_capabilities` 和策略证据，不能只用一个字符串完成授权。

### 判定输入

权限判定器至少读取：

```text
tool name + version
registered capabilities
run/task/user/project relationship
workspace reference and access mode
task-granted capabilities
deployment policy version
requested paths/command/network
whether an isolated backend is available
```

模型输入中的 `risk_level`、`allowed_paths`、`requires_approval` 或类似字段一律不可信。

### 阶段7默认规则

| 操作 | 默认决定 |
|---|---|
| 读取绑定 Workspace 内普通项目文件 | 策略允许 |
| 搜索代码 | 策略允许 |
| Git status/diff | 策略允许 |
| 写入显式可写的隔离 Workspace | Task 已授予 `write` 时允许 |
| 在原始仓库写入 | 拒绝 |
| 执行命令 | 只有隔离后端、允许命令和 `execute` 能力同时满足时允许 |
| 网络访问 | 拒绝 |
| 外部副作用 | 拒绝 |
| 读取密钥、系统目录或 Workspace 外路径 | 拒绝 |
| 需要用户批准的操作 | 记录 `approval_required` 后停止，不在阶段7伪造批准 |

最终用户批准依赖阶段5当前用户身份和阶段4交互界面。在这些能力完成前，策略要求批准的调用只能停止，不能由内部 API Key 自动代替用户同意。

## Workspace 与路径安全

- Tool Runtime 只接受平台创建并登记的 `workspace_reference`，不接受模型提交绝对路径。
- 执行上下文把 reference 解析为规范 Workspace root；工具输入路径必须是相对路径。
- 读取前解析规范路径并确认仍在 root 内；不跟随符号链接。写入不存在文件时还要解析和验证最近存在父目录。
- `.ssh`、`.aws`、`.gnupg`、凭据文件、系统目录、宿主用户目录和 Docker Socket 永久拒绝，即使它们被软链接进 Workspace。
- 文件类型判断不能只依赖扩展名；读取工具拒绝包含 NUL 字节的内容，并使用严格字节上限。
- 路径预检与实际打开之间存在竞争窗口。真实安全门禁必须包含符号链接替换测试；高风险写入优先在独占隔离 Workspace 内完成，不能只依赖字符串 `startswith`。
- Git 工具内部可以访问仓库元数据，但模型不能通过 `read_file` 直接遍历 `.git`。

## 命令执行与容器隔离

`run_command` 接收结构化内容：

```text
executable
arguments[]
working_directory
timeout_seconds
stdin_text（可选且有界）
```

约束：

- 使用参数数组执行，不拼接 Shell 命令，不使用 `shell=True`。
- 可执行文件必须来自按任务类型配置的允许列表；不继承宿主完整 `PATH` 或环境变量。
- 子进程使用最小环境变量白名单，不传入平台 API Key、数据库、MinIO 或 Provider 凭据。
- 超时先终止整个进程组，宽限期后强制结束；必须同时持续读取 stdout/stderr，避免管道填满造成死锁。
- 输出按流和总量限制；超过预览上限继续有界排空或终止，并标记截断，不能无限读入内存。
- 非零退出码是已完成的命令结果，不等同于 Tool Runtime 内部异常。

生产执行后端至少满足：非 root 用户、只挂载指定 Workspace、根文件系统只读、禁止 privileged/host PID/host network、删除额外 Linux capabilities、`no-new-privileges`、默认无网络、进程/CPU/内存限制、受控临时目录、确定超时和容器清理。Docker 官方说明容器默认没有 CPU/内存限制，因此这些限制必须显式配置。

Docker daemon 或 socket 本身具有高权限。API 进程不能把 socket 暴露给模型生成的命令；实施时优先将隔离执行适配器放在权限更小的独立执行边界。若只能让 API 进程直接访问 Docker daemon，必须在风险评审后才能启用 `run_command`，阶段7状态继续保持“进行中”。

## 同步执行和事务边界

```text
阶段3或内部测试提交 ToolExecutionRequest
  ↓
Registry 查找固定版本 + Pydantic 参数校验
  ↓
关系与 Workspace 校验
  ↓
PermissionPolicy 计算并保存决定
  ↓ commit：先留下 requested/denied 事实
ExecutionBackend 执行（不持有数据库事务）
  ↓
结果校验、脱敏、截断和 Artifact 保存
  ↓ commit
更新 llm_tool_calls 为 completed/failed/timed_out/outcome_unknown
  ↓
返回 ToolExecutionResult
```

- 权限事实无法提交时不执行工具，避免无审计副作用。
- 工具执行期间不持有请求数据库事务或连接。
- 只读工具在结果保存失败后可以使用相同幂等键安全重建；写入和执行工具不能盲目重试。
- 写入或命令已经产生副作用，但结果保存失败时标记 `outcome_unknown`，交给阶段3决定重新调查或请求用户确认。
- 同一 `(model_attempt_id, provider_tool_call_id)` 只能创建一个逻辑 Tool Call；重复收到时返回已有终态，运行中或未知状态不重复执行。
- 当前同步模式下请求取消会传播到执行后端，但只能在安全点终止；已经完成的文件写入或命令副作用不会自动回滚。

## 状态与错误合同

建议 Tool Call 状态固定为：

```text
requested
permission_denied
approval_required
running
completed
failed
timed_out
cancelled
outcome_unknown
```

稳定错误至少区分：

```text
tool_not_registered
tool_version_not_available
invalid_tool_arguments
tool_permission_denied
tool_approval_required
workspace_not_found
workspace_path_denied
workspace_changed
tool_dependency_unavailable
tool_execution_timeout
tool_output_limit_exceeded
tool_process_failed
tool_result_persistence_failed
tool_execution_outcome_unknown
```

- 输入、权限和路径错误不可重试。
- `rg`、Git、Docker 等依赖缺失返回 `tool_dependency_unavailable`，不能伪装成空结果。
- timeout 不等于进程一定没有产生副作用；写/执行工具超时后根据执行后端确认结果决定 `timed_out` 或 `outcome_unknown`。
- 对外错误不包含宿主绝对路径、命令环境、容器 ID、密钥或完整 stderr；内部审计保存脱敏和有界证据。

## 数据模型调整规划

现有 `llm_tool_calls` 保留，不创建第二张含义相同的工具调用表。实施迁移前先建立公共状态和兼容测试，拟补充：

```text
provider_tool_call_id
task_id（如确认查询和归属需要直接外键）
tool_version
idempotency_key
required_capabilities_json
policy_version
permission_reason_code
output_preview
output_content_hash
result_artifact_id
exit_code
duration_ms
is_output_truncated
error_code
updated_at
```

约束建议：

- `(attempt_id, provider_tool_call_id)` 唯一；若供应商没有稳定 ID，阶段3生成并持久化确定性 ID。
- `result_artifact_id` 引用现有 Artifact；`result_uri` 暂时保留兼容读取，确认无调用方后再迁移，不能直接删除。
- `input_json` 与输出摘要写入前进行递归脱敏和大小限制；大型结果只保存 Artifact。
- 风险和状态改为应用枚举并由数据库约束；旧字符串数据迁移必须有映射和未知值停止条件。

## 项目目录规划

可复用工具合同和纯执行逻辑放入独立包，不继续堆入 FastAPI Router：

```text
packages/tools/
├── pyproject.toml
├── src/nexuspilot_tools/
│   ├── contracts.py
│   ├── registry.py
│   ├── permission_policy.py
│   ├── workspace_policy.py
│   ├── execution_service.py
│   ├── execution_backends.py
│   └── tools/
│       ├── file_tools.py
│       ├── text_search_tool.py
│       ├── command_tool.py
│       └── git_tools.py
└── tests/

apps/api/src/nexuspilot_api/features/tool_runtime/
├── dependencies.py
├── tool_call_repository.py
└── tool_audit_service.py
```

- `packages/tools` 不依赖 FastAPI、SQLAlchemy、MySQL 或具体模型 Provider。
- API feature 只负责数据库关系、事务、审计持久化和依赖装配，不重新实现路径或权限规则。
- 暂不为每个工具创建独立 Service/Router；同类工具放在职责明确的文件中，避免产生大量只有一个薄函数的文件。
- 阶段3只依赖 `ToolExecutionService`，不直接导入具体工具实现。

## HTTP 接口边界

- 阶段7不增加普通用户 `POST /tools/execute`，尤其不公开 `run_command`。
- 现有 `GET /api/v1/internal/tool-calls` 与详情继续作为审计读取入口，并按新增状态和错误字段扩展脱敏响应。
- 可以增加只读 `GET /api/v1/internal/tools`，用于开发和运维查看当前 Registry、版本、能力和启用状态；它不是模型授权来源。
- 工具实际执行通过内部 Python Service 调用。若未来 Worker 跨进程部署需要 RPC，再根据真实部署边界单独规划认证、超时和幂等，不在阶段7预建空接口。

## 初始资源上限建议

以下是编码时的测试基线，不是不可调整的产品承诺；全部进入带单位的配置并设置硬上限：

| 资源 | 建议初值 | 硬边界目的 |
|---|---:|---|
| 单次目录返回 | 500 项 | 防止超大目录耗尽上下文 |
| 目录递归深度 | 8 层 | 防止无界遍历 |
| 单次文件读取 | 1 MiB | 防止读取大型或生成文件 |
| 单次写入文本 | 1 MiB | 防止模型写入异常大文件 |
| 搜索匹配 | 500 条 | 保持证据可消费 |
| 搜索/命令预览 | stdout、stderr 各 256 KiB | 防止响应和内存无界增长 |
| 搜索超时 | 30 秒 | 限制代码库扫描时间 |
| 普通命令默认超时 | 120 秒 | 防止请求无限占用进程 |
| 单命令最大超时 | 600 秒 | 与当前同步执行边界一致 |
| 每 Run 并发只读工具 | 4 | 避免文件句柄和 CPU 突发 |
| 每 Workspace 并发写工具 | 1 | 防止文件竞争和补丁交错 |

超过预览但未超过 Artifact 上限时可以保存完整结果 Artifact；超过执行后端硬输出上限时终止或丢弃后续输出并返回明确截断事实，不能静默返回看似完整的内容。

## 测试设计

权限、安全、状态、幂等和公共合同必须测试先行。

### 合同和 Registry

- 重复名称/版本、未知工具、未知版本、额外字段、非法参数、过大输入和输出 Schema 不匹配。
- Provider `ToolDefinition` 与 Pydantic 输入模型保持一致，不存在第二份漂移 Schema。
- 未授权工具不会出现在提供给模型的工具列表中。

### 路径和文件

- 正常相对路径、`..` 越界、绝对路径、符号链接到外部、父目录符号链接替换、隐藏凭据目录、NUL 二进制和超大文件。
- 写入成功、目标已变化、临时文件失败、原子替换失败、磁盘满和取消。
- 两个并发写请求不能交错；读写竞争返回稳定冲突或串行结果。

### 搜索和 Git

- 搜索正常、零匹配、非法表达式、结果上限、超长行、超时、`rg` 缺失和含连字符查询。
- 明确传入 `--no-config`，用户级 ripgrep 配置不能扩大搜索范围。
- Git status Porcelain v2 解析文件名边界；Git diff 禁止外部驱动、二进制大输出和仓库外路径。

### 命令和隔离

- 允许 argv、未允许 executable、Shell 元字符作为普通参数、工作目录越界、环境变量过滤、网络拒绝和容器无权读取宿主目录。
- stdout/stderr 同时大量输出不会死锁；超时终止整个进程组；取消、非零退出、信号退出和容器清理均留下事实。
- Docker CPU、内存和进程限制通过真实基础设施测试确认；只检查启动参数不算隔离验证。

### 审计和恢复

- 权限事实提交失败时工具不执行；重复 provider Tool Call 不重复执行。
- 完成后数据库写入失败：只读工具可重建，写/执行工具进入 `outcome_unknown`。
- Artifact 上传成功但数据库绑定失败会留下可识别孤儿对象，并有受控清理或补偿测试。
- 输入、输出、日志、错误和审计查询均不泄露 API Key、Provider 凭据、Cookie、绝对宿主路径和环境值。

## 实施步骤

| 步骤 | 修改对象 | 预期结果 | 验证方式 |
|---|---|---|---|
| 1 | 合同、状态、Registry 和权限测试 | 固定工具语义、授权输入和稳定错误 | 参数化单元测试、模型合同兼容测试 |
| 2 | `packages/tools` 最小骨架 | 可注册和执行纯测试工具，不依赖 FastAPI/数据库 | 包测试、架构依赖测试、Ruff |
| 3 | Workspace/path policy | 所有工具共享唯一规范路径边界 | 临时目录、符号链接和竞争测试 |
| 4 | 只读文件、搜索和 Git 工具 | 返回有界、结构化、可复核证据 | 文件集成、ripgrep/Git 真实进程测试 |
| 5 | Tool Call 迁移和 API 持久化适配 | 权限前事实、终态、幂等和 Artifact 可查询 | MySQL 迁移、事务和内部审计测试 |
| 6 | 写文件和补丁 | 只修改隔离可写 Workspace，失败不留下部分补丁 | 原子写、并发、补丁预检和 Git diff 测试 |
| 7 | 隔离执行后端与 `run_command` | 结构化 argv 在无网络、受限资源环境中执行 | Docker 基础设施、安全逃逸和资源测试 |
| 8 | 同步装配与取消 | 内部调用可执行工具，断开/取消不伪造完成 | ASGI/Service 集成和进程清理测试 |
| 9 | 文档与阶段验收 | 限制、未支持能力和运行方式与代码一致 | 完整快速测试、MySQL、MinIO、Docker 验证 |

## 失败与恢复设计

- 读取工具失败不写业务结果；保留已提交的失败 Tool Call 事实。
- 写入工具采用单 Workspace 互斥和原子文件替换；多文件补丁在预检全部目标后执行，执行中异常必须报告已修改文件，不能声称全部回滚。
- 命令调用不自动重试。只有错误发生在进程启动前且确定没有执行时，调用方才可使用同一逻辑 Tool Call 重新尝试。
- 同步请求断开后主动取消在途工具；若无法确认副作用，记录 `outcome_unknown`。
- API 重启不会自动恢复阶段7 Tool Call；运行中遗留记录由启动审计标记为未知结果，等待阶段3重新调查或人工处理。

## 风险与停止条件

- 没有隔离执行后端时，可以完成只读与受控文件工具，但不能启用 `run_command`，阶段7不能标记“已完成”。
- 无法可靠约束 Workspace root 或符号链接时，停止文件写入和命令执行，只保留安全的合同/Registry 工作。
- 需要网络或外部副作用时，停止并单独确定域名、凭据、幂等和用户批准；阶段7默认拒绝。
- 要求从普通用户 HTTP 请求直接执行命令时，停止并先完成阶段5身份、授权、限流以及部署隔离评审。
- Docker 只能以 privileged、host network、host PID 或挂载 Docker Socket 到执行容器的方式运行时，拒绝启用命令工具。

## 完成标准

- 工具注册、输入/输出 Schema、权限和错误合同稳定，未知工具或非法输入不会进入执行器。
- `list_files`、`read_file`、`search_text`、`git_status`、`git_diff`、`write_file`、`apply_patch` 和受限 `run_command` 均通过对应安全与失败测试。
- 读取、写入和执行都不能越过绑定 Workspace；原始仓库写入、网络和外部副作用默认拒绝。
- 写入和命令执行只在通过真实验证的隔离环境运行；超时、资源和输出均有硬上限。
- 每次允许、拒绝、失败、超时、取消和未知结果都形成可查询、脱敏的 `llm_tool_calls` 事实。
- 重复 Tool Call 不重复产生副作用；无法确认结果时不会自动重试或伪造失败/成功。
- 阶段7能力可由同步内部 Service 独立调用，不依赖 RabbitMQ、Worker 或 Agent 循环。
- 快速测试、真实 MySQL/MinIO、ripgrep/Git 进程测试和 Docker 隔离测试全部通过后，阶段状态才能改为“已完成”。

## 官方依据

- [Python asyncio subprocess](https://docs.python.org/3/library/asyncio-subprocess.html)：确认参数化异步进程接口、超时需要由 `asyncio.wait_for()` 控制，以及管道输出必须持续读取以避免死锁。
- [Python subprocess security considerations](https://docs.python.org/3/library/subprocess.html#security-considerations)：确认 Shell 调用的转义责任；本阶段因此默认使用参数数组和 `shell=False`。
- [Docker Engine security](https://docs.docker.com/engine/security/)：确认 daemon 权限边界、非特权用户、Linux capabilities 和容器隔离风险。
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)：确认容器默认没有 CPU/内存限制，阶段7必须显式设置资源上限。
- [Docker user namespace isolation](https://docs.docker.com/engine/security/userns-remap/)：确认非特权用户和 user namespace 的隔离作用及 daemon 仍可能具有高权限的边界。
- [ripgrep User Guide](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md)：确认默认忽略、符号链接和 `--no-config` 等搜索行为。
- [Git status Porcelain v2](https://git-scm.com/docs/git-status.html#_porcelain_format_version_2)：确认供程序解析的稳定状态格式。
- [Git diff](https://git-scm.com/docs/git-diff)：确认差异命令能力；阶段7只开放固定参数子集并禁用外部 diff 驱动。
