# Step 03：SGLang 部署与分层探活

## 目标

实现从 Web 页面填写部署配置，到拉取 SGLang Docker 镜像、启动容器、启动 SGLang 服务、执行分层探活并展示部署详情的完整闭环。

## 前置条件

- Step 02 已完成，目标主机环境检查达到 READY 或用户确认可部署。
- SSHExecutor、Job、JobLog、CommandSpec 已可用。
- 当前步骤只支持单主机单容器部署，后续再扩展多机、多副本、多集群。

## 涉及目录与文件

建议新增或修改：

```text
src/sglang_ops_stack/models/deployment.py
src/sglang_ops_stack/models/deployment_revision.py
src/sglang_ops_stack/schemas/deployment.py
src/sglang_ops_stack/services/deployment_service.py
src/sglang_ops_stack/services/health_service.py
src/sglang_ops_stack/commands/docker.py
src/sglang_ops_stack/commands/sglang.py
src/sglang_ops_stack/clients/sglang_http.py
src/sglang_ops_stack/jobs/deployment_jobs.py
src/sglang_ops_stack/api/deployments.py
src/sglang_ops_stack/web/deployments.py
src/sglang_ops_stack/web/templates/deployments/*.html
tests/test_docker_command_builder.py
tests/test_sglang_command_builder.py
tests/test_deployment_jobs.py
tests/test_health_service.py
```

## 数据模型

### Deployment

核心字段：

- `id`
- `host_id`
- `name`
- `container_name`
- `image`
- `model_path`
- `served_model_name`
- `host`
- `port`
- `tp_size`
- `dp_size`
- `pp_size`
- `mem_fraction_static`
- `docker_options`
- `sglang_options`
- `status`
- `service_url`
- `current_revision_id`

### DeploymentRevision

核心字段：

- `id`
- `deployment_id`
- `revision_no`
- `config_snapshot`
- `change_summary`
- `created_by`
- `created_at`

## 开发任务拆分

### 1. 实现部署配置表单

- Docker 镜像：默认可参考 `lmsysorg/sglang:latest`。
- 容器名：默认 `sglang`，同一主机内必须唯一。
- 端口：默认 SGLang API 端口，可配置。
- 模型路径、served model name。
- TP/DP/PP、显存比例、host、port 等常用参数。
- 高级参数必须白名单化。

### 2. 实现参数校验

- 容器名只允许安全字符。
- 镜像名符合 Docker image reference 格式。
- 端口范围合法，并检查目标主机端口占用。
- volume 必须拆分为 host_path/container_path/mode，不允许整段 `-v` 字符串。
- SGLang 参数只能来自白名单。

### 3. 实现命令预览

- 生成 Docker pull/run/exec 的审计渲染文本。
- 页面显示风险提示：`--privileged`、`--network host`、root 用户、GPU all。
- 命令预览必须脱敏，不显示凭据。

### 4. 实现 Docker 命令构建

至少覆盖：

- `docker pull <image>`
- `docker rm -f <container>`，仅在确认重新部署或覆盖时使用。
- `docker run -d ... tail -f /dev/null`
- `docker ps`
- `docker inspect`
- `docker logs`

固定参数可参考架构文档，但要保留配置化能力。

### 5. 实现 SGLang 启动命令构建

推荐优先使用：

```text
sglang serve <model_path> --host <host> --port <port> ...
```

兼容：

```text
python3 -m sglang.launch_server --model-path <model_path> ...
```

启动方式建议 MVP 采用两阶段：

1. 先启动常驻容器。
2. 再 `docker exec -d` 启动 SGLang 服务。

### 6. 实现部署 Job

Job 步骤建议：

1. 校验主机环境 READY。
2. 检查端口占用。
3. 拉取镜像。
4. 创建容器。
5. 启动 SGLang 服务。
6. 等待端口监听。
7. 调用 `/health`。
8. 调用 `/model_info` 或 `/server_info`。
9. 更新 Deployment 状态。

### 7. 实现 SGLang HTTP Client

- `/health`
- `/health_generate`，可作为深层探活，MVP 可手动触发。
- `/model_info`
- `/server_info`
- `/get_load`
- `/metrics` 可达性留给 Step 05 深化。

### 8. 实现部署详情页

- 展示服务 URL。
- 展示容器状态。
- 展示 SGLang HTTP 状态。
- 展示最近一次部署 Job 日志。
- 展示当前配置和 revision。

## 分层探活定义

建议状态层级：

1. Host reachable。
2. Docker ready。
3. Container running。
4. SGLang process exists。
5. SGLang HTTP `/health` OK。
6. Optional：`/health_generate` OK。
7. Optional：`/metrics` reachable。

不能只依赖容器 Running 判断部署成功。

## 验收标准

- 可在页面创建部署配置。
- 可预览部署命令和风险提示。
- 可拉取镜像并启动容器。
- 可启动 SGLang 服务。
- 部署详情页展示服务 URL、状态、日志和配置。
- `/health` 成功后 Deployment 状态变为 healthy/running。
- 失败时保留日志和错误原因。

## 建议测试

- DockerCommandBuilder 注入攻击测试。
- SGLangCommandBuilder 参数白名单测试。
- fake SSH 模拟 docker pull/run/exec 成功和失败。
- mock SGLang HTTP server 测试 HealthService。
- 部署表单校验测试。
