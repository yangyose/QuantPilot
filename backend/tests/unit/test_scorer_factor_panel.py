"""K-6 调用点前置：`Scorer.aggregate` 导出因子级 raw / z 两版。

## 缺口（2026-09-04 核实，设计文档 §2.2 此处失实）

§2.2 写「四个中间量（raw/winsorized/neutralized/z）都是现成的」。核实 `scorer.py`
的逐列循环：四个值确实都作为**局部变量**算了出来，但只有 `winsorized` 与
`neutralized` 被累积进 `*_per_code` 带出函数（Phase 12 血缘需要），
**K-6 要取的 `raw` 与 `z` 两端恰恰被丢掉**。

所以 K-6 的调用点不是「纯脚本改动」——必须先让 `aggregate` 把这两版导出来。

## ⚠️ 默认必须关闭

`aggregate` 跑在生产 17:30 管线里。2026-09-03 刚因内存打挂过一次
（`docs/ops/deploy_log.md`），再无条件多累积两份 per-code 嵌套 dict 是拿生产冒险。
故用开关控制，**默认 False**；只有面板重跑打开它。

本文件第一条用例就钉「默认关闭且不产生任何额外累积」——这条红了意味着
生产内存被悄悄加了负担。
"""
from __future__ import annotations

import pandas as pd
import pytest

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import Scorer


def _snapshot(codes: list[str]) -> dict:
    from datetime import date

    return {
        "trade_date": date(2026, 5, 12),
        "industry": {c: "银行" for c in codes},
        "market_cap": pd.Series([1e10] * len(codes), index=codes),
        "beta": None,
    }


def _factors(n: int = 40) -> tuple[list[str], dict[str, pd.DataFrame]]:
    codes = [f"{i:06d}.SZ" for i in range(n)]
    df = pd.DataFrame(
        {
            "f_a": [float(i) for i in range(n)],
            "f_b": [float(n - i) for i in range(n)],
        },
        index=codes,
    )
    return codes, {"momentum": df}


def _run(collect: bool | None = None):
    codes, sf = _factors()
    kwargs = {} if collect is None else {"collect_factor_panel": collect}
    return Scorer().aggregate(
        market_state=MarketStateEnum.OSCILLATION,
        strategy_factors=sf,
        snapshot=_snapshot(codes),
        weights_runtime={"momentum": 1.0},
        weights_source="icir",
        orthogonalize_order=["momentum"],
        hysteresis_status="stable",
        **kwargs,
    )


class TestDefaultOff:
    def test_default_does_not_collect(self) -> None:
        """默认关闭——本条红了意味着生产内存被悄悄加了负担。"""
        out = _run()
        assert out, "构造应产出 composite"
        assert all(c.factor_raw is None for c in out)
        assert all(c.factor_z is None for c in out)

    def test_explicit_false_does_not_collect(self) -> None:
        out = _run(collect=False)
        assert all(c.factor_raw is None and c.factor_z is None for c in out)


class TestCollectedValues:
    def test_raw_is_the_pre_pipeline_input(self) -> None:
        """`raw` 必须是**未经五步管线加工**的原始因子值，逐字等于入参 DataFrame。

        若误取 winsorized（管线第一步之后），极值会被截断——而「原始预测力」
        正是要拿它跟 z 比的基线，取错了整个 raw/z 对比就失去意义。
        """
        codes, sf = _factors()
        out = _run(collect=True)
        by_code = {c.ts_code: c for c in out}
        for code in codes:
            if code not in by_code:
                continue
            got = by_code[code].factor_raw["momentum"]
            assert got["f_a"] == pytest.approx(float(sf["momentum"].loc[code, "f_a"]))
            assert got["f_b"] == pytest.approx(float(sf["momentum"].loc[code, "f_b"]))

    def test_z_is_standardized_not_neutralized(self) -> None:
        """`z` 必须是 zscore **之后**的那一版（= 进 composite 的那个）。

        判据用分布而非单点：z 的横截面均值 ≈ 0、标准差 ≈ 1；
        而 neutralized 保留原始量级（本构造下 0..39），两者数值天差地别。
        """
        out = _run(collect=True)
        vals = pd.Series(
            [c.factor_z["momentum"]["f_a"] for c in out if c.factor_z]
        )
        assert len(vals) >= 20
        assert abs(float(vals.mean())) < 0.2, f"z 均值应近 0，实得 {vals.mean()}"
        assert 0.7 < float(vals.std()) < 1.4, f"z 标准差应近 1，实得 {vals.std()}"
        # 原始量级 0..39，若误取 neutralized/raw 这条必红
        assert float(vals.abs().max()) < 5.0

    def test_both_strategies_and_factors_are_keyed(self) -> None:
        """结构必须是 {strategy: {factor: value}}——面板侧据此重建矩阵。"""
        out = _run(collect=True)
        c = next(x for x in out if x.factor_raw)
        assert set(c.factor_raw) == {"momentum"}
        assert set(c.factor_raw["momentum"]) == {"f_a", "f_b"}
        assert set(c.factor_z["momentum"]) == {"f_a", "f_b"}

    def test_collect_flag_is_consumed(self) -> None:
        """§4.11「参数是否真被消费」：开/关必须给出不同结果。"""
        on = _run(collect=True)
        off = _run(collect=False)
        assert any(c.factor_raw is not None for c in on)
        assert all(c.factor_raw is None for c in off)


class TestBackwardCompatible:
    def test_existing_lineage_fields_unaffected(self) -> None:
        """Phase 12 血缘的 winsorized / neutralized 不受影响（无论开关状态）。"""
        for collect in (True, False):
            out = _run(collect=collect)
            c = next(x for x in out if x.factor_winsorized)
            assert "momentum" in c.factor_winsorized
            assert "momentum" in c.factor_neutralized

    def test_composite_values_identical_regardless_of_flag(self) -> None:
        """开关只影响「是否额外导出」，**绝不能改变评分结果本身**。

        这条是安全边界：面板重跑与生产必须算出同一个 composite，
        否则面板结论就不适用于生产。
        """
        on = {c.ts_code: c.composite_z for c in _run(collect=True)}
        off = {c.ts_code: c.composite_z for c in _run(collect=False)}
        assert on.keys() == off.keys()
        for code in on:
            assert on[code] == pytest.approx(off[code])
