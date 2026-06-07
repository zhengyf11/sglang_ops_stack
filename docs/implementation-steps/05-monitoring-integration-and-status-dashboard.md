# Step 05：监控集成与状态面板

## 目标

集成 Prometheus/Grafana 地址配置，展示每个 SGLang 部署的 `/metrics` target，提供 Grafana/Prometheus 跳转，并将 metrics 可达性纳入部署健康状态。

## 前置条件

- Step 03 已完成，部署详情与 HealthService 可用。
- Step 04 已完成或至少已有可刷新状态的部署详情页。
- SGLang 服务暴露 `/metrics`。

## 涉及目录与文件

建议新增或修改：

```text
src/sglang_ops_stack/models/monitoring.py
src/sglang_ops_stack/schemas/monitoring.py
src/sglang_ops_stack/services/monitoring_service.py
src/sglang_ops_stack/services/health_service.py
src/sglang_ops_stack/api/monitoring.py
src/sglang_ops_stack/web/monitoring.py
src/sglang_ops_stack/web/templates/monitoring/*.html
src/sglang_ops_stack/web/templates/dashboard.html
src/sglang_ops_stack/web/templates/deployments/detail.html
tests/test_monitoring_service.py
tests/test_metrics_health.py
tests/test_prometheus_scrape_config.py
```

## 数据模型

### MonitoringConfig

核心字段：

- `id`
- `grafana_base_url`
- `prometheus_base_url`
- `default_dashboard_path`
- `created_at`
- `updated_at`

### DeploymentMetricsStatus

可作为独立表或健康快照字段：

- `deployment_id`
- `metrics_url`
- `reachable`
- `last_status_code`
- `last_error`
- `checked_at`

## 开发任务拆分

### 1. 实现监控配置页面

- 配置 Grafana Base URL。
- 配置 Prometheus Base URL。
- 配置默认 Dashboard path 或完整 URL 模板。
- 保存前校验 URL 格式。
- 提供测试连接按钮。

### 2. 实现 metrics target 生成

对每个部署生成：

```text
http://<host-or-ip>:<sglang-port>/metrics
```

需要考虑：

- 部署使用 host network 时，target 通常为主机 IP + SGLang port。
- bridge network 场景需要使用映射端口。
- target 应显示在部署详情页，并提供复制按钮。

### 3. 生成 Prometheus scrape config 片段

为用户提供可复制配置：

```yaml
- job_name: sglang-<deployment-name>
  static_configs:
    - targets:
        - <host-ip>:<port>
      labels:
        deployment: <deployment-name>
        host: <host-name>
```

MVP 不自动修改 Prometheus 配置文件，只生成指引。

### 4. 实现 metrics 可达性检查

- HealthService 增加 `/metrics` GET 检查。
- 检查返回 200 且内容包含 Prometheus 文本格式特征时标记 reachable。
- metrics 失败不一定使服务部署失败，但应显示 warning。

### 5. 实现 Grafana 跳转

- 部署详情页展示“打开 Grafana”按钮。
- 支持 URL 模板变量：deployment、host、port、served_model_name。
- 若未配置 Grafana，则展示引导配置入口。

### 6. 实现 Dashboard 状态聚合

Dashboard 建议展示：

- 主机总数。
- 部署总数。
- healthy/warning/failed 部署数量。
- 最近失败 Job。
- metrics 不可达部署列表。
- Grafana/Prometheus 配置状态。

## 页面要求

- 监控配置页简洁明确。
- 部署详情页展示 metrics target、scrape config、Grafana 跳转。
- Dashboard 卡片可快速定位异常部署。
- URL 跳转使用新窗口打开，避免嵌入跨域问题；后续再考虑 iframe。

## 安全要求

- Grafana/Prometheus URL 仅允许 http/https。
- 不允许 javascript/data URL。
- 页面展示外部链接需明确提示跳转目标。
- 不在日志中记录包含 token 的监控 URL；如 URL 含 query token，应脱敏。

## 验收标准

- 可配置 Grafana/Prometheus 地址。
- 可从部署详情页跳转 Grafana。
- 页面展示每个部署的 metrics target。
- 可生成 Prometheus scrape config 片段。
- `/metrics` 可达性纳入健康状态。
- Dashboard 能展示部署健康概览。

## 建议测试

- MonitoringConfig URL 校验测试。
- metrics target 生成测试。
- scrape config 生成测试。
- mock SGLang `/metrics` 可达性测试。
- Dashboard 聚合数据测试。
