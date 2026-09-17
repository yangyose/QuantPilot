"""护栏自测：e2e 目录里任何真实 DB 连接必须以 E2ETouchedDatabase 失败（不靠环境）。

只钉「该拦的拦住」不够（§4.12）——还要证明它拦的是**连接**这一层：
直接用 AsyncSessionLocal 开 session 并执行 SQL，不经任何 API。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from quantpilot.core.database import AsyncSessionLocal
from tests.e2e.conftest import E2ETouchedDatabase


async def test_direct_session_use_is_refused_in_e2e() -> None:
    with pytest.raises(E2ETouchedDatabase):
        async with AsyncSessionLocal() as s:
            await s.execute(text("select 1"))
