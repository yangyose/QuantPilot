"""INT-P15-7-01: ReportService 月报因子告警 repoint 验证（Phase 15 §15-7）。

旧表 factor_ic_history 归并进 factor_ic_window_state（row_type='monthly_quality'）后，
月报告警段须从新表 monthly_quality 行读取 alert_status / ic_mean_3m（复用列 ic_mean_state）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.account import Account
from quantpilot.models.business import FactorICWindowState
from quantpilot.services.report_service import ReportService
from tests.integration._helpers import seeded_user_id

_MONTH_END = date(2026, 3, 31)


async def test_int_p15_7_01_monthly_report_reads_alerts_from_window_state(
    db_session: AsyncSession,
) -> None:
    """月报 factor_alerts 从 monthly_quality 行读出 alert_status（归并后等价）。"""
    # 月末有告警行（DECAY）+ 一行无告警（alert_status=NULL，不应出现在 factor_alerts）
    db_session.add(FactorICWindowState(
        strategy="MomentumStrategy", factor="momentum_score", state="ALL",
        trade_date=_MONTH_END, ic_value=-0.12, ic_mean_state=-0.09,
        sample_size=0, row_type="monthly_quality", alert_status="DECAY",
    ))
    db_session.add(FactorICWindowState(
        strategy="TrendStrategy", factor="trend_score", state="ALL",
        trade_date=_MONTH_END, ic_value=0.08, ic_mean_state=0.07,
        sample_size=0, row_type="monthly_quality", alert_status=None,
    ))
    # 干扰行：daily/aggregate 行（不同 row_type）不应被月报告警查询命中
    db_session.add(FactorICWindowState(
        strategy="ValueStrategy", factor="pe_value", state="UPTREND",
        trade_date=_MONTH_END, ic_value=0.03, sample_size=120,
        row_type="aggregate", icir=1.5, alert_status=None,
    ))
    account = Account(
        user_id=await seeded_user_id(db_session), name="报告测试账户",
        account_type="REAL", cash=0.0,
    )
    db_session.add(account)
    await db_session.flush()

    report = await ReportService(db_session).generate_monthly(_MONTH_END, account.id)

    alerts = report.content["factor_alerts"]
    assert len(alerts) == 1
    assert alerts[0]["strategy"] == "MomentumStrategy"
    assert alerts[0]["factor"] == "momentum_score"
    assert alerts[0]["alert"] == "DECAY"
