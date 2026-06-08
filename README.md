# sglang_ops_stack

sglang 推理引擎一键安装、部署、监控、运维平台。

## Phase 1 MVP

当前实现范围：

- FastAPI + SQLAlchemy + SQLite 最小可运行骨架。
- Dashboard 首页、结构化健康检查、结构化日志基础与安全配置入口。
- Alembic 初始迁移基线，覆盖当前 Host / Job / JobLog 表结构。
- Host 主机资产 CRUD API 与页面。
- 会话级 SSH 密码输入；密码仅用于本次 SSH 检查，不写入数据库。
- Paramiko-backed `SSHExecutor` 抽象，返回统一命令执行结果。
- `ssh_connect_check` Job：固定执行 `whoami`、`uname -a`、GPU 探测白名单命令。
- Job / JobLog API 与页面展示。
- JobLog、Job error message、API/HTML 输出敏感信息脱敏。

非目标：长期凭据存储、密钥认证、认证/RBAC、Celery/Redis Worker、Docker/NVIDIA/SGLang 安装部署。

## 安装与启动

```bash
python -m pip install -e .[dev]
python -m uvicorn sglang_ops_stack.main:app --reload
```

默认数据库：`sqlite:///./sglang_ops_stack.db`。可通过 `.env` 或环境变量覆盖：

```bash
SGLANG_OPS_DATABASE_URL=sqlite:///./sglang_ops_stack.db
```

打开页面：

- Dashboard：<http://127.0.0.1:8000/>
- Host 列表：<http://127.0.0.1:8000/hosts>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

## 数据库迁移

Alembic 已作为数据库迁移基础设施引入，初始 revision 对齐当前 SQLAlchemy models（Host / Job / JobLog）：

```bash
alembic upgrade head
```

MVP/development 路径中，`create_app()` 仍保留 `Base.metadata.create_all(bind=engine)` 作为本地启动和测试兜底；正式 schema 演进应优先通过 Alembic revision 管理。

## 测试与质量检查

```bash
python -m pytest
python -m ruff check .
python -m mypy src
```

自动化测试使用 fake/mocked executor，不依赖真实 SSH 主机。真实 SSH 连通性检查需要目标主机可达并允许密码认证。

## 安全说明

Host、Job、JobLog 表均不包含 password、private_key、token 等凭据字段。连接测试密码只通过请求进入执行上下文；日志、错误信息和页面展示写入/渲染前会进行脱敏。
