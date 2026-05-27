"""Phase 14 §14-2.1：5y candidate_pool 历史回填脚本。

枚举 [--start, --end] 范围内每个交易日，逐日：
1. 调 `MarketStateService.identify_and_save(trade_date)` 写当日 market_state_history
2. 调 `ScoringService.run_daily_scoring(trade_date)`（factor_monitor 已注入 →
   自动走 Phase 11 5 步管线 `_run_phase11_pipeline`）写当日 candidate_pool

per-day 独立 `AsyncSessionLocal`（Bug 5 修复 + 单日异常整日 rollback 不影响其他日）。
SIGINT/SIGTERM graceful：等当前 trade_date commit/rollback 完成后退出。

依赖：
- TUSHARE_TOKEN / DATABASE_URL 环境变量
- PostgreSQL 容器运行中
- daily_quote / financial_data / index_history / stock_info / index_component
  全量已在库（refill_history.py 5y 先行）

用法：
  # 5y 全量回填（断点续传，跳过已存在 trade_date）：
  uv run python scripts/backfill_candidate_pool.py \\
      --start 2021-01-04 --end 2026-05-22 --skip-confirm

  # 预检（不写库，仅打印 trade_dates 总数 / 已存在 / 待处理）：
  uv run python scripts/backfill_candidate_pool.py \\
      --start 2021-01-04 --end 2026-05-22 --dry-run-plan

  # 强制重算所有 trade_date（不删 + upsert 覆盖）：
  uv run python scripts/backfill_candidate_pool.py \\
      --start 2025-01-01 --end 2026-05-22 --force --skip-confirm

进度上报：每 50 trade_date 打印 logger.info；如配置 REDIS_URL，
推送 `quantpilot:backfill:progress` pubsub 频道供前端 PipelineProgressCard 风格
进度条消费。

graceful shutdown：捕 SIGINT/SIGTERM，等当前 trade_date 的 per-day session
commit/rollback 完成后退出，不留半 commit 状态。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import date, datetime, timedelta

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import FactorICRepository
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.factor_pipeline import FactorPipeline, FactorPipelineConfig
from quantpilot.engine.market_state import MarketStateEngine
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.engine.universe import UniverseFilter
from quantpilot.services.factor_monitor_service import FactorMonitorService
from quantpilot.services.market_state_service import MarketStateService
from quantpilot.services.strategy_service import ScoringService

logger = logging.getLogger(__name__)

_PROGRESS_CHANNEL = "quantpilot:backfill:progress"
_PROGRESS_INTERVAL = 50  # 每 N trade_date 打印 + 推 Redis 一次


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _compute_plan(
    trade_dates: list[date], existing: set[date], force: bool,
) -> tuple[list[date], list[date]]:
    """纯函数：计算 to_process / to_skip 列表（供单元测试覆盖 UT-P14-2-01）。

    - force=False → existing 中的 trade_date 进 to_skip
    - force=True → 全部进 to_process（仍 upsert 覆盖）
    """
    to_process: list[date] = []
    to_skip: list[date] = []
    for d in trade_dates:
        if d in existing and not force:
            to_skip.append(d)
        else:
            to_process.append(d)
    return to_process, to_skip


class _GracefulInterrupt:
    """信号 handler：SIGINT/SIGTERM 后置 stop=True，主循环检查后跳出。

    供 UT-P14-2-03 覆盖：构造、install、handler 调用后 stop 置 True 的契约。
    """

    def __init__(self) -> None:
        self.stop = False

    def _handler(self, signum: int, frame: object | None) -> None:  # noqa: ARG002
        print(
            f"\n[!] received signal {signum}, finishing current trade_date then exit",
            file=sys.stderr,
        )
        self.stop = True

    def install(self) -> None:
        # Windows 上 SIGTERM 仅占位；SIGINT (Ctrl+C) 主用
        try:
            signal.signal(signal.SIGINT, self._handler)
        except ValueError:
            # 非主线程（pytest 子线程场景）不支持 signal 注册，跳过
            pass
        if hasattr(signal, "SIGTERM"):
            try:
                signal.signal(signal.SIGTERM, self._handler)
            except ValueError:
                pass


async def _publish_progress(
    redis_client: object | None, payload: dict,
) -> None:
    """best-effort 推送进度到 Redis pubsub；失败仅 WARN 不阻断主循环。"""
    if redis_client is None:
        return
    try:
        await redis_client.publish(_PROGRESS_CHANNEL, json.dumps(payload, default=str))
    except Exception:
        logger.warning("backfill_progress_publish_failed", exc_info=True)


def _build_scoring_service(
    session, calendar: TradingCalendar,
) -> tuple[ScoringService, MarketStateService]:
    """组装 ScoringService（注入 FactorMonitorService 走 Phase 11 5 步管线）。

    使用 default config 派生 dataclass 实例化所有 Engine 组件——回填脚本独立于
    config_snapshot（PipelineRun 是 daily 实时路径专属），不引入 ConfigService 依赖。
    """
    from quantpilot.core.config_defaults import (
        DEFAULT_MARKET_STATE,
        DEFAULT_MEAN_REVERSION_STRATEGY,
        DEFAULT_MOMENTUM_STRATEGY,
        DEFAULT_STRATEGY_WEIGHTS,
        DEFAULT_TREND_STRATEGY,
        DEFAULT_UNIVERSE,
        DEFAULT_VALUE_STRATEGY,
    )

    repo = MarketDataRepository(session)
    factor_monitor = FactorMonitorService(
        session, FactorMonitorEngine(), FactorICRepository(),
        calendar=calendar,
    )
    fp_cfg = FactorPipelineConfig(
        winsorize_lower_pct=0.01,
        winsorize_upper_pct=0.99,
        neutralize_industry=True,
        neutralize_market_cap=True,
        neutralize_beta=False,
    )
    scoring_service = ScoringService(
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
    ms_engine = MarketStateEngine(DEFAULT_MARKET_STATE)
    ms_service = MarketStateService(ms_engine, repo)
    return scoring_service, ms_service


async def _run_one_trade_date(
    trade_date: date,
    calendar: TradingCalendar,
) -> tuple[bool, int]:
    """对单个 trade_date 跑 market_state + score_universe，独立 session + commit。

    返回 (success, candidate_count)：成功时 candidate_count = 入池 + fade-out 行数；
    异常 → 整日 rollback + (False, 0)。
    """
    async with AsyncSessionLocal() as session:
        try:
            scoring_service, ms_service = _build_scoring_service(session, calendar)
            await ms_service.identify_and_save(trade_date)
            await session.flush()  # market_state 落库供 score_universe 读
            composites = await scoring_service.run_daily_scoring(trade_date)
            await session.commit()
            return True, len(composites)
        except Exception as exc:
            await session.rollback()
            print(
                f"      ERROR trade_date={trade_date} → {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            logger.exception("backfill_candidate_pool_error trade_date=%s", trade_date)
            return False, 0


async def _print_plan(
    session,
    calendar: TradingCalendar,
    start: date, end: date,
    force: bool,
) -> tuple[list[date], list[date]]:
    """打印计划，返回 (to_process, to_skip)。"""
    repo = MarketDataRepository(session)
    trade_dates = calendar.get_trade_dates(start, end)
    existing = await repo.get_existing_candidate_pool_dates(start, end)
    to_process, to_skip = _compute_plan(trade_dates, existing, force)

    print(f"      total_trade_dates: {len(trade_dates)}")
    print(f"      already_in_candidate_pool: {len(existing)}")
    print(f"      to_process: {len(to_process)}")
    print(f"      to_skip (already done): {len(to_skip)}")
    if to_process:
        print(f"      range: {to_process[0]} → {to_process[-1]}")
    return to_process, to_skip


async def _maybe_redis_client() -> object | None:
    """best-effort 创建 Redis 客户端供进度推送；失败返回 None。"""
    if not getattr(settings, "redis_url", None):
        return None
    try:
        import redis.asyncio as redis_async  # noqa: PLC0415

        client = redis_async.from_url(settings.redis_url)
        await client.ping()
        return client
    except Exception:
        logger.warning("backfill_redis_unavailable, progress publish disabled",
                       exc_info=True)
        return None


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=_parse_date,
                        help="范围起点（YYYY-MM-DD，含）")
    parser.add_argument("--end", required=True, type=_parse_date,
                        help="范围终点（YYYY-MM-DD，含）")
    parser.add_argument("--dry-run-plan", action="store_true",
                        help="预检：仅打印 trade_dates / 已存在 / 待处理数")
    parser.add_argument("--force", action="store_true",
                        help="强制重算已存在 trade_date（upsert 覆盖，不删行）")
    parser.add_argument("--skip-confirm", action="store_true",
                        help="跳过交互确认")
    args = parser.parse_args()

    if args.start > args.end:
        print(f"ERROR: start ({args.start}) > end ({args.end})", file=sys.stderr)
        return 2
    if not settings.tushare_token:
        print("ERROR: TUSHARE_TOKEN 未配置", file=sys.stderr)
        return 2

    print(
        f"=== Backfill candidate_pool: {args.start} → {args.end} | "
        f"mode: {'force-overwrite' if args.force else 'skip-existing'} ==="
    )

    # 拉日历（前 120 / 后 30 天 buffer 保护边界查询）：
    # UniverseFilter 需要 calendar.get_prev_trade_date(today, 60 交易日) → 约 90 日历日；
    # _PRICE_WINDOW_DAYS=90 + ADX 14 期 warm-up → 取 120 日历日 buffer 保险（覆盖
    # ~80 交易日，可应对所有依赖前置历史的 engine 路径）。
    adapter = TushareAdapter(token=settings.tushare_token)
    calendar = await TradingCalendar.from_adapter(
        adapter,
        args.start - timedelta(days=120),
        args.end + timedelta(days=30),
    )

    # 预检
    print("[0/2] Pre-flight plan:")
    async with AsyncSessionLocal() as session:
        to_process, to_skip = await _print_plan(
            session, calendar, args.start, args.end, args.force,
        )

    if args.dry_run_plan:
        return 0
    if not to_process:
        print("\n[1/2] Nothing to do (all trade_dates already in candidate_pool).")
        return 0

    if not args.skip_confirm:
        try:
            ans = input(f"\n[?] proceed with {len(to_process)} trade_dates? [y/N] ")
        except EOFError:
            ans = "n"
        if ans.strip().lower() not in ("y", "yes"):
            print("aborted by user")
            return 1

    # 主循环：逐日 per-day session + graceful shutdown
    interrupt = _GracefulInterrupt()
    interrupt.install()
    redis_client = await _maybe_redis_client()
    total = len(to_process)
    print(f"\n[1/2] Backfilling {total} trade_dates...")
    success = 0
    fail = 0
    total_written = 0
    try:
        for i, td in enumerate(to_process, 1):
            if interrupt.stop:
                print(f"      interrupted at {i}/{total} (trade_date={td})")
                break
            ok, written = await _run_one_trade_date(td, calendar)
            if ok:
                success += 1
                total_written += written
            else:
                fail += 1
            if i % _PROGRESS_INTERVAL == 0 or i == total:
                print(
                    f"      {i}/{total}: trade_date={td} success={success} "
                    f"fail={fail} written={total_written}"
                )
                await _publish_progress(redis_client, {
                    "phase": "candidate_pool",
                    "current": i, "total": total,
                    "trade_date": str(td),
                    "success": success, "fail": fail,
                    "total_written": total_written,
                    "ts": datetime.utcnow().isoformat(),
                })
    finally:
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                logger.warning("backfill_redis_close_failed", exc_info=True)

    print(f"\n[2/2] Done: success={success} fail={fail} total_rows={total_written}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    exit_code = asyncio.run(_main())
    sys.exit(exit_code)
