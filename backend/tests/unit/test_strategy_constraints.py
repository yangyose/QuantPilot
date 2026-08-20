"""V1.5-C C1-1：策略硬约束落点统一（`BaseStrategy.apply_constraints` 钩子）。

依据 docs/design/phases/v1_5_c_strategy_expansion.md §3.2 C1-1。

**问题背景**：Phase 11 五步管线经 `compute_strategy_factors` 取因子矩阵，
**从不调用 `strategy.score()`**，而 `compute_strategy_factors` 默认透传
`compute_raw_factors` → 写在 `score()` 里的三处硬约束在生产全部失效：
动量追高剔除、动量数据不足 guard、价值陷阱截断（SDD §7.2.4）。
其中价值陷阱一条尤其严重：value 策略占 composite 权重 0.57~0.87。

**表达方式的语义变更**：五步管线里因子先 Winsorize→中性化→Z-score，
Phase 4 的「置分 0 / 截断到 50」这类 0-100 分域操作已无意义，统一改为在
raw 因子域施加：
- 「剔除」类 → 命中行该策略**所有因子列置 NaN**（禁止置 0——Z-score 后 0 是
  横截面均值，置 0 等于给中性分而非排除）
- 「截断」类 → 命中行逐列 `min(raw, raw.quantile(0.5))`

因此旧路径（`score()`）的**数值表现随之改变**：追高股不再是 `score=0.0` 而是
从结果中消失，价值陷阱股不再是 `min(score, 50)` 而由截断后的 rank 决定。
契约「约束仍然生效」不破，但「数值等价」不成立——见 test_strategies_impl.py
中 MOM-02 / VAL-02 的同步改写。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from quantpilot.core.config_defaults import MomentumStrategyConfig
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from tests.unit.test_strategies_impl import _make_snapshot_for_strategies

# ============================================================
# UT-C1-01：apply_constraints 钩子存在性与两路径同源
# ============================================================

class _SpyStrategy(BaseStrategy):
    """记录 apply_constraints 是否被调用的探针策略。"""

    name = "spy"
    display_name = "探针"
    weights = {"f1": 1.0}

    def __init__(self) -> None:
        self.constraint_calls = 0

    def compute_raw_factors(
        self, universe: pd.Index, market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        return pd.DataFrame({"f1": [1.0, 2.0, 3.0]}, index=universe)

    def apply_constraints(
        self, raw: pd.DataFrame, universe: pd.Index, market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        self.constraint_calls += 1
        out = raw.copy()
        out.loc[out.index[0], "f1"] = float("nan")   # 剔除第一只
        return out

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        return "spy"


def test_ut_c1_01a_apply_constraints_defaults_to_identity() -> None:
    """UT-C1-01a: BaseStrategy.apply_constraints 存在且默认恒等返回。

    默认恒等是「不覆写的策略零行为变化」的保证——trend / mean_reversion 不该
    因为引入钩子而改变任何输出。
    """
    assert hasattr(BaseStrategy, "apply_constraints"), (
        "BaseStrategy 须提供 apply_constraints 钩子（C1-1）"
    )
    idx = pd.Index(["A", "B"], name="ts_code")
    raw = pd.DataFrame({"f1": [1.0, 2.0]}, index=idx)
    out = BaseStrategy.apply_constraints(None, raw, idx, {})  # type: ignore[arg-type]
    pd.testing.assert_frame_equal(out, raw)


def test_ut_c1_01b_compute_strategy_factors_routes_through_hook() -> None:
    """UT-C1-01b: 五步管线入口 compute_strategy_factors 必须经过 apply_constraints。"""
    strategy = _SpyStrategy()
    idx = pd.Index(["A", "B", "C"], name="ts_code")
    out = strategy.compute_strategy_factors(idx, {})  # type: ignore[arg-type]

    assert strategy.constraint_calls == 1, "compute_strategy_factors 未调用 apply_constraints"
    assert pd.isna(out.loc["A", "f1"]), "约束未生效在五步管线入口上"
    assert out.loc["B", "f1"] == 2.0


def test_ut_c1_01c_score_routes_through_same_hook() -> None:
    """UT-C1-01c: 旧路径 score() 与五步管线**同源**——同一个 apply_constraints。

    同源是 C2（F-Score 门控）的前提：门控只写一处，两条路径同时生效。
    """
    strategy = _SpyStrategy()
    idx = pd.Index(["A", "B", "C"], name="ts_code")
    results = strategy.score(idx, {})  # type: ignore[arg-type]

    assert strategy.constraint_calls == 1, "score() 未调用 apply_constraints"
    scored = {r.ts_code for r in results}
    assert "A" not in scored, "被剔除的标的不应出现在 score() 结果中"
    assert scored == {"B", "C"}


# ============================================================
# UT-C1-02：动量约束迁入 apply_constraints
# ============================================================

def _momentum_snapshot_with_chaser() -> tuple[list[str], MarketSnapshot, str]:
    """21 只股票，最后一只近 1M 暴涨 95%（落在前 5%）。"""
    codes = [f"S{i:02d}" for i in range(21)]
    price_series: dict[str, list[float]] = {}
    for i, code in enumerate(codes[:-1]):
        return_pct = 0.01 * (i + 1)
        price_series[code] = (
            [100.0] * 110 + [100.0 * (1 + return_pct / 20 * j) for j in range(1, 21)]
        )
    chaser = codes[-1]
    price_series[chaser] = [100.0] * 110 + [100.0 * (1 + 0.95 / 20 * j) for j in range(1, 21)]
    close_vals = [price_series[c][-1] for c in codes]
    return codes, _make_snapshot_for_strategies(
        codes, close_values=close_vals, price_series=price_series,
    ), chaser


def test_ut_c1_02a_anti_chasing_nulls_all_factor_columns() -> None:
    """UT-C1-02a: 追高剔除在 compute_strategy_factors 生效——命中行**全列 NaN**。

    禁止置 0：Z-score 后 0 是横截面均值，置 0 等于给了中性分而非排除。
    """
    codes, snapshot, chaser = _momentum_snapshot_with_chaser()
    factors = MomentumStrategy().compute_strategy_factors(pd.Index(codes), snapshot)

    assert factors.loc[chaser].isna().all(), (
        f"追高股 {chaser} 应全列 NaN，实际 {factors.loc[chaser].to_dict()}"
    )
    # 条数必须精确：21 只 × 5% = 1.05 → floor → 恰好剔除 1 只。
    # （改造前用 quantile(0.95) 当阈值，线性插值会让它剔除 2 只 = 9.5%。）
    excluded = factors.index[factors.isna().all(axis=1)].tolist()
    assert excluded == [chaser], f"应恰好剔除追高股 1 只，实际 {excluded}"


def test_ut_c1_02b_anti_chasing_not_applied_to_normal_stocks() -> None:
    """UT-C1-02b: 未命中追高阈值的股票因子值保持 compute_raw_factors 原值。"""
    codes, snapshot, chaser = _momentum_snapshot_with_chaser()
    strategy = MomentumStrategy()
    raw = strategy.compute_raw_factors(pd.Index(codes), snapshot)
    constrained = strategy.compute_strategy_factors(pd.Index(codes), snapshot)

    keep = [c for c in codes if c != chaser]   # 除追高股外全部逐值不变
    # C1-2 起 compute_raw_factors 多产一列 σ 供理由文本用，五步管线入口会摘掉
    # （见 MomentumStrategy.compute_strategy_factors）。按管线实际拿到的列比对，
    # 仍是逐值精确。
    pd.testing.assert_frame_equal(
        constrained.loc[keep], raw.loc[keep, constrained.columns],
    )


def test_ut_c1_02e_reversal_exclude_pct_is_consumed() -> None:
    """UT-C1-02e: reversal_exclude_pct 真正被计算消费（改参数 → 结果变）。

    兑现 momentum.py 类注释里【降级说明】的恢复条件之一（原文：reversal_exclude_pct
    仅作为 Pipeline 快照登记，未传入计算）。
    """
    codes, snapshot, _ = _momentum_snapshot_with_chaser()
    idx = pd.Index(codes)

    def _n_excluded(pct: float) -> int:
        strategy = MomentumStrategy(MomentumStrategyConfig(reversal_exclude_pct=pct))
        return int(
            strategy.compute_strategy_factors(idx, snapshot).isna().all(axis=1).sum()
        )

    # 21 只：floor(21×0.05)=1，floor(21×0.5)=10
    assert _n_excluded(0.05) == 1
    assert _n_excluded(0.5) == 10, "reversal_exclude_pct 未被消费"
    assert _n_excluded(0.0) == 0, "pct=0 应不剔除任何标的"


def test_ut_c1_02f_lookback_short_is_consumed() -> None:
    """UT-C1-02f: lookback_short 真正被数据不足 guard 消费（同上【降级说明】）。

    价格必须有离散度，否则会被追高剔除的无离散度判定（02g）抢先短路，
    测不到 guard 本身。
    """
    codes = ["A", "B", "C"]
    price_series = {c: [100.0 + i * (j + 1) * 0.1 for i in range(130)]
                    for j, c in enumerate(codes)}
    snapshot = _make_snapshot_for_strategies(
        codes,
        close_values=[price_series[c][-1] for c in codes],
        price_series=price_series,
    )
    snapshot["adj_prices"] = snapshot["adj_prices"].iloc[:, -80:]   # 80 列

    idx = pd.Index(codes)
    # 默认 lookback_short=60 → 80 > 60，不触发 guard
    default_factors = MomentumStrategy().compute_strategy_factors(idx, snapshot)
    assert not default_factors.isna().all().all(), "默认配置不应触发数据不足 guard"

    # lookback_short=100 → 80 <= 100，触发 guard
    strict = MomentumStrategy(MomentumStrategyConfig(lookback_short=100))
    assert strict.compute_strategy_factors(idx, snapshot).isna().all().all(), (
        "lookback_short 未被 guard 消费"
    )


def test_ut_c1_02g_no_dispersion_excludes_nobody() -> None:
    """UT-C1-02g: 全体近 1M 收益率相同 → 不剔除任何标的。

    无离散度时不存在「相对追高」。缺这道判定时 quantile 等于该唯一值、`>=`
    命中全体 → 整个动量策略被静默清空（改造前表现为全体置 0 分，同样的坑）。
    """
    codes = ["A", "B", "C", "D"]
    snapshot = _make_snapshot_for_strategies(codes, close_values=[10.0] * 4)  # 恒定价格
    factors = MomentumStrategy().compute_strategy_factors(pd.Index(codes), snapshot)

    assert not factors.isna().all(axis=1).any(), (
        f"无离散度时不应剔除任何标的，实际：\n{factors}"
    )


def test_ut_c1_02c_insufficient_price_history_nulls_everything() -> None:
    """UT-C1-02c: 价格列数不足以算 return_3m 时，整个因子矩阵全 NaN。

    原 guard 写在 score() 里（`return []`），五步管线取不到 → industry_rs 的
    50.0 占位值会成为唯一有效因子，rank 出 ~0.5 的均匀分污染 composite。
    """
    codes = ["A", "B", "C"]
    snapshot = _make_snapshot_for_strategies(codes, close_values=[10.0, 11.0, 12.0])
    # 截断到 40 列（< lookback_short=60 + 1）
    snapshot["adj_prices"] = snapshot["adj_prices"].iloc[:, -40:]

    factors = MomentumStrategy().compute_strategy_factors(pd.Index(codes), snapshot)
    assert factors.isna().all().all(), (
        f"数据不足时应全 NaN，实际存在有效值：\n{factors}"
    )


def test_ut_c1_02d_score_path_drops_chaser() -> None:
    """UT-C1-02d: 旧路径 score() 下追高股**从结果中消失**（语义变更，非置 0）。

    改造前该股 score=0.0 并带「追高剔除」reason 留在列表里。0-100 分域里的 0 是
    「最差」，比「不参与」更强的惩罚；raw 域统一后语义收敛为「不参与本策略」，
    由 Scorer 把权重分给其余策略。
    """
    codes, snapshot, chaser = _momentum_snapshot_with_chaser()
    results = MomentumStrategy().score(pd.Index(codes), snapshot)
    scored = {r.ts_code for r in results}

    assert chaser not in scored, f"追高股 {chaser} 应不参与本策略评分"
    assert scored == set(codes) - {chaser}, "除追高股外不应有标的缺席"
    assert all(r.score > 0 for r in results), (
        "不应再出现 score=0.0 的『追高剔除』占位行——剔除语义已收敛为不参与"
    )


# ============================================================
# UT-C1-03：价值陷阱截断迁入 apply_constraints（raw 域分位截断）
# ============================================================

def _value_snapshot_with_trap() -> tuple[list[str], MarketSnapshot, str]:
    """6 只同行业股票，TRAP = 低估值 + 低 ROE 的典型价值陷阱。

    **不复用 test_strategies_impl 的 helper**：那个 helper 的 pb 历史对每只股票是
    常数、pe 历史用了全局 enumerate 下标，导致 pe/pb 历史分位退化成同一个值
    （实测 6 只股票 5 个 1.0 + 1 个 0.5）。用退化数据写「截断到中位数」的断言，
    截与不截都成立 —— 是个假 RED，分辨不出行为变化。这里自建有区分度的历史：
    TRAP 当前估值处在自身历史最低端（→ 低估分位高），其余股票处在最高端。
    """
    codes = ["V01", "V02", "V03", "V04", "V05", "TRAP"]
    trap = "TRAP"
    base_date = date(2025, 1, 31)
    idx = pd.Index(codes, name="ts_code")

    # 当前值：TRAP 最便宜
    pe_now = {"V01": 30.0, "V02": 30.0, "V03": 30.0, "V04": 30.0, "V05": 30.0, trap: 5.0}
    pb_now = {"V01": 4.0, "V02": 4.0, "V03": 4.0, "V04": 4.0, "V05": 4.0, trap: 0.5}
    roe_now = {"V01": 15.0, "V02": 14.0, "V03": 13.0, "V04": 12.0, "V05": 11.0, trap: 1.0}

    daily_quotes = pd.DataFrame(
        {
            "close": [10.0] * len(codes),
            "pe_ttm": [pe_now[c] for c in codes],
            "pb": [pb_now[c] for c in codes],
            "amount": [1e7] * len(codes),
            "vol": [1e4] * len(codes),
            "limit_up": [False] * len(codes),
        },
        index=idx,
    )
    financials = pd.DataFrame(
        {
            "roe": [roe_now[c] for c in codes],
            "net_profit_yoy": [5.0] * len(codes),
            "debt_to_asset": [0.5] * len(codes),
            "sw_industry_l1": ["银行"] * len(codes),
        },
        index=idx,
    )

    # 历史：TRAP 的当前值在自身历史最低端；其余股票的当前值在自身历史最高端
    hist_dates = pd.date_range(end=base_date, periods=250, freq="B").date.tolist()
    rows, index_tuples = [], []
    for c in codes:
        for i, d in enumerate(hist_dates):
            k = i / (len(hist_dates) - 1)          # 0 → 1
            if c == trap:
                pe_h, pb_h = pe_now[c] * (1.0 + 3.0 * k), pb_now[c] * (1.0 + 3.0 * k)
            else:
                pe_h, pb_h = pe_now[c] * (0.25 + 0.75 * k), pb_now[c] * (0.25 + 0.75 * k)
            rows.append({"pe_ttm": pe_h, "pb": pb_h})
            index_tuples.append((c, d))
    pe_pb_history = pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(index_tuples, names=["ts_code", "trade_date"]),
    )

    prices = pd.DataFrame(
        [[10.0] * 130 for _ in codes],
        index=idx,
        columns=pd.date_range(end=base_date, periods=130, freq="B").date.tolist(),
    )
    snapshot: MarketSnapshot = {
        "trade_date": base_date,
        "adj_prices": prices,
        "daily_quotes": daily_quotes,
        "financials": financials,
        "pe_pb_history": pe_pb_history,
        "index_adj_prices": prices.mean().to_frame(name="close").T,
    }
    return codes, snapshot, trap


def test_ut_c1_03a_value_trap_truncated_in_raw_domain() -> None:
    """UT-C1-03a: ROE 低于行业中位数 → 各因子列截断至该列中位数（≤ median）。

    分位截断而非置 NaN：SDD §7.2.4 的语义是「上限为中位水平」而非「排除」，
    且分位截断在 rank / Z-score 下不变形。
    """
    codes, snapshot, trap = _value_snapshot_with_trap()
    strategy = ValueStrategy()
    idx = pd.Index(codes)
    raw = strategy.compute_raw_factors(idx, snapshot)
    constrained = strategy.compute_strategy_factors(idx, snapshot)

    # 前置判据：估值类因子上 TRAP 必须**严格高于**中位数，否则本用例分辨不出
    # 「截断了」和「没截断」——数据一退化就会真空通过（首版就踩到过）。
    discriminating = [
        c for c in ("pe_percentile", "pb_percentile")
        if float(raw.loc[trap, c]) > float(raw[c].quantile(0.5)) + 1e-9
    ]
    assert discriminating, (
        "测试数据退化：TRAP 的估值因子未高于中位数，本用例无法证伪截断逻辑。\n"
        f"raw=\n{raw}\nmedians=\n{raw.quantile(0.5)}"
    )

    for col in constrained.columns:
        median = float(raw[col].quantile(0.5))
        val = float(constrained.loc[trap, col])
        assert val <= median + 1e-9, (
            f"{col}: 价值陷阱行未截断到中位数，{val} > {median}"
        )


def test_ut_c1_03b_value_non_trap_rows_unchanged() -> None:
    """UT-C1-03b: ROE 不低于行业中位数的股票因子值不被改动。"""
    codes, snapshot, trap = _value_snapshot_with_trap()
    strategy = ValueStrategy()
    idx = pd.Index(codes)
    raw = strategy.compute_raw_factors(idx, snapshot)
    constrained = strategy.compute_strategy_factors(idx, snapshot)

    keep = [c for c in codes if c != trap]
    pd.testing.assert_frame_equal(constrained.loc[keep], raw.loc[keep])
