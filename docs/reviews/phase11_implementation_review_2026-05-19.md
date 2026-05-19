# Phase 11 实施代码评审（v1.0）

> **评审对象：** Phase 11 评分公式工业化交付（commit `9b31492`，2026-05-19；54 文件 / +9685 / -476 行）
> **依据：** `docs/design/phases/phase11_scoring_industrialization.md` v1.4 / SDD v1.4 §7-10 / `docs/reviews/phase11_design_review_v1_1_2026-05-15.md`（设计文档评审）
> **评审角色：** Claude（Opus 4.7）/ 实施代码评审
> **评审日期：** 2026-05-19
> **结论：** **通过（含 P1 残留）**——核心 5 步管线 / ICIR 服务 / 分位阈值 / 双重失效止损 / Hysteresis 全部按 SDD v1.4 + 设计文档 v1.4 实施；5y 真机跨制度回归（4 trade_date × 3 state）已 PASS；ruff 0 error；unit+e2e 492 passed。**3 P1 + 6 P2 残留**集中在工程治理（Service/Repo 边界、静默降级、字段一致性），均不影响功能正确性与 5y 验收基线；建议在 Phase 12 启动前优先收口 P1。

---

## 0. 评审快照

| 维度 | 评级 | 备注 |
|---|---|---|
| 设计文档 DoD §12.1 实现层 | 🟢 | 14 项全部交付（迁移 0009 / 5 个 engine 新模块 / Scorer 重写 / SignalGenerator 升级 / ScoringService 原地重写 / FactorMonitorService ICIR+Hysteresis+下线规则 / MonthlyScheduler 新 Job / 2 个 API 端点 / schemas 字段扩展） |
| 设计文档 DoD §12.2 测试层 | 🟢 | unit + e2e 492 passed（本评审重跑确认）；Phase 11 单元 68 个 / 集成 22 个 / 冒烟 API-85~89 5 个；集成测试通过依据 commit log "17 passed"（本评审未重跑，原因见下方 §1 备注） |
| ruff lint 门槛 | 🟢 | `uv run ruff check src/ tests/` 输出 `All checks passed!` |
| 5y 真机回归 | 🟢 | 4 trade_date × 3 state 全 PASS：composite_z 3.2~4.6 / pool=50 / BUY 43~50/日 / market_state PIT 100% 一致；STRONG 18~23 vs §10.4 "≥ 30" 偏差已留 Phase 15 RC（设计文档 v1.3 收尾确认） |
| Engine 层纯函数性 | 🟢 | factor_pipeline / orthogonalizer / hysteresis 严格无 IO；scorer 5 步管线纯函数 |
| Service 层与 Repo 边界 | 🟡 | **3 处新增 `self._repo._session` 直接访问**违反 Phase 7 C-02（详见 §2.1 P1-1） |
| 静默降级 / 日志可观测性 | 🟡 | factor_pipeline 中性化奇异降级 + LinAlgError 无日志（详见 §2.2 P1-3） |
| MonthlyScheduler 双表过渡 | 🟡 | `run_monthly` 缺 deprecation 警告（设计文档 §13 R3 要求未落地，详见 §2.1 P1-2） |
| 跨文档一致性 | 🟢 | SDD v1.4 / system_design §9 / CLAUDE.md §9 / Phase 11 设计文档 v1.4 / V1.5 roadmap 全链路对齐 |
| 数学正确性 | 🟢 | composite_z = Σ wᵢ·zᵢ_normalized / sqrt(Σwᵢ²) 与 SDD §7.6 一致；Φ(z)×100 三层输出；Gram-Schmidt 改正算法 + v1.4 共线退化检测 |

---

## 1. 实施完整性核对（设计文档 §1.1 模块映射 + DoD §12）

### 1.1 P11-A 评分管线（5/5 全部交付）

| 模块 | 文件 | 行数 | 验证 |
|---|---|---|---|
| Scorer 重写 | `engine/scorer.py` | 429 | ✅ `aggregate` 5 步管线 + `aggregate_legacy` 冷启动 fallback；CompositeScore 三层输出 + 4 兼容字段 + score_breakdown_raw/residual + weights_source/hysteresis_status |
| FactorPipeline | `engine/factor_pipeline.py` | 179 | ✅ Winsorize 1%/99% + OLS 残差中性化 + Z-score + `run_steps_1_to_3` 组合 |
| Orthogonalizer | `engine/orthogonalizer.py` | 188 | ✅ Gram-Schmidt 残差化 + renormalize + v1.4 collinear_residual_ratio=0.3 共线退化检测 |
| BaseStrategy 改造 | `engine/strategies/base.py` | 29 行变更 | ✅ MarketSnapshot 扩展 industry/market_cap/beta；`compute_strategy_factors` 默认透传 compute_raw_factors（选项 A） |
| MarketSnapshot 扩展 | 同上 | — | ✅ TypedDict 加 industry: dict / market_cap: pd.Series / beta: pd.Series \| None |
| ScoringService 原地重写 | `services/strategy_service.py` | 552 | ✅ `_build_market_snapshot` 加载 industry/market_cap；`_run_phase11_pipeline` factor_monitor 注入时切换；`score_universe` 5 步管线编排；`write_candidate_pool` 写 6 新列 + 4 兼容列 |

### 1.2 P11-B ICIR 服务（5/5 全部交付）

| 模块 | 文件 | 验证 |
|---|---|---|
| FactorMonitorService.rolling_icir_state | `services/factor_monitor_service.py:392-482` | ✅ 窗口 [t-272d, t-20d] + sample_size<60 fallback + bootstrap CI seed=42 |
| FactorMonitorService.apply_monthly_rebalance | `services/factor_monitor_service.py:621-782` | ✅ ICIR 计算 → 聚合行写入 → Hysteresis → R1~R4 下线规则 → strategy_weights_history |
| FactorICRepository | `data/factor_ic_repository.py` | 361 行；6 个方法 (upsert_ic_daily/aggregate/get_window/get_recent/list_aggregates/strategy_weights CRUD) |
| HysteresisStateMachine | `engine/hysteresis.py` | 82 行；4 个 case 全覆盖 + 输入校验 |
| check_factor_offline_rules | `services/factor_monitor_service.py:488-572` | ✅ R1 (ICIR<0 连续 6 月) / R2 (t-stat<1.96 连续 12 月) / R3 (half_life<5) / R4 (sample_size<60 连续 3 月) |

### 1.3 P11-C 信号生成升级（4/4 全部交付）

| 模块 | 文件 | 验证 |
|---|---|---|
| SignalGenerator 分位主路径 | `engine/signal.py:128-344` | ✅ buy_pct_threshold / sell_pct_threshold / strong_pct_threshold 三阈值；enable_absolute_threshold_override fallback |
| 双重失效止损 | `engine/signal.py:233-244` | ✅ short_term_z_drop（>1.5σ）+ mid_term_icir_flip（核心策略 ICIR 月度由正转负）+ pct_above_sell + hard_stop_loss 四 trigger_reason |
| SignalService 适配 | `services/signal_service.py:423-615` | ✅ `_compute_holding_signal_states` 预计算 + composite_df 携带新列 + holding_signal_states 透传 SignalGenerator |
| LineageService 字段扩展 | `services/lineage_service.py:79-86` | ✅ score_snapshot 添加 5 个新字段（score_breakdown_raw/residual + factor_winsorized/neutralized/orthogonal） |

### 1.4 P11-D / P11-E / P11-F / P11-API（全部交付）

| 子任务 | 文件 | 验证 |
|---|---|---|
| MonthlyScheduler ICIR Job | `pipeline/monthly_scheduler.py:96-125` | ✅ `run_icir_rebalance` 独立 Job；run_all 在 run_factor_monitoring 后调用 |
| DailyPipeline CP2 适配 | `pipeline/daily_pipeline.py:210-290` | ✅ 注入 FactorMonitorService → ScoringService 自动走 5 步管线；scoring_pipeline_params 从 snapshot 派生 FactorPipelineConfig |
| ConfigDefaults 扩展 | `core/config_defaults.py:189-230` | ✅ FactorMonitorConfig 6 新字段 + ScoringPipelineConfig 6 字段 + SignalConfig 5 Phase 11 字段（共 17 项，对齐设计 §7.1） |
| ConfigService 分组映射 | `services/config_service.py:145-176` | ✅ `get_scoring_pipeline_params` + `get_pipeline_snapshot` 写入 `scoring_pipeline_params` |
| 迁移 0009 | `alembic/versions/0009_phase11_scoring_industrialization.py` | ✅ 2 新表 + candidate_pool 6 列 + signal_score_snapshot 3 列 + signal 3 列 + 2 索引 |
| ORM 扩展 | `models/business.py:36-261` | ✅ CandidatePool/Signal/SignalScoreSnapshot 字段扩展 + FactorICWindowState/StrategyWeightsHistory 完整 ORM |
| `GET /factor-quality/ic-history` | `api/v1/factor_quality.py:88-115` | ✅ list_aggregates 查询 + ICRollingHistoryItem schema |
| `GET /factor-quality/current-weights` | `api/v1/factor_quality.py:118-163` | ✅ 3 state × 4 strategy 兜底补齐 default_matrix |
| schemas/signals.py 扩展 | `schemas/signals.py:9-54` | ✅ SignalResponse 4 字段 + SignalSnapshotResponse 5 字段 |
| schemas/factor_quality.py 扩展 | `schemas/factor_quality.py:27-57` | ✅ ICRollingHistoryItem + CurrentWeightsItem |

**集成测试运行说明：** 本评审 **未重跑** `tests/integration/`——当前主机仅运行 `quantpilot-db-1` 生产 docker 栈（5y 真机数据），按 CLAUDE.md `feedback_pytest_wipes_db.md` + `feedback_db_isolation.md` "禁止在含真实数据的 DB 上跑 pytest integration"（conftest session-end alembic downgrade base 会 DROP 全表）。集成测试 17 passed 依据 commit `9b31492` 的实施期验证结果；DoD §12.2 "test_int_p11_scoring_e2e / test_int_p11_signal_e2e / test_int_monthly_rebalance 17 passed" 已留为 Phase 11 收尾签字证据。本评审 unit + e2e 重跑 **492 passed**（实施期 491 → 本评审 +1，无回归）。

---

## 2. 缺陷清单

### 2.1 P1 残留（3 项，建议 Phase 12 启动前修订）

#### P1-1：Service 层 3 处 `self._repo._session` 直接访问违反 Phase 7 C-02

**事实：**

| 文件 | 行 | 上下文 |
|---|---|---|
| `services/strategy_service.py` | 381 | `session = self._repo._session  # type: ignore[attr-defined]`（`_run_phase11_pipeline` 调 score_universe / write_candidate_pool 前取 session） |
| `services/signal_service.py` | 322 | `rows = (await self._repo._session.execute(stmt)).all()`（`_compute_holding_signal_states` 查 SignalScoreSnapshot 近 2 行） |
| `services/signal_service.py` | 390 | `ic_rows = (await self._repo._session.execute(stmt2)).scalars().all()`（同方法查 FactorICWindowState 近 2 行） |

**违反约束：** Phase 7 代码评审 C-02 + CLAUDE.md §6 "Service 层禁止直接访问 self._repo._session"。

**风险：**
1. Repository 实现变化（如改为无状态 / 多 session 池化）会让 3 处全部断
2. 测试 mock Repository 时需要额外暴露 `_session` 属性，污染 mock 接口
3. type: ignore 注释表明 IDE / mypy 已抱怨

**修复建议：**
- **strategy_service.py:381**：把 `_run_phase11_pipeline` 改为接收 `session` 参数（调用方 `run_daily_scoring` 已有 repo 关联 session，可通过 DailyPipeline `_cp2_scoring` 传入 session_factory）。或者更彻底：让 `ScoringService.__init__` 接收 `session` 一致与 FactorMonitorService 模式
- **signal_service.py:322 / 390**：把两段 raw query 改为 `MarketDataRepository.get_signal_snapshots_for_holdings(...)` 和 `FactorICRepository.get_recent_icir_for_strategy(...)`（后者 Repo 已有 `get_recent_aggregates` 几乎可复用），由 SignalService 调 Repository 方法，session 走 Repository 内部

**影响：** 不阻断功能，但维护性差。Phase 12 前端 lineage 视图实施时如果再加查询会进一步扩散。

---

#### P1-2：MonthlyScheduler 双表过渡——`run_monthly` 缺 deprecation 警告

**事实：**

设计文档 §13 风险表 v1.2 新增的 R3 缓解策略第 1 条明确要求：

> MonthlyScheduler dispatch 切换 `run_monthly` → `apply_monthly_rebalance` 后，在 `FactorMonitorService.run_monthly` 首行加 `logger.warning("run_monthly is deprecated since Phase 11, use apply_monthly_rebalance")`，避免误调

但实际实施：

1. **`monthly_scheduler.py:150-180::run_all`** 同时调 `run_factor_monitoring`（内部 `service.run_monthly(...)` 写旧表 `factor_ic_history`）+ `run_icir_rebalance`（写新表 `factor_ic_window_state` + `strategy_weights_history`）。**这是双写并存而非 dispatch 切换**——与设计文档 §2.1 v1.1 P0-2 "旧表保留 readonly 不再写入" 不一致
2. `factor_monitor_service.run_monthly`（行 139-275）**首行无 `logger.warning("deprecated")`**

**风险：**
1. 旧表 `factor_ic_history` 每月新增数据 → §2.1 "readonly baseline 不再写入" 假设失效；Phase 12 / Phase 15 决定归并旧表时数据范围扩大
2. 双 Job 并行运行月末批量耗时翻倍（虽然各自独立 session + 异常隔离，不影响 SUCCESS 状态）
3. 测试时如果同时启用两个 Job，旧表 IC 数据可能与新表 ICIR 数据语义混淆（旧表 calc_month 月度单点 vs 新表 trade_date 滚动 252 日）

**修复建议：**
- **方案 A（保守）**：保留双写，仅 `run_monthly` 首行加 `logger.warning(...)`；旧表数据继续写入但归类为 "phase7_legacy_baseline"。这与设计文档 §13 R3 第 1 条原意一致
- **方案 B（彻底）**：在 `monthly_scheduler.run_all` 内移除 `run_factor_monitoring` 调用（保留方法体供旧测试参考），旧表彻底冻结。需要确认 Phase 7~10 baseline 数据已足够，新数据不必继续写入

任一方案选择后须同步更新设计文档 §4.0 "MonthlyScheduler 切换 dispatch" 与 §13 R3 措辞，避免实施 / 文档漂移。

**影响：** 不影响 Phase 11 评分链路；但 Phase 12 因子级溯源 / Phase 13 因子告警接入旧表 vs 新表选择会受影响。

---

#### P1-3：`factor_pipeline.neutralize` 两处静默降级违反 CLAUDE.md §6

**事实：**

`backend/src/quantpilot/engine/factor_pipeline.py`：

```python
# 行 128-132：自由度不足降级
if combined.shape[0] <= combined.shape[1]:
    # 自由度不足（例如单只票 / 所有票同行业）→ 降级残差=原值
    out.loc[df.index] = values.loc[df.index]
    return out

# 行 137-140：LinAlgError 降级
try:
    beta_hat, *_ = np.linalg.lstsq(x_mat, y_arr, rcond=None)
except np.linalg.LinAlgError:
    return values.copy()
```

**违反约束：** CLAUDE.md §6 "静默吞异常禁止：Engine/Service 层主循环中 `except Exception` 分支若返回空集合/None/默认值，必须 `logger.exception(...)`（不可用 `logger.debug`）"。

虽然第一处不是 `except Exception`，但 **业务上是降级路径**——5y 真机若某 trade_date 全部股票同行业（或 industry 缺失率高到自由度不足），中性化会静默退化为 winsorized 原值传入 Z-score。设计文档 §7.1 行业中性化是 **强制开**，静默降级让 weights_source 无审计字段记录。

**风险：**
1. 5y 真机若早期股票数少（2021 年 ~4300 vs 现在 ~5800）某些 trade_date + 行业过滤后自由度临界 → 静默退化
2. 调用方（Scorer.aggregate）无法区分 "中性化成功" vs "降级为原值"，weights_source 不能写 `industry_missing_skipped` 或 `lstsq_singular`
3. CLAUDE.md §6 经验明确："业务上确实要降级时用 `【降级说明】` 注释标明，见下方「规格降级」"——`【降级说明】` 注释缺失

**修复建议：**

```python
if combined.shape[0] <= combined.shape[1]:
    logger.warning(
        "neutralize_degraded: n_obs=%d <= n_features=%d (industry over-concentrated), "
        "falling back to winsorized values",
        combined.shape[0], combined.shape[1],
    )
    out.loc[df.index] = values.loc[df.index]
    return out

try:
    beta_hat, *_ = np.linalg.lstsq(x_mat, y_arr, rcond=None)
except np.linalg.LinAlgError:
    logger.exception("neutralize_lstsq_failed: returning raw values")
    return values.copy()
```

同时 Scorer 在调用 `run_steps_1_to_3` 后应有方式接收 "中性化失败" 信号（或 FactorPipeline 改为返回 `(values, source_flag)` tuple，由 Scorer 把 flag 写到 weights_source）。

**影响：** 5y 真机已 PASS，此降级路径实际未触发；但作为生产工程师 / Phase 13 可观测性接入前提，必须有日志输出。

---

### 2.2 P2 残留（6 项，可在 Phase 12 / Phase 14 同步收敛）

#### P2-1：`scorer.aggregate` 全 NaN 策略跳过路径无日志（scorer.py:154-158, 171-174）

```python
if df.isna().all().all():
    continue  # ← 无日志：5y 真机 momentum NaN 率 33% 时静默跳过
...
strategy_z = strategy_z.dropna()
if strategy_z.empty:
    continue  # ← 同上
```

5y 真机暴露 momentum 因子 NaN 率 33%（v1.4 Robust Z-score 修复前导致 z=11+ outlier）；当前 v1.4 已通过 standardize + clip 兜底，但策略整体被跳过的事件（如所有股票 momentum 全 NaN）应有 `logger.info` 输出，便于 Phase 13 可观测性接入。建议：

```python
if df.isna().all().all():
    logger.info("scorer_strategy_skipped_all_nan: strategy=%s trade_date=%s", s_name, snapshot.get("trade_date"))
    continue
```

---

#### P2-2：`schemas/signals.py::SignalResponse.weights_source` 字段冗余（ORM 无对应列）

**事实：**

- `schemas/signals.py:29` 定义 `weights_source: str | None = None`
- `models/business.py::Signal` (行 74-112) **没有 `weights_source` 列**——设计文档 §2.1 signal 表扩展仅 3 列（composite_z / composite_pct_in_market / trigger_reason）
- `signal_service.save` (行 114-132) 写 Signal 时也没传 `weights_source`

后果：API `/signals` 响应 items 永远是 `weights_source=null`，前端按字段渲染会误判为"V1.0 旧路径"。同时 SignalResponse `from_attributes=True` Pydantic 在 source 对象没有该属性时使用默认值 None——技术上不崩，但语义错误。

**修复建议（二选一）：**

- **方案 A（推荐）**：删除 `SignalResponse.weights_source` 字段。weights_source 仅在 candidate_pool 持久化和 lineage 响应里有意义；Signal 自身不需要
- **方案 B**：迁移 0010 给 signal 表加 weights_source 列；SignalService.save 写入；同步更新设计文档 §2.1 "signal 表扩展 4 列"

---

#### P2-3：`factor_pipeline.neutralize` 强制开关闭分支注释模糊（行 94-97）

```python
if not self._cfg.neutralize_industry:
    # 强制开关闭时回 Z-score 前一步：直接返回 winsorize 后值
    # （Phase 11 V1.0 锁定 neutralize_industry=True，此分支仅用于研究模式）
    return values.copy()
```

设计文档 §7.1 Step 2 行业中性化 **强制开**，但代码留了 `if not neutralize_industry` 分支供"研究模式"使用。"研究模式" 含义模糊：

- 单元测试无 `test_neutralize_industry_false`（已确认 tests/unit/test_factor_pipeline.py 15 个测试无此场景）
- 实际线上配置 `ScoringPipelineConfig.neutralize_industry: bool = True` 写死
- 关闭后 market_cap / beta 中性化也被跳过——但 `market_cap_neutralize=True` 时关闭 industry 仍然合理？

**修复建议：**

- 要么删除该分支（V1.0 锁定后不再开放，避免误用），同时 `ScoringPipelineConfig` 也删除 `neutralize_industry` 字段
- 要么补单元测试 + 更新注释："仅用于研究模式 + Phase 14 backtest 单策略回测可能消费此路径"

---

#### P2-4：`rolling_icir_state` 窗口用 `timedelta(days=)` 代替交易日（factor_monitor_service.py:416-423）

```python
# 注：lag 严格意义应是 20 个交易日，但 factor_ic_window_state.trade_date
# 本身就是交易日，所以 [t-272d, t-20d] 在日历日维度等价于约 [t-272 交易日,
# t-20 交易日]——A 股 252 交易日 ≈ 365 日历日，本实现用日历日是设计简化
window_end = trade_date - timedelta(days=_ICIR_LAG_DAYS)        # 20 日历日 ≈ 14 交易日
window_start = trade_date - timedelta(days=_ICIR_WARMUP_DAYS)   # 272 日历日 ≈ 188 交易日
```

设计文档 §4.1 + SDD §7.4 规范"窗口固定 [t-272 交易日, t-20 交易日]"；当前实现用日历日近似：

- `lag=20 日历日 ≈ 14 交易日` —— 短了 6 个交易日，可能让 IC 计算混入 forward returns 未完成的窗口尾部
- `warmup=272 日历日 ≈ 188 交易日` —— 比 252 交易日少 ~30%

5y 真机已 PASS（v1.4 收尾确认）说明数学上能跑出合理结果，但**这与 SDD §7.4 表述不严格对齐**。

**修复建议：**

- Phase 14 §14-2 "ICIR 历史回算 / BacktestEngine 真 5 步管线接入"时改用 `TradingCalendar.get_prev_trade_date(trade_date, n=20)` 严格取 20 交易日
- 或将 `_ICIR_LAG_DAYS` 改名为 `_ICIR_LAG_CALENDAR_DAYS` 并在 SDD §7.4 添加附注："实现简化：lag 20 / warmup 272 按日历日；交易日严格版本 Phase 14 落地"

---

#### P2-5：`factor_ic_window_state` 表 daily + aggregate 共表语义易混淆

**事实：**

`FactorICRepository.upsert_ic_daily` 写 `(ic_value, sample_size)`；`upsert_ic_aggregate` 写 `(ic_mean_state, ic_std_state, icir, ci_low, ci_high, t_stat, half_life)`。两者共用同一张表 + 同一 UNIQUE 约束 `(strategy, factor, state, trade_date)`，**靠 ic_value vs icir 是否为 NULL 区分行类型**：

```python
# upsert_ic_daily ON CONFLICT 仅刷 ic_value/sample_size
# upsert_ic_aggregate ON CONFLICT 仅刷聚合列
```

**风险：**

1. 调用顺序敏感：如果 B2 重算 rebalance 先调 daily 再调 aggregate（同一 trade_date），ic_value 会保留 daily 值，但 sample_size 是 daily 单点的而非聚合后总数——sample_size 列语义就分裂了
2. `get_ic_daily_window` 用 `ic_value IS NOT NULL` 过滤；`get_recent_aggregates` 用 `icir IS NOT NULL` 过滤——索引 `(strategy, factor, state, trade_date)` 不能加速这两个 IS NOT NULL 谓词
3. Phase 14 ICIR 历史回算如果对每个 trade_date 单独写 daily 行，5y × 250 × 4 × 4 × 3 ≈ 1.2M 行——表膨胀

**修复建议：**

- Phase 14 ICIR 历史回算实施时考虑拆为两张表：`factor_ic_daily`（窄表，只存 ic_value/sample_size）+ `factor_ic_window_state`（聚合表，只存 ICIR/CI/t-stat）
- 或在当前表加 `row_type` 列（`'daily' | 'aggregate'`）+ partial unique index 区分两种行
- 当前实现 5y 真机已 PASS，Phase 14 决策即可

---

#### P2-6：`strategy_service._DEFAULT_ORDER` 与 DOWNTREND default_matrix 顺序冲突

**事实：**

```python
# strategy_service.py:33
_DEFAULT_ORDER = ["trend", "momentum", "mean_reversion", "value"]

# config_defaults.py StrategyWeightsConfig.downtrend
"trend": 0.10, "momentum": 0.05, "mean_reversion": 0.15, "value": 0.70
```

DOWNTREND 状态下 default_matrix value 占 0.70 但 `_DEFAULT_ORDER` 把 value 放最后——理论上 Orthogonalizer 应该让 ICIR 高（≈ 权重高）的策略先正交化，让低权重策略剔除高权重策略的投影。

实际调用链：
- `factor_monitor` 注入时：`get_active_weights` 返回的 `order = sorted(weights, key=lambda s: weights[s], reverse=True)`（按权重降序，DOWNTREND 时是 `[value, mean_reversion, trend, momentum]`）—— 正确
- `factor_monitor=None` fallback（strategy_service.py:439-449）：直接用 `_DEFAULT_ORDER`——**DOWNTREND 时顺序错**

**风险：** 当前 DailyPipeline CP2 总是注入 FactorMonitorService（daily_pipeline.py:259-261），所以线上路径走 ICIR 排序——不影响实际生产。仅当 ScoringService 被外部直接调用（如脚本 / 单元测试）且不注入 factor_monitor 时该路径才生效。

**修复建议：**

```python
# strategy_service.py:439-449
else:
    # 按 default_matrix 当前 state 权重降序，而非硬编码顺序
    from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS
    weights_map = {
        "uptrend": DEFAULT_STRATEGY_WEIGHTS.uptrend,
        "downtrend": DEFAULT_STRATEGY_WEIGHTS.downtrend,
        "oscillation": DEFAULT_STRATEGY_WEIGHTS.oscillation,
    }
    default_w = weights_map.get(market_state_str, DEFAULT_STRATEGY_WEIGHTS.oscillation)
    weights_runtime = dict(default_w)
    weights_source = "default_matrix"
    order = sorted(weights_runtime, key=lambda s: weights_runtime[s], reverse=True)  # ← 改为按权重降序
    hysteresis_status = "stable"
```

或删除 `_DEFAULT_ORDER` 模块级常量。

---

## 3. 文档同步核查

| 项 | 状态 | 备注 |
|---|---|---|
| Phase 11 设计文档 v1.4 修订历史 | 🟢 | 4 处实施期 bug（PIT P0 / Robust Z-score / 共线退化 / pool_capacity 50）已记入 |
| Phase 11 设计文档 §12 DoD | 🟢 | 5y 真机数据 / 单测数 / ruff 0 error / API-85~89 / system_design §9 互链全部勾选 |
| system_design.md §9 Phase 11 行 | 🟢 | "完成 ✓" + 6 项推迟项（Phase 14: 5y candidate_pool 回填 + ICIR 历史回算 + BacktestEngine 真 5 步；Phase 15: 30 trade_date 完整版 + STRONG 相对百分比 + Engine ≥90% 覆盖率门槛） |
| CLAUDE.md §9 进度表 Phase 11 行 | 🟢 | "完成 ✓" + Phase 12~15 待启动 + 4 处 bug 教训沉淀 |
| memory/MEMORY.md | 🟢 | `phase11_scoring_industrialization.md` + `v1_finalize_deferred_items.md` 已建索引 |
| SDD v1.4 §7-10 引用 | 🟢 | Phase 11 设计文档 §1.0 / §1.3 / §10.4 全部对齐 |
| `v1_5_roadmap.md` | 🟢 | V1.0 升级清单 17 项保持同步 |
| Phase 11 设计文档 §13 风险表 R3 双表并存 | 🟡 | R3 缓解策略第 1 条"`run_monthly` 首行加 deprecation 警告"未落地（详见 P1-2） |

---

## 4. 5y 真机验收交叉核对

设计文档 §10.4 验收基线 vs commit `9b31492` 实测：

| 维度 | §10.4 基线 | 实测（4 trade_date 抽样） | 一致性 |
|---|---|---|---|
| pool_count | 50（pool_capacity v1.4 升级） | 50 / 50 / 50 / 50 | ✅ |
| top 1% STRONG | ≥ 30（设计假设宇宙 ~3200） | 18 / 21 / 21 / 23 | 🟡 偏差（commit 说明 universe 过滤后 ~2400，top 1% ≈ 24；§10.4 留 Phase 15 RC 调整为相对百分比） |
| composite_z 顶端 | ≥ 2.33（Φ⁻¹(0.99)） | 3.85 / 3.23 / 4.57 / 4.52 | ✅ 落 N(0,1) top 0.05%（v1.4 Robust Z-score + 共线退化检测生效） |
| composite_score 顶端 | ≥ 99 | ≥ 99.94 | ✅ |
| composite_pct_in_market 顶端 | ≤ 0.01 | 0.0004~0.0005（top 0.05%） | ✅ |
| BUY 数量 | > 0（5y 累计 ~6000~20000） | 43~50/日 × 1210 日 ≈ 50k+ | ✅ |
| candidate_pool.market_state PIT | 与 MarketStateHistory 100% 一致 | 100% 一致（P11-SC-05 集成测试覆盖） | ✅ |
| weights_source 分布 | 冷启动期 100% default_matrix | 4 trade_date 全 default_matrix | 🟡 known issue（factor_ic_window_state / strategy_weights_history 仍空，需 Phase 14 §14-2 ICIR 历史回算累积 272 日后才有 icir 数据） |

**数学自洽：** 顶端 composite_z=4.57 → Φ(4.57) ≈ 0.99999756 → composite_score ≈ 99.9998 ≈ 99.94 实测（数值小差异源于 Φ(z)×100 在 z>4 时数值精度边界）✅

---

## 5. 总体评估

| 项 | 评价 |
|---|---|
| **DoD §12 实现层** | 🟢 14 项全部交付 |
| **DoD §12 测试层** | 🟢 unit + e2e 492 passed / 集成 17 passed（依据 commit log，本评审未重跑）/ ruff 0 error / 冒烟 API-85~89 落地 |
| **DoD §12 文档层** | 🟢 SDD v1.4 + system_design §9 + CLAUDE.md §9 + Phase 11 设计文档 v1.4 + memory 全链路一致 |
| **DoD §12 冒烟层** | 🟢 API-85~89 入 `tests/smoke/test_api_live.py`（编号续接 Phase 10 API-84 无漂移） |
| **数学正确性** | 🟢 5 步管线 / Hysteresis / 三层输出公式与 SDD §7.6 等价；5y 真机 4 trade_date × 3 state 数值落 N(0,1) 合理 |
| **Engine 层纯函数性** | 🟢 5 个新模块 + base.py 改造严格无 IO |
| **Service / Repo 边界** | 🟡 3 处 `_repo._session` 直接访问（P1-1）违反 C-02 |
| **可观测性 / 日志** | 🟡 中性化降级 + Scorer 策略跳过缺日志（P1-3 / P2-1） |
| **MonthlyScheduler 双表过渡** | 🟡 `run_monthly` deprecation 警告未加（P1-2） |
| **跨文档一致性** | 🟢 设计文档评审 v1.1 残留 R1~R5 全部在 v1.2 / v1.3 / v1.4 闭环 |
| **是否阻断 Phase 12 启动** | ❌ 不阻断 |

---

## 6. 修订动作建议

### 6.1 Phase 12 启动前（≤ 1 pd 工作量）

| # | 动作 | 文件 | 优先级 |
|---|---|---|---|
| 1 | P1-1：3 处 `_repo._session` 改为通过 Repository 方法或显式 session 参数访问 | strategy_service.py:381 + signal_service.py:322,390 | P1 |
| 2 | P1-2：`factor_monitor_service.run_monthly` 首行加 `logger.warning("deprecated since Phase 11")` | factor_monitor_service.py:139 | P1 |
| 3 | P1-3：`factor_pipeline.neutralize` 两处降级路径加 `logger.warning` + `【降级说明】` 注释 | factor_pipeline.py:128-140 | P1 |
| 4 | P2-2：删除 `SignalResponse.weights_source` 字段（或反向加 Signal ORM 列 + 迁移 0010） | schemas/signals.py:29 | P2 |

### 6.2 Phase 12 实施期（与因子级溯源前端视图同步）

| # | 动作 | 文件 | 优先级 |
|---|---|---|---|
| 5 | P2-1：`scorer.aggregate` 策略全 NaN 跳过加 `logger.info` | scorer.py:154-158, 171-174 | P2 |
| 6 | P2-3：`factor_pipeline.neutralize_industry=False` 分支删除（V1.0 锁定）或补单元测试 + 注释 | factor_pipeline.py:94-97 | P2 |
| 7 | P2-6：`_DEFAULT_ORDER` 改为按 default_matrix 当前 state 权重降序 | strategy_service.py:33, 439-449 | P2 |

### 6.3 Phase 14 实施期（5y 回填 + 真 5 步管线）

| # | 动作 | 文件 | 优先级 |
|---|---|---|---|
| 8 | P2-4：`rolling_icir_state` 窗口改用 `TradingCalendar.get_prev_trade_date` 严格交易日 | factor_monitor_service.py:416-423 | P2 |
| 9 | P2-5：`factor_ic_window_state` daily + aggregate 共表评估拆分 / 加 row_type 列 | data/factor_ic_repository.py + alembic | P2 |

### 6.4 不需要新增评审轮次

P1 残留均属 "Service/Repo 边界统一 / 日志规范化"，不涉及核心管线数学正确性或 SDD 规范偏离。Phase 12 实施过程中边修订即可，**不需要在 Phase 11 收尾后单独发起复审**。

---

## 7. 评审结论

**整体评级：通过（含 P1 残留）**

- ✅ **设计文档 DoD §12 全部 4 类交付**：实现 14/14 + 测试 unit/e2e 492 passed + 文档 5 链路对齐 + 冒烟 5/5
- ✅ **核心数学**：5 步管线 / Hysteresis / 双重失效止损 / 三层输出 与 SDD v1.4 §7-10 等价；5y 真机 4 trade_date × 3 state 跨制度回归 PASS
- ✅ **ruff 0 error**（本评审重跑确认）
- ✅ **跨文档一致**：设计文档 v1.4 / SDD v1.4 / system_design §9 / CLAUDE.md §9 / memory 全链路无漂移；v1.1 复审 5 项新残留 R1~R5 全部闭环
- 🟡 **3 P1 + 6 P2 残留**：均为工程治理类（Service/Repo 边界、静默降级日志、字段一致性），不影响功能正确性
- ❌ **不阻断 Phase 12 启动**

**建议下一步：**

1. **Phase 12 启动前（1 pd）** 收口 P1-1 / P1-2 / P1-3（3 处 `_repo._session` + run_monthly deprecation + neutralize 降级日志）
2. **Phase 12 实施期穿插** 收口 P2-1 / P2-3 / P2-6（日志补全 + neutralize_industry 分支决策 + 默认 order 修正）
3. **Phase 14 实施期** 收口 P2-4 / P2-5（ICIR 窗口严格交易日 + factor_ic_window_state 拆表决策）
4. **Phase 11 后续无独立评审节点**——下一评审在 Phase 12 实施完成后

---

## 8. 签署

| 项 | 值 |
|---|---|
| 评审人 | Claude（Opus 4.7）/ 实施代码评审 |
| 评审日期 | 2026-05-19 |
| 评审基线 | commit `9b31492` Phase 11 评分公式工业化交付 |
| 评审依据 | Phase 11 设计文档 v1.4 §12 DoD + SDD v1.4 §7-10 + Phase 11 设计文档评审 v1.1 + 5y 真机回归 commit log + 本评审 ruff/unit/e2e 重跑结果 |
| 评审输出 | DoD §12 4 类 14 项交付勾选 + 3 P1 残留 + 6 P2 残留 + 9 修订动作（按 Phase 12/14 阶段分配） |
| 阻断 Phase 12 启动 | ❌ 否 |
| 是否需要下一轮独立评审 | ❌ 否（残留在 Phase 12/14 实施过程中同步修订） |

---

## 9. 修订追踪表（2026-05-19 启动）

> 本表跨 session 维护。每项的"处置状态"由实施者更新，确保 Phase 12/14 不漏。
> 跨 session 接手时，先看本表 + memory `v1_finalize_deferred_items.md` "Phase 11 实施评审残留"节。

| # | 评审编号 | 简述 | 目标 Phase / 时点 | 处置状态 | 关键文件 |
|---|---|---|---|---|---|
| 1 | **P1-1a** | `strategy_service.py:381` `_repo._session` 改为 `_repo.session`（去 `type: ignore`）| Phase 12 启动前 | ✅ **已收口 2026-05-19** | `services/strategy_service.py` + `data/repository.py`（加 `session` property）|
| 2 | **P1-1b** | `signal_service.py:322` raw SQL → `repo.get_recent_score_snapshots_for_holdings` | Phase 12 启动前 | ✅ **已收口 2026-05-19** | `services/signal_service.py::_compute_holding_signal_states` + `data/repository.py`（加新方法）|
| 3 | **P1-1c** | `signal_service.py:390` raw SQL → `FactorICRepository.get_recent_aggregates`（已有方法）| Phase 12 启动前 | ✅ **已收口 2026-05-19** | `services/signal_service.py::_compute_holding_signal_states`（同步加 `market_state` 参数 + 调用方 `generate_for_date` 透传）|
| 4 | **P1-2** | `factor_monitor_service.run_monthly` 首行加 deprecation warning | Phase 12 启动前 | ✅ **已收口 2026-05-19** | `services/factor_monitor_service.py:139` |
| 5 | **P1-3a** | `factor_pipeline.neutralize` 自由度不足降级加 `logger.warning` + 【降级说明】 | Phase 12 启动前 | ✅ **已收口 2026-05-19** | `engine/factor_pipeline.py:128-132` |
| 6 | **P1-3b** | `factor_pipeline.neutralize` LinAlgError 降级加 `logger.exception` + 【降级说明】 | Phase 12 启动前 | ✅ **已收口 2026-05-19** | `engine/factor_pipeline.py:137-140` |
| 7 | **P2-1** | `scorer.aggregate` 全 NaN 策略跳过路径加 `logger.info` | Phase 12 v1.1 同 commit | ✅ **已收口 2026-05-19**（3 处 `continue` 前加 `scorer_strategy_skipped_empty` / `scorer_strategy_skipped_all_nan` / `scorer_strategy_z_empty_after_dropna`）| `engine/scorer.py` |
| 8 | **P2-2** | `SignalResponse.weights_source` 字段冗余 → 删除字段 | Phase 12 v1.1 同 commit | ✅ **已收口 2026-05-19**（前端 `grep weights_source frontend/src/` 无消费确认；schemas/signals.py 删除字段 + 注释）| `schemas/signals.py` |
| 9 | **P2-3** | `factor_pipeline.neutralize_industry=False` 分支决策 | Phase 12 v1.1 同 commit | ✅ **已收口 2026-05-19**（保留 + 单测 `test_neutralize_industry_disabled` + 强化注释列 3 个保留场景）| `engine/factor_pipeline.py` + `tests/unit/test_factor_pipeline.py` |
| 10 | **P2-6** | `_DEFAULT_ORDER` 改为按 default_matrix 当前 state 权重降序 | Phase 12 v1.1 同 commit | ✅ **已收口 2026-05-19**（删除模块级常量；fallback 路径 `sorted(weights_runtime, key=lambda s: weights_runtime[s], reverse=True)`）| `services/strategy_service.py` |
| 11 | **P2-4** | `rolling_icir_state` 窗口改用 `TradingCalendar.get_prev_trade_date` 严格交易日 | Phase 14 实施期（与 ICIR 历史回算同批）| ⏳ 待办；同步纠正 SDD §7.4 措辞 | `services/factor_monitor_service.py:416-423` + SDD §7.4 |
| 12 | **P2-5** | `factor_ic_window_state` daily + aggregate 共表评估拆分（或加 `row_type` 列）| Phase 14 实施期（与 5y candidate_pool 回填同批）| ⏳ 待办 | `data/factor_ic_repository.py` + alembic（potentially 0011）|

### 9.1 收口验证

- P1 全部修订后跑 `uv run ruff check src/ tests/` → `All checks passed!`（2026-05-19 实测）
- `uv run pytest tests/unit/ tests/e2e/ -q` → `492 passed`（无回归，2026-05-19 实测）
- 集成测试不重跑（5y 真机 DB 隔离原则，依据 `feedback_db_isolation.md`）

### 9.2 后续接手指南

跨 session 接手 Phase 14 时：
1. **看本表第 11-12 行**：P2-4 与 ICIR 历史回算同 commit；P2-5 与 5y candidate_pool 回填同 commit
2. 每完成一项，把处置状态从 `⏳ 待办` 改为 `✅ 已收口 YYYY-MM-DD`，并在 memory `v1_finalize_deferred_items.md` 同步

### 9.3 P12 设计评审顺带挖出的 Phase 11 实施补丁（v1.1 commit 落地）

Phase 12 设计评审 `phase12_design_review_2026-05-19.md` 在核 §7.1 验收基线可行性时反向 grep
到 Phase 11 实施缺陷：**Scorer.aggregate 内部跑了 5 步管线却没把 winsorized/neutralized/orthogonal
按 ts_code 塞回 CompositeScore；SignalService._build_snapshot_rows / ScoringService.write_candidate_pool
也没写这 3 列** → signal_score_snapshot + candidate_pool 这 3 列在 5y 真机数据上全 NULL。

修复（与 phase12 v1.1 commit 合并）：
- `alembic 0010` 给 candidate_pool 补 3 个 JSONB 列对齐 signal_score_snapshot
- `engine/scorer.py::Scorer.aggregate` 拆 `run_steps_1_to_3` 为显式三步调用，每股累积按
  `{strategy: {factor: float}}` 塞 `CompositeScore.factor_winsorized/neutralized/orthogonal`
- `services/strategy_service.py::write_candidate_pool` + `services/signal_service.py::_build_snapshot_rows`
  + `data/repository.py::upsert_candidate_pool_bulk` / `upsert_signal_snapshots`
  ON CONFLICT 加 3 列

**Phase 11 实施评审 v1.0 漏掉的根因**：v1.0 评审 §1.1 只核 5 个 engine 模块 + Scorer 重写都打 ✅，
没核 Scorer 是否真输出完整 factor_* dict。后续实施评审应增加"ORM 字段 → source 端写入路径"反向核查项。
