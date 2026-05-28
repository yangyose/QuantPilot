"""Phase 14 §14-4.2：4 策略 × 3 state 多场景 panel 对比脚本。

枚举 4 策略 × 3 state 的 IC_daily 序列，按 year_month 聚合后构建 panel，
输出 summary CSV（每 (strategy, state) 一行：ic_mean/ic_std/n_months/t_stat）
+ heatmap PNG（每策略一张，rows=state，cols=year_month，颜色=ic_mean）。

用法：
  uv run python scripts/compare_strategy_ic_panels.py \\
      --start 2021-01 --end 2026-05

输出目录（默认 `backend/var/diagnostics/phase14/`）：
  ic_panels_summary.csv
  ic_heatmap_<strategy>.png

依赖：`factor_ic_window_state` 已有 IC_daily 行（§14-2 backfill_icir_rebalance.py 跑完后）；
4 strategy × 3 state = 12 组合，每组合空 → summary 行 n_months=0 + t_stat=None。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.factor_ic_repository import FactorICRepository
from quantpilot.engine.diagnostics.ic_aggregator import (
    ICRecord,
    aggregate_monthly,
    build_panel,
)

_STRATEGIES = ("trend", "momentum", "mean_reversion", "value")
_STATES = ("UPTREND", "DOWNTREND", "OSCILLATION")
_DEFAULT_OUT_DIR = Path(__file__).parents[1] / "var" / "diagnostics" / "phase14"


def _parse_year_month(s: str) -> date:
    return datetime.strptime(s, "%Y-%m").date()


def _end_of_month(d: date) -> date:
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


async def _load_panels(
    start: date, end: date,
) -> dict[tuple[str, str], pd.DataFrame]:
    """加载 4 strategy × 3 state = 12 个 monthly aggregate DataFrame。"""
    repo = FactorICRepository()
    panels: dict[tuple[str, str], pd.DataFrame] = {}
    async with AsyncSessionLocal() as session:
        for strategy in _STRATEGIES:
            # V1.0 简化：factor=strategy（与 apply_monthly_rebalance 一致）
            factor = strategy
            for state in _STATES:
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
                panels[(strategy, state)] = aggregate_monthly(records)
                print(f"[+] {strategy}/{state} loaded {len(records)} ic_daily rows")
    return panels


def _write_heatmaps(
    monthly_panels: dict[tuple[str, str], pd.DataFrame], out_dir: Path,
) -> None:
    """每策略一张 heatmap PNG：rows=state, cols=year_month，颜色=ic_mean。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[warn] matplotlib 未安装，跳过 heatmap PNG", file=sys.stderr)
        return

    for strategy in _STRATEGIES:
        # 收集该策略 3 state 的所有月份 union
        all_months: set[str] = set()
        for state in _STATES:
            df = monthly_panels.get((strategy, state))
            if df is not None and not df.empty:
                all_months.update(df["year_month"].tolist())
        if not all_months:
            print(f"[warn] {strategy} 无数据，跳过 heatmap")
            continue
        months_sorted = sorted(all_months)

        # 构建矩阵 rows=state, cols=month, value=ic_mean
        matrix = []
        for state in _STATES:
            df = monthly_panels.get((strategy, state), pd.DataFrame())
            if df.empty:
                matrix.append([float("nan")] * len(months_sorted))
                continue
            lookup = dict(zip(df["year_month"], df["ic_mean"]))
            matrix.append([float(lookup.get(m, float("nan"))) for m in months_sorted])

        fig, ax = plt.subplots(figsize=(max(8, len(months_sorted) * 0.25), 3.5))
        im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=-0.1, vmax=0.1)
        ax.set_yticks(range(len(_STATES)))
        ax.set_yticklabels(_STATES)
        ax.set_xticks(range(len(months_sorted)))
        ax.set_xticklabels(months_sorted, rotation=70, ha="right", fontsize=7)
        ax.set_title(f"IC heatmap — {strategy}")
        fig.colorbar(im, ax=ax, shrink=0.6, label="ic_mean")
        fig.tight_layout()
        png = out_dir / f"ic_heatmap_{strategy}.png"
        fig.savefig(png, dpi=120)
        plt.close(fig)
        print(f"[+] wrote {png}")


async def _run(start: date, end: date, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    monthly_panels = await _load_panels(start, end)

    panel_df = build_panel(monthly_panels)
    summary_csv = out_dir / "ic_panels_summary.csv"
    panel_df.to_csv(summary_csv, index=False)
    print(f"[+] wrote {summary_csv} ({len(panel_df)} rows)")

    _write_heatmaps(monthly_panels, out_dir)
    return summary_csv


def _main() -> int:
    parser = argparse.ArgumentParser(description="Phase 14 §14-4.2 4×3 panel 对比")
    parser.add_argument("--start", type=_parse_year_month, required=True, help="YYYY-MM")
    parser.add_argument("--end", type=_parse_year_month, required=True, help="YYYY-MM")
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()

    start_d = args.start
    end_d = _end_of_month(args.end)
    print(f"[+] panel window=[{start_d}, {end_d}]")
    asyncio.run(_run(start_d, end_d, args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
