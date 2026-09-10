"""UniverseFilter：基本面底线过滤，生成每日可投资宇宙（Phase 4）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from quantpilot.core.config_defaults import DEFAULT_UNIVERSE, UniverseConfig
from quantpilot.data.calendar import TradingCalendar

logger = logging.getLogger(__name__)

# 基本面字段覆盖率告警阈值：低于此值即认为该条过滤规则已实质失效。
# ⚠️ 原实现只在**恰好 100% 全 NULL** 时才告警，那是个报不出「几乎全死」的阈值。
# 2026-09-07 于 5434 实测：total_equity 在 5 年 21 个采样日中 10 天覆盖率低于 19%
# （最低 1.2%），而全 5y 面板 1114 日里只有 18 天触发过那条 WARNING——因为它要求
# 100%。「F-4 对 98.8% 的股票不生效」在日志里与完全健康长得一模一样。
# 取 0.5：正常年份实测 92~99%，季末真空期跌到 1~19%，两者之间没有别的形态。
_MIN_FIELD_COVERAGE = 0.5

# F-5「非连续亏损」看几个**有值**报告期。SDD §5.4 定义为「连续两期」。
# ⚠️ 取数方必须多给几期：未披露报告期的 NULL 占位行会占名额，
# 只取 2 期时至多剩 1 个有值期 → F-5 整条实质失效（2026-09-07 实测 87% 的股票如此）。
_F5_PERIODS = 2


@dataclass(frozen=True)
class UniverseStats:
    """逐条规则的边际剔除数（可观测性缺口，CLAUDE.md §6）。

    生产没有任何表持久化每日 universe 规模，容器重启后日志只剩当日一行——
    2026-09-03 `is_suspended` 修复、2026-09-07 F-4 修复上线后，
    「选股面扩大/收窄了百分之几」都无法回溯实证。

    ⚠️ **边际**而非累计：同一只股票违反多条时只记在**先执行**的那条上，
    故 `sum(excluded.values()) == total_in - total_out` 恒成立。累计计数会让
    各条之和超过实际剔除数，「F-4 剔了多少」就不可解读。

    ⚠️ 剔除 0 只的规则也必须**出现且为 0**：缺省即省略的话，「规则生效但没命中」
    与「规则整条没跑」在数据里长得一模一样，正是本次要根治的形态。
    """

    total_in: int
    total_out: int
    excluded: dict[str, int]


def _warn_if_low_coverage(values: pd.Series, field: str, rule: str) -> None:
    """字段覆盖率低于阈值时告警，并**报出真实覆盖率**。

    只说「跳过了」而不说「覆盖多少」，下次仍然只能分辨 0% 与非 0%。
    """
    n = len(values)
    if n == 0:
        return
    cov = float(values.notna().sum()) / n
    if cov < _MIN_FIELD_COVERAGE:
        logger.warning(
            "universe_filter_low_coverage: %s 覆盖率 %.1f%%（%d/%d）→ %s 实质失效",
            field, cov * 100, int(values.notna().sum()), n, rule,
        )


class UniverseFilter:
    """SDD §5.4：八条硬性过滤规则（F-1~F-8），Engine 层纯函数，无 IO。

    Phase 10：`config` 参数注入自 `config_service.get_universe_params()`；
    `filter()` 的 `min_avg_amount` 缺省回退 `self._cfg.min_liquidity_amount`。
    """

    FINANCIAL_INDUSTRIES: frozenset[str] = frozenset({
        "银行", "证券", "保险", "多元金融",
    })
    MIN_AVG_AMOUNT_DEFAULT: int = int(DEFAULT_UNIVERSE.min_liquidity_amount)

    def __init__(self, config: UniverseConfig | None = None) -> None:
        self._cfg = config or DEFAULT_UNIVERSE

    def filter(
        self,
        stock_info: pd.DataFrame,
        financials: pd.DataFrame,
        daily_quotes: pd.DataFrame,
        today: date,
        calendar: TradingCalendar,
        min_avg_amount: int | None = None,
        financials_history: pd.DataFrame | None = None,
    ) -> pd.Index:
        """返回通过全部过滤条件的 ts_code 集合（pd.Index）。纯函数，无 IO。

        统计版见 `filter_with_stats`；本方法是它的薄封装，**逻辑完全同源**——
        各写一份必然漂移，而"统计说剔了 3 只、实际选出的却不是那批"这种不一致
        在数字上看不出来。
        """
        return self.filter_with_stats(
            stock_info, financials, daily_quotes, today, calendar,
            min_avg_amount, financials_history,
        )[0]

    def filter_with_stats(
        self,
        stock_info: pd.DataFrame,
        financials: pd.DataFrame,
        daily_quotes: pd.DataFrame,
        today: date,
        calendar: TradingCalendar,
        min_avg_amount: int | None = None,
        financials_history: pd.DataFrame | None = None,
    ) -> tuple[pd.Index, UniverseStats]:
        """同 `filter`，另返回逐条规则的**边际**剔除数（见 `UniverseStats`）。

        返回的 Index 与 `filter` 逐值相等——可观测性改造不得改变选股结果，
        `tests/unit/test_universe_stats.py` 第一条即钉此不变量。

        参数：
          stock_info         — index=ts_code，含 is_st/list_date/is_suspended/sw_industry_l1
          financials         — index=ts_code，含 total_equity/net_profit_yoy/debt_to_asset
          daily_quotes       — index=ts_code，含 amount/vol/limit_up（F-7/F-8 专用）；
                               若含 avg_amount 列（P5-PRE-4），F-7 优先使用该列（20日均量）
          today              — 评分日
          calendar           — 交易日历（F-2 精确计算用）
          min_avg_amount     — F-7 成交额阈值（元）
          financials_history — MultiIndex(ts_code, report_period)，含 net_profit_yoy；
                               非 None 时 F-5 执行最近两期连续亏损检查（P5-PRE-4 恢复），
                               None 时降级为单期检查（向后兼容）
        """
        if min_avg_amount is None:
            min_avg_amount = int(self._cfg.min_liquidity_amount)
        idx = stock_info.index
        mask = pd.Series(True, index=idx)
        excluded: dict[str, int] = {}
        _remaining = len(idx)

        def _tally(rule: str) -> None:
            """记下本条规则的**边际**剔除数（相对上一条之后的剩余）。"""
            nonlocal _remaining
            now = int(mask.sum())
            excluded[rule] = _remaining - now
            _remaining = now

        # F-1：非 ST/*ST
        mask &= ~stock_info["is_st"].fillna(False).astype(bool)
        _tally("F-1")

        # F-2：上市满 60 交易日（list_date <= get_prev_trade_date(today, 60)）
        # get_prev_trade_date(today, 60) = 60 个交易日之前的那天（today 不含）
        # 即"距 today 恰好有 60 个交易日的 list_date"是可接受的最晚上市日
        min_list_date = calendar.get_prev_trade_date(today, 60)
        list_date_ok = stock_info["list_date"].apply(
            lambda d: (d is not None) and (not _is_missing(d))
            and (pd.Timestamp(d).date() <= min_list_date)
        )
        mask &= list_date_ok
        _tally("F-2")

        # F-3：非停牌
        mask &= ~stock_info["is_suspended"].fillna(False).astype(bool)
        _tally("F-3")

        # 金融股标识（F-4/F-5/F-6 豁免）
        is_financial = stock_info["sw_industry_l1"].isin(self.FINANCIAL_INDUSTRIES)

        # F-4：净资产为正（NaN → 跳过该条件）
        equity = _get_col(financials, "total_equity", idx)
        _warn_if_low_coverage(equity, "total_equity", "F-4 净资产过滤")
        equity_ok = equity.isna() | (equity > 0)
        mask &= (equity_ok | is_financial)
        _tally("F-4")

        # F-5：非连续亏损
        #
        # ⚠️ **`net_profit_yoy` 是净利润「同比增长率」，不是利润额**（2026-09-10 订正；
        # 原注释写「net_profit_yoy < 0 为亏损」是事实错误，会把后来的人带错）。
        # 实测 2025-12-31 期该字段分位：min −416 / 中位 **0.04** / max 88——小数形式的增长率。
        # 所以 `< 0` 的含义是「利润同比**下滑**」，一家盈利但增速放缓的公司也会命中。
        #
        # 【降级说明】当前降级内容 = 只判 SDD 第 261 行那条合取规则的后半
        # （「无改善趋势」= 同比增速仍为负），**漏掉前半「净利润亏损」**；
        # 原因 = 该规则写就时库里没有任何利润额字段，`net_profit_yoy` 是唯一可用代理
        #        （`eps` 直到 C2 于 2026-09-09 回填才有，alembic 0028）；
        # 恢复条件 = **暂不恢复**，且这是有意的——实测按规范补上「亏损」这一半会
        #        **主动变差**：真亏损组在开发集上月均 +1.766% 反而是三组里最好的
        #        （A 股低价亏损股投机性反弹），照规范改的净效果是 −0.620%/月。
        #        详见 `docs/reviews/universe_f5_loss_filter_2026-09-10.md`。
        #
        # ⚠️ 现行口径剔掉全市场约 **37%**（其中约 26.7% 是「两期都盈利、只是增速下滑」），
        # 是选股面的绝对主导项，而其净效果实测为 **−0.181%/月（t=−0.77）**——
        # 不显著，但没有任何正向证据。**放宽它同样需要 holdout 验证，别凭这句注释就动手。**
        # P5-PRE-4 恢复：当 financials_history (MultiIndex ts_code × report_period) 可用时，
        # 检查最近 2 期是否全为负；不足 2 期则降级为单期；无数据时跳过过滤。
        if financials_history is not None and not financials_history.empty:
            if "net_profit_yoy" in financials_history.columns:
                hist_yoy = financials_history["net_profit_yoy"]

                def _is_consistently_losing(ts_yoy: pd.Series) -> bool:
                    """最近 `_F5_PERIODS` 个**有值**报告期是否全为负。

                    ⚠️ 三处都不能省（2026-09-07 修，每一处对应一种失效形态）：

                    1. **按 report_period 倒序**再取前 N —— 取数方
                       `get_latest_n_financials` 的 SELECT 无 ORDER BY，
                       行序不保证；不排序就成了「随便两期」。
                    2. **先 dropna 再取 N**，不是先取 N 再 dropna —— 未披露报告期
                       每天写一条基本面全 NULL 的占位行，它会占掉一个名额。
                       这正是缺陷本体：87% 的股票因此只剩 1 期可用值。
                    3. **不足 N 个有值期 → 返回 False（不剔除）** —— 旧实现在此
                       「降级为单期」，等于数据不足时做「疑罪从有」的推定，
                       而生产上 87% 的股票正处于这个状态。
                    """
                    non_nan = ts_yoy.dropna()
                    if len(non_nan) < _F5_PERIODS:
                        return False  # 有值期不足 → 无法确认「连续」亏损，保留
                    # index 是 (ts_code, report_period)；按报告期取最近 N 个
                    newest = non_nan.sort_index(level=-1, ascending=False).iloc[
                        :_F5_PERIODS
                    ]
                    return bool((newest < 0).all())

                losing_mask = hist_yoy.groupby(level=0).apply(_is_consistently_losing)
                losing = losing_mask.reindex(idx).fillna(False)
                mask &= (~losing | is_financial)
            else:
                # financials_history 无 net_profit_yoy 列 → 降级为单期
                yoy = _get_col(financials, "net_profit_yoy", idx)
                yoy_ok = yoy.isna() | (yoy >= 0)
                mask &= (yoy_ok | is_financial)
        else:
            # ⚠️ **无历史数据时的降级分支：单期为负即剔除**，与上面「最近 2 个有值期」
            # 口径**不同**。这不是疏忽，是有意不动它——`BacktestEngine` 走的正是这条
            # （它不传 `financials_history`），改了会静默改变所有历史回测结果。
            #
            # 但这意味着**回测与生产用的不是同一条 F-5**，2026-09-07 修复后分歧变大
            # （生产：两个有值期皆负才剔；回测：一期为负即剔 → 回测 universe 更小）。
            # 属回测保真度问题，已登记 roadmap V1.5-L；在那之前，
            # **回测结论不可直接外推到生产选股面**。
            yoy = _get_col(financials, "net_profit_yoy", idx)
            _warn_if_low_coverage(yoy, "net_profit_yoy", "F-5 连亏过滤")
            yoy_ok = yoy.isna() | (yoy >= 0)
            mask &= (yoy_ok | is_financial)
        # ⚠️ 记在分支汇合处而非各分支内：F-5 有三条路径（历史多期 / 降级单期 /
        # 无历史），逐分支插会漏掉某一条，而漏掉的那条剔除数会被算进 F-6。
        _tally("F-5")

        # F-6：非高杠杆（debt_to_asset >= 0.9 排除，NaN → 跳过）
        d2a = _get_col(financials, "debt_to_asset", idx)
        _warn_if_low_coverage(d2a, "debt_to_asset", "F-6 高杠杆过滤")
        d2a_ok = d2a.isna() | (d2a < 0.9)
        mask &= (d2a_ok | is_financial)
        _tally("F-6")

        # F-7：流动性过滤（20日均成交额 >= min_avg_amount，NaN → 跳过）
        # P5-PRE-4 恢复：优先使用 avg_amount 列（get_avg_amount() 预计算），
        # 降级回 amount 列（当日单日成交额）以保持向后兼容。
        if "avg_amount" in daily_quotes.columns:
            amount = daily_quotes["avg_amount"].reindex(idx)
            amount_ok = amount.isna() | (amount >= min_avg_amount)
            mask &= amount_ok
        elif "amount" in daily_quotes.columns:
            amount = daily_quotes["amount"].reindex(idx)
            amount_ok = amount.isna() | (amount >= min_avg_amount)
            mask &= amount_ok
        # 同 F-5：两条分支 + 「两列都没有 → 不过滤」的隐含第三条，汇合处统一记。
        _tally("F-7")

        # F-8：涨停封死过滤（limit_up=True 且 vol=0 → 无法买入）
        if "limit_up" in daily_quotes.columns and "vol" in daily_quotes.columns:
            limit_up = daily_quotes["limit_up"].reindex(idx).fillna(False).astype(bool)
            vol = daily_quotes["vol"].reindex(idx).fillna(0)
            sealed = limit_up & (vol == 0)
            mask &= ~sealed
        _tally("F-8")

        return idx[mask], UniverseStats(
            total_in=len(idx), total_out=int(mask.sum()), excluded=excluded,
        )


def _is_missing(value: object) -> bool:
    """检查值是否为 None / NaT / NaN。"""
    try:
        return pd.isna(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value is None


def _get_col(df: pd.DataFrame, col: str, idx: pd.Index) -> pd.Series:
    """从 DataFrame 安全提取列，缺列时返回全 NaN。"""
    if col in df.columns:
        return df[col].reindex(idx)
    return pd.Series(float("nan"), index=idx)
