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

# 部署版本戳。仓库里恒为 `unknown`（有测试钉死，防止有人把某个 sha 提交进去变成
# 永久过期的假版本）；`scripts/deploy_prod.sh` 在服务器上 build 之前把它覆写为
# 真实 commit sha，于是 GET /health 能自报「现在跑的到底是哪个版本」。
# 服务器不是 git 仓库，这是唯一不靠外部记录就能回答该问题的途径。
COPY VERSION .

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "quantpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
