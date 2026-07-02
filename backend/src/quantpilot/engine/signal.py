"""SignalGenerator：信号生成引擎。

Phase 5 初版；Phase 10 接入 ConfigService；Phase 11 §5 加入分位阈值主路径 +
四类 trigger_reason 细分（pct_below_buy / pct_above_sell / hard_stop_loss /
short_term_z_drop / mid_term_icir_flip）。Engine 层纯函数，无 IO。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from quantpilot.core.config_defaults import (
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_UNIVERSE,
    SignalConfig,
    UniverseConfig,
)
from quantpilot.engine.market_state import MarketStateEnum

logger = logging.getLogger(__name__)


@dataclass
class RiskParams:
    """SignalGenerator 参数（均可由 user_config 覆盖）。Phase 11 §5.1 新增分位阈值。"""

    # === Phase 11 新增（分位阈值，主路径）===
    buy_pct_threshold: float = 0.05         # composite_pct_in_market ≤ 此值触发 BUY
    sell_pct_threshold: float = 0.70        # composite_pct_in_market ≥ 此值触发 SELL
    strong_pct_threshold: float = 0.01      # composite_pct_in_market ≤ 此值标记 STRONG
    # 短期 z 降幅触发阈值（SELL trigger_reason = short_term_z_drop）
    short_term_failure_sigma: float = 1.5
    # L3 启用时回 V1.0-r5 绝对阈值路径
    enable_absolute_threshold_override: bool = False

    # === V1.0-r5 旧字段（保留兼容；enable_absolute_threshold_override=True 或缺 pct 数据时回退）===
    buy_threshold: float = 80.0          # 综合评分买入阈值（SDD §9.1）
    sell_threshold: float = 40.0         # 综合评分卖出阈值（SDD §9.2）
    stop_loss_pct: float = 0.08          # 硬止损比例（SDD §10.3）
    add_cost_deviation_pct: float = 0.10 # 加仓条件：价格偏离成本价≤±10%（SDD §10.1）
    min_liquidity_amount: float = 5_000_000.0  # 流动性阈值：20日均成交额≥500万元
    price_low_mult: float = 0.99         # 建议买入价区间下限：close × 0.99
    price_high_mult: float = 1.02        # 建议买入价区间上限：close × 1.02
    stop_loss_from_entry_pct: float = 0.08  # 止损价 = 建议买入价均值 × (1 - 8%)
    signal_strong_threshold: float = 90.0   # 绝对阈值场景的 STRONG 阈值（旧路径）


@dataclass
class TradeSignal:
    """Engine 层信号（纯函数输出），由 SignalService 映射为 ORM Signal 入库。

    Phase 11 §5 扩展：composite_z / composite_pct_in_market 反映新评分管线层 1/2
    输出；weights_source 透传 ICIR / default_matrix；trigger_reason 5 类细分。
    """

    ts_code: str
    signal_type: str                    # 'BUY' / 'SELL'
    trade_date: date
    score: float                        # 综合评分 0-100（兼容旧 UI 显示层）
    suggested_price_low: float | None = None
    suggested_price_high: float | None = None
    stop_loss_price: float | None = None
    suggested_pct: float | None = None   # PositionSizer 填充：建议买入占总资产比例
    signal_strength: str | None = None   # 'STRONG' / 'MODERATE'（仅买入信号）
    liquidity_note: str | None = None
    t1_warning: str = "A股T+1制度：买入当日不可卖出"
    reason: str = ""
    # 数据血缘（SignalService.save 写入 SignalScoreSnapshot 时使用）
    score_breakdown: dict | None = None  # {strategy: {score, weight, contribution}}
    raw_factors: dict | None = None      # {factor_name: value}

    # === Phase 11 §5 新增字段 ===
    composite_z: float | None = None
    composite_pct_in_market: float | None = None
    weights_source: str | None = None
    # pct_below_buy / pct_above_sell / hard_stop_loss / short_term_z_drop / mid_term_icir_flip
    trigger_reason: str | None = None


class SignalGenerator:
    """纯函数，无 IO。由 DailyPipeline CP3 调用（Phase 7），或测试直接调用。

    Phase 10：`signal_cfg` / `universe_cfg` 注入自 ConfigService；
    `generate()` 的 `risk_params` 显式覆盖入参保留（用于单元测试场景），
    未传入时按 `self._default_risk_params()` 从 dataclass 组装。
    """

    def __init__(
        self,
        signal_cfg: SignalConfig | None = None,
        universe_cfg: UniverseConfig | None = None,
    ) -> None:
        self._signal_cfg = signal_cfg or DEFAULT_SIGNAL_CONFIG
        self._universe_cfg = universe_cfg or DEFAULT_UNIVERSE

    def _default_risk_params(self) -> RiskParams:
        """从注入的 dataclass 组装 RiskParams。

        Phase 11 §7.1：透传 5 个分位字段（buy_pct / sell_pct / strong_pct /
        short_term_failure_sigma / enable_absolute_threshold_override）。
        SignalConfig 缺少新字段时（旧测试 mock）回退 dataclass 默认值。
        """
        s = self._signal_cfg
        u = self._universe_cfg
        return RiskParams(
            buy_threshold=s.buy_threshold,
            sell_threshold=s.sell_threshold,
            stop_loss_pct=s.stop_loss_pct,
            add_cost_deviation_pct=s.add_cost_deviation_pct,
            min_liquidity_amount=u.min_liquidity_amount,
            price_low_mult=s.price_low_mult,
            price_high_mult=s.price_high_mult,
            stop_loss_from_entry_pct=s.stop_loss_pct,
            signal_strong_threshold=s.strong_threshold,
            # Phase 11 §7.1 分位主路径字段
            buy_pct_threshold=getattr(s, "buy_pct_threshold", 0.05),
            sell_pct_threshold=getattr(s, "sell_pct_threshold", 0.70),
            strong_pct_threshold=getattr(s, "strong_pct_threshold", 0.01),
            short_term_failure_sigma=getattr(s, "short_term_failure_sigma", 1.5),
            enable_absolute_threshold_override=getattr(
                s, "enable_absolute_threshold_override", False,
            ),
        )

    def generate(
        self,
        composite_scores: pd.DataFrame,
        current_positions: list,
        market_state: MarketStateEnum,
        snapshot_quotes: pd.DataFrame,
        trade_date: date,
        risk_params: RiskParams | None = None,
        holding_signal_states: dict[str, dict] | None = None,
    ) -> list[TradeSignal]:
        """信号生成（Phase 11 §5 分位阈值主路径 + V1.0-r5 旧绝对阈值 fallback）。

        买入触发（Phase 11 §5.1）：
        - 优先用 ``composite_pct_in_market <= buy_pct_threshold``（默认 0.05）
        - 若 composite_pct_in_market 缺失（candidate_pool 无新列）或
          ``enable_absolute_threshold_override=True`` → 回退到 ``score > buy_threshold``
        - STRONG ↔ ``composite_pct_in_market <= strong_pct_threshold``（默认 0.01）

        卖出 trigger_reason 优先级（Phase 11 §5.2，逐级降级）：
        1. pct_above_sell ↔ ``composite_pct_in_market >= sell_pct_threshold``
        2. hard_stop_loss ↔ ``pnl_pct <= -stop_loss_pct``
        3. short_term_z_drop ↔ holding_signal_states[ts_code]['short_term_z_drop_value']
           > short_term_failure_sigma
        4. mid_term_icir_flip ↔ holding_signal_states[ts_code]['mid_term_icir_flipped'] == True

        ``holding_signal_states`` 由 SignalService 在调 generate 前预计算（查
        signal_score_snapshot 上日 factor_orthogonal + factor_ic_window_state
        近 1 月聚合），缺失键 → 该条件不触发（自然降级）。

        SDD §9.5 不变：持仓评分位于 (sell_pct_threshold, buy_pct_threshold) 区间
        既不卖也不买；加仓规则沿用 SDD §10.1。
        """
        params = risk_params or self._default_risk_params()
        signals: list[TradeSignal] = []
        holding_signal_states = holding_signal_states or {}

        # 构建持仓查找表：ts_code → position
        position_map = {p.ts_code: p for p in current_positions}
        holding_codes = set(position_map.keys())

        # composite_pct_in_market 列存在性（决定主路径 vs 旧路径）
        has_pct_col = "composite_pct_in_market" in composite_scores.columns
        has_z_col = "composite_z" in composite_scores.columns
        has_ws_col = "weights_source" in composite_scores.columns

        has_score_col = "composite_score" in composite_scores.columns
        for ts_code, row in composite_scores.iterrows():
            score = float(row["composite_score"]) if has_score_col else None
            pct_val: float | None = None
            z_val: float | None = None
            ws_val: str | None = None
            if has_pct_col:
                raw = row.get("composite_pct_in_market")
                if raw is not None and not pd.isna(raw):
                    pct_val = float(raw)
            if has_z_col:
                raw = row.get("composite_z")
                if raw is not None and not pd.isna(raw):
                    z_val = float(raw)
            if has_ws_col:
                raw = row.get("weights_source")
                if raw is not None and not pd.isna(raw):
                    ws_val = str(raw)

            # 当 pct_val 缺失或显式回退时走旧绝对阈值路径
            use_pct_path = (
                pct_val is not None and not params.enable_absolute_threshold_override
            )

            # 获取行情快照
            if ts_code not in snapshot_quotes.index:
                continue
            q = snapshot_quotes.loc[ts_code]
            is_suspended = bool(q.get("is_suspended", False))
            limit_up = bool(q.get("limit_up", False))
            avg_amount = float(q.get("avg_amount", float("nan")))
            close = float(q.get("close", float("nan")))

            # ─── 持仓分支：先 SELL 判定 → 再加仓 BUY ───
            if ts_code in holding_codes:
                pos = position_map[ts_code]
                pnl_pct = float(pos.pnl_pct) if pos.pnl_pct is not None else 0.0
                cost_price = float(pos.cost_price) if pos.cost_price is not None else close
                states = holding_signal_states.get(ts_code, {})

                # 卖出 trigger_reason 优先级（§5.2）
                sell_trigger: str | None = None
                sell_reason: str = ""
                if use_pct_path and pct_val is not None and pct_val >= params.sell_pct_threshold:
                    sell_trigger = "pct_above_sell"
                    sell_reason = (
                        f"评分跌出（市场分位 {pct_val * 100:.1f}% ≥ 阈值 "
                        f"{params.sell_pct_threshold * 100:.1f}%）"
                    )
                elif not use_pct_path and score is not None and score < params.sell_threshold:
                    # 旧 V1.0-r5 路径
                    sell_trigger = "pct_above_sell"  # 同语义，标记为 pct_above_sell
                    sell_reason = (
                        f"评分 {score:.1f} 低于卖出阈值 {params.sell_threshold}"
                    )
                elif pnl_pct <= -params.stop_loss_pct:
                    sell_trigger = "hard_stop_loss"
                    sell_reason = (
                        f"硬止损（浮亏 {pnl_pct:.1%}，阈值 -{params.stop_loss_pct:.1%}）"
                    )
                else:
                    z_drop = states.get("short_term_z_drop_value")
                    icir_flipped = bool(states.get("mid_term_icir_flipped", False))
                    if z_drop is not None and float(z_drop) > params.short_term_failure_sigma:
                        sell_trigger = "short_term_z_drop"
                        sell_reason = (
                            f"短期核心策略 z 降幅 {float(z_drop):.2f} 超阈值 "
                            f"{params.short_term_failure_sigma}"
                        )
                    elif icir_flipped:
                        sell_trigger = "mid_term_icir_flip"
                        sell_reason = "中期核心策略 ICIR 月度由正转负"

                if sell_trigger is not None:
                    signals.append(TradeSignal(
                        ts_code=ts_code,
                        signal_type="SELL",
                        trade_date=trade_date,
                        score=score if score is not None else 0.0,
                        reason=sell_reason,
                        composite_z=z_val,
                        composite_pct_in_market=pct_val,
                        weights_source=ws_val,
                        trigger_reason=sell_trigger,
                    ))
                    continue

                # 持仓但未触发 SELL，判定是否加仓 BUY
                if use_pct_path:
                    buy_triggered = pct_val is not None and pct_val <= params.buy_pct_threshold
                else:
                    buy_triggered = score is not None and score > params.buy_threshold
                if not buy_triggered:
                    continue

                # 加仓条件（SDD §10.1）：任一满足即可
                can_add = False
                if pnl_pct > 0:
                    can_add = True
                else:
                    price_deviation = (
                        abs(close - cost_price) / cost_price if cost_price > 0 else 1.0
                    )
                    if (
                        price_deviation <= params.add_cost_deviation_pct
                        and market_state != MarketStateEnum.DOWNTREND
                    ):
                        can_add = True
                if not can_add:
                    continue

            else:
                # ─── 无持仓分支 ───
                # V1.5-G G-4d-1：pct_above_sell 是客观市场事实（评分跌出全市场前列），
                # 对全体持有者均有意义 → 即使无持仓上下文也产**共享** SELL。管线去账户后
                # generate_for_date 以 current_positions=[] 调用，持仓私有 SELL（hard_stop_loss /
                # 加仓 / 短中期翻转）移到 API 请求期按用户账户叠加（§2 派生语义）。
                # 仅分位主路径适用（旧绝对阈值路径无市场分位，SELL 仍限持仓分支）。
                if (
                    use_pct_path
                    and pct_val is not None
                    and pct_val >= params.sell_pct_threshold
                ):
                    signals.append(TradeSignal(
                        ts_code=ts_code,
                        signal_type="SELL",
                        trade_date=trade_date,
                        score=score if score is not None else 0.0,
                        reason=(
                            f"评分跌出（市场分位 {pct_val * 100:.1f}% ≥ 阈值 "
                            f"{params.sell_pct_threshold * 100:.1f}%）"
                        ),
                        composite_z=z_val,
                        composite_pct_in_market=pct_val,
                        weights_source=ws_val,
                        trigger_reason="pct_above_sell",
                    ))
                    continue

                # BUY 触发判定
                if use_pct_path:
                    if pct_val is None or pct_val > params.buy_pct_threshold:
                        continue
                else:
                    if score is None or score <= params.buy_threshold:
                        continue

            # 通用买入条件检查
            if is_suspended or limit_up:
                continue
            if not pd.isna(avg_amount) and avg_amount < params.min_liquidity_amount:
                continue
            if pd.isna(close):
                continue

            # 构造 BUY 信号
            price_low = round(close * params.price_low_mult, 3)
            price_high = round(close * params.price_high_mult, 3)
            mid = (price_low + price_high) / 2
            stop_loss = round(mid * (1 - params.stop_loss_from_entry_pct), 3)
            if use_pct_path and pct_val is not None:
                strength = (
                    "STRONG" if pct_val <= params.strong_pct_threshold else "MODERATE"
                )
                buy_reason = f"综合评分位列全市场 top {pct_val * 100:.1f}%"
            else:
                strength = (
                    "STRONG" if score is not None and score >= params.signal_strong_threshold
                    else "MODERATE"
                )
                buy_reason = f"综合评分 {score:.1f}" if score is not None else "BUY"

            # 提取数据血缘字段
            has_breakdown = "score_breakdown" in composite_scores.columns
            breakdown = row.get("score_breakdown") if has_breakdown else None
            raw = row.get("raw_factors") if "raw_factors" in composite_scores.columns else None
            score_breakdown = breakdown if isinstance(breakdown, dict) else None
            raw_factors = raw if isinstance(raw, dict) else None

            signals.append(TradeSignal(
                ts_code=ts_code,
                signal_type="BUY",
                trade_date=trade_date,
                score=score if score is not None else 0.0,
                suggested_price_low=price_low,
                suggested_price_high=price_high,
                stop_loss_price=stop_loss,
                signal_strength=strength,
                t1_warning="A股T+1制度：买入当日不可卖出",
                reason=buy_reason,
                score_breakdown=score_breakdown,
                raw_factors=raw_factors,
                composite_z=z_val,
                composite_pct_in_market=pct_val,
                weights_source=ws_val,
                trigger_reason="pct_below_buy",
            ))

        return signals
