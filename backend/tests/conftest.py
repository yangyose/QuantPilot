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

from quantpilot.api.deps import get_current_user
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


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP 测试客户端，动态注册测试路由并在测试后清理"""

    @app.get("/test/protected", include_in_schema=False)
    async def _protected(user: str = Depends(get_current_user)):
        return {"user": user}

    test_route = app.routes[-1]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.routes.remove(test_route)


@pytest.fixture(scope="session")
def _ensure_schema() -> Generator[None, None, None]:
    """整个测试 session 跑一次 alembic upgrade head（同步，与 event loop 解耦）"""
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

    禁止用 scope=session：anyio 默认每个测试一个 loop，session 级 async engine
    会出现 "Future attached to a different loop" 错误（CI 上比 Windows 严格）。
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
