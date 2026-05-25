# Phase 14 设计评审报告（v1.0）

- 评审日期：2026-05-25
- 评审对象：`docs/design/phases/phase14_account_integrity.md` v1.0（2026-05-23 创建）
- 评审范围：8 项 scope（§14-1 ~ §14-8）+ §1.3 启动核查 + §11 依赖图 + §12 DoD
- 依据文档：
  - SDD §7.4 / §7.7.1 / §11；system_design v1.8 §9 Phase 14 行（2026-05-22 锁定 8 子项）
  - Phase 11 实施评审 §6.3 第 8/9 项；Phase 12 实施评审 §3 P1-2 + §4 P2-2/P2-3；Phase 13 实施评审 §8 修订追踪表 P2 6 项
  - CLAUDE.md §5 TDD 工作流 / §10 phase 治理 / §11 问题处理总原则 / §11.1 推迟项防丢失三链
- 评审基线（pre-implementation 设计期，未跑代码）：
  - 当前 main HEAD `9ff9cb3`；commits `d3dc806` (R13 P0/P1 补丁批) / `5e94d44` (§11.1 防丢失流程) / `29ed870` (§11.1 沉淀) 已合入
  - 实施代码核查（仅设计真实性核对）：
    - `services/attribution_service.py:83-100` ✓ 已用 `calendar.get_prev_trade_date` 严格交易日
    - `services/factor_monitor_service.py:437-438` ✗ rolling_icir_state 仍 `timedelta(days=272/20)` 日历天
    - `models/account.py:92` ✓ 实际表名 `FundFlow` / `fund_flow`（**非** 设计文档所述 `cash_flow`）
    - `services/account_service.py:296` ✓ 现 deposit 签名 `(account_id, amount, trade_date, note)`
    - `engine/backtest/engine.py:130-271` ✓ BacktestEngine `_scorer` 是 `Scorer`（engine 层），仅有 `aggregate_legacy/aggregate`，**没有** `score_universe`；`score_universe` 在 `ScoringService:399`（service 层 async + 需 session）
    - `data/repository.py:704-788` ✓ candidate_pool repo 仅有 `upsert_candidate_pool` / `bulk_upsert_candidate_pool` / `get_pool`，**没有** `get_existing_candidate_pool_dates`

---

## 0. 评审结论

**有条件通过（P1 必修后才可启动 TDD）** —— 设计文档结构完整，scope 与 system_design §9 + 三份历史评审报告对齐良好；但 §3（§14-1 deposit 幂等）与 §5（§14-3 BacktestEngine 真 5 步）存在**实施会撞墙的事实错误**，必须修订设计文档 v1.1 后再启动 TDD；另有 1 项工作项重复（R12-P1-2 已实施完毕）需删除。

**亮点**：
- §1.3 启动核查 + §11.1 三链 grep 完整，R13-P2 6 项 / R12-P1-2/AttrBackfill/32767 共 3 项均有正向引用 ✓
- §11 实施顺序图把 5y 回填长任务（§14-2，50-80h）摆在依赖关键路径正确位置
- §13 显式列出未在 Phase 14 收口的项目（Phase 15 RC / V1.5-A R13-P3）+ 归属清晰 ✓
- §6 §14-4 ICIR 校准最小集呼应 SDD §2.1 V1.0 RC 前要求

**风险等级标记**：
- **P0**：阻断核心功能或暴露生产风险（无）
- **P1**：实施时会直接撞墙（设计与代码 / 架构事实不符；本评审 3 项）
- **P2**：设计瑕疵 / 文档错误（本评审 4 项）
- **P3**：建议改进（本评审 6 项）

---

## 1. 启动核查核对

| 核查项 | 结论 | 备注 |
|--------|------|------|
| §9 Phase 14 行 8 子项与本文档 §1.2 表一致 | △ | system_design §9 估算 `~3-5 pd`，本文档 §1.2 估算 `~5-8 pd`——估算扩张未在 §9 同步（见 P2-3）|
| 模块孤儿（§3/§5）| ✓ | Phase 14 不新增模块；仅扩展 AccountService/ScoringService/BacktestEngine/FactorMonitorService/AttributionService |
| 端点孤儿（§6）| ✓ | 仅 deposit/dividend 既有端点行为扩展 + 1 个内部 CLI 脚本 |
| 跨 phase stub 标注 | ✓ | §3.3 兼容性段落明示旧无 key 行的 partial unique 语义 |
| `R13-P2-\d+` 跨三处 grep | ✓ | system_design §9 列 6 项 + phase14 §9.1~9.6 全展开 + reviews/phase13_implementation_review §8 表 |
| `R12-P[12]-\d+` 跨三处 grep | △ | R12-P1-2 已在 Phase 13 启动核查阶段实施完成（详见 §1.4），phase14 §10.1+§7.2+§11 依赖图三处仍把它列为待实施 → 工作项重复（见 P1-3）|
| R13-P3 5 项 → V1.5-A 链路 | ✓ | `v1_5_roadmap.md` §4.5 已有完整列项 |
| Phase 15 RC 验收前向引用 | ✓ | §13 明示 4 项保留至 Phase 15 |

### 1.4 R12-P1-2 现状核查（实证）

实测 `attribution_service.py:86-100`：
```python
if self._calendar is not None:
    try:
        start = self._calendar.get_prev_trade_date(
            month_end, n=20 * self._lookback_months,
        )
    except ValueError as exc:
        logger.warning("attribution_lookback_calendar_insufficient: %s, fallback to ...", exc)
        start = month_end - timedelta(days=int(self._lookback_months * 30.5))
else:
    # 【降级说明】calendar 未注入（单元/集成测试路径）→ 用日历天近似
    start = month_end - timedelta(days=int(self._lookback_months * 30.5))
```

注释明示 "Phase 13 启动核查阶段修复（评审 P1-4 + Phase 12 实施评审 P1-2）"。**R12-P1-2 已交付**——属 Phase 14 §14-8.1 多余列项。

---

## 2. P0 必修（0 项）

无。

---

## 3. P1 必修（3 项；实施时会直接撞墙）

### P1-1：§3.2 表名错（`cash_flow` 不存在，实际是 `fund_flow`）+ schema/签名与现状不一致

**证据**：

1. `alembic/versions/0001_initial_schema.py:319-330`：创建的表名是 `fund_flow`，索引 `idx_fund_flow_account_date`；
2. `models/account.py:92` `class FundFlow(Base): __tablename__ = "fund_flow"`；
3. `schemas/account.py:82` `class FundFlowCreate(BaseModel)`；
4. `services/account_service.py:296`：现 deposit 签名 `(account_id, amount, trade_date, note)`，**没有** `occurred_at` / `notes` / `ts_code`；返回类型是 `FundFlow`，不是 `CashFlow`。

设计文档 §3.2 写：

```python
# alembic 0013 给 `cash_flow` 表加 `idempotency_key VARCHAR(64) NULLABLE` + 唯一索引
class DepositRequest(BaseModel):
    amount: Decimal
    occurred_at: date | None = None
    notes: str | None = None
    ts_code: str | None = None
    idempotency_key: str | None = Field(None, max_length=64)
```

```python
if idempotency_key is not None:
    existing = await repo.find_cash_flow_by_idempotency(...)
```

**问题**：
- ❌ 表名错：执行 alembic 0013 时 `ALTER TABLE cash_flow ADD COLUMN ...` 会 `relation "cash_flow" does not exist`；
- ❌ schema 字段名错：`DepositRequest` 把 `trade_date` 写成 `occurred_at`、`note` 写成 `notes`，与 deposit 既有签名整体冲突，前端表单引用也会断；
- ❌ DepositRequest 加 `ts_code` 字段**业务上不正确**：deposit = 入金，不绑定股票；`ts_code` 仅 `record_dividend` 需要（分红是按持仓股票分派的）。设计文档把 deposit 和 dividend 混为一谈；
- ❌ Repository 调用 `find_cash_flow_by_idempotency` 方法签名错——既然实际表是 `fund_flow`，方法名应是 `find_fund_flow_by_idempotency`。

**影响**：

按设计 v1.0 实施 → alembic 0013 启动失败 / Pydantic schema breaking change → AccountService 集成测试全红 / 前端 OnboardingView+仓位页 deposit 表单 axios payload 字段对不上 → §14-1 UT/INT/E2E 全部无法跑通。

**修复（设计文档 v1.1 必改）**：

1. 全文 `cash_flow` → `fund_flow`；
2. `DepositRequest` 改为既有签名扩展 + idempotency_key：
   ```python
   class DepositRequest(BaseModel):
       amount: Decimal = Field(..., gt=0)
       trade_date: date | None = None        # 既有签名兼容
       note: str | None = None               # 既有签名兼容
       idempotency_key: str | None = Field(None, max_length=36)
       # 注：deposit 不绑定 ts_code，移除该字段
   ```
3. 单独定义 `DividendRequest` 扩展 record_dividend 幂等（保留 ts_code）；
4. Repository 方法名 `find_fund_flow_by_idempotency`（动词主语对齐表名）。

**充分理由检查（CLAUDE.md §11）**：4/4 不满足。**禁推迟，设计期改**。

---

### P1-2：§5.2 `scorer.score_universe()` 在 Scorer 类不存在，且 ScoringService.score_universe 违反 BacktestEngine no-IO 规约

**证据**：

1. `engine/scorer.py:91-401`：`Scorer` 类公开方法仅 `aggregate(market_state, strategy_scores_dict)` / `aggregate_legacy(...)`，**没有** `score_universe`；
2. `engine/backtest/engine.py:121,130,271`：`BacktestEngine.__init__(scorer: Any)`，实际持有的是 `Scorer` 实例（engine 层），不是 `ScoringService`；
3. `services/strategy_service.py:399`：`score_universe` 是 `ScoringService` 的方法：
   ```python
   async def score_universe(
       self,
       session: AsyncSession,        # 需要 DB session
       trade_date: date,
       universe: list[str],
       market_state: MarketStateEnum,
   ) -> list[CompositeScore]:
   ```
4. CLAUDE.md §6 "Engine 层（`engine/`）严格无 IO（数据库、文件、网络），只做纯函数计算"。

设计文档 §5.2 写：

```python
result = scorer.score_universe(
    snapshot, market_state, scoring_config=cfg,
)  # 走真 5 步
composite_z = result.composite_z
composite_pct = result.composite_pct_in_market
```

**问题**：

- ❌ **方法不存在**：`Scorer.score_universe` 未定义；BacktestEngine 持有 `_scorer` 是 `Scorer`，调不到 `ScoringService.score_universe`；
- ❌ **架构冲突**：即使 BacktestEngine 通过 `BacktestService` 转持 `ScoringService` 引用，`score_universe` 是 async + 需要 AsyncSession + 内部走 `factor_monitor.get_active_weights(session, ...)`——把它塞入 `engine/backtest/engine.py::BacktestEngine.run`（CLAUDE.md §6 要求 no-IO 纯函数）违反层级规约；
- ❌ **签名形不符**：`score_universe` 输入是 `universe: list[str]`，输出是 `list[CompositeScore]`（每股一行），不是 `result.composite_z`（DataFrame）；现 BacktestEngine 在第 252 行已是 `strategy_scores_dict[s.name] = score` 形式（各策略 already-scored），不是 universe 传入。
- ❌ **`scoring_config=cfg` 参数不存在**：`ScoringService.score_universe` 当前签名无 scoring_config 入参。

**影响**：

`§14-3` 整个章节的实施路线（"调 scorer.score_universe 走真 5 步"）**是死路**。强行实施 → 把 ScoringService 注入 BacktestEngine → 违反 CLAUDE.md §6 → 单元测试需 mock session → 真机回测 60s/日 × 1210 日 → 与 Phase 11 收尾 "BacktestEngine 走 aggregate_legacy 不影响实盘 critical path" 的临时降级理由形成主权矛盾。

**修复方案（建议设计文档 v1.1 明示 3 选 1）**：

**方案 A（推荐，与 CLAUDE.md §6 兼容）**：把 Phase 11 5 步管线的 **engine 层 5 步算法**（Winsorize + 行业+市值中性化 + Z-score + Gram-Schmidt 正交化 + 三层输出）从 `ScoringService` 抽出为 `engine/scoring/pipeline.py` 纯函数（输入 strategy_factor_dfs + industry/market_cap 切片 + active_weights snapshot；输出 composite DataFrame）。
- `Scorer` 新增公开方法 `aggregate_pipeline(strategy_factors, market_state, weights_snapshot, industry, market_cap)` —— engine 层纯函数；
- BacktestEngine 由 `BacktestService` 在 outer session 预查询好 5y `active_weights` 时序 + industry/market_cap PIT 切片，塞入 `BacktestDataBundle`；
- BacktestEngine 主循环用 weights_snapshot[trade_date] 查找对应日的 active_weights → 调 `_scorer.aggregate_pipeline(...)` → 拿到 composite DataFrame；
- universe < 30 时降级 `aggregate_legacy`。

**方案 B（最简，工作量小）**：保留 BacktestEngine 走 `aggregate_legacy` 不变，把 §14-3 改为"BacktestEngine 5 步管线接入 = 临时降级转正"（即承认 BacktestEngine 永远走 legacy），把 §14-4 ICIR 校准最小集改为只通过 DailyPipeline 历史回放（§14-2 5y 跑完 pipeline 写 candidate_pool 自动有 5y 真 5 步评分）+ 离线 IC 时序聚合分析。
- 代价：BacktestEngine 与实盘 critical path 评分链路永久双轨，回测信号与实盘信号无法严格对齐。

**方案 C（推迟）**：§14-3 移至 V1.5-A 回测引擎深化。
- 代价：违反 CLAUDE.md §11——"Phase X 一起做更高效"伪推迟情形之一；且方案 A 工作量明确（5 步算法已在 strategy_service.py 写过一次，抽出 ~1pd），不属"大重构跨 phase"。

**评审建议**：**采方案 A**——已有 Phase 11 5 步实施基础，抽出 engine 层纯函数本就符合架构方向；设计文档 v1.1 用方案 A 重写 §5.2 + §5.3 测试。

**充分理由检查**：4/4 不满足。**禁推迟，设计期改**。

---

### P1-3：§10.1 + §11 依赖图把 R12-P1-2（attribution_service 严格交易日）作为待实施工作项，但实际已在 Phase 13 阶段完成

**证据**：

1. `services/attribution_service.py:82-100` 明确注释 "Phase 13 启动核查阶段修复（评审 P1-4 + Phase 12 实施评审 P1-2）"——R12-P1-2 已用 `calendar.get_prev_trade_date(month_end, n=20 * lookback_months)` 严格交易日 + fallback；
2. Phase 13 实施评审 §1 启动核查："R12-P1-2 是否真改为严格交易日 ✓ —— `services/attribution_service.py:88-89` 已用 `calendar.get_prev_trade_date(...)` "

phase14 设计文档：

- §1.3 启动核查表："`grep R12-P[12]-\d+` 同上 | R12-P1-2 + R12-AttrBackfill + R12-32767 共 3 项全部进入 §10 ✓"——R12-P1-2 被列入 §10；
- §7.2 ICIR 窗口段："同源问题在 `services/attribution_service.py:79`：`timedelta(days=int(self._lookback_months * 30.5))`，Phase 12 评审 R12-P1-2 已标延后 Phase 14 同批"；
- §10.1：标题 "R12-P1-2：AttributionService 切严格交易日"；
- §11 实施顺序图："§14-8.1 R12-P1-2 严格交易日 ←─ §14-5 ICIR 窗口同批"。

**问题**：

设计文档基线时间是 2026-05-23，但 Phase 13 启动核查（2026-05-21）已经把 attribution 严格交易日实施了——设计作者**未跑 grep 验证实际代码状态**，把已完成的工作项重列入待实施 scope。

**影响**：

- 工作项重复：§14-5（factor_monitor）+ §14-8.1（attribution）两次 ICIR 严格交易日改动 → 实际只剩 §14-5 一项真正待修；
- 估算虚高：§1.2 表 "14-8 = 0.4pd"（含 P1-2 + AttrBackfill + 32767 注释），剥离 P1-2 后真实工作量约 0.2-0.3pd（仅 AttrBackfill + 注释）；
- 文档治理违规：CLAUDE.md §10 "禁止重复实施已交付项目"；
- DoD 误判风险：§12 DoD "Phase 12 评审报告 §3/§4 R12-P1-2/P2-2/P2-3 勾选"——P1-2 应该在 Phase 13 评审报告里勾，不是 Phase 14。

**修复（设计文档 v1.1 必改）**：

1. §1.3 启动核查表 R12 行改为 "R12-AttrBackfill + R12-32767 共 2 项进入 §10 ✓（R12-P1-2 已在 Phase 13 启动核查完成，无需重做）"；
2. §7.2 删 "同源问题在 attribution_service.py:79" 段；
3. §10.1 整节删除，§14-8 改为 "AttrBackfill + 32767 注释"两项；
4. §11 实施顺序图删 "§14-8.1 R12-P1-2 严格交易日 ←─ §14-5 同批"，§14-5 改为独立分支；
5. §12 DoD R12-P1-2 项删除；
6. Phase 13 实施评审报告 §1 已勾的 R12-P1-2 ✓ 不再重复标。

**充分理由检查**：4/4 不满足。**禁推迟，设计期改**。

---

## 4. P2 实施期收口（4 项）

### P2-1：§4.2.1 `get_existing_candidate_pool_dates()` repo 方法不存在 + "双表交集"措辞错

**证据**：

`data/repository.py:704-788` 列 candidate_pool repo 方法仅有 `upsert_candidate_pool` / `bulk_upsert_candidate_pool` / `get_pool`；**没有** `get_existing_candidate_pool_dates`。

设计文档 §4.2.1 第 4 步："断点续传：`get_existing_candidate_pool_dates()` 双表交集，仅补缺失日"。

**问题**：

- ❌ 方法不存在：需要在 §4.2 显式新增 repo 方法 + UT；
- ❌ "双表交集" 措辞错：candidate_pool 是单表，无交集语义（"双表交集"是 `get_fully_ingested_dates` 的语义——daily_quote ∩ financial_data 双表）；可能是从 ingest_history 断点续传段落复制时未改。

**修订**：

§4.2.1 第 4 步改为：

> 断点续传：新增 `repository.get_existing_candidate_pool_dates(start, end) -> set[date]`（返回 candidate_pool 表 [start, end] 区间已写入的 trade_date 集合），脚本主循环跳过已存在日。

§4.3 测试表追加 `UT-P14-2-02 repository.get_existing_candidate_pool_dates 返回集合正确`。

### P2-2：§6 §14-4 ICIR 校准最小集 DoD 缺量化阈值

**证据**：

§6.2.3 滑点敏感性："断言 `sharpe(slippage=0.0005) > sharpe(slippage=0.005)`（基本单调性）"——只断言相对方向，未给绝对差值阈；
§6.3 测试表："真机-P14-4 manual：跑 5y 全量后 PNG 人工核对"——"人工核对"非可执行 DoD；
§6.2.1 IC 时序量级："人工核对 ic_mean 量级是否落 (-0.1, 0.1) 合理区间"——这个阈值合理，但属"建议"未落 DoD。

**问题**：

§14-4 ICIR 校准最小集是 SDD §2.1 V1.0 RC 前要求的硬性验收，但 DoD 全是 "人工核对" / "断言方向"，导致 Phase 14 收尾时无法判定是否达标，会演变成 Phase 15 RC 阶段返工。

**修订**：

§6.3 测试表补：

| 编号 | 类型 | DoD（可执行）|
|------|------|--------------|
| 真机-P14-4-1 | manual | 5y monthly aggregate CSV 中 ≥ 85% trade_date 月度 ic_mean ∈ [-0.1, 0.1]，且 4 策略 × 3 state = 12 组合中至少 8 组 sample_size ≥ 60 |
| 真机-P14-4-2 | manual | 4 策略 × 3 state heatmap CSV 中至少 6 组 ic_mean 与 0 显著差异（\|t-stat\| > 2）|
| 真机-P14-4-3 | manual | 5y 三档滑点回测 sharpe(0.0005) - sharpe(0.005) ≥ 0.05（单调性最小差值）|

### P2-3：system_design §9 Phase 14 行 pd 估算未与本文档 §1.2 同步

**证据**：

`system_design.md:1364`："**账户资金链 + 5y candidate_pool 回填 + ICIR 历史回算 + BacktestEngine 真 5 步（V1.0 收尾，~3-5 pd）**"
`system_design.md:1374`："Phase 14 ~3-5 pd（RM-13 + 回测 IC 验证最小集）"

phase14 §1 引言："估算：~5-8 pd"；§1.2 表合计 "~5-8 pd"。

**问题**：

- §9 估算 3-5pd 出自 2026-05-14 V1.0 重新定位时仅含 RM-13 + 回测 IC 验证；2026-05-22 锁定的 8 子项含 R13-P2 + R12 + ICIR 严格交易日 + 共表拆分 → 工作量扩张但 §9 估算未回写；
- CLAUDE.md §10 文档治理："禁止 phase 实际范围与 system_design §9 不一致时跳过 §9 更新"——本设计文档 §1.3 启动核查未发现该不一致。

**修订**：

system_design v1.9 §9 Phase 14 行 / §9 注尾估算合计两处估算改为 ~5-8 pd，并在修订历史追加 "v1.9 Phase 14 估算同步"。

### P2-4：§14-1 deposit 幂等并发竞态保护未明示

**证据**：

§3.2 实施伪代码：
```python
if idempotency_key is not None:
    existing = await repo.find_cash_flow_by_idempotency(account_id, idempotency_key)
    if existing is not None:
        return existing
# else: INSERT
```

**问题**：

- "先查 + 后写" 模式在并发请求下有 race window：req A 查询无 → req B 查询无 → A INSERT 成功 → B INSERT 触发 partial unique 约束抛 `IntegrityError`；
- partial unique 约束保证最终一致性（最多 1 行），但 service 层未捕获 `IntegrityError` → 返回 500 而非幂等 200；
- §3.4 测试 INT-P14-1-01 "真 DB partial unique 约束（同 key 重复 INSERT raise）" 验证了"raise"，但未验证 service 层 "catch IntegrityError → 重查返回原记录"。

**修订**：

§3.2 实施增加并发处理：
```python
if idempotency_key is not None:
    existing = await repo.find_fund_flow_by_idempotency(account_id, idempotency_key)
    if existing is not None:
        return existing
try:
    flow = await self._do_deposit(...)
except IntegrityError as exc:
    if "uq_fund_flow_idempotency_key" in str(exc):
        # 并发竞态：另一请求已先 INSERT，重查返回
        await self._session.rollback()
        return await repo.find_fund_flow_by_idempotency(account_id, idempotency_key)
    raise
```

§3.4 测试补 `INT-P14-1-02 concurrent_deposit_same_idempotency_returns_same_flow_id`（asyncio.gather 2 个 deposit 同 key → 同 flow_id）。

---

## 5. P3 建议（6 项）

| 编号 | 范围 | 建议 |
|------|------|------|
| P3-1 | §3.2 idempotency_key 长度 | `VARCHAR(64)` 偏长；UUID4 仅 36 字符（含 4 个 `-`）。建议 `VARCHAR(36)` 与 UUID4 一致；可再加 `CHECK (length(idempotency_key) <= 36)` 防止注入超长字符串 |
| P3-2 | §4 5y 回填 graceful shutdown | §4.4 风险表只标 "nohup 后台 + 进度推 Redis + idempotent"，未含 SIGTERM 处理。建议 backfill_candidate_pool.py 注册 SIGINT/SIGTERM handler：收到信号时跳过新 trade_date 启动 + 等待当前 trade_date per-day session commit/rollback 后退出（防止半 commit）|
| P3-3 | §6 §14-4 输出路径 | 设计文档未指定 IC 时序 CSV / heatmap PNG 落盘位置。建议落 `backend/var/diagnostics/phase14/ic_*.csv|png`（与既有 `backend/scripts/output/` 区分，明示是诊断中间产出） |
| P3-4 | §5 §14-3 universe<30 阈值常量化 | "最小 winsorize 样本 = 30" 应该是 ScoringService.score_universe 已定义的常量，§5.2 直接 import 引用而非硬编码 30；同源风险 R14-OPEN-3 |
| P3-5 | §12 DoD "新增 API-102~104（若有新端点）" 措辞 | deposit 幂等是同端点行为扩展，不是新增端点。建议改为 "API-?? 现 deposit/dividend 冒烟扩展幂等用例（同 key 调用 2 次 → 200 + 同 flow_id）"，避免与"新端点新增"语义混淆 |
| P3-6 | §1.3 "全部 8 子项纳入本 phase，无推迟" 措辞 | 与 §13 "未在 Phase 14 收口的相关项目"形式上矛盾——§13 列出的 Phase 15 RC 项目（5y 真机验收 / 30 日完整版 / 覆盖率 / 文档校核）实际是依赖 Phase 14 完成的下游验证项，非"推迟"。建议 §1.3 改 "全部 8 子项纳入本 phase 实施；Phase 15 RC 覆盖下游真机验证（详见 §13）" |

---

## 6. 设计亮点（保留 / 推广）

- **§1.3 启动核查 + §11.1 三链 grep 模板**：CLAUDE.md §11.1 沉淀的"链 A/B/C"防丢失流程在本文档执行良好（仅 R12-P1-2 一项错列），明显优于 Phase 11/12 设计期；后续 phase 设计文档可直接复用 §1.3 表头 + grep 指令模板。
- **§11 实施顺序图**：把 §14-1（独立）/§14-7（独立）/§14-5+§14-8.1（同源同批）/§14-6（不阻塞）/§14-2（耗时最长）→ §14-3 → §14-8.2 → §14-4 排序合理，5y 回填长任务摆在依赖关键路径正确位置。
- **§13 显式列推迟项归属**：未在 Phase 14 收口的 Phase 15 RC + V1.5-A R13-P3 + V1.5+ + V2.0 四类项目分别有归属，符合 CLAUDE.md §11.1 "推迟项三链必填"。
- **§4.2.2 ICIR 历史回算复用 apply_monthly_rebalance**：识别到"Phase 13 R13-P1-2 已接入 check_persistent_decay → 跑历史 month_end 即可顺带产出"，避免另写独立脚本。
- **§14 风险表覆盖关键 OPEN-1 ~ OPEN-5**：50-80h 长任务 / 共表方案选 / universe 阈值 / UUID 时机 / ICIR 时延估算偏乐观——5 类风险均与"实际执行卡点"匹配。

---

## 7. 充分理由检查汇总（CLAUDE.md §11）

| 等级 | 项 | 4 类充分理由 | 处置 |
|------|---|--------------|------|
| P1 | P1-1 表名 / schema 错 | 4/4 不满足 | **设计期改 v1.1** |
| P1 | P1-2 BacktestEngine score_universe 不存在 + 架构冲突 | 4/4 不满足；方案 A 已有 Phase 11 实施基础 | **设计期改 v1.1（采方案 A）** |
| P1 | P1-3 R12-P1-2 工作项重复 | 4/4 不满足 | **设计期改 v1.1** |
| P2 | P2-1 get_existing_candidate_pool_dates 不存在 | 部分 "实施期补 repo 方法可接受" | 实施期收口 |
| P2 | P2-2 §14-4 DoD 缺量化阈值 | "验收标准未定义" 满足部分 | 设计文档 v1.1 同步补 |
| P2 | P2-3 §9 估算未同步 | 4/4 不满足（文档治理硬要求）| **同步 v1.1 时回写 §9** |
| P2 | P2-4 并发竞态保护未明示 | 4/4 不满足 | 设计文档 v1.1 补 IntegrityError 路径 |
| P3 | P3-1 ~ P3-6 | "非必要改进 / 措辞优化" | v1.1 一并修订，无需独立批次 |

---

## 8. 修订追踪表

| 编号 | 等级 | 处置 | 责任 / 截止 | 状态 |
|------|------|------|-------------|------|
| P1-1 | P1 | §3 全文 cash_flow→fund_flow + schema 改"扩既有 FundFlowCreate"（实证发现既有单 schema 共用 deposit/dividend/withdraw，更优于评审建议的双 schema 拆分）+ repo 方法名 `find_fund_flow_by_idempotency` | 设计文档 v1.1 | ✅ fixed @ v1.1（2026-05-25）|
| P1-2 | P1 | §5 重写采方案 A：engine 层新增 `engine/scoring/pipeline.py::run_scoring_pipeline` 纯函数 + `Scorer.aggregate_pipeline()` 公开方法 + BacktestService 预查 5y active_weights/industry/market_cap 入 BacktestDataBundle | 设计文档 v1.1 | ✅ fixed @ v1.1 |
| P1-3 | P1 | §1.3 / §7.2 / §10.1 / §11 / §12 删 R12-P1-2 重复列项；§14-8 改为 AttrBackfill + 32767 注释 2 项；§7.1 加注「attribution 已在 Phase 13 启动核查交付」 | 设计文档 v1.1 | ✅ fixed @ v1.1 |
| P2-1 | P2 | §4.2.1 改 "candidate_pool 单表已写入" 措辞（与 `get_fully_ingested_dates` 双表交集语义区分）+ 显式新增 `get_existing_candidate_pool_dates(start, end) -> set[date]` repo 方法 + UT-P14-2-02 | 设计文档 v1.1 | ✅ fixed @ v1.1 |
| P2-2 | P2 | §6.3 测试表补 3 项量化 DoD：真机-P14-4-1 (≥85% 月份 ic_mean ∈ [-0.1, 0.1] + ≥8 组 sample_size ≥ 60) / -2 (≥6 组 \|t-stat\| > 2) / -3 (sharpe(0.0005) - sharpe(0.005) ≥ 0.05) | 设计文档 v1.1 | ✅ fixed @ v1.1 |
| P2-3 | P2 | system_design v1.9 §9 Phase 14 行估算 ~3-5 pd → ~5-8 pd + §9 注尾合计同步 + 文件名引用更新为 `phase14_account_integrity.md v1.1 ✅` + 修订历史追加 v1.9 条目 | 设计文档 v1.1 同步 | ✅ fixed @ v1.1（system_design v1.9）|
| P2-4 | P2 | §3.2.3 补 IntegrityError 捕获 + rollback + 重查路径；§3.4 加 INT-P14-1-02 concurrent_deposit_same_idempotency_returns_same_flow_id（asyncio.gather 2 个同 key） | 设计文档 v1.1 | ✅ fixed @ v1.1 |
| P3-1 | P3 | idempotency_key VARCHAR(64)→(36) + CHECK 约束（含 pattern `^[A-Za-z0-9_\-]+$` 防注入）| v1.1 | ✅ fixed @ v1.1 |
| P3-2 | P3 | §4.2.1 第 5 步 + §4.4 风险表补 SIGINT/SIGTERM graceful shutdown handler；§10.1 backfill_attribution_history 同款 | v1.1 | ✅ fixed @ v1.1 |
| P3-3 | P3 | §6.2.1/6.2.2/6.2.3 输出落 `backend/var/diagnostics/phase14/` 明示路径 | v1.1 | ✅ fixed @ v1.1 |
| P3-4 | P3 | §5.2.2 函数签名注释明示 "从 ScoringService 既有 `WINSORIZE_MIN_SAMPLES` 常量 import"；R14-OPEN-3 加 "同源对齐" 措辞 | v1.1 | ✅ fixed @ v1.1 |
| P3-5 | P3 | §12 DoD 冒烟措辞改 "现 deposit/dividend 冒烟扩展幂等用例（同 key 调用 2 次 → 200 + 同 flow_id）" | v1.1 | ✅ fixed @ v1.1 |
| P3-6 | P3 | §1.3 改 "全部 8 子项纳入本 phase 实施；Phase 15 RC 覆盖下游真机验证（详见 §13）" | v1.1 | ✅ fixed @ v1.1 |

### 8.1 § R13-P3 / 推迟链路核查

| 链 | 项 | 结论 |
|----|----|------|
| 链 A | Phase 13 实施评审 §8.2 P3 5 项 → V1.5-A | ✓ 在评审报告标 pending |
| 链 B | system_design §9 Phase 14 行 R13-P2 6 项展开 | ✓ |
| 链 C | v1_5_roadmap.md §4.5 R13-P3 5 项展开 + V1.5-A 主题表合并 | ✓ |
| 链 A→B/C | R12-P1-2 已交付，但仍在 phase14 §10 与 §11 列入 | ✅ resolved @ v1.1（P1-3 fixed：§14-8 改为 AttrBackfill + 32767 注释 2 项；§1.3/§7.1/§10/§11/§12 同步删 R12-P1-2 重复）|

### 8.2 设计文档 v1.1 应在修订历史追加

```
| v1.1 | 2026-05-25 | 评审 P1/P2 全收口（P1-1 表名 / P1-2 方案 A / P1-3 R12 重复 / P2-1~4 + P3-1~6）；估算同步 system_design v1.9 §9 → 5-8pd |
```

---

## 9. 评审决策

- **本设计文档不允许 v1.0 状态进入 TDD 实施**——P1 三项中任一未修都会在实施期撞墙：
  - P1-1：alembic 0013 启动失败；
  - P1-2：BacktestEngine.run 调不到 score_universe；
  - P1-3：实施期发现 R12-P1-2 早已完成 → 工作量虚标 + DoD 勾错。
- **建议作者本周内出 v1.1**：含 §3/§5 重写 + 启动核查表更新 + system_design v1.9 §9 同步；
- **v1.1 可重新提交评审**或直接进入 TDD（视 v1.1 修订深度而定）。

### 9.1 v1.1 通过后启动 TDD 的依赖闭环验证

| 验证项 | 验收条件 |
|--------|---------|
| Phase 14 §1.3 grep 三链 | 设计文档作者本地实跑 `grep -rn "R13-P2-\d+"` / `grep -rn "R12-P[12]-\d+"` 后确认每条 hit 都对应到 phase14 / system_design §9 / roadmap 某节，无孤儿 |
| Phase 14 §14-3 方案 A engine 层抽象设计 | engine/scoring/pipeline.py 函数签名草稿写入 §5.2 + 与 ScoringService.score_universe:399-… 既有 5 步代码对齐验证 |
| Phase 14 §14-1 实测 deposit 既有签名兼容 | 给 deposit 增 idempotency_key 后跑 unit/test_account_service.py 既有用例（无 key 路径），断言旧行为不破坏 |

---

> **依据 CLAUDE.md §11.1 三链必填 + §11 充分理由**：本评审报告所有推迟项（P3 6 项）均回链 v1_5_roadmap.md §4.5（V1.5-A）；P1/P2 全部 7 项标"禁推迟，设计期改"。
