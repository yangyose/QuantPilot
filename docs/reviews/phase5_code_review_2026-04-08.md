# Phase 5 代码评审报告

> **审查日期**：2026-04-08
> **审查对象**：Phase 5 信号生成全部交付代码
> **依据文档**：`docs/design/phases/phase5_signals.md`（v1.0）、SDD §9/§10、CLAUDE.md
> **评审范围**：`engine/signal.py`、`engine/position.py`、`engine/risk.py`、`services/signal_service.py`、`api/v1/signals.py`、`schemas/signals.py`、P5-PRE 任务（`data_service.py`、`monthly_scheduler.py`、`engine/universe.py` F-5/F-7 恢复）、`api/deps.py`
> **关联评审**：Phase 1–4 综合代码评审（`phase1_4_code_review_2026-04-08.md`）

---

## 总体评价

Phase 5 Engine 层（SignalGenerator / PositionSizer / RiskChecker）结构清晰，IO 隔离彻底，算法逻辑与 SDD §9/§10 整体一致。P5-PRE-1~4 前置任务全部交付，`backfill_td123.py` 已删除，F-5/F-7 恢复符合设计预期。存在 **2 个 P1 问题**（状态码错误、DoD 测试可达性）、**6 个 P2 问题**（逻辑正确性或数据完整性）、**3 个 P3 问题**（次要质量）。

---

## P1 问题（须在 Phase 5 验收前修复）

### C-01：`update_status` "信号不存在" 返回 HTTP 400，应返回 404

**位置**：`services/signal_service.py:75–81`；`api/v1/signals.py:68–72`

**问题**：

```python
# signal_service.py
async def update_status(self, signal_id: int, new_status: str) -> SignalModel:
    signal = await self._repo.get_signal_by_id(signal_id)
    if signal is None:
        raise ValueError(f"Signal {signal_id} not found")   # ← "不存在" 与"非法转换"用同一异常类型

    allowed = _VALID_TRANSITIONS.get(signal.status, set())
    if new_status not in allowed:
        raise ValueError(...)                               # ← 非法转换
```

```python
# signals.py
try:
    updated = await service.update_status(signal_id, body.status)
except ValueError as exc:
    return JSONResponse(status_code=400, ...)               # ← 两类错误都返回 400
```

"信号不存在"应返回 404，"非法状态转换"应返回 400。当前两类错误均以相同 `ValueError` 抛出，API 层无法区分，统一返回 400，违反 HTTP 语义。

**修复**：将 Service 层"不存在"改用不同异常标识，API 层分别处理：

```python
# 方案 A：自定义异常
class SignalNotFoundError(ValueError): pass

if signal is None:
    raise SignalNotFoundError(f"Signal {signal_id} not found")

# signals.py
except SignalNotFoundError:
    raise HTTPException(status_code=404, ...)
except ValueError as exc:
    return JSONResponse(status_code=400, ...)
```

```python
# 方案 B（最简单）：直接在 service 层抛 HTTPException
```

同样问题也存在于 `get_lineage()`，但该方法已在 `signals.py` 中正确处理为 404（通过捕获 `ValueError` 后 raise HTTPException 404）。**但 `update_status` 的 not-found 路径返回的是 400 而非 404**——为最高优先级修复。

---

### C-02：`SignalScoreSnapshot` 在 Phase 5 从未写入，但设计明确要求 Phase 5 写入；INT-SVC-04 DoD 测试无法通过

**位置**：`services/signal_service.py:97–112`（`_build_snapshot_rows`）

**问题**：

```python
def _build_snapshot_rows(self, signals, trade_date, composite_df) -> list[dict]:
    """...
    【降级说明】Phase 5 的 upsert_signals 使用 ON CONFLICT UPDATE，无法保证返回
    正确的 signal.id（PostgreSQL RETURNING 不总与 ON CONFLICT 顺序匹配）。
    完整血缘写入（signal_id FK）推迟至 Phase 7。
    当前 save() 接受 composite_df 参数但跳过快照写入。
    """
    return []   # 永远返回空列表
```

设计文档 §4.1 明确：
> "若提供 composite_df，则为每个未被阻断的信号写入 SignalScoreSnapshot"

设计文档 §1.2 推迟列表中只推迟了 **LineageService（封装抽象层）**，但同时注明：
> "Phase 5 在 SignalService **内直接**写 SignalScoreSnapshot"

这意味着 Phase 5 应绕过 LineageService 抽象，直接在 SignalService 内写快照——但当前实现连直接写入都跳过了。

此外，DoD D-05 要求集成测试 INT-SVC-04 通过：
> "get_lineage(signal_id) → 返回信号及其 SignalScoreSnapshot"

由于快照从未写入，`get_lineage` 始终返回 `snapshot=None`，INT-SVC-04 要么测试不通过，要么已被弱化为接受 None——若后者，DoD 实质已降低标准。

**关于降级原因的技术分析**：

注释称 "RETURNING 不总与 ON CONFLICT 顺序匹配"。实际上，PostgreSQL `INSERT ... ON CONFLICT DO UPDATE RETURNING id` 对于**新插入行**会可靠返回 id；对于 **UPDATE 行**，RETURNING 也返回更新后的 id。对于批量 `upsert_signals` 已知顺序与 rows 列表顺序可能不匹配，但可通过在返回结果中按 `ts_code` 或 `signal_type` 对应即可解决。

**修复建议**：

```python
# upsert_signals 改为 RETURNING id, ts_code, signal_type
# 在 save() 中对照 ts_code+signal_type 匹配 signal_id
# 再写入 signal_score_snapshot（signal_id, trade_date, ts_code, ...）
```

或退而求其次：只为**新插入**的信号（`status='NEW'` 且此前无记录）写入快照，通过 `xmax=0`（PostgreSQL 系统列）区分插入与更新。

---

## P2 问题（应在 Phase 5 结束前修复）

### C-03：`save()` upsert 将 VIEWED/ACTED 状态覆盖为 NEW，丢失用户操作记录

**位置**：`services/signal_service.py:53–60`（rows 构建）；`data/repository.py:upsert_signals`

**问题**：

```python
rows.append({
    ...
    "status": "NEW",   # ← 硬编码，覆盖已有的 VIEWED/ACTED 状态
})
```

ON CONFLICT DO UPDATE 会将已有信号的 status 字段无条件改为 "NEW"。若用户已将某信号标记为 VIEWED，当 DailyPipeline 第二次运行（CP3 重试或手动触发）时，该记录会被重置为 NEW，用户操作丢失。

设计文档 §4.1 还要求 SUPERSEDED 状态：
> "若同一 ts_code 当日已有信号，将旧信号置 SUPERSEDED"

但当前 upsert 直接覆盖，SUPERSEDED 状态永远无法被设置。

**影响**：此问题在 Phase 5 单次运行下不会显现（无 DailyPipeline），但 Phase 7 集成后立刻暴露。建议 Phase 5 就修复，避免技术债累积。

**修复建议**：

```python
# upsert_signals 的 DO UPDATE SET 中排除 status 字段（保留用户操作）
stmt = stmt.on_conflict_do_update(
    constraint="uq_signal_code_date_type",
    set_={k: v for k, v in update_dict.items() if k != "status"},
)
# 或在 upsert 前将旧信号的 status 改为 SUPERSEDED，再插入新信号
```

---

### C-04：`refresh_financials_full` 逐股先后 upsert `fin_df` 和 `bal_df`，后者可能将前者字段覆盖为 NULL

**位置**：`services/data_service.py`（`refresh_financials_full` 内层循环）

**问题**：

```python
for ts_code in batch:
    fin_df = await self._adapter.fetch_financial_by_stock(ts_code)
    if not fin_df.empty:
        await self._repo.upsert_financial_data(fin_df)   # 写入 roe/net_profit_yoy/...
    bal_df = await self._adapter.fetch_balance_sheet(ts_code)
    if not bal_df.empty:
        await self._repo.upsert_financial_data(bal_df)   # 写入 total_equity
```

`fetch_financial_by_stock` 返回含 `roe`/`net_profit_yoy`/`revenue_yoy`/`debt_to_asset` 字段的行；`fetch_balance_sheet` 返回含 `total_equity` 字段的行。两者可能具有相同 `(ts_code, report_period, publish_date)` 主键（Tushare 两个接口共享同一 `ann_date`）。

第二次 `upsert_financial_data(bal_df)` 执行 ON CONFLICT DO UPDATE 时，`bal_df` 中不含 `roe` 等字段（为 NULL），若 upsert 的 SET 子句包含这些列，则将原本已写入的 `roe` 覆盖为 NULL——完全抵消第一次 upsert 的效果。

**影响**：`refresh_financials_full` 执行后 `roe` 等关键字段依然为 NULL，F-5/F-7 恢复无效，ValueStrategy/UniverseFilter 仍退化运行。

**修复**（三选一）：
1. **合并后 upsert**：在内存中将 `fin_df` 和 `bal_df` 按 `(ts_code, report_period, publish_date)` merge 后一次 upsert；
2. **COALESCE 保留非空值**：upsert 的 DO UPDATE SET 改用 `COALESCE(EXCLUDED.col, target.col)` 保留已有非 NULL 值；
3. **分列更新**：分别调用不同 upsert 方法，只更新各自对应列。

---

### C-05：`RiskChecker.check()` 对 `suggested_pct=None` 的信号产生误报 BLOCK

**位置**：`engine/risk.py:62–71`

**问题**：

```python
for sig in signals:
    if sig.signal_type != "BUY":
        continue
    suggested = sig.suggested_pct or 0.0   # ← None → 0.0
    new_stock_pct = position_pct.get(sig.ts_code, 0.0) + suggested
    if new_stock_pct > max_single_stock_pct:
        warnings.append(RiskWarning(..., severity="BLOCK"))
```

当 `PositionSizer` 因资金不足将 `suggested_pct=None` 时，信号的含义是"建议买入，但当前仓位不足以执行"。RiskChecker 对该信号使用 `suggested=0.0` 计算集中度，但如果当前已有持仓超过 `max_single_stock_pct`（20%），则会产生 BLOCK 警告，在 `SignalService.save()` 中将该 BUY 信号移除。

这产生了矛盾：信号已因 `suggested_pct=None` 标记为不可执行，RiskChecker 又对其产生 BLOCK 并移除，导致 `get_today_signals` 中该信号消失，用户连"建议买入但资金不足"的提示都看不到。

**修复**：跳过 `suggested_pct` 为 None 或 0 的信号：

```python
if sig.signal_type != "BUY":
    continue
suggested = sig.suggested_pct
if not suggested:      # None 或 0 → 跳过集中度检查
    continue
```

---

### C-06：`PositionSizer` 批量信号处理未扣减已分配仓位，多信号累计超出可用额度

**位置**：`engine/position.py:55–78`

**问题**：

```python
available = max(0.0, effective_max - current_used - cfg.min_cash_pct)
# ↑ 仅依据当前持仓计算，在循环内不随已分配信号递减

for sig in signals:
    ...
    single = min(cfg.single_pct, stock_remaining, available)
    result.append(replace(sig, suggested_pct=single))
```

假设 `available=20%`，有 3 个不同股票的 BUY 信号，每个 `single_pct=10%`——3 个信号均得到 `suggested_pct=10%`，合计 30% > 可用 20%。

这对用户可能产生误导：如果用户全部执行，将超出仓位上限。

**设计文档 §3.3 原文**没有明确说明多信号批量处理时是否应扣减，但从直觉上"可用仓位"应该是一个消耗性资源。

**修复建议**（若按设计意图独立评估每个信号）：在循环内累减 `available`：

```python
allocated = 0.0
for sig in signals:
    if sig.signal_type != "BUY":
        ...
        continue
    remaining_available = max(0.0, available - allocated)
    if remaining_available < cfg.single_pct * 0.5:
        result.append(replace(sig, suggested_pct=None))
        continue
    single = min(cfg.single_pct, stock_remaining, remaining_available)
    allocated += single
    result.append(replace(sig, suggested_pct=single if single > 0 else None))
```

若设计意图确实是"独立评估，用户自行决策"，则应在 Phase 5 设计文档中明确说明此行为，并在 API 响应或 `liquidity_note` 字段中提示用户总建议仓位可能超出可用额度。

---

### C-07：`GET /signals` 路由定义为 `"/"` 而非 `""`，与全项目路由风格不一致

**位置**：`api/v1/signals.py:28`

```python
@router.get("/")    # ← 与 watchlist.py, market.py 等使用 "" 的风格不一致
async def get_signals(...):
```

其他路由文件（`watchlist.py:@router.get("")`，`market.py:@router.get("/state")` 等）均采用无尾斜杠风格。FastAPI 默认 `redirect_slashes=True`，访问 `/api/v1/signals`（无尾斜杠）时会发生 307 重定向到 `/api/v1/signals/`，部分客户端（如移动端、某些 HTTP 库）不自动跟随 307 重定向，导致 API 不可用。

**修复**：将 `@router.get("/")` 改为 `@router.get("")`，与全项目保持一致。

---

### C-08：`generate()` 方法签名包含设计规格未列入的 `trade_date` 参数，设计文档需同步

**位置**：`engine/signal.py:59`

```python
def generate(
    self,
    composite_scores: pd.DataFrame,
    current_positions: list,
    market_state: MarketStateEnum,
    snapshot_quotes: pd.DataFrame,
    trade_date: date,             # ← 设计规格未列入此参数
    risk_params: RiskParams | None = None,
) -> list[TradeSignal]:
```

设计文档 §3.2 的方法签名中没有 `trade_date` 参数，但 `TradeSignal` 需要该字段，此参数是必要的（否则无法构造 TradeSignal）——这是设计规格的遗漏，实现正确处理了该遗漏。

**处置**：更新 `phase5_signals.md §3.2` 中的方法签名，补充 `trade_date: date` 参数及说明，消除文档与实现的不一致。

---

## P3 问题（次要，可在 Phase 5/6 迭代中处理）

### C-09：Service 层使用 `ValueError` 同时表达"not found"和"非法参数"，语义不清

**位置**：`services/signal_service.py:73–75, 83–87`

`ValueError` 的语义是"无效参数值"，而不是"资源不存在"。当前 `update_status` 中两类错误（"信号不存在"和"非法状态转换"）都抛 `ValueError`，依赖 API 层无法通过异常类型区分（C-01 的根因之一）。

**建议**：定义轻量内部异常：

```python
# core/exceptions.py（已有 AuthError 等，可追加）
class NotFoundError(Exception): pass
```

Service 层信号不存在时抛 `NotFoundError`，非法转换继续用 `ValueError`。

---

### C-10：`TradeSignal.score_breakdown` / `raw_factors` 字段在 `SignalGenerator` 中始终为 `None`

**位置**：`engine/signal.py:103–117`（BUY 信号构建）

```python
signals.append(TradeSignal(
    ts_code=ts_code,
    signal_type="BUY",
    trade_date=trade_date,
    score=score,
    # score_breakdown 和 raw_factors 未设置 → None
))
```

设计意图：这两个字段由 `generate()` 从 `composite_scores` DataFrame 中提取，携带到 `SignalService.save()` 用于写入血缘快照。但由于 C-02（快照写入推迟至 Phase 7），这两个字段目前完全无用。

这不是 bug，但会让后续 Phase 7 开发者困惑（为什么字段存在但总是 None）。

**建议**：在 `generate()` 中从 `composite_scores` 提取 `score_breakdown`/`raw_factors` 并填入 TradeSignal，即使 Phase 5 快照写入暂时跳过，这样 Phase 7 只需修改 `_build_snapshot_rows` 即可，不需要修改 Engine 层。示例：

```python
breakdown = row.get("score_breakdown")
raw = row.get("raw_factors")
signals.append(TradeSignal(
    ...
    score_breakdown=breakdown if pd.notna(breakdown) else None,
    raw_factors=raw if pd.notna(raw) else None,
))
```

---

### C-11：`UniverseFilter.filter()` docstring 未记录新增 `financials_history` 参数

**位置**：`engine/universe.py:33–41`

```python
def filter(
    ...
    financials_history: pd.DataFrame | None = None,   # P5-PRE-4 新增
) -> pd.Index:
    """
    参数：
      stock_info   — ...
      financials   — ...
      daily_quotes — ...
      today        — ...
      calendar     — ...
      min_avg_amount — ...
      # financials_history 未在 docstring 中描述
    """
```

**修复**：在 docstring 参数列表中补充：
```
financials_history — MultiIndex(ts_code, report_period)，含 net_profit_yoy；
                     非 None 时 F-5 执行两期检查（P5-PRE-4 恢复），None 时降级为单期。
```

---

## 设计整合性专项检查

### P5-PRE 前置任务对标

| 前置任务 | 设计要求 | 实现状态 |
|---------|----------|---------|
| P5-PRE-1：DataService 新增两个方法 | `refresh_industry_classification` / `refresh_financials_full` | ✓ 已实现；但 `refresh_financials_full` 存在 upsert 覆盖问题（C-04） |
| P5-PRE-2：MonthlyScheduler 季度财务调度 | `run_quarterly_financial_refresh`，3/6/9/12 月执行 | ✓ 已实现，按月份过滤逻辑正确 |
| P5-PRE-3：退役 backfill_td123.py | 文件删除 | ✓ 已删除，确认不存在 |
| P5-PRE-4：F-5 恢复两期检查 | `get_latest_n_financials`(n=2) 替代单期 | ✓ 实现正确；`financials_history` 参数传递链完整（ScoringService → UniverseFilter） |
| P5-PRE-4：F-7 恢复 20 日均量 | `avg_amount` 列优先，降级 `amount` | ✓ 实现正确；`avg_amount` 传递链完整 |

### Engine 层对标

| 设计项 | 规格 | 实现状态 |
|--------|------|---------|
| `RiskParams` 所有字段 | 9 个字段及默认值 | ✓ 完全一致 |
| `TradeSignal` 所有字段 | 14 个字段 | ✓ 完全一致 |
| `PositionConfig` 所有字段 | 7 个字段及默认值 | ✓ 完全一致 |
| `RiskWarning` frozen dataclass | 4 字段，frozen=True | ✓ 符合 |
| `SignalGenerator.generate()` 签名 | 设计缺少 trade_date 参数 | ✗ 实现多一参数（C-08，设计遗漏，实现正确，需同步文档） |
| BUY 信号：score > 80（严格大于） | SDD §9.1 | ✓ 实现 `score <= buy_threshold → continue` |
| BUY 信号：非停牌、非涨停 | SDD §9.1 | ✓ 实现 |
| BUY 信号：avg_amount ≥ 500万 | SDD §9.1 | ✓ 实现；avg_amount=NaN 时跳过（宽松策略） |
| SELL 信号：score < 40 或止损 | SDD §9.2 | ✓ 实现 |
| 持有区间 [40,80] 无信号 | SDD §9.5 | ✓ 实现 |
| 加仓规则（浮盈>0 或偏离≤10%+非下跌） | SDD §10.1 | ✓ 实现 |
| signal_strength：score≥90→STRONG, 80-89→MODERATE | SDD §9.1 | ✓ 仅买入信号填充 |
| t1_warning 买入信号必填 | SDD §9.3 | ✓ 实现 |
| `PositionSizer.suggest()` 有效仓位 = max_total × market_multiplier | SDD §10.1 | ✓ 实现 |
| `PositionSizer.suggest()` 可用仓位扣减最低现金 | SDD §10.1 | ✓ 实现 |
| `PositionSizer.suggest()` 多信号间互相扣减 | SDD §10.1 未明确 | ✗ 未实现（C-06） |
| `RiskChecker` 单股集中度 BLOCK | SDD §10.2 | ✓ 实现；但对 suggested_pct=None 有误报（C-05） |
| `RiskChecker` 行业集中度 BLOCK | SDD §10.2 | ✓ 实现 |
| `RiskChecker` 回撤 WARN | SDD §10.2 | ✓ 实现；Phase 5 传 None 跳过，降级说明保留 |
| Engine 层严格无 IO | CLAUDE.md | ✓ signal.py / position.py / risk.py 均无 DB/网络/文件调用 |

### Service 层对标

| 设计项 | 规格 | 实现状态 |
|--------|------|---------|
| `save()` BLOCK 信号移除 | SDD §10.2，`risk_warnings` 参数 | ✓ 实现 |
| `save()` WARN 追加 reason | SDD §10.2 | ✓ 实现；含 ACCOUNT 级 WARN 追加到所有 BUY |
| `save()` 写入 SignalScoreSnapshot | 设计 §4.1 | ✗ 未实现（C-02） |
| `save()` SUPERSEDED 状态设置 | 设计 §4.1 | ✗ 未实现（C-03） |
| `save()` 保留已有 VIEWED/ACTED 状态 | 隐含语义 | ✗ 会覆盖（C-03） |
| `update_status()` 合法转换 NEW→VIEWED/ACTED, VIEWED→ACTED | SDD §9.4 | ✓ 实现；`_VALID_TRANSITIONS` 与设计一致 |
| `update_status()` 不存在返回 404 | HTTP 语义 | ✗ 实际返回 400（C-01） |
| `expire_old_signals()` ttl_days=3 | 设计 §4.1 | ✓ 实现 |
| `get_lineage()` 返回信号+快照 | 设计 §4.1 | ✓ 接口正确；但快照始终 None（C-02 副作用） |

### API 层对标

| 设计项 | 规格 | 实现状态 |
|--------|------|---------|
| GET `/signals` | trade_date/signal_type/status 参数 | ✓ 符合；路由尾斜杠问题（C-07） |
| GET `/signals/history` | ts_code/signal_type/status/limit/offset | ✓ 符合 |
| PATCH `/signals/{id}/status` | 仅允许 VIEWED/ACTED，422 校验 | ✓ 实现；但 not-found 返回 400（C-01） |
| GET `/signals/{id}/lineage` | 含评分快照 | ✓ 实现；快照始终 None（C-02 副作用） |
| `get_signal_service` 在 `deps.py` | CLAUDE.md 规范 | ✓ 符合（已按规范放在 deps.py:72） |
| 统一响应格式 `{"code":0,...}` | CLAUDE.md | ✓ 全部端点一致 |
| JWT 认证 | 设计 §6 | ✓ 所有端点均有 `Depends(get_current_user)` |

### Repository 层对标

| 设计项 | 是否实现 |
|--------|---------|
| `upsert_signals` | ✓ |
| `get_signals_by_date` | ✓ |
| `get_signal_history` | ✓ |
| `update_signal_status` | ✓ |
| `get_signal_by_id` | ✓ |
| `get_signal_snapshot` | ✓ |
| `upsert_signal_snapshots` | ✓（已实现但目前不被调用，见 C-02） |
| `expire_signals_before` | ✓ |
| `get_positions_by_account` | ✓ |
| `get_account_by_id` | ✓ |
| `get_default_account` | ✓ |
| `get_avg_amount` | ✓ |
| `get_latest_n_financials` | ✓ |

---

## 设计整合性已验证通过的关键项

- **Engine 层 IO 隔离**：`signal.py` / `position.py` / `risk.py` 均无 DB/网络/文件调用，符合 Engine 层纯函数规范
- **RiskParams / PositionConfig 默认值**：与 SDD §9/§10 所有参数完全一致
- **买入/卖出信号逻辑**：score 阈值、止损比例、加仓规则、涨停/停牌过滤均与 SDD §9.1/§9.2/§10.1 一致
- **状态机转换约束**：`_VALID_TRANSITIONS` 与设计 §4.1 所列转换完全一致，EXPIRED/SUPERSEDED 均不可向外转换
- **SignalService 依赖注入**：`get_signal_service` 正确放在 `deps.py`，遵守 CLAUDE.md 规范
- **F-5 两期检查逻辑**：`_is_consistently_losing` 函数语义正确（全负才过滤，单正期则保留）
- **F-7 avg_amount 列传递链**：ScoringService._build_filter_snapshot → UniverseFilter.filter 传递路径完整且正确
- **backfill_td123.py 已删除**：P5-PRE-3 完全交付

---

## 修复优先级汇总

| 编号 | 位置 | 级别 | 描述 | 目标 |
|------|------|------|------|------|
| C-01 | `signal_service.py:75`；`signals.py:68` | **P1** | `update_status` not-found 返回 400，改为 404 | Phase 5 验收前 |
| C-02 | `signal_service.py:97–112` | **P1** | 快照写入跳过，INT-SVC-04 DoD 无法通过；修复写入路径或更新 DoD | Phase 5 验收前 |
| C-03 | `signal_service.py:53–60`；`upsert_signals` | P2 | save() 覆盖 VIEWED/ACTED 状态；补充 SUPERSEDED 逻辑 | Phase 7 前 |
| C-04 | `data_service.py:refresh_financials_full` | P2 | 先后 upsert fin_df/bal_df 导致字段覆盖为 NULL | Phase 5 完成后立刻修复 |
| C-05 | `engine/risk.py:62` | P2 | suggested_pct=None 信号误报 BLOCK | Phase 7 前 |
| C-06 | `engine/position.py:55–78` | P2 | 批量信号已分配仓位未扣减 | Phase 7 前或设计文档明确语义 |
| C-07 | `api/v1/signals.py:28` | P2 | GET /signals 路由尾斜杠问题 | Phase 5 验收前 |
| C-08 | `engine/signal.py:59`；`phase5_signals.md §3.2` | P2 | generate() 签名比设计多 trade_date；更新设计文档 | 下次设计文档更新时 |
| C-09 | `signal_service.py` | P3 | ValueError 语义不清（not-found vs 非法参数） | 可迭代 |
| C-10 | `engine/signal.py:103–117` | P3 | generate() 不填充 score_breakdown/raw_factors | Phase 7 前 |
| C-11 | `engine/universe.py:33–41` | P3 | filter() docstring 缺少 financials_history 文档 | 可迭代 |

---

## DoD 验收状态

| DoD 项 | 状态 | 说明 |
|--------|------|------|
| D-01 P5-PRE-1~4 全部完成 | ✓（部分） | PRE-1~4 逻辑已实现；但 PRE-1 的 upsert 合并有 C-04 数据完整性问题 |
| D-02 Engine 层严格无 IO | ✓ | signal/position/risk 无 IO 操作 |
| D-03 单元测试 22 个通过（SGN/PSZ/RSK/URF-*r） | 待验证 | 设计测试用例与实现逻辑一致；C-05/C-06 可能导致 RSK/PSZ 部分用例行为与预期偏差 |
| D-04 E2E 测试 6 个通过（SAPI-01~06） | 待验证 | C-01 C-07 可能导致 SAPI-03 行为偏差 |
| D-05 集成测试 5 个通过（INT-SVC-01~05） | ✗ | INT-SVC-04（快照血缘）无法通过（C-02） |
| D-06 ruff 0 错误 | 待验证 | — |
| D-07 4 个 API 端点 Swagger 可测 | 待验证 | C-07 尾斜杠可能影响 /signals 端点 |
| D-08 expire_old_signals 可独立调用 | ✓ | 已实现，不依赖 DailyPipeline |
| D-09 Phase 1–4 回归测试 141 个通过 | 待验证 | _build_filter_snapshot 返回值变更（2-tuple→4-tuple）须确认所有调用点已更新 |
