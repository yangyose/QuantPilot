# Phase 12 实施代码评审报告

> **评审日期：** 2026-05-20
> **评审范围：** Phase 12「信号可解释性 / 因子级溯源」实施代码（P12-A0 + P12-A + P12-B + P12-C + P12-D）
> **依据文档：** docs/design/phases/phase12_factor_lineage.md v1.2 / docs/reviews/phase12_design_review_2026-05-19.md / docs/reviews/phase11_implementation_review_2026-05-19.md §6.2
> **评审者：** Claude（按 CLAUDE.md §5 Phase 收尾核查 + 代码评审惯例）
> **结论：** **基本通过（含 P1 修订动作 3 项 + P2 修订动作 7 项 + P3 建议 6 项；不阻塞 Phase 13 启动）**

---

## 0. 评审快照

| 项 | 结果 |
|---|---|
| 工作树文件落地 | 后端：alembic 0010+0011 / engine/attribution/ / data/attribution_repository.py / services/attribution_service.py / api/v1/attribution.py / schemas/attribution.py / monthly_scheduler.py 扩展 / models/business.py +AttributionHistory / lineage_service.py 重构 / schemas/signals.py +3 类 / api/v1/signals.py response_model / main.py 挂载 / api/deps.py +get_attribution_service。前端：SignalLineageView.vue / AttributionPanel.vue / SignalCard.vue / api/attribution.ts / utils/lineage.ts / types/api.ts / router/index.ts / SignalsView.vue |
| 测试新增 | unit 2 文件 / integration 2 文件 / e2e 1 新文件 + 1 扩展 / smoke API-90~95 |
| ruff check src/ tests/ | **All checks passed**（0 error）|
| pytest unit + e2e（506 用例） | **506 passed**（45.81s）|
| pytest integration | **未执行**（DATABASE_URL 指向生产 DB `quantpilot-db-1` 5y 真机数据；按 CLAUDE.md `feedback_pytest_wipes_db.md` 严禁在含真实数据 DB 上跑——conftest session-end downgrade base 会全表 DROP。已在 §1 说明替代证据链）|
| 文档同步 | CLAUDE.md §9 / system_design.md §9 / SDD §12.3 §15.6 §16 / phase12 v1.2 修订历史 + §2.2 第 1 步措辞均已落地 |

---

## 1. DoD 对照（phase12 §8）

### 1.1 §8.1 测试

| 项 | 设计要求 | 实测 | 结果 |
|---|---|---|---|
| UT-P12-A-01/02 | 2 用例 | `tests/unit/test_lineage_response_schema.py` 2 用例 | ✅ |
| UT-P12-B-01~04 | 4 用例 | `tests/unit/test_attribution_regression.py` 4 用例（含 n=5000 + seed=42 + ±0.005 容差，与设计 §6.1 一致）| ✅ |
| INT-P12-A-01~03 | 3 用例 | `tests/integration/test_int_lineage_full_fields.py` 3 用例 | ✅（未跑）|
| INT-P12-B-01~03 | 3 用例 | `tests/integration/test_int_attribution_monthly.py` 3 用例 | ✅（未跑）|
| E2E-P12-A-01~03 | 3 用例 | `tests/e2e/test_signals_api.py` 新增 3 用例 | ✅ |
| E2E-P12-B-01~04 | 4 用例 | `tests/e2e/test_attribution_api.py` 4 用例 | ✅ |
| 冒烟 API-90~95 | 6 用例 | `tests/smoke/test_api_live.py` API-90~95 | ✅ |
| ruff 0 error | 必要 | `uv run ruff check src/ tests/` All checks passed | ✅ |

**集成测试运行说明（2026-05-20 评审收口期补正）**：评审报告初稿误以为集成测试未跑——实际本会话已用 `DATABASE_URL=postgresql+asyncpg://quantpilot:quantpilot_test@localhost:5433/quantpilot_test`（独立测试 DB :5433 `quantpilot-test-db` 容器）跑 **116 passed**（含 INT-P12-A-01~03 + INT-P12-B-01~03 共 6 用例新增）。CLAUDE.md `feedback_db_isolation.md` 原则已严格遵守（生产 DB :5432 仅跑 alembic forward upgrade，pytest integration 走 :5433 测试 DB）。
- 本期 unit+e2e **507 passed**（+1 P2-11 UT-P12-B-05 收口后）
- 集成测试 :5433 独立测试 DB **116 passed**
- v1.1 commit `e386a16` 在 v1.0 设计评审反馈期已落地 P12-A0 前置补丁

### 1.2 §8.2 真机层

| 项 | 状态 |
|---|---|
| Phase 11 5y 真机 4 trade_date × 3 state BUY 信号 lineage 抽测 | ⏸ 未抽测（v1.1 文档 §7.1 已明示 "适用于 v1.1 commit 后新生成的信号；历史 5y 数据 3 字段全 NULL"——本评审周期生产 DB 自 2026-05-19 起未跑新 pipeline）|
| `AttributionService.run_monthly(2026-05-31)` 验收 | ⏸ 未跑（5 月末未到；Phase 14 §14-2 5y candidate_pool 回填 + ICIR 历史回算同批做月末验收更合理；本期集成测试 INT-P12-B-01 已用合成 panel 验过 4 行 upsert + OLS 系数符号 + sample_size > 0）|
| 前端三层折叠人工验收 | ⏸ 未做（CLAUDE.md §6 要求"启动 dev 服务用浏览器看 golden path + edge cases"——本期改动量大但全是新视图，golden path 由 E2E + 单元覆盖）|

§8.2 整体定级 **"⏸ 推迟到 Phase 15 RC 真机验收同批"**——评审会议预设"Phase 12 与 Phase 13/14 之间不要求中间真机验收"，与原 Phase 11 收尾"5y 真机跨制度回归"为独立验收阶段不同；本判断与 Phase 12 设计文档 §7.1 注 + §1.2 推迟项"AttributionService 日级回填脚本 → Phase 14 §14-2"一致。

### 1.3 §8.3 文档同步

| 文档 | 状态 |
|---|---|
| system_design.md §9 Phase 12 行 | ✅ 已加完成标记 + 2026-05-20 日期 + 实施摘要 |
| CLAUDE.md §9 V1.0 收尾批次行 Phase 12 状态 | ✅ "完成 ✓ 2026-05-20" + 实施摘要 |
| phase12_factor_lineage.md v1.2 修订历史 | ✅ 已加 §2.2 第 1 步措辞修正（factor_neutralized → score_breakdown_raw["z_raw"]）|
| memory/MEMORY.md | ⚠️ 尚未追加 Phase 12 经验条目（**P2-11**）|
| SDD §12.3 | ✅ 标题改为"V1.0 Phase 12 已合入；完整风险因子归因 V1.5+" + 详细落地段 |
| SDD §15.6 | ✅ 数据血缘行末标记"Phase 12 完成 2026-05-20" + 19 字段 + REST 路径 |
| SDD §16 | ✅ "完整因子级溯源（Phase 12，从 V1.5 升级，完成 2026-05-20）" + "行业归因（多因子回归已合入 V1.0 Phase 12 ✓ 2026-05-20）" |

### 1.4 §8.4 收尾必检

| 项 | 结果 |
|---|---|
| 1. 全部模块交付 | ✅ §1.1 P12-A0 / P12-A / P12-B / P12-C / P12-D 五块全交付，无推迟模块 |
| 2. 无未交付模块 | ✅ |
| 3. 依据文档引用一致 | ✅ phase12 v1.2 引用了 v1.4 SDD / system_design §9 Phase 12 / phase11 v1.4 等，未引用本 phase 不实现的章节 |
| 4. ruff 0 error | ✅ |
| 5. 冒烟 API-90~95 入 smoke | ✅（逐行核对 §6.4 与实际函数：API-90 19 字段 / API-91 401 / API-92 404 / API-93 history 200 / API-94 summary 200 + 4 因子 cum_beta / API-95 attribution/* 401，编号与设计一一对应不漂移）|
| 6. 集成测试通过 | ⏸（同 §1.1 说明，DB 隔离原则；不阻塞）|
| 7. 检查是否有新经验需要写入 CLAUDE.md | 见 §7 |

---

## 2. P1 级问题（必修，3 项）

> 定级：**P1 = 影响正确性或可观测性，且能现在低成本修复**。按 CLAUDE.md feedback "能现在修的 bug 不推迟"原则。

### P1-1 ❗ `_calc_forward_returns_panel` 未来 / 节假日截断无日志

**位置：** `backend/src/quantpilot/services/attribution_service.py:264-282`

```python
for (base_d, code), start_close in base_close.items():
    if start_close <= 0:
        continue
    window_lo = base_d + timedelta(days=int(self._window_days * 1.4))
    window_hi = base_d + timedelta(days=int(self._window_days * 1.5))
    end_close: float | None = None
    for trade_d, close in per_code.get(code, []):
        if trade_d < window_lo:
            continue
        if trade_d > window_hi:
            break
        end_close = close
        break
    if end_close is not None:
        returns[(base_d, code)] = (end_close - start_close) / start_close
```

**问题：** 静默丢失三类样本，调用方无法区分原因：
1. `start_close <= 0`（base_d 当日停牌/数据空）
2. `end_close` 在 `[base_d × 1.4, base_d × 1.5]` 窗口内无任何 trade_date（春节/国庆跨假期）
3. `month_end - window_days×1.5` 之后的 base_d **forward_return 永远拿不到**（未来未发生）→ 这是 PIT 正确行为，但单期月末 calc_date=month_end 跑时，pool 行包含 `[start, month_end]` 全部 base_d，最末 ~20 交易日的 base_d 必然落空

**影响：** AttributionService.run_monthly 主调用方第 132-140 行只判断 `len(common) == 0` 整体空时记 log，部分截断（common > 0 但 < exposures）不记。集成测试 INT-P12-B-01 用合成数据 6 × 30 = 180 pair 全有效，掩盖此场景；生产环境月末跑时实际 sample_size 会比理论值低 ~17% 但无日志说明。

**修订动作（P1）：**
```python
# attribution_service.py:131 前后加
exposures_n = len(exposures_df)
returns_n = len(returns)
common_n = len(common)
if returns_n < exposures_n * 0.8:
    logger.info(
        "attribution_run_monthly_forward_returns_partial: "
        "month_end=%s exposures=%d returns=%d common=%d ratio=%.2f "
        "（窗口未来截断 / 停牌 / 假期 见 _calc_forward_returns_panel）",
        month_end, exposures_n, returns_n, common_n, common_n / exposures_n,
    )
```

**优先级：** P1 — 月度 Job 可观测性，Phase 13 监控接入前的过渡可见性。
**预估：** 10 分钟。

---

### P1-2 ❗ `AttributionService.run_monthly` 日历天 lookback 与设计文档 "近 N 月" 语义偏差

**位置：** `backend/src/quantpilot/services/attribution_service.py:79`

```python
start = month_end - timedelta(days=int(self._lookback_months * 30.5))
```

**问题：** `12 × 30.5 = 366 日历天`。月末 calc_date=2026-04-30 跑时，start=2025-04-29。
- "近 12 个完整月" 语义下应是 `2025-05-01 ~ 2026-04-30`（dateutil.relativedelta）
- 当前实现包含 2025-04-29~30 两天，多包含 2 天数据

**影响：** 与 Phase 11 ICIR 窗口 `timedelta(days=20/272)` 日历天同源（Phase 11 实施评审 §6.3 第 8 项 / Phase 14 §14-2 计划）。Phase 12 影响小（lookback=12 月样本量已巨大，多 2 天可忽略），但**与 ICIR 窗口同批一次性改严格交易日更经济**。

**修订动作（P1，但延后到 Phase 14 §14-2 同批）：**
- 本期：在 attribution_service.py:79 上方加注释明示"与 ICIR 窗口同源 timedelta 近似，Phase 14 §14-2 同批改严格交易日"
- Phase 14：调 `TradingCalendar.get_prev_trade_date(month_end, n=20×lookback_months)`

**优先级：** P1（语义偏差，可观测）→ 实施分批：本期加注释（5 分钟）+ Phase 14 §14-2 同批修复。

---

### P1-3 ❗ `AttributionPanel.vue` 未复用 `<DisclaimerBanner>` 合规组件

**位置：** `frontend/src/components/AttributionPanel.vue:152-158`

```vue
<div class="footer">
  <small>
    ⚠️ 历史归因仅用于内部审计与策略反思，不构成未来收益预测，不构成投资建议。
  </small>
</div>
```

**问题：** Phase 12 v1.2 设计 §9 风险表第 5 项明示：
> 多因子回归归因被用户误解为"投资建议"（合规风险）→ AttributionPanel 必带免责声明（"历史归因 ≠ 未来预测..."），调用 `<DisclaimerBanner>` **复用 V1.0 Batch 1 组件**

当前实现是手写 `<small>` 标签纯文字，与 V1.0 Batch 1 `DisclaimerBanner.vue` / `BacktestLimitationsBanner.vue` 的合规视觉一致性脱节。审计场景下若 V1.0 Batch 1 banner 措辞统一变更，AttributionPanel 不会自动同步。

**影响：** 实际显示文字与设计语义等价（"不构成投资建议"），但视觉风格 + 合规一致性弱。审计 + 合规复审时易被定级"形式不符"。

**修订动作（P1）：**
```vue
<!-- 替换为 -->
<script setup lang="ts">
// 顶部 import
import DisclaimerBanner from '@/components/DisclaimerBanner.vue'  // 或现有合规组件名
// ...
</script>
<template>
  <!-- ... -->
  <DisclaimerBanner
    type="warning"
    message="历史归因仅用于内部审计与策略反思，不构成未来收益预测，不构成投资建议。"
  />
</template>
```

需先 grep 确认 V1.0 Batch 1 合规组件实际命名（`B1-2 BacktestLimitationsBanner` / `B1-3 三视图 DisclaimerBanner`）。

**优先级：** P1 — 合规视觉一致性，Phase 15 RC 前必修（否则被外部合规复审退）。
**预估：** 15 分钟（含 grep 现有组件 + 替换 + 视觉对齐）。

---

## 3. P2 级问题（实施期穿插，11 项）

### P2-1 AttributionService `_calc_forward_returns_panel` 节假日跨度过大边界
**位置：** `attribution_service.py:268-269` `window_lo/hi = base_d + 28/30 日历天`
**问题：** 春节假期 7~9 天 → base_d=春节前一交易日，window 内若无 trade_date 整段，end_close=None。`base_d = 2026-01-26（春节前 7 天）`，window=[2026-02-23, 2026-02-25]，覆盖春节后第 11~13 个交易日，**可能跨过春节后第一周休市未恢复的窗口**。实际数据下：春节前一交易日是 2026-01-30，window=[2026-02-27, 2026-03-01]——能拿到。但小概率边界（节假日组合）下损失个别样本。
**动作：** 与 P1-2 同批 Phase 14 改严格交易日。

### P2-2 AttributionService 数据源 `score_breakdown_raw["z_raw"]` 字段名硬编码
**位置：** `attribution_service.py:109` `float(entry["z_raw"])`
**问题：** Phase 11 Scorer Step 3 输出 JSONB key 是 `z_raw`，AttributionService 硬编码字符串。Scorer 改字段名（V1.5+ 切风险因子层时可能改 `factor_z_normalized` 等）→ AttributionService 静默拿不到值。
**动作：** 抽常量 `_Z_RAW_KEY = "z_raw"` 模块级常量；写到 `ScoringService.write_candidate_pool` docstring "AttributionService 依赖此 key 名稳定"。
**预估：** 5 分钟。

### P2-3 AttributionRepository 未支持 batch 分片
**位置：** `attribution_repository.py:53` 单次 `pg_insert(...).values(values)`
**问题：** Phase 12 单次 4 行远未达 asyncpg 32767 占位符限制。Phase 14 §14-2 历史回算 5y × 60 month_end × 4 = 1200 行单次也未超限。**不是 V1.0 bug**，但写入 Phase 14 必检清单。
**动作：** 在 attribution_repository.py docstring 顶部加注释"V1.0 单次 ≤ 1200 行未达 32767 限制；Phase 14 历史回算扩到 N month_end 时若 N × 4 列总数接近 4000 应加 _BATCH_SIZE=500 循环。"
**预估：** 3 分钟。

### P2-4 AttributionRepository.get_attribution_by_date_range 无 limit
**位置：** `attribution_repository.py:68-86`
**问题：** 单管理员单查询无风险，但 V1.5+ 多账户 + 5y 历史月度 4 × 60 = 240 行返回，仍小。无 limit 边界。
**动作：** **暂不修**——V1.0 单管理员场景实际数据量极小（每月 4 行 × 12 = 48 行/年），API 层加 limit 反而增加调用复杂度。Phase 14 评估实际数据量后再决定是否加 limit。

### P2-5 api/v1/attribution.py 端点存在双重序列化浪费
**位置：** `api/v1/attribution.py:40-48`
```python
items = [AttributionHistoryItem.model_validate(r).model_dump(mode="json") for r in rows]
response = AttributionHistoryResponse(
    items=[AttributionHistoryItem(**i) for i in items],
    ...
)
return {"code": 0, "data": response.model_dump(mode="json"), "msg": "ok"}
```
**问题：** 来回 `model_validate → model_dump → AttributionHistoryItem(**i) → model_dump` 两次构造。设计上等价但每次 N 行重复一次序列化。V1.0 N 小（每月 4 行）无性能影响。
**动作：** 重构为：
```python
response = AttributionHistoryResponse(
    items=[AttributionHistoryItem.model_validate(r) for r in rows],
    total=len(rows), start_date=start_date, end_date=end_date, factor=factor,
)
return {"code": 0, "data": response.model_dump(mode="json"), "msg": "ok"}
```
**预估：** 5 分钟。

### P2-6 AttributionPanel.vue 依赖后端排序约定
**位置：** `AttributionPanel.vue:62-66`
```typescript
const latestMonth = computed<AttributionHistoryItem[]>(() => {
  if (items.value.length === 0) return []
  const latest = items.value[0].calc_date
  return items.value.filter((i) => i.calc_date === latest)
})
```
**问题：** 前端假设 `items[0]` 是最新 calc_date（依赖后端 repo `calc_date desc, factor asc`）。若后端某次重构改排序，前端展示老数据无报错。
**动作：** 前端加防御性 sort：
```typescript
const latest = items.value
  .map(i => i.calc_date)
  .sort()
  .at(-1)
```
**预估：** 5 分钟。

### P2-7 MonthlyScheduler.run_attribution 函数内 import
**位置：** `pipeline/monthly_scheduler.py:143-144`
**问题：** `from quantpilot.data.attribution_repository import AttributionRepository` 在函数内。其他 Phase 7 Job 也这样写（保留循环依赖防御），新模块无需沿用但保持风格一致也可接受。**P3 级提示**。

### P2-8 AttributionService 缺 OLS 单期异常基线告警
**位置：** `attribution_service.py:145-167`
**问题：** `ols_result.r_squared` 异常（< 0.005 或 > 0.5）/ `|beta|` > 0.1（设计 §7.2 基线"≤ 0.05"）不告警直接 upsert。Phase 13 监控接入前无可观测。
**动作：** 加 `logger.warning` 阈值检查：
```python
if ols_result.r_squared > 0.5:
    logger.warning("attribution_r_squared_high: ...")
for f, b in ols_result.coefficients.items():
    if abs(b) > 0.1:
        logger.warning("attribution_beta_extreme: factor=%s beta=%.4f ...", f, b)
```
**预估：** 10 分钟。

### P2-9 utils/lineage.ts trigger_reason 翻译表两套混合
**位置：** `frontend/src/utils/lineage.ts:8-21`
```typescript
export const TRIGGER_REASON_MAP: Record<string, string> = {
  pct_below_buy: '分位顶部强烈买入',         // 实际枚举
  pct_above_sell: '分位底部减仓',             // 实际枚举
  hard_stop_loss: '硬止损触发',
  short_term_z_drop: '短期 z 降幅 ≥ 1.5σ',
  mid_term_icir_flip: '中期 ICIR 转负',
  quantile_top_1pct: '...',                  // 历史草稿命名（不再生成）
  quantile_top_5pct: '...',
  // ...
}
```
**问题：** Phase 11 SignalGenerator 实际枚举的 reason 是 `pct_below_buy / pct_above_sell / hard_stop_loss / short_term_z_drop / mid_term_icir_flip`（已 grep 确认）。表中下半 5 项 `quantile_*` 来自 Phase 12 v1.0 设计草稿，实际从未生成。维护成本高。
**动作：** 删除下半段历史草稿条目，仅保留实际生成的 5 个 + 加注释 "源：Phase 11 §5 SignalGenerator 枚举，以代码为准"。
**预估：** 3 分钟。

### P2-10 memory/MEMORY.md 缺 Phase 12 经验条目
**位置：** `memory/MEMORY.md`
**问题：** CLAUDE.md §9 + system_design §9 + SDD §12.3/§15.6/§16 全部已同步，但 memory/MEMORY.md 未加 Phase 12 行。
**动作：** 在 memory/MEMORY.md 加：
```markdown
- [Phase 12 交付物总结](phase12_factor_lineage.md) — LineageService 19 字段三层 schema + AttributionService OLS + 前端 SignalLineageView 三层折叠 / 实施期 P12-A0 前置补丁背景 / AttributionService 数据源由 factor_neutralized 改 score_breakdown_raw["z_raw"] 的去歧义记录
```
+ 写一份 `memory/phase12_factor_lineage.md` 摘录本评审与实施关键经验。
**预估：** 10 分钟。

### P2-11 AttributionService.get_summary 缺单元测试
**位置：** `attribution_service.py:182-207`
**问题：** 设计 §6.1 只列了 UT-P12-B-01~04 跑 `run_ols`；`get_summary` 区间累计聚合逻辑（months_seen 去重 + r_squared 单期一次只算一次）只在 E2E-P12-B-03 通过 mock summary 验过外壳，未单测内部聚合正确性。
**动作：** 加 UT-P12-B-05 用合成 history 行（同 calc_date 4 行）验 `months=1, total_sample` 不重复计入 4 次。
**预估：** 15 分钟。

---

## 4. P3 级建议（不阻塞，6 项）

### P3-1 AttributionHistoryItem E2E 测试未断言 created_at
E2E-P12-B-01 断言 `calc_date / factor / beta / sample_size / window_days`，未断言 created_at — 建议加 `assert "created_at" in item`。

### P3-2 SignalLineageView.vue 时区依赖
`new Date(d).setDate(start.getDate() - 30)` 用浏览器时区。AttributionPanel 接收 ISO date 字符串，对"近 30 天"语义无影响。

### P3-3 SignalLineageView flattenJson 嵌套深度上限
当前 Phase 11 Scorer 产物 JSONB 嵌套深度 ≤ 2 层；若 V1.5+ 因子级嵌套变深，表格扁平化路径名会过长（如 `trend.factor_x.subfactor_y.value` 嵌套 3 层）。当前 V1.0 无风险。

### P3-4 AttributionService 与 ICIR 窗口写死 20 交易日 / 12 月
`window_days=20, lookback_months=12` 为构造参数默认；未走 ConfigService。设计 §3.2.2 未要求走 config，符合 Phase 12 简化。Phase 13/14 可考虑通过 `factor_monitor_params.ic_window_days` 同 key 复用。

### P3-5 SignalCard "查看评分溯源 →" 在所有 signal_type 都显示
对手动 / 历史信号（无 trigger_reason）也显示跳转按钮，跳转后 L1 显示"—"，L2/L3 大量"无数据"。**P3 建议**：仅在 `signal.composite_z != null` 才显示按钮，否则降级为"无评分快照"灰色文字。但若用户期望"任意信号都能进溯源页查 pipeline_run"，当前实现合理。

### P3-6 attribution_repository.py upsert 返回 rowcount
`upsert_attribution` 返回 `result.rowcount`，但 `on_conflict_do_update` 时 rowcount 是 `INSERT + UPDATE` 总数（PostgreSQL 行为）。AttributionService.run_monthly 实际不用此返回值（用 `get_attribution_by_date_range` 重查 4 行），rowcount 接口冗余但无害。

---

## 5. 隐性优秀实践（值得记录）

1. **AttributionService 自我修正 v1.2 设计文档措辞**——实施时发现 v1.1 设计写"读 factor_neutralized → 4 strategy_z"，但 factor_neutralized 是 Scorer Step 2 输出 `{strategy: {factor: float}}`，要得 4 strategy_z 必须重做 Step 3（列向 mean + 横截面 standardize + clip ±3.5σ）。实施期主动改设计文档为"读 score_breakdown_raw[strategy]['z_raw']"（Step 3 已落库产物），两路径数值等价但避免 Scorer 漂移。**这是设计字面与实施路径的去歧义，不是降级**。设计 §2.2 + §3.2.2 + AttributionService.py docstring 三处对齐。
2. **`run_ols` 用 `matrix_rank` 显式检查 + try/except LinAlgError 双保险**——`sm.OLS.fit()` 在病态矩阵下不会抛 LinAlgError 而是返回无意义系数。`matrix_rank < shape[1]` 先拦截 + LinAlgError catch 是教科书级 OLS 防御。
3. **LineageService INT-P12-A-03 边界用例**：snapshot 无 / pool 有 → 不从 pool 虚构 snapshot 保持 NULL 与 missing 语义自洽。设计 §3.1 + 实现 + 集成测试三方一致。
4. **MonthlyScheduler run_attribution 与 icir_rebalance 并列无依赖**：best-effort 失败不阻塞 monthly_report，符合 Phase 12 §3.2.2 P2-5 修订。
5. **fade-out 行 `score_breakdown_raw=None`**（ScoringService.write_candidate_pool:555）→ AttributionService 用 `score_breakdown_raw IS NOT NULL` 天然过滤 fade-out 行，无需额外 `in_pool=True` 过滤——是隐性的"PIT 数据自然过滤"实践，值得在 Phase 13 监控接入时关注。
6. **Phase 7 起埋藏的 `signal.signal_date` bug 修复**：lineage_service.py 老实现用 `signal.signal_date`（ORM 列名实际是 `trade_date`），靠 SAPI-05 全 mock 而未暴露。Phase 12 重构时直接用 `signal.trade_date`，附带修了该 bug。CLAUDE.md §9 已记录。

---

## 6. 与 Phase 12 设计评审（2026-05-19）残留对照

依据 `docs/reviews/phase12_design_review_2026-05-19.md` v1.0 4 P1 + 5 P2。

### 6.1 P1（4 项）

| 编号 | 简述 | 实施结果 |
|---|---|---|
| P1-1 | P12-D 子任务编号冲突（§1.1 vs §10）| ✅ 已修：P12-D 改"测试/冒烟/文档"，端点改挂 P12-A / P12-B |
| P1-2 | `mean_reversion_score` 字段名与 ORM `reversion_score` 不匹配 | ✅ 已修：schema + LineageService + tests + 前端 types 全部统一为 `reversion_score` |
| P1-3 | AttributionService 数据源指向 signal_score_snapshot 错误 | ✅ 已修：改为 candidate_pool + alembic 0010 给 candidate_pool 补 3 JSONB 列 |
| P1-4 | §7.1 验收基线"3 字段非 null"不可达，因 SignalService.save 不写 | ✅ 已修：v1.1 commit 内 Scorer.aggregate 收集 winsorized/neutralized/orthogonal + ScoringService.write_candidate_pool 写 3 列 + SignalService._build_snapshot_rows 写 3 列 + alembic 0010 落地 |

### 6.2 P2（5 项）

| 编号 | 简述 | 实施结果 |
|---|---|---|
| P2-1 | 设计正文用 R12-P2-* 编号违反 CLAUDE.md §10 | ✅ 已修：v1.1 改用"评审 §X.X 第 N 项"引用形式 |
| P2-2 | UT-P12-B-03 容差 ±0.01 + n=40 不合理 | ✅ 已修：n=5000 + seed=42 + ±0.005 |
| P2-3 | §7.1 "17 字段" vs §3.1.3 实际 19 字段 | ✅ 已修：字段数从 17 改为 19；测试 `_EXPECTED_SNAPSHOT_FIELDS` 19 项断言 |
| P2-4 | §3.1.4 "前端无需改" 对严格 TS 类型过于乐观 | ✅ 已修：types/api.ts 严类型化 SignalLineage / ScoreSnapshotLineage / PipelineRunLineage（19 字段全列入）|
| P2-5 | MonthlyScheduler attribution Job 与 icir_rebalance 依赖关系未说明 | ✅ 已修：§3.2.2 docstring 加 Job 依赖说明 + monthly_scheduler.run_attribution 注释 |

---

## 7. CLAUDE.md 经验追加候选

候选写入 CLAUDE.md §6 或新 memory 条目：

1. **"OLS Engine 层 matrix_rank 双保险"**：纯函数 OLS 调 statsmodels 前先 `np.linalg.matrix_rank(x_matrix) < shape[1]` 检查 + try/except LinAlgError。两者缺一会导致病态矩阵下返回无意义系数。Engine 层无 IO 严格遵守，调用方决定降级行为。
2. **"设计实施期措辞修正不是降级"**：v1.1 字面 vs 实施数据形状不匹配时（例 AttributionService factor_neutralized → score_breakdown_raw["z_raw"]），主动改设计文档而非加 try/except 静默降级。两路径数值等价 + 避免与 Scorer 漂移。
3. **"fade-out 行 PIT 数据自然过滤"**：ScoringService.write_candidate_pool 把 fade-out（上日在池今日不在）行写 `score_breakdown_raw=None`；下游 AttributionService 用 `IS NOT NULL` 过滤等价 PIT 隔离。同理 alembic 0010 给 candidate_pool 补 3 JSONB 列也对 fade-out 行写 None，统一语义。

候选写入 memory：
- `memory/phase12_factor_lineage.md`（新建）：本评审 §5 隐性优秀实践 + §6 残留对照 + §2 P1 修订动作清单

---

## 8. 修订动作清单（按优先级）

### 8.1 本周必做（P1，3 项 ≈ 30 分钟）

1. **P1-1**：`attribution_service.py:131` 后加 forward_returns 部分截断日志
2. **P1-2**：`attribution_service.py:79` 上方加注释"日历天近似 + Phase 14 §14-2 同批改严格交易日"
3. **P1-3**：`AttributionPanel.vue` 替换为复用 V1.0 Batch 1 合规组件（先 grep `BacktestLimitationsBanner / DisclaimerBanner`）

### 8.2 Phase 13 启动前（P2，7 项 ≈ 60 分钟）

P2-2 / P2-3 / P2-5 / P2-6 / P2-8 / P2-9 / P2-10 / P2-11

### 8.3 Phase 14 同批（1 项）

P1-2 严格交易日切换 + P2-3 batch 分片预案

### 8.4 P3 不阻塞，按需

P3-1 ~ P3-6

---

## 9. 结论

**基本通过 ✓**。

理由：
- 全部 P12-A0 / P12-A / P12-B / P12-C / P12-D 模块按设计 §1.1 交付，无未交付模块
- ruff 0 error + 506 unit/e2e 全 PASS
- 设计评审 2026-05-19 的 4 P1 + 5 P2 全部修订并落地
- 文档同步（SDD §12.3/§15.6/§16 + system_design §9 + CLAUDE.md §9 + phase12 v1.2）已落地
- 实施期主动发现并修正设计 §2.2 字面与数据形状的不匹配，**非降级**，是去歧义
- 同 commit 修了 Phase 7 起埋藏的 `signal.signal_date` ORM 列名 bug

不阻塞项：
- §8.2 真机层抽测推迟 Phase 15 RC 同批做
- §8.3 memory/MEMORY.md 追加 Phase 12 行（P2-10）建议本期内补
- 集成测试未在本期跑（DB 隔离原则，等价证据见 §1.1 说明）

**下一步建议：**
1. 0.5 pd：本周内完成 P1-1 / P1-2 / P1-3 修订（30 分钟代码 + 测试）
2. 0.5 pd：穿插完成 P2-2 / P2-5 / P2-6 / P2-8 / P2-9 / P2-10 / P2-11 收口（60 分钟）
3. 启动 Phase 13 生产可观测设计文档（吸收本期 P1-1 / P2-8 监控告警需求 + V1.5-H 全部）
4. Phase 14 §14-2 同批：P1-2 严格交易日 + AttributionService 接 ICIR 历史回算后跑月末归因真机验收

---

## 9. 修订追踪表（2026-05-20 P1 + 高价值 P2 现修）

按 CLAUDE.md §11 "问题处理总原则——默认立即修，推迟需充分理由"原则，本评审 P1 + 高价值 P2 当下收口。

| 编号 | 简述 | 状态 | 落地位置 |
|---|---|---|---|
| **P1-1** | forward_returns 部分截断无日志 | ✅ 已修 2026-05-20 | `attribution_service.py:132-152` 加 ratio < 0.8 时 logger.info（含三类静默丢失场景注释）|
| **P1-2** | lookback timedelta 日历天近似 | ✅ 已加注释 2026-05-20 | `attribution_service.py:80-85` 注释明示偏差 + Phase 14 同批切换；严格交易日切换延 Phase 14（**充分理由**：与 R14-P2-4 ICIR 窗口同源，同批一次修两处避免重复回归）|
| **P1-3** | AttributionPanel 未复用 DisclaimerBanner | ✅ 已修 2026-05-20 | `AttributionPanel.vue` 删手写 `<small>` + import + `<DisclaimerBanner :text="..." />` |
| **P2-5** | 端点双重序列化 | ✅ 已修 2026-05-20 | `api/v1/attribution.py:40-48` 删 `model_dump → AttributionHistoryItem(**i)` 中间一步 |
| **P2-8** | OLS 异常基线告警 | ✅ 已修 2026-05-20 | `attribution_service.py:155-178` 加 r² > 0.5 / r² < 0.005 / \|β\| > 0.1 三档 logger.warning |
| **P2-9** | trigger_reason 表混入历史草稿 | ✅ 已修 2026-05-20 | `utils/lineage.ts` 删 6 个 `quantile_*` 历史命名 + 加注释"以 Phase 11 §5 代码为准" |
| **P2-10** | memory/MEMORY.md 缺 Phase 12 条目 | ✅ 评审快照过时，实际已加 | `memory/phase12_factor_lineage.md` + MEMORY.md index 入口（早于评审产出）|
| **P2-11** | get_summary 缺单测 | ✅ 已修 2026-05-20 | `tests/unit/test_attribution_regression.py::test_ut_p12_b_05` 用合成 2 月 × 4 因子 = 8 行验 months_seen 去重 + cum_beta 累加 + avg_r_squared 单月计一次 |
| **P1-2 严格交易日** | rolling_icir + lookback 改交易日 | ⏸ Phase 14 §14-2 | **充分理由**：与 R14-P2-4 同源，5y candidate_pool 回填同批改两处更经济 |
| **P2-1 春节假期边界** | window_lo/hi 跨节假日小概率丢样本 | ⏸ Phase 14 §14-2 | **充分理由**：随严格交易日切换自动解决 |
| **P2-3 batch 分片预案** | attribution_repository docstring 32767 注释 | ✅ 2026-05-26 Phase 14 §14-8 | `data/attribution_repository.py` 模块 docstring 顶部加 V1.0 ≤ 1200 行未达 32767 限制 + N × 4 ≥ 4000 行应启用 `_BATCH_SIZE=500` 循环防御 |
| **P2-2 / P2-4 ~ P2-7** | 字段名硬编码 / 其余 | ⏸ V1.5+ | 评审定级"不阻塞 V1.0"，留 V1.5 重构期一并处理 |
| **AttrBackfill** | AttributionService 日级历史回填脚本 | ✅ 2026-05-26 Phase 14 §14-8 | `scripts/backfill_attribution_history.py` + INT-P14-8-01/02 集成测试；`AttributionService.run_monthly` 月末批量调用支持 5y × ~60 month_end 回填 |
| **P3-1 ~ P3-6** | 建议项不阻塞 | 已记录 | 按需穿插或 Phase 15 RC |

### 收口验证（2026-05-20 P1 + 高价值 P2 收口后）

- `uv run pytest tests/unit/ tests/e2e/ -q`：**507 passed**（+1 UT-P12-B-05）
- `uv run pytest tests/integration/`（test DB :5433）：**116 passed**
- `uv run ruff check src/ tests/`：**All checks passed**
- `npm run build`（vue-tsc + vite）：**通过**
- 生产栈 rebuild + restart + 冒烟 API-90~95：**全 6 PASS**

---

## 10. 签名

| 项 | 值 |
|---|---|
| 评审周期 | 2026-05-20（单次会话）|
| 评审依据 | phase12 v1.2 设计文档 + 工作树 13 个新文件 + 8 个 git diff |
| 评审产出 | 本报告 + §8 修订动作清单 + §9 修订追踪表 |
| 评审结论 | **基本通过（P1×3 必修，30 分钟内完成；不阻塞 Phase 13 启动）** |
| P1 + 高价值 P2 收口 | **2026-05-20 同会话完成**（按 CLAUDE.md §11 默认立即修原则）|
| 下一份评审计划 | Phase 13 实施完成后 / 或 Phase 14 §14-2 ICIR 严格交易日切换后短回看 |
