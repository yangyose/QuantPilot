"""
MIG: 数据库迁移集成测试（RED 阶段）
0001_initial_schema.py 尚未实现，alembic upgrade head 会无事可做或找不到迁移。
"""
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from quantpilot.core.config import settings

BACKEND_DIR = Path(__file__).parents[2]  # tests/integration/ → tests/ → backend/

EXPECTED_TABLES = {
    # 市场数据
    "stock_info",
    "daily_quote",
    "financial_data",
    "index_history",
    # 业务数据
    "market_state_history",
    "candidate_pool",
    "signal",
    "signal_score_snapshot",
    # Phase 15 §15-7：factor_ic_history 已归并进 factor_ic_window_state 并 DROP（0017）
    "factor_ic_window_state",
    "report",
    "user_watchlist",
    # 账户数据
    "account",
    "position",
    "trade_record",
    "fund_flow",
    # 系统表
    "pipeline_run",
    "system_config",
    "user_config",
    "user_config_history",
}

EXPECTED_INDEXES = [
    "idx_daily_quote_code",
    "idx_pool_date_score",
    "idx_signal_date_type",
    "idx_trade_record_account_date",
    "idx_fund_flow_account_date",
    "idx_snapshot_signal",
]


def _run_alembic(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *cmd.split()],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
    )


# ---------------------------------------------------------------------------
# MIG-01~03 : upgrade → downgrade → re-upgrade 完整生命周期（顺序强制）
# ---------------------------------------------------------------------------

def test_migration_lifecycle():
    """MIG-01~03：upgrade → downgrade → re-upgrade 完整生命周期"""
    r1 = _run_alembic("upgrade head")
    assert r1.returncode == 0, f"初次 upgrade 失败:\n{r1.stderr}"

    r2 = _run_alembic("downgrade base")
    assert r2.returncode == 0, f"downgrade 失败:\n{r2.stderr}"

    r3 = _run_alembic("upgrade head")
    assert r3.returncode == 0, f"二次 upgrade（幂等）失败:\n{r3.stderr}"


# ---------------------------------------------------------------------------
# MIG-04 : 19 张表全部存在
# ---------------------------------------------------------------------------

async def test_all_tables_exist():
    """MIG-04: information_schema 中全部 19 张表可找到"""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        existing = {row[0] for row in result}
    await engine.dispose()
    missing = EXPECTED_TABLES - existing
    assert not missing, f"以下表缺失: {missing}"


# ---------------------------------------------------------------------------
# MIG-05 : 关键索引存在
# ---------------------------------------------------------------------------

async def test_key_indexes_exist():
    """MIG-05: 关键索引在 pg_indexes 中可查到"""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
        )
        existing_indexes = {row[0] for row in result}
    await engine.dispose()
    missing = [i for i in EXPECTED_INDEXES if i not in existing_indexes]
    assert not missing, f"以下索引缺失: {missing}"


# ---------------------------------------------------------------------------
# MIG-06 : signal_score_snapshot.signal_id CASCADE DELETE
# ---------------------------------------------------------------------------

async def test_cascade_delete_signal_score_snapshot():
    """MIG-06: 删除 signal 记录后，对应快照自动级联删除"""
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.connect() as conn:          # connect() 允许手动控制事务
        await conn.execute(text(
            "INSERT INTO signal (ts_code, signal_type, trade_date, status) "
            "VALUES ('000001.SZ', 'BUY', '2026-01-01', 'NEW')"
        ))
        result = await conn.execute(text(
            "SELECT id FROM signal WHERE ts_code='000001.SZ' AND trade_date='2026-01-01'"
        ))
        signal_id = result.scalar_one()

        await conn.execute(text(
            f"INSERT INTO signal_score_snapshot (signal_id, trade_date, ts_code) "
            f"VALUES ({signal_id}, '2026-01-01', '000001.SZ')"
        ))

        await conn.execute(text(f"DELETE FROM signal WHERE id = {signal_id}"))

        result = await conn.execute(text(
            f"SELECT COUNT(*) FROM signal_score_snapshot WHERE signal_id = {signal_id}"
        ))
        assert result.scalar_one() == 0, "CASCADE DELETE 未生效，快照记录未被删除"

        await conn.rollback()                     # connect() 内合法，清理测试数据

    await engine.dispose()
