# Phase 7 设计评审报告

**评审对象**：`docs/design/phases/phase7_pipeline.md` v1.0  
**评审依据**：`QuantPilot_SDD.md`；`system_design.md` §2/§5/§6/§9；`CLAUDE.md` Phase 启动核查规则  
**评审日期**：2026-04-10  
**评审人**：Claude Code  
**状态**：已关闭（2026-04-12，全部 8 项问题 + 3 项残留项均已修复）

---

## 1. 总体评价

Phase 7 设计文档完成度高：

| 维度 | 评价 |
|------|------|
| **范围符合性** | §9 分配的所有模块全部纳入 scope，Phase 6 推迟项（mark_to_market/fetch_dividends）均已接收 ✓ |
| **设计待定项** | system_design §9 的 5 项设计待定（D7-1～D7-5）全部在 §2 中给出明确决策 ✓ |
| **降级说明** | DailyPipeline best-effort 步骤、NotificationService no-op stub 均有正确降级注释 ✓ |
| **前置条件核查** | 关键依赖（PipelineRun ORM、FactorIcHistory、Report、SignalScoreSnapshot 表）均已确认存在 ✓ |
| **推迟项** | Phase 8（PerformanceService）、Phase 10（WxPusher）推迟明确 ✓ |

**结论：存在 2 个 P2 级（需修复后方可开始实现）、6 个 P3 级问题。**

---

## 2. 问题清单

### 2.1 P2 级（必须在实现前修复）

#### D7-P2-01：`SignalService.generate_for_date()` 为 CP3 必要前提，但在 scope/设计/DoD 中全部缺失

**位置**：`phase7_pipeline.md §3.1`（DailyPipeline._cp3_signals 的脚注）

**问题描述**：

设计文档 §3.1 CP3 描述：

> `SignalService.generate_for_date(trade_date)` — 从评分快照生成当日信号列表  
> Phase 7 需补充

但该方法在整份设计文档中**缺少三样东西**：

1. **未加入 §1.1 交付范围表**：SignalService 不在 Phase 7 scope 的模块列表中
2. **无设计规格**：`generate_for_date` 内部如何工作？是从 candidate_pool 读取评分？还是重调 ScoringService？还是从 signal_score_snapshot 查询已有结果？完全未描述
3. **未加入 DoD**：D-08 只要求 "DailyPipeline 完整实现（CP1→CP2→CP3→Step4→Step5→Step6）"，但 CP3 依赖的 `generate_for_date` 没有独立验收项

**直接后果**：若开发组仅按 §1.1 scope 和 DoD 实现，CP3 将缺少调用目标，流水线无法完成。

Phase 5 `SignalService` 只有手动触发路径（接受 POST body 的 `generate_signals`），没有按日期批量生成的方法，已通过以下验证：

```
# 搜索结果：generate_for_date 和 generate_signals 均不存在
backend/src/quantpilot/services/signal_service.py — 无 generate_for_date
```

**修正方案**：

1. 在 §1.1 scope 表中增加一行：`SignalService 扩展 | signal_service.py | 新增 generate_for_date(trade_date)`
2. 在 §3.X 中补充 `generate_for_date` 的设计规格，至少包括：
   - 输入：`trade_date: date`
   - 数据源：建议从 `candidate_pool`（Phase 4 已写入的评分结果）读取，避免重复计算
   - 输出：`list[Signal]`，并写入 `signal_score_snapshot`（血缘）
   - 若候选池为空 → 返回 `[]`，不报错
3. 在 DoD 中增加：`D-08a | SignalService.generate_for_date(trade_date) 实现；CP3 集成测试验证自动信号生成路径`

---

#### D7-P2-02：`FactorMonitorEngine` 接口设计与 `system_design §5.5` 不一致，须更新权威设计文档

**位置**：`phase7_pipeline.md §3.4` vs `system_design.md §5.5`

**问题描述**：

两份文档对 `FactorMonitorEngine` 的接口定义存在实质差异：

| 方法 | system_design §5.5 | phase7 设计 §3.4 | 差异 |
|------|--------------------|------------------|------|
| `calc_ic` | `-> float` | `-> float | None`（样本 < 5 返回 None） | 返回类型扩展 |
| `calc_ic_batch` | `(calc_month, return_window) -> list[FactorIcRecord]` | **不存在**（改由 FactorMonitorService 编排） | 接口拆分 |
| `calc_ic_ir` | **不存在** | `(ic_series, window) -> tuple[float\|None, float\|None, float\|None]` | phase7 新增 |
| `calc_half_life` | **不存在**（内含于 calc_ic_batch） | `(ic_series) -> float | None`（独立方法） | phase7 新增 |
| `detect_alert` | `(record: FactorIcRecord) -> str | None` | `(ic_mean, ir, half_life_days, recent_ic_signs: list[float]) -> str | None` | 参数签名完全不同 |

**评价**：Phase 7 的细粒度接口设计（独立纯函数 + service 编排）更有利于单元测试，是合理的设计演进。特别是 `detect_alert` 需要 `recent_ic_signs`（最近 3 个月 IC 值）来判断 DECAY，而 `system_design §5.5` 的 `detect_alert(record)` 仅凭单条记录无法实现连续 3 月为负的判断——所以 Phase 7 的接口实际上修正了 system_design 的设计缺陷。

但 CLAUDE.md §10 规定：**"禁止 phase 实际范围与 system_design §9 不一致时跳过 §9 更新"**；同理，Phase 7 对 §5.5 接口的实质性修改必须同步回写。

**修正方案**：

在 phase7_pipeline.md §3.4 末尾加说明：
> 【接口演进说明】本 Phase 对 system_design §5.5 的 FactorMonitorEngine 接口进行细化重构：拆除 `calc_ic_batch` 批量方法（改由 FactorMonitorService.run_monthly 编排），新增 `calc_ic_ir`/`calc_half_life` 独立纯函数，`detect_alert` 改接收各指标离散值以支持 DECAY 连续判断。system_design §5.5 须在本 Phase 完成后同步更新。

同步在 system_design §5.5 将伪代码更新为 Phase 7 的细粒度接口。

---

### 2.2 P3 级（建议在实现前修正，不阻塞开始）

#### D7-P3-03：`NotificationService.notify_market_state_change` 无调用者

**位置**：`phase7_pipeline.md §3.8`

**问题描述**：

Phase 7 NotificationService stub 定义了 `notify_market_state_change(old_state, new_state)` 方法，但设计文档中没有任何地方说明谁调用它。

- 最自然的调用者是 MarketStateService（Phase 3），但 Phase 3 当前没有 NotificationService 依赖
- Phase 10 实现真实 WxPusher 时，若 Phase 7 没有接入调用链，Phase 10 还需额外修改 MarketStateService

**建议**：在 §3.8 或 §1.1 中明确接入策略：
- 选项 A：Phase 7 同步修改 MarketStateService，注入 NotificationService（no-op stub），在状态变化时调用 — 接入成本低
- 选项 B：Phase 10 统一接入，Phase 7 仅定义接口不接入 — 需在 §3.8 显式注明"Phase 7 不接入调用链，Phase 10 接入"

---

#### D7-P3-04：`GET /factor-quality` 响应顶层 `calc_month` 字段存在歧义

**位置**：`phase7_pipeline.md §5.2`

**问题描述**：

响应结构：
```json
{
  "data": {
    "calc_month": "2026-03-31",
    "items": [...]
  }
}
```

当不同策略的最新记录来自不同月份（例如部分策略上月末计算、部分策略当月初补算），顶层 `calc_month` 值不明确：取最大值？最小值？还是强制要求所有策略同月？

此外 `/factor-quality/history` 使用标准 `{items, total}` 结构，两端点格式不统一。

**建议**：在 §5.2 中明确 `calc_month` 的含义（如"取所有返回记录中最新的 calc_month"），或将 `calc_month` 下移至每个 item 中（更精确，无歧义），并移除顶层 `calc_month` 字段。

---

#### D7-P3-05：`DailyPortfolioValue` ORM 模型 `__table_args__` 缺少 Index 定义

**位置**：`phase7_pipeline.md §4.1`

**问题描述**：

迁移文件定义了：
```python
op.create_index("ix_dpv_account_date", "daily_portfolio_value",
                ["account_id", sa.text("trade_date DESC")])
```

但 ORM 模型 `__table_args__` 只有 `UniqueConstraint`，没有对应的 `Index`：

```python
__table_args__ = (
    UniqueConstraint("account_id", "trade_date", name="uq_dpv_account_date"),
    # 缺少：Index("ix_dpv_account_date", "account_id", ...)
)
```

CLAUDE.md §8 规定：**"ORM `__table_args__` 与迁移文件保持一致"**。

**建议**：在 ORM 定义中加入：
```python
Index("ix_dpv_account_date", "account_id", text("trade_date DESC")),
```

---

#### D7-P3-06：月末 Job 触发时非交易日处理逻辑未明确

**位置**：`phase7_pipeline.md §3.9`

**问题描述**：

设计文档说"每月最后一个自然日 20:00，由 MonthlyScheduler 判断是否为交易日"，但未说明判断失败后的行为：

- 若最后一个自然日为非交易日（周末、节假日），MonthlyScheduler 是：
  - A. 顺延至次月初？
  - B. 回溯到当月最后一个交易日？
  - C. 直接跳过（不运行月报/因子监控）？

月末因子监控需要当月所有收益率数据到位后才能准确计算，选项 B（回溯到最后交易日）更合适，但需要明确。

**建议**：在 §3.9 中增加一段说明月末回溯逻辑，如："若触发日非交易日，`run_all()` 调用 `calendar.prev_trade_date(month_end)` 取当月最后交易日作为 `calc_month`"。

---

#### D7-P3-07：新服务依赖注入函数未列入交付范围

**位置**：`phase7_pipeline.md §1.1`

**问题描述**：

新增服务（FactorMonitorService、ReportService、LineageService）需要在 `api/deps.py` 中添加对应的 `get_*_service` 依赖注入函数。

CLAUDE.md §6 规定：**"所有依赖注入函数统一放在 `api/deps.py`，禁止在路由文件内定义"**。

设计文档 §1.1 的 scope 表和 DoD 均未提及 `api/deps.py` 的更新（而历史上 Phase 5/6 都需要新增依赖函数）。

**建议**：在 §1.1 scope 表补充一行：`api/deps.py 扩展 | api/deps.py | 新增 get_factor_monitor_service / get_report_service / get_lineage_service`；并在 DoD D-11 中注明。

---

#### D7-P3-08：LineageService 重构后现有 Phase 5 lineage E2E 测试未纳入 DoD

**位置**：`phase7_pipeline.md §3.7`、§7 DoD

**问题描述**：

设计文档 §3.7 说明 Phase 5 的 `signals.py` 路由直接调用 repo，Phase 7 重构为调用 `LineageService`。这是对已有端点 `GET /signals/{id}/lineage` 的实现重构。

Phase 5 已有对应 E2E 测试覆盖此端点，但 DoD 中没有任何项要求"重构后 Phase 5 lineage 测试仍通过"，存在回归风险。

**建议**：在 DoD 中增加：`D-12a | 重构后，现有 Phase 5 /signals/{id}/lineage E2E 测试（test_sapi_*/test_signals_api.py 中 lineage 相关用例）全部通过`。

---

## 3. 整合验证

### 3.1 §9 分配模块核查

| system_design §9 分配 | phase7 设计文档 §1.1 | 状态 |
|----------------------|---------------------|------|
| DailyPipeline（CP1/CP2/CP3 + 盯市） | ✓ §3.1、D-08 | ✓ |
| LineageService（信号-快照绑定） | ✓ §3.7、D-04 | ✓ |
| FactorMonitorEngine（IC/IR） | ✓ §3.4、D-01 | ✓（见 P2-02）|
| MonthlyScheduler（月末） | ✓ §3.9、D-10 | ✓ |
| notifier no-op stub | ✓ §3.8、D-05 | ✓ |
| AccountService.mark_to_market | ✓ §3.2、D-06 | ✓ |
| fetch_dividends（自动分红） | ✓ §3.3、D-07 | ✓ |
| /pipeline/* API（2端点） | ✓ §5.1、D-11 | ✓ |
| /factor-quality/* API（2端点） | ✓ §5.2、D-11 | ✓ |
| /reports/* API（3端点） | ✓ §5.3、D-11 | ✓ |
| SignalService.generate_for_date | **§1.1 缺失**（见 P2-01）| ✗ |

### 3.2 关键接口前置确认

| 前置接口 | 状态 |
|---------|------|
| `ScoringService.run_daily_scoring(trade_date)` — CP2 调用 | ✓ 已确认存在（strategy_service.py:46） |
| `SignalService.expire_old_signals()` — Step6 调用 | ✓ 已确认存在（signal_service.py:230） |
| `PipelineRun` ORM + 全部 CP 字段 | ✓ 已确认（system.py:19-37，含 data_snapshot_version） |
| `FactorIcHistory` ORM | ✓ 已确认（business.py:116，含 ic_mean_3m/ir_3m/half_life_days/alert_status） |
| `Report` ORM | ✓ 已确认（business.py:142，含 content JSONB/summary/generated_at） |
| `SignalScoreSnapshot` ORM | ✓ 已确认（business.py:90） |
| `SignalService.generate_for_date()` — CP3 调用 | **✗ 不存在**（P2-01） |

---

## 4. 评审总结

### 问题汇总

| 编号 | 级别 | 标题 | 状态 |
|------|------|------|------|
| D7-P2-01 | **P2** | `SignalService.generate_for_date()` 为 CP3 必要前提，scope/设计/DoD 全部缺失 | ✅ 已关闭 |
| D7-P2-02 | **P2** | `FactorMonitorEngine` 接口与 system_design §5.5 不一致，须更新权威文档 | ✅ 已关闭 |
| D7-P3-03 | P3 | `notify_market_state_change` 无调用者，Phase 7/10 接入策略未明确 | ✅ 已关闭 |
| D7-P3-04 | P3 | `/factor-quality` 响应顶层 `calc_month` 多策略场景歧义 | ✅ 已关闭 |
| D7-P3-05 | P3 | `DailyPortfolioValue` ORM 缺少 Index 定义（CLAUDE.md §8 违规） | ✅ 已关闭 |
| D7-P3-06 | P3 | 月末 Job 非交易日触发的回溯/跳过逻辑未说明 | ✅ 已关闭 |
| D7-P3-07 | P3 | 新服务 deps.py 依赖注入函数未列入 scope 和 DoD | ✅ 已关闭 |
| D7-P3-08 | P3 | LineageService 重构后 Phase 5 lineage E2E 测试未纳入 DoD 验证 | ✅ 已关闭 |
| R-01 | 轻微 | phase7_pipeline.md 头部版本号未同步（仍为 v1.0） | ✅ 已关闭 |
| R-02 | P3 | system_design §2.3 MonthlyScheduler 流程代码仍调用已拆除的 `calc_ic_batch` / `save_and_alert` | ✅ 已关闭 |
| R-03 | 轻微 | phase7_pipeline.md §3.1 `_cp3_signals` docstring 方法名误写为 `generate_signals` | ✅ 已关闭 |

### 修复结果

**全部 11 项问题均已关闭**（2026-04-12 验证）：
- phase7_pipeline.md 升至 v1.1，完整纳入 `generate_for_date` scope/设计/DoD
- system_design §5.5 更新为细粒度接口；§2.3 同步移除旧调用
- 所有 P3 改进项均已落地

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-10 | 初版设计评审，共 8 项问题（P2×2、P3×6） |
| v1.1 | 2026-04-12 | 验证修复结果，补录 R-01/R-02/R-03 三项残留问题并确认关闭；全部 11 项问题已关闭，评审报告状态置为已关闭 |
