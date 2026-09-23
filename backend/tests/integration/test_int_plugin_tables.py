"""INT：C5 两表在真 PostgreSQL 上的约束（设计 §7.4，2026-09-23）。

`tests/unit/test_plugin_models.py` 只看 ORM 元数据——它证明不了**数据库真的拒绝**脏数据。
这里在真库上逐条打：UNIQUE 撞车、CHECK 值域、FK 级联方向（用户注销带走插件）、
以及 RESTRICT（硬删插件时审计不许被带走）。alembic 升降级由集成 conftest 的
`upgrade head` 覆盖，本文件只管语义。

⚠️ 这些断言必须真触发 `IntegrityError`：只断言「插入成功」的测试在约束整条漏掉时照样绿。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.plugin import StrategyPlugin, StrategyPluginAudit
from quantpilot.models.user import User

_SRC = "def compute_raw_factors(universe, data):\n    return None\n"


async def _plugin(session: AsyncSession, user: User, **kw) -> StrategyPlugin:
    row = StrategyPlugin(
        user_id=user.id, name=kw.pop("name", "demo"), version=kw.pop("version", "v1"),
        source_code=kw.pop("source_code", _SRC), **kw,
    )
    session.add(row)
    await session.flush()
    return row


async def test_int_plugin_01_unique_user_name_version(
    db_session: AsyncSession, test_user: User
) -> None:
    """同一用户同名同版本第二次插入必须被 DB 拒（§7.4 UNIQUE）。"""
    await _plugin(db_session, test_user)
    with pytest.raises(IntegrityError):
        await _plugin(db_session, test_user)
    await db_session.rollback()


async def test_int_plugin_02_same_name_different_version_is_allowed(
    db_session: AsyncSession, test_user: User
) -> None:
    """版本升级走新行（旧行保留，审计可追）——同名不同版本必须放行。"""
    await _plugin(db_session, test_user, version="v1")
    await _plugin(db_session, test_user, version="v2")
    rows = (await db_session.execute(
        select(StrategyPlugin).where(StrategyPlugin.user_id == test_user.id)
    )).scalars().all()
    assert {r.version for r in rows} == {"v1", "v2"}


async def test_int_plugin_03_status_check_rejects_unknown(
    db_session: AsyncSession, test_user: User
) -> None:
    """状态值域由 DB CHECK 兜住——应用层校验挡不住脚本/手工 SQL。"""
    with pytest.raises((IntegrityError, DBAPIError)):
        await _plugin(db_session, test_user, status="whatever")
    await db_session.rollback()


async def test_int_plugin_04_default_status_is_active(
    db_session: AsyncSession, test_user: User
) -> None:
    row = await _plugin(db_session, test_user)
    await db_session.refresh(row)
    assert row.status == "active"
    assert row.created_at is not None


async def test_int_plugin_05_audit_action_check(
    db_session: AsyncSession, test_user: User
) -> None:
    plugin = await _plugin(db_session, test_user)
    with pytest.raises((IntegrityError, DBAPIError)):
        db_session.add(StrategyPluginAudit(
            plugin_id=plugin.id, user_id=test_user.id, action="exfiltrate",
        ))
        await db_session.flush()
    await db_session.rollback()


async def test_int_plugin_06_audit_runtime_metrics_stay_null(
    db_session: AsyncSession, test_user: User
) -> None:
    """`upload` 动作没有耗时/内存/退出状态 → 必须留 NULL（C-4：不用 0 冒充「没有」）。"""
    plugin = await _plugin(db_session, test_user)
    db_session.add(StrategyPluginAudit(
        plugin_id=plugin.id, user_id=test_user.id, action="upload",
    ))
    await db_session.flush()
    row = (await db_session.execute(
        select(StrategyPluginAudit).where(StrategyPluginAudit.plugin_id == plugin.id)
    )).scalar_one()
    assert row.duration_ms is None
    assert row.peak_memory_kb is None
    assert row.exit_status is None
    assert row.trade_date is None
    assert row.created_at is not None


async def test_int_plugin_07_execute_audit_keeps_metrics(
    db_session: AsyncSession, test_user: User
) -> None:
    plugin = await _plugin(db_session, test_user)
    db_session.add(StrategyPluginAudit(
        plugin_id=plugin.id, user_id=test_user.id, action="execute",
        trade_date=date(2026, 9, 23), duration_ms=1234, peak_memory_kb=65432,
        exit_status="ok", error_excerpt=None,
    ))
    await db_session.flush()
    row = (await db_session.execute(
        select(StrategyPluginAudit).where(StrategyPluginAudit.plugin_id == plugin.id)
    )).scalar_one()
    assert (row.duration_ms, row.peak_memory_kb, row.exit_status) == (1234, 65432, "ok")


async def test_int_plugin_08_hard_delete_is_restricted_by_audit(
    db_session: AsyncSession, test_user: User
) -> None:
    """有审计在时硬删插件必须失败（RESTRICT）——审计的意义就是留痕，软删才是产品路径。"""
    plugin = await _plugin(db_session, test_user)
    db_session.add(StrategyPluginAudit(
        plugin_id=plugin.id, user_id=test_user.id, action="upload",
    ))
    await db_session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.execute(delete(StrategyPlugin).where(StrategyPlugin.id == plugin.id))
        await db_session.flush()
    await db_session.rollback()


async def test_int_plugin_09_soft_delete_keeps_audit(
    db_session: AsyncSession, test_user: User
) -> None:
    plugin = await _plugin(db_session, test_user)
    db_session.add(StrategyPluginAudit(
        plugin_id=plugin.id, user_id=test_user.id, action="upload",
    ))
    await db_session.flush()
    plugin.status = "deleted"
    await db_session.flush()
    audits = (await db_session.execute(
        select(StrategyPluginAudit).where(StrategyPluginAudit.plugin_id == plugin.id)
    )).scalars().all()
    assert len(audits) == 1, "软删后审计必须还在"


async def test_int_plugin_10_desc_index_exists_in_db(db_session: AsyncSession) -> None:
    """§7.4 的降序索引必须**在库里**是降序——ORM 元数据对不对，库里可能是另一回事。

    判据取 `pg_indexes.indexdef`（真实 DDL），不是 ORM 定义：alembic 与 ORM 分别写一遍，
    只核对后者等于只核对自己（CLAUDE.md §4.11 自证式测试）。
    """
    row = (await db_session.execute(text(
        "select indexdef from pg_indexes where indexname = "
        "'idx_strategy_plugin_audit_plugin_created'"
    ))).scalar_one_or_none()
    assert row is not None, "索引没建（alembic 0032 漏了？）"
    assert "DESC" in row.upper(), f"created_at 不是降序：{row}"
