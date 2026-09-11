"""买入信号的 `liquidity_note`（流动性提示）—— SDD §9.1 规定却从未实现。

## 背景

SDD §9.1 的「买入信号展示字段」表里第 12 行就是：

| liquidity_note | 流动性提示 | "近20日日均成交额2.3亿元，流动性充足" |

而 2026-09-11 实测生产 **5904 条信号里该字段非空 0 条**，全仓 `grep 'liquidity_note\\s*='`
**一个赋值点都没有**——只有 `TradeSignal` dataclass 上那个 `= None` 默认值。

⚠️ 它的形状是本仓最高发的那一族（CLAUDE.md §4.11「接了但没生效」）里**最彻底**的一种：
字段在 engine dataclass 上声明了、`signal_service` 把它写进 DB、`repository` 的 upsert
带着它、DB 有列、API schema 暴露它、前端 `types/api.ts` 也声明了类型——
**唯独没有任何代码产生过值**。整条管道通着，源头没接。
对比同一构造点的 `t1_warning`（`signal.py` 有字面量赋值）非空 5885 条。

## 判据选择

- 主要断言走**真实的 `gen.generate()` 路径**，不是单测一个 helper 就完事
  （§4.11：只测 helper 等于自证）
- 「改输入 → 结果必须变」：同一只股票换成薄流动性，措辞必须跟着变，
  否则恒返一句「流动性充足」也能让「非空」类断言全绿
- **窗口耦合**单独钉：文案里写死「近20日」，而 20 来自
  `strategy_service.py` 调 `get_avg_amount(..., window=20)`。
  那边改了这边不改，就是**对用户说谎**（声称 20 日、实际算的是别的窗口）。
"""
from __future__ import annotations

import ast
import pathlib

import pandas as pd

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.signal import RiskParams, SignalGenerator, build_liquidity_note

TRADE_DATE = pd.Timestamp("2026-06-15").date()
gen = SignalGenerator()
_MIN = float(RiskParams().min_liquidity_amount)  # 500 万元


def _scores(ts_codes: list[str], score: float = 95.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "composite_score": [score] * len(ts_codes),
            "composite_pct_in_market": [0.005] * len(ts_codes),
        },
        index=pd.Index(ts_codes, name="ts_code"),
    )


def _quotes(ts_codes: list[str], *, avg_amount: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [10.0] * len(ts_codes),
            "is_suspended": [False] * len(ts_codes),
            "limit_up": [False] * len(ts_codes),
            "avg_amount": [avg_amount] * len(ts_codes),
        },
        index=pd.Index(ts_codes, name="ts_code"),
    )


def _buy(avg_amount: float):
    sigs = gen.generate(
        _scores(["000001.SZ"]), [], MarketStateEnum.UPTREND,
        _quotes(["000001.SZ"], avg_amount=avg_amount), TRADE_DATE,
    )
    buys = [s for s in sigs if s.signal_type == "BUY"]
    assert buys, f"avg_amount={avg_amount} 下未产生 BUY，无法检查 liquidity_note"
    return buys[0]


# ── 真实路径：字段必须真的被填上 ───────────────────────────────────────────


def test_liq_01_buy_signal_carries_liquidity_note() -> None:
    """走 generate() 的 BUY 信号必须带流动性提示（此前恒为 None）。"""
    sig = _buy(avg_amount=2.3e8)
    assert sig.liquidity_note, "liquidity_note 仍为空——源头没有产生值"
    assert "近20日日均成交额" in sig.liquidity_note
    assert "2.3亿元" in sig.liquidity_note


def test_liq_02_wording_changes_with_liquidity() -> None:
    """**改输入 → 结果必须变**：薄流动性不得与充足流动性同一句话。

    只断言「非空」的话，恒返一句「流动性充足」照样绿——那会对着一只
    刚过门槛的标的说「充足」，比不给提示更糟。
    """
    ample = _buy(avg_amount=2.3e8).liquidity_note
    thin = _buy(avg_amount=_MIN * 1.1).liquidity_note
    assert ample != thin, f"充足与偏薄给出了同一句话：{ample!r}"
    assert "充足" in ample
    assert "充足" not in thin


# ── 纯函数：格式与分档 ─────────────────────────────────────────────────────


def test_liq_03_absent_when_amount_unknown() -> None:
    """成交额未知 → 返回 None，**不编一句提示**（C-4：不静默用占位值）。"""
    assert build_liquidity_note(float("nan"), _MIN) is None
    assert build_liquidity_note(None, _MIN) is None


def test_liq_04_unit_formatting() -> None:
    """>= 1 亿用「亿元」，否则用「万元」——SDD 例子用的是亿元。"""
    assert "2.3亿元" in (build_liquidity_note(2.3e8, _MIN) or "")
    assert "800万元" in (build_liquidity_note(8.0e6, _MIN) or "")


def test_liq_05_tiers_are_ordered() -> None:
    """三档措辞互不相同，且随成交额单调变化。"""
    notes = [build_liquidity_note(a, _MIN) for a in (_MIN * 1.1, _MIN * 3, _MIN * 20)]
    assert all(n for n in notes)
    assert len(set(notes)) == 3, f"三档给出了重复措辞：{notes}"


# ── 窗口耦合：文案写死「近20日」，取数那边必须真的是 20 ──────────────────


def test_liq_06_window_coupling_pinned() -> None:
    """`get_avg_amount(..., window=20)` 必须仍是 20，否则文案在对用户说谎。

    按 §4.11「调用点是否真传参」用 AST 查**调用点**，不构造替身。
    这条红了不代表 bug，代表**文案与取数窗口脱钩了**，两边要一起改。
    """
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "src/quantpilot/services/strategy_service.py").read_text(encoding="utf-8")
    calls = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "get_avg_amount"
    ]
    assert calls, "找不到 get_avg_amount 调用点（重构过？文案窗口需重新核对）"
    for c in calls:
        kw = {k.arg: k for k in c.keywords}
        assert "window" in kw, "调用点未显式传 window，文案里的「近20日」失去依据"
        assert isinstance(kw["window"].value, ast.Constant), "window 不再是字面量，请人工核对文案"
        assert kw["window"].value.value == 20, (
            f"window 已改为 {kw['window'].value.value}，"
            "但 build_liquidity_note 的文案仍写「近20日」——两边必须一起改"
        )
