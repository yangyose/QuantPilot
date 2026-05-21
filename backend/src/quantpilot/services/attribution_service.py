"""AttributionService：Phase 12 多因子回归归因编排（§3.2.2）。

V1.0 简化：归因因子 = 4 策略（trend / momentum / mean_reversion / value）的
**合成后 strategy_z**（V1.5+ 切换风险因子层 Size/Value/Momentum/Beta 时另议）。

【实施路径说明】数据源选 ``candidate_pool.score_breakdown_raw[strategy]["z_raw"]``，
不走设计文档 §3.2.2 字面措辞的 ``factor_neutralized → 重算 strategy_z`` 路径。
两路径数值等价（同 cross-section、同公式：列向 mean + 横截面 standardize +
clip ±3.5σ），但直接读 Step 3 已落库的 z_raw 避免在 AttributionService 内重复
Step 3 实现导致与 Scorer 漂移。V1.0 简化注本身明示"合成后 strategy_z" = Step 3
输出 = z_raw，与本实施一致。设计文档 §3.2.2 措辞已同步修正。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.attribution_repository import (
    AttributionRepository,
    AttributionRow,
)
from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.attribution import run_ols
from quantpilot.models.business import AttributionHistory, CandidatePool
from quantpilot.models.market import DailyQuote

logger = logging.getLogger(__name__)

_STRATEGIES = ["trend", "momentum", "mean_reversion", "value"]


@dataclass(frozen=True)
class AttributionSummary:
    """区间累计归因摘要（GET /attribution/summary）。"""

    start: date
    end: date
    cum_beta: dict[str, float]      # 每因子 beta 累加
    avg_r_squared: float | None     # 月度 R² 均值
    total_sample: int               # 月度 sample_size 累加
    months: int                      # 覆盖 calc_date 月数


class AttributionService:
    """月末多因子归因写库 + 区间查询。"""

    def __init__(
        self,
        session: AsyncSession,
        repo: AttributionRepository,
        window_days: int = 20,
        lookback_months: int = 12,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self._session = session
        self._repo = repo
        self._window_days = window_days
        self._lookback_months = lookback_months
        self._calendar = calendar

    # ─── 月末批写入 ────────────────────────────────────────────────────────

    async def run_monthly(self, month_end: date) -> list[AttributionHistory]:
        """月末计算近 N 月归因，写入 attribution_history。

        步骤：
        1. 取 [month_end - lookback_months 月, month_end] candidate_pool 行
           （score_breakdown_raw 非空）
        2. 解析 score_breakdown_raw → DataFrame[(date, ts_code) × 4 strategy_z]
        3. 计算 forward_return（window=20 交易日）
        4. engine.run_ols(exposures, returns) → AttributionResult
        5. upsert 4 行 attribution_history（每因子一行，calc_date=month_end）

        样本不足 / OLS 奇异 → 返回空 list（best-effort 不阻塞 MonthlyScheduler）。
        """
        # 1. 取候选池行
        # Phase 13 启动核查阶段修复（评审 P1-4 + Phase 12 实施评审 P1-2）：
        # lookback_months 改用 TradingCalendar.get_prev_trade_date 严格交易日（
        # 20 交易日 × lookback_months ≈ 月内 20 个交易日）；calendar 未注入时
        # 保留 timedelta(days=30.5 × n) 日历天近似 fallback（单测路径无 calendar）。
        if self._calendar is not None:
            try:
                start = self._calendar.get_prev_trade_date(
                    month_end, n=20 * self._lookback_months,
                )
            except ValueError as exc:
                logger.warning(
                    "attribution_lookback_calendar_insufficient: %s, fallback to "
                    "calendar-days approximation", exc,
                )
                start = month_end - timedelta(days=int(self._lookback_months * 30.5))
        else:
            # 【降级说明】calendar 未注入（单元/集成测试路径）→ 用日历天近似，
            # 精度差异 ~1~2 个交易日，对 lookback=12 月 OLS 影响极小。
            start = month_end - timedelta(days=int(self._lookback_months * 30.5))
        stmt = (
            select(
                CandidatePool.trade_date,
                CandidatePool.ts_code,
                CandidatePool.score_breakdown_raw,
            )
            .where(
                CandidatePool.trade_date >= start,
                CandidatePool.trade_date <= month_end,
                CandidatePool.score_breakdown_raw.isnot(None),
            )
        )
        result = await self._session.execute(stmt)
        rows = list(result.all())
        if not rows:
            logger.info(
                "attribution_run_monthly_no_pool_rows: start=%s end=%s", start, month_end,
            )
            return []

        # 2. 解析 score_breakdown_raw → strategy_z 4 列
        exposure_records: list[dict] = []
        for trade_date, ts_code, breakdown in rows:
            if not isinstance(breakdown, dict):
                continue
            rec: dict = {"trade_date": trade_date, "ts_code": ts_code}
            for s in _STRATEGIES:
                entry = breakdown.get(s)
                rec[s] = (
                    float(entry["z_raw"])
                    if isinstance(entry, dict) and entry.get("z_raw") is not None
                    else None
                )
            exposure_records.append(rec)
        if not exposure_records:
            logger.info("attribution_run_monthly_no_strategy_z: month_end=%s", month_end)
            return []

        exposures_df = pd.DataFrame(exposure_records).set_index(["trade_date", "ts_code"])
        exposures_df = exposures_df[_STRATEGIES].astype(float)

        # 3. 计算 forward_return
        returns = await self._calc_forward_returns_panel(
            list({(td, tc) for td, tc, _ in rows}),
        )
        if returns.empty:
            logger.info(
                "attribution_run_monthly_no_forward_returns: month_end=%s pool_rows=%d",
                month_end, len(rows),
            )
            return []

        # 对齐 index + 部分截断可观测
        exposures_n = len(exposures_df)
        returns_n = len(returns)
        common = exposures_df.index.intersection(returns.index)
        common_n = len(common)
        if common_n == 0:
            logger.info(
                "attribution_run_monthly_no_index_overlap: month_end=%s "
                "exposures=%d returns=%d",
                month_end, exposures_n, returns_n,
            )
            return []
        # 部分截断告警（Phase 13 监控接入前的过渡可见性）：
        # 三类样本静默丢失——(a) base_d 当日停牌/数据空 → start_close ≤ 0；
        # (b) [base_d×1.4, base_d×1.5] 窗口内无 trade_date（跨节假日）；
        # (c) base_d > month_end - window_days×1.5（PIT 未来截断）。
        # 月末 calc_date=month_end 跑时，pool 行包含全部 base_d，最末 ~20 交易日的
        # base_d 永远拿不到 forward_return → 必然部分截断 ~17%；< 80% 时记 info。
        if common_n < exposures_n * 0.8:
            logger.info(
                "attribution_run_monthly_forward_returns_partial: "
                "month_end=%s exposures=%d returns=%d common=%d ratio=%.2f "
                "（窗口未来截断 / 停牌 / 假期 见 _calc_forward_returns_panel）",
                month_end, exposures_n, returns_n, common_n,
                common_n / exposures_n,
            )
        exposures_df = exposures_df.loc[common]
        returns = returns.loc[common]

        # 4. 跑 OLS
        ols_result = run_ols(exposures_df, returns, factors=_STRATEGIES)
        if ols_result is None:
            logger.info(
                "attribution_run_monthly_ols_none: month_end=%s sample=%d",
                month_end, len(common),
            )
            return []

        # 异常基线告警（Phase 12 §7.2 + Phase 13 监控接入前的过渡可见性）：
        # - r_squared > 0.5 → 横截面 OLS 单期罕见，疑似数据泄漏 / 暴露重复入侵
        # - r_squared < 0.005 → 因子完全失效或 forward_returns 噪声过大
        # - |beta| > 0.1 → 设计基线"≤ 0.05"边界外，单单位 z 暴露 ≥ 10% 月度收益
        if ols_result.r_squared > 0.5:
            logger.warning(
                "attribution_r_squared_high: month_end=%s r2=%.4f sample=%d "
                "（疑似数据泄漏 / 暴露重复，检查 exposures 与 returns 时间对齐）",
                month_end, ols_result.r_squared, ols_result.sample_size,
            )
        elif ols_result.r_squared < 0.005:
            logger.warning(
                "attribution_r_squared_low: month_end=%s r2=%.4f sample=%d "
                "（因子完全失效或 forward_returns 噪声过大）",
                month_end, ols_result.r_squared, ols_result.sample_size,
            )
        for factor_name, beta_val in ols_result.coefficients.items():
            if abs(beta_val) > 0.1:
                logger.warning(
                    "attribution_beta_extreme: month_end=%s factor=%s beta=%.4f "
                    "（超出设计基线 ≤ 0.05，检查因子定义是否异常）",
                    month_end, factor_name, beta_val,
                )

        # 5. upsert
        attribution_rows = [
            AttributionRow(
                calc_date=month_end,
                factor=factor,
                beta=ols_result.coefficients[factor],
                t_stat=ols_result.t_stats[factor],
                residual_std=ols_result.residual_std,
                r_squared=ols_result.r_squared,
                sample_size=ols_result.sample_size,
                window_days=self._window_days,
            )
            for factor in _STRATEGIES
        ]
        await self._repo.upsert_attribution(self._session, attribution_rows)

        return await self._repo.get_attribution_by_date_range(
            self._session, month_end, month_end,
        )

    # ─── 查询 ───────────────────────────────────────────────────────────────

    async def get_history(
        self, start: date, end: date, factor: str | None = None,
    ) -> list[AttributionHistory]:
        return await self._repo.get_attribution_by_date_range(
            self._session, start, end, factor=factor,
        )

    async def get_summary(self, start: date, end: date) -> AttributionSummary:
        """区间累计归因：每因子 cum_beta + 平均 R² + 月度 sample_size 总和。"""
        history = await self._repo.get_attribution_by_date_range(self._session, start, end)
        cum_beta: dict[str, float] = {f: 0.0 for f in _STRATEGIES}
        r_squared_vals: list[float] = []
        total_sample = 0
        months_seen: set[date] = set()
        for row in history:
            if row.factor in cum_beta:
                cum_beta[row.factor] += float(row.beta)
            if row.r_squared is not None and row.calc_date not in months_seen:
                r_squared_vals.append(float(row.r_squared))
            if row.calc_date not in months_seen:
                total_sample += int(row.sample_size)
                months_seen.add(row.calc_date)
        avg_r_squared = (
            sum(r_squared_vals) / len(r_squared_vals) if r_squared_vals else None
        )
        return AttributionSummary(
            start=start,
            end=end,
            cum_beta=cum_beta,
            avg_r_squared=avg_r_squared,
            total_sample=total_sample,
            months=len(months_seen),
        )

    # ─── 内部 helpers ───────────────────────────────────────────────────────

    async def _calc_forward_returns_panel(
        self, base_pairs: list[tuple[date, str]],
    ) -> pd.Series:
        """对每个 (base_date, ts_code) 算 base_date → base_date+window 交易日的简单收益。

        返回 Series(index=(base_date, ts_code), values=forward_return)。
        end_price 选 base_date 后 [window*1.4, window*1.5] 日历天窗口内最早的 close
        （以最大限度落在交易日）。
        """
        if not base_pairs:
            return pd.Series(dtype=float, name="forward_return")

        ts_codes = list({tc for _, tc in base_pairs})
        base_dates = list({bd for bd, _ in base_pairs})

        # 取 base_date 当日 close
        base_rows = await self._session.execute(
            select(DailyQuote.ts_code, DailyQuote.trade_date, DailyQuote.close).where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date.in_(base_dates),
            )
        )
        base_close: dict[tuple[date, str], float] = {
            (row.trade_date, row.ts_code): float(row.close)
            for row in base_rows
            if row.close is not None
        }
        if not base_close:
            return pd.Series(dtype=float, name="forward_return")

        # 对每个 base_date 求结束日历区间，再 union 一次性查
        min_base = min(base_dates)
        max_base = max(base_dates)
        approx_start = min_base + timedelta(days=int(self._window_days * 1.4))
        approx_end = max_base + timedelta(days=int(self._window_days * 1.5))

        end_rows = await self._session.execute(
            select(DailyQuote.ts_code, DailyQuote.trade_date, DailyQuote.close).where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date >= approx_start,
                DailyQuote.trade_date <= approx_end,
            )
        )
        # 每只股票按 trade_date 升序，找各 base_date 后第一个落在
        # [b*1.4, b*1.5] 窗口内的 close（≈ window 交易日后）。
        per_code: dict[str, list[tuple[date, float]]] = {}
        for row in end_rows:
            if row.close is None:
                continue
            per_code.setdefault(row.ts_code, []).append((row.trade_date, float(row.close)))
        for codes in per_code.values():
            codes.sort()

        returns: dict[tuple[date, str], float] = {}
        for (base_d, code), start_close in base_close.items():
            if start_close <= 0:
                continue
            window_lo = base_d + timedelta(days=int(self._window_days * 1.4))
            window_hi = base_d + timedelta(days=int(self._window_days * 1.5))
            end_close: float | None = None
            for trade_d, close in per_code.get(code, []):
                if trade_d < window_lo:
                    continue
                if trade_d > window_hi:
                    break
                end_close = close
                break
            if end_close is not None:
                returns[(base_d, code)] = (end_close - start_close) / start_close

        if not returns:
            return pd.Series(dtype=float, name="forward_return")
        index = pd.MultiIndex.from_tuples(returns.keys(), names=["trade_date", "ts_code"])
        return pd.Series(list(returns.values()), index=index, name="forward_return")
