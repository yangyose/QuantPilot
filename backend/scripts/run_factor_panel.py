"""V1.5-K 面板重跑驱动：逐交易日产出 K-2~K-6 全部统计量并落 `factor_panel_stat`。

⚠️ **只在本地算力中心（DB:5434）跑**。本脚本调用 `score_universe_for_date`
（全 universe 评分），生产 CLAUDE.md §6 运维红线① 明令禁止在生产机执行。

## 与 `backfill_daily_ic.py` 的分工

| | 表 | 用途 |
|---|---|---|
| `backfill_daily_ic.py` | `factor_ic_window_state` | 策略级 IC @ h=20，喂 ICIR 定实盘权重 |
| **本脚本** | `factor_panel_stat`（研究）| K-2~K-6 全部统计量，整批可丢弃重来 |

设计 §2.1 要求**运行时表一个字节都不动**，故两者完全分离，本脚本不碰前者。

## 为什么不把每天评分跑两遍

`turnover_jaccard` / `cost_drag` 需要相邻两日的因子矩阵。若每天把前一日也重算一遍，
13h 会变成 26h。故在循环内**把前一日的 composites 带到下一轮**——同一次连续运行内
免费，代价只是内存里多留一天。

⚠️ 副作用：**每段连续运行的第一天没有前一日**，故不产换手/成本行。断点续传后
同理。这是有意的取舍，不是缺陷——把「无前一日」硬凑成 J=0 会让那天伪装成
「换手 100%」（与 K-4 里「某因子只在一天存在」同一族错误）。

## 用法

    cd backend
    DATABASE_URL=postgresql+asyncpg://quantpilot:quantpilot@localhost:5434/quantpilot \\
      uv run python scripts/run_factor_panel.py --start 2021-05-13 --end 2026-06-30 \\
      --panel-run v1.5k-full-5y

    # 先试跑少量交易日（强烈建议：13h 的作业不要盲开）
    ... --limit-days 3

长任务务必 detached 起，判断存活看落盘 log 的 `panel_day` 行，
**不认通知、不认退出码**（CLAUDE.md §4.11）。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from quantpilot.core.database import AsyncSessionLocal  # noqa: E402
from quantpilot.data.calendar import TradingCalendar  # noqa: E402
from quantpilot.data.repository import MarketDataRepository  # noqa: E402
from quantpilot.engine.diagnostics.factor_panel import (  # noqa: E402
    compute_panel_stats_for_date,
)
from quantpilot.engine.diagnostics.multi_horizon import (  # noqa: E402
    HORIZONS,
    resolve_forward_returns,
)
from quantpilot.models.business import FactorPanelStat  # noqa: E402
from quantpilot.models.market import DailyQuote  # noqa: E402
from quantpilot.services.scoring_factory import build_default_scoring_service  # noqa: E402

logger = logging.getLogger("run_factor_panel")

# 日历缓冲（自然日）。给足即可，**不靠这个数字保证正确**——真正的保证是
# `_assert_calendar_covers` 在启动时显式验一遍。
#
# ⚠️ CLAUDE.md §4.4 点名过这一族：「同类缺陷会成群出现在脚本的日历回看缓冲上
# （backfill_daily_ic / backfill_candidate_pool / pipeline_multi_date /
# backfill_icir_rebalance），缓冲不足只表现为『跑通了但因子是残缺的』，不报错」。
# 本脚本首次试跑即被这个坑绊倒（universe.filter 要往前数 60 个交易日，
# 而我把日历起点设成了窗口起点）——那次是直接抛异常，比静默残缺幸运。
# 故此处不重复「凭经验选一个够大的数」，改为**选大 + 启动即校验**。
_CAL_BACK_DAYS = 400      # ≈ 273 交易日，覆盖 momentum 的 121 + universe 的 60
_CAL_FWD_DAYS = 120       # ≈ 82 交易日，覆盖 max(HORIZONS)=40
# 评分路径实际需要的回看交易日数：universe.filter 60 / momentum 价格窗口 121。
_REQUIRED_BACK_TRADING_DAYS = 130


def _assert_calendar_covers(
    calendar: TradingCalendar, first_day: date, last_day: date
) -> None:
    """启动即验日历两端够用，不够就**当场失败**。

    把「缓冲够不够」从一个经验数字变成一条被检查的前置条件。
    不这么做的话，回看不足会让 `resolve_price_window_start` 每日降级 →
    `rs_6m` 全 NaN → 产出的是「残缺 momentum 的面板」，而且**不报错**（§4.4）。
    """
    try:
        calendar.get_prev_trade_date(first_day, _REQUIRED_BACK_TRADING_DAYS)
    except ValueError as exc:
        raise SystemExit(
            f"日历回看不足：{first_day} 之前没有 {_REQUIRED_BACK_TRADING_DAYS} 个交易日。"
            f"评分路径需要它（universe.filter 60 / momentum 价格窗口 121）。"
            f"请扩大 _CAL_BACK_DAYS 或确认 trade_calendar 表覆盖范围。原始错误：{exc}"
        ) from exc
    try:
        calendar.get_next_trade_date(last_day, max(HORIZONS))
    except ValueError as exc:
        raise SystemExit(
            f"日历前瞻不足：{last_day} 之后没有 {max(HORIZONS)} 个交易日，"
            f"h={max(HORIZONS)} 的前向收益无从计算。请确认窗口末端与 trade_calendar "
            f"覆盖范围（设计 §2.5：末端按 h=40 可达性定为 2026-06-30）。原始错误：{exc}"
        ) from exc


async def _assert_price_history_covers(calendar: TradingCalendar, first_day: date) -> None:
    """验**行情数据**在 `first_day` 之前有足够历史，不够就当场失败。

    ⚠️ 这与 `_assert_calendar_covers` **不是同一件事**，2026-09-05 起跑时踩到：
    `trade_calendar` 表覆盖范围远大于 `daily_quote`（日历含未来前瞻、且回溯更早），
    所以「日历够」时「价格仍可能不够」——守卫照样放行，而 momentum/trend/reversion
    三个策略的因子会**静默全 NaN**（`Scorer.aggregate` 见 df 全 NaN 即跳过该策略），
    面板前段只剩 value 一个策略，每日行数 1224 → 306 却不报任何错。

    这正是 CLAUDE.md §4.4 C1-3 那一族：价格窗口深度不足 → 因子静默残缺。
    **窗口起点必须由「行情最早日 + 所需回看交易日数」推出，
    不能拿「行情最早日」直接当因子日起点**——中间少了一整段历史。
    """
    async with AsyncSessionLocal() as s:
        first_price = (
            await s.execute(select(func.min(DailyQuote.trade_date)))
        ).scalar_one_or_none()
    if first_price is None:
        raise SystemExit("daily_quote 为空，无法跑面板")

    have = calendar.count_trade_days(first_price, first_day) - 1  # 不含 first_day 自身
    if have < _REQUIRED_BACK_TRADING_DAYS:
        try:
            earliest_ok = calendar.get_next_trade_date(
                first_price, _REQUIRED_BACK_TRADING_DAYS
            )
        except ValueError:
            earliest_ok = None
        raise SystemExit(
            f"行情历史不足：{first_day} 之前只有 {have} 个交易日的行情"
            f"（daily_quote 最早 {first_price}），需要 {_REQUIRED_BACK_TRADING_DAYS} 个。"
            f"不足时 momentum/trend/mean_reversion 的因子会**静默全 NaN**、"
            f"面板前段只剩 value 一个策略且不报错。"
            + (f" 最早可用的因子日是 {earliest_ok}。" if earliest_ok else "")
        )


async def _done_dates(session, panel_run: str) -> set[date]:
    """已产出的交易日——断点续传据此跳过。

    按 `panel_run` 隔离：不同批次可并存比较（`panel_run` 是唯一键首列）。
    """
    rows = (
        await session.execute(
            select(FactorPanelStat.trade_date)
            .where(FactorPanelStat.panel_run == panel_run)
            .distinct()
        )
    ).scalars().all()
    return {d for d in rows}


async def _run_one_day(
    td: date,
    panel_run: str,
    calendar: TradingCalendar,
    prev_composites: list | None,
) -> tuple[int, list | None]:
    """跑一个交易日。返回 (写入行数, 本日 composites 供下一轮当 prev)。

    per-day 独立 session（§4.3：共用 outer session 会让 asyncpg 语句级 savepoint
    形成混合状态）。任一天失败不中断整批——面板要跑上千天。
    """
    async with AsyncSessionLocal() as session:
        scoring = build_default_scoring_service(session, calendar, None)
        composites = await scoring.score_universe_for_date(
            td, collect_factor_panel=True
        )
        if not composites:
            logger.info("panel_day trade_date=%s skipped=empty_universe", td)
            return 0, None

        repo = MarketDataRepository(session)
        ts_codes = [str(c.ts_code) for c in composites]
        # 取到最长 horizon 的右端；不足者由 resolve_forward_returns 逐个记原因跳过
        try:
            far_end = calendar.get_next_trade_date(td, max(HORIZONS))
        except ValueError:
            logger.warning("panel_day trade_date=%s skipped=calendar_short", td)
            return 0, composites
        adj = await repo.get_adj_prices_bulk(ts_codes, td, far_end)
        excluded = await repo.get_excluded_codes_for_ic(ts_codes, td, far_end)

        forwards, missing = resolve_forward_returns(
            adj, calendar, td, HORIZONS, excluded=excluded
        )
        if missing:
            # 不静默：分析侧据此才知道某 horizon 是「数据不可用」而非「无预测力」
            logger.warning(
                "panel_day trade_date=%s missing_horizons=%s", td, missing
            )

        points = compute_panel_stats_for_date(
            composites, forwards,
            state=str(getattr(composites[0], "market_state", "OSCILLATION")),
            universe_size=len(composites),
            prev_composites=prev_composites,
        )
        n = await repo.upsert_factor_panel_stat_bulk(panel_run, td, points)
        await session.commit()
        logger.info(
            "panel_day trade_date=%s universe=%d horizons=%d rows=%d",
            td, len(composites), len(forwards), n,
        )
        return n, composites


async def main() -> int:
    ap = argparse.ArgumentParser(description="V1.5-K 面板重跑（只在 DB:5434 跑）")
    ap.add_argument("--start", required=True, help="起始交易日 YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="结束交易日 YYYY-MM-DD（含）")
    ap.add_argument("--panel-run", required=True, help="批次标识，写入 panel_run 列")
    ap.add_argument("--limit-days", type=int, default=0, help="只跑前 N 个交易日（试跑）")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    async with AsyncSessionLocal() as s:
        repo = MarketDataRepository(s)
        calendar = await TradingCalendar.from_repo(
            repo,
            start - timedelta(days=_CAL_BACK_DAYS),
            end + timedelta(days=_CAL_FWD_DAYS),
        )
        done = await _done_dates(s, args.panel_run)

    todo = [d for d in calendar.get_trade_dates(start, end) if d not in done]
    if args.limit_days:
        todo = todo[: args.limit_days]
    if not todo:
        logger.info("panel_exit run=%s nothing_to_do", args.panel_run)
        return 0
    # 前置校验：两端都够用才开跑。13h 的作业不该在第 3 天才发现日历不够。
    _assert_calendar_covers(calendar, todo[0], todo[-1])
    await _assert_price_history_covers(calendar, todo[0])
    logger.info(
        "panel_start run=%s window=[%s, %s] todo=%d skipped_done=%d",
        args.panel_run, start, end, len(todo), len(done),
    )

    prev: list | None = None
    total = 0
    t0 = time.perf_counter()
    for i, td in enumerate(todo, 1):
        try:
            n, prev = await _run_one_day(td, args.panel_run, calendar, prev)
            total += n
        except Exception:
            # 任一天失败不中断整批；下一天的 prev 置空（避免跨越缺口算换手）
            logger.exception("panel_day_failed trade_date=%s", td)
            prev = None
        if i % 20 == 0:
            el = time.perf_counter() - t0
            logger.info(
                "panel_progress %d/%d rows=%d elapsed=%.0fs rate=%.1fs/day",
                i, len(todo), total, el, el / i,
            )

    logger.info(
        "panel_exit run=%s days=%d rows=%d elapsed=%.0fs",
        args.panel_run, len(todo), total, time.perf_counter() - t0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
