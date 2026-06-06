# Step 06：安全、审计与生产化

## 目标

在 MVP 可用能力基础上，补齐平台安全、审计、凭据管理、权限控制、生产任务队列、数据库生产化与质量保障能力，使平台适合受信内网长期运行。

## 前置条件

- Step 00 至 Step 05 的核心功能已完成。
- 已具备主机管理、环境检查、SGLang 部署、运维操作、监控跳转能力。
- 已识别 root 凭据、远程命令、Docker privileged、host network 等高风险点。

## 涉及目录与文件

建议新增或修改：

```text
src/sglang_ops_stack/models/user.py
src/sglang_ops_stack/models/audit.py
src/sglang_ops_stack/models/credential.py
src/sglang_ops_stack/services/auth_service.py
src/sglang_ops_stack/services/audit_service.py
src/sglang_ops_stack/services/credential_service.py
src/sglang_ops_stack/security/encryption.py
src/sglang_ops_stack/security/rbac.py
src/sglang_ops_stack/jobs/worker.py
src/sglang_ops_stack/core/celery_app.py
src/sglang_ops_stack/core/production_config.py
deploy/docker-compose.yml
deploy/README.md
tests/test_auth.py
tests/test_audit.py
tests/test_credentials.py
tests/test_rbac.py
tests/test_production_config.py
```

## 开发任务拆分

### 1. 实现用户登录

MVP 后续增强建议：

- 用户表：username、password_hash、role、is_active。
- 密码使用安全哈希算法。
- 登录后使用 Session 或 JWT。
- 所有危险操作要求登录态。

### 2. 实现 RBAC

建议角色：

| 角色 | 权限 |
|---|---|
| admin | 用户管理、监控配置、所有部署操作 |
| operator | 主机管理、环境检查、部署和运维操作 |
| viewer | 查看主机、部署、日志、监控链接 |

权限检查应在 API route 层统一处理，service 层保留必要的业务校验。

### 3. 实现 AuditLog

审计事件覆盖：

- 新增/修改/删除主机。
- SSH 检查。
- 环境安装。
- 创建部署。
- 重启/停止/启动容器。
- 重新部署。
- 修改监控配置。
- 修改凭据。

审计字段建议：

- actor_user_id。
- action。
- target_type。
- target_id。
- request_id。
- risk_level。
- before/after 摘要。
- created_at。

### 4. 实现凭据加密存储或密钥方式

支持两种模式：

1. 会话级凭据：继续作为默认安全模式。
2. 加密存储：使用本地 master key、KMS 或 Secret Manager 加密。

要求：

- API 永不返回明文 secret。
- 日志永不记录明文 secret。
- 数据库中只保存密文或引用。
- 密钥轮换策略有文档说明。

### 5. 强化日志脱敏

- 所有 JobLog、应用日志、审计摘要经过统一脱敏管道。
- 覆盖 password、token、private key、Authorization header、监控 URL token。
- 增加脱敏回归测试。

### 6. 引入生产任务队列

MVP 的 BackgroundTasks/asyncio 队列不适合长任务生产运行。生产化建议：

- Celery + Redis。
- 或 Dramatiq/RQ。
- Worker 独立进程执行 SSH 长任务。
- Job 状态存数据库。
- Worker 崩溃后任务可标记失败或重试。

### 7. 数据库生产化

- SQLite 保留本地开发用途。
- 生产推荐 PostgreSQL。
- Alembic 迁移必须可重复执行。
- 配置连接池、超时、健康检查。

### 8. 增加部署文档

- `deploy/docker-compose.yml` 提供 Web、Worker、Redis、PostgreSQL 组合。
- `deploy/README.md` 说明环境变量、启动步骤、备份恢复。
- 明确生产访问必须放在受信内网。

### 9. 增加安全测试

覆盖：

- 未登录不能执行危险操作。
- viewer 不能执行部署/重启/安装。
- 日志不出现密码。
- 容器名、镜像、volume、SGLang 参数拒绝 shell payload。
- 外部 URL 拒绝 javascript/data scheme。

## 生产化约束

- 不支持任意 shell 控制台。
- 不默认公开到公网。
- Docker privileged 和 host network 必须有风险提示与审计。
- root 密码优先会话级使用，长期保存必须加密。
- 自动修改 Prometheus 配置不是 MVP 必需项，默认只生成 scrape config。

## 验收标准

- 用户登录和角色权限生效。
- 危险操作有审计记录。
- 凭据可会话级使用，或以密文方式存储。
- 日志无敏感信息。
- 长任务可在 Worker 中稳定执行。
- PostgreSQL + Redis + Worker 部署方案可运行。
- 安全测试通过。

## 建议测试

- Auth/RBAC 接口测试。
- AuditLog 写入测试。
- Credential 加解密和不回显测试。
- 日志脱敏测试。
- Celery/RQ job 状态流转测试。
- docker-compose smoke test。
