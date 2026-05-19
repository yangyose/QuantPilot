# Phase 12：信号可解释性 / 因子级溯源（V1.0 收尾批次）

> **版本：** v1.0
> **日期：** 2026-05-19
> **依据文档：** QuantPilot_SDD.md v1.4 §12.3（因子归因）/ §15.6（数据血缘）/ §16（V1.5+ 路线图：完整因子级溯源 + 多因子回归归因已升级 V1.0 Phase 12）；system_design.md §9 Phase 12 行；docs/design/phases/phase11_scoring_industrialization.md v1.4（P1-7 lineage 后端字段扩展已落地 / 前端分层视图归 Phase 12）；docs/design/v1_5_roadmap.md §1.x 升级清单（V1.5-B S1-GAP-01 + D1-GAP-02 + V1.5-E 多因子回归 → Phase 12）

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-05-19 | Phase 12 设计文档初版。基于 system_design §9 Phase 12 行 + SDD §12.3/§15.6/§16 + v1_5_roadmap V1.5-B/V1.5-E 升级清单展开模块/数据流/API/DoD/测试用例 |

---

## 1. 范围声明

### 1.1 本 Phase 纳入模块（system_design §9 Phase 12 行）

**P12-A LineageService 后端稳定化**

| 模块 | 路径 | 说明 |
|------|------|------|
| LineageService 字段稳定化 | `services/lineage_service.py` | 去除 Phase 11 P1-7 临时 `getattr(snapshot, X, None)` fallback，统一直读 ORM 字段（Phase 11 alembic 0009 已加全部 5 字段且 ORM 已正确映射） |
| LineageResponse pydantic schema | `schemas/signals.py` | 新增 `SignalLineageResponse` / `ScoreSnapshotLineage` / `PipelineRunLineage` 类型，取代 dict 返回；端点响应改用 `SignalLineageResponse` |
| `/signals/{id}/lineage` 端点响应类型 | `api/v1/signals.py` | `response_model=SignalLineageResponse`；保持 URL + HTTP 行为不变（前端已消费旧 dict 格式，新 schema 字段是 dict 超集） |

**P12-B AttributionService 多因子回归归因**

| 模块 | 路径 | 说明 |
|------|------|------|
| OLS 归因纯函数 | `engine/attribution/regression.py`（新增）| 纯函数：输入 `factor_exposures: pd.DataFrame[date, ts_code → factor_z]` + `forward_returns: pd.Series[(date, ts_code)]` → 输出 `AttributionResult`（含 4 因子收益 / 残差 / R² / t-stat / IC）。Engine 层严格无 IO，statsmodels OLS 调用 |
| AttributionService | `services/attribution_service.py`（新增）| 编排：装载 SignalScoreSnapshot.factor_neutralized + forward_returns + 调用 OLS engine + 写 `attribution_history` 表 |
| AttributionRepository | `data/attribution_repository.py`（新增）| `upsert_attribution(records)` / `get_attribution_by_date_range(start, end)` |
| AttributionHistory ORM | `models/business.py` | 新表 `attribution_history`（日级或周级，按 calc_date / factor 复合主键）|
| Alembic 0010 | `alembic/versions/0010_phase12_attribution_history.py` | 创建 `attribution_history` 表 + 索引 |
| MonthlyScheduler attribution Job | `pipeline/monthly_scheduler.py` | 月末调用 `AttributionService.run_monthly(month_end)` 计算近 N 月归因 |

**P12-C 前端三层折叠视图**

| 模块 | 路径 | 说明 |
|------|------|------|
| SignalLineageView | `frontend/src/views/SignalLineageView.vue`（新增）| 三层折叠：L1 业务可解释（trigger_reason + market_state + composite_score）/ L2 ICIR + 中性化前后值（4 strategy_z + weights_source + hysteresis_status）/ L3 正交化残差 + 审计（Gram-Schmidt 残差列 + 5 步管线各阶段 JSONB）|
| SignalCard L1 入口 | `frontend/src/components/SignalCard.vue` | 现状只展示 type + score + suggested_pct；Phase 12 加业务可解释一行文本（trigger_reason 翻译）+ "查看溯源详情"按钮 → 跳转 SignalLineageView |
| AttributionPanel | `frontend/src/components/AttributionPanel.vue`（新增）| 多因子回归归因展示：4 因子收益 bar + 残差 + R² + IC 时序 |
| 类型 + API client | `frontend/src/types/api.ts` + `frontend/src/api/signals.ts` + `frontend/src/api/attribution.ts`（新增）| `SignalLineage` / `Attribution*` 类型；`getSignalLineage(id)` / `getAttribution(start, end)` |
| 路由 | `frontend/src/router/index.ts` | `/signals/:id/lineage` |

**P12-D API 端点**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/signals/{id}/lineage` | GET | 现有，Phase 12 加 `response_model=SignalLineageResponse`（保持向后兼容）|
| `/attribution/history` | GET | 新增，参数 `start_date` / `end_date`，返回因子归因时序 |
| `/attribution/summary` | GET | 新增，参数 `start_date` / `end_date`，返回区间累计因子收益 + R² 均值 |

### 1.2 推迟项 / 不在本 Phase 范围

| 项 | 推迟到 | 理由 |
|---|---|---|
| **行业归因（行业配置偏离基准的超额收益分解）** | V1.5-E §1 | Phase 12 多因子回归归因覆盖"因子层"（个股层），行业归因属"业绩层"，需 Phase 14 5y candidate_pool 历史回填 + 持仓时序后才能跑组合层 OLS |
| **配置历史 UI 集成（L3 权重 UI）** | V1.5-F | weights_source + hysteresis_status 信息已在 SignalLineageView L2 展示；独立"权重历史"管理界面属于运营工具范畴，留 V1.5-F |
| **AttributionService 日级回填脚本** | Phase 14 §14-2 | 与 5y candidate_pool 回填同批，需 forward_returns 历史链 |
| **多账户归因切换 UI** | V1.5-G §G-4 | V1.0 单管理员，归因默认 account_id=1；多账户 UI 切换归 V1.5-G |

### 1.3 前序 Phase 推迟项继承清单

| 继承自 | 项 | 在 Phase 12 的处理 |
|---|---|---|
| Phase 11 P1-7 | LineageService 后端 5 字段扩展（getattr 临时实现）| P12-A：清理 getattr fallback + 加 schema 类型 |
| Phase 11 §12 DoD | SignalCard / SignalLineageView 前端分层视图渲染 | P12-C：完整实施 |
| V1.5-B S1-GAP-01 | 因子级溯源缺失（P0 阻断） | P12-A + P12-C 一并解决 |
| V1.5-B D1-GAP-02 | SignalCard 不展示评分决策路径 | P12-C SignalCard L1 + Lineage 跳转 |
| V1.5-E 多因子回归 | 多因子回归归因（SDD §12.3）| P12-B 完整实施 |
| SDD §16 因子归因 | 多因子回归收益拆解 | P12-B 实施；SDD §16 路线图行标记"已合入 V1.0 Phase 12" |

### 1.4 Phase 11 实施评审 P2 穿插项（2026-05-19 评审）

> 评审依据 `docs/reviews/phase11_implementation_review_2026-05-19.md` §6.2 "Phase 12 实施期"动作清单。
> P1 残留已于 2026-05-19 commit 收口；P2 残留在 Phase 12 实施过程中**与对应子任务同 commit** 收口。

| 编号 | 简述 | 关联子任务 | 修订要点 |
|---|---|---|---|
| **R12-P2-1** | `scorer.aggregate` 全 NaN 策略跳过路径加 `logger.info` | P12-A 后端字段稳定化期间扫到 | 在 `engine/scorer.py:154-158, 171-174` 两处 `continue` 前加 `logger.info("scorer_strategy_skipped_all_nan: strategy=%s ...", s_name, trade_date)`——便于 Phase 13 可观测性接入 |
| **R12-P2-2** | 删除 `SignalResponse.weights_source` 字段（ORM `Signal` 无对应列，永远 null）| P12-A schema 改造同 commit | **前端 grep 已确认无消费**（2026-05-19），可安全删除 `schemas/signals.py:29` 该字段；weights_source 仅在 `SignalLineageResponse.score_snapshot` 中暴露（数据来源 `candidate_pool`）|
| **R12-P2-3** | `factor_pipeline.neutralize_industry=False` 分支决策 | P12-B 实施穿插（与 OLS 归因测试同批补单测）| 推荐保留 + 补单测 `tests/unit/test_factor_pipeline.py::test_neutralize_industry_disabled` + 注释"V1.0 锁定 neutralize_industry=True，本分支仅用于研究模式 / Phase 14 backtest 单策略回测"|
| **R12-P2-6** | `_DEFAULT_ORDER` 改为按 default_matrix 当前 state 权重降序 | P12-A 实施期（同 strategy_service.py 邻近改动一起改）| 删除模块级 `_DEFAULT_ORDER`；fallback 路径用 `sorted(weights_runtime, key=lambda s: weights_runtime[s], reverse=True)`——DOWNTREND value 权重 0.70 应最先正交化 |

**收口追踪：** 每完成一项，更新评审报告 `phase11_implementation_review_2026-05-19.md` §9 修订追踪表，并在 memory `v1_finalize_deferred_items.md` "Phase 11 实施评审残留" 节同步移除对应行。Phase 14 实施评审残留（R14-P2-4 / R14-P2-5）见 phase14 设计文档（待创建）。

---

## 2. 数据流

### 2.1 LineageService 数据流（P12-A）

```
GET /signals/{id}/lineage
  ↓
LineageService.get_signal_lineage(signal_id)
  ↓
1. select Signal where id=signal_id  → signal
   (404 if None)
  ↓
2. select SignalScoreSnapshot where signal_id=signal_id  → snapshot
   (best-effort：snapshot 可能 None，老数据 / 手动信号无快照)
  ↓
3. select PipelineRun where trade_date=signal.signal_date  → run
   (best-effort：可能 None，pipeline 未跑过 / 手动信号)
  ↓
4. 组装 SignalLineageResponse：
     signal_id / trade_date
     ├── score_snapshot:
     │     L1: composite_score / market_state / trigger_reason
     │     L2: trend_score / momentum_score / mean_reversion_score / value_score
     │         weights_source / hysteresis_status（来自 candidate_pool 同日同 ts_code 行）
     │         score_breakdown（旧 V1.0 字段，含原 strategy 权重明细）
     │     L3: raw_factors / factor_winsorized / factor_neutralized / factor_orthogonal
     │         score_breakdown_raw / score_breakdown_residual
     └── pipeline_run:
           trade_date / cp1_at / cp2_at / cp3_at / data_snapshot_version
```

**Phase 11 alembic 0009 已加字段**（无需新迁移）：
- `signal_score_snapshot`: `factor_winsorized` / `factor_neutralized` / `factor_orthogonal` (JSONB)
- `candidate_pool`: `composite_z` / `composite_pct_in_market` / `weights_source` / `hysteresis_status` / `score_breakdown_raw` / `score_breakdown_residual`
- `signal`: `composite_z` / `composite_pct_in_market` / `trigger_reason`

P12-A 仅做 schema/ORM 字段稳定读取，不动迁移。

### 2.2 AttributionService 数据流（P12-B）

```
MonthlyScheduler 月末 Job (24:00 of month_end)
  ↓
AttributionService.run_monthly(month_end_date)
  ↓
1. 取近 N 个月 candidate_pool（factor_neutralized JSONB → DataFrame[date, ts_code → 4 factor_z]）
  ↓
2. 取对应 forward_returns
     window = 20 交易日（与 ICIR 一致）
     forward_return(t, ts_code) = (close(t+window) - close(t)) / close(t)
   （Repository 用 daily_quote_adj 切片，calendar 跳过非交易日）
  ↓
3. engine.attribution.regression.run_ols(
       exposures = DataFrame[(date, ts_code) × 4 factor_z],
       returns   = Series[(date, ts_code) → forward_return],
   ) → AttributionResult
       coefficients: dict[factor → beta]
       residual_series: Series[(date, ts_code)]
       r_squared: float
       t_stats: dict[factor → t_stat]
       sample_size: int
  ↓
4. AttributionRepository.upsert_attribution(records)
     calc_date = month_end
     for factor in [trend, momentum, mean_reversion, value]:
         insert row(calc_date, factor, beta, t_stat, residual_std, r_squared, sample_size)
```

**API 实时查询路径**（GET /attribution/history）：

```
GET /attribution/history?start_date=...&end_date=...
  ↓
AttributionService.get_history(start, end)
  ↓
AttributionRepository.get_attribution_by_date_range(start, end)
  → list[AttributionHistory]
  ↓
schemas/attribution.py AttributionHistoryResponse 序列化
```

### 2.3 前端分层视图数据流（P12-C）

```
SignalsView
  ↓ 用户点击 SignalCard
SignalCard onClick → emit('viewLineage', signal)
  ↓
路由跳转 /signals/:id/lineage
  ↓
SignalLineageView mounted
  ↓
GET /signals/{id}/lineage → SignalLineageResponse
  ↓
三层折叠渲染：
  ├── L1 折叠（默认展开）：
  │     trigger_reason 翻译（"quantile_top_1pct" → "市场顶 1% 强烈买入"）
  │     market_state 中文（UPTREND → "上升趋势"）
  │     composite_score + composite_pct_in_market（"99.87 分 · 市场顶 0.05%"）
  ├── L2 折叠（默认折叠）：
  │     4 strategy_z bar chart（trend / momentum / mean_reversion / value）
  │     weights_source 标签（icir / default_matrix）
  │     hysteresis_status 标签（active / pending_switch / cooled_down）
  │     中性化前/后对比（factor_winsorized vs factor_neutralized JSONB tree）
  └── L3 折叠（默认折叠，"开发审计"提示）：
        raw_factors / factor_orthogonal JSONB tree
        score_breakdown_raw / score_breakdown_residual JSONB tree
        pipeline_run cp1_at / cp2_at / cp3_at / data_snapshot_version

  + AttributionPanel（嵌入 L2 底部，可选展开）：
    GET /attribution/history?start_date=signal.signal_date-30d&end_date=signal.signal_date
    → 4 因子收益 bar + R² + IC 趋势线
```

---

## 3. 模块详细设计

### 3.1 LineageService 重构（P12-A）

#### 3.1.1 当前状态（Phase 11 收尾）

```python
# services/lineage_service.py（Phase 11 现状）
score_snapshot = {
    "ts_code": signal.ts_code,
    "composite_score": ...,
    "market_state": ...,
    "score_breakdown": snapshot.score_breakdown,
    # Phase 11 §9.1：因子级溯源 5 字段（前端分层视图 Phase 12 渲染）
    "score_breakdown_raw": getattr(snapshot, "score_breakdown_raw", None),
    "score_breakdown_residual": getattr(snapshot, "score_breakdown_residual", None),
    "factor_winsorized": getattr(snapshot, "factor_winsorized", None),
    "factor_neutralized": getattr(snapshot, "factor_neutralized", None),
    "factor_orthogonal": getattr(snapshot, "factor_orthogonal", None),
}
```

**问题**：
1. `getattr` fallback 模糊"字段不存在"与"字段值为 NULL"两种语义——前端无法区分
2. `score_breakdown_raw` / `score_breakdown_residual` 实际在 **candidate_pool** 表（不在 signal_score_snapshot），Phase 11 P1-7 临时把这两个字段也用 getattr 从 snapshot 取——永远 None。需要从 candidate_pool 查
3. 返回值是 `dict` 类型，无 schema 约束，端点序列化 silently 漏字段

#### 3.1.2 Phase 12 实现

```python
# services/lineage_service.py（Phase 12 目标）
from quantpilot.models.business import (
    Signal, SignalScoreSnapshot, CandidatePool, MarketStateHistory,
)

class LineageService:
    async def get_signal_lineage(self, signal_id: int) -> dict | None:
        # 1. signal
        signal = ...
        if signal is None:
            return None
        trade_date = signal.signal_date

        # 2. snapshot（无 getattr，直接读 ORM 字段）
        snap_result = await self._session.execute(
            select(SignalScoreSnapshot).where(SignalScoreSnapshot.signal_id == signal_id)
        )
        snapshot = snap_result.scalar_one_or_none()

        # 3. candidate_pool 同日同 ts_code（取 score_breakdown_raw / residual / weights_source / hysteresis_status）
        pool_result = await self._session.execute(
            select(CandidatePool).where(
                CandidatePool.trade_date == trade_date,
                CandidatePool.ts_code == signal.ts_code,
            )
        )
        pool_row = pool_result.scalar_one_or_none()

        # 4. pipeline_run
        run = ...

        # 5. 装配（明确区分 NULL 与 missing）
        return {
            "signal_id": signal_id,
            "trade_date": str(trade_date),
            "score_snapshot": (
                _serialize_snapshot(signal, snapshot, pool_row)
                if snapshot is not None else None
            ),
            "pipeline_run": _serialize_run(run) if run is not None else None,
        }
```

#### 3.1.3 SignalLineageResponse pydantic schema（schemas/signals.py）

```python
class ScoreSnapshotLineage(BaseModel):
    """信号评分快照 L1+L2+L3 完整字段"""
    ts_code: str
    # L1 业务可解释
    composite_score: float | None = None
    composite_z: float | None = None
    composite_pct_in_market: float | None = None
    market_state: str | None = None
    trigger_reason: str | None = None
    # L2 ICIR + 中性化
    trend_score: float | None = None
    momentum_score: float | None = None
    mean_reversion_score: float | None = None
    value_score: float | None = None
    weights_source: str | None = None
    hysteresis_status: str | None = None
    score_breakdown: dict | None = None
    factor_winsorized: dict | None = None
    factor_neutralized: dict | None = None
    # L3 正交化 + 审计
    raw_factors: dict | None = None
    factor_orthogonal: dict | None = None
    score_breakdown_raw: dict | None = None
    score_breakdown_residual: dict | None = None


class PipelineRunLineage(BaseModel):
    trade_date: str
    cp1_at: str | None = None
    cp2_at: str | None = None
    cp3_at: str | None = None
    data_snapshot_version: str | None = None


class SignalLineageResponse(BaseModel):
    signal_id: int
    trade_date: str
    score_snapshot: ScoreSnapshotLineage | None = None
    pipeline_run: PipelineRunLineage | None = None
```

#### 3.1.4 兼容性

前端已消费的 dict 字段（Phase 7 留下）：`signal_id` / `trade_date` / `score_snapshot.composite_score` / `score_snapshot.market_state` / `score_snapshot.score_breakdown` / `pipeline_run.*` 全部保留——`SignalLineageResponse` 是旧 dict 的超集，前端不改动即可工作。

### 3.2 AttributionService OLS 归因（P12-B）

#### 3.2.1 engine/attribution/regression.py（纯函数）

```python
"""多因子回归归因（OLS 收益拆解）。Engine 层严格无 IO。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm


@dataclass(frozen=True)
class AttributionResult:
    """单次 OLS 归因结果。

    coefficients: 因子收益 β（每单位 z 暴露对应的收益）
    t_stats: 因子收益 t 统计量
    residual_std: 残差标准差
    r_squared: 模型解释力
    sample_size: 有效观测数（drop NaN 后）
    """
    coefficients: dict[str, float]
    t_stats: dict[str, float]
    residual_std: float
    r_squared: float
    sample_size: int


def run_ols(
    exposures: pd.DataFrame,
    returns: pd.Series,
    factors: list[str] | None = None,
) -> AttributionResult | None:
    """跑横截面 / panel OLS。

    Args:
        exposures: index 可为 ts_code（单日横截面）或 (date, ts_code)（panel），
                   columns 为因子名（与 factors 对齐）；值为标准化后因子暴露 z。
        returns: index 与 exposures 对齐；值为前向收益（已对齐窗口）。
        factors: 显式指定因子列；默认用 exposures.columns。

    Returns:
        AttributionResult；样本不足或矩阵奇异 → None（不抛异常，调用方决定降级）。
    """
    if factors is None:
        factors = list(exposures.columns)

    # 对齐 + drop NaN
    df = exposures[factors].copy()
    df["__y__"] = returns
    df = df.dropna()
    if len(df) < 10 * len(factors):  # 经验下限：每因子 10 观测
        return None

    X = sm.add_constant(df[factors].to_numpy(dtype=float))
    y = df["__y__"].to_numpy(dtype=float)
    try:
        model = sm.OLS(y, X).fit()
    except np.linalg.LinAlgError:
        return None

    coeffs = dict(zip(factors, model.params[1:], strict=True))  # 跳过 const
    t_stats = dict(zip(factors, model.tvalues[1:], strict=True))
    return AttributionResult(
        coefficients=coeffs,
        t_stats=t_stats,
        residual_std=float(np.std(model.resid)),
        r_squared=float(model.rsquared),
        sample_size=len(df),
    )
```

#### 3.2.2 services/attribution_service.py

```python
class AttributionService:
    def __init__(
        self,
        session: AsyncSession,
        repo: AttributionRepository,
        calendar: TradingCalendar,
        window_days: int = 20,
    ) -> None:
        self._session = session
        self._repo = repo
        self._calendar = calendar
        self._window = window_days

    async def run_monthly(self, month_end: date) -> list[AttributionHistory]:
        """月末计算近 N 月归因（N=ic_window_days/21≈12 月）。

        步骤：
        1. 拉近 N 月 candidate_pool.factor_neutralized → exposures
        2. 拉对应 forward_returns（window=20 交易日）
        3. 调 engine.run_ols → AttributionResult
        4. upsert attribution_history（calc_date=month_end, factor=*, beta/t_stat/...）
        """
        ...

    async def get_history(self, start: date, end: date) -> list[AttributionHistory]:
        return await self._repo.get_attribution_by_date_range(start, end)

    async def get_summary(self, start: date, end: date) -> AttributionSummary:
        """区间累计：每因子 cum_beta + 平均 R² + 总样本"""
        ...
```

#### 3.2.3 attribution_history 表（alembic 0010）

```sql
CREATE TABLE attribution_history (
    id            BIGSERIAL PRIMARY KEY,
    calc_date     DATE NOT NULL,           -- 月末日
    factor        VARCHAR(32) NOT NULL,    -- trend / momentum / mean_reversion / value
    beta          NUMERIC(10, 6) NOT NULL, -- 因子收益
    t_stat        NUMERIC(8, 4),
    residual_std  NUMERIC(10, 6),
    r_squared     NUMERIC(6, 4),
    sample_size   INTEGER NOT NULL,
    window_days   INTEGER NOT NULL,        -- forward_return window，默认 20
    created_at    TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_attribution_date_factor UNIQUE (calc_date, factor)
);
CREATE INDEX idx_attribution_date_desc ON attribution_history (calc_date DESC);
```

ORM：`models/business.py::AttributionHistory`（V1.0 收尾新增），字段映射同上。

### 3.3 前端三层折叠视图（P12-C）

#### 3.3.1 SignalCard L1 入口扩展

```vue
<!-- frontend/src/components/SignalCard.vue（Phase 12 扩展） -->
<template>
  <a-card hoverable size="small" ...>
    <!-- 现有第 1 行：tag + ts_code + name + strength + status -->
    <!-- 现有第 2 行：评分 / 仓位 / 日期 -->

    <!-- Phase 12 新增 L1 业务可解释（trigger_reason 翻译） -->
    <div v-if="signal.trigger_reason" class="lineage-reason">
      <a-tooltip :title="rawReasonTooltip">
        💡 {{ translatedReason }}
      </a-tooltip>
    </div>

    <!-- Phase 12 新增"溯源详情"按钮 -->
    <div style="margin-top: 6px; text-align: right">
      <a-button type="link" size="small" @click.stop="goLineage">
        查看评分溯源 →
      </a-button>
    </div>
  </a-card>
</template>
```

`translatedReason` 由 `utils/lineage.ts` 提供 trigger_reason → 中文映射：

```typescript
// frontend/src/utils/lineage.ts
export const TRIGGER_REASON_MAP: Record<string, string> = {
  quantile_top_1pct: '市场顶 1% 强烈买入',
  quantile_top_5pct: '市场顶 5% 买入',
  quantile_bottom_30pct: '市场底 30% 卖出',
  short_term_failure_1_5sigma: '短期失效（z 降幅 ≥ 1.5σ）',
  icir_monthly_negative: 'ICIR 月度转负',
  absolute_threshold_override: '绝对阈值覆盖',
}
```

#### 3.3.2 SignalLineageView 三层折叠

```vue
<!-- frontend/src/views/SignalLineageView.vue（新增） -->
<template>
  <div class="lineage-page">
    <PageHeader :title="`信号 #${signalId} 评分溯源`" />

    <a-collapse v-model:active-key="activeKeys" :bordered="false">
      <!-- L1：业务可解释（默认展开） -->
      <a-collapse-panel key="L1" header="💡 L1 · 业务可解释">
        <LineageL1Panel :data="lineage?.score_snapshot" />
      </a-collapse-panel>

      <!-- L2：ICIR + 中性化（默认折叠） -->
      <a-collapse-panel key="L2" header="📊 L2 · 因子分数 + ICIR 权重 + 中性化前后">
        <LineageL2Panel :data="lineage?.score_snapshot" />
        <a-divider>
          <TermLabel term="multi_factor_attribution" />
        </a-divider>
        <AttributionPanel :start="thirtyDaysBefore" :end="signalDate" />
      </a-collapse-panel>

      <!-- L3：正交化残差 + 审计（默认折叠，"开发审计"提示） -->
      <a-collapse-panel key="L3" header="🔬 L3 · 正交化残差 + Pipeline 审计">
        <a-alert
          message="本节面向开发与审计人员，含 Gram-Schmidt 残差与 Pipeline 时间戳。"
          type="info" show-icon style="margin-bottom: 12px"
        />
        <LineageL3Panel :data="lineage" />
      </a-collapse-panel>
    </a-collapse>
  </div>
</template>
```

#### 3.3.3 AttributionPanel 多因子归因图

```vue
<!-- frontend/src/components/AttributionPanel.vue（新增） -->
<template>
  <div>
    <h4>近期因子收益归因（OLS）</h4>
    <BarChart :data="factorBetas" :colors="FACTOR_COLORS" />
    <div class="meta">
      R² <b>{{ rSquared }}</b> · 样本 <b>{{ sampleSize }}</b> · 窗口 <b>{{ window }}d</b>
    </div>
  </div>
</template>
```

数据来自 `GET /attribution/history?start_date=...&end_date=...`。

---

## 4. API 端点设计

### 4.1 现有端点扩展

#### 4.1.1 GET /signals/{id}/lineage

| 项 | 值 |
|---|---|
| 路径 | `/signals/{signal_id}/lineage`（不变）|
| 鉴权 | JWT Bearer（不变）|
| 响应 | `SignalLineageResponse`（取代旧 dict）|
| 错误 | 404 信号不存在；422 signal_id 非法 |

响应示例：

```json
{
  "code": 0,
  "data": {
    "signal_id": 12345,
    "trade_date": "2026-05-12",
    "score_snapshot": {
      "ts_code": "600519.SH",
      "composite_score": 99.87,
      "composite_z": 3.85,
      "composite_pct_in_market": 0.0005,
      "market_state": "UPTREND",
      "trigger_reason": "quantile_top_1pct",
      "trend_score": 1.85,
      "momentum_score": 0.94,
      "mean_reversion_score": -0.21,
      "value_score": 1.12,
      "weights_source": "default_matrix",
      "hysteresis_status": "active",
      "score_breakdown": {...},
      "factor_winsorized": {...},
      "factor_neutralized": {...},
      "raw_factors": {...},
      "factor_orthogonal": {...},
      "score_breakdown_raw": {...},
      "score_breakdown_residual": {...}
    },
    "pipeline_run": {
      "trade_date": "2026-05-12",
      "cp1_at": "2026-05-12T15:30:00+08:00",
      "cp2_at": "2026-05-12T15:35:12+08:00",
      "cp3_at": "2026-05-12T15:37:48+08:00",
      "data_snapshot_version": "abc12345"
    }
  },
  "msg": "ok"
}
```

### 4.2 新增端点

#### 4.2.1 GET /attribution/history

| 项 | 值 |
|---|---|
| 路径 | `/attribution/history` |
| 鉴权 | JWT Bearer |
| 查询参数 | `start_date: date`（必填）/ `end_date: date`（必填）/ `factor: str \| None`（可选过滤）|
| 响应 | `AttributionHistoryResponse`（items 数组）|
| 错误 | 422 日期格式 / start > end |

#### 4.2.2 GET /attribution/summary

| 项 | 值 |
|---|---|
| 路径 | `/attribution/summary` |
| 查询参数 | `start_date: date` / `end_date: date` |
| 响应 | `AttributionSummaryResponse`（每因子 cum_beta + 平均 R² + 总样本）|

---

## 5. 数据库 schema（alembic 0010）

```python
# backend/alembic/versions/0010_phase12_attribution_history.py
"""Phase 12 attribution history table

Revision ID: 0010
Revises: 0009
"""
revision = "0010"
down_revision = "0009"


def upgrade() -> None:
    op.create_table(
        "attribution_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("calc_date", sa.Date, nullable=False),
        sa.Column("factor", sa.String(32), nullable=False),
        sa.Column("beta", sa.Numeric(10, 6), nullable=False),
        sa.Column("t_stat", sa.Numeric(8, 4), nullable=True),
        sa.Column("residual_std", sa.Numeric(10, 6), nullable=True),
        sa.Column("r_squared", sa.Numeric(6, 4), nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("window_days", sa.Integer, nullable=False, server_default="20"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"), nullable=False,
        ),
        sa.UniqueConstraint("calc_date", "factor", name="uq_attribution_date_factor"),
    )
    op.create_index(
        "idx_attribution_date_desc",
        "attribution_history",
        [sa.text("calc_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_attribution_date_desc", table_name="attribution_history")
    op.drop_table("attribution_history")
```

---

## 6. 测试用例编号

### 6.1 单元测试

| 编号 | 文件 | 用例 |
|------|------|------|
| **UT-P12-A-01** | `tests/unit/test_lineage_response_schema.py` | `SignalLineageResponse` 序列化字段齐全（17 字段）|
| **UT-P12-A-02** | `tests/unit/test_lineage_response_schema.py` | snapshot 为 None 时 `score_snapshot=null`（区分"无快照"与"NULL 字段"）|
| **UT-P12-B-01** | `tests/unit/test_attribution_regression.py` | `run_ols` 在样本 < 10×factor 时返回 None |
| **UT-P12-B-02** | `tests/unit/test_attribution_regression.py` | 因子矩阵奇异 → 返回 None（不抛 LinAlgError）|
| **UT-P12-B-03** | `tests/unit/test_attribution_regression.py` | 4 因子标准 N(0,1) panel + 真实 β=[0.05, 0.03, -0.02, 0.04] → 回归系数 ±0.01 容差 |
| **UT-P12-B-04** | `tests/unit/test_attribution_regression.py` | `AttributionResult` r_squared / t_stats / sample_size 字段完整 |

### 6.2 集成测试

| 编号 | 文件 | 用例 |
|------|------|------|
| **INT-P12-A-01** | `tests/integration/test_int_lineage_full_fields.py` | 跑 Phase 11 完整 pipeline 写一个 signal + snapshot + pool 行，调 LineageService.get_signal_lineage → L1+L2+L3 17 字段全部非 None |
| **INT-P12-A-02** | `tests/integration/test_int_lineage_full_fields.py` | 手动信号（无 snapshot）→ `score_snapshot=null`，`pipeline_run=null` |
| **INT-P12-A-03** | `tests/integration/test_int_lineage_full_fields.py` | 信号对应的 candidate_pool 行存在但 snapshot 不存在 → `score_snapshot=null`（L2 字段不从 pool 补，避免数据不自洽）|
| **INT-P12-B-01** | `tests/integration/test_int_attribution_monthly.py` | 月末跑 `AttributionService.run_monthly(month_end)` → attribution_history 写入 4 行 |
| **INT-P12-B-02** | `tests/integration/test_int_attribution_monthly.py` | candidate_pool 不足 12 月 → 返回空 list（不抛异常 / 不写 NULL 行）|
| **INT-P12-B-03** | `tests/integration/test_int_attribution_monthly.py` | 重跑同月 → upsert 不重复（uq_attribution_date_factor）|

### 6.3 E2E 测试

| 编号 | 文件 | 用例 |
|------|------|------|
| **E2E-P12-A-01** | `tests/e2e/test_signals_api.py` | `GET /signals/{id}/lineage` 200 + `SignalLineageResponse` 字段齐全 |
| **E2E-P12-A-02** | `tests/e2e/test_signals_api.py` | `GET /signals/999999/lineage` → 404 |
| **E2E-P12-A-03** | `tests/e2e/test_signals_api.py` | `GET /signals/abc/lineage` → 422 |
| **E2E-P12-B-01** | `tests/e2e/test_attribution_api.py`（新增）| `GET /attribution/history` 200 + items 数组结构 |
| **E2E-P12-B-02** | `tests/e2e/test_attribution_api.py` | `GET /attribution/history?start_date=2026-01-01&end_date=2025-01-01` → 422（start > end）|
| **E2E-P12-B-03** | `tests/e2e/test_attribution_api.py` | `GET /attribution/summary` 200 + 4 因子 cum_beta |
| **E2E-P12-B-04** | `tests/e2e/test_attribution_api.py` | 全部端点未鉴权 → 401 |

### 6.4 冒烟测试

| 编号 | 文件 | 用例 |
|------|------|------|
| **API-90** | `tests/smoke/test_api_live.py` | `GET /signals/{id}/lineage` 200 + 17 字段 |
| **API-91** | `tests/smoke/test_api_live.py` | `GET /signals/{id}/lineage` 401（无鉴权）|
| **API-92** | `tests/smoke/test_api_live.py` | `GET /signals/999999/lineage` 404 |
| **API-93** | `tests/smoke/test_api_live.py` | `GET /attribution/history` 200 + items |
| **API-94** | `tests/smoke/test_api_live.py` | `GET /attribution/summary` 200 + 4 因子 |
| **API-95** | `tests/smoke/test_api_live.py` | `GET /attribution/*` 全部 401（无鉴权）|

---

## 7. 验收基线

### 7.1 LineageService 数据完整性

Phase 11 5y 真机已积累 2022-04-25 / 2023-06-30 / 2024-09-30 / 2026-05-12 四个 trade_date 的 candidate_pool + signal + snapshot。Phase 12 收尾跑：

```bash
# 任选一个 BUY 信号 id
curl -H "Authorization: Bearer $TOKEN" "$API/signals/{id}/lineage" | jq .
```

**基线**：
- `score_snapshot.composite_score` ≥ 99.94 ∧ `composite_z` ≥ 2.33
- `factor_winsorized` / `factor_neutralized` / `factor_orthogonal` 均非 null（dict 有 4 因子键）
- `score_breakdown_raw` / `score_breakdown_residual` 来自同日 candidate_pool 非 null
- `pipeline_run.cp1_at` / `cp2_at` / `cp3_at` 均非 null

### 7.2 AttributionService 量级合理性

跑 `AttributionService.run_monthly(2026-05-31)`（或最近月末），attribution_history 行：

**基线**：
- 4 行 factor in [trend, momentum, mean_reversion, value]
- `sample_size` ≥ 1000（5y 4 trade_date × 50 pool × 12 月理论上 ≥ 1000；实际取决于数据回填进度）
- `r_squared` ∈ [0.005, 0.15]（横截面 OLS 经验范围：低 R² 正常）
- `|beta|` 各因子 ≤ 0.05（每单位 z 暴露对应 ≤ 5% 月度收益；超过提示因子定义异常）
- `|t_stat|` 若 > 2 标记"显著因子"

### 7.3 前端三层视图

人工验收：
- 进入 `/signals/123/lineage` 默认展开 L1 + 折叠 L2/L3
- L1 显示中文 trigger_reason + market_state
- 展开 L2 看到 4 strategy_z bar + AttributionPanel
- 展开 L3 看到 JSONB tree + pipeline_run 时间戳

---

## 8. DoD（Phase 收尾验收）

### 8.1 测试

- [ ] 单元测试通过（UT-P12-A-01~02 + UT-P12-B-01~04 共 6 用例）
- [ ] 集成测试通过（INT-P12-A-01~03 + INT-P12-B-01~03 共 6 用例）
- [ ] E2E 测试通过（E2E-P12-A-01~03 + E2E-P12-B-01~04 共 7 用例）
- [ ] `uv run ruff check src/ tests/` 输出 0 error
- [ ] 冒烟测试 API-90~95 入 `tests/smoke/test_api_live.py`，**手动逐行核对 §6.4 与实际函数避免场景漂移**

### 8.2 真机层

- [ ] Phase 11 5y 真机 4 trade_date × 3 state 任选一个 BUY 信号，`GET /signals/{id}/lineage` 返回 §7.1 基线字段
- [ ] `AttributionService.run_monthly(2026-05-31)` 写入 4 行 attribution_history，符合 §7.2 基线
- [ ] 前端 SignalsView → SignalCard → SignalLineageView 三层折叠人工验收通过

### 8.3 文档层

- [ ] system_design.md §9 Phase 12 行末加 ✅ 标记
- [ ] CLAUDE.md §9 V1.0 收尾批次行 Phase 12 状态更新（"完成 ✓"）
- [ ] 本设计文档加 v1.x 修订历史条目（实施完成 + 真机验收 + 偏差记录）
- [ ] memory/MEMORY.md 增加 Phase 12 经验条目
- [ ] SDD §12.3 "因子归因（V1.5+）"前缀更新为"因子归因（V1.0 Phase 12 已合入）"
- [ ] SDD §15.6 数据血缘行末标记 Phase 12 完成
- [ ] SDD §16 路线图行末标记"已合入 V1.0 Phase 12"

### 8.4 Phase 12 收尾必检（CLAUDE.md §5 收尾核查）

1. 本设计文档全部模块交付（对照 §1.1 表）
2. 无未交付模块（若有则更新 system_design §9 显式移入 Phase 13+）
3. 本文档"依据文档"引用章节号与实际实现范围一致
4. `uv run ruff check src/ tests/` 输出 0 error
5. 冒烟测试 API-90~95 入 `tests/smoke/test_api_live.py`
6. 集成测试通过（DB 容器在线 + alembic upgrade head）
7. 检查是否有新经验需要写入 CLAUDE.md

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Phase 11 5y 真机 4 trade_date 的 candidate_pool factor_neutralized JSONB 字段为空 / 部分缺失 | 中 | LineageService L2 字段返回 None，前端 L2 panel 显示"数据不足" | 集成测试 INT-P12-A-03 显式覆盖"snapshot 无但 pool 有"路径不补字段；前端 L2 Panel 加 fallback 文案"该日数据 V1.0 之前生成，无中性化记录" |
| AttributionService 月末 Job 性能 > 60s（5y 数据 × 4 因子 OLS）| 低 | 月末批延迟 | numpy 向量化；statsmodels OLS 单次 < 1s；预期总耗时 < 10s |
| attribution_history 与 factor_ic_window_state 双表混淆 | 中 | 前端 / 测试取错表 | (1) 文档 + ORM docstring 显式区分（ICIR 表 = 单因子 IC 时序；归因表 = 多因子收益拆解）；(2) API 路径分开 `/factor-quality/*` vs `/attribution/*` |
| 前端 SignalLineageView 加载 JSONB 树过大（> 1MB）导致渲染慢 | 低 | UX 卡顿 | L3 折叠默认折叠 + JSONB 按需懒加载 |
| 多因子回归归因被用户误解为"投资建议"（合规风险）| 中 | 法律 / 合规 | AttributionPanel 必带免责声明（"历史归因 ≠ 未来预测，仅用于内部审计与策略反思"），调用 `<DisclaimerBanner>` 复用 V1.0 Batch 1 组件 |

---

## 10. 实施序列

```
P12-A LineageService 后端稳定化（前置）
    ├── P12-A1 SignalLineageResponse pydantic schema + 单元测试 UT-P12-A-01/02
    └── P12-A2 LineageService.get_signal_lineage 去 getattr + candidate_pool join + 集成测试 INT-P12-A-01~03
        ↓
P12-B AttributionService（与 P12-A 并行）
    ├── P12-B1 engine/attribution/regression.py 纯函数 + 单元测试 UT-P12-B-01~04
    ├── P12-B2 alembic 0010 attribution_history + ORM
    ├── P12-B3 AttributionRepository + AttributionService.run_monthly + 集成测试 INT-P12-B-01~03
    ├── P12-B4 MonthlyScheduler.add_attribution_job + dispatch
    └── P12-B5 API /attribution/* + E2E 测试 E2E-P12-B-01~04
        ↓（P12-A + P12-B 完成后启动 P12-C）
P12-C 前端三层视图
    ├── P12-C1 types/api.ts + api/signals.ts + api/attribution.ts client
    ├── P12-C2 SignalCard.vue L1 入口扩展
    ├── P12-C3 SignalLineageView.vue + LineageL1/L2/L3 Panel 子组件
    ├── P12-C4 AttributionPanel.vue + 路由 + utils/lineage.ts trigger_reason 映射
    └── P12-C5 前端冒烟（人工三层折叠 + AttributionPanel 渲染）
        ↓
P12-D 测试 + 冒烟 + 文档同步
    ├── P12-D1 tests/smoke/test_api_live.py API-90~95 新增
    ├── P12-D2 ruff check 收尾 + 全套回归
    ├── P12-D3 CLAUDE.md §9 / system_design §9 / SDD §12.3 §15.6 §16 同步
    └── P12-D4 memory/MEMORY.md Phase 12 经验条目
```

依赖关系：
- P12-C 强依赖 P12-A（前端消费 SignalLineageResponse） + P12-B（前端消费 AttributionResponse）
- P12-A 与 P12-B 之间无依赖，可并行
- P12-D 是 P12-A/B/C 全部完成后的收尾
