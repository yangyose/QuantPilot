"""C5 `PluginStrategy` 适配器（设计 §7.3）——插件接进五步管线后必须与内建策略同构。

设计 §7.3 的承诺有两半，两半都要钉：

1. **复用而非另起**：插件只实现 `compute_raw_factors(universe, data)` 单函数，由适配器
   包成标准 `BaseStrategy` → 天然获得五步管线 / `apply_constraints` / lineage / ICIR。
   判据不是「适配器有那些方法」，而是 **`Scorer.aggregate` 真能消费它产出的因子矩阵**
   （§7.6 DoD 第二条）。
2. **插件不得覆写 `score()` / `apply_constraints`**（否则可绕过硬约束）。插件源码里即便
   定义了同名函数也不该被接进去——适配器只取 `compute_raw_factors` 那一个名字。

另外两条来自本仓既有教训、不在设计文档里但必须守：
- **因子矩阵禁止夹带非因子列**（§4.4）：适配器返回的列全部会被 `Scorer` 当因子逐列处理。
- **`weights` 只是登记**（生产不读，见 `BaseStrategy.weights` 那段注释）——适配器按等权
  填它，但测试断言的是「`Scorer` 拿到的列」，不是权重值。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.sandbox.plugin_strategy import PluginStrategy
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.strategies.base import BaseStrategy

_UNIVERSE = pd.Index([f"{i:06d}.SZ" for i in range(30)], name="ts_code")
_SNAP = {
    "trade_date": date(2026, 8, 25),
    "industry": {c: "I1" for c in _UNIVERSE},
    "market_cap": pd.Series(1e9, index=_UNIVERSE),
    "beta": None,
}
_ALLOW = {"allow_without_memory_limit": True}

_PLUGIN_OK = '''
import pandas as pd

def compute_raw_factors(universe, data):
    n = len(universe)
    return pd.DataFrame(
        {"alpha": [float(i) for i in range(n)], "beta_f": [float(n - i) for i in range(n)]},
        index=universe,
    )
'''


def _strategy(source: str = _PLUGIN_OK, **kw) -> PluginStrategy:
    return PluginStrategy(
        name="plugin_demo", display_name="演示插件", source=source,
        timeout_s=30.0, **{**_ALLOW, **kw},
    )


class TestIsARealStrategy:
    def test_subclasses_base_strategy(self) -> None:
        s = _strategy()
        assert isinstance(s, BaseStrategy)
        assert s.name == "plugin_demo" and s.display_name == "演示插件"

    def test_compute_raw_factors_runs_in_sandbox(self) -> None:
        raw = _strategy().compute_raw_factors(_UNIVERSE, _SNAP)  # type: ignore[arg-type]
        assert list(raw.index) == list(_UNIVERSE)
        assert list(raw.columns) == ["alpha", "beta_f"]
        assert raw["alpha"].iloc[0] == 0.0

    def test_weights_are_equal_and_sum_to_one(self) -> None:
        s = _strategy()
        s.compute_raw_factors(_UNIVERSE, _SNAP)  # type: ignore[arg-type]
        assert set(s.weights) == {"alpha", "beta_f"}
        assert sum(s.weights.values()) == pytest.approx(1.0)

    def test_required_history_days_is_configurable(self) -> None:
        assert _strategy(required_history_days=200).required_history_days == 200


class TestScorerCanConsumeIt:
    """§7.6 DoD 第二条：适配器产出的因子矩阵能被 `Scorer` 正常消费。"""

    def test_aggregate_produces_composites(self) -> None:
        s = _strategy()
        factors = s.compute_strategy_factors(_UNIVERSE, _SNAP)  # type: ignore[arg-type]
        out = Scorer().aggregate(
            MarketStateEnum.OSCILLATION, {"plugin_demo": factors}, _SNAP,  # type: ignore[arg-type]
            {"plugin_demo": 1.0}, "user_override", ["plugin_demo"], "stable",
            single_strategy_mode=True,
        )
        assert len(out) == len(_UNIVERSE)
        assert all(0.0 <= c.composite_score <= 100.0 for c in out)
        # 因子确实进了血缘（不是被当成空策略跳过）
        assert out[0].score_breakdown_raw.get("plugin_demo") is not None

    def test_failed_plugin_yields_all_nan_and_scorer_skips_it(self) -> None:
        """插件跑挂 → 全 NaN（不是 0！）→ `Scorer` 记 `skipped_all_nan` 并跳过。

        置 0 会被 Z-score 读成「横截面均值」= 一张中性分，等于让坏插件参与选股（§4.4）。
        """
        s = _strategy(source="def compute_raw_factors(universe, data):\n    return None\n")
        raw = s.compute_raw_factors(_UNIVERSE, _SNAP)  # type: ignore[arg-type]
        assert raw.isna().all().all()
        assert s.last_run is not None and not s.last_run.ok
        out = Scorer().aggregate(
            MarketStateEnum.OSCILLATION, {"plugin_demo": raw}, _SNAP,  # type: ignore[arg-type]
            {"plugin_demo": 1.0}, "user_override", ["plugin_demo"], "stable",
            single_strategy_mode=True,
        )
        assert out == []


class TestPluginCannotOverrideConstraints:
    """设计 §7.3：插件不允许覆写 `score()` / `apply_constraints`。"""

    def test_plugin_defined_apply_constraints_is_ignored(self) -> None:
        src = '''
import pandas as pd

def apply_constraints(raw, universe, market_data):
    raise AssertionError("插件的 apply_constraints 不该被调用")

def score(universe, market_data):
    raise AssertionError("插件的 score 不该被调用")

def compute_raw_factors(universe, data):
    return pd.DataFrame({"alpha": [1.0] * len(universe)}, index=universe)
'''
        s = _strategy(source=src)
        # 五步管线入口照常工作，插件那两个同名函数被无视
        factors = s.compute_strategy_factors(_UNIVERSE, _SNAP)  # type: ignore[arg-type]
        assert list(factors.columns) == ["alpha"]
        assert s.apply_constraints.__qualname__.startswith("BaseStrategy")


class TestAuditTrail:
    """§7.4：加载 / 执行 / 输出都要可审计 —— 适配器把每次运行的结果留在 `last_run`。"""

    def test_last_run_records_status_and_duration(self) -> None:
        s = _strategy()
        s.compute_raw_factors(_UNIVERSE, _SNAP)  # type: ignore[arg-type]
        assert s.last_run is not None
        assert s.last_run.ok and s.last_run.exit_status == "ok"
        assert s.last_run.duration_ms >= 0
        assert s.last_run.capabilities.subprocess_isolation is True

    def test_rejected_import_is_visible_in_last_run(self) -> None:
        s = _strategy(source="import os\n\ndef compute_raw_factors(u, d):\n    return None\n")
        s.compute_raw_factors(_UNIVERSE, _SNAP)  # type: ignore[arg-type]
        assert s.last_run is not None
        assert s.last_run.exit_status == "rejected_import"
        assert "os" in (s.last_run.error or "")


class TestSnapshotIsNotHandedToThePlugin:
    """SDD §15.2「只能通过系统提供的标准数据接口获取数据」：不传 session / repo / adapter。

    判据：喂给插件的 `data` 只含白名单键，且**值都是 pandas 结构或普通标量**——
    若把整个 `MarketSnapshot` 原样丢进去，插件就能顺着里面的对象往外爬。
    """

    def test_plugin_sees_only_whitelisted_pandas_payload(self) -> None:
        src = '''
import pandas as pd

def compute_raw_factors(universe, data):
    keys = sorted(data.keys())
    bad = [k for k, v in data.items()
           if not isinstance(v, (pd.DataFrame, pd.Series, int, float, str, type(None)))]
    return pd.DataFrame({"nkeys": [float(len(keys))] * len(universe),
                         "nbad": [float(len(bad))] * len(universe)}, index=universe)
'''
        snap = dict(_SNAP)
        snap["daily_quotes"] = pd.DataFrame({"close": [1.0] * len(_UNIVERSE)}, index=_UNIVERSE)
        snap["_secret_repo"] = object()  # 绝不能被传进去
        raw = _strategy(source=src).compute_raw_factors(_UNIVERSE, snap)  # type: ignore[arg-type]
        assert raw["nbad"].iloc[0] == 0.0, "插件拿到了非 pandas/标量对象"
        assert raw["nkeys"].iloc[0] > 0.0
