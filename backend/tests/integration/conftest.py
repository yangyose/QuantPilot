"""集成测试目录级 conftest。

只放集成测试专属的 autouse 兜底；通用 fixture（db_engine / db_session /
_ensure_schema / 红线护栏）仍在 tests/conftest.py。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from quantpilot.core.database import engine as app_engine


@pytest.fixture(autouse=True)
async def _drop_stale_app_engine_pool() -> AsyncGenerator[None, None]:
    """每个集成测试开始前丢弃全局 app engine 连接池（``close=False``）。

    根因（2026-06-26 CI ``test_int_p14_1_06_concurrent_deposit_same_key`` flaky）：
    ``quantpilot.core.database.engine`` 是默认 QueuePool + ``pool_pre_ping=True``。
    pytest-asyncio function-scoped event loop 下，**上一个测试 loop** 建立的连接残留
    在池里；本测试 loop 复用它时，``pool_pre_ping`` 的 ``SELECT 1`` 打到那条连接绑定
    的**已关闭旧 loop** → asyncpg 在 proactor 上 ``'NoneType' object has no attribute
    'send'``，在本测试首个 DB 操作处直接炸（CI 报在 seed commit 行）。

    e2e 阶段先经 ``get_db`` 把池填上 e2e loop 的连接，因此首个直接/经生产脚本使用
    全局 ``AsyncSessionLocal`` 的集成测试（账户并发幂等、backfill/end2end 脚本类）最易
    中招；单独跑该测试时池为空、反而不复现。

    ``close=False``：只替换池对象、不在当前 loop 关闭那些属于旧 loop 的连接（本就关
    不掉），残连交 GC。开销近乎为零。db_engine（NullPool）是另一个 engine 实例，不受
    影响。详见 ~/.claude/CLAUDE.md §4 跨 loop 连接复用条目。
    """
    await app_engine.dispose(close=False)
    yield
