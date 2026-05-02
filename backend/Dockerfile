FROM python:3.12-slim

WORKDIR /app

# 安装 uv
RUN pip install uv --no-cache-dir

# 先复制依赖声明，利用 Docker 层缓存
COPY pyproject.toml .

# 安装生产依赖（不含 dev）
RUN uv sync --no-dev

# 复制源码和迁移文件
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "quantpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
