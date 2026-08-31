"""P0 退出域修复 EXIT-01~05（2026-08-28）。

**缺陷背景**（`docs/reviews/algo_framework_audit_2026-08-28.md` §1）：
退出判定寄生在 `composite_scores` 的循环里，而 `composite_scores` 来自 `get_pool()`
即候选池（日均 68 只）。持仓一旦跌出候选池就再也不会被判定止损——**跌得越狠越判不到**。
生产实证：四只持仓全部亏损超 8% 阈值、全部不在池中、全部零卖出信号（一只 −40.67%）。

配套缺陷：`compute_pool` 写了「持仓保护：holding_codes 强制入池」，但调用链四层每层
都默认 `frozenset()`，管线最外层根本没传 → 保护成空操作。生产 `candidate_pool` 全历史
87924 行中 `is_holding=true` 的有 **0 行**（2021-05-13 ~ 2026-08-25），
证明该保护**自项目开始就从未生效过**。

本文件只覆盖**纠正域错误**，不涉及退出判据变更（波动率标定 / 死区调窄 / 论点失效退出
等需先有评估手段，见审计报告 §10）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from quantpilot.core.config_defaults import UniverseConfig
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.signal import RiskParams, SignalGenerator


@dataclass
class _MockPosition:
    """简化版 Position（仅含 SignalGenerator 读取的字段）。"""

    ts_code: str
    pnl_pct: float = 0.0
    cost_price: float = 10.0


@dataclass
class _MockComposite:
    """简化版 CompositeScore（仅含 compute_pool 读取的字段）。

    `market_state` 必须是枚举——`compute_pool` 会取 `.value`。
    """

    ts_code: str
    composite_score: float
    trend_score: float = 0.0
    momentum_score: float = 0.0
    reversion_score: float = 0.0
    value_score: float = 0.0
    market_state: MarketStateEnum = MarketStateEnum.OSCILLATION


def _make_quotes(ts_codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": tc,
                "close": 10.0,
                "avg_amount": 1e8,
                "is_suspended": False,
                "limit_up": False,
                "sw_industry_l1": "TECH",
            }
            for tc in ts_codes
        ]
    ).set_index("ts_code")


def _pool_only(*ts_codes: str) -> pd.DataFrame:
    """构造只含"评分最高那批"的 composite——分位极低，永远够不到卖出阈值。"""
    return pd.DataFrame(
        {
            "composite_score": [70.0] * len(ts_codes),
            "composite_pct_in_market": [0.01] * len(ts_codes),
            "composite_z": [2.0] * len(ts_codes),
            "weights_source": ["icir"] * len(ts_codes),
        },
        index=pd.Index(list(ts_codes), name="ts_code"),
    )


# ======================================================================
# EXIT-01：持仓不在 composite_scores 中，亏损超阈值 → 仍须产出 hard_stop_loss
#          这是缺陷本体：当前实现产出 0 条
# ======================================================================
def test_exit_01_stop_loss_fires_for_holding_outside_composite() -> None:
    composite = _pool_only("POOL1.SZ", "POOL2.SZ")     # 池里没有 HOLD.SZ
    quotes = _make_quotes(["POOL1.SZ", "POOL2.SZ", "HOLD.SZ"])
    holding = _MockPosition(ts_code="HOLD.SZ", pnl_pct=-0.12)   # 亏 12% > 阈值 8%

    sigs = SignalGenerator().generate(
        composite_scores=composite,
        current_positions=[holding],
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 8, 28),
        risk_params=RiskParams(stop_loss_pct=0.08),
    )

    stops = [s for s in sigs if s.trigger_reason == "hard_stop_loss"]
    assert len(stops) == 1, (
        "持仓跌出候选池后仍须触发硬止损——止损依据是 position.pnl_pct，与评分无关。"
        f"实得信号：{[(s.ts_code, s.signal_type, s.trigger_reason) for s in sigs]}"
    )
    assert stops[0].ts_code == "HOLD.SZ"
    assert stops[0].signal_type == "SELL"


# ======================================================================
# EXIT-02：持仓在 composite_scores 中且亏损超阈值 → 只产 1 条，不因补判而重复
# ======================================================================
def test_exit_02_no_duplicate_when_holding_in_composite() -> None:
    composite = _pool_only("HOLD.SZ", "POOL1.SZ")      # 池里**有** HOLD.SZ
    quotes = _make_quotes(["HOLD.SZ", "POOL1.SZ"])
    holding = _MockPosition(ts_code="HOLD.SZ", pnl_pct=-0.15)

    sigs = SignalGenerator().generate(
        composite_scores=composite,
        current_positions=[holding],
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 8, 28),
        risk_params=RiskParams(stop_loss_pct=0.08),
    )

    stops = [s for s in sigs if s.ts_code == "HOLD.SZ" and s.signal_type == "SELL"]
    assert len(stops) == 1, f"同一持仓不得产出重复 SELL，实得 {len(stops)} 条"


# ======================================================================
# EXIT-03：持仓不在池中但亏损未超阈值 → 不产出（防补判变成过度触发）
# ======================================================================
def test_exit_03_no_signal_when_loss_within_threshold() -> None:
    composite = _pool_only("POOL1.SZ")
    quotes = _make_quotes(["POOL1.SZ", "HOLD.SZ"])
    holding = _MockPosition(ts_code="HOLD.SZ", pnl_pct=-0.03)   # 只亏 3%

    sigs = SignalGenerator().generate(
        composite_scores=composite,
        current_positions=[holding],
        market_state=MarketStateEnum.OSCILLATION,
        snapshot_quotes=quotes,
        trade_date=date(2026, 8, 28),
        risk_params=RiskParams(stop_loss_pct=0.08),
    )

    assert [s for s in sigs if s.ts_code == "HOLD.SZ"] == [], (
        "亏损未达阈值不得产出退出信号"
    )


# ======================================================================
# EXIT-04：compute_pool 的持仓保护真的被消费（正反两面）
#          反面用例是关键——只测"传了就生效"，退化回默认空集时照样绿
# ======================================================================
def test_exit_04_compute_pool_consumes_holding_codes() -> None:
    mgr = CandidatePoolManager(UniverseConfig(pool_capacity=2))
    scores = [
        _MockComposite("TOP1.SZ", 90.0),
        _MockComposite("TOP2.SZ", 80.0),
        _MockComposite("TOP3.SZ", 70.0),
        _MockComposite("HOLD.SZ", 1.0),          # 评分垫底，绝不可能进前 2
    ]

    with_holding = mgr.compute_pool(
        composite_scores=scores,
        holding_codes={"HOLD.SZ"},
        whitelist_codes=set(),
    )
    codes = {e.ts_code for e in with_holding}
    assert "HOLD.SZ" in codes, "传入 holding_codes 后持仓必须强制入池"
    held = [e for e in with_holding if e.ts_code == "HOLD.SZ"][0]
    assert held.is_holding is True, "入池的持仓须标记 is_holding=True"

    without = mgr.compute_pool(
        composite_scores=scores, holding_codes=set(), whitelist_codes=set(),
    )
    assert "HOLD.SZ" not in {e.ts_code for e in without}, (
        "不传 holding_codes 时垫底股不应入池——本断言钉住'参数真的被消费'，"
        "防止实现退化为忽略该参数后测试仍然全绿"
    )


# ======================================================================
# EXIT-05：管线**确实把非空 holding_codes 传下去**
#          本次缺陷的根因正是"参数存在但没人传"——只测 compute_pool 抓不到它
# ======================================================================
def test_exit_05_pipeline_passes_holding_codes() -> None:
    """用 AST 直接检查调用点，而不是 mock。

    本次缺陷的形态是**调用点漏传关键字参数**——`strategy_service` 链上三层（116/472/579）
    默认 `frozenset()`，终点 `compute_pool` 虽是必填参数但恒收到空集。任何"构造一个 spy
    再调用它"的测试都会自证式通过（写这条用例时先踩过一次：spy 版本在缺陷仍存在时照样绿）。
    能钉住"调用点真的传了"的，只有直接检查调用点。

    **作用域（判据不写作用域会产生假阴性）**：本用例只覆盖
    `pipeline/daily_pipeline.py`——生产每日管线这一条路径。全仓另有一个
    `run_daily_scoring` 生产调用点 `scripts/backfill_candidate_pool.py`，它**故意不传**
    `holding_codes`（传今天的持仓去重建历史候选池是 PIT 穿越），豁免理由写在该文件内。
    新增走每日管线的调用点时，把文件加进下面的 `_SCOPE`；新增回填类脚本前先判断
    PIT 语义，别无脑照抄本断言。
    """
    import ast
    import pathlib

    # 必须显式传 holding_codes 的文件（每日管线路径）。
    # 刻意**不**用「全仓扫 run_daily_scoring」——那会误伤 backfill_candidate_pool.py
    # 这个 PIT 豁免点，把一条正确的代码判成缺陷。
    _SCOPE = ["src/quantpilot/pipeline/daily_pipeline.py"]

    for rel in _SCOPE:
        src = pathlib.Path(rel).read_text(encoding="utf-8")
        tree = ast.parse(src)

        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_daily_scoring"
        ]
        assert calls, f"{rel} 中未找到 run_daily_scoring 调用点"

        for call in calls:
            kwargs = {kw.arg for kw in call.keywords if kw.arg}
            assert "holding_codes" in kwargs, (
                f"{rel} 调用 run_daily_scoring 时必须显式传 holding_codes。"
                "缺陷根因即此处漏传，导致 compute_pool 的持仓保护成为空操作——"
                "生产 candidate_pool 全历史 87924 行中 is_holding=true 的为 0 行，"
                "证明该保护自项目开始从未生效过"
            )
