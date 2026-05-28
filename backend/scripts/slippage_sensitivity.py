"""Phase 14 §14-4.3：滑点敏感性最小验证脚本。

对同一 5y 回测时间窗，配置 3 档滑点 [0.0005, 0.002, 0.005]（万 5 / 千 2 / 千 5）
跑同一 BacktestEngine，比较 sharpe / max_drawdown / ann_return 单调性。

用法：
  uv run python scripts/slippage_sensitivity.py \\
      --start 2021-05-13 --end 2026-05-26

输出（默认 `backend/var/diagnostics/phase14/slippage_sensitivity.csv`）：
  slippage,sharpe,max_drawdown,ann_return,total_return

设计 §6.3 真机-P14-4-3 DoD：sharpe(0.0005) - sharpe(0.005) ≥ 0.05。

依赖：5y candidate_pool/strategy_weights_history 已回填（§14-2 完成）。
单进程跑时 BacktestEngine 内部循环约 130-250s/day（与 5y 回填同量级）；
3 档滑点 = 跑 3 次同一 5y 数据，但 BacktestDataBundle 仅加载一次（共享）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from quantpilot.core.config import settings
from quantpilot.core.config_defaults import (
    DEFAULT_MARKET_STATE,
    DEFAULT_MEAN_REVERSION_STRATEGY,
    DEFAULT_MOMENTUM_STRATEGY,
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_STRATEGY_WEIGHTS,
    DEFAULT_TREND_STRATEGY,
    DEFAULT_UNIVERSE,
    DEFAULT_VALUE_STRATEGY,
)
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.backtest.engine import BacktestConfig, BacktestEngine
from quantpilot.engine.market_state import MarketStateEngine
from quantpilot.engine.position import PositionSizer
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.signal import SignalGenerator
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.engine.universe import UniverseFilter
from quantpilot.services.backtest_service import BacktestService

_DEFAULT_OUT_DIR = Path(__file__).parents[1] / "var" / "diagnostics" / "phase14"
_DEFAULT_SLIPPAGES = (0.0005, 0.002, 0.005)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_slippages(s: str) -> tuple[float, ...]:
    return tuple(float(x.strip()) for x in s.split(",") if x.strip())


def _build_engine(calendar: TradingCalendar) -> BacktestEngine:
    return BacktestEngine(
        strategies=[
            TrendStrategy(DEFAULT_TREND_STRATEGY),
            MomentumStrategy(DEFAULT_MOMENTUM_STRATEGY),
            MeanReversionStrategy(DEFAULT_MEAN_REVERSION_STRATEGY),
            ValueStrategy(DEFAULT_VALUE_STRATEGY),
        ],
        market_state_engine=MarketStateEngine(DEFAULT_MARKET_STATE),
        universe_filter=UniverseFilter(DEFAULT_UNIVERSE),
        scorer=Scorer(DEFAULT_STRATEGY_WEIGHTS),
        signal_engine=SignalGenerator(
            signal_cfg=DEFAULT_SIGNAL_CONFIG, universe_cfg=DEFAULT_UNIVERSE,
        ),
        position_engine=PositionSizer(),
        price_provider=None,
        calendar=calendar,
    )


async def _run(
    start: date, end: date, slippages: tuple[float, ...], out_dir: Path,
) -> Path:
    # 拉日历（前 120 天 buffer，与 backfill_candidate_pool.py 一致）
    adapter = TushareAdapter(token=settings.tushare_token)
    calendar = await TradingCalendar.from_adapter(
        adapter, start - timedelta(days=120), end + timedelta(days=30),
    )

    engine = _build_engine(calendar)

    # 数据 bundle 复用：3 档滑点共享同一份 5y 数据（仅 cfg.slippage_rate 不同）
    template_cfg = BacktestConfig(
        start_date=start, end_date=end,
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
    )
    print(f"[+] loading 5y data bundle [{start}, {end}] ...")
    async with AsyncSessionLocal() as session:
        service = BacktestService(session=session, engine=engine)
        data = await service._load_data_bundle(template_cfg)
    print(f"[+] data bundle loaded: {len(data.daily_quotes)} daily_quote rows")

    results: list[dict] = []
    for slip in slippages:
        cfg = BacktestConfig(
            start_date=start, end_date=end,
            initial_capital=1_000_000.0,
            strategy_config={},
            account_config={},
            slippage_rate=float(slip),
        )
        print(f"[+] running backtest slippage={slip} ...")
        result = engine.run(cfg, data, progress_cb=None)
        perf = result.performance or {}
        results.append({
            "slippage": float(slip),
            "sharpe": float(perf.get("sharpe_ratio", 0.0)),
            "max_drawdown": float(perf.get("max_drawdown", 0.0)),
            "ann_return": float(perf.get("annualized_return", 0.0)),
            "total_return": float(perf.get("total_return", 0.0)),
            "pipeline_mode": result.pipeline_mode,
        })
        print(
            f"    sharpe={results[-1]['sharpe']:.4f} "
            f"max_dd={results[-1]['max_drawdown']:.4f} "
            f"ann_ret={results[-1]['ann_return']:.4f} "
            f"mode={result.pipeline_mode}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "slippage_sensitivity.csv"
    pd.DataFrame(results).to_csv(csv_path, index=False)
    print(f"[+] wrote {csv_path}")

    if len(results) >= 2:
        diff = results[0]["sharpe"] - results[-1]["sharpe"]
        threshold = 0.05
        status = "✅ PASS" if diff >= threshold else "❌ FAIL"
        print(
            f"[+] DoD 真机-P14-4-3: sharpe({slippages[0]}) - "
            f"sharpe({slippages[-1]}) = {diff:.4f} ≥ {threshold} → {status}"
        )

    return csv_path


def _main() -> int:
    parser = argparse.ArgumentParser(description="Phase 14 §14-4.3 滑点敏感性最小验证")
    parser.add_argument("--start", type=_parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", type=_parse_date, required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--slippages", type=_parse_slippages,
        default=_DEFAULT_SLIPPAGES,
        help="逗号分隔，默认 0.0005,0.002,0.005",
    )
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()

    print(f"[+] window=[{args.start}, {args.end}] slippages={args.slippages}")
    asyncio.run(_run(args.start, args.end, args.slippages, args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
