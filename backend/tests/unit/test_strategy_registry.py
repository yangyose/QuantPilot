"""策略名单一事实来源（V1.5-C C3 / 设计 §8.3 陷阱 3）。

## 为什么必须有

策略名清单此前散在**五处**（设计只数到四处，`api/v1/factor_quality.py` 那份漏了）：

| 位置 | 形态 |
|---|---|
| `engine/scorer.py::_STRATEGY_KEYS` | tuple |
| `engine/scorer.py::SCORE_COLUMN_MAP` | key → DB 列名（`mean_reversion` →
  `reversion_score`，**不规则**）|
| `services/factor_monitor_service.py::_STRATEGY_NAMES` | tuple |
| `services/factor_monitor_service.py::_FACTOR_MAP` | DB 列名 → (类名, 列名) |
| `api/v1/factor_quality.py::_STRATEGY_NAMES` | tuple |
| `core/config_defaults.py::StrategyWeightsConfig` | 三个 state dict |

「改了三处漏第四处」是本项目反复踩的坑——**同一天内已被同族形态咬三次**
（`get_latest_financial` 共用报告期、适配器 `fina_cols` 白名单、
`_FINANCIAL_UPDATE_COLS` upsert 白名单），全都不报错、测试全绿。

## 本文件的判据

不是「registry 里有几个策略」，而是**各处清单是否真的引用了它**——
留一份内容恰好相同的副本，任何只比较内容的测试都会绿。故断言**对象同一性**
或由 registry 派生。
"""
from __future__ import annotations

from quantpilot.core.strategy_registry import (
    SCORE_COLUMN_MAP,
    STRATEGY_NAMES,
    score_column,
)


class TestRegistryContent:
    def test_contains_the_four_v1_strategies(self) -> None:
        for s in ("trend", "momentum", "mean_reversion", "value"):
            assert s in STRATEGY_NAMES

    def test_includes_low_volatility(self) -> None:
        """C3 新增——registry 是加策略的第一落点。"""
        assert "low_volatility" in STRATEGY_NAMES

    def test_score_column_map_covers_every_strategy(self) -> None:
        """每个策略都要有 DB 列名，缺一个就会在写 candidate_pool 时静默丢分。"""
        assert set(SCORE_COLUMN_MAP) == set(STRATEGY_NAMES)

    def test_mean_reversion_keeps_its_irregular_column_name(self) -> None:
        """`mean_reversion` 的列名是 `reversion_score`（**不规则**，历史遗留）。

        统一成 `mean_reversion_score` 会静默写错列——这条锁死它。
        """
        assert score_column("mean_reversion") == "reversion_score"

    def test_names_are_unique_and_ordered(self) -> None:
        assert len(set(STRATEGY_NAMES)) == len(STRATEGY_NAMES)
        assert isinstance(STRATEGY_NAMES, tuple), "必须不可变——被多处引用"


class TestAllCallSitesDeriveFromRegistry:
    """⚠️ 核心：各处必须**引用** registry，不能留内容相同的副本。

    留副本的话「内容相等」的断言照样绿，而下一次加策略仍会漏。
    故比较对象同一性（`is`），或断言由 registry 派生。
    """

    def test_scorer_keys_is_the_registry_tuple(self) -> None:
        from quantpilot.engine import scorer

        assert scorer._STRATEGY_KEYS is STRATEGY_NAMES

    def test_scorer_score_column_map_is_the_registry_map(self) -> None:
        from quantpilot.engine import scorer

        assert scorer.SCORE_COLUMN_MAP is SCORE_COLUMN_MAP

    def test_factor_monitor_names_is_the_registry_tuple(self) -> None:
        from quantpilot.services import factor_monitor_service as fms

        assert fms._STRATEGY_NAMES is STRATEGY_NAMES

    def test_factor_quality_api_names_is_the_registry_tuple(self) -> None:
        """⚠️ 这一处是设计文档没数到的第五处——漏它就会让因子质量 API
        少报新策略，而接口照常返回 200。"""
        from quantpilot.api.v1 import factor_quality

        assert factor_quality._STRATEGY_NAMES is STRATEGY_NAMES

    def test_factor_monitor_factor_map_covers_all_strategies(self) -> None:
        """`_FACTOR_MAP` 的键是 DB 列名，必须覆盖 registry 的每个策略。"""
        from quantpilot.services import factor_monitor_service as fms

        for s in STRATEGY_NAMES:
            assert score_column(s) in fms._FACTOR_MAP, f"{s} 未登记进 _FACTOR_MAP"


class TestDefaultWeightMatrixCoversEveryStrategy:
    """设计 §8.3 陷阱 2：`default_matrix` 必须**显式**登记新策略 = 0.0。

    不登记的话 `_default_weights_for_state` 返回的 dict 缺键 →
    冷启动 / DOWNTREND（当前正走 `default_matrix`）路径下新策略直接缺席，
    **行为在不同路径间不一致**。
    """

    def test_all_three_states_list_every_strategy(self) -> None:
        from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS

        for state in ("uptrend", "downtrend", "oscillation"):
            w = getattr(DEFAULT_STRATEGY_WEIGHTS, state)
            assert set(w) == set(STRATEGY_NAMES), f"{state} 缺 {set(STRATEGY_NAMES) - set(w)}"

    def test_low_volatility_starts_at_zero_weight(self) -> None:
        """影子模式：C3 权重从 0 起步，经 ICIR 验证后由月末 rebalance 自动激活。"""
        from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS

        for state in ("uptrend", "downtrend", "oscillation"):
            assert getattr(DEFAULT_STRATEGY_WEIGHTS, state)["low_volatility"] == 0.0

    def test_existing_four_weights_unchanged(self) -> None:
        """⚠️ 零回归的数学保证：加了 0 权重的新策略，四策略权重**逐值不变**。"""
        from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS

        expected = {
            "uptrend": {"trend": 0.40, "momentum": 0.25, "mean_reversion": 0.15, "value": 0.20},
            "downtrend": {"trend": 0.10, "momentum": 0.05, "mean_reversion": 0.15, "value": 0.70},
            "oscillation": {"trend": 0.15, "momentum": 0.15, "mean_reversion": 0.40, "value": 0.30},
        }
        for state, exp in expected.items():
            got = getattr(DEFAULT_STRATEGY_WEIGHTS, state)
            for k, v in exp.items():
                assert got[k] == v, f"{state}.{k} 由 {v} 变成了 {got[k]}"


class TestScoreColumnMapIsACheckedContract:
    """⚠️ `SCORE_COLUMN_MAP` 此前在生产代码里**零消费者**——写库走的是显式命名参数，
    这个 map 一直是装饰品（又一例「定义了但没人用」，本项目最高发的缺陷族）。

    不删它，而是把它变成**被检查的契约**：map 里每个列名必须真实存在于
    `CandidatePool` 模型与 `PoolEntry` 上。这样「加了策略却忘了建列」会在
    单测阶段当场红，而不是等生产写库时炸、或更糟——静默写不进去。
    """

    def test_every_mapped_column_exists_on_both_tables(self) -> None:
        """⚠️ 策略分数列**横跨两张表**——设计 §8.3 只数到 `candidate_pool`，
        而 `signal_score_snapshot`（信号数据血缘）有同样四列。
        漏一张则该表的新策略分数永远 NULL，且不报错。"""
        from quantpilot.models.business import CandidatePool, SignalScoreSnapshot

        for model in (CandidatePool, SignalScoreSnapshot):
            cols = {c.name for c in model.__table__.columns}
            for strategy, col in SCORE_COLUMN_MAP.items():
                assert col in cols, (
                    f"{strategy} 的列 {col} 不在 {model.__tablename__} 表上"
                )

    def test_every_mapped_column_exists_on_pool_entry(self) -> None:
        """`PoolEntry` 是写库前的载体——缺字段的话分数根本传不到 repository。"""
        import dataclasses

        from quantpilot.engine.pool import PoolEntry

        fields = {f.name for f in dataclasses.fields(PoolEntry)}
        for strategy, col in SCORE_COLUMN_MAP.items():
            assert col in fields, f"{strategy} 的列 {col} 不在 PoolEntry 上"


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY_DISPLAY_NAMES 契约（2026-09-11 加）
#
# 该映射是**第二份**策略名→中文名的副本（第一份是各策略类的 `display_name`）。
# 之所以容忍这份副本：`core/` 不能反向 import `engine/strategies/`（会成环）。
# 代价就是它会漂，所以下面这条契约测试**是必需的，不是锦上添花**——
# §4.11 第 2 例记着「配置的平行副本」正是本仓高发缺陷。
# ─────────────────────────────────────────────────────────────────────────────


def test_display_names_match_strategy_classes() -> None:
    """每个已登记策略的中文名必须与其类的 `display_name` 逐字相等。"""
    from quantpilot.core.strategy_registry import STRATEGY_DISPLAY_NAMES
    from quantpilot.services.scoring_factory import build_default_strategies

    by_name = {s.name: s for s in build_default_strategies()}
    missing = [n for n in STRATEGY_NAMES if n not in STRATEGY_DISPLAY_NAMES]
    assert not missing, f"STRATEGY_DISPLAY_NAMES 缺策略：{missing}"

    mismatched = []
    for name in STRATEGY_NAMES:
        strat = by_name.get(name)
        if strat is None:
            continue
        if STRATEGY_DISPLAY_NAMES[name] != strat.display_name:
            mismatched.append(
                f"{name}: registry={STRATEGY_DISPLAY_NAMES[name]!r} "
                f"class={strat.display_name!r}"
            )
    assert not mismatched, "中文名与策略类 display_name 不一致：\n  " + "\n  ".join(mismatched)


def test_display_names_have_no_extra_entries() -> None:
    """反向：不得有 STRATEGY_NAMES 里没有的多余条目（否则是删策略时漏删）。"""
    from quantpilot.core.strategy_registry import STRATEGY_DISPLAY_NAMES

    extra = set(STRATEGY_DISPLAY_NAMES) - set(STRATEGY_NAMES)
    assert not extra, f"STRATEGY_DISPLAY_NAMES 有多余条目：{sorted(extra)}"


class TestEveryAssemblySiteCarriesEveryStrategy:
    """策略实例的**组装点**必须全部含齐 registry 里的每个策略。

    2026-09-16 C4 探针在 5434 跑第一次就照出：`scripts/backfill_candidate_pool.py`
    自写了一份 4 策略字面量——设计 §5.2 数了三处、`scoring_factory` 又补了一处，
    **它是第五处**，C3 的 low_volatility 在它写出的池行里恒为 NULL、无人察觉。
    「改了 N 处漏第 N+1 处」正是本模块存在的理由，故这里不数处数：扫 `src/` 与
    `scripts/` 下**所有**含 `strategies=[` 字面量的文件，逐个要求含齐全部策略类；
    走 `build_default_strategies()` 的文件天然通过。
    """

    @staticmethod
    def _strategy_class_names() -> set[str]:
        import importlib
        import inspect

        from quantpilot.engine.strategies.base import BaseStrategy

        names: set[str] = set()
        for name in STRATEGY_NAMES:
            mod = importlib.import_module(f"quantpilot.engine.strategies.{name}")
            found = [
                obj.__name__ for _, obj in inspect.getmembers(mod, inspect.isclass)
                if issubclass(obj, BaseStrategy) and obj is not BaseStrategy
                and getattr(obj, "name", None) == name
            ]
            assert found, f"engine/strategies/{name}.py 里找不到 name == {name!r} 的策略类"
            names.add(found[0])
        return names

    def test_every_literal_strategy_list_contains_every_strategy(self) -> None:
        import ast
        import pathlib

        backend = pathlib.Path(__file__).resolve().parents[2]
        wanted = self._strategy_class_names()
        problems: list[str] = []
        scanned = 0
        for py in list((backend / "src").rglob("*.py")) + list((backend / "scripts").rglob("*.py")):
            src = py.read_text(encoding="utf-8")
            if "strategies=[" not in src:
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg != "strategies" or not isinstance(kw.value, ast.List):
                        continue
                    scanned += 1
                    present = {
                        (e.func.id if isinstance(e.func, ast.Name) else getattr(e.func, "attr", ""))
                        for e in kw.value.elts if isinstance(e, ast.Call)
                    }
                    missing = wanted - present
                    if missing:
                        rel = py.relative_to(backend).as_posix()
                        problems.append(f"{rel}:{node.lineno} 缺 {sorted(missing)}")
        assert scanned >= 3, f"只扫到 {scanned} 处字面量组装点——扫描逻辑可能坏了"
        assert not problems, (
            "以下组装点漏了策略（该处算出的 composite 与生产不是同一个）：\n"
            + "\n".join(problems)
        )
