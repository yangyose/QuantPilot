"""MF-STR-01~08: MoneyFlowStrategy（V1.5-C C4 / 设计 §6.5，v0.13 后只做主力资金）。

因子（纯函数、无 IO）：
- `main_net_inflow_5d`  = Σ5（特大+大单买入 − 特大+大单卖出）/ Σ5 成交额
- `main_net_inflow_20d` = 同上，20 日
两者都是「净流入占比」——去市值量纲，大票小票可比；方向「越高越好」与其余策略一致。

判据按 CLAUDE.md §4.4：
- **临界点两侧都钉**：恰好 N 行 → 有值；N−1 行 → NaN（只钉"够"不钉"不够"，窗口写大 10 倍照样绿）
- **改参数 → 结果必须变**：short_window 5→3 时因子值变
- **方向**：净流入为正的股因子值 > 净流出的股（反号不报错，只会专挑被砸盘的票）
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quantpilot.core.config_defaults import MoneyFlowStrategyConfig
from quantpilot.engine.strategies.money_flow import MoneyFlowStrategy


def _trade_days(n: int, end: date = date(2026, 9, 15)) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _flow_frame(spec: dict[str, dict], n_days: int) -> pd.DataFrame:
    """spec: ts_code -> {"main_net": 日主力净额(元), "amount": 日成交额(元), "days": 行数}"""
    rows = []
    days = _trade_days(n_days)
    for code, s in spec.items():
        use_days = days[-s.get("days", n_days):]
        for d in use_days:
            net = float(s["main_net"])
            rows.append({
                "ts_code": code, "trade_date": d,
                "net_mf_amount": net,
                "buy_elg_amount": max(net, 0.0), "sell_elg_amount": max(-net, 0.0),
                "buy_lg_amount": 0.0, "sell_lg_amount": 0.0,
                "amount": float(s["amount"]),
            })
    return pd.DataFrame(rows)


def _snapshot(flow: pd.DataFrame | None) -> dict:
    snap: dict = {"trade_date": date(2026, 9, 15)}
    if flow is not None:
        snap["money_flow"] = flow
    return snap


def _factors(strategy: MoneyFlowStrategy, universe: list[str], flow) -> pd.DataFrame:
    return strategy.compute_raw_factors(pd.Index(universe), _snapshot(flow))


def test_mf_str_01_registry_contract() -> None:
    s = MoneyFlowStrategy()
    assert s.name == "money_flow"
    assert s.display_name == "资金动向"
    assert list(s.weights) == ["main_net_inflow_5d", "main_net_inflow_20d"]
    assert s.required_history_days >= 1


def test_mf_str_02_ratio_definition_exact() -> None:
    """5 日：每日净流入 1e6、成交额 1e7 → 5e6 / 5e7 = 0.10。20 日同理。"""
    flow = _flow_frame({"A": {"main_net": 1e6, "amount": 1e7}}, 20)
    f = _factors(MoneyFlowStrategy(), ["A"], flow)
    assert f.loc["A", "main_net_inflow_5d"] == pytest.approx(0.10)
    assert f.loc["A", "main_net_inflow_20d"] == pytest.approx(0.10)


def test_mf_str_03_direction_inflow_beats_outflow() -> None:
    flow = _flow_frame({
        "IN": {"main_net": 2e6, "amount": 1e7},
        "OUT": {"main_net": -2e6, "amount": 1e7},
    }, 20)
    f = _factors(MoneyFlowStrategy(), ["IN", "OUT"], flow)
    for col in ("main_net_inflow_5d", "main_net_inflow_20d"):
        assert f.loc["IN", col] > 0 > f.loc["OUT", col], col


def test_mf_str_04_window_boundary_both_sides() -> None:
    """20 行 → 20d 有值；19 行 → 20d NaN 而 5d 仍有值（不足窗口不得用部分和冒充）。"""
    ok = _flow_frame({"A": {"main_net": 1e6, "amount": 1e7}}, 20)
    short = _flow_frame({"A": {"main_net": 1e6, "amount": 1e7}}, 19)
    f_ok = _factors(MoneyFlowStrategy(), ["A"], ok)
    f_short = _factors(MoneyFlowStrategy(), ["A"], short)
    assert pd.notna(f_ok.loc["A", "main_net_inflow_20d"])
    assert pd.isna(f_short.loc["A", "main_net_inflow_20d"])
    assert pd.notna(f_short.loc["A", "main_net_inflow_5d"])


def test_mf_str_05_changing_window_changes_result() -> None:
    """§4.4「改参数 → 结果必须变」：前 2 日大额流入、后 3 日为 0——3 日窗与 5 日窗必不同。"""
    days = _trade_days(5)
    rows = []
    for i, d in enumerate(days):
        net = 5e6 if i < 2 else 0.0
        rows.append({"ts_code": "A", "trade_date": d, "net_mf_amount": net,
                     "buy_elg_amount": net, "sell_elg_amount": 0.0,
                     "buy_lg_amount": 0.0, "sell_lg_amount": 0.0, "amount": 1e7})
    flow = pd.DataFrame(rows)
    f5 = _factors(MoneyFlowStrategy(MoneyFlowStrategyConfig(short_window=5)), ["A"], flow)
    f3 = _factors(MoneyFlowStrategy(MoneyFlowStrategyConfig(short_window=3)), ["A"], flow)
    assert f5.loc["A", "main_net_inflow_5d"] == pytest.approx(1e7 / 5e7)
    assert f3.loc["A", "main_net_inflow_5d"] == pytest.approx(0.0)


def test_mf_str_06_uses_elg_plus_lg_not_total_net() -> None:
    """因子用「特大+大单」净额，不是 net_mf_amount（后者含小单/中单）。两者相反时以前者为准。"""
    days = _trade_days(5)
    rows = [{
        "ts_code": "A", "trade_date": d,
        "net_mf_amount": -9e6,                     # 总净额为负
        "buy_elg_amount": 3e6, "sell_elg_amount": 1e6,
        "buy_lg_amount": 2e6, "sell_lg_amount": 1e6,  # 主力净额 +3e6
        "amount": 1e7,
    } for d in days]
    f = _factors(MoneyFlowStrategy(), ["A"], pd.DataFrame(rows))
    assert f.loc["A", "main_net_inflow_5d"] == pytest.approx(3e6 * 5 / 5e7)


def test_mf_str_07_missing_snapshot_key_or_stock_gives_nan_not_crash() -> None:
    """快照无 money_flow（回测 / 冷启动）→ 全 NaN；universe 里没数据的股 → NaN，其余照算。"""
    s = MoneyFlowStrategy()
    f_none = _factors(s, ["A", "B"], None)
    assert f_none.isna().all().all()
    assert list(f_none.index) == ["A", "B"]

    flow = _flow_frame({"A": {"main_net": 1e6, "amount": 1e7}}, 20)
    f = _factors(s, ["A", "B"], flow)
    assert pd.notna(f.loc["A", "main_net_inflow_5d"])
    assert f.loc["B"].isna().all()


def test_mf_str_08_zero_amount_gives_nan_not_inf() -> None:
    """停牌日成交额 0 → 分母 0 → NaN（不是 inf；inf 进 Winsorize 会把整列毁掉）。"""
    flow = _flow_frame({"A": {"main_net": 1e6, "amount": 0.0}}, 20)
    f = _factors(MoneyFlowStrategy(), ["A"], flow)
    assert pd.isna(f.loc["A", "main_net_inflow_5d"])
    assert not np.isinf(f.to_numpy(dtype=float)).any()


def test_mf_str_09_reason_text_mentions_both_windows() -> None:
    s = MoneyFlowStrategy()
    row = pd.Series({"main_net_inflow_5d": 0.052, "main_net_inflow_20d": -0.013})
    txt = s._build_reason("A", row, 70.0)
    assert "5 日" in txt and "20 日" in txt
    assert "+5.2%" in txt and "-1.3%" in txt


# ─────────────────────────────────────────────────────────────────────────────
# 调用点：策略写对了 ≠ 生产会算——`money_flow` 必须真的被取出来并放进快照
# （§4.11 表第 4 例 / C2 `f_score` 同型；只能在调用点上验，替身测试是自证式的）。
# ─────────────────────────────────────────────────────────────────────────────
class TestServiceActuallyFeedsMoneyFlow:
    @staticmethod
    def _snapshot_src() -> str:
        import inspect

        from quantpilot.services.strategy_service import ScoringService

        return inspect.getsource(ScoringService._build_market_snapshot)

    def test_service_calls_get_money_flow_window(self) -> None:
        import ast

        tree = ast.parse(self._snapshot_src().lstrip())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "get_money_flow_window"]
        assert calls, "ScoringService 未取 money_flow → 策略永远全 NaN、影子期观察不到任何东西"

    def test_snapshot_carries_money_flow_key(self) -> None:
        assert '"money_flow"' in self._snapshot_src(), "money_flow 未写入快照"

    def test_repo_window_carries_amount_column(self) -> None:
        """因子分母是成交额，来自 daily_quote——repo 查询必须联出 `amount` 列。"""
        import inspect

        from quantpilot.data.repository import MarketDataRepository

        src = inspect.getsource(MarketDataRepository.get_money_flow_window)
        assert "DailyQuote.amount" in src, "get_money_flow_window 没联 daily_quote.amount"
