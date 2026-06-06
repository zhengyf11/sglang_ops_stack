# Step 04：运维操作与重新部署

## 目标

在已有 SGLang 部署基础上，实现容器启动、停止、重启、查看日志、修改参数后重新部署，以及 DeploymentRevision 历史追踪。

## 前置条件

- Step 03 已完成，Deployment、DeploymentRevision、部署 Job、HealthService 可用。
- 已存在至少一个可管理的部署记录。
- 本步骤仍聚焦单主机单容器部署。

## 涉及目录与文件

建议新增或修改：

```text
src/sglang_ops_stack/services/operation_service.py
src/sglang_ops_stack/services/redeploy_service.py
src/sglang_ops_stack/commands/docker.py
src/sglang_ops_stack/jobs/operation_jobs.py
src/sglang_ops_stack/jobs/redeploy_jobs.py
src/sglang_ops_stack/api/operations.py
src/sglang_ops_stack/web/operations.py
src/sglang_ops_stack/web/templates/deployments/detail.html
src/sglang_ops_stack/web/templates/deployments/redeploy.html
src/sglang_ops_stack/web/templates/deployments/revisions.html
tests/test_operation_service.py
tests/test_redeploy_service.py
tests/test_deployment_revision.py
```

## 开发任务拆分

### 1. 实现 OperationService

提供方法：

- `restart_container(deployment_id)`
- `stop_container(deployment_id)`
- `start_container(deployment_id)`
- `fetch_container_logs(deployment_id, tail=500)`
- `refresh_status(deployment_id)`

所有操作必须写 Job 和审计事件。

### 2. 实现 restart/stop/start Job

重启 Job 建议步骤：

1. 校验 Deployment 存在且具备 container_name。
2. 执行 `docker restart <container>`。
3. 等待容器 Running。
4. 执行 SGLang 分层探活。
5. 更新部署状态。

停止 Job 建议步骤：

1. 执行 `docker stop <container>`。
2. 更新状态为 stopped。
3. 保留最近日志和 revision。

启动 Job 建议步骤：

1. 执行 `docker start <container>`。
2. 如果 SGLang 进程未启动，则按当前 revision 启动 SGLang。
3. 执行健康检查。

### 3. 实现容器日志查看

- 支持 `tail` 参数，但限制最大值。
- 日志返回前必须脱敏。
- 页面支持刷新或流式查看。

### 4. 实现重新部署配置 diff

- 用户进入重新部署页面时加载当前 revision。
- 用户修改镜像、模型路径、端口、SGLang 参数、Docker 参数。
- 提交前展示 diff：旧值、新值、风险级别。
- 高风险字段如 `privileged`、`network host`、volume 变更需二次确认。

### 5. 实现 DeploymentRevision 历史

- 每次部署和重新部署都创建不可变 revision snapshot。
- 记录 revision_no、配置快照、修改摘要、操作者、时间。
- 部署详情页可查看 revision 列表和配置详情。

### 6. 实现重新部署 Job

MVP 推荐 stop-and-replace：

1. 创建 N+1 revision。
2. 校验新配置。
3. 预览命令并确认。
4. 停止旧容器或删除旧容器。
5. 用新配置启动容器和 SGLang。
6. 执行分层探活。
7. 成功则 current_revision 指向 N+1。
8. 失败则保留失败日志和旧 revision 信息。

自动回滚可在生产化阶段完善；MVP 至少提供旧版本查看和手动回滚入口。

## 页面要求

- 部署详情页提供：重启、停止、启动、重新部署、查看日志按钮。
- 重新部署页面提供配置 diff。
- Revision 历史页支持查看每次配置快照。
- 操作按钮必须显示当前状态下是否可用，例如 stopped 状态不可重复 stop。

## 安全要求

- 运维操作必须确认目标 deployment 与 container_name 来自数据库记录，不能来自任意用户输入。
- 不允许 Web 页面提供任意 shell 控制台。
- 高风险操作必须写审计日志。
- 日志输出必须脱敏。

## 验收标准

- 可重启容器并自动重新探活。
- 可停止和启动容器。
- 可查看脱敏后的容器日志。
- 可修改参数并重新部署。
- 可查看配置版本历史和 diff。
- 重新部署失败时日志和错误原因清晰，并可查看旧版本配置。

## 建议测试

- OperationService 状态流转测试。
- fake SSH 模拟 docker restart/stop/start。
- redeploy diff 生成测试。
- revision 快照不可变测试。
- 高风险参数确认逻辑测试。
