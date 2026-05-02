# Phase 1–5 设计文档评审报告

> **评审范围：** Phase 1–5 设计文档 vs 当前版本 SDD（v1.0-r1）及 system_design（v1.4）
> **评审日期：** 2026-04-07
> **评审方向：** 功能边界错漏、跨文档接口一致性、设计完整性

---

## 评审摘要

Phase 1–3 设计文档整体质量良好，与 SDD 和 system_design 的核心规格保持一致，已实施过的代码审查（CR-01~08、C-01~12）大幅提升了实现质量。Phase 4 的降级实现已有显式注释，边界清晰。Phase 5 是本次评审的重点问题集中区，共发现 **3 项 P1 级缺陷、2 项 P2 级缺口、1 项 P3 级注释错误**。

| ID | 级别 | 文档 | 问题摘要 |
|----|------|------|----------|
| P5-RISK-01 | P1 | Phase 5 §3.4 | RiskWarning 数据结构与 system_design §5.9 有 4 处不一致 |
| P5-RISK-02 | P1 | Phase 5 §3.4 | RiskChecker 完全缺失 DRAWDOWN 告警类型 |
| P5-SIGNAL-01 | P1 | Phase 5 §4.1 + §附录 | SignalService.save() 无 risk_warnings 参数，告警结果被丢弃 |
| P1-SCHEMA-01 | P2 | Phase 1 §3.2 | signal 表 DDL 注释仍含已废弃的 HOLD/EXIT 类型 |
| P5-PRE-01 | P2 | Phase 4 §4.4 vs Phase 5 §2.1 | P5-PRE-1 实现路线未满足 Phase 4 §4.4 验收标准 |
| P5-TEST-01 | P3 | Phase 5 §8.1 | 单元测试 RSK-01/RSK-02 期望值沿用了错误的 warning_type 字符串 |

---

## 一、P1 级缺陷（设计正确性，阻断实现）

### P5-RISK-01：RiskWarning 数据结构与 system_design §5.9 四处不一致

**位置：** `docs/design/phases/phase5_signals.md` §3.4（`engine/risk.py`）

**当前 Phase 5 设计：**

```python
@dataclass
class RiskWarning:
    ts_code: str
    warning_type: str  # 'CONCENTRATION' / 'INDUSTRY_CONCENTRATION'
    current_pct: float
    limit_pct: float
    message: str
```

**system_design §5.9 权威定义：**

```python
@dataclass(frozen=True)
class RiskWarning:
    ts_code: str
    warning_type: str  # 'CONCENTRATION_STOCK' | 'CONCENTRATION_INDUSTRY' | 'DRAWDOWN'
    message: str
    severity: str      # 'WARN'（不阻断信号）| 'BLOCK'（阻断对应 BUY 信号）
```

**四处差异：**

| # | 差异项 | Phase 5 | system_design §5.9 |
|---|--------|---------|-------------------|
| ① | `frozen=True` | 缺失 | 有 |
| ② | `warning_type` 枚举值 | `'CONCENTRATION'` / `'INDUSTRY_CONCENTRATION'` | `'CONCENTRATION_STOCK'` / `'CONCENTRATION_INDUSTRY'` / `'DRAWDOWN'` |
| ③ | `severity` 字段 | **缺失** | `str  # 'WARN' \| 'BLOCK'`（必需字段） |
| ④ | `current_pct` / `limit_pct` 字段 | **多余** | system_design §5.9 未定义 |

**影响：**

`severity` 字段缺失是结构性缺陷——BLOCK（阻断信号）vs WARN（只追加 reason）的分支逻辑依赖此字段。缺少后，Phase 7 的 DailyPipeline CP3 无法正确区分两类告警，导致风险管控失效。`warning_type` 枚举值错误则会使任何依赖字符串匹配的下游代码静默失效。

**建议修复：**

按 system_design §5.9 重新定义 RiskWarning：添加 `frozen=True`，修正 `warning_type` 枚举值，新增 `severity` 字段，去除 `current_pct` / `limit_pct`（可将相关信息并入 `message` 字符串）。

---

### P5-RISK-02：RiskChecker 完全缺失 DRAWDOWN 告警

**位置：** `docs/design/phases/phase5_signals.md` §3.4（`engine/risk.py` `RiskChecker`）

**问题：**

system_design §5.9 明确要求三类风险检查：

```
- BLOCK：加入后单股持仓比例 > max_single_stock_pct
- BLOCK：加入后行业集中度 > max_industry_pct
- WARN：账户最大回撤 > max_drawdown_pct  ← Phase 5 完全缺失
```

Phase 5 的 `RiskChecker.check()` 签名：

```python
def check(
    self,
    signals: list[TradeSignal],
    current_positions: list[Position],
    account_total_assets: float,
    stock_industry: pd.DataFrame,
    max_single_stock_pct: float = 0.20,
    max_industry_pct: float = 0.30,
) -> list[RiskWarning]:
```

缺少两个必需输入：
- `max_drawdown_pct: float`——回撤告警阈值
- `account_max_drawdown_pct: float`——账户当前最大回撤值（需要 AccountService 提供，或直接从 Account 对象读取）

**影响：**

WARN 类回撤告警是 SDD §10.2 要求的 V1.0 功能。缺失后，账户大幅亏损时用户得不到任何提示，风险控制体系残缺。此告警不阻断信号，实现代价低，无充分理由推迟。

**建议修复：**

在 `check()` 参数列表补入 `account_max_drawdown_pct: float` 和 `max_drawdown_pct: float`，添加：

```python
if account_max_drawdown_pct > max_drawdown_pct:
    warnings.append(RiskWarning(
        ts_code="ACCOUNT",        # 账户级告警，ts_code 用特殊值标识
        warning_type="DRAWDOWN",
        message=f"账户最大回撤 {account_max_drawdown_pct:.1%} 超过阈值 {max_drawdown_pct:.1%}",
        severity="WARN",
    ))
```

> **注意：** `account_max_drawdown_pct` 的来源需要 AccountService（Phase 6）提供。Phase 5 若确实无法获取此值，应在 RiskChecker 中预留参数和逻辑，但在 SignalService 集成时以 `None` 跳过检查，并加注降级说明。

---

### P5-SIGNAL-01：SignalService.save() 缺少 risk_warnings 参数，风险告警结果被丢弃

**位置：** `docs/design/phases/phase5_signals.md` §4.1（`services/signal_service.py`）和 §附录

**问题 A：save() 缺少 risk_warnings 参数**

system_design §2.2 DailyPipeline CP3 调用链（权威规格）：

```python
risk_warnings = self.risk_engine.check(signals, positions, account)
# BLOCK 级告警：对应信号从列表移除（不持久化）；WARN 级：附加到 signal.reason（SDD §10.2）
await self.signal_service.save(signals, risk_warnings=risk_warnings)
```

Phase 5 定义的 `SignalService.save()` 签名：

```python
async def save(
    self,
    signals: list[TradeSignal],
    trade_date: date,
    composite_df: pd.DataFrame | None = None,
) -> int:
```

`save()` 没有 `risk_warnings` 参数，无法接收风险告警结果。

**问题 B：Phase 5 附录中 warnings 变量被丢弃**

Phase 5 §附录 DailyPipeline CP3 调用链（Phase 5 描述版）：

```python
warnings = risk_engine.check(signals, positions, total_assets, stock_info)
await signal_service.save(signals, trade_date, composite_df)   # warnings 未使用
```

`warnings` 赋值后从未传入 `save()`，导致：
- BLOCK 类告警不会阻断任何信号（对应 BUY 信号仍被持久化）
- WARN 类告警不会追加到 `signal.reason` 字段

整个风险检查结果被静默丢弃，SDD §10.2 的集中度/回撤告警机制名存实亡。

**影响：**

这是 P5-RISK-01（缺少 severity 字段）的直接下游效应。两者形成链式缺陷：没有 severity → 无法区分 BLOCK/WARN → 无法在调用链中正确处理 → 告警结果被丢弃。

**建议修复：**

1. 在 `SignalService.save()` 中添加 `risk_warnings: list[RiskWarning] | None = None` 参数
2. `save()` 内部逻辑：BLOCK 类告警 → 从 signals 中移除对应标的的信号；WARN 类 → 将告警 message 追加到对应信号的 reason 字段
3. 更新 Phase 5 §附录的调用链伪代码，与 system_design §2.2 保持一致

---

## 二、P2 级缺口（设计完整性）

### P1-SCHEMA-01：Phase 1 signal 表 DDL 注释含已废弃类型 HOLD/EXIT

**位置：** `docs/design/phases/phase1_infrastructure.md` §3.2，signal 表 DDL

**当前注释：**

```sql
signal_type VARCHAR(10) NOT NULL, -- 'BUY'/'SELL'/'HOLD'/'EXIT'
```

**问题：**

system_design v1.4 修复了 DESIGN-12，将 signal_type 合法值限制为 `'BUY'/'SELL'` 两种，移除了从未明确定义的 HOLD 和 EXIT。Phase 1 文档中的 DDL 注释未随之更新，仍保留四种值，与现行规格矛盾。

虽然这只是注释层面的不一致（实际约束由 signal_type 的取值范围控制），但对后续维护人员会造成误导，可能在扩展时错误地引入 HOLD/EXIT 类型。

**建议修复：**

将注释更新为：`-- 'BUY'/'SELL'（详见 system_design §5.9）`

---

### P5-PRE-01：P5-PRE-1 实现路线未满足 Phase 4 §4.4 验收标准

**位置：** `docs/design/phases/phase4_factor_engine.md` §4.4 vs `docs/design/phases/phase5_signals.md` §2.1

**Phase 4 §4.4 原始验收标准：**

> "从空库执行完 `ingest_history` 后，无需任何手动命令，roe/total_equity/sw_industry_l1 即有有效数据。"

**Phase 5 P5-PRE-1 实际设计：**

在 DataService 新增两个**独立方法**：

```python
async def refresh_industry_classification(self) -> int: ...  # TD-3
async def refresh_financials_full(...) -> dict: ...           # TD-1/2
```

Phase 5 §2.1（P5-PRE-3 退役说明）明确：

> "一次性补录：`uv run python -c "...asyncio.run(svc.refresh_financials_full())"`"

即新部署后仍须手动执行命令，或等待 Phase 7 季度调度器触发，方能完成 ROE/total_equity/行业分类的初始入库。**"无需任何手动命令"的验收标准未达成。**

**影响：**

这是策略层面的偏移：Phase 4 计划扩展 `ingest_history` 管道（自动闭环），Phase 5 改为独立方法（仍需人工触发）。这不影响 Phase 5 的日常运营（季度调度器持续更新），但影响**首次部署**体验，且使"退役手动脚本"的目标实质上只移动了触发方式，并未彻底消除手动依赖。

**建议处理（二选一）：**

方案 A：在 Phase 5 设计中将 `refresh_financials_full()` 和 `refresh_industry_classification()` 集成进 `ingest_history()` 的初次执行路径（仅在库为空或强制刷新时调用），满足原验收标准。

方案 B：在 Phase 5 设计文档中显式修订验收标准，注明"首次部署需执行一次性初始化命令"，同时在部署文档中说明。禁止静默降级（CLAUDE.md 要求）。

---

## 三、P3 级注释错误

### P5-TEST-01：单元测试 RSK-01/RSK-02 期望值沿用错误的 warning_type 字符串

**位置：** `docs/design/phases/phase5_signals.md` §8.1 `test_risk_checker.py`

| 用例 | 当前期望值 | 正确期望值 |
|------|----------|----------|
| RSK-01 | `warning_type=='CONCENTRATION'` | `warning_type=='CONCENTRATION_STOCK'` |
| RSK-02 | `warning_type正确`（隐含 `'INDUSTRY_CONCENTRATION'`） | `'CONCENTRATION_INDUSTRY'` |

这是 P5-RISK-01 的连锁错误——设计文档定义了错误的类型名，测试用例随之使用了错误的期望值。修复 P5-RISK-01 后须同步修正测试用例，否则测试通过但 warning_type 仍不符合规格。

---

## 四、Phase 1–4 无重大问题（说明）

### Phase 1
整体架构合理，19 张表的 schema 设计经过代码审查已修正主要问题。除 P1-SCHEMA-01（DDL 注释）外，无功能边界错漏。

### Phase 2
DataSourceAdapter ABC、TradingCalendar、DataValidator、AdjustedPriceProvider 接口设计完整，与 SDD §4/§5 规格对齐。复权公式（后复权 `close × adj_factor`，前复权 `close × (adj_factor[-1] / adj_factor[t])`）与 SDD §4.1 一致。已知 TD-1/2/3 问题有显式降级注释，不属于设计错漏。

### Phase 3
MarketStateEngine 算法规格（ADX > 25 + MA20/MA60 + close 位置 → 三态）与 SDD §6.3 完整对齐。防抖动机制（3 日连续一致才切换）与 SDD §6.5 一致。CR-01（交易日→日历天换算）和 CR-04（APScheduler 显式传参）的修正已同步回设计文档 v1.1。

### Phase 4
四大策略因子与权重（SDD §7.2.1–7.2.4）均正确实现。Scorer 权重矩阵（SDD §7.5）三状态值精确对齐。F-5/F-7 降级实现有显式 `【降级说明】` 注释，代码审查 C-01~C-12 的修复已同步到文档 v1.1。

---

## 五、建议修复优先级

| 优先级 | ID | 执行建议 |
|--------|-----|---------|
| **立即修复** | P5-RISK-01 | 重新定义 RiskWarning 数据结构（frozen=True + 正确枚举值 + severity 字段） |
| **立即修复** | P5-RISK-02 | 在 RiskChecker.check() 补入 DRAWDOWN 检查（输入参数 + 返回告警逻辑） |
| **立即修复** | P5-SIGNAL-01 | SignalService.save() 补 risk_warnings 参数，更新附录调用链 |
| **Phase 5 实现前** | P5-PRE-01 | 明确 P5-PRE-1 路线并更新验收标准（扩展管道 or 显式文档化） |
| **随同修复** | P5-TEST-01 | 修正 RSK-01/RSK-02 的 warning_type 期望值（与 P5-RISK-01 联动） |
| **下次文档修订** | P1-SCHEMA-01 | 更新 Phase 1 DDL 注释，移除 HOLD/EXIT 引用 |

---

*评审人：金融 IT 专家*
*本报告存档于 `docs/reviews/phase1_5_design_review_2026-04-07.md`*
