# Phase 15：V1.0 RC 验收 + 文档校核 + 收尾清理

> 版本：v1.2（实施中，2026-06-27 §15-7 盘点纠偏）
> 状态：设计中 → 待评审 → TDD/验收实施
> 估算：~7-10 pd（V1.0 收尾批次第 5 个、也是最后一个 phase）
> 依据文档：
> - SDD §7-10（评分/信号工业化，v1.4 已合入）/ §9.1（STRONG/MODERATE 分级基线，line 682/688）/ §15（非功能性）/ §2.1（RC 前 ICIR 校准硬性验收）
> - **phase11 §10.4（跨制度回归基线 + 「≥30 STRONG」待相对百分比化，line 945/951）**——注：「§10.4 跨制度回归」是 **phase11 设计文档**章节号；SDD §10.4 = 压力测试(V2.0)、system_design §10.4 = 可维护性，均**非**本基线出处
> - system_design §9 Phase 15 行（8 子项）/ §10 非功能性
> - 评审报告：`phase11_implementation_review_2026-05-19.md`（STRONG 偏差 / 30 日完整版 / 覆盖率门槛 / 旧表归并留 Phase 15）
> - 评审报告：`phase12_implementation_review_2026-05-20.md` §2（P1 合规 banner，RC 前必修）/ §8.2（真机层抽测推迟 RC 同批）
> - 评审报告：`sdd_7_10_doc_sync_review_2026-05-14.md`（§7.2.1 共线性附注残留 P2）
> - 路线图：`v1_post_release_roadmap.md`（本 phase §15-5 由原名 `v1_5_roadmap.md` 改名）

---

## 修订历史

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| v1.0 | 2026-06-26 | 初版设计草案。启动核查锁定 8 子项（§9 原 6 + 核查捞出 2：`factor_ic_history` 旧表 DROP + Phase 12 P1 合规 banner）。决策：旧表 DROP 纳入 Phase 15、5y/30 日长跑在生产腾讯机、先出设计文档。同步回写 system_design §9 Phase 15 行（6→8 子项 + 估算 6-8→7-10 pd + rollup 40-59→41-61） |
| v1.1 | 2026-06-26 | 设计评审收口（`docs/reviews/phase15_design_review_2026-06-26.md` 有条件通过，1 P1 / 2 P2 / 2 P3）：**P1（F-1）** 纠正「§10.4 跨制度回归 / ≥30 STRONG」出处错归——是 **phase11 §10.4（line 951）+ SDD §9.1（line 682/688）**，非 SDD/system_design §10.4（后者分别为压力测试 V2.0 / 可维护性）；头依据 + 15-2/15-3 全文重定向。**P2（F-2）** alembic 0017 撞号：Phase 15 占 0017，V1.5-G 顺延 0018（已改 `v1_5_g_multiuser.md`）。**P2（F-3）** 15-1「日均 BUY 5~15」与 phase11 实测 43~50 对账：拆原始 pipeline BUY（a，~40~50，RC 硬门槛）/ 用户可执行 BUY（b，5~15，UX 概念），15-5 修正 §9 行口径。**P3（F-4）** rollup 41-61→40-62 精确和；**P3（F-5）** 15-5 改名 grep 显式区分正文/归档 reviews 策略 |
| v1.2 | 2026-06-27 | **§15-7 实施启动核查盘点纠偏**：原设计「僵尸双表」premise 不成立——`factor_ic_history`（月度 strategy-composite 因子质量 + `alert_status`）与 `factor_ic_window_state`（日级/聚合 × state ICIR）是两个不同功能，旧表仍每月活跃写入且独家支撑 `/factor-quality`+`/factor-quality/history`+月报告警，新表无等价数据（无 composite 月度 IC、无 alert_status）。**用户拍板（2026-06-27）：现在做完整迁移**——归并进单表 `factor_ic_window_state`（`row_type='monthly_quality'`，复用统计列 + 新增 `alert_status` 列 + `state='ALL'` 哨兵），保留月度计算改写新表，repoint 端点/报表 + DROP 旧表。§8 重写（盘点结论表 + 字段映射表 + 8 步 TDD）；§11 推迟表删除「条件 DROP 推迟 V1.5-J」行（改为不推迟）|

---

## 1. 概述

### 1.1 背景

Phase 15 是 V1.0 收尾批次（Phase 11~15）的**最后一个 phase**，目标：在 5y 真机数据上完成 V1.0 发布前的端到端验收、把 Phase 11~14 留下的"依赖 5y 全量数据才能判定"的下游验证项跑出结论、清掉 V1.0 内的技术债（僵尸旧表 + 合规视觉），并完成 RC 标签 + 生产部署演练。**不引入新业务功能**——纯验证 + 文档 + 收尾清理。

> **定位**：本 phase 是 V1.0「所有阻断用户达成核心目标的问题修复完成才能发布」定位的最终关卡。多用户 + L1/L2/L3（V1.5-G）明确**排在 RC 之后**，不在本 phase。

### 1.2 Scope 总览（system_design §9 Phase 15 行 8 子项）

| 子项 | 主题 | 性质 | pd | 段落 |
|------|------|------|-----|------|
| 15-1 | 5y 真机端到端验收 | 物理资源（长跑）| 1-1.5 | §2 |
| 15-2 | 跨制度回归 30 日完整版（phase11 §10.4）| 物理资源（~10h）| 1-1.5 | §3 |
| 15-3 | STRONG 基线相对百分比化 + SDD §7-10 校核 | 文档 + 验证 | 0.5 | §4 |
| 15-4 | Engine 层 ≥90% 覆盖率门槛验证 | 验证 | 0.5-1 | §5 |
| 15-5 | 文档校核（SDD §7-10 交叉 + §7.2.1 附注 + CLAUDE.md 残留 + roadmap 改名）| 文档 | 0.5-1 | §6 |
| 15-6 | RC 标签 + 生产部署演练 | 发布 | 0.5 | §7 |
| 15-7 | `factor_ic_history` 旧表归并 + DROP | 数据模型重构（TDD）| 1-1.5 | §8 |
| 15-8 | Phase 12 评审 P1 合规 DisclaimerBanner | 前端合规 | 0.25 | §9 |

**合计 ~7-10 pd**。

### 1.3 启动核查（CLAUDE.md §5.1）

| 核查项 | 结论 |
|--------|------|
| 读 system_design §9 Phase 15 行 | ✓ 原 6 子项；启动核查 grep 捞出 2 项（15-7 旧表 DROP / 15-8 合规 banner）并已回写 §9 行（6→8）+ 估算同步 6-8→7-10 pd + rollup 40-62（分项精确和）|
| 模块去向决定 | 8 子项全部纳入本 phase（V1.0 最后一个 phase，无下游可推迟）；15-7 旧表 DROP 经用户决策纳入（不再推迟 V1.5）|
| grep `R\d+-P[23]-\d+` 跨 system_design + roadmap + reviews | R13-P3-1~5 明确归 **V1.5-A**（roadmap §4.5 三链完整），**不属** Phase 15，确认不漏；本 phase 消费的 STRONG/30 日/覆盖率/旧表归并/合规 banner 均非 R 编号、来自 phase11/12 评审正文 |
| 孤儿检查（§3/§5 模块 + §6 端点）| 本 phase 不新增模块/端点；15-7 反而 **DROP** 旧表 `factor_ic_history`（消除僵尸双表，孤儿减少）。多用户/L1L2L3 已归 V1.5-G ✓ |
| 推迟模块引言注明 + §9 更新 + 三链 | 本 phase 无新增推迟项；V1.5-G（多用户）与 R13-P3（V1.5-A）作为 RC 后项，§11 列明归属 |

**资源/风险前置说明**：
- 15-1 / 15-2 在**生产腾讯机**（43.134.63.13，2GB）跑。每日 pipeline 逐日执行、内存按日释放（**非**回测式多月内存累积，避开 `backtest_2gb_memory_wall`），但仍须 off-hours + `nohup` 后台 + `free -m` 监控；单日 pipeline 130~1600s（5y 数据），30 日全跑 ~10h。
- 15-7 旧表 DROP 涉及生产数据迁移 → 执行前**单独确认** + `pg_dump` 旧表备份（C-1）。

---

## 2. 15-1：5y 真机端到端验收

**目标**：在生产 5y 数据上验证五条链路达标，作为 V1.0 可发布的硬证据。

| 链路 | 验收阈值 | 验证方法 |
|------|---------|---------|
| 评分链路 | candidate top score ≥ 85 | 跑 pipeline，查 `candidate_pool` 当日 top composite 0-100 |
| 信号链路 | 原始 pipeline BUY ≈ 40~50/日（与 phase11 实测对账，见下口径）| 多日 pipeline，统计当日 `signal` 表 BUY 行数均值 |
| 因子溯源 | 溯源完整（19 字段 + 三层折叠无空）| `GET /signals/{id}/lineage` 抽样校验 |
| 监控链路 | 告警 PASS（数据质量/因子/调度健康）| `/health/*` + `/metrics` + 触发一次告警闭环 |
| 账户幂等 | RM-13 deposit 幂等 PASS | 真机重复 deposit 同 idempotency_key → 单次入账 |

> **BUY 计数口径对账（F-3）**：system_design §9 Phase 15 行原写"日均 BUY 5~15"，但 phase11 §10.4 收尾（line 1012）5y 实测**原始 pipeline BUY 43~50/日**（pool_capacity 50）。差异源于两个不同计数：(a) **原始 pipeline BUY 信号数**＝当日 `signal` 表 BUY 行（pool 内分位 top 5% 全部发射，~40~50）；(b) **用户可执行 BUY**＝经 RiskChecker（最大持仓/回撤）+ 已持仓 + watchlist 二次过滤后的可操作数（设想 5~15）。**RC 硬门槛用 (a)**——口径与 phase11 实测一致（避免 5~15 误阻断 RC），目标 ≈ 40~50/日、非零且分位合理；(b) 5~15 作为 UX 层"今日可操作"展示概念，不作 RC 阻断门槛。15-5 文档校核时把 system_design §9 行的"5~15"修正/标注为 (a)/(b) 双口径。

**DoD**：五条链路均出具实测数值 + PASS/FAIL 结论，写入收尾验收记录（§10）。BUY 用口径 (a) 与 phase11 实测对账；FAIL 即阻断 RC。

## 3. 15-2：跨制度回归 30 日完整版（phase11 §10.4）

**目标**：Phase 11 收尾只跑 4 trade_date 抽样；本阶段跑 **3 state × 10 trade_date = 30 日**完整版，验证 UPTREND / OSCILLATION / DOWNTREND 三制度下评分-信号行为符合 **phase11 §10.4**（设计文档章节，非 SDD/system_design §10.4）。

- 选 30 个 trade_date：每个 state 取 10 个真实历史交易日（PIT market_state 命中该 state）。
- 每日跑 pipeline，断言：composite_z 量级合理 / pool 规模 / BUY-SELL 计数方向与 state 一致 / market_state PIT 100% 一致。
- **运行**：生产腾讯机 `nohup` 后台串行，`free -m` 每日采样；预计 ~10h。
- **断言精确**（CLAUDE.md）：用 `== N` / 区间，不用宽松上界。

**DoD**：30 日结果表（state × trade_date × 指标）+ 三制度行为符合 §10.4 结论；偏差项归因。

## 4. 15-3：STRONG 基线相对百分比化 + SDD §7-10 校核

**问题**：**phase11 §10.4（line 951）** 写"各 trade_date ≥ 30 只 STRONG（宇宙 ~3200 × 1% ≈ 32）"，假设全市场 ~3200；但 V1.0 universe 过滤后 ~2400，top 1% ≈ 24，5y 实测 STRONG 18~23 → 绝对基线 30 不成立（非缺陷，是基线口径错）。

> **出处澄清**：跨制度回归 + 「≥30 STRONG」基线**只在 `phase11_scoring_industrialization.md §10.4`（line 951）**；SDD 侧相关数字在 **§9.1**（line 682「STRONG 约前 30~35 只」、line 688「top 5% = 160~175 只」），**非** SDD §10.4（压力测试 V2.0）。system_design 全文无此基线正文。

**动作**：
- **phase11 §10.4（line 951）** 把 STRONG 验收基线由绝对数「≥30」改为**相对百分比**「≈ 当日过滤后 universe × 1%」（口径：相对当日 universe ~2400，非全市场 3200）。
- **SDD §9.1（line 682「约前 30~35 只」）** 同步：把绝对数括注改为相对百分比口径（避免与 phase11 双源不一致）。
- 校核 SDD §7-10（§9.1/§9.2）与 system_design / phase11 的分位阈值数值一致（top 5% BUY / top 1% STRONG / bottom 30% SELL）。

**DoD**：phase11 §10.4 + SDD §9.1 措辞改为相对百分比 + 5y 实测落入区间确认；SDD §7-10 ↔ system_design ↔ phase11 三处分位阈值交叉一致。

## 5. 15-4：Engine 层 ≥90% 覆盖率门槛验证

**目标**：Phase 11 留的 Engine 层（`engine/`）≥90% 覆盖率统计兑现。

- 跑 `pytest tests/unit tests/e2e tests/integration --cov=quantpilot.engine --cov-report=term-missing`（`backtest/engine.py` 等编排类由 integration 覆盖，需含 e2e+integration 才反映真实覆盖率；仅 unit 会低估）。
- < 90% 的模块：补 UT 或对**有正当理由**的不可达分支显式标注豁免（不得无注释 `# pragma: no cover`）。

**实施结果（2026-06-27，留档 `docs/reviews/phase15_engine_coverage_2026-06-27.txt`）：Engine 层总覆盖率 91%（1865 stmt / miss 175）✓**。补强 UT：
- `tests/unit/test_engine_degraded_branches.py`（value 历史分位边界 / value 缺列降级 / trend·mean_reversion 数据不足 / factor_pipeline 中性化开关缺数据降级）
- `tests/unit/test_backtest_report.py`（empty-nav + win_rate/profit_loss_ratio 配对路径）

所有纯函数模块 ≥90%；唯一低于 90% 的是 `backtest/engine.py`（435 stmt，75%）——大型回测编排类，happy path 由 INT-P14-3 覆盖，残留为「畸形数据防御分支 + 多配置分支」，仅 integration 可达。**接受为门槛例外**（总覆盖率 91% 已达标；穷尽编排类每条防御分支与 RC 价值不成比例，非纯函数计算逻辑）。

**DoD（已满足）**：`engine/` 总覆盖率 ≥ 90%（91%）；term-missing 报告留档；无新增无注释 `# pragma: no cover`。

## 6. 15-5：文档校核

| 项 | 动作 |
|----|------|
| SDD §7.2.1 共线性附注（残留 P2）| 确认 SDD §7.2.1 趋势策略表后的共线性研究附注已落（`sdd_7_10_doc_sync_review` P0-1 方案 A 应已带入；缺则补）|
| SDD §7-10 ↔ system_design 交叉一致性 | 阈值/窗口/术语逐条核对（与 15-3 联动）|
| CLAUDE.md 残留小项 | Phase 9 行状态等遗留（memory task #136 称已改，核实）|
| **roadmap 改名** | `git mv docs/design/v1_5_roadmap.md docs/design/v1_post_release_roadmap.md` + 全仓 grep 替换正文引用（SDD/system_design/各 phase 设计文档/CLAUDE.md/memory）。**注意**：V1.5-G 设计 `v1_5_g_multiuser.md` 正文引用须一并改。**归档评审报告 `docs/reviews/`（含 V1.5-G/Phase 15 评审）策略（F-5）**：不重写历史归档；改名后在新文件头加「原名 `v1_5_roadmap.md`，2026-06-26 改名」重定向注，reviews/ 内旧名引用作为历史快照保留（可接受悬挂）。grep 范围显式区分"正文引用（改）/ 归档报告（留 + 头部重定向兜底）" |

**DoD**：四项校核完成；改名后 `grep -rn "v1_5_roadmap" docs/ backend/ *.md` 仅剩历史修订记录中的旧名（正文引用全切新名）。

## 7. 15-6：RC 标签 + 生产部署演练

- `git tag v1.0-rc1`（带 annotated message）+ push tag。
- 生产 Docker 部署演练：从 tag 干净部署一遍（`docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build`），核对 `/health` + 前端首屏 + 关键端点。
- 部署文档 `deployment.md` 演练记录更新。

**DoD**：RC tag 就位；从 tag 部署演练 PASS；演练记录留档。

## 8. 15-7：`factor_ic_history` 旧表归并 + DROP（TDD）

**盘点结论（启动核查锁定）**：原设计假设的"僵尸双表"**不成立**——`factor_ic_history` 与 `factor_ic_window_state` 是**两个不同的功能**：

| 维度 | 旧表 `factor_ic_history`（Phase 7 月度因子质量） | 新表 `factor_ic_window_state`（Phase 11 ICIR 校准） |
|------|------|------|
| 粒度 | **月度** × strategy-COMPOSITE 评分（5 行/月：composite/trend/reversion/momentum/value），**state 无关** | **日级/聚合** × 子因子 × **market state** × trade_date |
| 独有列 | `alert_status`（DECAY/INEFFICIENT/FAST_DECAY）、`ic_mean_3m`、`ir_3m`、`half_life_days` | `icir`、`sample_size`、`ci`、`t_stat`、per-state 统计 |
| 写入 | `run_factor_monitoring` 仍**每月末活跃写入**（MonthlyScheduler.run_all line 223） | `run_icir_rebalance` 写入 |
| 消费 | `GET /factor-quality` + `/factor-quality/history` + `report_service` 月报告警段 | `GET /factor-quality/ic-history` + `/current-weights` + scoring 取权重 |

关键：新表**不含 strategy-composite 评分的月度 IC，也无 `alert_status`**——旧表数据**无法从新表派生**。故"DROP 旧表读迁移到 aggregate"在语义上不可行；轻量"停写 + readonly"会**冻结 `/factor-quality` 月度仪表盘**（功能回归）。

**决策（用户拍板 2026-06-27：现在做完整迁移，保 V1.0 单表干净，不留双表/不推迟、不丢功能）**：把月度因子质量行**归并进单表 `factor_ic_window_state`**（`row_type='monthly_quality'` 区分），保留月度计算逻辑但改写新表，DROP 旧表。

**归并字段映射**（`monthly_quality` 行）：

| 旧表列 | 新表落点 | 备注 |
|------|------|------|
| `calc_month` | `trade_date` | 月末交易日 |
| `strategy_name` | `strategy` | composite/trend/... |
| `factor_name` | `factor` | composite_score/trend_score/... |
| `ic_value` | `ic_value` | 当月 IC |
| `ic_mean_3m` / `ic_std_3m` / `ir_3m` | `ic_mean_state` / `ic_std_state` / `icir` | 复用列（语义=3 月滚动；ORM/row_type 注明双语义）|
| `half_life_days`（Numeric 6,1）| `half_life`（Integer）| 取整（仪表盘可接受）|
| `alert_status` | **新增列 `alert_status`** | 唯一新增列 |
| `return_window`（恒 20）| 不存，mapper 常量 20 | 月度路径恒用 20 |
| —（state 无关）| `state='ALL'` 哨兵 | `state` 无 CHECK 约束；`monthly_quality` 行专用，与 daily/aggregate 读路径隔离 |

**步骤（TDD，先 RED）**：
1. alembic 0017：①`ADD COLUMN alert_status String(20) NULL` 到 `factor_ic_window_state`；②数据迁移 `factor_ic_history` 行 → `factor_ic_window_state`（按上表映射，`row_type='monthly_quality'`、`state='ALL'`）；③`DROP TABLE factor_ic_history`。downgrade 反向（重建旧表 + 回拷 monthly_quality 行 + DROP 列）。
   > **迁移号占用（F-2）**：盘内最新 0016；**本 phase 占 0017**。V1.5-G 原 §3.3 声明 0017 已**顺延 0018**（`v1_5_g_multiuser.md` §3.3/§10/§11 同步，down_revision 接 0017）。
2. ORM：`FactorICWindowState` 加 `alert_status` 列 + `row_type='monthly_quality'` 语义注释（含复用列双语义说明）；删除 `FactorIcHistory` 类 + `models/__init__` 导出。
3. `FactorICRepository`：加 `upsert_monthly_quality` / `get_latest_monthly_quality` / `list_monthly_quality_history`（均 `row_type='monthly_quality'`）。
4. `factor_monitor_service.run_monthly`：改写 `factor_ic_window_state`（monthly_quality）；3 月历史 IC 回看（旧 line 230）+ `get_latest` / `get_history` 一并 repoint。
5. `report_service`：月报告警段读 repoint 到 monthly_quality 行。
6. schema/API：`FactorIcHistoryItem` 经 mapper 从 `FactorICWindowState` monthly_quality 行映射（外部响应字段名 `calc_month`/`strategy_name`/... 保持不变，前端零改动）。
7. `seed_demo_data` 写 monthly_quality 行；测试（INT `test_int_factor_monitor_service` / E2E `test_factor_quality_api` / `test_migrations`）更新。
8. 回归：unit+e2e+integration 全绿。

**DoD**：`grep -rn factor_ic_history src/` 0 命中（除迁移文件）；alembic upgrade/downgrade 往返通过；归并前后 `/factor-quality` + 月报告警数值等价（INT 断言）；`/factor-quality` 月度仪表盘功能保留；生产迁移单独确认 + 备份。

**生产迁移实证（2026-06-28，腾讯机 `quantpilot-db-1`，用户单独确认）**：
- 备份：`~/QuantPilot/backups/pre_0017/factor_ic_tables_*.sql`（pg_dump 两表，含 window_state 4920 行）
- 代码交付：`git archive HEAD backend/`（仅 backend 子树，**不触碰服务器加固的 root `docker-compose.prod.yml`**）→ backend 重建 → 启动自动 `alembic upgrade head`
- 实证：`alembic_version=0017` / `factor_ic_history` 已 DROP（information_schema 0 命中）/ `factor_ic_window_state` 仍 4920 行（数据存活）/ `monthly_quality` 0 行（与迁移前空旧表一致）/ `alert_status` 列已加 / `/health` OK / `/factor-quality` + `/history` 返回 401（路由+repoint 服务正常，非 500）/ backend 日志 `0016→0017` 升级干净无异常
- 备注：生产旧表迁移前即 0 行（月度因子质量在生产尚未产出，月末批未遇有候选池数据的月末）——数据搬迁为 no-op，仅结构归并 + DROP；功能零回归。

## 9. 15-8：Phase 12 评审 P1 合规 DisclaimerBanner

**目标**：归因视图（AttributionPanel）补合规声明 `DisclaimerBanner`，与 V1.0 Batch 1 合规组件（`BacktestLimitationsBanner` / 三视图 `DisclaimerBanner`）视觉一致。

**实施核查结论（2026-06-27）：已在 Phase 12 交付，无需再做。** 经核查 `AttributionPanel.vue` 已复用 `<DisclaimerBanner :text="…" />`（commit `954770c`，2026-05-20，对应 phase12 实施评审 P1-3「✅ 已修 2026-05-20」），文案为「历史归因仅用于内部审计与策略反思，反映模型对历史数据的拟合结果，不构成未来收益预测、不构成任何投资建议、不接受委托、不构成投顾服务。」。Phase 15 设计草拟时把此项重列为待办属误记——P1-3 在 Phase 12 已闭环。本阶段仅做验证（banner 在位 + 复用共享组件 + vue-tsc 0），不改代码。

> 注：`DisclaimerBanner.vue` 实际 prop 名为 `text`（非草拟设计示意的 `message`/`type`）；以实际组件为准。

**DoD（已满足）**：归因视图合规声明就位 + 复用 `DisclaimerBanner` 视觉一致；`vue-tsc --noEmit` 0 error（2026-06-27 验证）。

---

## 10. DoD（Phase 15 整体 = V1.0 RC 放行门槛）

- [x] 15-1 五条链路真机验收全 PASS（实测数值留档，见 §10.1）
- [x] 15-2 30 日跨制度回归完整版 PASS（结果表留档，见 §10.2）
- [x] 15-3 STRONG 相对百分比化 + SDD §7-10/system_design 交叉一致
- [x] 15-4 Engine 层覆盖率 ≥ 90%（91%）
- [x] 15-5 文档校核四项完成 + roadmap 改名 + 引用切换干净
- [x] 15-6 RC tag `v1.0-rc1` + 从 tag 部署演练 PASS（数据零变动，见 §10.3）
- [x] 15-7 旧表 DROP + 迁移等价 INT + 回归绿 + 生产迁移实证（§8）
- [x] 15-8 归因视图合规 banner + vue-tsc 0（Phase 12 已交付）
- [ ] 收尾门槛：`uv run ruff check src/ tests/` 0 error；unit+e2e+integration 全绿；vue-tsc 0；冒烟全 PASS
- [ ] 文档头版本与修订历史一致；经验沉淀 CLAUDE.md / memory

### 10.1 15-1 五条链路真机验收记录（2026-06-28，腾讯机生产 5y 数据）

| # | 链路 | 阈值 | 实测 | 结论 |
|---|------|------|------|------|
| 1 | 评分 | top composite ≥ 85 | 2026-06-26 top **99.90**（pool 56 行，区间 97.45~99.90）| ✅ PASS |
| 2 | 信号 | 原始 pipeline BUY ≈ 40~50/日 | 近 12 交易日均 **50 BUY/日**（=pool_capacity 50；0 SELL 符合原始口径 a）| ✅ PASS |
| 3 | 因子溯源 | 19 字段 + 三层折叠无空 | signal 2263：`factor_winsorized`/`neutralized`/`orthogonal` 全 dict[4] + `score_breakdown` 三层 + `composite_z=3.08`/`pct=0.0004`/ICIR 权重；`raw_factors=null`（ORM 设计内遗留字段，非三层产物，business.py:130）| ✅ PASS |
| 4 | 监控 | health + metrics + 告警闭环 | `/health/scheduler` running 无失败 / `/health/data` 延迟 2d、violations 0 / `/metrics` 74 series / 站内信告警闭环 **2239 条**（latest 06-26）| ✅ PASS |
| 5 | 账户幂等 | 重复 deposit 同 key → 单次入账 | 同 `idempotency_key` POST×2 → 均返回 flow id=101（去重）、fund_flow **仅 1 行**、cash 15049.06→15050.06（**+1.00 一次**）；验毕 void 还原 15049.06 | ✅ PASS |

**观察（非阻断）**：WxPusher 推送在本生产为降级态——`WXPUSHER_APP_TOKEN`/`UID` 未配置 → 告警自动降级仅站内信（`wx_error="WxPusher 重试 3 次均失败，已降级"`，文档定义为可选）。如需微信推送在 `.env.prod` 配置两 token。

**结论：5/5 链路 PASS，无 FAIL，不阻断 RC。**

### 10.2 15-2 30 日跨制度回归验收记录（2026-06-29，腾讯机生产 5y 数据）

**运行元信息**：`scripts/pipeline_multi_date.py --dates`（3 state × 10 PIT trade_date = 30 日，从 `market_state_history` 抽取）detached 跑在 `quantpilot-backend-1` 容器内，2026-06-28 22:46 → 06-29 01:20 CST（~2.5h），**30/30 SUCCESS，`EXITCODE=0`**，识别出 3 种 market_state。`notification_channel=None` 无推送。

**phase11 §10.4 基线对账**：

| §10.4 基线 | 阈值 | 30 日实测 | 结论 |
|---|---|---|---|
| 顶分 composite_score | ≥ 99（z≥2.33） | 30/30 落 **[99.32, 100.0]**（最低 d19 99.32） | ✅ |
| 3 种 market_state 全识别 | UPTREND/DOWNTREND/OSCILLATION | 三态全现：**U×10 / D×10 / O×10** | ✅ |
| **market_state PIT 一致** | pipeline 重判 = 抽样桶 | **30/30 回落原桶（100%）** | ✅ |
| ADX × state 自洽 | OSC 低 ADX / 趋势态高 ADX | 中位 **OSC ~19** < DOWN ~32 < UP ~38，清晰分层 | ✅ |
| 5y 历史信号行 > 0 | 每日 BUY > 0 | 30/30 有信号（**44~67 BUY/日**，偶 1 SELL） | ✅ |
| STRONG ≈ universe×1% | 相对百分比 | 已在 **§15-3** 单列验证（5y 实测 STRONG 18~23 落区间） | ✅(15-3) |

**30 日结果表（留档）**：

| label | date | state | adx | pool_top | sig_total |
|---|---|---|---|---|---|
| d1 | 2021-08-05 | OSCILLATION | 24.1 | 99.95 | 61 |
| d2 | 2021-12-03 | OSCILLATION | 12.7 | 99.99 | 52 |
| d3 | 2021-12-20 | UPTREND | 23.6 | 100.00 | 46 |
| d4 | 2022-01-28 | DOWNTREND | 29.0 | 99.81 | 51 |
| d5 | 2022-03-14 | DOWNTREND | 53.8 | 99.68 | 51 |
| d6 | 2022-04-21 | DOWNTREND | 48.8 | 99.99 | 50 |
| d7 | 2022-08-04 | OSCILLATION | 27.2 | 99.97 | 60 |
| d8 | 2022-09-05 | DOWNTREND | 32.4 | 99.87 | 56 |
| d9 | 2022-10-19 | DOWNTREND | 45.4 | 99.82 | 53 |
| d10 | 2023-01-19 | UPTREND | 29.1 | 99.98 | 50 |
| d11 | 2023-02-16 | UPTREND | 23.1 | 100.00 | 50 |
| d12 | 2023-03-13 | OSCILLATION | 19.0 | 99.72 | 60 |
| d13 | 2023-06-14 | DOWNTREND | 31.1 | 99.94 | 53 |
| d14 | 2023-07-25 | OSCILLATION | 14.8 | 99.98 | 62 |
| d15 | 2023-12-05 | OSCILLATION | 28.0 | 99.51 | 67 |
| d16 | 2023-12-25 | DOWNTREND | 53.1 | 99.90 | 50 |
| d17 | 2024-06-18 | OSCILLATION | 23.5 | 99.99 | 61 |
| d18 | 2024-06-24 | DOWNTREND | 29.5 | 99.67 | 61 |
| d19 | 2024-08-07 | DOWNTREND | 27.8 | 99.32 | 51 |
| d20 | 2024-09-12 | DOWNTREND | 28.7 | 99.41 | 55 |
| d21 | 2024-10-16 | UPTREND | 46.7 | 99.99 | 47 |
| d22 | 2024-11-11 | UPTREND | 47.0 | 99.98 | 51 |
| d23 | 2025-02-21 | OSCILLATION | 16.2 | 99.91 | 60 |
| d24 | 2025-07-02 | OSCILLATION | 15.4 | 99.95 | 66 |
| d25 | 2025-07-28 | UPTREND | 43.9 | 100.00 | 46 |
| d26 | 2025-08-18 | UPTREND | 43.5 | 99.98 | 48 |
| d27 | 2025-09-05 | UPTREND | 42.3 | 100.00 | 44 |
| d28 | 2026-01-08 | OSCILLATION | 18.8 | 99.98 | 60 |
| d29 | 2026-01-22 | UPTREND | 26.2 | 100.00 | 49 |
| d30 | 2026-05-20 | UPTREND | 32.9 | 100.00 | 46 |

**偏差归因（均非缺陷）**：

1. **d30（2026-05-20）duration 0.6s**（vs 其余 180~720s）—— DailyPipeline **resume 幂等**：该日生产每日管线已跑过，检测到 existing run → 短路不重生成；指标从既有数据读出仍正确。属 [[is_st_daily_pipeline_bug]] 记录的 resume 幂等行为。
2. **duration 漂移 180s→720s**（越近期越慢）—— 近期日 ValueStrategy 5y PE/PB 分位窗口数据全 + universe 更大 → 计算量增。
3. **OSCILLATION 日信号数（60~67）系统性高于趋势态**（UP 44~51）—— 震荡市过滤更松、信号更密；方向仍 BUY 主导，符合预期。
4. **SELL≈0~1**：回归无既有持仓 → SELL 仅在持仓跌出时触发；BUY=新晋 top candidate，故全程 BUY 主导（口径 a，与 §10.1 链 2 一致）。
5. **早期日（2021~2022）降级日志** `total_equity 全为 NULL → F-4 跳过` + neutralize `dof=0`—— 早期财务/因子覆盖稀疏触发**设计内降级分支**（15-4 已补 UT 覆盖），run 仍 SUCCESS。

**结论：30/30 PASS，§10.4 全部基线满足，无 FAIL，不阻断 RC。**

### 10.3 15-6 RC tag + 从 tag 部署演练记录（2026-06-29，腾讯机生产）

**RC tag**：`v1.0-rc1`（annotated，打在 `8eb7356`，含 Phase 11~15 全部 V1.0 收尾成果）已 push GitHub。

**部署演练方式**（最小爆炸半径 + 可复现）：
1. `git archive v1.0-rc1 backend/` → tar.gz → scp 腾讯机 `/tmp` → 解压 `~/QuantPilot/backend/`（**仅 backend 子树，不碰服务器加固的 root `docker-compose.prod.yml`/`.env.prod`**）
2. `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build backend`（**仅重建 backend**；db/redis 容器保持 Running 未动）
3. 启动自动 `alembic upgrade head`

**安全措施**：演练前 ① 备份服务器 `backend/` 目录（`backups/backend_pre_rc1_rehearsal_*.tar.gz`）② 抓数据基线快照。前置核查证明 `v1.0-rc1` 的 `backend/src`+`backend/alembic` 与生产运行代码（15-7 部署点）**逐字节相同**，唯一差异是 15-4 两个新测试文件（不进运行时）→ 本演练本质是**功能等价的 no-op 重建**。

**数据完整性比对（部署前 = 部署后，逐项相等 → 零数据变动）**：

| 项 | 部署前 | 部署后 | 结论 |
|---|---|---|---|
| alembic_version | 0017 | 0017 | ✅ |
| public 表数 | 29 | 29 | ✅ |
| daily_quote | 6396652 | 6396652 | ✅ |
| candidate_pool | 85279 | 85279 | ✅ |
| signal | 3189 | 3189 | ✅ |
| fund_flow | 100 | 100 | ✅ |
| trade_record | 90 | 90 | ✅ |

**端点核对**：`/health` ok / 前端首屏 200（`<title>QuantPilot — 量化领航</title>` + `#app` 挂载点）/ `/api/v1/signals` 与 `/api/v1/factor-quality/current-weights` 未鉴权均 401（路由+鉴权门生效）。backend 容器 healthy（启动 20s）；alembic 仅初始化 context 无 `Running upgrade` 行 = 已在 head，no-op 确认。

**结论：从 tag 干净部署可复现，PASS；生产数据零变动，不破坏生产环境。**

## 11. 推迟项（RC 后，归属清晰）

| 项 | 充分理由 | 去向 |
|----|---------|------|
| 多用户 + 注册 + L1/L2/L3 | RC 后首个 V1.5 phase（已设计登记）| V1.5-G `v1_5_g_multiuser.md` |
| R13-P3-1~5 监控增强 | 非阻断增强 | V1.5-A（roadmap §4.5 三链）|
| OpenTelemetry / AlertManager / APScheduler 集群化 | V1.5+ 增强 | roadmap V1.5-A/I |
| ~~（条件）`factor_ic_history` DROP 若盘点不等价~~ | 已决策**现在做完整迁移**（§8 归并进 `factor_ic_window_state` `row_type='monthly_quality'` + DROP），不推迟 | — |

## 12. 收尾后续

RC 通过 → 打 `v1.0` 正式 tag → V1.0 发布 → 启动 V1.5（首个 V1.5-G 多用户，设计已就绪）。本 phase 收尾时按 phase-closeout（CLAUDE.md §5.2）核查。

---

> **下一步**：本文档为设计草案，提交设计评审；评审通过后按 §2~§9 顺序 TDD/验收实施（建议先做文档+代码类 15-3/15-5/15-7/15-8，再跑验证类 15-1/15-2/15-4，最后 15-6 RC tag + 部署演练）。
