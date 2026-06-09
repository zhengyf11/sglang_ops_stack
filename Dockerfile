FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "sglang_ops_stack.main:app", "--host", "0.0.0.0", "--port", "8000"]
