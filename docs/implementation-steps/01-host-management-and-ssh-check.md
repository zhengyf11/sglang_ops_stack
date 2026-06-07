# Step 01：主机管理与 SSH 连通性检查

## 目标

实现 GPU 主机录入、查看、编辑、删除，以及基于会话级凭据的 SSH 连通性检查。该步骤为后续环境检查、Docker 安装和 SGLang 部署提供主机资产与远程执行入口。

## 前置条件

- Step 00 已完成，FastAPI、数据库、页面、测试框架可用。
- 已有基础 Job 模型或可在本步骤补齐最小 Job 状态模型。
- 本步骤只做连通性检查和基础主机探测，不安装软件，不启动容器。

## 涉及目录与文件

建议新增或修改：

```text
src/sglang_ops_stack/models/host.py
src/sglang_ops_stack/models/job.py
src/sglang_ops_stack/schemas/host.py
src/sglang_ops_stack/schemas/job.py
src/sglang_ops_stack/services/host_service.py
src/sglang_ops_stack/services/job_service.py
src/sglang_ops_stack/executors/ssh_executor.py
src/sglang_ops_stack/security/masking.py
src/sglang_ops_stack/api/hosts.py
src/sglang_ops_stack/web/hosts.py
src/sglang_ops_stack/web/templates/hosts/*.html
tests/test_hosts.py
tests/test_ssh_executor.py
tests/test_sensitive_masking.py
```

## 数据模型

### Host

核心字段：

- `id`
- `name`
- `ip`
- `ssh_port`
- `ssh_user`
- `auth_type`
- `tags`
- `note`
- `os_info`
- `gpu_info`
- `last_check_status`
- `last_check_at`
- `created_at`
- `updated_at`

### Job

核心字段：

- `id`
- `type`：如 `ssh_connect_check`
- `status`：pending/running/succeeded/failed/canceled
- `target_type`：host/deployment
- `target_id`
- `started_at`
- `finished_at`
- `error_code`
- `error_message`

### JobLog

核心字段：

- `id`
- `job_id`
- `level`
- `message`
- `created_at`

日志写入前必须脱敏。

## 开发任务拆分

### 1. 实现 Host CRUD

- 新增主机列表页。
- 新增主机表单页或弹窗。
- 支持 IP、端口、SSH 用户、备注、标签录入。
- 对 IP/端口做基础校验。
- 暂不保存 root 密码。

### 2. 实现会话级凭据输入

- 在“测试连接”动作中要求输入密码或密钥。
- 密码只进入内存中的执行上下文，不写入 Host 表。
- API 响应和日志中禁止回显密码。

### 3. 实现 SSHExecutor 最小能力

- 支持基于 host、port、username、password 的远程命令执行。
- 提供统一结果模型：exit_code、stdout、stderr、timed_out、started_at、finished_at。
- 支持连接超时、命令超时。
- 后续可替换为 AsyncSSH 或 Paramiko；接口要稳定。

### 4. 实现连接测试 Job

- Job 步骤建议：连接建立、执行 `whoami`、执行 `uname -a`、执行基础 GPU 探测命令可选。
- 每一步写入 JobLog。
- 失败时记录错误码，不暴露敏感连接信息。

### 5. 实现日志展示

- 主机详情页显示最近一次检查状态。
- Job 详情页显示实时或准实时日志。
- MVP 可先轮询，后续再切换 SSE。

### 6. 增加脱敏工具

- 提供 `mask_secret(text, secrets)`。
- 对 stdout、stderr、异常文本、JobLog message 统一脱敏。
- 覆盖 root 密码、token、私钥片段。

## 安全要求

- 密码不落库。
- 密码不写日志。
- 不允许用户输入任意 shell。
- SSH 检查命令固定为白名单命令。
- 错误信息对用户可读，但不能包含完整凭据。

## 验收标准

- 页面可新增、查看、编辑、删除主机。
- 用户可输入 root 密码发起 SSH 测试。
- SSH 测试成功时页面显示 succeeded 和基础主机信息。
- SSH 测试失败时页面显示 failed 和可理解错误原因。
- 数据库中不存在明文 root 密码。
- JobLog 中不存在用户输入的密码。

## 建议测试

- Host CRUD 单元/接口测试。
- SSHExecutor 使用 fake server 或 mock executor 测试成功/失败/超时。
- 日志脱敏测试。
- 表单校验测试。
