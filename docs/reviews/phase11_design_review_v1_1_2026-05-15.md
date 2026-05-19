# Phase 11 设计文档 v1.1 复审

> **评审对象：** `docs/design/phases/phase11_scoring_industrialization.md` v1.1（2026-05-15，1076 行；v1.0 951 行 → v1.1 +125 行）
> **依据：** `docs/reviews/phase11_design_review_2026-05-15.md` v1.0 评审（4 P0 + 8 P1 + 5 P2 + 14 修订动作）
> **评审角色：** Claude（Opus 4.7）/ 实现可行性复审
> **评审日期：** 2026-05-15
> **结论：** **通过（轻微残留）** —— v1.0 评审 17 项问题中 **15 项已完整闭环、2 项基本闭环存在小残留**；新引入 5 项小残留可在 v1.2 与 Phase 11 实施第一周同步处理，**不阻断启动 Phase 11 实施**。

---

## 0. 评审快照

| 维度 | v1.0 评级 | v1.1 评级 | 变化 |
|---|---|---|---|
| 模块路径与既有代码对应 | 🔴 | 🟢 | P0-1 闭环：ScoringService 统一指 `services/strategy_service.py::ScoringService`（不搬迁文件）|
| factor_ic_history schema 处理 | 🔴 | 🟢 | P0-2 闭环：改用新表名 `factor_ic_window_state`，Phase 7 旧表保留 readonly |
| MarketSnapshot 扩展 | 🔴 | 🟢 | P0-3 闭环：TypedDict 显式新增 `industry` / `market_cap` / `beta` 字段 |
| BaseStrategy.score() 改造路径 | 🔴 | 🟢 | P0-4 闭环：选项 A 明确（保留 score() + 新增 compute_strategy_factors）|
| §10.4 验收基线数值自洽 | 🟡 | 🟢 | P1-8 闭环：z ≥ 2.33 / score ≥ 99 / top 1% STRONG ≥ 30，三者数学等价 |
| §1.3 SDD 裁决表 vs §6.2 实施位置 | — | 🟡 残留 | **新发现**：§1.3 第 7 行仍指"MarketStateService.on_state_changed hook"，与 §6.2 v1.1 改写"不需改 market_state_service.py"冲突 |
| §3.3 Scorer.aggregate 签名 vs §3.4 调用方 | — | 🟡 残留 | **新发现**：§3.3 签名 `strategy_scores: dict[str, list[StrategyScore]]`，§3.4 调用 `strategy_factors=dict[str, pd.DataFrame]`，类型/名字不一致 |
| §13 风险表 | 🟢 | 🟡 微缺 | **新发现**：v1.1 引入"双表并存"新约束（factor_ic_window_state vs Phase 7 factor_ic_history readonly），未补入风险表 |
| 与 system_design §9 估算同步 | 🟡 | 🟢 | P2-5 闭环：system_design §9 Phase 11 行 + 注释均已加 12-18 pd |

**结论：** v1.0 评审 17 项问题全部修订到位（15 项完整闭环 / 2 项基本闭环含小残留）；新发现 3 项小残留（2 P1 + 1 P2）属于 v1.1 修订引入或暴露。**不阻断启动 Phase 11 实施**，建议在 P11-A1 单元测试启动前 v1.2 一次性收口。

---

## 1. v1.0 评审 17 项闭环验证

### 1.1 P0 全部闭环（4/4）✅

| # | v1.0 问题 | v1.1 修复 | 验证 |
|---|---|---|---|
| P0-1 | `services/scoring_service.py` 不存在 | §1.1 顶部加注："ScoringService 实际位于 `services/strategy_service.py`（Phase 4 创建），Phase 11 **不搬迁文件**，原地重写类内方法。所有 services/scoring_service.py 统一指 strategy_service.py::ScoringService。" §3.4 显式 "**原地重写**类内方法"。§12.1 DoD "services/strategy_service.py::ScoringService 原地重写编排（不搬迁文件）"。 | ✅ 三处一致 |
| P0-2 | factor_ic_history 表已存在却被标"新表" | §2.1 顶部新增"既有 factor_ic_history 处理策略"段：旧表保留 readonly + 新建 `factor_ic_window_state` 表，Phase 7~10 baseline 数据保留供回归对照。表正名从"新表 1：factor_ic_history" 改为"新表 1：factor_ic_window_state"。§9.1 端点回链 `factor_ic_window_state`。 | ✅ 全文 6 处引用已统一为新表名 |
| P0-3 | MarketSnapshot 缺 industry / market_cap | §1.1 P11-A 表新增"MarketSnapshot 扩展"行；§3.0 数据契约表加载位置说明 ScoringService._build_market_snapshot；§3.0 末追加 MarketSnapshot TypedDict 完整扩展定义（industry: dict / market_cap: pd.Series / beta: Series \| None）。 | ✅ §1.1 + §3.0 双向对齐 |
| P0-4 | BaseStrategy.score() 改造路径模糊 | §3.0.1 新增专章"BaseStrategy 改造（选项 A）"：保留 score() 输出 0-100（冷启动 / 单策略回测 / L1 reason），新增 compute_strategy_factors（默认实现 = compute_raw_factors）。"4 个具体策略子类零改动"。§3.4 调用方调 compute_strategy_factors。 | ✅ 改造范围、默认行为、稳态期 weights 语义均明确 |

### 1.2 P1 闭环（8/8）✅

| # | v1.0 问题 | v1.1 修复 | 验证 |
|---|---|---|---|
| P1-1 | 文件路径错位 | §9.2 改为"既有状态 / 变更"二列表，标注 signals.py / scoring.py / factor_quality.py 均已存在；新增 `ICRollingHistoryItem` 名匹配新表 factor_ic_window_state；§9.3 前端 ts 类型保留 "实施时核对" 兜底。 | ✅ |
| P1-2 | CompositeScore 缺 4 标量字段 | §3.3 dataclass 补回 trend_score / momentum_score / reversion_score / value_score，取值规则 `Φ(strategy_z_raw) × 100`；§3.4 write_candidate_pool 注释明确"兼容旧 4 列"。 | ✅ |
| P1-3 | run_monthly vs apply_monthly_rebalance 关系 | §4.0 新增专章"迁移路径"：MonthlyScheduler 切换 dispatch 到新方法；旧 run_monthly 保留作 fallback / 测试参考，不调度；旧表写入停止行保留。 | ✅ |
| P1-4 | IC_daily 持久化路径 | §4.0 明确"月末批后回算，不加新 CP"；SDD §7.4 "每日"措辞解读为"概念上每日 / 月末批后写入"。 | ✅ |
| P1-5 | FactorMonitorService session 注入方式 | §4.0 末段"无状态构造 `__init__(self, engine, repo)` 不持有 session；所有方法显式接收 session 参数"。§4.1 / §4.2 / §4.5 方法签名一致。 | ✅ |
| P1-6 | State 切换换权实施位置自相矛盾 | §6.2 重写："唯一在 `FactorMonitorService.get_active_weights` 中"；明确"不需改 market_state_service / daily_pipeline.py"；§1.1 P11-D 表实施位置同步更新。 | ✅（但 §1.3 SDD 裁决表残留，见 §2.1 R1）|
| P1-7 | LineageService 边界 | §1.1 P11-C 表新增"LineageService 后端字段扩展"行；§1.2 排除项改为"完整因子级溯源**前端**分层视图（SignalCard / SignalLineageView）→ Phase 12"。 | ✅ |
| P1-8 | §10.4 验收基线 z 阈值不自洽 | §10.4 重写为 6 行数值表 + 数学对应列：top 1% STRONG ≥ 30 / z ≥ 2.33 / score ≥ 99 / top 5% z ≥ 1.65 / score ≥ 95；末段加"85 分校准说明"解释旧版数学矛盾。§12.2 DoD 第 7 项同步更新。 | ✅ 数学完全自洽 |

### 1.3 P2 闭环（5/5）✅

| # | v1.0 问题 | v1.1 修复 | 验证 |
|---|---|---|---|
| P2-1 | "新增 11 个常量"数目对不上 | §7.1 标题改为 "新增 17 项配置（其中 11 项来自修订草案锁定值 + 6 项 dataclass 内部辅助）"。 | ✅ |
| P2-2 | UNIQUE 索引字段顺序 | §2.1 加"主查询模式"说明：(strategy, factor, state) 时序回看为主，月度批量查询走附加索引。 | ✅ |
| P2-3 | SignalGenerator.generate 参数命名 | §5.3 已用 `candidates=candidates`，§5.1 RiskParams 描述以"composite_pct_in_market"为主；§5.1 触发逻辑代码段简洁未引入老变量名。 | ✅ |
| P2-4 | `_max_strategy_z_drop_1d` 数据源 | §5.2 加完整 JSON 示例 + 函数签名细化：从 signal_score_snapshot.factor_orthogonal 取 top_contributor 的 z_orthogonal_normalized 跨日差；昨日 snapshot 不存在时跳过条件 3。 | ✅ |
| P2-5 | pd 估算回写 system_design | system_design §9 Phase 11 行已加 "(V1.0 收尾，~12-18 pd)" + ✅ 标记；§9 注释加 "Phase 11~15 估算合计 ~38-55 pd" + 逐 Phase 拆分；§14 末加"与 system_design §9 互为索引"。 | ✅ 双向回链 |

### 1.4 数学验证（§10.4 验收基线）

| 阈值 | 公式 | 实际值 | 一致性 |
|---|---|---|---|
| top 1% STRONG | composite_pct ≤ 0.01 ↔ z ≥ Φ⁻¹(0.99) | z ≥ 2.326 ≈ 2.33 | ✅ |
| top 1% composite_score | Φ(2.33) × 100 | ≈ 99.01 | ✅ |
| top 5% MODERATE | composite_pct ≤ 0.05 ↔ z ≥ Φ⁻¹(0.95) | z ≥ 1.645 ≈ 1.65 | ✅ |
| top 5% composite_score | Φ(1.65) × 100 | ≈ 95.05 | ✅ |
| top 1% STRONG 数量 | 可投资宇宙 ~3200 × 1% | ≈ 32 只（≥ 30）| ✅ |

数学完全自洽。

---

## 2. v1.1 引入或暴露的小残留

### 2.1 R1（P1）：§1.3 SDD 裁决表第 7 行与 §6.2 v1.1 修订冲突

**事实：**

§1.3 SDD 功能点裁决表第 7 行（行 96）：

> | §7.5 state 切换即时换权 | **§6.2 MarketStateService.on_state_changed hook** |

§6.2 v1.1 改写后（行 759~765）：

> **实施位置：唯一在 `FactorMonitorService.get_active_weights(session, trade_date, market_state)` 中** …… **不需改 `services/market_state_service.py`**（保持现状，无 hook 修改）。**不需改 `pipeline/daily_pipeline.py::_cp2_scoring`**（保持现状，调用形态不变）。

**风险：** 实施工程师按 §1.3 表查阅"§7.5 state 切换即时换权"对应实施位置时会到 §6.2 找 MarketStateService.on_state_changed hook，但 §6.2 明确不改这个方法 —— 二处需要同步。

**修复（10 秒）：** §1.3 第 7 行右列改为：

> | §7.5 state 切换即时换权 | §6.2 `FactorMonitorService.get_active_weights` 实时按 state 查 `strategy_weights_history`（不改 market_state_service）|

### 2.2 R2（P1）：§3.3 Scorer.aggregate 签名 vs §3.4 调用方类型不一致

**事实：**

§3.3 Scorer.aggregate 签名（行 410~420）：

```python
def aggregate(
    self,
    market_state: MarketStateEnum,
    strategy_scores: dict[str, list[StrategyScore]],     # ← 第 2 参数
    snapshot: MarketSnapshot,
    ...
) -> list[CompositeScore]:
```

§3.4 ScoringService.score_universe 调用方（行 486~495）：

```python
return self._scorer.aggregate(
    market_state=market_state,
    strategy_factors=strategy_factors,                    # ← 关键字 strategy_factors，类型 dict[str, pd.DataFrame]
    snapshot=snapshot,
    weights_runtime=weights_runtime,
    ...
)
```

调用方传 `strategy_factors=dict[str, pd.DataFrame]`（来自 P0-4 的 compute_strategy_factors 返回值），但接收方签名是 `strategy_scores: dict[str, list[StrategyScore]]`。这是名字 + 类型双重不一致。

**风险：** 实施 Scorer.aggregate 第一版会撞上 mypy / ruff 类型错误，或退而求其次按 §3.3 签名实现 → 然后 §3.4 调用方挂不上。

**修复（30 秒）：** §3.3 dataclass 签名改写为：

```python
def aggregate(
    self,
    market_state: MarketStateEnum,
    strategy_factors: dict[str, pd.DataFrame],   # 各策略 raw_factors 矩阵（compute_strategy_factors 返回值）
    snapshot: MarketSnapshot,
    weights_runtime: dict[str, float],
    weights_source: str,
    orthogonalize_order: list[str],
    hysteresis_status: str,
    single_strategy_mode: bool = False,
) -> list[CompositeScore]: ...
```

并在 §3.3 描述段加一句"`strategy_factors` 由 ScoringService 调各策略的 compute_strategy_factors 收集而来"。

### 2.3 R3（P2）：§13 风险表未补"双表并存"新约束

**事实：** v1.1 P0-2 修复引入"factor_ic_window_state（Phase 11 新）+ factor_ic_history（Phase 7 旧 readonly）"双表并存约束，但 §13 风险矩阵 6 项均为 v1.0 原内容，未列入新风险。

**潜在问题：**

- Phase 7 已有的 `/factor-quality/*` 端点（如已存在）若查 factor_ic_history 旧表，5y 真机数据在那里；新端点查 factor_ic_window_state，Phase 11 上线后才有数据 → 用户在两个端点看到不同时间窗口 / 不同口径 / 不同列名的数据
- Phase 12 / Phase 15 决定归并旧表时若延误，旧表 read-only 会变成长期"僵尸数据源"
- 测试集成时 `test_int_p11_monthly_rebalance.py` 若误调 run_monthly 旧路径会写错表

**修复（1 分钟）：** §13 风险表末尾追加一行：

```
| factor_ic_window_state 与旧 factor_ic_history 双表并存导致前端 / 测试误用旧表 | 中 | 中 | (1) MonthlyScheduler dispatch 切换后旧 run_monthly 加 logger.warning "已废弃，转用 apply_monthly_rebalance"；(2) Phase 7 已有 /factor-quality/* 端点（若存在）加注释指向新端点 factor_ic_window_state；(3) Phase 12 或 Phase 15 末归并旧表数据并 DROP（不在 Phase 11 范围内）|
```

### 2.4 R4（P2）：§3.0.1 compute_strategy_factors 默认实现的扩展意图缺论证

**事实：** §3.0.1（行 268~280）：

```python
def compute_strategy_factors(self, universe, market_data) -> pd.DataFrame:
    """默认实现 = compute_raw_factors（已有），子类无需覆写。"""
    return self.compute_raw_factors(universe, market_data)
```

这是"包一层皮 + 默认透传"——评审看到第一反应是"为什么不直接调 compute_raw_factors？"

**实际扩展价值：** 未来 V1.5+ 若策略要给管线提供降维后特征 / 多周期合成因子 / 因子工程后中间产物（与 raw_factors 不同），可以重写 compute_strategy_factors 不动 compute_raw_factors（后者继续作"业务可解释 raw 值"展示用）。

**修复（30 秒）：** §3.0.1 在默认实现段加一句：

> **为什么不直接用 compute_raw_factors？** 给 V1.5+ 留扩展接口——未来策略可能在 raw_factors 之上做降维 / 合成 / 多周期混合等加工，作为 5 步管线的入口；此时重写 compute_strategy_factors 不影响 compute_raw_factors（后者继续用于 L1 业务可解释 reason 文本）。Phase 11 默认实现透传，无运行时开销。

### 2.5 R5（P2）：§3.4 universe 类型转换边界

**事实：**

§3.4 score_universe 入参签名（行 466~472）：

```python
async def score_universe(
    self,
    session: AsyncSession,
    trade_date: date,
    universe: list[str],         # list[str]
    market_state: MarketStateEnum,
) -> list[CompositeScore]:
```

§3.4 内部调用（行 477）：

```python
strategy_factors = {
    s.name: s.compute_strategy_factors(universe_idx, snapshot)   # universe_idx?
    for s in self._strategies
}
```

`universe_idx` 没显式定义；§3.0.1 默认实现签名是 `compute_strategy_factors(universe: pd.Index, market_data)`。从 `list[str]` → `pd.Index` 需要一次 `pd.Index(universe)` 转换。

**修复（30 秒）：** §3.4 代码示例前加一行：

```python
universe_idx = pd.Index(universe, name="ts_code")
strategy_factors = {
    s.name: s.compute_strategy_factors(universe_idx, snapshot)
    for s in self._strategies
}
```

或在 §3.0.1 默认实现签名改为 `(universe: pd.Index | list[str], market_data)` + 内部归一化。前者更干净。

---

## 3. 总体评估

| 项 | 评价 |
|---|---|
| **v1.0 → v1.1 修订完整度** | 🟢 17 项中 15 项完整闭环、2 项基本闭环含小残留（占 88% 完整 + 12% 基本）|
| **v1.1 引入新问题** | 🟡 5 项小残留（2 P1 + 1 P2 + 2 P2），全部为"措辞一致性 / 表内数据回填漏掉一处"类问题，无设计层面新缺陷 |
| **数学自洽** | 🟢 §10.4 验收基线全部数学等价 |
| **与既有代码契合度** | 🟢 ScoringService / MarketSnapshot / BaseStrategy / factor_ic_window_state 全部对齐实际结构 |
| **跨文档一致性** | 🟢 system_design §9 估算同步、SDD §7-10 v1.4 引用、本设计 §1.3 / §1.4 / §10.4 与 SDD / 修订草案锁定值一致 |
| **TDD 与 DoD** | 🟢 6 unit / 5 integration / 3 e2e / 5 smoke 测试覆盖 P0~P2 全部新逻辑 |
| **是否阻断 Phase 11 启动** | ❌ 不阻断 |

---

## 4. 修订动作建议（v1.2 / Phase 11 第一周）

### 启动 P11-F 迁移 0009 / P11-A1 单元测试之前（≤5 分钟）

| # | 动作 | 章节 |
|---|---|---|
| 1 | R1：§1.3 SDD 裁决表第 7 行实施位置改为 `FactorMonitorService.get_active_weights` | §1.3 |
| 2 | R2：§3.3 Scorer.aggregate 第 2 参数改名 / 改类型为 `strategy_factors: dict[str, pd.DataFrame]` | §3.3 |
| 3 | R5：§3.4 代码示例加 `universe_idx = pd.Index(universe, name="ts_code")` 显式转换 | §3.4 |

### Phase 11 实施第一周内（同步）

| # | 动作 | 章节 |
|---|---|---|
| 4 | R3：§13 风险表新增"双表并存"风险条目 + 缓解策略 | §13 |
| 5 | R4：§3.0.1 加 compute_strategy_factors 默认实现的扩展意图说明 | §3.0.1 |

### 不需要新增评审轮次

5 项残留均属"措辞 / 类型一致性"类小问题，不涉及设计决策。**Phase 11 实施过程中边做边修订到 v1.2 / v1.3**即可，无需再发起独立评审。

---

## 5. 评审结论

**整体评级：通过（轻微残留）**

- ✅ **v1.0 评审 17 项问题全部修订**：15 项完整闭环 + 2 项基本闭环
- ✅ **设计骨架与 SDD v1.4 / 既有代码结构、Q1~Q11 锁定决策完整一致**
- ✅ **数学自洽**：§10.4 验收基线 top 1% STRONG / z ≥ 2.33 / score ≥ 99 / top 5% z ≥ 1.65 / score ≥ 95 全部相互等价
- ✅ **跨文档一致**：system_design §9 + SDD v1.4 + roadmap v2.0 + 本设计 v1.1 全链路引用对齐
- 🟡 **5 项小残留**（2 P1 + 3 P2）：§1.3 表 vs §6.2 实施位置 / §3.3 签名 vs §3.4 调用方类型 / §13 风险表 / §3.0.1 论证 / §3.4 universe 转换 —— 均为措辞 / 类型一致性问题，10 分钟可全部修订
- ❌ **不阻断 Phase 11 启动**

**建议下一步：**

1. **5 分钟内** R1 + R2 + R5（最影响实施的 3 项）修订到 v1.2
2. **启动 P11-F 迁移 0009**——开始 P11 实施
3. **第一周内** R3 + R4 修订到 v1.2 / v1.3
4. **Phase 11 收尾时**逐项核对本评审 §4 修订动作清单

**下一次评审节点：** Phase 11 实施完成后做收尾评审，对照本评审 + v1.0 评审的全部修订动作 + DoD §12 验收。

---

## 6. 签署

| 项 | 值 |
|---|---|
| 评审人 | Claude（Opus 4.7）/ 实现可行性复审 |
| 评审日期 | 2026-05-15 |
| 评审依据 | v1.0 评审 17 项动作清单 + SDD v1.4 + 既有 backend/src 实现 + system_design §9 已加 pd 估算 |
| 评审输出 | 17/17 v1.0 问题闭环（15 完整 / 2 基本）+ 5 v1.1 新残留 + 5 修订动作 |
| 阻断 Phase 11 启动 | ❌ 否 |
| 是否需要下一轮独立评审 | ❌ 否（残留小问题随 Phase 11 实施同步修订即可）|
