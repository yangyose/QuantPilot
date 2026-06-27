# Phase 15：V1.0 RC 验收 + 文档校核 + 收尾清理

> 版本：v1.0（设计草案，2026-06-26）
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

**目标**：Phase 11 留的 Engine 层（`engine/` 纯函数）≥90% 覆盖率统计兑现。

- 跑 `uv run pytest tests/unit --cov=quantpilot.engine --cov-report=term-missing`。
- < 90% 的模块：补 UT 或对**有正当理由**的不可达分支显式标注豁免（不得无注释 `# pragma: no cover`）。

**DoD**：`engine/` 覆盖率 ≥ 90%，term-missing 报告留档；豁免项均带理由注释。

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

**现状**：Phase 11 引入新表 `factor_ic_window_state`（daily + aggregate row_type），但旧表 `factor_ic_history` 仍被 `factor_monitor_service.run_monthly`（旧路径）**写**、`report_service` **读** → 僵尸双表。phase11 评审挂"Phase 12 或 Phase 15 末归并 + DROP"，Phase 12 未做，本阶段在 V1.0 内清掉。

**步骤（TDD，先 RED）**：
1. 盘点 `factor_ic_history` 全部读写点：`factor_monitor_service.py`（write line 213 旧 run_monthly）、`report_service.py`（read）、`factor_ic_repository.py`、`monthly_scheduler.py`、`models/business.py`。
2. `report_service` 读迁移到 `factor_ic_window_state`（`row_type='aggregate'` 取 icir；语义对齐）——先写 INT 断言迁移后报表数值与旧表等价。
3. 旧 `run_monthly` 写旧表路径：确认 MonthlyScheduler dispatch 已切 `apply_monthly_rebalance`（写新表），旧 `run_monthly` 退役（删写旧表 or 整体删该死路径）。
4. alembic 0017（Phase 15）：`DROP TABLE factor_ic_history`（生产数据若需保留先 `pg_dump`；本质是历史 IC，新表已有等价）。ORM `FactorICHistory` + `factor_ic_repository.py` 删除或收敛。
   > **迁移号占用（F-2）**：盘内最新 0016；**本 phase 先占 0017**（Phase 15 先于 RC 落地）。V1.5-G 原 §3.3 也声明 0017，须**顺延 0018**——已在 `v1_5_g_multiuser.md` §3.3/§10/§11 同步改为 0018（V1.5-G 排 RC 后实施，其 down_revision 接 Phase 15 的 0017）。
5. 回归：unit+e2e+integration 全绿；report 相关测试通过。

**DoD**：`grep -rn factor_ic_history src/` 0 命中（除迁移注释）；alembic upgrade/downgrade 往返通过；report 数值迁移前后等价（INT 断言）；生产迁移单独确认 + 备份。

> **【降级说明】** 若盘点发现 `report_service` 依赖旧表独有的历史粒度（新表 aggregate 无法等价），则本子项降级为"旧表停写 + 标注 readonly + DROP 推迟 V1.5"，并落三链（§11）。盘点结论在实施启动核查锁定。

## 9. 15-8：Phase 12 评审 P1 合规 DisclaimerBanner

**目标**：归因视图（AttributionPanel / 归因相关视图）补合规声明 `DisclaimerBanner`，与 V1.0 Batch 1 合规组件（`BacktestLimitationsBanner` / 三视图 `DisclaimerBanner`）视觉一致。RC 前必修，否则外部合规复审退。

- 先 grep 确认现有组件命名（`BacktestLimitationsBanner` / `DisclaimerBanner`）+ 文案规范。
- 归因视图加 `<DisclaimerBanner type="warning" message="历史归因仅用于内部审计与策略反思，不构成未来收益预测，不构成投资建议。" />`。
- vue-tsc 0 error。

**DoD**：归因视图合规声明就位 + 视觉一致；vue-tsc 通过。

---

## 10. DoD（Phase 15 整体 = V1.0 RC 放行门槛）

- [ ] 15-1 五条链路真机验收全 PASS（实测数值留档）
- [ ] 15-2 30 日跨制度回归完整版 PASS（结果表留档）
- [ ] 15-3 STRONG 相对百分比化 + SDD §7-10/system_design 交叉一致
- [ ] 15-4 Engine 层覆盖率 ≥ 90%
- [ ] 15-5 文档校核四项完成 + roadmap 改名 + 引用切换干净
- [ ] 15-6 RC tag + 从 tag 部署演练 PASS
- [ ] 15-7 旧表 DROP（或降级 + 三链）+ 迁移等价 INT + 回归绿
- [ ] 15-8 归因视图合规 banner + vue-tsc 0
- [ ] 收尾门槛：`uv run ruff check src/ tests/` 0 error；unit+e2e+integration 全绿；vue-tsc 0；冒烟全 PASS
- [ ] 文档头版本与修订历史一致；经验沉淀 CLAUDE.md / memory

## 11. 推迟项（RC 后，归属清晰）

| 项 | 充分理由 | 去向 |
|----|---------|------|
| 多用户 + 注册 + L1/L2/L3 | RC 后首个 V1.5 phase（已设计登记）| V1.5-G `v1_5_g_multiuser.md` |
| R13-P3-1~5 监控增强 | 非阻断增强 | V1.5-A（roadmap §4.5 三链）|
| OpenTelemetry / AlertManager / APScheduler 集群化 | V1.5+ 增强 | roadmap V1.5-A/I |
| （条件）`factor_ic_history` DROP 若盘点不等价 | report 历史粒度依赖（§8 降级分支）| V1.5-J 技术债 + 三链 |

## 12. 收尾后续

RC 通过 → 打 `v1.0` 正式 tag → V1.0 发布 → 启动 V1.5（首个 V1.5-G 多用户，设计已就绪）。本 phase 收尾时按 phase-closeout（CLAUDE.md §5.2）核查。

---

> **下一步**：本文档为设计草案，提交设计评审；评审通过后按 §2~§9 顺序 TDD/验收实施（建议先做文档+代码类 15-3/15-5/15-7/15-8，再跑验证类 15-1/15-2/15-4，最后 15-6 RC tag + 部署演练）。
