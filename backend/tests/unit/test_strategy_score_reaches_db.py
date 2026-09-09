"""每个策略的分数**真的流到了终点**——不是「字段存在」，是「值到了」。

## 这条测试为什么存在

C3 接入 `low_volatility` 时，链上每一环看起来都对：
`STRATEGY_NAMES` 登记了、`SCORE_COLUMN_MAP` 映射了、`PoolEntry` 有字段、
alembic 0029 给 `candidate_pool` / `signal_score_snapshot` 两张表都加了列、
契约测试断言列名存在——**而 `Scorer.aggregate()` 从头到尾没把值填进去**。
`CompositeScore.low_volatility_score` 恒为 `None`，两张表的新列永远是 NULL。

1184 条测试全绿，因为没有一条问过「值到了吗」。
这是 CLAUDE.md §4.11「接了但没生效」表第 4 例的同型：
机制、迁移、契约全对，终点从未收到值。

## 判据

**遍历 `STRATEGY_NAMES` 而不是写死五个名字**——写死的话，
加第六个策略时这条测试照样绿，缺陷原样复发。
断言的是「改这个策略的因子 → 它的分数字段必须跟着变」，
不是「字段不是 None」（后者在恒等填 0 时也为真）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantpilot.core.strategy_registry import SCORE_COLUMN_MAP, STRATEGY_NAMES
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import CompositeScore, Scorer

_CODES = [f"00000{i}.SZ" for i in range(1, 13)]

# CompositeScore 上承载各策略分数的属性名。`mean_reversion` → `reversion_score`
# 是历史遗留的不规则映射，与 DB 列名同源，故直接复用 SCORE_COLUMN_MAP。
_ATTR = SCORE_COLUMN_MAP


def _factors(seed_by_strategy: dict[str, float]) -> dict[str, pd.DataFrame]:
    out = {}
    for s, seed in seed_by_strategy.items():
        df = pd.DataFrame.from_dict(
            {c: [seed + i * 0.37] for i, c in enumerate(_CODES)},
            orient="index", columns=[f"f_{s}"],
        )
        df.index.name = "ts_code"
        out[s] = df
    return out


def _snapshot() -> dict:
    return {
        "industry": {c: ("TECH" if i % 2 == 0 else "FIN") for i, c in enumerate(_CODES)},
        "market_cap": pd.Series(
            np.linspace(1e9, 2e9, num=len(_CODES)), index=pd.Index(_CODES, name="ts_code")
        ),
        "beta": None,
    }


def _agg(seeds: dict[str, float]) -> dict[str, CompositeScore]:
    # 全部给非零权重：本文件测的是「分数有没有被填进去」，与影子权重无关。
    w = {s: 1.0 / len(STRATEGY_NAMES) for s in STRATEGY_NAMES}
    res = Scorer().aggregate(
        market_state=MarketStateEnum.OSCILLATION,
        strategy_factors=_factors(seeds),
        snapshot=_snapshot(),
        weights_runtime=w,
        weights_source="default_matrix",
        orthogonalize_order=list(STRATEGY_NAMES),
        hysteresis_status="stable",
    )
    return {r.ts_code: r for r in res}


def test_every_registered_strategy_has_its_score_attribute() -> None:
    """先钉映射本身：漏登记一个策略，后面两条就无从检查。"""
    for s in STRATEGY_NAMES:
        assert s in _ATTR, f"{s} 未登记进 SCORE_COLUMN_MAP"
        assert hasattr(CompositeScore, "__dataclass_fields__")
        assert _ATTR[s] in CompositeScore.__dataclass_fields__, (
            f"CompositeScore 缺字段 {_ATTR[s]}（策略 {s}）"
        )


def test_every_registered_strategy_score_is_populated_by_aggregate() -> None:
    """`aggregate()` 必须把每个策略的分数都填进 CompositeScore。

    缺任何一个 → 该策略在 candidate_pool / signal_score_snapshot 里永远是 NULL。
    """
    base = {s: 0.0 for s in STRATEGY_NAMES}
    out = _agg(base)
    assert out, "aggregate 未产出任何结果"

    missing = []
    for s in STRATEGY_NAMES:
        attr = _ATTR[s]
        if all(getattr(c, attr, None) is None for c in out.values()):
            missing.append(f"{s} → CompositeScore.{attr} 全为 None")
    assert not missing, "以下策略的分数从未被填入：\n  " + "\n  ".join(missing)


def test_shadow_weight_zero_still_populates_the_score() -> None:
    """**影子权重 0 的策略，分数照样要落库。**

    这是生产实际配置（`low_volatility` 权重 0.0），而上面两条用的是等权非零——
    若只测非零权重，「非零时有值、影子时恒 None」这种失效不会被发现，
    而影子期恰恰是唯一要靠这一列观察的时期。

    风险不是假想的：C0-6 记过一次——零权重策略被 `valid_weights` 过滤，
    连 `score_breakdown_raw` 都进不去，导致 trend/momentum 完全不产日级 IC。
    """
    seeds = {s: 0.0 for s in STRATEGY_NAMES}
    w = {s: 1.0 / (len(STRATEGY_NAMES) - 1) for s in STRATEGY_NAMES}
    w[STRATEGY_NAMES[-1]] = 0.0          # 最后一个 = 影子策略
    res = Scorer().aggregate(
        market_state=MarketStateEnum.OSCILLATION,
        strategy_factors=_factors(seeds),
        snapshot=_snapshot(),
        weights_runtime=w,
        weights_source="default_matrix",
        orthogonalize_order=list(STRATEGY_NAMES),
        hysteresis_status="stable",
    )
    attr = _ATTR[STRATEGY_NAMES[-1]]
    populated = [getattr(c, attr, None) for c in res]
    assert res, "aggregate 未产出结果"
    assert any(v is not None for v in populated), (
        f"影子权重 0 时 {attr} 全为 None —— 影子期将观察不到任何东西"
    )


def test_changing_a_strategy_factor_changes_that_strategy_score() -> None:
    """「改因子 → 结果必须变」——恒等填 0 或填错策略的值都会在这里露馅。

    只断言「不是 None」不够：那在把别人的分数抄过来时也为真。
    """
    base = {s: 0.0 for s in STRATEGY_NAMES}
    before = _agg(base)

    for s in STRATEGY_NAMES:
        attr = _ATTR[s]
        # 只翻转这一个策略的因子方向，其余不动
        bumped = _agg({**base, s: 0.0} | {s: -100.0})
        changed = any(
            (getattr(before[c], attr, None) is not None)
            and (getattr(bumped[c], attr, None) != getattr(before[c], attr, None))
            for c in before
            if c in bumped
        )
        assert changed, (
            f"改 {s} 的因子后 CompositeScore.{attr} 没有任何变化 —— "
            f"该字段要么没接线，要么接到了别的策略上"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 落库那一段：`PoolEntry` → `upsert_candidate_pool_bulk` 的行 dict。
#
# 上面三条只证到 `CompositeScore`。值到了 CompositeScore 但行 dict 漏了这个键，
# 列照样恒为 NULL——**C3 这次就是四处 dict 全漏**。
#
# 按 CLAUDE.md §4.11「调用点是否真传参」：只能在**调用点**上验证。
# 构造一个 PoolEntry 再调 write_candidate_pool 的测试是自证式的
# （缺陷仍在时，那条路径根本不会被走到），故用 AST 直接查源码里的字面 dict。
# ─────────────────────────────────────────────────────────────────────────────


def _candidate_pool_row_dicts() -> list[dict]:
    """源码里所有「喂给 upsert_candidate_pool_bulk 的行 dict」的键集合。

    判据：一个 dict 只要含 `ts_code` + `trade_date` + `in_pool` 三个键，
    就是候选池行——按变量名或行号定位都会随重构失效。
    """
    import ast
    import pathlib

    src = pathlib.Path("src/quantpilot/services/strategy_service.py").read_text(
        encoding="utf-8"
    )
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if {"ts_code", "trade_date", "in_pool"} <= keys:
            out.append({"keys": keys, "line": node.lineno})
    return out


def test_candidate_pool_rows_carry_every_strategy_score_column() -> None:
    """每个策略的分数列都必须出现在**每一处**候选池行 dict 里。

    包括 fade-out 那两处写 None 的——漏了它们，
    「淡出行」与「在池行」的列集合不一致，upsert 的 `set_` 子句会随之漂移。
    """
    rows = _candidate_pool_row_dicts()
    assert len(rows) >= 4, f"只找到 {len(rows)} 处候选池行 dict，预期 >= 4（是否重构过？）"

    missing = []
    for row in rows:
        for strategy in STRATEGY_NAMES:
            col = SCORE_COLUMN_MAP[strategy]
            if col not in row["keys"]:
                missing.append(f"  strategy_service.py:{row['line']} 缺 {col}（策略 {strategy}）")
    assert not missing, (
        "候选池行 dict 漏了策略分数列 —— 该列在 DB 里会恒为 NULL 且不报错：\n"
        + "\n".join(missing)
    )


# ─────────────────────────────────────────────────────────────────────────────
# 泛化护栏：**每一个 DB 列都得有人写**。
#
# `low_volatility_score` 那次是「迁移加了列、ORM 有字段、契约测试断言列名存在，
# 但没有任何代码把值放进去」。这不是某一列的问题——**下一次 alembic 加列时
# 同样会发生**，而且同样不报错、同样测试全绿。
#
# 本测试把那次的教训泛化：拿 ORM 的列清单去比对生产写入路径的源码。
# ⚠️ 判据是「列名在写入路径里出现过」，这挡不住「写了但写错值」——
# 那由上面三条「改因子 → 结果必须变」负责。两层各管一段，不重叠。
# ─────────────────────────────────────────────────────────────────────────────

# 由 DB / ORM 自行填充，不该出现在写入 dict 里。加进来必须写明理由。
_DB_MANAGED = {
    "id",          # 自增主键
    "created_at",  # server_default NOW()
    "updated_at",  # upsert 时由 func.now() 显式写，不走行 dict
}

_WRITE_PATH_FILES = (
    "src/quantpilot/services/strategy_service.py",
    "src/quantpilot/services/signal_service.py",
)


def test_every_orm_column_is_written_somewhere() -> None:
    """`candidate_pool` / `signal_score_snapshot` 的每一列都要有写入方。

    红了说明：要么刚加的列忘了接线（那一列会恒为 NULL 且不报错），
    要么该列确实由 DB 管理 —— 后者请加进 `_DB_MANAGED` 并写明理由，
    不要直接把断言删掉。
    """
    import pathlib

    from quantpilot.models.business import CandidatePool, SignalScoreSnapshot

    src = "\n".join(
        pathlib.Path(f).read_text(encoding="utf-8") for f in _WRITE_PATH_FILES
    )

    orphans = []
    for model in (CandidatePool, SignalScoreSnapshot):
        for col in model.__table__.columns:
            name = col.name
            if name in _DB_MANAGED:
                continue
            if f'"{name}"' not in src:
                orphans.append(f"  {model.__tablename__}.{name}")
    assert not orphans, (
        "以下 DB 列在生产写入路径中从未出现 —— 它们会恒为 NULL 且不报错：\n"
        + "\n".join(orphans)
        + "\n（若确属 DB 自管列，加进 _DB_MANAGED 并注明理由）"
    )
