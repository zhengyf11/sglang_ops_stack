# Step 02：环境检查与安装状态机

## 目标

实现目标 GPU 主机的环境检查与受控安装状态机，覆盖 OS、NVIDIA GPU、Driver、Docker、NVIDIA Container Toolkit、Docker runtime 等 SGLang 容器运行前置条件。

本步骤先聚焦受信内网 Ubuntu LTS 主机，安装能力以可恢复、可重试、幂等为核心，不写成一次性不可拆分脚本。

## 前置条件

- Step 01 已完成，主机管理、会话级凭据、SSHExecutor、Job/JobLog 可用。
- 目标主机可以通过 SSH 连接。
- 本步骤不启动 SGLang 容器，只检查和准备运行环境。

## 涉及目录与文件

建议新增或修改：

```text
src/sglang_ops_stack/models/environment.py
src/sglang_ops_stack/schemas/environment.py
src/sglang_ops_stack/services/environment_service.py
src/sglang_ops_stack/commands/base.py
src/sglang_ops_stack/commands/host_check.py
src/sglang_ops_stack/commands/install_steps.py
src/sglang_ops_stack/jobs/environment_jobs.py
src/sglang_ops_stack/api/environment.py
src/sglang_ops_stack/web/environment.py
src/sglang_ops_stack/web/templates/environment/*.html
tests/test_host_check_command_builder.py
tests/test_environment_state_machine.py
tests/test_environment_jobs.py
```

## 核心状态机

建议状态：

```text
SSH_CHECK
OS_DETECT
GPU_DETECT
DRIVER_CHECK
DRIVER_INSTALL
REBOOT_REQUIRED
DOCKER_CHECK
DOCKER_INSTALL
NVIDIA_TOOLKIT_CHECK
NVIDIA_TOOLKIT_INSTALL
DOCKER_RUNTIME_CONFIG
RUNTIME_VERIFY
READY
FAILED
```

每个状态必须包含：

- check command。
- success condition。
- repair/install command，可选。
- retry policy。
- failure code。
- human readable message。

## 开发任务拆分

### 1. 实现 CommandSpec 模型

- 定义命令名称、参数列表、超时时间、是否 sudo、脱敏字段、审计渲染文本。
- 禁止直接把用户输入拼接成 shell 字符串。
- 所有命令通过白名单 builder 生成。

### 2. 实现 HostCheckCommandBuilder

覆盖检查项：

- OS：`lsb_release` 或 `/etc/os-release`。
- Kernel：`uname -r`。
- GPU：`nvidia-smi` 或 `lspci | grep -i nvidia`。
- Driver：`nvidia-smi` 返回码与版本解析。
- Docker：`docker --version`、`docker info`。
- NVIDIA runtime：`nvidia-ctk --version`、`docker info` runtime 字段。
- Container GPU 验证：`docker run --rm --gpus all ... nvidia-smi`。

### 3. 实现环境检查 Job

- 从 Host 详情页发起环境检查。
- 按状态机顺序执行 check。
- 每个步骤写入结构化结果。
- 页面分层展示：系统、GPU、Driver、Docker、NVIDIA runtime、容器 GPU 测试。

### 4. 实现安装步骤

MVP 支持范围：

- Ubuntu LTS。
- Docker Engine 安装。
- NVIDIA Container Toolkit 安装。
- Docker runtime 配置。

Driver 安装可先做策略化处理：

- 若目标机器无 Driver，则提示风险和需要确认。
- MVP 可以先生成安装计划与命令预览，待用户确认后执行。
- 检测到需要重启时进入 `REBOOT_REQUIRED`。

### 5. 实现幂等与恢复

- 执行安装前先检查当前状态。
- 已满足条件的步骤直接标记 skipped/succeeded。
- Job 失败后可重试，从最近检查状态继续。
- 重启场景下允许用户手动重启后点击“继续检查”。

### 6. 实现错误码体系

建议错误码示例：

- `SSH_CONNECT_FAILED`
- `UNSUPPORTED_OS`
- `NO_NVIDIA_GPU`
- `DRIVER_NOT_READY`
- `DOCKER_NOT_INSTALLED`
- `NVIDIA_RUNTIME_MISSING`
- `RUNTIME_VERIFY_FAILED`
- `COMMAND_TIMEOUT`

## 页面要求

- 主机详情页显示环境状态卡片。
- 环境检查页显示步骤树或时间线。
- 每个步骤可展开查看脱敏后的 stdout/stderr。
- 安装前显示风险提示和确认按钮。

## 安全要求

- 安装命令必须由平台固定生成。
- 不提供任意 shell 输入框。
- 使用 root 执行时必须记录审计事件。
- 安装日志脱敏。
- Docker runtime 修改属于高风险操作，需要页面确认。

## 验收标准

- 可对目标主机执行环境检查。
- 检查结果按层级展示。
- 已安装组件不会重复安装。
- Docker/NVIDIA Toolkit 安装步骤可执行或可生成明确安装计划。
- 失败时有错误码、错误原因和重试入口。
- 日志不包含 root 密码或 token。

## 建议测试

- CommandSpec 渲染与脱敏测试。
- HostCheckCommandBuilder 参数安全测试。
- 使用 fake SSH executor 模拟各类命令输出。
- 状态机成功、失败、跳过、重试测试。
- 页面状态展示测试。
