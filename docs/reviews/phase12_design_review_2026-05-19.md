# Phase 12 设计文档评审（v1.0）

> **评审对象：** `docs/design/phases/phase12_factor_lineage.md` v1.0（2026-05-19，849 行；commit `f5a6ae4`）
> **依据：** SDD v1.4 §12.3 / §15.6 / §16 / system_design.md §9 Phase 12 行 / Phase 11 设计文档 v1.4 / Phase 11 实施评审 v1.0 §6.2 / v1_5_roadmap v2.0
> **评审角色：** Claude（Opus 4.7）/ 设计文档可行性评审
> **评审日期：** 2026-05-19
> **结论：** **基本通过（含 P1 修订动作）**——P12-A/B/C/D 模块划分合理；数据流 / API 设计 / DoD 与 SDD §12.3 §15.6 §16 + roadmap V1.5-B/E 升级清单一致；R12-P2-1/2/3/6 4 项 Phase 11 评审 P2 推迟项已显式列入 §1.4 穿插。**4 P1 + 5 P2 问题**：核心是 (1) **AttributionService 数据源指向错** + (2) **§7.1 验收基线"factor_winsorized/neutralized/orthogonal 均非 null"在当前实施基础上不可达成**——Phase 11 加了 ORM 列但 SignalService.save 未写入；建议**修订到 v1.1 后启动 P12 实施**。

---

## 0. 评审快照

| 维度 | 评级 | 备注 |
|---|---|---|
| 设计文档结构 | 🟢 | 10 章覆盖完整（范围 / 数据流 / 模块详设 / API / schema / 测试 / 验收 / DoD / 风险 / 实施序列） |
| 与 SDD v1.4 引用 | 🟢 | §12.3 因子归因 / §15.6 数据血缘 / §16 V1.5+ 升级清单全部正确引用 |
| 与 system_design §9 一致 | 🟢 | Phase 12 行末已有 ✅ 标记（设计文档交付状态）；估算 ~6-10 pd 已在 §9 注释中互链 |
| 与 v1_5_roadmap 一致 | 🟢 | V1.5-B（S1-GAP-01 / D1-GAP-02）+ V1.5-E（多因子回归）3 项升级 Phase 12 在 roadmap §★ 已对齐 |
| Phase 11 P1-7 lineage 后端继承 | 🟢 | §1.3 显式列入；P12-A 处理 getattr fallback 清理 |
| Phase 11 评审 P2 穿插（R12-P2-1/2/3/6）| 🟢 | §1.4 显式列入 4 项 + 收口追踪规则 |
| Phase 11 P2 编号外部追踪在设计文档正文使用 | 🟡 | 违反 CLAUDE.md §10 治理规则（详见 §2.2 P2-5） |
| 子任务 P12-A/B/C/D 编号一致性 | 🔴 | **§1.1 P12-D = "API 端点"** vs **§10 实施序列 P12-D = "测试+冒烟+文档同步"** 冲突（详见 §2.1 P1-1） |
| ScoreSnapshotLineage 字段名 vs ORM | 🔴 | `mean_reversion_score` 与 ORM `reversion_score` 不一致（详见 §2.1 P1-2） |
| AttributionService 数据源指向 | 🔴 | §2.2 / §3.2.2 把 `factor_neutralized` 标在 `candidate_pool`，实际在 `signal_score_snapshot`（详见 §2.1 P1-3） |
| §7.1 验收基线可达性 | 🔴 | Phase 11 5y 真机 `signal_score_snapshot.factor_winsorized/neutralized/orthogonal` 永远 NULL（SignalService.save 未写）→ "均非 null" 基线不可达（详见 §2.1 P1-4） |
| 数学正确性（OLS） | 🟢 | `run_ols` 主流程 statsmodels + sample_size 下限 + 异常返回 None 合理；UT-P12-B-03 ±0.01 容差需复核（§2.2 P2-2） |
| 实施序列依赖 | 🟢 | P12-A / P12-B 并行 → P12-C → P12-D 收尾；依赖关系明确 |

---

## 1. 设计文档完整性核对

### 1.1 SDD 引用与 V1.5 升级清单对齐

| 设计文档来源 | Phase 12 实施位置 | 验证 |
|---|---|---|
| SDD §12.3 因子归因（多因子回归收益拆解）| P12-B AttributionService + alembic 0010 attribution_history | ✅ 数学公式 `Rp = α + Σ βᵢ × Fᵢ + ε` 与 SDD §12.3 一致 |
| SDD §15.6 数据血缘（完整因子级溯源）| P12-A LineageService 后端 + P12-C 三层视图 | ✅ |
| SDD §16 V1.5+ 升级清单 | §8.3 DoD 列入"SDD §16 路线图行末标记已合入 V1.0 Phase 12" | ✅ |
| Phase 11 P1-7 LineageService 后端 5 字段（getattr 临时） | P12-A1: SignalLineageResponse schema；P12-A2: 去 getattr | ✅ |
| Phase 11 §12 DoD "前端分层视图归 Phase 12" | P12-C SignalLineageView + LineageL1/L2/L3 Panel | ✅ |
| V1.5-B S1-GAP-01（因子级溯源 P0 阻断）| P12-A + P12-C 一并解决 | ✅ |
| V1.5-B D1-GAP-02（SignalCard 不展示评分决策路径）| P12-C SignalCard L1 + Lineage 跳转 | ✅ |
| V1.5-E 多因子回归归因 | P12-B 完整实施 | ✅ |

### 1.2 模块清单完整性（§1.1 表）

| 子任务 | 模块数 | 覆盖完整性 |
|---|---|---|
| P12-A 后端稳定化 | 3 模块（LineageService / pydantic schema / endpoint response_model）| ✅ |
| P12-B AttributionService | 6 模块（OLS engine / Service / Repository / ORM / alembic 0010 / MonthlyScheduler Job）| ✅ |
| P12-C 前端三层视图 | 5 模块（SignalLineageView / SignalCard 扩展 / AttributionPanel / API client 与类型 / 路由）| ✅ |
| P12-D API 端点（§1.1） | 3 端点（GET /signals/{id}/lineage 扩展 + 2 个 attribution 新增）| ✅ 端点完整；但 P12-D 编号冲突（详见 P1-1）|

### 1.3 测试用例编号续接

| 编号段 | 数量 | 验证 |
|---|---|---|
| UT-P12-A-01/02 + UT-P12-B-01~04 | 6 | ✅ |
| INT-P12-A-01~03 + INT-P12-B-01~03 | 6 | ✅ |
| E2E-P12-A-01~03 + E2E-P12-B-01~04 | 7 | ✅ |
| API-90~95（冒烟，编号续接 Phase 11 API-89） | 6 | ✅ 无漂移 |

---

## 2. 缺陷清单

### 2.1 P1 残留（4 项，**建议修订到 v1.1 后启动 P12 实施**）

#### P1-1：子任务 P12-D 编号在 §1.1 与 §10 指代不同

**事实：**

| 章节 | P12-D 含义 |
|---|---|
| **§1.1（行 50-56）** | **P12-D API 端点**（3 端点：lineage / attribution/history / attribution/summary）|
| **§10 实施序列（行 839-843）** | **P12-D 测试 + 冒烟 + 文档同步**（包含 D1 冒烟新增 / D2 ruff 收尾 / D3 文档同步 / D4 memory 经验条目）|

§10 中 API 端点实际归到 **P12-B5**（行 830："P12-B5 API /attribution/* + E2E 测试 E2E-P12-B-01~04"），这与 §1.1 "P12-D API 端点" 冲突。

**风险：**

1. 实施工程师按 §1.1 找 "P12-D API 端点" 实施时会到 §10 看序列，发现 §10 P12-D 是测试 + 文档同步；混淆"端点该归 B5 还是 D"
2. /signals/{id}/lineage 端点的 `response_model=SignalLineageResponse` 改造在 §1.1 P12-D 内但 §10 序列没显式列入（只有 P12-B5 提到 attribution 端点）；lineage 端点改造可能漏做
3. CLAUDE.md §5 "新增 REST API 端点须在 tests/smoke/test_api_live.py 补充冒烟测试" 该任务挂在 P12-D（§1.1）还是 P12-D1（§10 测试）？

**修复（5 分钟）：**

二选一：

- **方案 A（推荐）**：§1.1 标题改为 "**P12-D 测试 / 冒烟 / 文档同步**"；§4 "API 端点设计" 重新挂在 P12-A（lineage endpoint）+ P12-B（attribution 端点；与 §10 P12-B5 一致）。**重新校核** §1.1 模块表（行 21-56）让 P12-D 行内容与 §10 一致
- **方案 B**：§10 实施序列把 P12-D 拆为 "P12-D API 端点（前端用）"，原"测试+冒烟+文档"改为 "P12-E"；§1.1 加 P12-E 行

任一方案选择后，§6.4 冒烟测试段落需要更新所属子任务编号。

---

#### P1-2：`ScoreSnapshotLineage.mean_reversion_score` 字段名与 ORM `reversion_score` 不一致

**事实：**

§3.1.3 pydantic schema（行 286-309）：

```python
class ScoreSnapshotLineage(BaseModel):
    ...
    # L2 ICIR + 中性化
    trend_score: float | None = None
    momentum_score: float | None = None
    mean_reversion_score: float | None = None    # ← 字段名
    value_score: float | None = None
```

但实际 ORM `SignalScoreSnapshot` / `CandidatePool` 字段名（`models/business.py:51, 134`）：

```python
reversion_score: Mapped[float | None] = mapped_column(Numeric(5, 2))   # ← 不是 mean_reversion_score
```

`engine/scorer.py::SCORE_COLUMN_MAP`（Phase 11 锁定）：

```python
SCORE_COLUMN_MAP = {
    "trend":           "trend_score",
    "momentum":        "momentum_score",
    "mean_reversion":  "reversion_score",   # ← strategy key=mean_reversion，DB 列=reversion_score
    "value":           "value_score",
}
```

Phase 11 已交付的 `SignalSnapshotResponse`（`schemas/signals.py:43`）也用 `reversion_score`。

**风险：**

1. Pydantic `model_validate(snapshot, from_attributes=True)` 从 ORM 取值时找 `snapshot.mean_reversion_score` 找不到 → 字段永远 None
2. 前端按 §3.1.3 写 TS 类型 `mean_reversion_score: number | null` —— Vue 模板渲染 `lineage.score_snapshot.mean_reversion_score` 永远拿到 null
3. 单元测试 UT-P12-A-01 "17 字段齐全" 会因该字段始终 None 而 fail（或者用 mock 绕过——但生产链路仍坏）

**修复（30 秒）：**

§3.1.3 改字段名为 `reversion_score` 与 ORM / Phase 11 schema 对齐：

```python
# L2 ICIR + 中性化
trend_score: float | None = None
momentum_score: float | None = None
reversion_score: float | None = None    # ← 改名匹配 ORM
value_score: float | None = None
```

§7.1 验收基线 + UT-P12-A-01 同步更新（如有 "mean_reversion_score" 字面值的话）。

---

#### P1-3：AttributionService 数据源指向错——`factor_neutralized` 不在 `candidate_pool`

**事实：**

§2.2 行 139：

> 1. 取近 N 月 **candidate_pool**（factor_neutralized JSONB → DataFrame[date, ts_code → 4 factor_z]）

§3.2.2 行 427：

> 1. 拉近 N 月 **candidate_pool**.factor_neutralized → exposures

但 Phase 11 alembic 0009（行 110-124）：

```python
# candidate_pool 扩展 6 列
op.add_column("candidate_pool", sa.Column("composite_z", ...))
op.add_column("candidate_pool", sa.Column("composite_pct_in_market", ...))
op.add_column("candidate_pool", sa.Column("weights_source", ...))
op.add_column("candidate_pool", sa.Column("hysteresis_status", ...))
op.add_column("candidate_pool", sa.Column("score_breakdown_raw", JSONB, ...))
op.add_column("candidate_pool", sa.Column("score_breakdown_residual", JSONB, ...))

# signal_score_snapshot 扩展 3 列
op.add_column("signal_score_snapshot", sa.Column("factor_winsorized", JSONB, ...))
op.add_column("signal_score_snapshot", sa.Column("factor_neutralized", JSONB, ...))    # ← 这才是真实位置
op.add_column("signal_score_snapshot", sa.Column("factor_orthogonal", JSONB, ...))
```

`factor_neutralized` 在 **signal_score_snapshot** 表，不在 candidate_pool。

**风险：**

1. P12-B3 实施时按设计文档查 candidate_pool.factor_neutralized → 列不存在 → SQL error
2. 即使切换查 signal_score_snapshot.factor_neutralized → 该字段在当前实施基础上全 NULL（详见 P1-4）→ exposures DataFrame 全 NaN → run_ols 返回 None → attribution_history 永远不写入

**修复（连同 P1-4 处理）：**

§2.2 + §3.2.2 的数据源重新设计——三个备选：

| 方案 | 数据源 | 优劣 |
|---|---|---|
| **A：补 Phase 11 implementation gap，让 signal_score_snapshot.factor_neutralized 真实写入** | signal_score_snapshot.factor_neutralized | 优：与 alembic 0009 schema 设计意图一致。缺：信号 ≠ 全 universe，归因样本只有当日有信号的 ~40-80 只股票 |
| **B：从 candidate_pool.score_breakdown_raw 解析 4 策略 z_raw（不是 4 因子 z）** | candidate_pool.score_breakdown_raw | 优：candidate_pool 覆盖 ~50 只 pool stocks。缺：score_breakdown_raw 是策略级 z（trend/momentum/mean_reversion/value）不是 SDD §12.3 所说的"Size/Value/Momentum"风险因子；归因含义偏向"策略归因"而非"因子归因" |
| **C：新增 `factor_exposures_history` 表持久化全 universe 的 4 因子标准化值** | 新表 + Phase 12 加迁移 0011 | 优：覆盖完整。缺：5y × 250 trade_date × 5000 universe × 4 factor ≈ 25M 行，迁移成本与设计复杂度大幅上升 |

**推荐方案 B**（与 candidate_pool score_breakdown_raw 已写入 5y 真机数据匹配）+ 改写 §2.2 / §3.2.2 数据来源 + 同步更新 §7.2 验收基线（"4 策略归因"而非"4 因子归因"）。同时 SDD §12.3 表述也需调整（"多因子回归"实际是"多策略回归"），或显式说明 V1.0 Phase 12 简化为 4 策略归因 / 完整 4 风险因子归因留 V1.5。

---

#### P1-4：§7.1 验收基线 "factor_winsorized / factor_neutralized / factor_orthogonal 均非 null" 在 Phase 11 实施基础上不可达成

**事实：**

§7.1 验收基线（行 743）：

> **基线**：
> - ...
> - `factor_winsorized` / `factor_neutralized` / `factor_orthogonal` 均非 null（dict 有 4 因子键）

但 **Phase 11 实施未把这 3 字段写入 `signal_score_snapshot`**——`signal_service.py::_build_snapshot_rows`（行 169-200）只写 11 个旧字段：

```python
rows.append({
    "signal_id": signal_id,
    "trade_date": trade_date,
    "ts_code": sig.ts_code,
    "composite_score": composite_score,
    "trend_score": trend_score,
    "reversion_score": reversion_score,
    "momentum_score": momentum_score,
    "value_score": value_score,
    "market_state": market_state,
    "score_breakdown": sig.score_breakdown,
    "raw_factors": sig.raw_factors,
    # ↑ 没有 factor_winsorized / factor_neutralized / factor_orthogonal
})
```

grep 确认全代码库无 `factor_winsorized` / `factor_neutralized` / `factor_orthogonal` 的写入路径——只有 ORM 定义（business.py:142-144）+ Phase 11 lineage_service.py 读取（getattr 路径 + 现已修订后直读）+ Phase 11 signal_service.py 读取（短期 z 降幅判定）。

**风险：**

1. **§7.1 验收基线无法达成**：Phase 11 5y 真机 4 trade_date 的 signal_score_snapshot 行该 3 字段全 NULL；P12-A 收尾人工执行 `curl /signals/{id}/lineage | jq` 看到 `factor_winsorized=null` → 验收 fail
2. Phase 11 P1-7 lineage 后端字段扩展 + Phase 12 P12-A 去 getattr 仅是把 NULL 包成 schema 类型——没解决 source 端不写入的问题
3. **AttributionService（P1-3）即使切换到 signal_score_snapshot.factor_neutralized，该字段也全 NULL**

**修复（P12-A 范围扩充，~0.5-1 pd）：**

Phase 12 P12-A 子任务清单加入：

> **P12-A0（前置）：补 SignalService.save 写入 signal_score_snapshot.factor_winsorized / factor_neutralized / factor_orthogonal。**
>
> 数据流：
> 1. Scorer.aggregate 已生成 CompositeScore.score_breakdown_raw / score_breakdown_residual（含每策略 z_raw / z_orthogonal_normalized）；FactorPipeline.run_steps_1_to_3 内部产物 winsorized / neutralized 序列**目前未持久化**
> 2. 方案 1：Scorer.aggregate 输出新增 `CompositeScore.factor_winsorized / factor_neutralized / factor_orthogonal: dict | None`，FactorPipeline 在 run_steps_1_to_3 内部回填到 Scorer 上下文
> 3. 方案 2：Scorer 仅记录最终 strategy_z 矩阵，SignalService.save 时从 score_breakdown_raw / residual 反推（简化版）
>
> Phase 11 5y 真机 4 trade_date 不回填（旧数据保持 NULL）；Phase 12 起新写入。§7.1 验收基线调整为"**Phase 12 上线后新生成的信号**对应快照 3 字段非 null"。

或者更激进：**§7.1 验收基线放宽**为 "factor_winsorized / factor_neutralized / factor_orthogonal **可为 null**（Phase 11 旧数据），Phase 12 实施期起新写入路径覆盖；端点行为侧只要求 schema 字段存在 + 类型正确"。

---

### 2.2 P2 残留（5 项，可在实施期穿插修订）

#### P2-1：R12-P2-* 编号在 §1.4 设计文档正文使用违反 CLAUDE.md §10

**事实：**

CLAUDE.md §10 治理规则：

> 禁止在设计文档中使用外部追踪编号：评审报告编号（如 DESIGN-09）、会话内问题编号（如 P-3、G-02、N-01）、仅存在于 memory 文件的技术债编号（如 TD-1/2/3）等，均不得出现在 SDD、system_design.md 或 phase 设计文档的正文及修订历史中

Phase 12 设计文档 §1.4 使用了 R12-P2-1 / R12-P2-2 / R12-P2-3 / R12-P2-6 编号，**这是评审报告（docs/reviews/phase11_implementation_review_2026-05-19.md）的编号**——按 §10 不应出现在设计文档正文。

**风险：**

1. R12-P2-* 编号在 Phase 11 评审报告中定义；Phase 12 实施工程师查阅本设计文档时需跳到评审报告才能理解；6 个月后评审报告归档 / 编号体系变化时本节失锚
2. Phase 11 评审 v1.0 §6.1 第 4 行 P2 编号是 "P2-2"（属于评审报告内编号），同样不在 SDD/system_design/phase 设计中正式定义

**风险等级：** 仅治理规则违反，不影响功能。CLAUDE.md §10 自身也允许"可接受的跨文档引用：在对应设计文档中有正式定义的编号"——但 R12-P2-* 不属此例（评审报告非设计文档）。

**修复建议（折中）：**

§1.4 标题保留 "Phase 11 实施评审 P2 穿插项"，但把 4 项重写为**直接描述问题内容**而非引用编号：

```markdown
| 来源 | 简述 | 关联子任务 | 修订要点 |
|---|---|---|---|
| 评审 §6.2 第 1 项 | `scorer.aggregate` 全 NaN 策略跳过路径加 `logger.info` | P12-A | ... |
| 评审 §6.2 第 2 项 / §6.1 第 4 项 | 删除 `SignalResponse.weights_source` 字段 | P12-A schema 改造同 commit | ... |
| 评审 §6.2 第 6 项 | `factor_pipeline.neutralize_industry=False` 分支决策 | P12-B | ... |
| 评审 §6.2 第 7 项 | `_DEFAULT_ORDER` 改为按 default_matrix 权重降序 | P12-A | ... |
```

或者全部用 [[file_path]] 引用代替编号（CLAUDE.md memory 风格）。

---

#### P2-2：UT-P12-B-03 OLS 回归系数 ±0.01 容差不合理

**事实：**

§6.1 UT-P12-B-03：

> 4 因子标准 N(0,1) panel + 真实 β=[0.05, 0.03, -0.02, 0.04] → 回归系数 **±0.01 容差**

4 因子 OLS 系数估计标准误 ≈ σ_residual / sqrt(n)；若 n = 10×4 = 40（§6.1 sample_size 下限）/ 4 因子 / σ_resid ≈ 0.5（残差合理量级），单系数 95% CI 宽度约 ±0.15。±0.01 容差需要 n ≈ 50000 才可靠达成——单元测试小样本下高概率失败。

**修复（实施时调）：**

二选一：

- 把 UT-P12-B-03 sample_size 改为 n ≥ 5000 + 容差 ±0.005（用合成正态噪声）
- 把容差放宽到 ±0.05（n = 40 仍可重现）+ 用 seed 固定（`np.random.default_rng(42)`）

---

#### P2-3：§7.1 验收基线 "17 字段全部非 None" 字段数与 §3.1.3 ScoreSnapshotLineage 不符

**事实：**

§3.1.3 ScoreSnapshotLineage 字段实际数：
- ts_code (1)
- L1: composite_score / composite_z / composite_pct_in_market / market_state / trigger_reason (5)
- L2: trend_score / momentum_score / mean_reversion_score（P1-2 修后改为 reversion_score）/ value_score / weights_source / hysteresis_status / score_breakdown / factor_winsorized / factor_neutralized (9)
- L3: raw_factors / factor_orthogonal / score_breakdown_raw / score_breakdown_residual (4)

合计 **1+5+9+4 = 19 字段**，UT-P12-A-01 / §7.1 写 "17 字段" 与实际差 2。

**修复（5 秒）：**

§7.1 + UT-P12-A-01 改 "17 字段" → "19 字段"。或者实施时按实际计数。

---

#### P2-4：§3.1.4 "前端不改动即可工作" 论断过于乐观

**事实：**

§3.1.4：

> 前端已消费的 dict 字段（Phase 7 留下）：`signal_id` / `trade_date` / `score_snapshot.composite_score` / `score_snapshot.market_state` / `score_snapshot.score_breakdown` / `pipeline_run.*` 全部保留——`SignalLineageResponse` 是旧 dict 的超集，前端不改动即可工作。

**风险：**

1. 前端 TS 类型如果定义为 `interface SignalLineage { score_snapshot: { ... } }` 严格匹配字段，新增 19 字段会让 TS 编译报"对象字面量多余属性"——除非用 `interface SignalLineage { score_snapshot?: { [key: string]: any } }`
2. Vue 模板用 `lineage.score_snapshot.composite_score` 这种访问方式不影响，但 TS 严格类型校验下需要 update interface
3. 这与 Phase 12 §1.4 R12-P2-2 "前端 grep 已确认无消费" 同样需要核实——P12-C 实施期前应再次 `grep -rn 'score_snapshot' frontend/src/` 确认

**修复（建议加入 §3.1.4）：**

> **兼容性核实：** Phase 12 实施期 P12-C 启动前由前端工程师 `grep -rn 'score_snapshot\|SignalLineage' frontend/src/` 确认实际消费字段；若 TS 类型严格匹配则**必须更新 `frontend/src/types/api.ts` 类型定义为 SignalLineageResponse**（19 字段全列入）。"不改动即可工作"仅对弱类型 JSON 消费成立。

---

#### P2-5：MonthlyScheduler attribution Job 接入位置与 Phase 11 双 Job 关系未说明

**事实：**

§1.1 P12-B 列入 "MonthlyScheduler attribution Job"；§10 P12-B4 也列入 "MonthlyScheduler.add_attribution_job + dispatch"。但 Phase 11 已经在 monthly_scheduler.py:150-180 `run_all` 中调用 `run_factor_monitoring`（写旧表 factor_ic_history）+ `run_icir_rebalance`（写新表 factor_ic_window_state + strategy_weights_history）—— **第三个 attribution Job 接入位置**与现有 2 个 Job 的依赖关系未在 §6.1 / §10 列入。

**修复（建议加入 §3.2.2 或 §6.1）：**

> **MonthlyScheduler Job 依赖：** attribution Job 与 icir_rebalance Job 并列，**无依赖**（attribution 数据源 candidate_pool.score_breakdown_raw 在 daily pipeline CP2 已写入，与月末 ICIR 无关）。调度顺序：`run_quarterly_financial_refresh` → `run_factor_monitoring`（旧表 Phase 7~10 baseline）→ `run_icir_rebalance`（Phase 11 新表）→ `run_attribution`（Phase 12 新）→ `run_monthly_report`。任一 Job 失败 best-effort 不阻塞下一个。

---

## 3. 与 Phase 11 评审 P2 推迟项的对接

| 评审报告 §6.2 编号 | Phase 12 §1.4 编号 | 关联子任务 | 修订建议清晰度 |
|---|---|---|---|
| §6.1 第 4 项 / §6.2 第 5 项（推迟） | **R12-P2-2** | P12-A schema 改造 | 🟢 清晰（前端 grep 已确认）|
| §6.2 第 5 项 | **R12-P2-1** | P12-A | 🟢 清晰 |
| §6.2 第 6 项 | **R12-P2-3** | P12-B | 🟡 推荐保留 + 补单测（措辞中性，未明确删除/保留决策）|
| §6.2 第 7 项 | **R12-P2-6** | P12-A | 🟢 清晰 |

P2-1 治理规则违反不影响内容正确性。

---

## 4. 数学正确性核对

| 项 | 验证 |
|---|---|
| OLS 多因子回归 `Rp = α + β_trend·z_trend + β_momentum·z_momentum + β_mr·z_mr + β_value·z_value + ε` | ✅ 与 SDD §12.3 一致；statsmodels.OLS 标准实现 |
| Forward returns 窗口 = 20 交易日（与 ICIR 一致） | ✅ §2.2 / §3.2.2 行 416 显式 `window_days: int = 20` |
| 样本下限 `n ≥ 10 × k_factors` (k=4 → n ≥ 40) | ✅ §3.2.1 行 386 显式 `if len(df) < 10 * len(factors): return None`；经验下限合理（每因子 10 观测） |
| 矩阵奇异退化 → 返回 None 不抛 | ✅ §3.2.1 行 392 显式 `except np.linalg.LinAlgError: return None` |
| `r_squared` 经验范围 [0.005, 0.15] | 🟢 横截面 OLS A 股个股月度收益经验值合理（市场层 R²≈0.1，因子层叠加后 0.005~0.15）|

---

## 5. 设计质量综合评估

| 项 | 评价 |
|---|---|
| **设计文档结构完整性** | 🟢 10 章节覆盖 scope / 数据流 / 模块 / API / schema / 测试 / 验收 / DoD / 风险 / 实施序列 |
| **与 SDD v1.4 引用** | 🟢 §12.3 / §15.6 / §16 全部正确 |
| **Phase 11 P1-7 lineage 衔接** | 🟢 §1.3 显式继承 |
| **Phase 11 评审 P2 4 项穿插** | 🟢 §1.4 显式列入 + 收口追踪规则 |
| **测试用例编号续接** | 🟢 UT/INT/E2E/API-90~95 与 Phase 11 编号无漂移 |
| **数学正确性** | 🟢 OLS 主流程合理 |
| **子任务编号一致性（P12-D）** | 🔴 §1.1 与 §10 冲突（P1-1） |
| **字段名 ORM 一致性** | 🔴 mean_reversion_score vs reversion_score（P1-2） |
| **AttributionService 数据源** | 🔴 candidate_pool.factor_neutralized 不存在该列（P1-3） |
| **§7.1 验收基线可达性** | 🔴 signal_score_snapshot.factor_* 在 Phase 11 实施基础上全 NULL（P1-4） |
| **治理规则（CLAUDE.md §10）** | 🟡 R12-P2-* 编号外部追踪（P2-1） |
| **测试容差合理性** | 🟡 UT-P12-B-03 ±0.01（P2-2） |
| **字段数核对** | 🟡 17 vs 19（P2-3） |
| **前端兼容性论断** | 🟡 "不改动即可" 过乐观（P2-4） |
| **MonthlyScheduler Job 依赖说明** | 🟡 P12-B Job 与 Phase 11 双 Job 关系未说明（P2-5） |
| **是否阻断 Phase 12 启动** | 🟡 **需先修订到 v1.1**（P1-1~P1-4 闭环后），不阻断长期路线 |

---

## 6. 修订动作建议

### 6.1 v1.1 修订（P12 实施启动前，≤ 0.5 pd）

| # | 动作 | 章节 | 优先级 |
|---|---|---|---|
| 1 | **P1-1**：统一 P12-D 编号——§1.1 标题改为 "P12-D 测试 / 冒烟 / 文档同步"；§4 API 端点重新挂在 P12-A / P12-B；§1.1 模块表第 50 行改写 | §1.1 / §4 / §10 | P1 |
| 2 | **P1-2**：§3.1.3 `mean_reversion_score` 改为 `reversion_score` 与 ORM 对齐 | §3.1.3 | P1 |
| 3 | **P1-3**：AttributionService 数据源改写——推荐方案 B（candidate_pool.score_breakdown_raw 解析策略 z）；§2.2 / §3.2.2 数据流图同步；同时调整 §7.2 基线措辞为"4 策略归因"或显式说明 V1.0 简化 | §2.2 / §3.2.2 / §7.2 | P1 |
| 4 | **P1-4**：P12-A 新增子任务 P12-A0 "补 SignalService.save 写入 signal_score_snapshot 3 个新 JSONB 字段"；或 §7.1 验收基线放宽为 "Phase 12 上线后新数据覆盖" | §1.1 / §7.1 / §10 | P1 |

### 6.2 v1.2 / Phase 12 实施期穿插

| # | 动作 | 章节 | 优先级 |
|---|---|---|---|
| 5 | **P2-1**：§1.4 R12-P2-* 编号改为"评审 §X.X 第 N 项"引用形式（治理规则合规） | §1.4 | P2 |
| 6 | **P2-2**：UT-P12-B-03 容差调整（±0.005 大样本 / 或 ±0.05 小样本 + seed=42） | §6.1 | P2 |
| 7 | **P2-3**：§7.1 + UT-P12-A-01 "17 字段" 改为 "19 字段"（或重新计数） | §6.1 / §7.1 | P2 |
| 8 | **P2-4**：§3.1.4 增加 "兼容性核实" 段——P12-C 启动前 grep 前端确认 + 必要时更新 TS 类型 | §3.1.4 | P2 |
| 9 | **P2-5**：§3.2.2 或 §6.1 加 MonthlyScheduler Job 依赖说明（4 Job 串行 best-effort）| §3.2.2 | P2 |

### 6.3 不需要新增评审轮次

P1 修订动作均为"措辞 / 字段名 / 数据源指向修正"，不涉及核心 OLS 算法或模块边界设计决策。修订到 v1.1 后**不需要在 P12-A 启动前再发起独立评审**——v1.1 修订完成后即可启动 P12-A1 单元测试编写。

---

## 7. 评审结论

**整体评级：基本通过（含 P1 修订动作）**

- ✅ **设计文档结构完整**：10 章覆盖 scope / 数据流 / 模块 / API / schema / 测试 / 验收 / DoD / 风险 / 实施序列
- ✅ **跨文档引用正确**：SDD v1.4 §12.3 / §15.6 / §16 + system_design §9 + roadmap V1.5-B/E 升级清单全部对齐
- ✅ **Phase 11 推迟项继承完整**：§1.3 P1-7 + §1.4 R12-P2-1/2/3/6 4 项穿插
- ✅ **数学正确性**：OLS 主流程 + 经验下限 + 异常处理合理
- 🔴 **4 P1 残留**（**修订到 v1.1 后启动 P12 实施**）：
  - P12-D 编号 §1.1 vs §10 冲突
  - `mean_reversion_score` 字段名与 ORM `reversion_score` 不一致
  - AttributionService 数据源指向错（factor_neutralized 不在 candidate_pool）
  - §7.1 验收基线 factor_winsorized/neutralized/orthogonal 在 Phase 11 实施基础上不可达成
- 🟡 **5 P2 残留**：治理规则编号 / OLS 容差 / 字段数 / 前端兼容性论断 / MonthlyScheduler Job 依赖
- ❌ **不阻断 Phase 12 长期路线**，但建议 P1 4 项修订到 v1.1 后再启动 P12-A1 实施

**建议下一步：**

1. **0.5 pd 修订到 v1.1**：P1-1 / P1-2 / P1-3 / P1-4 全部闭环
2. **P12-A 启动**：在 v1.1 基础上启动 P12-A1 单元测试编写
3. **实施期穿插** P2 5 项修订到 v1.2 / v1.3
4. **Phase 12 实施完成后** 做独立"Phase 12 实施代码评审"（与 Phase 11 实施评审格式一致）

---

## 8. 签署

| 项 | 值 |
|---|---|
| 评审人 | Claude（Opus 4.7）/ 设计文档可行性评审 |
| 评审日期 | 2026-05-19 |
| 评审依据 | Phase 12 设计文档 v1.0（commit `f5a6ae4`，849 行）+ SDD v1.4 + system_design.md §9 + Phase 11 设计文档 v1.4 + Phase 11 实施评审 v1.0 + v1_5_roadmap v2.0 + 当前 backend/src 实现（grep factor_winsorized 等字段写入路径）|
| 评审输出 | 设计完整性 ✅ + 4 P1 阻断 v1.0 实施启动 + 5 P2 实施期穿插 + 9 修订动作（v1.1 + v1.2/v1.3） |
| 阻断 Phase 12 长期路线 | ❌ 否 |
| 阻断 v1.0 直接启动 P12-A1 实施 | 🟡 建议先修订到 v1.1 |
| 是否需要下一轮独立评审 | ❌ 否（v1.1 修订完成后直接启动实施；Phase 12 实施完成后再做实施代码评审）|
