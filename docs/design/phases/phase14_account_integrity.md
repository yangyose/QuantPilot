# Phase 14：账户资金链 + 5y candidate_pool 回填 + ICIR 历史回算 + BacktestEngine 真 5 步 + 评审推迟项收口

> 版本：v1.0（2026-05-23 创建）
> 状态：设计完成，待评审 → TDD 实施
> 估算：~5-8 pd（V1.0 收尾批次第 4 个 phase）
> 依据文档：
> - SDD §7.4（ICIR 窗口定义）/ §7.7.1（回测引擎）/ §11（账户资金链 V1.0 必修项）
> - system_design §9 Phase 14 行（8 项 scope 锁定）
> - 评审报告：`docs/reviews/phase11_implementation_review_2026-05-19.md` §6.3 第 8/9 项
> - 评审报告：`docs/reviews/phase12_implementation_review_2026-05-20.md` §3 P1-2 + §4 P2-2/P2-3
> - 评审报告：`docs/reviews/phase13_implementation_review_2026-05-22.md` §8 P2 6 项

---

## 修订历史

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| v1.0 | 2026-05-23 | 初版交付 |

---

## 1. 概述

### 1.1 背景

Phase 14 是 V1.0 收尾批次（Phase 11~15）的第 4 个 phase，承担 3 类工作：

1. **业务必修缺口**：5y 真机验收 RM-13 deposit 不幂等 bug + Phase 11 ICIR rebalance Job 未在历史数据上跑通（weights_source 仍 default_matrix）
2. **回测路径补全**：BacktestEngine 走 `aggregate_legacy` + 派生 z 是 Phase 11 临时降级，本阶段切回真 5 步管线 + 配套 ICIR 校准最小集
3. **评审推迟项收口**：Phase 11 §6.3 / Phase 12 P1-2+P2 / Phase 13 P2 共 10 项推迟到本批次的项目

### 1.2 Scope 总览（system_design §9 Phase 14 行 8 子项）

| 子项编号 | 主题 | pd | 段落 |
|---------|------|-----|------|
| 14-1 | RM-13 deposit 幂等 | 0.5 | §3 |
| 14-2 | 5y candidate_pool 历史回填 + ICIR 历史回算 | 1.5-2 | §4 |
| 14-3 | BacktestEngine 真 5 步管线接入 | 1-1.5 | §5 |
| 14-4 | 回测引擎 §2.1 ICIR 校准最小集 | 1 | §6 |
| 14-5 | ICIR 窗口改严格交易日（含 Attribution 同源）| 0.3 | §7 |
| 14-6 | factor_ic_window_state 共表拆分评估 | 0.5 | §8 |
| 14-7 | Phase 13 评审 P2 6 项 | 0.8 | §9 |
| 14-8 | Phase 12 评审 P1-2 + AttrBackfill + 32767 注释 | 0.4 | §10 |

**合计 ~5-8 pd**。

### 1.3 启动核查（CLAUDE.md §5 + §11.1）

| 核查项 | 结论 |
|--------|------|
| 读 system_design §9 Phase 14 行 | ✓ 8 子项已锁定 2026-05-22（含 R12-* / R13-P2-* 前向引用） |
| 模块去向决定 | 全部 8 子项纳入本 phase，无推迟 |
| grep `R13-P2-\d+` 跨 system_design + roadmap + reviews/ | 6 项全部进入 §9.x 子节 ✓ |
| grep `R12-P[12]-\d+` 同上 | R12-P1-2 + R12-AttrBackfill + R12-32767 共 3 项全部进入 §10 ✓ |
| 孤儿模块检查（system_design §3/§5）| Phase 14 不新增模块，仅扩展既有 Service/Engine |
| 孤儿端点检查（§6）| Phase 14 不新增 REST API；含 1 个新内部 CLI 脚本 `scripts/backfill_attribution_history.py` |
| 跨 phase stub 标注 | RM-13 idempotency_key 列 alembic 0013 新增；旧记录回填默认值（详见 §3.3）|

**未在 Phase 14 收口的推迟项**：
- R13-P3 5 项（监控增强）→ v1_5_roadmap §4.5 V1.5-A ✓
- Phase 15 RC 验收项（5y 真机 + STRONG 相对百分比 + 覆盖率门槛 + 文档校核）→ Phase 15 ✓

---

## 2. 设计原则

- **不破坏生产数据**（CLAUDE.md §11 持久约束）：所有 alembic 迁移仅前向无破坏；5y 回填脚本默认 incremental 模式，需 `--force-clean` 显式启用全量
- **测试 DB 隔离**：integration 测试一律跑测试 DB（port 5433）；生产 DB 仅跑 alembic upgrade
- **批次原子性**：每个子项独立 commit，便于回滚；最后 §10 收尾合并文档
- **DoD 三层验证**：unit + integration + 真机（仅 RM-13 deposit 幂等 + 5y 回填 + ICIR 校准必跑真机）

---

## 3. §14-1：RM-13 deposit 幂等

### 3.1 问题

`POST /account/deposit`（Phase 6 实现）当前无幂等保护：客户端网络抖动重试 / 浏览器误双击 → 同笔入金被重复记录，account.cash 翻倍。
2026-05-12 真机验收 RM-13 报：用户 5 月初连续 3 次点击「录入入金 10 万」按钮，account.cash 显示 30 万。

### 3.2 实施路径

新增 alembic 0013 给 `cash_flow` 表加 `idempotency_key VARCHAR(64) NULLABLE` + 唯一索引 `(account_id, idempotency_key) WHERE idempotency_key IS NOT NULL`（partial unique 允许历史无 key 行）。

`POST /account/deposit` schema 加可选字段 `idempotency_key`：

```python
class DepositRequest(BaseModel):
    amount: Decimal
    occurred_at: date | None = None
    notes: str | None = None
    ts_code: str | None = None
    idempotency_key: str | None = Field(None, max_length=64)
```

`AccountService.deposit` / `record_dividend` 内部：

```python
if idempotency_key is not None:
    existing = await repo.find_cash_flow_by_idempotency(account_id, idempotency_key)
    if existing is not None:
        logger.info("deposit_idempotent_hit key=%s flow_id=%d", key, existing.id)
        return existing  # 直接返回原记录，account.cash 不变
```

前端 `OnboardingView.vue` / 仓位页"录入入金"按钮：提交前 `idempotency_key = crypto.randomUUID()`，按钮 disabled 期间复用同一 key（避免 React/Vue 重渲染产生新 key）。

### 3.3 兼容性

- 现存 cash_flow 行 `idempotency_key=NULL` → partial unique 索引允许多个 NULL
- 旧客户端不传 key → 走非幂等路径（兼容旧行为）
- 新前端永远传 key → V1.0 RC 后逐步要求强制

### 3.4 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-1-01 | unit | repo.find_cash_flow_by_idempotency 命中/未命中 |
| UT-P14-1-02 | unit | AccountService.deposit 同 key 重复 → 返回原 CashFlow，account.cash 不变 |
| INT-P14-1-01 | integration | 真 DB partial unique 约束（同 key 重复 INSERT raise） |
| E2E-P14-1-01 | e2e | POST /account/deposit 2 次同 idempotency_key → 200 + 同 flow_id |

---

## 4. §14-2：5y candidate_pool 历史回填 + ICIR 历史回算

### 4.1 问题

Phase 11 收尾时手动跑 `apply_monthly_rebalance(2026-04-30)` 仅写入 12 行（3 state × 4 strategy），`weights_source` 全 `default_matrix`，因为 ICIR 滚动累积需 ≥ 272 日候选池历史（`ic_window_days=252 + icir_lag_days=20`），而生产 DB 当时只有 2026-02 ~ 2026-05 的 78 天 candidate_pool 数据。

### 4.2 实施路径

**4.2.1 5y candidate_pool 回填脚本 `scripts/backfill_candidate_pool.py`**

输入：`--start 2021-01-01 --end 2026-05-22`（默认）
逻辑：
1. 调 `TradingCalendar.get_trade_dates(start, end)` 拿 ~1210 trade_date
2. 对每个 trade_date：
   - `MarketStateService.identify_state(trade_date)`（PIT，before_date=trade_date+1d）
   - `ScoringService.score_universe(trade_date, market_state)` → 写 candidate_pool
3. 进度上报：每 50 trade_date logger.info + 推 Redis pubsub `quantpilot:backfill:progress`（前端 BacktestRunView 风格的进度条可消费）
4. 断点续传：`get_existing_candidate_pool_dates()` 双表交集，仅补缺失日

**单日耗时**：生产 5y 数据上 5 步管线 ~130-250s/日；5y × 1210 trade_date ≈ 50-80 小时。建议跑分布式或周末整夜跑。脚本支持 `--resume` 从断点续传。

**4.2.2 ICIR 历史回算（直接复用 candidate_pool 回填）**

`apply_monthly_rebalance` 已经接入 `check_persistent_decay`（Phase 13 R13-P1-2）；只要 candidate_pool 5y 全量在库，对每个月末跑一次 `apply_monthly_rebalance` 就能写满 `factor_ic_window_state` + `strategy_weights_history` 5y 历史。

**4.2.3 月末批量脚本 `scripts/backfill_icir_rebalance.py`**

输入：`--start 2021-01 --end 2026-05`
逻辑：枚举所有月末交易日（~60 个），逐个跑 `FactorMonitorService.apply_monthly_rebalance(month_end)` + commit

**估算**：5y × 60 month_end × ~5s/次 ≈ 5 分钟（远低于 candidate_pool 回填）。

### 4.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-2-01 | unit | backfill_candidate_pool 跳过已存在日 |
| INT-P14-2-01 | integration | backfill_icir_rebalance 5 个 month_end → factor_ic_window_state 写入 60 行（3 state × 4 strategy × 5 月） |
| 真机-P14-2 | manual | 5y 全量回填后 `SELECT COUNT(*) FROM strategy_weights_history WHERE weights_source='icir'` ≥ 700（60 month_end × ~12 行 - 冷启动月） |

### 4.4 风险

- **5y 回填长时间运行**：建议 `nohup` 后台 + 进度推 Redis；脚本必须 idempotent（断点续传 + UPSERT）
- **ICIR 冷启动月**：前 ~13 个月（272 日窗口未满）`weights_source` 仍 `default_matrix`，第 14 个月起切 `icir`
- **生产 DB 写压力**：5y × 1210 trade_date 写 candidate_pool（~50 行/日）+ daily_quote 已有 → 总增 ~60k candidate_pool 行，PG `work_mem=8MB` 应足够

---

## 5. §14-3：BacktestEngine 真 5 步管线接入

### 5.1 问题

Phase 11 收尾时 `engine/backtest/engine.py::run` 仍走 `Scorer.aggregate_legacy()` + 后处理派生 `composite_z` / `composite_pct_in_market`，原因：单股 mock 场景被 5 步 Z-score 标准化打死（单股 std=0 → div by zero）。
不影响实盘 critical path（DailyPipeline CP2 已切真 5 步），但 V1.0 回测 IC 验证、滑点情景对比无法跑准确数。

### 5.2 实施路径

`engine/backtest/engine.py::run` 主循环每个 trade_date：

```python
# 旧（Phase 11 临时降级）
result = scorer.aggregate_legacy(snapshot, market_state)
composite_z = derive_z_from_score(result.composite_score)  # 派生

# 新（Phase 14）
result = scorer.score_universe(
    snapshot, market_state, scoring_config=cfg,
)  # 走真 5 步
composite_z = result.composite_z  # 直接读
composite_pct = result.composite_pct_in_market
```

**单股保护**：Scorer.score_universe 内部检测 `len(universe) < 30`（最小 winsorize 样本）→ 降级 `aggregate_legacy` + 派生（保留旧路径作 fallback）；并在 BacktestResult 写 `pipeline_mode: "real_5step" | "legacy_fallback"` 字段供前端展示。

### 5.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-3-01 | unit | BacktestEngine run universe ≥ 30 → 走真 5 步，composite_z 落 N(0,1) ±3.5σ |
| UT-P14-3-02 | unit | universe < 30 → 自动降级 legacy_fallback，BacktestResult.pipeline_mode 标注 |
| INT-P14-3-01 | integration | 真 DB 跑 30 trade_date × ~2400 universe 回测，composite_z 分布断言 |

---

## 6. §14-4：回测引擎 §2.1 ICIR 校准最小集

### 6.1 问题

SDD §2.1 要求"V1.0 必须在真历史数据上验证 IC 时序量级、多场景对比、滑点敏感性"；Phase 11 收尾仅做了 4 trade_date × 3 state 跨制度抽样，未做时序量级 + 多场景对比。

### 6.2 实施路径

**6.2.1 IC 时序量级验证脚本** `scripts/validate_ic_timeseries.py`

输入：`--strategy trend --factor macd_hist --state UPTREND --start 2021-01 --end 2026-05`
输出：CSV + matplotlib PNG（按月聚合 ic_mean + ic_std + sample_size），人工核对 ic_mean 量级是否落 (-0.1, 0.1) 合理区间。

**6.2.2 多场景对比**：4 策略 × 3 state × 60 月 = 720 行 panel，写 `scripts/compare_strategy_ic_panels.py` 输出 heatmap PNG。

**6.2.3 滑点敏感性最小验证**：BacktestEngine 配置 3 档滑点 `[0.0005, 0.002, 0.005]`（万 5 / 千 2 / 千 5），跑同一 5y 回测，断言 `sharpe(slippage=0.0005) > sharpe(slippage=0.005)`（基本单调性）。

### 6.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-4-01 | unit | validate_ic_timeseries 接受 ic_value 序列 → 输出 monthly aggregate |
| 真机-P14-4 | manual | 跑 5y 全量后 PNG 人工核对 |

---

## 7. §14-5：ICIR 窗口改严格交易日

### 7.1 问题

`services/factor_monitor_service.py::rolling_icir_state:416-423`：
```python
start = trade_date - timedelta(days=272)
end = trade_date - timedelta(days=20)
```
272 / 20 是日历日 → 实际窗口 ≈ 188 / 14 交易日，比 SDD §7.4 "252 + 20 交易日 = 272 交易日 / lag 20 交易日" 短 ~25%。

同源问题在 `services/attribution_service.py:79`：`timedelta(days=int(self._lookback_months * 30.5))`，Phase 12 评审 R12-P1-2 已标延后 Phase 14 同批。

### 7.2 实施路径

```python
# factor_monitor_service.py
end = self._calendar.get_prev_trade_date(trade_date, n=20)
start = self._calendar.get_prev_trade_date(end, n=252)  # 自 end 再回 252 交易日

# attribution_service.py
start = self._calendar.get_prev_trade_date(month_end, n=20 * self._lookback_months)
```

同步：SDD §7.4 措辞确认（v1.4 已写"交易日"，与本批一致，无需改）。

### 7.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-5-01 | unit | rolling_icir_state 用 mock calendar 断言 start = end - 252 交易日 |
| UT-P14-5-02 | unit | attribution_service.run_monthly 起始 = month_end - 20×lookback_months 交易日 |
| INT-P14-5-01 | integration | 跨周末 (252 trade days ≠ 252 calendar days) 校验 |

---

## 8. §14-6：factor_ic_window_state 共表拆分评估

### 8.1 问题

当前 `factor_ic_window_state` 表 daily 行（仅 ic_value/sample_size）+ aggregate 行（icir/CI/t_stat/half_life/...）共表 + 同 UNIQUE 约束 `(strategy, factor, state, trade_date)`，靠 `ic_value/icir IS NOT NULL` 区分行类型。5y × 250 trade_date × 4 strategy × 4 factor × 3 state ≈ 1.2M 行后表膨胀；索引不能加速 `WHERE icir IS NOT NULL` 谓词查询。

### 8.2 实施路径（2 选 1）

**方案 A（保守）**：加 `row_type VARCHAR(8) NOT NULL DEFAULT 'daily'` 列 + partial unique index `(strategy, factor, state, trade_date) WHERE row_type='aggregate'`。改动小，向后兼容。

**方案 B（重构）**：拆 `factor_ic_daily`（窄表：strategy/factor/state/trade_date/ic_value/sample_size）+ `factor_ic_window_state`（聚合表保留）。SQL 清晰，但需 alembic + Repository + Service 三层联动改动。

**建议**：本期实施评估，**方案 A 先上**（alembic 0014 加列 + partial index）；方案 B 留 V1.5+ DBA 视图。

### 8.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| INT-P14-6-01 | integration | 旧表数据 backfill `row_type` 推断（icir IS NOT NULL → 'aggregate'，否则 'daily'） |
| INT-P14-6-02 | integration | partial unique index 拒绝同 trade_date aggregate 重复，但允许 daily 共存 |

---

## 9. §14-7：Phase 13 实施评审 P2 6 项

依据：`docs/reviews/phase13_implementation_review_2026-05-22.md` §8 P2 行。

### 9.1 R13-P2-1：DataQualityRepository 改 instance + 取消 repo._session

**问题**：`services/data_service.py::_record_validation` 直接读 `repo._session` 私有属性 → 违反 CLAUDE.md §6 Phase 7 评审 C-02 规范。
**修复**：`DataQualityRepository` 改实例方法（持 session），`MarketDataRepository` 加 delegate 方法 `upsert_data_quality_metric(...)`。

### 9.2 R13-P2-2：ingest_daily 异常分支也写 metric

**问题**：daily_quote fetch 异常 → 不调 `_record_validation` → `/health/data` 看不到当日异常。
**修复**：try/except 块外层加 finally 写一行 `metric_key="exception_occurred", metric_value=1`（或 0 if 正常完成）。

### 9.3 R13-P2-3：apply_monthly_rebalance 持续告警抑制单月

**问题**：同月 (strategy, factor) 命中持续告警 + 单月告警 → 24h 内 `_is_duplicate` payload 区分 alert_type，两条都发，用户体感重复。
**修复**：`apply_monthly_rebalance` 主循环内调 `check_persistent_decay` 返回 True 时设标志位，跳过同月 `_maybe_alert` 调用。

### 9.4 R13-P2-4：API_REQUEST_DURATION 用 route template

**问题**：`endpoint=path`（raw URL）→ 每个 signal_id 独立 time series，Prometheus 基数爆炸。
**修复**：`request.scope.get("route").path` 拿模板（如 `/api/v1/signals/{signal_id}/lineage`）；fallback raw path 仅当 route 未匹配。

### 9.5 R13-P2-5：WS error 帧改 `{code, data, msg}` 格式

**问题**：`api/v1/pipeline.py::ws_pipeline_progress` 发 `{"error": "..."}` 与 REST `{code, data, msg}` 不一致。
**修复**：改 `{"code": 503, "data": None, "msg": "Redis 未初始化"}`；前端 PipelineProgressCard 同步适配（已用 `data.error` 兼容旧 schema，本批改 `data.msg + data.code === 503`）。

### 9.6 R13-P2-6：lifespan 加 redis.aclose()

**问题**：`main.py` yield 后只 `scheduler.shutdown`，未释放 Redis client；多 worker 启停可能连接泄漏。
**修复**：lifespan finally 段加 `await app.state.redis.aclose()` + try/except 包 best-effort。

### 9.7 测试

每项 1-2 UT + 必要 INT；总计 ~10 UT + 4 INT。

---

## 10. §14-8：Phase 12 实施评审 P1-2 + AttrBackfill + 32767 注释

依据：`docs/reviews/phase12_implementation_review_2026-05-20.md` §3 P1-2 / §4 P2-2/P2-3。

### 10.1 R12-P1-2：AttributionService 切严格交易日

**与 §7 同源**，在 §7 实施时一并改 `attribution_service.py:79`。

### 10.2 AttributionService 日级历史回填

**问题**：Phase 12 设计文档 §1.2 推迟项标注"AttributionService 日级回填脚本 → Phase 14 §14-2"；Phase 12 验收仅用合成 panel 4 行集成测试。
**实施**：新增 `scripts/backfill_attribution_history.py`：
- 输入：`--start 2021-01 --end 2026-05`
- 逻辑：枚举所有 month_end → 调 `AttributionService.run_monthly(month_end)` → 写 `attribution_history` 表
- 与 §14-2 ICIR 历史回算同批跑（依赖 candidate_pool 全量在库）

### 10.3 attribution_repository _BATCH_SIZE 注释

**问题**：Phase 12 评审 §4 P2-3 警告：Phase 12 单次 4 行远未达 asyncpg 32767 占位符限制，但 §14-2 5y × 60 month_end × 4 = 1200 行单次仍未超限；本批仅在 docstring 顶部加注释作未来防御。
**实施**：`data/attribution_repository.py` docstring 加："V1.0 单次 ≤ 1200 行未达 32767 限制；若扩 N month_end 使 N × 4 列总数 ≥ 8000 行，应在 upsert_attributions 内启用 `_BATCH_SIZE=500` 循环。"

### 10.4 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| INT-P14-8-01 | integration | backfill_attribution_history 跑 6 month_end → attribution_history 写入 24 行（6 × 4 因子） |
| UT-P14-8-01 | unit | run_monthly 严格交易日 mock（同 UT-P14-5-02 复用） |

---

## 11. 实施顺序与依赖

```
§14-1 RM-13 deposit 幂等            （独立，可先做）
       ↓
§14-7 Phase 13 P2 6 项              （独立，可并行）
       ↓
§14-8.1 R12-P1-2 严格交易日 ←─ §14-5 ICIR 窗口同批
       ↓
§14-6 共表拆分（方案 A） ←─ 不阻塞 §14-2，但建议先做
       ↓
§14-2 5y candidate_pool 回填 + ICIR 历史回算（耗时最长 50-80h）
       ↓
§14-3 BacktestEngine 真 5 步       （依赖 candidate_pool 全量）
       ↓
§14-8.2 AttrBackfill                （依赖 §14-2 candidate_pool + §14-5 严格交易日）
       ↓
§14-4 ICIR 校准最小集               （依赖 §14-2 + §14-3 全部到位）
```

**关键路径**：§14-1/§14-7 → §14-5/§14-8.1 → §14-6 → §14-2 → §14-3 → §14-8.2 → §14-4

---

## 12. DoD（Phase 14 收尾标准）

| 项 | 验收标准 |
|---|---------|
| 单元测试 | UT-P14-1-* / UT-P14-3-* / UT-P14-4-01 / UT-P14-5-* / UT-P14-8-01 全部 PASS |
| 集成测试 | INT-P14-1-01 / INT-P14-2-01 / INT-P14-3-01 / INT-P14-5-01 / INT-P14-6-01/02 / INT-P14-8-01 全部 PASS（测试 DB 5433）|
| E2E | E2E-P14-1-01（deposit 幂等）PASS |
| 真机验收 | 5y candidate_pool 全量在库（≥ 60k 行）+ `strategy_weights_history.weights_source='icir'` ≥ 700 行 + attribution_history ≥ 200 行 |
| ruff | 0 error |
| 冒烟 | 新增 API-102~104（若有新端点）+ Phase 13 冒烟仍 PASS |
| 文档同步 | system_design §9 Phase 14 行标 "完成 ✓"；CLAUDE.md §9；Phase 13 评审报告 §8 R13-P2-* 6 项勾选；Phase 12 评审报告 §3/§4 R12-P1-2/P2-2/P2-3 勾选 |
| 评审 | 本设计文档评审通过 + Phase 14 实施评审报告产出（建议 P0/P1 当批修） |

---

## 13. 未在 Phase 14 收口的相关项目

- **Phase 15 RC**：5y 真机端到端验收（评分 + 信号 + 因子 + 监控 + 账户）/ 30 日完整版跨制度回归 / STRONG 相对百分比化 / 覆盖率 ≥ 90% 门槛 / 文档校核 / V1.0 RC 标签
- **V1.5-A 监控增强**：R13-P3 5 项（API-101 Upgrade header / SecretFilter record.__dict__ / factor_monitor_params config_key / TushareAdapter 统一埋点 / Grafana 3 panel）
- **V1.5+ 其他主题**：见 `docs/design/v1_5_roadmap.md` §6
- **V2.0**：边际 VaR / 因子拥挤度 / 涨停板完整版

---

## 14. 风险与开放问题

| 编号 | 风险 | 缓解 |
|------|------|------|
| R14-OPEN-1 | 5y 回填 50-80h 长任务可能在中途网络/资源故障 | scripts 必须 idempotent + 进度持久化 + nohup 后台跑 |
| R14-OPEN-2 | §14-6 共表拆分方案 A vs B 选择 | 建议 A 先上（小改动），B 留 V1.5+ |
| R14-OPEN-3 | §14-3 真 5 步 universe < 30 降级判定阈值 30 是否合适 | 与 §14-2 candidate_pool 默认 50 上限对齐 |
| R14-OPEN-4 | RM-13 idempotency_key 前端 UUID 生成时机（disabled 按钮内复用 vs 新生成）| 按钮 mounted 时生成，提交后清空，下次按钮 disabled→enabled 重新生成 |
| R14-OPEN-5 | ICIR 历史回算 60 month_end × 5s 估算偏乐观（生产 PG 调用 + commit 可能 ~20s/次）| 实际跑前先 1 个 month_end 单次计时验证 |

---

> **依据 CLAUDE.md §11.1 三链必填**：本设计文档 §1.3 启动核查已 grep 跨 system_design + roadmap + reviews/ 三处确认 8 项 scope 完整；§13 显式列出未在本 phase 收口的项目及其归属。
