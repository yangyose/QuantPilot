from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import pandas as pd
import pandas_ta as ta

from quantpilot.core.config_defaults import DEFAULT_MARKET_STATE, MarketStateConfig


class MarketStateEnum(StrEnum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    OSCILLATION = "OSCILLATION"


@dataclass(frozen=True)
class MarketStateRecord:
    trade_date: date
    market_state: MarketStateEnum
    trend_strength: float  # 0-100，ADX 值（已 clip）
    adx_value: float
    ma20: float
    ma60: float
    state_changed: bool  # 与前一已确认状态相比是否发生切换
    description: str
    # V1.5-A A3（SDD-EXT-07）：市场宽度弱信号。UPTREND 且 NH-NL 差值 ≤ 0（创 60 日
    # 新高家数 ≤ 新低家数）时置 True——「上涨趋势但宽度弱」，下游 Scorer 按 OSCILLATION
    # 权重压制趋势策略（market_state 仍报 UPTREND，仅策略配置软化）。默认 False。
    breadth_weak: bool = False


class MarketStateEngine:
    def __init__(
        self,
        config: MarketStateConfig | None = None,
        *,
        # 保留旧位参/关键字参数作为兼容入口（Phase 10 前的构造方式）
        ma_short: int | None = None,
        ma_long: int | None = None,
        adx_period: int | None = None,
        adx_threshold: float | None = None,
        debounce_days: int | None = None,
    ) -> None:
        cfg = config or MarketStateConfig(
            ma_short=ma_short if ma_short is not None else DEFAULT_MARKET_STATE.ma_short,
            ma_long=ma_long if ma_long is not None else DEFAULT_MARKET_STATE.ma_long,
            adx_period=(
                adx_period if adx_period is not None else DEFAULT_MARKET_STATE.adx_period
            ),
            adx_threshold=(
                adx_threshold
                if adx_threshold is not None
                else DEFAULT_MARKET_STATE.adx_threshold
            ),
            debounce_days=(
                debounce_days
                if debounce_days is not None
                else DEFAULT_MARKET_STATE.debounce_days
            ),
        )
        self._cfg = cfg
        self.ma_short = cfg.ma_short
        self.ma_long = cfg.ma_long
        self.adx_period = cfg.adx_period
        self.adx_threshold = cfg.adx_threshold
        self.debounce_days = cfg.debounce_days

    def compute_indicators(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """
        输入：ohlcv — columns=[high, low, close]（open 可选），index=date（升序）
        输出：在输入 DataFrame 上追加 [ma20, ma60, adx] 列，返回新 DataFrame。
        前 (ma_long-1) 行 ma60 为 NaN；前 ~27 行 adx 为 NaN（暖启动）。
        """
        df = ohlcv.copy()
        df[f"ma{self.ma_short}"] = df["close"].rolling(self.ma_short).mean()
        df[f"ma{self.ma_long}"] = df["close"].rolling(self.ma_long).mean()
        adx_df = ta.adx(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            length=self.adx_period,
        )
        # pandas-ta 在输入行数 < adx_period * 2 时返回 None（不抛异常）。
        # 把 df["adx"] 置 NaN 让下游 identify() 的 valid_mask 把这些日子过滤掉，
        # 与原 warm-up 行为一致；否则会 TypeError 'NoneType' object is not subscriptable
        # （2026-05-27 5y 回填早期日 2021-05-13 ~ ~2021-06-01 触发，daily_quote MIN
        # = 2021-05-13，ADX 14 期需 ~28 行才返回 DataFrame）。
        if adx_df is None or f"ADX_{self.adx_period}" not in adx_df.columns:
            df["adx"] = float("nan")
        else:
            df["adx"] = adx_df[f"ADX_{self.adx_period}"]
        return df

    def determine_raw_state(
        self, adx: float, ma20: float, ma60: float, close: float
    ) -> MarketStateEnum:
        """单行判定，无防抖动。纯函数。"""
        if adx > self.adx_threshold:
            if ma20 > ma60 and close > ma20:
                return MarketStateEnum.UPTREND
            elif ma20 < ma60 and close < ma20:
                return MarketStateEnum.DOWNTREND
            else:
                return MarketStateEnum.OSCILLATION
        else:
            return MarketStateEnum.OSCILLATION

    @staticmethod
    def compute_nh_nl_diff(
        close_wide: pd.DataFrame, lookback: int = 60
    ) -> float | None:
        """V1.5-A A3（SDD-EXT-07）：计算最后一日的 NH-NL 市场宽度差值（纯函数）。

        输入 ``close_wide``：index=trade_date（升序），columns=ts_code，values=close。
        NH-NL 差值 = (创 ``lookback`` 日新高家数 − 创 ``lookback`` 日新低家数) / 有效标的数。
        「创新高」= 该股当日 close == 近 ``lookback`` 日（含当日）close 最大值；新低同理取 min。

        数据不足（行数 < lookback）或无任何有效标的 → None（上游不据此降级）。
        """
        if close_wide.empty or len(close_wide) < lookback:
            return None
        window = close_wide.iloc[-lookback:]
        last = window.iloc[-1]
        col_max = window.max()
        col_min = window.min()
        # 仅统计当日 close 非 NaN 的标的
        valid = last.notna()
        n_valid = int(valid.sum())
        if n_valid == 0:
            return None
        new_high = int(((last >= col_max) & valid).sum())
        new_low = int(((last <= col_min) & valid).sum())
        return (new_high - new_low) / n_valid

    @staticmethod
    def compute_breadth_weak(
        state: MarketStateEnum, nh_nl: float | None
    ) -> bool:
        """V1.5-A A3（SDD-EXT-07）：市场宽度弱判定（纯函数）。

        仅 UPTREND 且 NH-NL 差值 ≤ 0 时 True（外部评审 §3.4 的 0% 分界）。
        非 UPTREND 规则不适用（恒 False）；nh_nl 缺失（None）→ False（数据不足不误降级）。
        """
        if state != MarketStateEnum.UPTREND or nh_nl is None:
            return False
        return nh_nl <= 0

    def apply_debounce(
        self,
        raw_states: pd.Series,
        prev_confirmed: MarketStateEnum = MarketStateEnum.OSCILLATION,
    ) -> pd.Series:
        """
        对 raw_states 序列逐日应用防抖动规则。
        返回同 index 的 confirmed_state 序列。纯函数。
        """
        confirmed_list: list[MarketStateEnum] = []
        current_confirmed = prev_confirmed

        for i in range(len(raw_states)):
            if i >= self.debounce_days - 1:
                # 检查最近 debounce_days 天的 raw state 是否一致
                window = [raw_states.iloc[j] for j in range(i - self.debounce_days + 1, i + 1)]
                if len(set(window)) == 1 and window[0] != current_confirmed:
                    current_confirmed = window[0]
            confirmed_list.append(current_confirmed)

        return pd.Series(confirmed_list, index=raw_states.index)

    def identify(
        self,
        ohlcv: pd.DataFrame,
        prev_confirmed: MarketStateEnum = MarketStateEnum.OSCILLATION,
        nh_nl_series: pd.Series | None = None,
    ) -> list[MarketStateRecord]:
        """
        完整流水线：compute_indicators → determine_raw_state → apply_debounce → records。
        仅返回所有指标均非 NaN 的日期的记录（即丢弃暖启动期）。
        prev_confirmed：OHLCV 窗口第一天之前的已确认状态（首次运行传 OSCILLATION）。
        description 由本方法按 §4.4 模板生成。

        V1.5-A A3（SDD-EXT-07）：``nh_nl_series``（index=trade_date，值=NH-NL 差值）
        由 Service 层预算传入（横截面市场宽度，单指数 OHLCV 内算不出）。逐日按
        ``compute_breadth_weak`` 置 ``MarketStateRecord.breadth_weak``；None → 全 False。
        """
        df = self.compute_indicators(ohlcv)
        ma_short_col = f"ma{self.ma_short}"
        ma_long_col = f"ma{self.ma_long}"

        # 丢弃暖启动期（任意指标为 NaN 的行）
        valid_mask = df[ma_short_col].notna() & df[ma_long_col].notna() & df["adx"].notna()
        df_valid = df[valid_mask]

        if df_valid.empty:
            return []

        # 计算 raw states（apply 避免 iterrows 的类型 upcast 问题）
        raw_states = df_valid.apply(
            lambda row: self.determine_raw_state(
                adx=row["adx"],
                ma20=row[ma_short_col],
                ma60=row[ma_long_col],
                close=row["close"],
            ),
            axis=1,
        )

        # 应用防抖动
        confirmed_states = self.apply_debounce(raw_states, prev_confirmed=prev_confirmed)

        # 构建记录列表
        records: list[MarketStateRecord] = []
        prev_state = prev_confirmed
        for idx in df_valid.index:
            row = df_valid.loc[idx]
            adx_val = float(row["adx"])
            ma20_val = float(row[ma_short_col])
            ma60_val = float(row[ma_long_col])
            confirmed = confirmed_states[idx]

            description = self._build_description(confirmed, adx_val, ma20_val, ma60_val)
            state_changed = confirmed != prev_state

            # A3：市场宽度弱判定（nh_nl_series 为 None 时 breadth_weak 恒 False）
            nh_nl_val: float | None = None
            if nh_nl_series is not None:
                try:
                    _v = nh_nl_series.get(idx)
                    nh_nl_val = float(_v) if _v is not None and pd.notna(_v) else None
                except Exception:
                    nh_nl_val = None
            breadth_weak = self.compute_breadth_weak(confirmed, nh_nl_val)

            records.append(
                MarketStateRecord(
                    trade_date=idx if isinstance(idx, date) else idx.date(),
                    market_state=confirmed,
                    trend_strength=min(adx_val, 100.0),
                    adx_value=adx_val,
                    ma20=ma20_val,
                    ma60=ma60_val,
                    state_changed=state_changed,
                    description=description,
                    breadth_weak=breadth_weak,
                )
            )
            prev_state = confirmed

        return records

    def identify_latest(
        self,
        ohlcv: pd.DataFrame,
        prev_confirmed: MarketStateEnum = MarketStateEnum.OSCILLATION,
        nh_nl_series: pd.Series | None = None,
    ) -> MarketStateRecord | None:
        """
        便捷方法：只返回 ohlcv 最后一行的 MarketStateRecord。
        历史数据不足时返回 None。日常生产调用入口。

        V1.5-A A3：``nh_nl_series`` 透传给 ``identify`` 供 breadth_weak 判定。
        """
        records = self.identify(
            ohlcv, prev_confirmed=prev_confirmed, nh_nl_series=nh_nl_series,
        )
        if not records:
            return None
        return records[-1]

    def _build_description(
        self, state: MarketStateEnum, adx: float, ma20: float, ma60: float
    ) -> str:
        """按 §4.4 模板生成 description。"""
        if state == MarketStateEnum.UPTREND:
            return f"上涨趋势：ADX={adx:.1f}，均线多头排列（MA20={ma20:.2f} > MA60={ma60:.2f}）"
        elif state == MarketStateEnum.DOWNTREND:
            return f"下跌趋势：ADX={adx:.1f}，均线空头排列（MA20={ma20:.2f} < MA60={ma60:.2f}）"
        elif adx <= self.adx_threshold:
            return f"震荡市：趋势强度不足（ADX={adx:.1f} ≤ {self.adx_threshold:.0f}），无明确方向"
        else:
            return f"震荡市：ADX={adx:.1f} 偏强但均线方向不明确"
