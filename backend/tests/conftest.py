import os
import re
import subprocess
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from quantpilot.api.deps import get_current_user_id
from quantpilot.core.config import settings
from quantpilot.core.security import hash_password
from quantpilot.main import app

BACKEND_DIR = Path(__file__).parents[1]  # tests/ → backend/

# 测试专用明文密码（与 .env 解耦，不依赖真实管理员密码）
TEST_PASSWORD = "ci-test-password-only"


@pytest.fixture(autouse=True, scope="session")
def override_admin_password() -> Generator[None, None, None]:
    """将 settings 中的密码哈希替换为测试专用值，解耦 .env"""
    original = settings.admin_password_hash
    settings.admin_password_hash = hash_password(TEST_PASSWORD)
    yield
    settings.admin_password_hash = original


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP 测试客户端，动态注册测试路由并在测试后清理"""

    @app.get("/test/protected", include_in_schema=False)
    async def _protected(user_id: int = Depends(get_current_user_id)):
        return {"user": user_id}

    test_route = app.routes[-1]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.routes.remove(test_route)


def _guard_test_db_or_abort() -> None:
    """红线护栏：集成测试只允许打测试库（:5433）。

    `_ensure_schema` 收尾跑 `alembic downgrade base` 会 DROP 所有表。若 DATABASE_URL
    误指向生产/本地数据库（:5432），整套真实数据会被灭。此前仅靠「跑测试时手动改
    DATABASE_URL」+ auto_test 钩子小心，无源头拦截（CLAUDE.md C-1 / feedback_pytest
    _wipes_db 踩过一次）。本护栏在任何 alembic 动作之前硬中止整个 session。

    放行条件：URL 含 ':5433'（测试库约定端口）或显式 QUANTPILOT_ALLOW_PROD_TEST_DB=1
    （仅限明知后果的特殊场景）。
    """
    url = settings.database_url
    if ":5433" in url or os.getenv("QUANTPILOT_ALLOW_PROD_TEST_DB") == "1":
        return
    masked = re.sub(r"://[^@]*@", "://***@", url)
    pytest.exit(
        "拒绝对非测试库运行集成测试：集成测试会 `alembic downgrade base` DROP 所有表。\n"
        f"  当前 DATABASE_URL = {masked}\n"
        "  测试库约定端口为 :5433。请把 DATABASE_URL 指向 :5433 测试库后重试\n"
        "  （确知后果时可设 QUANTPILOT_ALLOW_PROD_TEST_DB=1 绕过）。",
        returncode=2,
    )


@pytest.fixture(scope="session")
def _ensure_schema() -> Generator[None, None, None]:
    """整个测试 session 跑一次 alembic upgrade head（同步，与 event loop 解耦）"""
    _guard_test_db_or_abort()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"
    yield
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=BACKEND_DIR, capture_output=True, text=True,
    )


@pytest.fixture
async def db_engine(_ensure_schema: None) -> AsyncGenerator[AsyncEngine, None]:
    """每个测试独立 async engine，绑定到当前测试的 event loop。

    禁止 scope=session：每个测试一个新 loop，session 级 async engine 会出现
    "Future attached to a different loop" 错误。

    依赖 pyproject.toml `asyncio_mode = "auto"` 让 pytest-asyncio 统一接管
    所有 async test + fixture——禁用 @pytest.mark.anyio 标记（fixture 跑在
    pytest-asyncio loop A，test body 跑在 anyio runner loop B，session 跨
    loop 必抛；2026-05-21 CI ubuntu 17 个 teardown errors 根因，已统一移除）。
    """
    engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """每个集成测试拥有独立事务，测试后回滚"""
    async_session = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        async with session.begin():
            yield session
            await session.rollback()
