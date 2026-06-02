# Phase 14：账户资金链 + 5y candidate_pool 回填 + ICIR 历史回算 + BacktestEngine 真 5 步 + 日级 IC 生产者 + 评审推迟项收口

> 版本：v1.3-r1（2026-06-02 新增 §14-9 日级 IC 生产者 + 设计评审 P1/P2/P3 + 补充 S 项收口）
> 状态：设计完成 + 评审通过（v1.0 P1×3 + P2×4 + P3×6 + v1.1 短复审 C-1×3 + C-2×4 + C-3×1 + v1.3 §14-9 评审 P1×1 + P2×2 + P3×3 + 补充自审 S-01/02/03 全收口） → TDD 实施
> 估算：~7-12 pd（V1.0 收尾批次第 4 个 phase；v1.3 新增 §14-9 日级 IC 生产者 ~2-4 pd）
> 依据文档：
> - SDD §7.4（ICIR 窗口定义）/ §7.7.1（回测引擎）/ §11（账户资金链 V1.0 必修项）
> - system_design §9 Phase 14 行（8 项 scope 锁定）
> - 评审报告：`docs/reviews/phase11_implementation_review_2026-05-19.md` §6.3 第 8/9 项
> - 评审报告：`docs/reviews/phase12_implementation_review_2026-05-20.md` §3 P1-2 + §4 P2-2/P2-3
> - 评审报告：`docs/reviews/phase13_implementation_review_2026-05-22.md` §8 P2 6 项
> - 评审报告：`docs/reviews/phase14_design_review_2026-05-25.md` v1.0（v1.1 修订依据）
> - 评审报告：`docs/reviews/phase14_design_review_v1_1_short_2026-05-25.md`（v1.1 §5 短复审，v1.2 修订依据）
> - 评审报告：`docs/reviews/phase14_design_review_v1_3_2026-06-02.md`（v1.3 §14-9 评审 + 整合性专项，v1.3-r1 修订依据）

---

## 修订历史

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| v1.0 | 2026-05-23 | 初版交付 |
| v1.1 | 2026-05-25 | 评审 P1/P2 全收口：P1-1 §3 表名 `cash_flow`→`fund_flow` + schema 改"扩既有 `FundFlowCreate`"（保留单 schema 路径，不新增 DepositRequest/DividendRequest）+ repo 方法名对齐；P1-2 §5 重写采方案 A（engine 层抽 5 步纯函数 `Scorer.aggregate_pipeline` + active_weights 入 BacktestDataBundle）；P1-3 §1.3/§7.2/§10.1/§11/§12 删 R12-P1-2 重复（已在 Phase 13 启动核查交付）；P2-1 §4.2.1 措辞改正 + 显式新增 `get_existing_candidate_pool_dates` repo 方法；P2-2 §6.3 补 3 项量化 DoD；P2-3 同步 system_design v1.9 §9 估算 3-5pd → 5-8pd；P2-4 §3.2 补 IntegrityError 重查路径 + §3.4 加并发竞态 INT；P3-1~6 措辞 / 长度 / SIGTERM / 路径 / 常量 / 端点措辞批量修订 |
| v1.2 | 2026-05-25 | §5 短复审收口（详见 `docs/reviews/phase14_design_review_v1_1_short_2026-05-25.md`）：C-1×3 删除冗余抽象——v1.1 §5.2 设计 "新增 engine/scoring/pipeline.py + Scorer.aggregate_pipeline + InsufficientUniverseError" 是 3 项已存在轮子的误判，既有 `engine/factor_pipeline.py::FactorPipeline` + `engine/orthogonalizer.py` + `engine/scorer.py::Scorer.aggregate` 已完整覆盖 5 步管线 engine 层纯函数；v1.2 §5.2 重写为 "BacktestEngine 直接复用既有 Scorer.aggregate"。C-2×4 补实施细节——(1) BacktestService `_load_data_bundle` 补加载 `daily_quote.float_mkt_cap` 列；(2) 新增 `BacktestDataBundle.active_weights_history` 字段 + 加载 `strategy_weights_history` 全表；(3) BacktestEngine 主循环 3 处改造（MarketSnapshot 补 industry/market_cap/beta + s.score → compute_strategy_factors + aggregate_legacy/aggregate 分支二选）；(4) `WINSORIZE_MIN_SAMPLES=30` 常量定义在 `engine/scorer.py` 顶部（既有代码 grep 无此常量）+ 新增 `_lookup_active_weights` helper（PIT 前向查找）。C-3 §1.2 §14-3 估算 1-1.5 pd → 0.6-1 pd（5-8 pd 总估算不变 → system_design 无需再次同步）。R14-OPEN-3 + P3-4 引用路径同步修正 |
| v1.3 | 2026-06-02 | **新增 §14-9 日级 IC 生产者（ICIR 历史回算补全 + 实盘续算）**：迁移准备期实跑 `backfill_icir_rebalance.py` 发现 §14-2 ICIR 历史回算只产 `default_matrix`、`factor_ic_window_state` 0 行，根因是日级 IC 时序 `IC_daily(s,f,t)`（SDD §7.4 全 universe Spearman Rank IC）**无任何生产者**（`upsert_ic_daily` 仅测试调用，CP2 只读权重不写日级 IC）→ `rolling_icir_state` 恒 < 60 样本回退冷启动。§14-9 落地：engine 纯函数 `compute_daily_ic`（复用 `FactorMonitorEngine.calc_ic`）+ `scripts/backfill_daily_ic.py`（5y 全 universe 逐日 `score_universe` 抽 `score_breakdown_raw[strategy]["z_raw"]` × 前向收益 → 写 `row_type='daily'`，trade_date=因子值日 d、state=state[d]）+ 实盘 CP2 续算接线（20 日 lag，z 源方案 (a)/(b) 待定）+ 重跑 §14-2 月末批 `--force` 切 `icir`；解锁 §14-4 ICIR 校准。同步 system_design §9 Phase 14 行 item (9) + 估算 ~5-8 → ~7-12 pd |
| v1.3-r1 | 2026-06-02 | §14-9 设计评审收口（`docs/reviews/phase14_design_review_v1_3_2026-06-02.md` 6 项 + 补充自审 3 项）：**P1-1** 文档头版本 v1.2→v1.3-r1 + 估算 ~5-8→~7-12 pd 同步；**P2-1** system_design §9 累计估算脚注 rollup 同步（Phase 14 ~7-12 pd + 合计 ~40-59 pd）；**P2-2** §14-9 daily/aggregate 同 4-tuple 碰撞 → `get_ic_daily_window` 增 `row_type='daily'` 谓词 + INT-P14-9-02；**P3-1** `calc_ic -> float\|None` + None 不写占位行；**P3-2** 修订历史行序；**P3-3** 实盘续算 z 源消费节点闭合 §11.1；**S-01** §11.3.3 显式禁止复用缺陷版前向收益（`_calc_forward_returns`/`_calc_forward_returns_panel` 原始 close+日历近似+无剔除）改用 adj+严格交易日 + R14-OPEN-8 V1.5 统一项；**S-02** `_DAILY_IC_MIN_XS=30` 每日最小横截面；**S-03** INT-P14-9-01 fixture 去 candidate_pool 改 daily_quote/financial/market_state |

---

## 1. 概述

### 1.1 背景

Phase 14 是 V1.0 收尾批次（Phase 11~15）的第 4 个 phase，承担 3 类工作：

1. **业务必修缺口**：5y 真机验收 RM-13 deposit 不幂等 bug + Phase 11 ICIR rebalance Job 未在历史数据上跑通（weights_source 仍 default_matrix）
2. **回测路径补全**：BacktestEngine 走 `aggregate_legacy` + 派生 z 是 Phase 11 临时降级，本阶段切回真 5 步管线 + 配套 ICIR 校准最小集
3. **评审推迟项收口**：Phase 11 §6.3 / Phase 12 P2 (P1-2 已在 Phase 13 启动核查交付) / Phase 13 P2 共 9 项推迟到本批次的项目

### 1.2 Scope 总览（system_design §9 Phase 14 行 8 子项）

| 子项编号 | 主题 | pd | 段落 |
|---------|------|-----|------|
| 14-1 | RM-13 deposit 幂等 | 0.5 | §3 |
| 14-2 | 5y candidate_pool 历史回填 + ICIR 历史回算 | 1.5-2 | §4 |
| 14-3 | BacktestEngine 真 5 步管线接入（采方案 A，直接复用既有 `Scorer.aggregate`）| 0.6-1 | §5 |
| 14-4 | 回测引擎 §2.1 ICIR 校准最小集 | 1 | §6 |
| 14-5 | ICIR 窗口改严格交易日（factor_monitor_service） | 0.2 | §7 |
| 14-6 | factor_ic_window_state 共表拆分评估 | 0.5 | §8 |
| 14-7 | Phase 13 评审 P2 6 项 | 0.8 | §9 |
| 14-8 | AttrBackfill + asyncpg 32767 注释（R12-P1-2 已在 Phase 13 启动核查交付，不重列） | 0.2 | §10 |
| 14-9 | 日级 IC 生产者（ICIR 历史回算补全 + 实盘续算；v1.3 新增 2026-06-02）| 2-4 | §11 |

**合计 ~7-12 pd**（v1.3 新增 §14-9 ~2-4 pd；估算同步至 system_design §9 Phase 14 行）。

### 1.3 启动核查（CLAUDE.md §5 + §11.1）

| 核查项 | 结论 |
|--------|------|
| 读 system_design §9 Phase 14 行 | ✓ 8 子项已锁定 2026-05-22（含 R12-* / R13-P2-* 前向引用） |
| 模块去向决定 | 全部 8 子项纳入本 phase 实施；Phase 15 RC 覆盖下游真机验证（详见 §13） |
| grep `R13-P2-\d+` 跨 system_design + roadmap + reviews/ | 6 项全部进入 §9.x 子节 ✓ |
| grep `R12-P[12]-\d+` 同上 | R12-P1-2 已在 Phase 13 启动核查交付（`services/attribution_service.py:82-100` 已用 `calendar.get_prev_trade_date(...)` 严格交易日 + 注释明示「Phase 13 启动核查阶段修复」），**不重列入本 phase scope**；R12-AttrBackfill + R12-32767 共 2 项进入 §10 ✓ |
| 孤儿模块检查（system_design §3/§5）| Phase 14 不新增模块；v1.2 §14-3 直接复用既有 `engine/scorer.py::Scorer.aggregate`，无需新建 engine 层抽象（v1.1 §5.2.2 "新增 engine/scoring/pipeline.py" 已在 v1.2 短复审撤销）|
| 孤儿端点检查（§6）| Phase 14 不新增 REST API；含 1 个新内部 CLI 脚本 `scripts/backfill_attribution_history.py` |
| 跨 phase stub 标注 | RM-13 idempotency_key 列 alembic 0013 新增；旧记录保 NULL，partial unique 允许多 NULL 共存（详见 §3.3）|

**未在 Phase 14 收口的推迟项**：
- R13-P3 5 项（监控增强）→ v1_5_roadmap §4.5 V1.5-A ✓
- Phase 15 RC 验收项（5y 真机 + STRONG 相对百分比 + 覆盖率门槛 + 文档校核）→ Phase 15 ✓

**v1.3 增补启动核查（2026-06-02，§14-9）**：
| 核查项 | 结论 |
|--------|------|
| 范围归属判定 | §14-9 是 §9 Phase 14 行 item (2) 的**补全**（item 2 已分配"ICIR 历史回算→icir"，日级 IC 生产者为其隐藏未交付部分）→ 归 Phase 14 新增 §14-9，**不新建 phase**（C-5 唯一归属，新建会与已分配模块重复）|
| 孤儿模块检查 | 增强既有 `FactorMonitorEngine`/`ScoringService.score_universe`/`DailyPipeline` CP2 + 新增纯函数 `compute_daily_ic` + 新脚本 `scripts/backfill_daily_ic.py`；**无新 §3/§5 顶层模块、无新 §6 端点** |
| 推迟项消费 | D14-1（ICIR 累积→icir）= 本 §14-9；D14-2（BacktestEngine 真 5 步）= §14-3 独立；D14-3（ICIR 校准最小集）= §14-4，**依赖本 §14-9 跑出真 IC 后方可消费**（仍待 §14-9 完成）|
| §9 回写（C-5 红线）| ✓ 先于本设计正文：system_design §9 Phase 14 行 item (2) 标缺口 + 新增 item (9) + header 估算 ~5-8 → ~7-12 pd |
| 实盘续算 z 源 | 【设计待定】见 §11.3.5，回填先行、续算接线后定夺 |

---

## 2. 设计原则

- **不破坏生产数据**（CLAUDE.md §11 持久约束）：所有 alembic 迁移仅前向无破坏；5y 回填脚本默认 incremental 模式，需 `--force-clean` 显式启用全量
- **测试 DB 隔离**：integration 测试一律跑测试 DB（port 5433）；生产 DB 仅跑 alembic upgrade
- **批次原子性**：每个子项独立 commit，便于回滚；最后 §10 收尾合并文档
- **DoD 三层验证**：unit + integration + 真机（仅 RM-13 deposit 幂等 + 5y 回填 + ICIR 校准必跑真机）

---

## 3. §14-1：RM-13 deposit 幂等

### 3.1 问题

`POST /account/deposit`（Phase 6 实现，body=`FundFlowCreate`；ts_code 无值 → `AccountService.deposit`，ts_code 有值 → `AccountService.record_dividend`）当前无幂等保护：客户端网络抖动重试 / 浏览器误双击 → 同笔入金被重复记录，`account.cash` 翻倍。
2026-05-12 真机验收 RM-13 报：用户 5 月初连续 3 次点击「录入入金 10 万」按钮，`account.cash` 显示 30 万。

### 3.2 实施路径

**3.2.1 alembic 0013 — `fund_flow` 加列**

```sql
ALTER TABLE fund_flow ADD COLUMN idempotency_key VARCHAR(36) NULL;
ALTER TABLE fund_flow ADD CONSTRAINT ck_fund_flow_idempotency_key_len
    CHECK (idempotency_key IS NULL OR length(idempotency_key) <= 36);
CREATE UNIQUE INDEX uq_fund_flow_account_idempotency
    ON fund_flow (account_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

- 表名：`fund_flow`（既有 ORM `models/account.py::FundFlow.__tablename__='fund_flow'`，**非** "cash_flow"）
- 长度上界 36：UUID4 含 4 个 `-` 共 36 字符；额外 CHECK 约束防止超长字符串注入
- partial unique：`WHERE idempotency_key IS NOT NULL` 允许多个 NULL 旧行共存

**3.2.2 Schema 扩展（保留单 schema 兼容 deposit/dividend/withdraw 路径）**

既有 `schemas/account.py::FundFlowCreate` 是 deposit/dividend/withdraw 共用 schema（注释明示 "ts_code 有值 → DIVIDEND，无值 → DEPOSIT"），本批次仅追加可选字段，不破坏既有契约、不新增独立 `DepositRequest`/`DividendRequest`：

```python
class FundFlowCreate(BaseModel):
    """POST /account/deposit 和 /account/withdraw 共用。

    deposit 路由：ts_code 有值 → DIVIDEND（分红），无值 → DEPOSIT（入金）。
    withdraw 路由：flow_type 固定为 WITHDRAW，ts_code 忽略。

    Phase 14 §14-1：新增 idempotency_key 可选字段保护 deposit/dividend 重复提交。
    withdraw 路径默认忽略 idempotency_key（出金本身已有现金余额二次校验）。
    """

    account_id: int
    amount: float
    trade_date: date
    ts_code: str | None = None
    note: str | None = None
    idempotency_key: str | None = Field(None, max_length=36, pattern=r"^[A-Za-z0-9_\-]+$")
```

**3.2.3 Service 层 — `deposit` / `record_dividend` 同步加幂等保护**

两个方法各加 idempotency_key 可选参数（默认 `None` 保留旧行为）+ 共用 `repo.find_fund_flow_by_idempotency`：

```python
async def deposit(
    self,
    account_id: int,
    amount: float,
    trade_date: date,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> FundFlow:
    if idempotency_key is not None:
        existing = await self._repo.find_fund_flow_by_idempotency(
            account_id, idempotency_key,
        )
        if existing is not None:
            logger.info(
                "deposit_idempotent_hit account=%d key=%s flow_id=%d",
                account_id, idempotency_key, existing.id,
            )
            return existing  # 直接返回原记录，account.cash 不变

    try:
        flow = await self._do_deposit(account_id, amount, trade_date, note, idempotency_key)
    except IntegrityError as exc:
        # 并发竞态：先查未命中 → 另一请求已先 INSERT → 本请求 INSERT 撞唯一索引
        if idempotency_key is not None and "uq_fund_flow_account_idempotency" in str(exc):
            await self._session.rollback()
            existing = await self._repo.find_fund_flow_by_idempotency(
                account_id, idempotency_key,
            )
            if existing is not None:
                logger.info("deposit_idempotent_race_resolved key=%s", idempotency_key)
                return existing
        raise
    return flow
```

`record_dividend` 同款（多一个 `ts_code` 入参，对 `_do_record_dividend` 调用与 IntegrityError 处理路径一致）。

**3.2.4 Repository 方法**

```python
# data/account_repository.py（或归入 MarketDataRepository / AccountRepository 现存位置）
async def find_fund_flow_by_idempotency(
    self, account_id: int, idempotency_key: str,
) -> FundFlow | None:
    stmt = select(FundFlow).where(
        FundFlow.account_id == account_id,
        FundFlow.idempotency_key == idempotency_key,
    )
    result = await self._session.execute(stmt)
    return result.scalar_one_or_none()
```

方法名 `find_fund_flow_by_idempotency` 对齐表名 `fund_flow`（评审报告 P1-1 建议）。

**3.2.5 前端**

`OnboardingView.vue` / 仓位页"录入入金 / 录入分红"按钮：组件 mounted 时 `idempotency_key = crypto.randomUUID()`，提交成功后清空并在按钮 disabled→enabled 重新生成下次的 key（详见 R14-OPEN-4 决策）。

### 3.3 兼容性

- 现存 `fund_flow` 行 `idempotency_key=NULL` → partial unique 索引允许多个 NULL
- 旧客户端 / 旧端点调用不传 key → 走非幂等路径（兼容旧行为）
- 新前端永远传 key → V1.0 RC 后逐步要求强制
- `withdraw` 路径不接 idempotency（业务上有 cash 余额校验，重复请求第二次会因 cash 不足直接 400）

### 3.4 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-1-01 | unit | repo.find_fund_flow_by_idempotency 命中/未命中 |
| UT-P14-1-02 | unit | AccountService.deposit 同 key 重复 → 返回原 FundFlow，account.cash 不变 |
| UT-P14-1-03 | unit | AccountService.record_dividend 同 key 重复 → 返回原 FundFlow + 不二次扣 cost_price |
| UT-P14-1-04 | unit | deposit 不传 idempotency_key（旧路径）→ 多次调用产生多行（兼容回归） |
| INT-P14-1-01 | integration | 真 DB partial unique 约束（同 key 重复 INSERT 抛 IntegrityError + service 捕获后重查返回原行） |
| INT-P14-1-02 | integration | 并发竞态：`asyncio.gather` 2 个 deposit 同 key → 两个返回值同 flow_id + account.cash 仅加一次 |
| E2E-P14-1-01 | e2e | POST /account/deposit 2 次同 idempotency_key → 200 + 同 flow_id |

---

## 4. §14-2：5y candidate_pool 历史回填 + ICIR 历史回算

> **实施状态：代码 ✅ 完成 2026-05-27**（commit 待补；scripts/backfill_candidate_pool.py + scripts/backfill_icir_rebalance.py + repo `get_existing_candidate_pool_dates` 交付；UT-P14-2-01/03 + INT-P14-2-01/02 全部 PASS；unit+e2e 577 + integration 136 + ruff 0 error）；**真机回填 50-80h 后台执行 ⏳ 启动中**

### 4.1 问题

Phase 11 收尾时手动跑 `apply_monthly_rebalance(2026-04-30)` 仅写入 12 行（3 state × 4 strategy），`weights_source` 全 `default_matrix`，因为 ICIR 滚动累积需 ≥ 272 日候选池历史（`ic_window_days=252 + icir_lag_days=20`），而生产 DB 当时只有 2026-02 ~ 2026-05 的 78 天 candidate_pool 数据。

### 4.2 实施路径

**4.2.1 5y candidate_pool 回填脚本 `scripts/backfill_candidate_pool.py`**

输入：`--start 2021-01-01 --end 2026-05-22`（默认）；可选 `--resume` / `--force-clean` / `--dry-run-plan`
逻辑：
1. 调 `TradingCalendar.get_trade_dates(start, end)` 拿 ~1210 trade_date
2. 对每个 trade_date：
   - `MarketStateService.identify_state(trade_date)`（PIT，before_date=trade_date+1d）
   - `ScoringService.score_universe(trade_date, market_state)` → 写 candidate_pool
3. 进度上报：每 50 trade_date logger.info + 推 Redis pubsub `quantpilot:backfill:progress`（前端 PipelineProgressCard 风格的进度条可消费）
4. 断点续传：新增 `repository.get_existing_candidate_pool_dates(start, end) -> set[date]`（仅 candidate_pool **单表**已写入 trade_date 集合，**与 `get_fully_ingested_dates` 双表交集语义不同**），脚本主循环跳过已存在日
5. **Graceful shutdown**：注册 `signal.SIGINT/SIGTERM` handler，收到信号后跳过新 trade_date 启动 + 等待当前 trade_date per-day session commit/rollback 后退出，避免半 commit

**单日耗时**：生产 5y 数据上 5 步管线 ~130-250s/日；5y × 1210 trade_date ≈ 50-80 小时。建议跑分布式或周末整夜跑。脚本支持 `--resume` 从断点续传。

**4.2.2 ICIR 历史回算（直接复用 candidate_pool 回填）**

`apply_monthly_rebalance` 已经接入 `check_persistent_decay`（Phase 13 R13-P1-2）；只要 candidate_pool 5y 全量在库，对每个月末跑一次 `apply_monthly_rebalance` 就能写满 `factor_ic_window_state` + `strategy_weights_history` 5y 历史。

**4.2.3 月末批量脚本 `scripts/backfill_icir_rebalance.py`**

输入：`--start 2021-01 --end 2026-05`
逻辑：枚举所有月末交易日（~60 个），逐个跑 `FactorMonitorService.apply_monthly_rebalance(month_end)` + commit；同款 SIGINT/SIGTERM graceful handler

**估算**：5y × 60 month_end × ~5-20s/次 ≈ 5-20 分钟（实际跑前先单月计时验证，见 R14-OPEN-5）。

### 4.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-2-01 | unit | backfill_candidate_pool 跳过已存在日（mock get_existing_candidate_pool_dates 返回部分 set） |
| UT-P14-2-02 | unit | repository.get_existing_candidate_pool_dates 返回 set[date] 与查询区间一致 |
| UT-P14-2-03 | unit | backfill 脚本捕获 SIGINT 后 inflight trade_date commit + 不启动下一日 |
| INT-P14-2-01 | integration | backfill_icir_rebalance 5 个 month_end → factor_ic_window_state 写入 60 行（3 state × 4 strategy × 5 月） |
| 真机-P14-2 | manual | 5y 全量回填后 `SELECT COUNT(*) FROM strategy_weights_history WHERE weights_source='icir'` ≥ 700（60 month_end × ~12 行 - 冷启动月） |

### 4.4 风险

- **5y 回填长时间运行**：`nohup` 后台 + 进度推 Redis；脚本必须 idempotent（断点续传 + UPSERT）+ SIGTERM graceful（见 §4.2.1 第 5 步）
- **ICIR 冷启动月**：前 ~13 个月（272 日窗口未满）`weights_source` 仍 `default_matrix`，第 14 个月起切 `icir`
- **生产 DB 写压力**：5y × 1210 trade_date 写 candidate_pool（~50 行/日）+ daily_quote 已有 → 总增 ~60k candidate_pool 行，PG `work_mem=8MB` 应足够

---

## 5. §14-3：BacktestEngine 真 5 步管线接入（方案 A）

> **实施状态：✅ 完成 2026-05-28**（commit 待补；UT-P14-3-01~05 全 5 项 PASS + INT-P14-3-01a/01b 真 DB PASS；回归 unit+e2e 582 + integration 138 + ruff 0 error）。**实施期发现 1 项设计偏差并按真实 ORM 落地**：v1.2 §5.2.2 模板 `select(StrategyWeightsHistory).where(StrategyWeightsHistory.effective_date <= ...)` + `r.weights_json` blob 是误判——实际 ORM 是一行一 `(state, strategy, trade_date)` + `weight_used` scalar（无 `effective_date` / `weights_json` 列）；本实现按真 schema group by `(state, trade_date)` 组装 dict，`orthogonalize_order` 由 weights 降序派生（与生产 `FactorMonitorService.get_active_weights:711` 同路径），与设计语义等价、未引入降级。完整 30 trade_date × ~2400 universe + composite_z ±3.5σ + real_5step ≥ 90% 真机 DoD 由 §14-2 5y 回填完成后跑（属 Phase 14 整批收尾验收）。

### 5.1 问题

Phase 11 收尾时 `engine/backtest/engine.py::run` 仍走 `Scorer.aggregate_legacy()` + 后处理派生 `composite_z` / `composite_pct_in_market`，原因：单股 mock 场景被 5 步 Z-score 标准化打死（单股 std=0 → div by zero）。
不影响实盘 critical path（DailyPipeline CP2 已切真 5 步——通过 `ScoringService.score_universe`），但 V1.0 回测 IC 验证、滑点情景对比无法跑准确数。

### 5.2 实施路径（方案 A：直接复用既有 Scorer.aggregate）

> **v1.2 短复审收口（详见 `docs/reviews/phase14_design_review_v1_1_short_2026-05-25.md`）**：v1.1 §5.2 原设计"engine 层新增 pipeline.py + Scorer.aggregate_pipeline + InsufficientUniverseError"是 3 项已存在轮子的误判——`engine/factor_pipeline.py::FactorPipeline`（Step 1~3）+ `engine/orthogonalizer.py`（Step 4a）+ `engine/scorer.py::Scorer.aggregate`（Step 4b+5 编排）已完整覆盖 5 步管线 engine 层纯函数实现，已被 `ScoringService.score_universe:457` 在生产 critical path 5 年验证。v1.2 §5.2 简化为"BacktestEngine 直接复用既有 Scorer.aggregate" + 3 处主循环改造 + 2 项数据加载补全。

#### 5.2.1 架构事实

| 5 步管线步骤 | 既有实现 | 是否纯函数 |
|--------------|----------|-----------|
| Step 1 Winsorize | `engine/factor_pipeline.py:54::FactorPipeline.winsorize` | ✅ |
| Step 2 行业+市值中性化 | `engine/factor_pipeline.py:73::FactorPipeline.neutralize` | ✅ |
| Step 3 Z-score | `engine/factor_pipeline.py:176::FactorPipeline.zscore` | ✅ |
| Step 4a Gram-Schmidt | `engine/orthogonalizer.py::Orthogonalizer.compute` | ✅ |
| Step 4b 残差再标准化 + Hysteresis | `engine/scorer.py:255` 内联 | ✅ |
| Step 5 三层输出 | `engine/scorer.py:282-393` 内联 | ✅ |
| **整合入口** | **`engine/scorer.py:120::Scorer.aggregate(market_state, strategy_factors, snapshot, weights_runtime, weights_source, orthogonalize_order, hysteresis_status, single_strategy_mode=False) -> list[CompositeScore]`** | ✅ |

`Scorer.aggregate` 签名已满足方案 A 全部需求：纯函数、snapshot 含 industry/market_cap/beta（MarketSnapshot Phase 11 §3.0 P0-3 已扩展）、输出含 composite_z/composite_pct_in_market/composite_score/score_breakdown_raw/score_breakdown_residual/factor_winsorized/factor_neutralized/factor_orthogonal Phase 11+12 全字段。BacktestEngine 直接调用即可，**无需新增 engine 层抽象**。

#### 5.2.2 BacktestService 数据预加载扩展

`services/backtest_service.py::_load_data_bundle` 追加两项：

```python
# 1. daily_quotes 行字典补 float_mkt_cap 列（既有 models/market.py:53 DailyQuote.float_mkt_cap）
dq_df 构造时追加：
    "float_mkt_cap": float(r.float_mkt_cap) if r.float_mkt_cap is not None else None,

# 2. 新增加载 strategy_weights_history 全表
from quantpilot.models.business import StrategyWeightsHistory
sw_rows = (await self._session.execute(
    select(StrategyWeightsHistory)
    .where(StrategyWeightsHistory.effective_date <= config.end_date)
    .order_by(StrategyWeightsHistory.effective_date)
)).scalars().all()
active_weights_history = {
    (r.market_state, r.effective_date): {
        "weights": r.weights_json,
        "weights_source": r.weights_source,
        "orthogonalize_order": r.orthogonalize_order_json or [],
        "hysteresis_status": r.hysteresis_status or "stable",
    }
    for r in sw_rows
}
```

`BacktestDataBundle` 新增字段：

```python
@dataclass
class BacktestDataBundle:
    # ... 既有字段（adj_prices/stock_info/financials/hs300_history/daily_quotes/...）
    # Phase 14 §14-3：5y active_weights 时序（按 (market_state, effective_date) PIT 切片）
    # 主循环用 max(effective_date) <= trade_date AND market_state 做前向查找（月末 snapshot）
    active_weights_history: dict[tuple[str, date], dict] = field(default_factory=dict)
```

#### 5.2.3 WINSORIZE_MIN_SAMPLES 常量定义

`engine/scorer.py` 顶部新定义（Phase 14 §14-3 落地，**不存在于既有代码**）：

```python
WINSORIZE_MIN_SAMPLES = 30
# 5 步管线 Winsorize 横截面最小样本（< 30 → 走 aggregate_legacy 降级）。
# 30 是 Phase 11 设计估计值，ScoringService.score_universe 未来若需同等门槛
# 检查可同源 import；BacktestEngine 是首个消费方。
```

#### 5.2.4 BacktestEngine 主循环改造（共 3 处）

`engine/backtest/engine.py::run` 主循环：

**改造 1：MarketSnapshot 补 industry / market_cap / beta（既有第 f 步 line 240-247）**

```python
# 从 stock_info_t 派生 industry dict（PIT）
industry_map: dict[str, str] = {}
if "sw_industry_l1" in stock_info_t.columns:
    sw = stock_info_t["sw_industry_l1"].dropna()
    industry_map = {str(k): str(v) for k, v in sw.items()}

# 从 quotes_t 派生 market_cap Series（PIT；需 §5.2.2 第 1 项 float_mkt_cap 加载）
market_cap_series: pd.Series | None = None
if "float_mkt_cap" in quotes_t.columns:
    market_cap_series = quotes_t["float_mkt_cap"].dropna().astype(float)

from quantpilot.engine.strategies.base import MarketSnapshot
market_snap: MarketSnapshot = {
    "trade_date": trade_date,
    "adj_prices": adj_hist,
    "daily_quotes": quotes_t,
    "financials": financials_t,
    "pe_pb_history": pe_pb_t,
    "index_adj_prices": idx_adj_t,
    "industry": industry_map,
    "market_cap": market_cap_series,
    "beta": None,  # V1.0 永远 None（与 ScoringService._build_market_snapshot 一致）
}
```

**改造 2：策略循环改用 compute_strategy_factors（既有 line 249-258）**

```python
# 旧（Phase 4 路径）：
# score = s.score(universe_idx, market_snap)        # 返回 list[StrategyScore] 0-100
# strategy_scores_dict[s.name] = score

# 新（与 ScoringService.score_universe:422-430 一致）：
strategy_factors: dict[str, pd.DataFrame] = {}
for s in self._strategies:
    try:
        factor_df = s.compute_strategy_factors(universe_idx, market_snap)
        strategy_factors[s.name] = factor_df
    except Exception:
        logger.exception(
            "backtest_strategy_compute_factors_error strategy=%s date=%s",
            s, trade_date,
        )
```

注：`compute_strategy_factors` 是 `BaseStrategy:65` 既有默认实现（透传 `compute_raw_factors`），无需新策略接口改动；旧 `s.score` 路径仍保留供 fallback 分支（见改造 3）。

**改造 3：聚合分支二路径（既有 line 263-277 替换）**

```python
from quantpilot.engine.scorer import WINSORIZE_MIN_SAMPLES

# 前向查找 active_weights snapshot（max(effective_date) <= trade_date AND market_state）
market_state_str = (
    market_state.value if hasattr(market_state, "value") else str(market_state)
)
weights_record = self._lookup_active_weights(
    trade_date, market_state_str, data.active_weights_history,
)

composite_scores: list = []
pipeline_mode: str = "legacy_fallback"

if (len(universe_idx) < WINSORIZE_MIN_SAMPLES
    or weights_record["weights"] is None):
    # 降级：universe 不足 / active_weights 未就绪 → 走 Phase 4 legacy + 派生 z
    # 兼容旧路径所需的 strategy_scores_dict（每股 0-100 分）：用 s.score(...) 临时计算
    legacy_scores_dict: dict[str, list] = {}
    for s in self._strategies:
        try:
            legacy_scores_dict[s.name] = s.score(universe_idx, market_snap)
        except Exception:
            logger.exception(
                "backtest_strategy_score_legacy_error strategy=%s date=%s",
                s, trade_date,
            )
    try:
        composite_scores = self._scorer.aggregate_legacy(
            market_state, legacy_scores_dict,
        )
    except Exception:
        logger.exception("backtest_scorer_aggregate_legacy_error date=%s", trade_date)
        composite_scores = []
    pipeline_mode = "legacy_fallback"
else:
    # 真 5 步：直接调既有 Scorer.aggregate（engine 层纯函数）
    try:
        composite_scores = self._scorer.aggregate(
            market_state=market_state,
            strategy_factors=strategy_factors,
            snapshot=market_snap,
            weights_runtime=weights_record["weights"],
            weights_source=weights_record["weights_source"],
            orthogonalize_order=weights_record["orthogonalize_order"],
            hysteresis_status=weights_record["hysteresis_status"],
            single_strategy_mode=False,
        )
        pipeline_mode = "real_5step"
    except Exception:
        logger.exception("backtest_scorer_aggregate_error date=%s", trade_date)
        composite_scores = []
        pipeline_mode = "real_5step_failed"
```

`BacktestResult` 新增 `pipeline_mode: str` 字段（默认 `"legacy_fallback"` 兼容旧测试）供前端展示。

#### 5.2.5 _lookup_active_weights helper

`engine/backtest/engine.py` 私有方法（纯函数）：

```python
def _lookup_active_weights(
    self,
    trade_date: date,
    market_state_str: str,
    history: dict[tuple[str, date], dict],
) -> dict:
    """前向查找：max(effective_date) <= trade_date AND market_state == market_state_str。

    找不到 → 返回 {"weights": None, "weights_source": "default_matrix",
    "orthogonalize_order": [], "hysteresis_status": "stable"}（触发 §5.2.4 改造 3
    降级路径）。
    """
    candidates = [
        (eff_date, rec) for (state, eff_date), rec in history.items()
        if state == market_state_str and eff_date <= trade_date
    ]
    if not candidates:
        return {
            "weights": None,
            "weights_source": "default_matrix",
            "orthogonalize_order": [],
            "hysteresis_status": "stable",
        }
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]
```

### 5.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-3-01 | unit | `_lookup_active_weights` 前向查找正确（多 state × 多日期，断言 PIT + state 过滤）|
| UT-P14-3-02 | unit | `_lookup_active_weights` 找不到 snapshot → 返回 default_matrix sentinel |
| UT-P14-3-03 | unit | BacktestEngine 主循环 universe ≥ 30 + weights 就绪 → pipeline_mode='real_5step'，composite_scores 含 composite_z 字段 |
| UT-P14-3-04 | unit | BacktestEngine 主循环 universe < 30 → pipeline_mode='legacy_fallback'，走 aggregate_legacy |
| UT-P14-3-05 | unit | BacktestEngine 主循环 active_weights_history 为空 → pipeline_mode='legacy_fallback' |
| INT-P14-3-01 | integration | 真 DB 跑 30 trade_date × ~2400 universe 回测，composite_z 分布断言（±3.5σ）+ pipeline_mode 统计（real_5step 占比 ≥ 90%）|

---

## 6. §14-4：回测引擎 §2.1 ICIR 校准最小集

> **实施状态（代码 + UT 部分）：✅ 完成 2026-05-28**（commit 待补；engine/diagnostics/ic_aggregator.py 纯函数 + 3 个 CLI 脚本 + UT-P14-4-01/02 + compute_t_stat 边界 10 项全 PASS；ruff 0 error；unit+e2e 592 回归通过）。**真机 DoD（真机-P14-4-1/2/3）：等 §14-2 5y 回填完成后跑**——3 个脚本均依赖 `factor_ic_window_state` IC_daily 行存在（§14-2 `backfill_icir_rebalance.py` 跑完后才有 ≥272d IC_daily 序列），slippage_sensitivity 还依赖 5y daily_quote + strategy_weights_history（也是 §14-2 产出）。

### 6.1 问题

SDD §2.1 要求"V1.0 必须在真历史数据上验证 IC 时序量级、多场景对比、滑点敏感性"；Phase 11 收尾仅做了 4 trade_date × 3 state 跨制度抽样，未做时序量级 + 多场景对比。

### 6.2 实施路径

**6.2.1 IC 时序量级验证脚本** `scripts/validate_ic_timeseries.py`

输入：`--strategy trend --factor macd_hist --state UPTREND --start 2021-01 --end 2026-05`
输出落 `backend/var/diagnostics/phase14/`（与既有 `backend/scripts/output/` 区分，明示是诊断中间产出）：
- `ic_timeseries_<strategy>_<factor>_<state>.csv`（按月聚合 ic_mean + ic_std + sample_size）
- `ic_timeseries_<strategy>_<factor>_<state>.png`（matplotlib 折线图）

**6.2.2 多场景对比**：4 策略 × 3 state × 60 月 = 720 行 panel，写 `scripts/compare_strategy_ic_panels.py` 输出 `ic_heatmap_<strategy>.png` heatmap PNG + `ic_panels_summary.csv`。

**6.2.3 滑点敏感性最小验证**：BacktestEngine 配置 3 档滑点 `[0.0005, 0.002, 0.005]`（万 5 / 千 2 / 千 5），跑同一 5y 回测，输出 `slippage_sensitivity.csv` 三档 sharpe / max_drawdown / ann_return 对比。

### 6.3 测试（DoD 量化阈值）

| 编号 | 类型 | DoD（可执行）|
|------|------|--------------|
| UT-P14-4-01 | unit | validate_ic_timeseries 接受 ic_value 序列 → 输出 monthly aggregate |
| UT-P14-4-02 | unit | compare_strategy_ic_panels 接受 panel df → 输出 heatmap 数据 |
| 真机-P14-4-1 | manual | 5y monthly aggregate CSV 中 **≥ 85% 月份** ic_mean ∈ [-0.1, 0.1]，且 4 策略 × 3 state = 12 组合中**至少 8 组** sample_size ≥ 60 |
| 真机-P14-4-2 | manual | 4 策略 × 3 state heatmap CSV 中**至少 6 组** ic_mean 与 0 显著差异（\|t-stat\| > 2） |
| 真机-P14-4-3 | manual | 5y 三档滑点回测 `sharpe(slippage=0.0005) - sharpe(slippage=0.005) ≥ 0.05`（单调性最小差值） |

---

## 7. §14-5：ICIR 窗口改严格交易日（factor_monitor_service）

> **实施状态：✅ 完成 2026-05-26**（commit 待补；UT-P14-5-01 + INT-P14-5-01 全部 PASS；ruff 0 error；unit+e2e 560 + integration 126 回归通过）

### 7.1 问题

`services/factor_monitor_service.py::rolling_icir_state:416-423`：
```python
start = trade_date - timedelta(days=272)
end = trade_date - timedelta(days=20)
```
272 / 20 是日历日 → 实际窗口 ≈ 188 / 14 交易日，比 SDD §7.4 "252 + 20 交易日 = 272 交易日 / lag 20 交易日" 短 ~25%。

**注**：`services/attribution_service.py` 的同源问题（评审 R12-P1-2）**已在 Phase 13 启动核查阶段交付**（`services/attribution_service.py:82-100` 已用 `calendar.get_prev_trade_date(month_end, n=20 * self._lookback_months)`），本批次仅修 `factor_monitor_service.py` 一处。

### 7.2 实施路径

```python
# factor_monitor_service.py
# __init__ 新增 calendar 参数
def __init__(
    self,
    session: AsyncSession,
    engine: FactorMonitorEngine,
    repo: FactorICRepository | None = None,
    calendar: TradingCalendar | None = None,
) -> None:
    ...
    self._calendar = calendar

# rolling_icir_state 内：calendar 注入时走严格交易日，否则回退日历日 + WARNING
if self._calendar is not None:
    window_end = self._calendar.get_prev_trade_date(trade_date, n=_ICIR_LAG_DAYS)
    window_start = self._calendar.get_prev_trade_date(window_end, n=_ICIR_WINDOW_DAYS)
else:
    # 【降级说明】兼容旧测试；生产路径已全部注入 calendar
    logger.warning("rolling_icir_state_calendar_missing ... — falling back to calendar-day")
    window_end = trade_date - timedelta(days=_ICIR_LAG_DAYS)
    window_start = trade_date - timedelta(days=_ICIR_WARMUP_DAYS)
```

4 处生产路径全部注入：
- `api/deps.py::get_factor_monitor_service` → 从 `request.app.state.calendar` 取
- `pipeline/monthly_scheduler.py::run_factor_monitoring` + `run_icir_rebalance` → 用 `self._calendar`
- `pipeline/daily_pipeline.py::_run_phase11_pipeline` → 用 `self._calendar`

同步：SDD §7.4 措辞确认（v1.4 已写"交易日"，与本批一致，无需改）。

### 7.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-5-01 | unit | rolling_icir_state 用 mock calendar 断言 start = end - 252 交易日 |
| INT-P14-5-01 | integration | 跨周末 (252 trade days ≠ 252 calendar days) 校验 |

---

## 8. §14-6：factor_ic_window_state 共表拆分评估

> **实施状态：✅ 完成 2026-05-26**（方案 A 落地；alembic 0014 + ORM/Repository 切 row_type 过滤；6 INT-P14-6 全 PASS；测试 DB 已升级到 0014；生产 DB 待 Phase 14 整批收尾统一升级）

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

> **实施状态：✅ 完成 2026-05-26**（commit 待补；6 P2 全部收口，新增 9 UT/E2E，回归 unit+e2e 570 + integration 126 + ruff 0 error）

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

## 10. §14-8：AttrBackfill + asyncpg 32767 注释

> **实施状态：✅ 完成 2026-05-26**（commit 待补；2 INT-P14-8 全 PASS；scripts/backfill_attribution_history.py 交付 + attribution_repository docstring 32767 防御注释；回归 unit+e2e 570 + integration 128 + ruff 0 error）

> **注**：评审 R12-P1-2（AttributionService 严格交易日）已在 Phase 13 启动核查阶段交付，**不重列入本 phase scope**（见 §1.3 + §7.1 实证核查）。本节仅含 AttrBackfill + 32767 注释 2 项。

依据：`docs/reviews/phase12_implementation_review_2026-05-20.md` §4 P2-2/P2-3。

### 10.1 AttributionService 日级历史回填

**问题**：Phase 12 设计文档 §1.2 推迟项标注"AttributionService 日级回填脚本 → Phase 14 §14-2"；Phase 12 验收仅用合成 panel 4 行集成测试。
**实施**：新增 `scripts/backfill_attribution_history.py`：
- 输入：`--start 2021-01 --end 2026-05`
- 逻辑：枚举所有 month_end → 调 `AttributionService.run_monthly(month_end)` → 写 `attribution_history` 表
- 与 §14-2 ICIR 历史回算同批跑（依赖 candidate_pool 全量在库）
- 同款 SIGINT/SIGTERM graceful handler

### 10.2 attribution_repository _BATCH_SIZE 注释

**问题**：Phase 12 评审 §4 P2-3 警告：Phase 12 单次 4 行远未达 asyncpg 32767 占位符限制，但 §14-2 5y × 60 month_end × 4 = 1200 行单次仍未超限；本批仅在 docstring 顶部加注释作未来防御。
**实施**：`data/attribution_repository.py` docstring 加："V1.0 单次 ≤ 1200 行未达 32767 限制；若扩 N month_end 使 N × 4 列总数 ≥ 8000 行，应在 upsert_attributions 内启用 `_BATCH_SIZE=500` 循环。"

### 10.3 测试

| 编号 | 类型 | 覆盖 |
|------|------|------|
| INT-P14-8-01 | integration | backfill_attribution_history 跑 6 month_end → attribution_history 写入 24 行（6 × 4 因子） |

---

## 11. §14-9：日级 IC 生产者（ICIR 历史回算补全 + 实盘续算）

> **实施状态：⏳ 设计 v1.3 新增 2026-06-02**（迁移准备期发现 §14-2 ICIR 历史回算隐藏未交付）。本节补全 §14-2 缺失的上游"日级 IC 时序生产者"，并接线实盘续算让 ICIR 长期不失效。

### 11.1 问题

§14-2 设计假设"只要 candidate_pool 5y 全量在库，逐月跑 `apply_monthly_rebalance` 就能写满 `factor_ic_window_state` + `strategy_weights_history` 5y 历史"。2026-06-02 迁移准备期实跑（`backfill_icir_rebalance.py --start 2021-05 --end 2026-05`）发现：61 月末中 49 个有 ≥ 272 日历史的月**全部产 `default_matrix`**，`factor_ic_window_state` **0 行**。

根因链：`apply_monthly_rebalance` → `rolling_icir_state` → `get_ic_daily_window` 读 `factor_ic_window_state` 的 `row_type='daily'` 行作 IC 样本；样本 < 60 → 返回 None → 冷启动 fallback。而**这些日级 IC 行无任何生产者**：`FactorICRepository.upsert_ic_daily` 全代码仅测试调用，日常 pipeline CP2 `score_universe` 只读 active weights 不写日级 IC。即 §14-2 ICIR 历史回算缺失上游输入，结构性恒冷启动。

SDD §7.4（line 460）：`IC_daily(s,f,t) = Spearman corr(factor_value_{t-20}, return_{t-20→t})`，全 universe 横截面 Rank IC，单点不区分 state；月末按 `state_{t-20}` 子集聚合 ICIR。

### 11.2 数据契约（与既有约定对齐）

日级 IC 行写 `factor_ic_window_state`（§14-6 已分 `row_type`）：

| 列 | 值 | 依据 |
|---|---|---|
| strategy / factor | strategy / = strategy | V1.0 factor=strategy 简化（`apply_monthly_rebalance._STRATEGY_NAMES`）|
| trade_date | **因子值日 d**（= 观测日 t 的 t−20）| 对齐 `rolling_icir_state` 窗口 `[t−272, t−20]` 选因子值日 + INT-P14-2 `_seed_daily_ic` key = `month_end−(lag+i)` |
| state | `market_state[d]`（因子值日真实 state，对应 SDD `state_{t-20}`）| 每日仅一行该日真实 state（**非**测试那样跨 3 state 复制）|
| ic_value | 全 universe Spearman Rank IC | SDD §7.4 |
| sample_size | 对齐后横截面有效股票数 | `rolling_icir_state` 以日级**行数**（≥60）判样本；本列存横截面 N |
| row_type | 'daily' | §14-6 partial unique 仅约束 'aggregate'，不约束 daily 行 |

### 11.3 实施路径

**11.3.1 engine 纯函数（复用为主）**
- 复用 `engine/factor_monitor.py::FactorMonitorEngine.calc_ic(factor_values, forward_returns) -> float | None`（已 `spearmanr` + dropna 对齐；**有效对齐样本 < 5 返 `None`**——P3-1）。
- 新增薄编排纯函数 `compute_daily_ic(strategy_z: dict[str, pd.Series], forward_returns: pd.Series) -> list[DailyICPoint]`（每策略调 `calc_ic` + 统计对齐后 sample_size）；严格无 IO，配 UT。落 `engine/diagnostics/ic_aggregator.py`（§14-4 既有 IC 聚合模块）或同层新 module。
- **None 处置（P3-1）**：某策略当日 `calc_ic` 返 `None`（样本不足）→ **不写该 (strategy, d) daily 行**（不写 `ic_value=NULL` 占位——`get_ic_daily_window` 按 `ic_value IS NOT NULL` 过滤，NULL 行只徒增表行不被采样；少写一行 = 该策略该日不计入窗口样本，语义正确）。

**11.3.2 全 universe strategy_z 来源**
- 关键约束：`candidate_pool` 仅存 top-N pool（~60/日），IC 必须全 universe（~2400）→ **不可复用 candidate_pool**（截断截面 + 量程受限会系统性低估 IC）。
- `ScoringService.score_universe(session, d, universe, state)` 返回**全 universe** `list[CompositeScore]`（写 pool 是独立步）；每 `CompositeScore.score_breakdown_raw[strategy]["z_raw"]` = 该股该策略 z（AttributionService 已消费同字段，P12 实证等价）。
- backfill 逐日：`UniverseFilter(d)` 取全 universe → `score_universe(d, universe, state[d])` → 抽每策略 z Series（全 universe，剔 None）。Rank IC 对单调变换不变 → z_raw 与 Φ(z)×100 等价。

**11.3.3 前向收益（严格定义，不复用既有缺陷实现）**
- **【S-01 警告：禁止复用既有两个前向收益函数】** `FactorMonitorService._calc_forward_returns`（`:299`）与 `AttributionService._calc_forward_returns_panel`（`:273`）均有三处缺陷，**不可用于 IC 计算**：① 用**原始 close（未复权）**——20 日窗口内除权/拆分扭曲收益；② **日历日近似窗口**（`window×1.4~1.5` 天取首个 close，非严格 t=d+20 交易日，与 §14-5 严格交易日整改相悖）；③ **无涨跌停/停牌剔除**（违反 SDD §7.4 line 473）。实施者易顺手复用 → 必须显式回避。
- **正确实现**：复用 `MarketDataRepository.get_adj_prices_bulk`（**后复权 adj_close = close×adj_factor**）：取 universe 在 `[d, t]`（`t = calendar.get_next_trade_date(d, 20)` **严格交易日**）的 adj_close → `ret = adj_close[t]/adj_close[d] − 1`；剔 d 或 t 的 `daily_quote.limit_up/limit_down/is_suspended` 异常收益（SDD §7.4 line 473）。
- 跳过区间末尾 20 交易日（t 越界 → forward return 不可得）。
- **【S-02 每日最小横截面】**：对齐后有效股票数 < `_DAILY_IC_MIN_XS`（建议 30；高于 `calc_ic` 自带的 5 地板，避免噪声 IC 污染窗口）→ 该 (strategy, d) 不写 daily 行 + `logger.info`（A 股全 universe ~2400，正常日远超 30；触发多为早期数据 / 大面积停牌日）。
- **【已知分歧 → V1.5 统一项】** attribution 自身仍用缺陷版前向收益（原始 close + 日历近似），与 daily-IC 的 adj+严格版定义不同；二者不同消费方，V1.0 不强行统一，登记 V1.5 抽共享 `compute_forward_returns` 纯函数收敛（R14-OPEN-8）。

**11.3.4 backfill 脚本 `scripts/backfill_daily_ic.py`**
- 仿 `backfill_candidate_pool.py` 骨架：`--start/--end/--dry-run-plan/--skip-confirm/--force`；per-day `AsyncSessionLocal` + commit；SIGINT/SIGTERM graceful（复用 `_GracefulInterrupt`）；断点续传查已存在 daily trade_date（新增 repo `get_existing_daily_ic_dates(start, end) -> set[date]`）。
- 逐 trade_date d（[start, end] 去末 20 交易日）：universe → state[d] → score_universe → forward_returns → `compute_daily_ic` → `ICDailyRow`×4（state=state[d]）→ batched `upsert_ic_daily`（每行 6 列；批 ≤ 2000 行 = 12000 占位符 < 32767，沿用既有 §14-2 `_seed` 批长）。
- **单日耗时未实测** → R14-OPEN-6：实跑前先单日计时定全量工期（初判 `score_universe` 全 universe ~10-60s/日 → 5y ~3-20h，纯 DB 无 Tushare）。
- **【P2-2 daily/aggregate 4-tuple 碰撞】**：daily 行写在因子值日 d，而每个 month_end 自身也是某观测的因子值日 → §11.6 顺序"先 daily 回填、再月末批 `--force`"时 `upsert_ic_aggregate` 会命中既有 daily 行的 `(strategy,factor,state,trade_date)` 4-tuple，`on_conflict_do_update` 升 `row_type='aggregate'` 但 `set_` 不含 `ic_value` → 旧 daily `ic_value` 残留 → 该行被 `get_ic_daily_window`（按 `ic_value IS NOT NULL`）与 aggregate 查询双重计入。**修复**：`FactorICRepository.get_ic_daily_window` 谓词增 `row_type='daily'`（最干净，使 daily/aggregate 区分显式化）；配 INT-P14-9-02 覆盖"同 4-tuple 先 daily 后 aggregate → daily 窗口不再取到该行"。

**11.3.5 实盘续算接线（CP2，forward-looking）**
- 回填只填到迁移日；要让 ICIR 长期不失效，须把日级 IC 计算接进日常 pipeline：交易日 t 用已实现 t−20→t 收益对因子值日 d=t−20 计算 IC → 写 `row_type='daily'`。
- d=t−20 的 strategy_z 获取：**【设计待定：(a) CP2 落"全 universe 日级 z 快照"（新表/缓存，~2400×4/日，20 日滚动保留即可，t 日回看 t−20）vs (b) 续算步骤在 t 日 PIT 复算 d=t−20 的 `score_universe`（省存储、每日多一次 CP2 计算）。回填脚本天然走 (b) 等价逐日现算；实盘倾向 (a) 避免每日双倍 CP2 算力。本子项分两步交付——回填（迁移关键路径）先行验证，续算接线方案 (a)/(b) 在回填验证通过后定夺。**消费节点：§14-9 续算接线步（本批内、回填验证通过后定夺，不推迟出 Phase 14）**。】**

### 11.4 测试矩阵

| 编号 | 类型 | 覆盖 |
|------|------|------|
| UT-P14-9-01 | unit | `compute_daily_ic`：单调正相关 strategy_z↔return → IC≈+1；反相关 → IC≈−1；含 NaN → sample_size 为对齐后有效数；**某策略对齐样本 < 5 → `calc_ic` 返 None → 该策略不产 daily 行（P3-1）** |
| UT-P14-9-02 | unit | 前向收益：**后复权 adj_close** 序列 + **严格 20 交易日** → 正确 ret；涨跌停/停牌行剔除；**对齐有效 N < `_DAILY_IC_MIN_XS`(30) → 不写该日（S-02）** |
| UT-P14-9-03 | unit | backfill plan/skip：mock `get_existing_daily_ic_dates` 部分 set → 跳过；区间末尾 20 交易日不处理 |
| UT-P14-9-04 | unit | SIGINT graceful（仿 UT-P14-2-03）：inflight d commit + 不启动下一日 |
| INT-P14-9-01 | integration | 合成 **daily_quote/financial_data/market_state_history**（跳周末；**非 candidate_pool**——backfill 经 `score_universe` 读原始数据不读 pool，S-03）小窗口 → `factor_ic_window_state` daily 行写入 + state 标签 = 因子值日 state；串联 `apply_monthly_rebalance` 后 dominant state 出 `weights_source='icir'` |
| INT-P14-9-02 | integration | **daily/aggregate 4-tuple 碰撞（P2-2）**：同 `(s,f,state,d)` 先 `upsert_ic_daily` 后月末批 `upsert_ic_aggregate` → `get_ic_daily_window`（增 `row_type='daily'` 谓词后）不再取到被升级行，daily 窗口不重复计入 |
| 真机-P14-9 | manual | 5y 回填后 `COUNT(*) WHERE row_type='daily'` ≈ 1180 trade_date × 4 ≈ 4700（减末 20 日 + None/稀疏跳过）；重跑 §14-2 月末批 `--force` 后 `weights_source='icir'` 行 > 0（至少 UPTREND dominant 月）|

### 11.5 已知边界

- **稀疏 state**：DOWNTREND/OSCILLATION 在某 252 日窗口 state 子集 < 60 → 该 state×月仍 `default_matrix`（SDD §7.4 line 478 合规降级，非 bug）。UPTREND（A 股近 5y 主导）应稳定切 icir。
- **末尾 20 交易日**：区间最末 20 交易日 forward return 不可得，IC 留空（实盘续算在 +20 日后自然补上）。
- **实盘续算 z 源 (a)/(b)**：见 §11.3.5 【设计待定】。
- **daily/aggregate 共表区分（P2-2）**：`get_ic_daily_window` 必须按 `row_type='daily'` 过滤（§11.3.4），不能仅靠 `ic_value IS NOT NULL`——否则月末批 `--force` 升级行残留 ic_value 会被双重计入。

### 11.6 实施顺序

§14-9 依赖 §14-2 candidate_pool 5y 在库（✅ 已完成）。顺序：engine 纯函数 + 脚本（TDD RED→GREEN）→ 单日计时（R14-OPEN-6）→ 5y daily IC 回填（**prod 5432，需用户单独确认**）→ 重跑 §14-2 月末批 `--force`（icir 覆盖 default_matrix）→ 真机验证 → 实盘续算接线（CP2，方案定夺后）→ 全回归。关键路径插 §14-2 之后、§14-4 之前（§14-4 ICIR 校准依赖真 IC 时序）。

---

## 12. 实施顺序与依赖

```
§14-1 RM-13 deposit 幂等            （独立，可先做）
       ↓
§14-7 Phase 13 P2 6 项              （独立，可并行）
       ↓
§14-5 ICIR 窗口严格交易日           （独立，~0.2pd）
       ↓
§14-6 共表拆分（方案 A）            （不阻塞 §14-2，但建议先做）
       ↓
§14-2 5y candidate_pool 回填 + ICIR 历史回算（耗时最长 50-80h；2026-06-02 发现 ICIR 段缺日级 IC 生产者 → §14-9 补全）
       ↓
§14-9 日级 IC 生产者（依赖 §14-2 candidate_pool 在库；engine 纯函数 + backfill_daily_ic 5y 回填 ~3-20h + 重跑月末批 --force 切 icir + 实盘续算接线）
       ↓
§14-3 BacktestEngine 真 5 步（方案 A v1.2：直接复用既有 `Scorer.aggregate` + BacktestService 补加载 float_mkt_cap + active_weights_history + BacktestEngine 主循环 3 处改造 + WINSORIZE_MIN_SAMPLES=30 + _lookup_active_weights helper；与 §14-9 无依赖，可并行）
       ↓
§14-8 AttrBackfill                  （依赖 §14-2 candidate_pool）
       ↓
§14-4 ICIR 校准最小集               （依赖 §14-2 + §14-3 + **§14-9 真 IC 时序** 全部到位）
```

**关键路径**：§14-1/§14-7 → §14-5 → §14-6 → §14-2 → §14-9 → §14-3 → §14-8 → §14-4

---

## 13. DoD（Phase 14 收尾标准）

| 项 | 验收标准 |
|---|---------|
| 单元测试 | UT-P14-1-* / UT-P14-2-* / UT-P14-3-* / UT-P14-4-01~02 / UT-P14-5-01 / UT-P14-9-01~04 全部 PASS |
| 集成测试 | INT-P14-1-01/02 / INT-P14-2-01 / INT-P14-3-01 / INT-P14-5-01 / INT-P14-6-01/02 / INT-P14-8-01 / INT-P14-9-01 全部 PASS（测试 DB 5433）|
| E2E | E2E-P14-1-01（deposit 幂等）PASS |
| 真机验收 | 5y candidate_pool 全量在库（≥ 60k 行）+ **§14-9 `factor_ic_window_state` daily 行 ≈ 4700**（~1180 trade_date × 4 strategy）+ 重跑月末批 `--force` 后 `strategy_weights_history.weights_source='icir'` 行 **> 0**（dominant state UPTREND；稀疏 state < 60 样本仍 `default_matrix` 属 SDD §7.4 合规，故不设 ≥700 绝对门槛）+ attribution_history ≥ 200 行 + 真机-P14-4-1/2/3 三项量化阈值全部 PASS |
| ruff | 0 error |
| 冒烟 | 现 deposit/dividend 冒烟扩展幂等用例（同 key 调用 2 次 → 200 + 同 flow_id），编号续接 API-102（具体编号实施时确认）+ Phase 13 冒烟仍 PASS |
| 文档同步 | system_design v1.9 §9 Phase 14 行标 "完成 ✓"；CLAUDE.md §9；Phase 13 评审报告 §8 R13-P2-* 6 项勾选；Phase 12 评审报告 §4 R12-P2-2/P2-3 勾选（R12-P1-2 已在 Phase 13 启动核查勾选，不重复）|
| 评审 | 本设计文档 v1.1 评审通过 + Phase 14 实施评审报告产出（建议 P0/P1 当批修） |

---

## 14. 未在 Phase 14 收口的相关项目

- **Phase 15 RC**：5y 真机端到端验收（评分 + 信号 + 因子 + 监控 + 账户）/ 30 日完整版跨制度回归 / STRONG 相对百分比化 / 覆盖率 ≥ 90% 门槛 / 文档校核 / V1.0 RC 标签
- **V1.5-A 监控增强**：R13-P3 5 项（API-101 Upgrade header / SecretFilter record.__dict__ / factor_monitor_params config_key / TushareAdapter 统一埋点 / Grafana 3 panel）
- **V1.5+ 其他主题**：见 `docs/design/v1_5_roadmap.md` §6
- **V2.0**：边际 VaR / 因子拥挤度 / 涨停板完整版

---

## 15. 风险与开放问题

| 编号 | 风险 | 缓解 |
|------|------|------|
| R14-OPEN-1 | 5y 回填 50-80h 长任务可能在中途网络/资源故障 | scripts 必须 idempotent + 进度持久化 + nohup 后台跑 + SIGINT/SIGTERM graceful handler（§4.2.1 第 5 步）|
| R14-OPEN-2 | §14-6 共表拆分方案 A vs B 选择 | 建议 A 先上（小改动），B 留 V1.5+ |
| R14-OPEN-3 | §14-3 真 5 步 universe 阈值 | `WINSORIZE_MIN_SAMPLES=30` 在 §5.2.3 新定义于 `engine/scorer.py` 顶部（既有代码 grep 无此常量，Phase 14 §14-3 是首个消费方）；ScoringService 未来如需同等门槛检查可同源 import；选 30 的理由：与 §14-2 candidate_pool 默认 50 上限同源思路（横截面统计量稳定性下限）|
| R14-OPEN-4 | RM-13 idempotency_key 前端 UUID 生成时机（disabled 按钮内复用 vs 新生成）| 按钮 mounted 时生成；提交成功后清空 + 下次 enabled 重新生成（避免长时间复用导致跨会话冲突）|
| R14-OPEN-5 | ICIR 历史回算 60 month_end × 5-20s 估算 | 实际跑前先 1 个 month_end 单次计时验证（PG 调用 + commit 可能高于 5s）|
| R14-OPEN-6 | §14-9 日级 IC 回填单日 `score_universe`（全 universe）耗时未实测，5y 工期 ~3-20h 为初判 | 实跑前先单日计时定全量工期；纯 DB 无 Tushare，可 nohup 后台 + 断点续传（`get_existing_daily_ic_dates`）+ SIGINT/SIGTERM graceful |
| R14-OPEN-7 | §14-9 实盘续算 d=t−20 因子值 z 源方案 (a) 落全 universe 日级 z 快照 vs (b) t 日 PIT 复算 `score_universe` 未定 | 回填（迁移关键路径）先行验证；续算接线方案在回填验证通过后定夺（§11.3.5 【设计待定】）|
| R14-OPEN-8 | daily-IC 与 attribution 前向收益定义分歧（前者 adj+严格交易日 / 后者原始 close+日历近似）| V1.0 二者不同消费方不强行统一；V1.5 抽共享 `compute_forward_returns(adj, 严格交易日, 剔涨跌停/停牌)` 纯函数收敛 attribution + daily-IC + ICIR 三处（S-01）|

---

> **依据 CLAUDE.md §11.1 三链必填**：本设计文档 §1.3 启动核查已 grep 跨 system_design + roadmap + reviews/ 三处确认 8 项 scope 完整；§14 显式列出未在本 phase 收口的项目及其归属。
