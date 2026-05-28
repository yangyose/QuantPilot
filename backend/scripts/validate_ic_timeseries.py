"""Phase 14 §14-4.1：IC 时序量级验证脚本。

读 `factor_ic_window_state` 的 ic_value 单点序列（row_type='daily'），
按 year_month 聚合，输出 CSV + matplotlib PNG。

用法：
  uv run python scripts/validate_ic_timeseries.py \\
      --strategy trend --factor trend --state UPTREND \\
      --start 2021-01 --end 2026-05

输出目录（默认 `backend/var/diagnostics/phase14/`）：
  ic_timeseries_<strategy>_<factor>_<state>.csv
  ic_timeseries_<strategy>_<factor>_<state>.png

依赖：5y candidate_pool 回填完成 + IC_daily 月末批已回算（§14-2 backfill_icir_rebalance.py）；
若 ic_daily 表为空 → 输出空 CSV + 跳过 PNG（不报错）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.factor_ic_repository import FactorICRepository
from quantpilot.engine.diagnostics.ic_aggregator import ICRecord, aggregate_monthly

_DEFAULT_OUT_DIR = Path(__file__).parents[1] / "var" / "diagnostics" / "phase14"


def _parse_year_month(s: str) -> date:
    return datetime.strptime(s, "%Y-%m").date()


def _end_of_month(d: date) -> date:
    from datetime import timedelta
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


async def _run(
    strategy: str, factor: str, state: str,
    start: date, end: date, out_dir: Path,
) -> tuple[int, Path]:
    repo = FactorICRepository()
    async with AsyncSessionLocal() as session:
        rows = await repo.get_ic_daily_window(
            session, strategy=strategy, factor=factor, state=state,
            start_date=start, end_date=end,
        )
    records = [
        ICRecord(
            trade_date=r.trade_date,
            ic_value=float(r.ic_value),
            sample_size=int(r.sample_size or 0),
        )
        for r in rows
        if r.ic_value is not None
    ]
    monthly_df = aggregate_monthly(records)

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{strategy}_{factor}_{state}"
    csv_path = out_dir / f"ic_timeseries_{suffix}.csv"
    monthly_df.to_csv(csv_path, index=False)

    # PNG 输出（matplotlib lazy import；空数据时跳过）
    if not monthly_df.empty:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(12, 4))
            ax.plot(monthly_df["year_month"], monthly_df["ic_mean"], marker="o", linewidth=1.0)
            ax.axhline(0, linestyle="--", color="gray", linewidth=0.8)
            ax.axhline(0.05, linestyle=":", color="green", linewidth=0.6)
            ax.axhline(-0.05, linestyle=":", color="red", linewidth=0.6)
            ax.set_title(f"IC 月度时序：{strategy} / {factor} / {state}")
            ax.set_xlabel("year_month")
            ax.set_ylabel("ic_mean")
            ax.tick_params(axis="x", rotation=60)
            fig.tight_layout()
            png_path = out_dir / f"ic_timeseries_{suffix}.png"
            fig.savefig(png_path, dpi=120)
            plt.close(fig)
        except ImportError:
            print("[warn] matplotlib 未安装，跳过 PNG", file=sys.stderr)

    return len(records), csv_path


def _main() -> int:
    parser = argparse.ArgumentParser(description="Phase 14 §14-4.1 IC 时序量级验证")
    parser.add_argument("--strategy", required=True, help="策略名（trend/momentum/...）")
    parser.add_argument("--factor", required=True, help="因子名（V1.0 简化等于 strategy）")
    parser.add_argument(
        "--state", required=True,
        choices=["UPTREND", "DOWNTREND", "OSCILLATION"],
    )
    parser.add_argument("--start", type=_parse_year_month, required=True, help="YYYY-MM")
    parser.add_argument("--end", type=_parse_year_month, required=True, help="YYYY-MM")
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()

    start_d = args.start
    end_d = _end_of_month(args.end)
    print(
        f"[+] {args.strategy}/{args.factor}/{args.state} "
        f"window=[{start_d}, {end_d}]"
    )
    n, csv = asyncio.run(
        _run(args.strategy, args.factor, args.state, start_d, end_d, args.out_dir),
    )
    print(f"[+] loaded {n} ic_daily rows → {csv}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
