# Phase 14 设计评审报告 v1.3（§14-9 日级 IC 生产者 + 整合性专项）

> 评审对象：`docs/design/phases/phase14_account_integrity.md` v1.3（2026-06-02 新增 §14-9）
> 评审重点：**与 SDD / system_design 的整合性**（用户指定）+ §14-9 新增内容技术正确性
> 评审日期：2026-06-02
> 评审人：Claude（设计评审）
> 依据：CLAUDE.md §0（C-3/C-5）/ §5.2 收尾门槛 / §5.5 编号规约 / §11.1 三链；SDD §7.4；system_design §9
> 前序评审：`phase14_design_review_2026-05-25.md`（v1.0）/ `phase14_design_review_v1_1_short_2026-05-25.md`（v1.1 短复审）

---

## 0. 评审结论

**有条件通过**：§14-9 技术方案本身正确、根因诊断扎实、与 SDD §7.4 数据契约逐条对齐、可进入 TDD。但存在 **1 项 P1（文档头版本/估算未同步，直接违反 §5.2 收尾门槛）** 必须先修；**2 项 P2**（system_design §9 估算脚注部分同步漏项 / §14-9 daily-aggregate 行碰撞未处理）建议进入 TDD 前修正；**3 项 P3** 可随实施批改。

整合性总评：**与 SDD §7.4 高度一致**（IC_daily 定义、lag 20、state 子集语义逐条吻合）；**与 system_design §9 Phase 14 行已同步**（item (9) + 根因链 + 行内估算 ~7-12 pd 均到位）。唯一整合缺口是 §9 **累计估算脚注**漏同步（P2-1）+ phase14 文档头自身版本/估算自相矛盾（P1-1）。

| 等级 | 数量 | 是否阻断 TDD |
|------|------|-------------|
| P0 | 0 | — |
| P1 | 1 | **是**（文档治理红线） |
| P2 | 2 | 建议（P2-2 影响 §14-9 实现正确性） |
| P3 | 3 | 否 |

---

## 1. 评审范围与方法

本次评审在三个层面交叉核对，全部结论经代码/文档实证（非纸面推演）：

1. **SDD 整合性**：§14-9 数据契约（§11.2）逐列对照 SDD §7.4 IC_daily 定义（line 458-484）。
2. **system_design 整合性**：§9 Phase 14 行（line 1379）+ §9 累计估算脚注（line 1389）是否同步 v1.3 变更（C-5 红线 + §5.2 文档同步）。
3. **代码实证**：v1.3 §14-9 根因链（"日级 IC 无生产者")、复用轮子、碰撞边界逐条 grep/read 验证当前 ORM/Repository/Service 真实状态。

---

## 2. 整合性核查（SDD / system_design）— 用户指定重点

### 2.1 与 SDD §7.4 — ✅ 高度一致

逐条对照 §14-9 §11.2 数据契约 vs SDD §7.4：

| 契约项 | §14-9 设计 | SDD §7.4 | 结论 |
|--------|-----------|----------|------|
| IC_daily 定义 | `corr(factor_value_{t-20}, return_{t-20→t})` 全 universe Spearman Rank IC | line 460 同式 + line 471 Rank IC | ✅ 一致 |
| 命名 lag | trade_date = 因子值日 d（= 观测日 t 的 t−20） | line 460 "IC_daily(t) 名为 t 日，信号源 t-20" | ✅ 一致 |
| state 归属 | 每日一行，state = `market_state[d]` 因子值日真实 state | line 462/484 "按 state_{t-20} 子集计算"，观察日 state 决定归属 | ✅ 一致 |
| 最小样本 | 稀疏 state 子集 < 60 仍 default_matrix（§11.5） | line 462 "子集 ≥ 60 最小样本门槛" + line 478 合规降级 | ✅ 一致 |
| warmup | 依赖 ≥272 交易日历史 | line 454 `ICIR_WARMUP_DAYS=272` | ✅ 一致 |

**关键验证**：§14-9 把 daily 行写在因子值日 d 且 state=state[d]，与 `rolling_icir_state`（`factor_monitor_service.py:443-480`）"按单一 state 过滤窗口 + 计数非空 ic_value 行 ≥ `_STATE_MIN_SAMPLES`(60)" 的真实采样逻辑**语义闭合**——写入此契约后 dominant state（UPTREND）窗口内 daily 行数可达 ≥60，确能让 `weights_source` 切到 `icir`，方案有效性成立。

### 2.2 与 system_design §9 Phase 14 行 — ✅ 已同步

- **item (9)**（line 1379）：§14-9 日级 IC 生产者已写入，含根因链（`IC_daily ... 无任何生产者`、`upsert_ic_daily 仅测试调用`）、落地件（engine 纯函数 + `backfill_daily_ic.py` + CP2 续算 + 月末批 `--force`）、解锁 item (4)。✅
- **item (2)**（line 1379）：已补缺口标注"2026-06-02 迁移准备期发现 item (2) 隐藏未交付 ... 详见 item (9) + §14-9"。✅
- **行内估算**（line 1379 header）：`~7-12 pd` 已更新。✅
- **C-5 回写顺序**：§1.3 v1.3 增补启动核查表声明"先于本设计正文回写 §9"，符合 C-5"先回写 SDD/system_design 再写 phase 文档"。✅

### 2.3 整合性缺口（→ 见 §4 findings）

- **P1-1**：phase14 文档头（line 3 `版本：v1.2`、line 5 `估算：~5-8 pd`）未随 v1.3 更新，自相矛盾且违反 §5.2。
- **P2-1**：system_design §9 **累计估算脚注**（line 1389）仍写"Phase 14 ~5-8 pd"+"Phase 11~15 合计 ~38-55 pd"，v1.3 只同步了 Phase 14 **行**未同步**脚注 rollup**。

---

## 3. §14-9 技术内容评审（设计正确性）

### 3.1 设计亮点

1. **根因诊断扎实**：迁移准备期实跑 `backfill_icir_rebalance.py` 发现"61 月末中 49 个有 ≥272 日历史的月全产 default_matrix、`factor_ic_window_state` 0 行"，非纸面推演。根因链经本评审 grep 实证完全吻合——`upsert_ic_daily` 全仓仅测试 + 两个 §14-2 脚本（且脚本只 **读** `get_ic_daily_window`），生产 pipeline CP2 确无写日级 IC 路径。
2. **全 universe vs candidate_pool 截断的区分**（§11.3.2）正确——指出 candidate_pool 仅 top-N pool（~60/日），IC 必须全 universe（~2400），截断截面会系统性低估 IC。这是设计的核心洞察。
3. **复用既有轮子**符合 v1.2 短复审教训——`FactorMonitorEngine.calc_ic`（`engine/factor_monitor.py:17` 已 `spearmanr`+dropna）、`score_breakdown_raw[strategy]["z_raw"]`（已被 `attribution_service.py:130` 消费、P12 实证等价）、`engine/diagnostics/ic_aggregator.py`（§14-4 既有，已确认存在）。
4. **实施顺序正确**（§11.6 / §12）：§14-9 插在 §14-2 之后、§14-4 之前；关键路径图已更新。
5. **prod 5432 单独确认红线**（§11.6）已显式标注，符合 C-1。

### 3.2 已确认正确的整合点

- `ScoringService.score_universe`（类名在 `services/strategy_service.py:39`，方法 :399）返回全 universe `list[CompositeScore]`——§14-9 §11.3.2 引用准确。
- 前向收益剔涨跌停/停牌（§11.3.3）对齐 SDD §7.4 line 473。
- 末尾 20 交易日 forward return 不可得 → 留空（§11.5），实盘续算 +20 日补上——边界处理正确。

---

## 4. 问题清单

### P1（阻断：进入 TDD 前必修）

**P1-1：phase14 文档头版本号/估算未随 v1.3 同步，且自相矛盾 — 违反 CLAUDE.md §5.2 收尾门槛**

- `phase14_account_integrity.md` line 3：`> 版本：v1.2（2026-05-25 短复审收口）`，而修订历史首行（line 23）已是 **v1.3（2026-06-02）**。
- line 5：`> 估算：~5-8 pd`，而 §1.2（line 52）已写"合计 **~7-12 pd**"，且 line 5 自身与 §1.2、修订历史 v1.3 条目（"估算 ~5-8 → ~7-12 pd"）三处矛盾。
- CLAUDE.md §5.2 明列收尾门槛："文档头部 `版本：` 与修订历史最新版本号一致"。当前直接违反。
- **修复**：line 3 → `版本：v1.3（2026-06-02 新增 §14-9 日级 IC 生产者）`；line 5 估算 → `~7-12 pd`；line 4 状态行补"v1.3 §14-9 评审"出处。

### P2（建议：进入 TDD 前修正）

**P2-1：system_design §9 累计估算脚注未同步（partial sync）— 违反 C-5 + §5.2 文档同步**

- system_design §9 Phase 14 **行**（line 1379）已更新 `~7-12 pd`，但 §9 **累计估算脚注**（line 1389）仍写：`+ Phase 14 ~5-8 pd（... 估算同步自 Phase 14 设计 v1.1 评审 P2-3，2026-05-25）`，且开头"**Phase 11~15 估算合计 ~38-55 pd（8-11 周）**"未含 v1.3 新增的 ~2-4 pd。
- phase14 v1.3 修订历史声称"同步 system_design §9 Phase 14 行 ... 估算 ~5-8 → ~7-12 pd"——只同步了**行**，漏了**脚注 rollup**。这正是 §10 推迟三链/文档同步要求"一处改全处投影"的反面教材。
- **修复**：line 1389 脚注 Phase 14 改 `~7-12 pd`；合计改 `~40-59 pd`（38+2~55+4）；并补一句估算变更出处"v1.3 新增 §14-9 日级 IC 生产者，2026-06-02"。

**P2-2：§14-9 daily 行与 §14-2 aggregate 行在 month_end 4-tuple 碰撞未处理 — 影响 §14-9 实现正确性**

- 当前 `factor_ic_window_state` 保留**全表 UNIQUE** `uq_factor_ic_window_state_skft (strategy,factor,state,trade_date)`（`models/business.py:233`）+ aggregate partial index（:244，仅索引优化，唯一性由全表 UNIQUE 兜底）。
- `get_ic_daily_window`（`factor_ic_repository.py:199`）区分 daily/aggregate **仅靠 `ic_value IS NOT NULL`**，不滤 `row_type`；正常情况靠"`upsert_ic_aggregate` 不写 ic_value"维持区分。
- **碰撞路径**：§14-9 daily 行写在因子值日 d，而每个 month_end 本身也是某观测的因子值日 d → §11.6 顺序"先 daily 回填、再 `--force` 重跑月末批"会让 `upsert_ic_aggregate` 命中既有 daily 行的 4-tuple → `on_conflict_do_update` 升 `row_type='aggregate'` 但 **set_ 不含 ic_value**（`factor_ic_repository.py:161-171`）→ 旧 daily ic_value 残留 → 产生"`row_type='aggregate'` 且 ic_value 非空"的混合行 → 被 `get_ic_daily_window`（按 ic_value 非空）**与** `get_recent_aggregates`（按 row_type）**双重计入**。
- 影响量：~60 month_end（dominant state 各 1 行）/ ~4700 daily 行，数值影响小，但属数据模型完整性瑕疵，§14-9 §11.2/§11.5 完全未提及。
- **修复（任一）**：(a) §14-9 明确将 `get_ic_daily_window` 谓词增 `row_type='daily'`（最干净，且使 daily/aggregate 区分显式化）；(b) §11.5 已知边界登记此碰撞 + 量化影响 + 确认可接受；(c) backfill_daily_ic 跳过 month_end 当日写入（不推荐，破坏窗口连续性）。建议 (a)。

### P3（可随实施批改）

**P3-1：§11.3.1 `calc_ic` 签名标注不准**。设计写 `calc_ic(...) -> float`，实际 `engine/factor_monitor.py:21` 为 `-> float | None`（有效样本 < 5 返 None）。`compute_daily_ic` 必须处理 None 分支。建议 §11.3.1 修签名 + §11.4 UT-P14-9-01 增"某策略当日 IC=None 时写 NULL 行 vs 不写"的明确用例（关系到 `get_ic_daily_window` 的 ic_value 非空过滤是否漏算样本）。

**P3-2：修订历史表行序倒置**。phase14 文档修订历史 v1.3（line 23）列在 v1.2（line 24）**之上**，与其余各行"旧版在上"惯例不一致。统一即可。

**P3-3：§14-9 实盘续算 z 源【设计待定】推迟三链未落全**。§11.3.5 + R14-OPEN-7 的方案 (a)/(b) 推迟属合规（符合 §5.4 充分理由"验收标准未定义 / 依赖回填验证结果"），但 §1.3 v1.3 增补启动核查表"实盘续算 z 源【设计待定】见 §11.3.5"仅有链内指向，未明确推迟去向（消费于本 §14-9 续算接线步、不跨 phase）。建议在 §11.3.5 一句话补"消费节点：§14-9 续算接线（回填验证通过后本批内定夺，不推迟出 Phase 14）"以闭合 §11.1 防丢失。

---

## 5. 充分理由检查（C-3 / §5.4）

| 待定项 | §5.4 充分理由 | 判定 |
|--------|--------------|------|
| §11.3.5 实盘续算 z 源 (a)/(b) | 验收标准未定义（依赖回填实跑性能数据定夺存储 vs 算力权衡） | ✅ 合规，本批内消费（见 P3-3 补链） |
| R14-OPEN-6 单日 IC 耗时未实测 | 物理资源约束（需实跑计时） | ✅ 合规，实施期先单日计时 |

无伪推迟。

---

## 6. 修订追踪表

| 编号 | 等级 | 处置建议 | 责任 | 状态 |
|------|------|---------|------|------|
| R14d3-P1-1 | P1 | phase14 文档头 line 3 版本→v1.3 + line 5 估算→~7-12 pd + line 4 状态补 v1.3 | 设计 | ✅ 已收口 v1.3-r1 |
| R14d3-P2-1 | P2 | system_design §9 脚注 line 1389 Phase 14→~7-12 pd + 合计→~40-59 pd | 设计 | ✅ 已收口 v1.3-r1 |
| R14d3-P2-2 | P2 | §14-9 处理 daily/aggregate month_end 4-tuple 碰撞（建议 `get_ic_daily_window` 增 `row_type='daily'` 谓词 + 对应 UT/INT） | 实施 | ✅ 已写入设计 v1.3-r1（TDD RED 覆盖）|
| R14d3-P3-1 | P3 | §11.3.1 calc_ic 签名→`float\|None` + §11.4 增 IC=None 写入用例 | 实施 | ✅ 已写入设计 v1.3-r1（TDD RED 覆盖）|
| R14d3-P3-2 | P3 | 修订历史表行序统一 | 设计 | ✅ 已收口 v1.3-r1 |
| R14d3-P3-3 | P3 | §11.3.5 补实盘续算 z 源消费节点（闭合 §11.1） | 设计 | ✅ 已收口 v1.3-r1 |

---

## 7. 评审决策

- **§14-9 技术方案**：通过。根因诊断、SDD §7.4 数据契约对齐、复用轮子、实施顺序均正确，可进入 TDD。
- **文档治理**：P1-1 必修（§5.2 红线）；P2-1 必修（C-5 + 文档同步，纯文档改动）；二者修正后 v1.3 设计即就绪。
- **实现正确性**：P2-2 建议在 §14-9 TDD 的 RED 阶段一并覆盖（碰撞 INT 用例），避免回填实跑后才暴露混合行。
- **P3**：随 §14-9 实施批改。

> 建议作者本日内出 phase14 **v1.3-r1**（P1-1 + P2-1 文档同步 + P2-2 §14-9 碰撞处理写入设计 + P3 批改），r1 后 §14-9 可直接进入 TDD。

---

## 8. 补充自审（评审组报告外，作者本日补核）+ 全项收口确认（v1.3-r1，2026-06-02）

### 8.1 补充发现（S 项，报告未覆盖）

| 编号 | 等级 | 问题 | 处置 |
|------|------|------|------|
| S-01 | P2 | 既有两个前向收益实现 `FactorMonitorService._calc_forward_returns`(:299) / `AttributionService._calc_forward_returns_panel`(:273) 均 **原始 close（未复权）+ 日历日近似窗口（非严格 t=d+20）+ 无涨跌停/停牌剔除**，不可复用于 IC——20 日窗口内除权会扭曲。报告 §3.2 曾按设计字面把"剔涨跌停/停牌"记为已对齐，**未抓到可复用函数实际做不到**（评审盲点） | §11.3.3 显式警告禁止复用 + 改 **adj 后复权 + 严格交易日 `get_next_trade_date(d,20)` + 剔除**；attribution 定义分歧登记 **R14-OPEN-8**（V1.5 抽共享 `compute_forward_returns` 统一三处）✅ |
| S-02 | P3 | 每日最小横截面未定义（`calc_ic` 仅 <5 返 None，地板过低，噪声 IC 污染窗口）| §11.3.3 + UT-P14-9-02 增 `_DAILY_IC_MIN_XS=30`，对齐后 N 不足跳过该日 + `logger.info` ✅ |
| S-03 | P3 | INT-P14-9-01 fixture 误列 `candidate_pool`（backfill 经 `score_universe` 读原始数据**不读 pool**）| §11.4 改 seed `daily_quote/financial_data/market_state_history` ✅ |

### 8.2 全项收口

报告 6 项（P1×1 + P2×2 + P3×3）+ 补充自审 3 项（S-01/02/03）= **9 项全部收口**，落地见 `phase14_account_integrity.md` v1.3-r1 修订历史 + §11/§13/§15。

- **文档治理红线**：§5.2 文档头版本/估算（P1-1）+ C-5 system_design §9 脚注 rollup（P2-1）已修复。
- **§14-9 实现正确性**：P2-2 daily/aggregate 碰撞（`get_ic_daily_window` 增 `row_type='daily'` 谓词 + INT-P14-9-02）/ P3-1 `calc_ic -> float|None` None 处置 / S-01 前向收益严格定义 / S-02 每日最小横截面——均已写入设计正文 + 测试矩阵。

**结论：v1.3-r1 就绪，§14-9 可进入 TDD**（RED 阶段须覆盖 INT-P14-9-02 碰撞 + UT-P14-9-01 IC=None + UT-P14-9-02 min-XS 用例）。
