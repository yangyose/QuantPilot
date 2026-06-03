"""Phase 14 §14-9：日级 IC 时序回填脚本（补全 §14-2 ICIR 历史回算缺失的上游生产者）。

枚举 [--start, --end] 范围内每个**有完整前向窗口**的交易日 d（= 因子值日），逐日：
1. `ScoringService.score_universe_for_date(d)` → 全 universe `CompositeScore`（**不写 pool**）
2. 抽每策略 `score_breakdown_raw[strategy]["z_raw"]` → 全 universe strategy_z Series
3. `compute_forward_returns`（后复权 adj_close + 严格 t=d+20 交易日 + 剔涨跌停/停牌）
4. `compute_daily_ic` → 每策略 Spearman Rank IC
5. 写 `factor_ic_window_state` row_type='daily'（trade_date=d、state=market_state[d]）

写完后重跑 `scripts/backfill_icir_rebalance.py --force` 即可让月末批切 `weights_source='icir'`。

per-day 独立 `AsyncSessionLocal` + commit；SIGINT/SIGTERM graceful；断点续传查
`get_existing_daily_ic_dates` 跳过已回填日。

依赖：5y candidate_pool 回填（§14-2）+ market_state_history 已在库（score_universe_for_date
读当日 state）+ daily_quote 全量。纯 DB 计算（score_universe 读原始数据，无 Tushare 采集）。

用法：
  uv run python scripts/backfill_daily_ic.py --start 2021-05-13 --end 2026-06-01 --skip-confirm
  uv run python scripts/backfill_daily_ic.py --start 2021-05-13 --end 2026-06-01 --dry-run-plan
  uv run python scripts/backfill_daily_ic.py \
      --start 2024-01-01 --end 2026-06-01 --force --skip-confirm

退出码：0=全部成功 / 1=任一日失败 / 2=用法错。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import func, or_, select

from quantpilot.core.config import settings
from quantpilot.core.config_defaults import (
    DEFAULT_MEAN_REVERSION_STRATEGY,
    DEFAULT_MOMENTUM_STRATEGY,
    DEFAULT_STRATEGY_WEIGHTS,
    DEFAULT_TREND_STRATEGY,
    DEFAULT_UNIVERSE,
    DEFAULT_VALUE_STRATEGY,
)
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import FactorICRepository, ICDailyRow
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.diagnostics.ic_aggregator import (
    _DAILY_IC_MIN_XS,
    compute_daily_ic,
    compute_forward_returns,
)
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.factor_pipeline import FactorPipeline, FactorPipelineConfig
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.engine.universe import UniverseFilter
from quantpilot.models.market import DailyQuote
from quantpilot.services.factor_monitor_service import FactorMonitorService
from quantpilot.services.strategy_service import ScoringService

# 复用 candidate_pool 回填脚本的 graceful 中断（DRY）
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from backfill_candidate_pool import _GracefulInterrupt  # noqa: E402, I001

logger = logging.getLogger(__name__)

_FORWARD_WINDOW = 20  # SDD §7.4：前向收益窗口 = 20 交易日（lag）
_STRATEGY_NAMES = ("trend", "momentum", "mean_reversion", "value")
_PROGRESS_INTERVAL = 50


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# ---------------------------------------------------------------- 纯函数（UT 覆盖）

def _plan_daily_ic(
    trade_dates: list[date],
    existing: set[date],
    force: bool,
    forward_complete: set[date],
) -> tuple[list[date], list[date]]:
    """纯函数：计算 (to_process, to_skip)（UT-P14-9-03）。

    - `forward_complete` 外的 trade_date（末尾 ~20 交易日无完整前向窗口）→ 既不处理也不跳过。
    - `existing` 中且非 force → to_skip；否则 to_process。
    """
    to_process: list[date] = []
    to_skip: list[date] = []
    for d in trade_dates:
        if d not in forward_complete:
            continue
        if d in existing and not force:
            to_skip.append(d)
        else:
            to_process.append(d)
    return to_process, to_skip


def _extract_strategy_z(composites: list) -> dict[str, pd.Series]:
    """从全 universe CompositeScore 列表抽每策略 z_raw Series（UT-P14-9-03b）。

    `CompositeScore.score_breakdown_raw[strategy]["z_raw"]`（Phase 11 Scorer Step 3
    落产物；AttributionService 已消费同字段）。空策略列省略。
    """
    data: dict[str, dict[str, float]] = {s: {} for s in _STRATEGY_NAMES}
    for c in composites:
        raw = getattr(c, "score_breakdown_raw", None) or {}
        for s in _STRATEGY_NAMES:
            entry = raw.get(s)
            if entry is not None and entry.get("z_raw") is not None:
                z = entry["z_raw"]
                if not (isinstance(z, float) and pd.isna(z)):
                    data[s][str(c.ts_code)] = float(z)
    return {s: pd.Series(d) for s, d in data.items() if d}


def _forward_complete_dates(
    trade_dates: list[date], calendar: TradingCalendar, max_data_date: date,
) -> set[date]:
    """d 有完整前向窗口 ⟺ get_next_trade_date(d, 20) 存在且 ≤ max_data_date。"""
    out: set[date] = set()
    for d in trade_dates:
        try:
            t = calendar.get_next_trade_date(d, _FORWARD_WINDOW)
        except (ValueError, IndexError):
            continue
        if t is not None and t <= max_data_date:
            out.add(d)
    return out


# ---------------------------------------------------------------- 编排（INT 覆盖）

def _build_scoring_service(session, calendar: TradingCalendar) -> ScoringService:
    """组装 ScoringService（注入 FactorMonitorService 走 5 步管线，default config）。"""
    repo = MarketDataRepository(session)
    factor_monitor = FactorMonitorService(
        session, FactorMonitorEngine(), FactorICRepository(), calendar=calendar,
    )
    fp_cfg = FactorPipelineConfig(
        winsorize_lower_pct=0.01, winsorize_upper_pct=0.99,
        neutralize_industry=True, neutralize_market_cap=True, neutralize_beta=False,
    )
    return ScoringService(
        repo=repo,
        universe_filter=UniverseFilter(DEFAULT_UNIVERSE),
        strategies=[
            TrendStrategy(DEFAULT_TREND_STRATEGY),
            MomentumStrategy(DEFAULT_MOMENTUM_STRATEGY),
            MeanReversionStrategy(DEFAULT_MEAN_REVERSION_STRATEGY),
            ValueStrategy(DEFAULT_VALUE_STRATEGY),
        ],
        scorer=Scorer(DEFAULT_STRATEGY_WEIGHTS, pipeline=FactorPipeline(fp_cfg)),
        pool_manager=CandidatePoolManager(DEFAULT_UNIVERSE),
        calendar=calendar,
        factor_monitor=factor_monitor,
    )


async def _excluded_codes(session, ts_codes: list[str], td: date, t: date) -> set[str]:
    """base(d) 或 end(t) 日涨跌停 / 停牌的 ts_code（SDD §7.4 line 473 剔异常收益）。"""
    if not ts_codes:
        return set()
    stmt = (
        select(DailyQuote.ts_code)
        .where(
            DailyQuote.ts_code.in_(ts_codes),
            DailyQuote.trade_date.in_([td, t]),
            or_(
                DailyQuote.limit_up.is_(True),
                DailyQuote.limit_down.is_(True),
                DailyQuote.is_suspended.is_(True),
            ),
        )
        .distinct()
    )
    return {r[0] for r in (await session.execute(stmt)).all()}


async def _run_one_trade_date(
    td: date, calendar: TradingCalendar, min_xs: int,
) -> tuple[bool, int]:
    """对因子值日 td 算全策略日级 IC，独立 session + commit。返回 (success, n_rows)。"""
    async with AsyncSessionLocal() as session:
        try:
            scoring_service = _build_scoring_service(session, calendar)
            composites = await scoring_service.score_universe_for_date(td)
            if not composites:
                return True, 0  # 空 universe，无 IC 可算（视为成功跳过）

            repo = MarketDataRepository(session)
            state_record = await repo.get_latest_market_state(
                before_date=td + timedelta(days=1)
            )
            state = state_record.market_state if state_record else "OSCILLATION"

            strategy_z = _extract_strategy_z(composites)
            if not strategy_z:
                return True, 0

            t = calendar.get_next_trade_date(td, _FORWARD_WINDOW)
            ts_codes = [str(c.ts_code) for c in composites]
            adj = await repo.get_adj_prices_bulk(ts_codes, td, t)
            excluded = await _excluded_codes(session, ts_codes, td, t)
            fwd = compute_forward_returns(adj, td, t, excluded=excluded)

            points = compute_daily_ic(strategy_z, fwd, min_xs=min_xs)
            if not points:
                logger.info("daily_ic_no_points trade_date=%s (稀疏/样本不足)", td)
                return True, 0

            rows = [
                ICDailyRow(
                    strategy=p.strategy, factor=p.strategy, state=state,
                    trade_date=td, ic_value=p.ic_value, sample_size=p.sample_size,
                )
                for p in points
            ]
            await FactorICRepository().upsert_ic_daily(session, rows)
            await session.commit()
            return True, len(rows)
        except Exception as exc:
            await session.rollback()
            print(
                f"      ERROR trade_date={td} → {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            logger.exception("backfill_daily_ic_error trade_date=%s", td)
            return False, 0


async def _max_daily_quote_date(session) -> date | None:
    return (await session.execute(select(func.max(DailyQuote.trade_date)))).scalar()


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=_parse_date, help="起点（YYYY-MM-DD，含）")
    parser.add_argument("--end", required=True, type=_parse_date, help="终点（YYYY-MM-DD，含）")
    parser.add_argument("--dry-run-plan", action="store_true", help="预检：仅打印计划")
    parser.add_argument("--force", action="store_true", help="强制重算已存在日（upsert 覆盖）")
    parser.add_argument("--skip-confirm", action="store_true", help="跳过交互确认")
    parser.add_argument("--min-xs", type=int, default=_DAILY_IC_MIN_XS,
                        help=f"每日最小横截面样本（默认 {_DAILY_IC_MIN_XS}）")
    args = parser.parse_args()

    if args.start > args.end:
        print(f"ERROR: start ({args.start}) > end ({args.end})", file=sys.stderr)
        return 2
    if not settings.tushare_token:
        print("ERROR: TUSHARE_TOKEN 未配置（仅用于拉日历）", file=sys.stderr)
        return 2

    print(
        f"=== Backfill daily IC: {args.start} → {args.end} | "
        f"mode: {'force-overwrite' if args.force else 'skip-existing'} | "
        f"min_xs={args.min_xs} ==="
    )

    adapter = TushareAdapter(token=settings.tushare_token)
    calendar = await TradingCalendar.from_adapter(
        adapter, args.start - timedelta(days=120), args.end + timedelta(days=60),
    )
    trade_dates = calendar.get_trade_dates(args.start, args.end)

    print("[0/2] Pre-flight plan:")
    async with AsyncSessionLocal() as session:
        max_data = await _max_daily_quote_date(session)
        if max_data is None:
            print("      ERROR: daily_quote 为空", file=sys.stderr)
            return 2
        forward_complete = _forward_complete_dates(trade_dates, calendar, max_data)
        existing = await FactorICRepository().get_existing_daily_ic_dates(
            session, args.start, args.end,
        )
        to_process, to_skip = _plan_daily_ic(
            trade_dates, existing, args.force, forward_complete,
        )
    tail = len(trade_dates) - len(forward_complete)
    print(f"      total_trade_dates: {len(trade_dates)}")
    print(f"      tail_no_forward_window (末尾~20 交易日): {tail}")
    print(f"      already_in_daily_ic: {len(existing)}")
    print(f"      to_process: {len(to_process)}")
    print(f"      to_skip (already done): {len(to_skip)}")
    if to_process:
        print(f"      range: {to_process[0]} → {to_process[-1]}")

    if args.dry_run_plan:
        return 0
    if not to_process:
        print("\n[1/2] Nothing to do.")
        return 0
    if not args.skip_confirm:
        try:
            ans = input(f"\n[?] proceed with {len(to_process)} trade_dates? [y/N] ")
        except EOFError:
            ans = "n"
        if ans.strip().lower() not in ("y", "yes"):
            print("aborted by user")
            return 1

    interrupt = _GracefulInterrupt()
    interrupt.install()
    total = len(to_process)
    print(f"\n[1/2] Backfilling daily IC for {total} trade_dates...")
    success = fail = total_rows = 0
    for i, td in enumerate(to_process, 1):
        if interrupt.stop:
            print(f"      interrupted at {i}/{total} (trade_date={td})")
            break
        ok, n = await _run_one_trade_date(td, calendar, args.min_xs)
        if ok:
            success += 1
            total_rows += n
        else:
            fail += 1
        if i % _PROGRESS_INTERVAL == 0 or i == total:
            print(f"      {i}/{total}: trade_date={td} success={success} "
                  f"fail={fail} rows={total_rows}")

    print(f"\n[2/2] Done: success={success} fail={fail} daily_rows={total_rows}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.exit(asyncio.run(_main()))
