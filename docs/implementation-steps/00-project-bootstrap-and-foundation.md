# Step 00：项目初始化与基础骨架

## 目标

建立一个可本地启动、可持续扩展的 Python 3.11+ Web 项目骨架，为后续主机管理、远程执行、部署编排、状态监控等能力提供统一工程基础。

本步骤完成后，项目应具备：

- FastAPI 应用入口。
- 基础配置管理。
- 结构化日志。
- SQLAlchemy + Alembic 数据库基础设施。
- Jinja2/HTMX 页面基础布局。
- 健康检查接口和 Dashboard 空页面。
- 可被后续步骤复用的目录结构与测试框架。

## 前置条件

- 已阅读 `docs/architecture-and-technical-plan.md`。
- 当前仓库尚未包含业务代码，允许创建 Python Web 项目基础结构。
- Python 版本要求为 3.11+。
- 本步骤不接入真实 GPU 机器，不执行远程 SSH 命令。

## 涉及目录与文件

建议新增：

```text
pyproject.toml
README.md
alembic.ini
src/sglang_ops_stack/
  __init__.py
  main.py
  core/
    __init__.py
    config.py
    logging.py
    security.py
  db/
    __init__.py
    base.py
    session.py
  web/
    __init__.py
    routes.py
    templates/
      base.html
      dashboard.html
    static/
      app.css
  api/
    __init__.py
    routes.py
    health.py
  jobs/
    __init__.py
    models.py
  services/
    __init__.py
tests/
  conftest.py
  test_health.py
alembic/
  env.py
  versions/
```

## 开发任务拆分

### 1. 初始化依赖与项目元数据

- 创建 `pyproject.toml`。
- 配置项目名称、Python 版本、包发现规则。
- 引入运行依赖：FastAPI、Uvicorn、Pydantic Settings、SQLAlchemy、Alembic、Jinja2。
- 引入开发依赖：pytest、pytest-asyncio、httpx、ruff、mypy。
- 配置格式化、lint、测试命令。

### 2. 创建应用入口

- 在 `src/sglang_ops_stack/main.py` 中提供 `create_app()`。
- 注册 API 路由和 Web 路由。
- 配置静态文件与模板目录。
- 暴露 `app = create_app()` 供 Uvicorn 使用。

### 3. 创建配置模块

- 使用 `pydantic-settings` 定义 Settings。
- 支持环境变量覆盖：应用名称、环境、数据库 URL、日志级别、Secret Key。
- 默认使用 SQLite 本地文件，便于 MVP 开发。
- 避免把敏感默认值写死在代码中。

### 4. 创建数据库基础设施

- 定义 SQLAlchemy `Base`。
- 创建同步或异步 session 工厂；MVP 可先使用同步 SQLAlchemy。
- 配置 Alembic 迁移环境。
- 预留未来切换 PostgreSQL 的能力。

### 5. 创建基础页面

- 创建 `base.html`，包含导航、主内容区域、消息提示区域。
- 创建 `dashboard.html`，先展示空状态卡片：主机数量、部署数量、运行任务数量、告警数量。
- 选择 HTMX 时，应保留局部刷新区域规范。

### 6. 创建健康检查接口

- 提供 `GET /api/health`，返回应用状态、版本、数据库连接状态。
- 提供 Web 首页 `/`，渲染 Dashboard。
- 保持接口输出简单稳定，作为后续 smoke test 基础。

### 7. 建立测试框架

- 使用 FastAPI TestClient 或 HTTPX AsyncClient。
- 添加健康检查接口测试。
- 添加首页渲染测试。
- 添加配置加载测试。

## 关键设计约束

- 不在本步骤引入远程执行、Docker 操作、SGLang 命令拼接等业务逻辑。
- 所有后续业务模块应通过 service 层接入，而不是直接写在 route 中。
- API route 与 Web route 分离，避免页面渲染逻辑污染业务服务。
- 配置、日志、数据库初始化必须集中在 core/db 模块，避免散落。

## 验收标准

- `uvicorn sglang_ops_stack.main:app --reload` 可启动。
- 浏览器可打开 `/`。
- `GET /api/health` 返回 200。
- `/docs` OpenAPI 页面可访问。
- Alembic 能执行 `upgrade head`。
- 测试命令通过。

## 建议测试命令

```bash
python -m pytest
python -m ruff check .
python -m mypy src
```

## 交付物

- 可运行的 Python Web 项目骨架。
- 初始数据库迁移框架。
- 最小健康检查与 Dashboard 页面。
- 基础自动化测试。
