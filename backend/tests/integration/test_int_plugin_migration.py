"""INT：alembic 0032 的升/降级**自动化**回归（设计 §7.6 DoD，2026-09-23）。

## 为什么不能靠「我手工跑过一轮」

0032 落地当天我在 5433 手工跑了 `upgrade → downgrade -1 → upgrade head` 并肉眼看了表与
索引——冷启动评审当场指出：**手工验证过不算判据**（CLAUDE.md §5.3「『已手工验证过』不算
判据」那条），下一个人改了迁移不会有任何东西拦住他。故补这条自动化的。

## 判据（降级那一半才是重点）

`upgrade head` 由集成 conftest 每次跑，天然被覆盖；**`downgrade` 从来没人跑过**——而它
是生产出事时的唯一退路（C-1 要求破坏性动作前有回滚点）。所以本文件真做一次
`downgrade -1` → 断言两表连索引一起消失 → `upgrade head` → 断言约束/索引**逐项复原**。

⚠️ **只在指向测试库 5433 时运行**（C-1 红线：含真实数据的库不得跑 alembic 降级；
5434 有 57h 才能重造的面板数据、生产更不必说）。URL 不是 5433 → 直接 skip 并说明，
不是静默跳过。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from quantpilot.core.config import settings

_BACKEND = Path(__file__).resolve().parents[2]
_TABLES = ("strategy_plugin", "strategy_plugin_audit")
_DESC_INDEX = "idx_strategy_plugin_audit_plugin_created"


def _is_test_db() -> bool:
    """只认 5433。判据取实际连接串，不是「我以为在测试库」。"""
    return ":5433/" in settings.database_url


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_BACKEND), capture_output=True, text=True, timeout=300,
    )


async def _scalar(sql: str) -> object:
    """用**自建连接**读，不碰集成 conftest 的 `db_session`。

    ⚠️ 两个原因：①那个 fixture 把整条用例包在一个会回滚的事务里，本用例要观察
    alembic（另一个连接）提交的 DDL，同一事务里看不到、`rollback()` 还会掐断它的
    上下文管理器（首版就栽在这里）；②`NullPool` + 用完即 dispose，避免连接跨 loop
    残留在全局池里（§4.11 那条「禁止让全局 app engine 跨 loop」）。
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar_one_or_none()
    finally:
        await engine.dispose()


async def _table_count() -> int:
    return int(await _scalar(
        "select count(*) from information_schema.tables where table_name in "
        "('strategy_plugin', 'strategy_plugin_audit')"
    ) or 0)


async def _index_def() -> str | None:
    value = await _scalar(
        f"select indexdef from pg_indexes where indexname = '{_DESC_INDEX}'"
    )
    return str(value) if value is not None else None


@pytest.mark.skipif(
    not _is_test_db(),
    reason=(
        "alembic 降级会 DROP 表——只允许对测试库 5433 跑（C-1）。"
        f"当前 DATABASE_URL 不指向 5433（{settings.database_url.split('@')[-1]}）"
    ),
)
async def test_int_plugin_migration_down_then_up(_ensure_schema: None) -> None:
    """0032 降级 → 两表消失；再升级 → 表与降序索引、约束逐项复原。

    ⚠️ 必须显式请求 `_ensure_schema`：本用例不用 `db_session`，而建表是那个 session 级
    fixture 的副作用——不请求它，单跑本文件时库里压根没有表（首版就这样前置失败）。
    ⚠️ 降级目标写**显式版本号 0031**，不是 `-1`：将来加了 0033，`-1` 会去降那个新迁移，
    测试名字还写着 0032 却在测别的东西（同一类「判据漂移」）。
    """
    assert await _table_count() == 2, "前置：应处于 head（两表在）"
    assert "DESC" in (await _index_def() or "")

    down = _alembic("downgrade", "0031")
    assert down.returncode == 0, down.stderr[-800:]
    assert await _table_count() == 0, "降级后两表应消失（downgrade 漏删表）"
    assert await _index_def() is None

    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-800:]
    assert await _table_count() == 2, "再升级后两表应回来"
    restored = await _index_def()
    assert restored is not None and "DESC" in restored.upper(), f"降序索引没复原：{restored}"
    # 约束也要真回来——只看表存在会漏掉「表建了但约束没建」
    checks = int(await _scalar(
        "select count(*) from pg_constraint where conname in "
        "('uq_strategy_plugin_user_name_ver', 'ck_strategy_plugin_status', "
        "'ck_strategy_plugin_audit_action')"
    ) or 0)
    assert checks == 3, f"UNIQUE/CHECK 三条约束只回来了 {checks} 条"


def test_int_plugin_migration_guard_is_real() -> None:
    """反向钉：那个 skipif 判据必须真按连接串判，不是写死 True。

    没有这条，谁把 `_is_test_db` 改成 `return True`，上面那条就会在 5434 / 生产上
    DROP 表——而它自己不会报错（C-1 那类损失没有回头路）。
    """
    assert _is_test_db() == (":5433/" in settings.database_url)
    assert "5433" in _is_test_db.__doc__ or "5433" in str(_is_test_db.__code__.co_consts)
