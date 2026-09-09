"""value 策略的 `roe_quality` 开关（2026-09-09）——**已就位，但默认仍开启**。

## 为什么剔除——理由不是「它 IC 低」

「IC 低就剔掉」是拟合开发集，本仓当天刚在 F-Score 门控上吃过这个教训
（`docs/reviews/scoring_monotonicity_2026-09-09.md` §7）。真正的理由有两条：

1. **它的入选依据已被证伪**。前视偏差修复前它的 IC 是 **+0.0183**，
   修复后变成 **−0.0028**（直接变号）。它由 ROE 构成，正是被污染的字段——
   「这个因子有效」此前完全建立在「把后来才公布的财报写进当时的行」之上。
2. **六年里它从未确立方向**（干净面板 z 阶段 h=20，逐年）：
   −0.041 / −0.021 / −0.001 / **+0.020** / −0.002 / +0.005，全期 −0.0015。
   而同策略的 `pb_percentile`（+0.041）与 `pe_percentile`（+0.028）**6/6 年全正**。
   注意它**不是一直为负**——2024 年是正的；剔它不是因为它有害，
   而是因为它在等权平均里稀释了两个方向稳定的因子。

⚠️ **数据本身是有效的**：覆盖率 98.8%、构造就是横截面 ROE 排名。
不要把本次改动记成「roe 数据坏了」——ROE 仍被 `apply_constraints` 的
价值陷阱护栏（SDD §7.2.4）使用，那条**不受影响**。

## 结论（2026-09-09 专项实测后）：**维持现状，证据不支持剔除**

A/B/C 三口径实测（35 日，开发集，h=20）：

| 口径 | 平均 IC | 低 ROE 组平均分位（越低 = 陷阱压得越靠后）|
|---|---|---|
| A 现行 pe+pb+roe_quality | +0.0319 | **0.351** |
| B 剔除 pe+pb | +0.0398 | 0.407 |
| C 换 F-Score | +0.0380 | 0.406 |

① alpha 上三者**无法区分**（配对差值 |t| ≤ 1.47）；
② 陷阱区分能力上 **A 明显最好**，且 **F-Score 顶替不了 ROE**（C ≈ B）。

⚠️ **最值得记的**：`roe_quality` 同时承担**两个职责**——弱 alpha 因子 + 价值陷阱的
**区分维度**。先前只测 alpha 就得出「该剔」（ΔIC +0.0062、t=1.48），
是把第二个职责悄悄扔了。**一个因子可能身兼数职，只测其中一个就下剔除结论必然出错。**

## ⚠️ 下面两条是「即便证据支持剔除也仍然拦着」的原因，一并保留

两个独立的阻断原因，任一成立都不能直接关：

1. **SDD §7.2.4 冲突**：该表把「ROE 质量」列为 35% 权重因子，并把价值陷阱规避
   写成「需结合 ROE 质量因子过滤」。按 C-5，范围变更必须**先回写
   `system_design §9` + SDD**，再动代码——不能反过来改测试去迁就实现。
2. **关掉会真的缺一块能力**：ROE 届时只剩 `apply_constraints` 的「截断到横截面
   中位数」护栏。它对「便宜但低质」仍然有效（把标的从高位压到中位），
   但对**两只估值完全相同、只是质量不同**的股票是 **no-op**——中位数就等于
   它俩的值。而 SDD 的判定标准正是「陷阱股不得排在健康同业之前」
   （`test_strategies_impl.py::test_val_02_value_trap_ranked_below_healthy_peer` 钉着）。
   实测：关掉后该用例 TRAP 与 OK **同分 75.0**，护栏区分不了它们。
   所以「关掉 roe_quality」必须与「给出替代的价值陷阱机制」同批做。

开关本身先落地，是为了这两件事解决后能一键切换、以及随时重测。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantpilot.core.config_defaults import ValueStrategyConfig
from quantpilot.engine.strategies.value import ValueStrategy

_CODES = pd.Index([f"00000{i}.SZ" for i in range(1, 7)], name="ts_code")


def _snapshot() -> dict:
    n = len(_CODES)
    return {
        "daily_quotes": pd.DataFrame(
            {"pe_ttm": np.linspace(8, 40, n), "pb": np.linspace(0.8, 5.0, n)},
            index=_CODES,
        ),
        "financials": pd.DataFrame(
            {
                "roe": np.linspace(0.02, 0.25, n),
                "sw_industry_l1": ["TECH"] * n,
            },
            index=_CODES,
        ),
        "pe_pb_history": pd.DataFrame(),
        # ⚠️ 键名就是 "pe_percentile" / "pb_percentile"（`_resolve_percentile` 的 key
        # 参数），不是 *_precomputed。首版写错了名字 → 两列全 NaN → 断言假失败，
        # 差点被当成实现的问题。
        "pe_percentile": pd.Series(np.linspace(0.1, 0.9, n), index=_CODES),
        "pb_percentile": pd.Series(np.linspace(0.15, 0.85, n), index=_CODES),
    }


def _cols(cfg: ValueStrategyConfig | None) -> list[str]:
    df = ValueStrategy(cfg).compute_raw_factors(_CODES, _snapshot())
    return list(df.columns)


def test_roe_quality_present_by_default() -> None:
    """默认仍按 SDD §7.2.4 三因子 —— 开关不改变现状。"""
    cols = _cols(None)
    assert "roe_quality" in cols
    assert len(cols) == 3


def test_flag_removes_it() -> None:
    """关掉开关 → 它消失。与上一条构成「改开关 → 结果必须变」。"""
    cols = _cols(ValueStrategyConfig(include_roe_quality=False))
    assert "roe_quality" not in cols
    assert "pe_percentile" in cols and "pb_percentile" in cols


def test_value_trap_guard_still_works_without_the_factor() -> None:
    """⚠️ 价值陷阱护栏必须**不受影响**——它读 `financials["roe"]`，不读因子列。

    剔因子时顺手把护栏弄坏，是这次改动最贵的失败方式：
    SDD §7.2.4 那条护栏 C1-1 之前刚失效过很久。
    """
    snap = _snapshot()
    strat = ValueStrategy(ValueStrategyConfig(include_roe_quality=False))
    raw = strat.compute_raw_factors(_CODES, snap)
    out = strat.apply_constraints(raw, _CODES, snap)

    # 低 ROE（低于行业中位）的那半边必须被截断到各列中位数以下
    roe = snap["financials"]["roe"]
    low = roe < roe.median()
    assert low.any(), "构造有误：没有低于中位数的样本"
    for col in out.columns:
        med = raw[col].quantile(0.5)
        assert (out.loc[low.to_numpy(), col] <= med + 1e-12).all(), (
            f"{col}: 低 ROE 行未被截断 —— 价值陷阱护栏坏了"
        )


def test_reason_text_has_no_nan_when_factor_excluded() -> None:
    """理由文本不能出现 `ROE=nan%`——那是把内部缺失直接漏给用户。"""
    scores = ValueStrategy(
        ValueStrategyConfig(include_roe_quality=False)
    ).score(_CODES, _snapshot())
    assert scores, "score() 未产出结果"
    for s in scores:
        assert "nan" not in s.reason.lower(), f"理由文本含 nan：{s.reason}"
