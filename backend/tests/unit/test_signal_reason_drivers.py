"""买入理由补上「主要驱动」——SDD §9.1 的 `top_contributors` / `explanation` 那一半。

## 背景（2026-09-11 实测）

SDD §9.1 列了 `explanation`（综合理由）与 `top_contributors`（前 2 贡献策略）。实况：

- `top_contributors`：**全仓零引用**
- `explanation`：`Scorer.aggregate` **算了**（按 contribution 取前 2）**但零消费者**
  ——`grep '\.explanation'` 无任何命中，且它没进 `candidate_pool`，
  而信号生成读的正是 candidate_pool，所以算完即弃
- 用户实际看到的买入理由是「综合评分位列全市场 top 1.1%」——知道**排名**，
  不知道**为什么**

## ⚠️ 为什么不能直接把 scorer 那段代码接出去

`score_breakdown_raw` 的键是**内部英文名**（实测 `{"value": …, "mean_reversion": …}`），
原先 `scorer.py` 直接 `" · ".join(name ...)`，真接到用户面前会显示
「value · mean_reversion」，而 SDD 例子要的是「价值 · 均值回归」。

故先有 `STRATEGY_DISPLAY_NAMES`（单一事实来源 + 契约测试钉住它等于各策略类的
`display_name`），再有**一处**共享的 `build_top_drivers`，供 `Scorer` 与
`SignalGenerator` 同用——两处各写一份必然措辞分叉。

## 这条改动同时改善推送

`notification_service` 的买入模板里就有 `理由：{signal.reason}`，
所以 reason 变详细，WxPusher 推送**立刻**跟着变——那是用户真正接收信号的渠道。
"""
from __future__ import annotations

import pandas as pd

from quantpilot.core.strategy_registry import build_top_drivers
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.signal import SignalGenerator

TRADE_DATE = pd.Timestamp("2026-06-15").date()
gen = SignalGenerator()


def _bd(**contrib: float) -> dict:
    """构造 score_breakdown_raw 形状：{策略内部名: {z_raw, weight, contribution}}。"""
    return {
        k: {"z_raw": v, "weight": 0.5, "contribution": v}
        for k, v in contrib.items()
    }


# ── 纯函数：取前 N 个驱动并译名 ──────────────────────────────────────────────


def test_drv_01_uses_display_names_not_internal_keys() -> None:
    """必须输出中文展示名——这正是原 scorer 代码的缺陷所在。"""
    out = build_top_drivers(_bd(value=2.0, mean_reversion=1.5))
    assert out == "价值 · 均值回归", out
    assert "value" not in out and "mean_reversion" not in out


def test_drv_02_sorted_by_contribution_desc() -> None:
    """按 contribution 降序，不是 dict 插入序。"""
    assert build_top_drivers(_bd(trend=0.1, value=9.9)) == "价值 · 趋势跟踪"


def test_drv_03_top_n_respected() -> None:
    out = build_top_drivers(_bd(trend=3.0, value=2.0, momentum=1.0), top_n=2)
    assert out == "趋势跟踪 · 价值", out


def test_drv_04_none_when_unusable() -> None:
    """无 breakdown / 空 / 非 dict → None，**不编造**（C-4）。"""
    assert build_top_drivers(None) is None
    assert build_top_drivers({}) is None
    assert build_top_drivers("not a dict") is None  # type: ignore[arg-type]


def test_drv_05_unknown_strategy_falls_back_to_key() -> None:
    """未登记的策略名**原样保留**，不静默丢弃该驱动。

    丢弃会让「主要驱动」少一项且无人察觉；保留原名至少暴露出「有个没登记的策略」。
    """
    out = build_top_drivers(_bd(value=2.0, brand_new=1.9))
    assert out is not None and "价值" in out and "brand_new" in out


# ── 真实路径：买入理由里必须出现主要驱动 ────────────────────────────────────


def _buy(breakdown: dict | None):
    scores = pd.DataFrame(
        {
            "composite_score": [95.0],
            "composite_pct_in_market": [0.005],
            "score_breakdown": [breakdown],
        },
        index=pd.Index(["000001.SZ"], name="ts_code"),
    )
    quotes = pd.DataFrame(
        {
            "close": [10.0], "is_suspended": [False],
            "limit_up": [False], "avg_amount": [2.3e8],
        },
        index=pd.Index(["000001.SZ"], name="ts_code"),
    )
    sigs = gen.generate(scores, [], MarketStateEnum.UPTREND, quotes, TRADE_DATE)
    buys = [s for s in sigs if s.signal_type == "BUY"]
    assert buys, "未产生 BUY 信号，无法检查 reason"
    return buys[0]


def test_drv_06_buy_reason_includes_drivers() -> None:
    """走 generate() 的买入理由必须同时含「排名」与「主要驱动」。"""
    reason = _buy(_bd(value=2.0, mean_reversion=1.5)).reason
    assert "top" in reason, reason
    assert "主要驱动" in reason, reason
    assert "价值" in reason, reason


def test_drv_07_buy_reason_degrades_without_breakdown() -> None:
    """没有 breakdown → 保留原来的排名理由，**不出现空的「主要驱动：」**。"""
    reason = _buy(None).reason
    assert "top" in reason
    assert "主要驱动" not in reason, f"无驱动数据时不应出现该段：{reason!r}"
