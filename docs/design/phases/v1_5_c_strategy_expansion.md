# V1.5-C：策略扩展（风险调整动量 + Piotroski 过滤 + 低波动 + 资金动向 + 插件沙箱）

> 版本：v0.13（C4 范围收窄，2026-08-28）
> 状态：**C0 全量上线**（2026-08-19 六步生产收尾逐步实证，alembic 至 0025，`daily_ic_producer` 19:30 Job 已激活并完成首跑）；**C1-1 已交付**（约束落点统一，commit ac069e5，未部署）；**C1-2 已交付**（风险调整动量，commit 85df015，未部署）；**C1-3 已交付**（价格窗口按交易日推导，commit be6d6d6，未部署）；C1 面板对比**待在第二台 24h 算力机上起跑**（迁移 runbook `docs/guides/machine_migration.md`）；C2-C5 待启动。scope 锁定 C0-C5 六子批、零推迟
> 估算：roadmap §6 登记 **9-13 pd** → 启动核查重估 **~12.5-20 pd** → 本次详细设计展开后 **~14.5-22 pd**（分项见 §1.2；增量来自 C0 前置闭环 + C1 策略约束落点统一，二者均为设计展开期实证发现的既有缺口，非范围扩张）
> 实施顺序（沿用 V1.5-A「先轻后重」先例）：**C0 → C1 → C2 → C3 → C4 → C5**
> 依据文档：
> - roadmap `v1_post_release_roadmap.md` §1（资金动向 / 低波动 / 插件沙箱 3 产品功能行）+ §3（SDD-EXT-04/08）+ §6 主题表 V1.5-C 行
> - SDD `QuantPilot_SDD.md` §7.2.2（均值回归）/ §7.2.3（动量策略 + 风险调整动量注）/ §7.2.4（价值陷阱规避）/ §7.3（策略扩展 V1.5+）/ §7.4（因子 ICIR 监控与权重校准）/ §15.2（策略插件沙箱安全规范）
> - SDD 外部评审 `docs/reviews/SDD_review_outside_2026-04-22.md` §7.2.2（Piotroski F-Score 硬过滤）/ §7.2.3（波动率惩罚系数）
> - Phase 11 评分工业化 `docs/design/phases/phase11_scoring_industrialization.md`（5 步管线 / strategy_weights 矩阵 / 逐策略 ICIR / Gram-Schmidt 正交化——新增策略入 composite 的耦合面，见 §8）
> - Phase 14 `phase14_account_integrity.md` §14-9（日级 IC 回填与 ICIR rebalance 真机验收——C0 直接续接）

---

## 修订历史

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| v0.1 | 2026-08-10 | 初版（启动核查）。执行 CLAUDE.md §5.1：确认 V1.5 主题不占 system_design §9（沿用 V1.5-A/G 先例）；辨明「V1.5-C 因子监控自动降权」为 v2.0 重构前旧标签、已被 Phase 11 §4.1/§4.4 ICIR 自动加权消费，与本主题（策略扩展）无关；grep 推迟项三处确认；**用户拍板两项范围决策（2026-08-10）**：① 资金动向策略的 moneyflow/北向数据层在本主题内一并建（非拆分推迟）；② 策略插件沙箱完整纳入本主题（非拆分/推迟）→ 全 5 模块纳入、零推迟。§2-§6 详细设计待展开 |
| v0.2 | 2026-08-10 | **设计评审收口**（启动核查门评审通过 ✓，0 阻断 / 1 P2 / 4 P3）。P2：估算上修（~50%）的 roadmap §6 权威登记从「收尾回写」提前到启动放行时——已在 roadmap §6 V1.5-C 行加前向说明（→ ~12.5-20 pd）。P3 全部就地纠正：① §1.2 分项和下界据实 13→**12.5**；② C3 5y strategy_weights 历史回填补挂 **C-1 门控**（与 C2/C4 一致）；③ Piotroski 项数明确以经典 **9 项**为准 + 回修 roadmap §3「8 项」笔误；④ §3 financial_data 字段枚举据 market.py 补 revenue_yoy / pe_ttm 实名 + 金融股数据不全降级纳入 Altman Z-Score 备选 |
| v0.3 | 2026-08-14 | **§2-§9 详细设计展开**。展开期对生产 + 代码做了 5 项实证核查，三项直接改变设计：① **日级 IC 产出无调度闭环**（生产 `factor_ic_window_state` daily 行止于 2026-05-11，距今 3 个月且持续拉大；UPTREND 聚合 sample_size 已贴 60 下限）→ 新增 **C0 前置子批**；② **Phase 4 时代写在 `score()` 里的策略硬约束在 Phase 11 五步管线全部失效**（动量追高剔除 / 动量数据不足 guard / **价值陷阱截断**——后者所在的 value 策略当前占生产 composite 权重 0.63~0.82）→ 并入 C1 作「策略约束落点统一」，并成为 C2 门控的落点前提；③ **零权重策略仍参与 Gram-Schmidt 正交化**，其 NaN 行经 `valid_mask` 交集收紧把整行 composite_z 打到 0 → §8 定为新策略入 composite 的头号陷阱 + 影子模式设计。另据实证：生产 momentum ICIR = −0.66/−0.73（权重已被压至 0.000）→ 成为 C1 可证伪验收锚点；生产磁盘 83% 已用（可用 8.2G）→ C4 回填窗口据实收敛为 2 年 + 列裁剪。scope 由 5 子批增至 6 子批，估算 ~12.5-20 → **~14.5-22 pd** |
| v0.4 | 2026-08-17 | **C0 实施期修订**，全部由生产实证驱动：① **C0-3 追平路径改写**——在生产容器跑回填脚本仅一个交易日即被 OOM killer 杀掉（RSS 1.58 GB），站点 530 共 43 分钟；单日耗时实测约 20 分钟（48 天 ≈ 16 小时）→ 改走本地算力中心（5434 全量副本）批量回填后导入生产，并写入**「先导入、后部署」硬顺序约束**（先部署会让 Job 在 19:30 对 48 天积压逐日全 universe 评分，即打挂生产的同一条路径）；② **`_CATCHUP_MAX_DAYS` 3 → 1**——单次运行绝不连做多次全 universe 评分，大断档不由 Job 承担；③ 新增 **C0-5 评分路径财务查询优化（alembic 0024）**——追平标定挖出 universe 过滤单次 9 分钟，卡在 `financial_data` 658 万行的两条查询（其中 `DISTINCT ON` 段 external merge 落盘 261 MB），该表日频行不可删故修在索引侧，两个覆盖索引经本地全量副本 A/B 实测分别 31.3s→2.3s、69.7s→25.9s 且排序节点全消。断档区间据前向窗口据实收敛为 **48 个交易日**（2026-05-12 → 2026-07-17，非原估 60）|
| v0.5 | 2026-08-18 | **C0 追平期修订**：新增 **C0-6 零权重策略的 IC 可观测性**——本地追平实测发现 2026-06-09 起 `trend`/`momentum` 完全不产日级 IC 行，查证为 R1 判 offline → 权重 0 → `Scorer.aggregate` Step 5 的 `valid_weights` 过滤把二者挡在 `score_breakdown_raw` 之外 → `extract_strategy_z` 抽不到。R1 的判定本身正确（momentum ICIR 稳定 −0.66~−0.94、样本 144~188），坏掉的是**复活路径**：无新 IC 则永远评估不出「已恢复」，且已实证 UPTREND 窗口冻结（连续两月 sample_size=60、ICIR 数值完全相同）。处置为解耦 IC 观测与 composite 加权：新增仅内存字段 `CompositeScore.strategy_z_all`，`extract_strategy_z` 优先消费、缺失回退旧字段——无迁移、`score_breakdown_raw` 语义不变、下游消费方零改动。同批**提前实施 §8.3 陷阱 1 的处置**（原定 C3）——该条并非「新增策略才触发的风险」而是当前生产正在犯的缺陷：trend/momentum 权重已为 0 却仍进 Gram-Schmidt，`valid_mask` 使其 NaN 行残差全 NaN → `fillna(0.0)` → `composite_z=0` → `Φ(0)×100=50` 假中位分且不被 `any_valid` 剔除，真实高分股正被压平到中位；与 C0-6 同处 Step 4/5，同批修复同批回归（UT-C0-08a/b）|
| v0.6 | 2026-08-18 | **C0 收尾期修订**：新增 **C0-7 daily/aggregate 行唯一约束撞车**（alembic 0025）——`factor_ic_window_state` 的全表唯一键不含 `row_type`，月末当日状态等于某 aggregate 行 state 时两行合并，日级观测对 ICIR 窗口消失、aggregate 的 `sample_size` 被覆盖；生产实测 156 行 / 39 个月末，且**本次追平终态 47 天 / 188 行而非 48 / 192 正是它的指纹**。Phase 14（0014）注释显示这是当时「冗余但向后兼容」的有意取舍，判定为错误取舍并纠正。同步记录部署期新增风险：0025 生效后旧代码三处 upsert 立即失效（4 列 `index_elements` 推断不到唯一索引），收尾须避开月末批 |
| v0.7 | 2026-08-20 | **C0 收尾完成 + C1-1 实施期修订**。① C0 六步生产收尾全部实证完成（备份 → alembic 0025 → 拆分 156 行 → 导入 48 天 → ICIR 重算 51 月 → 部署），**首日两次全 universe 评分内存峰值 1.364 GiB（17:30 管线）/ 1.371 GiB（19:30 Job）仅差 7 MiB**，证实 19:30 不抬高峰值只是把同一峰值再走一遍；`trend` 在 UPTREND 下权重由 0 → 0.0037，C0-6 的复活路径在生产验证成立（2026-07-20 的 4 条 IC 行含权重为 0 的 momentum）。收尾期另修 `backfill_icir_rebalance` 日历缓冲缺陷（仅留 30 天 < ICIR 回看 272 交易日 → 任何 `--start` 的开头约一年月份注定失败，且是"部分成功 + exit 1"极易误判为跑完）。② **§3.2 C1-1 措辞订正**：原文「旧路径行为等价，不产生回归」与同节「剔除类置 NaN、禁止置 0」不可兼得——约束表达迁到 raw 因子域后旧路径数值必然改变（追高股由 `score=0` 变为不出现在结果中，陷阱股不再有 50 的固定上界）。真正要守的契约是「约束仍生效且两路径同源」，§3.3 相应 DoD 判定标准同步调整。③ **§3.2 回写 C1-1 实施期实证三项**：追高剔除在无离散度时会清空整个策略、「前 5%」用分位数阈值在小样本上实为 9.5%~50%（改按名次取前 `floor(n×pct)` 名）、既有测试 helper 数据退化导致的假 RED 及其前置判据防护 |
| v0.8 | 2026-08-24 | **C1-3 价格窗口按交易日推导**（§3.2 新增子节 + §3.3 DoD 两条）。准备 C1 面板对比时抓到**自 Initial commit 起**的生产缺陷：`ScoringService` 用 180 **日历天**取价格窗口、注释写「≈ 120 交易日」，本地全量副本实测 2026-07-01 往前 180 日历天只有 **117 个交易日** → `rs_6m`（momentum 权重 0.35）有效率 **0/2274**，修复后 **2246/2274（98.8%）**；同根第二处静默降级是 `index_adj_prices` 不足使 `idx_return_6m` 回落 0.0、`rs_6m` 退化为绝对收益。两处均无告警。修法是消除整类缺陷而非调大系数：策略自报 `required_history_days`（交易日）+ Service 取全体最大值 + `TradingCalendar` 精确回退 + 日历不足必发 WARNING；同批修掉三个脚本不足的日历回看缓冲。**该缺陷同时污染面板对比两侧** → C1-2 的 before/after 基准改为「C1-3 修复后的 `off` 组」，不再用 `ic_baseline_pre_c1` 快照；配套把「一键回退对照」从手改 `config_defaults.py` 改为 `build_default_strategies(momentum_risk_adjusted=...)` + CLI 开关，使产出配置可从命令行与日志追溯 |
| v0.9 | 2026-08-26 | **面板 `total_equity` 口径澄清 + 一处错误结论订正**（§3.2 新增「面板口径」小节 + §3.3 DoD 回写要求）。面板跑期间持续出现的 `total_equity 全为 NULL，F-4 跳过` 此前被判为「本地副本陈旧、生产已回填」，**该判断经实测否定**：非空行按报告期分布为 2023 年 225 / 2024 年 16067（1.2%）/ 2025-06-30 起每期 ≈ 5500（≈ 每股一行，全库 `count(DISTINCT ts_code) = 5507`），即 2026-08 的回填只覆盖 **2025-06-30 起 5 个报告期、未回补历史**，属**生产数据本身的属性**，重灌算力库不会改变。后果：面板窗口 2024-07 → 2026-07 中段存在一次 **F-4 由跳过转为生效**的 universe 口径切换，解读绝对 IC 时间序列须标出该断点；off/on 对照的内部有效性不受影响。另订正状态行：C1-3 已提交（be6d6d6），面板对比改在第二台 24h 算力机上起跑 |
| v0.10 | 2026-08-27 | **DoD 状态对账 + SDD 回写完成**（无范围变更、无设计变更）。①C0 DoD 三项补勾并附实证：导入生产（回滚点 sha256 两端一致 / 192 次 INSERT / `daily_ic_max` → 2026-07-17）、先 alembic 0023→0025 再部署 backend（顺序合 C0-3 约束）、`daily_ic_producer` **连续 7 个生产日**每日恰 4 行（因子日 2026-07-20~07-28，`created_at` 08-19~08-25，每个日历日推进一个因子日 = 已进入稳态）。**同时记录一处阈值不自洽**：`factor_ic_daily_lag_days < 30` 在导入当日实为 ≈33、稳态 29——该指标下界受 20 交易日前向窗口（≈28 日历天）约束，代码注释写的稳态 20~25 不可达，**阈值待订正**。②C1 DoD 三项补勾并附用例名：`test_ut_c1_04a/04b/04d/04e`（风险调整因子）、`test_ut_c1_05/06a/06b`（参数被消费）、`test_ut_c1_07` + commit `f68b761`（理由模板 + glossary）——均属已交付未回写。③SDD §7.2.3/§7.2.4 回写完成（SDD **v1.4-r4**）。C1 剩余项仅「面板对比」与其结论回写 |
| v0.11 | 2026-08-27 | **C2 前置真调核对完成（只读，无代码变更）**。①`fina_indicator` 逗号多码**无坑**：逐单码 vs 多码返回行数与各列非空数完全一致，C2 要新增的 6 列全部随多码正常返回 → `fetch_financial_by_stock` 扩列即可、调用形态不变；缺值那 1/5 经核对是银行股（§4.2 已设计走 ROE 替代分支），属预期。②**§4.1【设计待定：`total_share` 数据源】判定为 `daily_basic`**——实测 `total_share` 非空率在 20210513~20260825 全部抽查日均 **100%**，且每日期仅 1 次调用即取回全市场，对比 `balancesheet` 逐单码的 ≈11000 次；实施期仍须处理「`publish_date` 落在非交易日 → 回退到之前最近交易日」并写进单测。③实调出第三例**静默忽略**：`fina_indicator` 被索要 `total_share` 时不报错也不返回该列 —— 与 `balancesheet` 多码静默返空、pandas_ta 旧参数名被吞同类，判据同 §4.4 |
| v0.12 | 2026-08-28 | **C4 前置真调核对（只读，无代码变更）——挖出一处设计前提失效**。①🔴 **`hk_hold` 的「个股 × 日」前提自 2024-08-19 起不成立**：A 股（北向）持股行**只在季末存在**，日频已停止披露（断点精确到 2024-08-16 有数据 / 08-19 起为 0；断点后 2025 四个季末均有 3300+ 行，非季末 07-15 / 11-20 均为 0）。近端仍返回 ~950 行但**全是 `.HK` 港股（南向）**，只看总行数会误判为「有数据」。→「北向持股变化」作为日频横截面因子**不可实现**，§6.1 已列三条出路并倾向「砍掉北向副因子、C4 只做 `moneyflow`」，**待拍板**。②✅ `moneyflow` 支持逗号多码，采集形态不受限。③✅ `net_mf_amount` 单位确认为**万元**（量级反推：600519.SH 净流入按万元解释占当日成交额 −7.56%，按元解释 −0.0008% 荒谬），设计的换算正确。④🔴 `hk_hold` **逗号多码静默返空**（单码 1 行 / 多码 0 行，均不报错）——「静默忽略」族**第四例**，若保留北向采集必须按 trade_date 全市场或逐单码 |
| v0.13 | 2026-08-28 | **C4 范围收窄：砍掉北向副因子（用户拍板）+ 市场级北向经实测不纳入**。①「北向持股变化」个股日频因子**确定不可实现**——已逐个排除替代源：`hk_hold` A 股日频自 2024-08-19 停（仅季末）、`hsgt_top10` 未停但每日仅十大成交股（5000 只 universe 覆盖 0.4%）、`ggt_top10` 是南向。**C4 只做 `moneyflow` 主力资金**，§6.2 的 `hk_hold` 表与 `north_hold_chg` 相关内容作废。②连带发现 **`moneyflow_hsgt.north_money` 语义在同一天（2024-08-19）由「净流入」变为「成交额」**——字段名/单位/供数全不变，按年负值占比 2021 34.2% → 2025/2026 **0.0%**，量级跳约 100 倍。③按「先测再决定」实测其对市场状态的预测力（干净段 464 交易日）：原始水平 fwd20 Spearman −0.292 看似显著，但**是水平趋势伪相关**——同序列 60 日 z-score 去趋势后塌到 **+0.007**，按状态分组亦为噪声级。**→ 不纳入 MarketState**（非嫌麻烦，是实测无信号；若北向净流入恢复披露可重启评估）。两条新实例已并入 `docs/reviews/silent_ignore_audit_2026-08-27.md`（该族累计 5 例）|

---

## 1. 概述

### 1.1 背景

V1.5-C 是 V1.0 RC + V1.5-G 多用户 + V1.5-A 回测/数据收尾之后的**策略广度扩展**主题批。V1.0~V1.5-A 的信号系统建立在四大策略（趋势 / 动量 / 均值回归 / 价值）之上；本主题从三个方向扩展策略广度与质量：

- **既有策略增强**（C1/C2）：动量策略引入风险调整（涨幅/波动率）避免偏向高波动标的（SDD §7.2.3 注 + 外部评审 §7.2.3）；均值回归引入 Piotroski F-Score 硬性前置过滤，避免下跌趋势中选中「价值毁灭」标的（SDD 外部评审 P1，§7.2.2）。
- **新增策略**（C3/C4）：低波动策略（低历史波动率 + 低 Beta，A 股低波动异象有实证支持，SDD §7.3）；资金动向策略（主力资金净流入 + 北向资金变化，SDD §7.3——**需先建 moneyflow/北向数据层**，代码库当前零实现）。
- **平台能力**（C5）：策略插件沙箱（SDD §15.2），让 L3 用户在受限隔离环境中编写/挂载自定义策略。
- **前置闭环**（C0，v0.3 新增）：日级 IC 持续产出。C1 的验收、C3/C4 的权重激活全部依赖 ICIR，而 ICIR 的输入（日级 IC 行）当前**没有任何调度产出路径**，只靠一次性回填脚本；生产实证已停更 3 个月并开始侵蚀现有四策略的权重校准（§2）。

**关键设计风险（§8 专述）**：C3/C4 把 composite 策略数从 4 增至 6，直接牵动 Phase 11 评分管线——strategy_weights_history 权重矩阵、逐策略 ICIR 滚动加权、Gram-Schmidt 正交化的 NaN 交集语义、横截面相关性。新增策略不是「加个文件」而是「改评分骨架」。

### 1.2 Scope 总览

| 子批 | 主题 | pd | 段落 | 生产写 | 数据依赖 | 实施序 |
|------|------|-----|------|--------|---------|--------|
| **C0** | 日级 IC 产出闭环（调度 Job + 断档追平）**【v0.3 新增前置】** | 1-1.5 | §2 | **有**（factor_ic_window_state daily 行；追平 2026-05-12 至今断档）| 无（复用现有 engine 纯函数 + 现有表）| 0 |
| **C1** | SDD-EXT-08 风险调整动量 + **策略约束落点统一**（追高剔除 / 价值陷阱截断迁入五步管线）| 1-1.5 | §3 | 无（Engine 纯函数改 momentum.py / value.py / base.py）| 无（daily_quote 可算）| 1 |
| **C2** | SDD-EXT-04 均值回归 Piotroski F-Score 硬过滤（F-Score<6 → mean_reversion 因子置 NaN；金融股 ROE>5% 替代）| 2-3 | §4 | **有**（financial_data 扩 6 列 + 5y 回填，C-1 门控）| financial_data 字段扩展（Tushare fina_indicator 增量列）| 2 |
| **C3** | 低波动策略 `low_volatility.py`（低历史波动率 + 低 Beta）| 2-3 | §5 | **有**（新策略入 composite → candidate_pool 扩列 + 权重激活，**C-1 门控**，见 §8）| 无（复用现有 adj_prices + index_history 窗口）| 3 |
| **C4** | 资金动向数据层 + 策略（moneyflow / hk_hold adapter+表+采集 + `money_flow.py`）| 3-5 | §6 | **有**（新表 alembic + 采集接线 + 2y 回填，C-1 门控 + 新策略入 composite）| **新 Tushare 接口**（moneyflow / hk_hold；moneyflow_hsgt 市场级备选）| 4 |
| **C5** | 策略插件沙箱 `plugin_runner.py`（SDD §15.2 受限隔离运行时 + 标准接口 + 审计）| 5-8 | §7 | **有**（插件存储/审计表 + L3 用户插件管理端点）| 无 | 5 |

**合计 ~14.5-22 pd**（下界 1+1+2+2+3+5=14；取分项下界保守值 14.5，上界 1.5+1.5+3+3+5+8=22）。相较 v0.2 的 12.5-20 增量 = C0 前置闭环（1-1.5）+ C1 因并入策略约束落点统一而上修（0.5-1 → 1-1.5）。二者均非新需求，而是设计展开期实证发现的**既有生产缺口**，且都卡在本主题主路径上（§2 / §3.1）——按项目宪法 C-3「现在的问题现在处理」就地纳入，不走推迟三链。

> **给用户的范围提示**：C0 若拆出作为独立补丁批（先于 V1.5-C 上线），本主题估算回落 ~13.5-20.5 pd，但 C1 验收与 C3/C4 激活必须等 C0 落地后才能判定——顺序不可颠倒。默认按上表纳入。

### 1.3 启动核查（CLAUDE.md §5.1）

| 核查项 | 结论 |
|--------|------|
| 读 system_design §9 本 phase 行 | V1.5-C 是 RC 后 V1.5 主题，**不在 V1.0 §9 Phase 表内**（§9 note 明确 low_volatility.py/plugin_runner.py 属 V1.5+、Phase 1-15 不创建；V1.5 按 roadmap §6 主题打包）。权威登记在 roadmap §1/§3/§6 V1.5-C 行。沿用 V1.5-A/G 先例（`v1_5_<letter>_<topic>.md` + roadmap §6 登记，不占 §9 Phase 编号）✓ |
| 模块去向决定 | 全 6 子批（C0-C5）纳入本 phase；**零推迟**（用户 2026-08-10 拍板：资金动向数据层本主题内建 + 插件沙箱完整纳入；C0 为 v0.3 设计展开期发现的前置缺口，就地纳入）。无需推迟三链 ✓ |
| 命名撞车辨明 | system_design / phase11 语境的「V1.5-C 因子监控自动降权」= v2.0 重构前旧 V1.5-C 标签，已升级并被 Phase 11 §4.1 ICIR 滚动加权 + §4.4 自动下线消费 ≠ 本主题「V1.5-C 策略扩展」。已确认无残留孤儿引用误指本主题 ✓ |
| grep `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + reviews | 本主题源自 SDD §16 产品功能（资金动向/低波动/沙箱）+ SDD-EXT-04/08，非 V1.0 phase 评审 P2/P3 推迟项，无追溯编号需消费。已核 reviews/ 无指向 V1.5-C 的未消费评审项 ✓ |
| 孤儿检查（system_design §3/§5 模块 + §6 端点）| 新增模块：`engine/strategies/{low_volatility,money_flow}.py` + `engine/piotroski.py` + `engine/sandbox/plugin_runner.py` + momentum/value/mean_reversion 扩展 + moneyflow/北向数据层（adapter + models + repository + 采集接线）+ 日级 IC 调度 Job + 插件/审计表。新增端点：L3 插件管理 5 个（§7.5）。**均须在收尾回写 system_design §3/§5/§6 落位** ✓ |
| C-5 范围变更回写顺序 | scope（C1-C5）与 roadmap §6 既有打包一致；**C0 为新增子批**，属范围变更 → 按 C-5 须先回写 roadmap §6 V1.5-C 行再进入实施（收尾前完成，见 §10.3）。触及 SDD §7.2.2/§7.2.3/§7.2.4/§7.3/§7.4/§15.2——设计展开时回写 SDD 对应节 ✓ |

**启动核查清单（收尾逐项勾选）**：
- [ ] grep `system_design §9` 无 V1.5-C 专行（确认沿用 roadmap 登记先例，不误建 §9 Phase 行）
- [ ] 命名撞车「因子监控自动降权」旧标签在 system_design/phase11 语境保持指向 Phase 11，无文档误把本主题接线到旧标签
- [ ] 新增模块/端点全部回写 system_design §3/§5/§6 + SDD §7.2.2/§7.2.3/§7.2.4/§7.3/§7.4/§15.2
- [ ] 收尾回写 roadmap §6 V1.5-C 估算 9-13 → 实际值收敛，并登记 C0 子批

---

## 2. C0 — 日级 IC 产出闭环【前置】

> 依据 SDD §7.4（ICIR 滚动窗口 `[t-272, t-20]` 交易日）+ Phase 14 §14-9（日级 IC 回填与 rebalance 真机验收）。本子批不引入新表、不引入新算法，只补**产出路径**。

### 2.1 现状与问题（生产实证 2026-08-14）

| 实证项 | 结果 |
|--------|------|
| `factor_ic_window_state` row_type='daily' | 4592 行，`min=2021-05-13`，`max=**2026-05-11**`，4 策略 |
| row_type='aggregate' | 344 行，`max=2026-07-31`，4 策略 |
| 最近一次月末 rebalance 生效权重（trade_date=2026-08-01）| OSCILLATION / UPTREND = `icir`；DOWNTREND = `default_matrix` |
| 2026-06 起聚合行 sample_size | OSCILLATION 144~166；**UPTREND 恰为 60**（= `state_min_samples` 下限） |

日级 IC 行只由 `scripts/backfill_daily_ic.py`（Phase 14 §14-9 一次性回填脚本）产出。`pipeline/scheduler.py` 现有 6 个 Job（daily_pipeline / monthly_job / weekly_report / stop_loss_warn / trade_calendar_refresh / pipeline_watchdog）**均不产出日级 IC**；`DailyPipeline` 的 CP1→CP3 + Step4/5/6 亦无该步骤。

后果链：日级 IC 停更 → ICIR 窗口 `[t-272, t-20]` 右段逐日空洞 → `rolling_icir_state` 的 state 子集 sample_size 递减 → 跌破 `state_min_samples=60` 后 `rolling_icir_state` 返回 None → `apply_monthly_rebalance` 全 state 回落 `default_matrix`。UPTREND 已贴在 60 边界，**下一次月末 rebalance 即可能掉出 ICIR 路径**。这与「因子监控自动降权」的产品承诺（Phase 11 §4.1/§4.4）直接冲突，且属于 CLAUDE.md 全局经验「禁止依赖手动操作补全功能」——手动脚本不算功能闭环。

对本主题的直接阻断：C1 的验收锚点（momentum ICIR 由负转正）、C3/C4 的权重激活（新策略 ICIR 从无到有）都需要日级 IC **持续**产出，否则新策略永远拿不到 sample_size ≥ 60，权重恒为 0——即「代码上线但功能空转」，与 V1.5-A 的 A5b/F-4 教训同型。

### 2.2 设计

**C0-1 日级 IC 生产者服务方法**。在 `FactorMonitorService` 新增
`produce_daily_ic(session, trade_date, scoring_service, min_xs=_DAILY_IC_MIN_XS) -> int`
（RED 阶段定稿签名：`scoring_service` 走**参数注入**而非构造注入——`ScoringService`
本身已把 `FactorMonitorService` 作为构造依赖，反向构造注入会成环）：

1. 取 `base_date = trade_date`，`end_date = calendar.get_next_trade_date(trade_date, 20)`；若 `end_date` 尚未到达（前向收益未实现）→ 直接返回 0（不写行）。
2. 复用 `ScoringService.score_universe_for_date(base_date)` 取全 universe composites → `extract_strategy_z`（现由 `scripts/backfill_daily_ic.py` 以 `_extract_strategy_z` 持有，本子批**下沉至 `engine/diagnostics/ic_aggregator.py`**，脚本改为再导出别名，消除双实现）。下沉时**去掉硬编码的 4 策略元组**，改为从 `score_breakdown_raw` 的键推导策略名——这是 C3/C4 新策略自动进入 IC 监控的前提。
3. 复用 `engine/diagnostics/ic_aggregator.compute_forward_returns` + `compute_daily_ic`（均为现成纯函数，且 `compute_daily_ic(strategy_z: dict[str, pd.Series], ...)` 与策略数无关——C3/C4 新策略自动纳入，无需改动）。
4. `FactorICRepository.upsert_ic_daily` 写入；`state` 取 `base_date` 当日 market_state。

**C0-2 调度落点**。新增 APScheduler Job `daily_ic_producer`：

- 触发：`CronTrigger(hour=19, minute=30, timezone="Asia/Shanghai")`（避开 17:30 日线管线的 CPU/内存高峰；2GB 机不并发跑两个全 universe 评分）。
- 语义：**滞后消费**——每次运行处理 `t-20 个交易日`那一天（其前向收益在今日已实现），而非当日。这与 SDD §7.4 的 lag 20 约束天然一致。
- 幂等：先查 `get_existing_daily_ic_dates` 跳过已有日；`upsert_ic_daily` 本身幂等。
- 追平：纯函数 `plan_catchup_dates(trade_dates, existing, last_eligible, max_days) -> list[date]`（置于 `services/factor_monitor_service.py` 模块级，便于单测）——只取 ≤ `last_eligible`（= `t-20` 交易日）、跳过 `existing`、按**升序取最旧的 `max_days` 天**（`_CATCHUP_MAX_DAYS = 1`）。升序补最旧是刻意的：ICIR 窗口要连续，必须从断档最左端往右填。上限取 1 而非更大值是 2026-08-17 实证的结果：一次全 universe 评分在生产 2GB 机上 RSS 达 1.58 GB，单次运行绝不可连做多次；17:30 每日管线长期稳定跑的正是"每次运行一次评分"。稳态下每次恰好补上新满足 `t-20` 的那一天；**大断档不由本 Job 承担**（见 C0-3）。
- 单日异常隔离：某日失败 `logger.exception` 后继续下一日，不整批中止。
- 自建 session 显式 commit（CLAUDE.md：调度 Job 不走 `get_db` 自动 commit）。

**C0-3 断档追平（一次性）**。断档区间 **2026-05-12 → 2026-07-17 共 48 个交易日**（终点由前向窗口决定：`get_next_trade_date(d, 20) ≤ max(daily_quote.trade_date)`）。

**执行路径：本地算力中心，禁止在生产跑。** 2026-08-17 在生产容器内跑本脚本**仅一个交易日**即被 OOM killer 杀掉（RSS 1.58 GB / 可用 1.0-1.4 GB），站点 530 共 43 分钟；同时实测单日耗时约 20 分钟（48 天 ≈ 16 小时，无论如何压不进不撞 17:30 管线的窗口）。故改走既有回测通路：

1. `bash scripts/sync_local_backtest_db.sh --force` 把最新远端备份恢复进本地算力库（5434）；
2. 以 `quantpilot-backend` 镜像 + bind-mount 本地 `src`/`scripts` 起一次性容器，`DATABASE_URL` 指向 5434，跑 `backfill_daily_ic.py --start 2026-05-12 --end <max daily_quote>`（末尾无前向窗口的日自动排除）；
3. 导出该区间 daily 行 → 导入生产（`INSERT ... ON CONFLICT (strategy, factor, state, trade_date) DO UPDATE`，与 `upsert_ic_daily` 同语义；**不带 `id`**，该列自增）。导入前对 `factor_ic_window_state` 做 `pg_dump --data-only -t` 精确回滚点。

**顺序约束（硬）**：必须**先导入、后部署 C0 代码**。若先部署，`daily_ic_producer` 会在 19:30 发现 48 天积压并开始逐日全 universe 评分——正是打挂生产的那条路径。导入后 Job 上线即无积压，稳态每次仅 1 天。

**C0-5 评分路径财务查询优化（本子批实测挖出，alembic 0024）**。追平标定时发现 `score_universe_for_date` 的 universe 过滤单次耗时 9 分钟，卡在 `financial_data`（658 万行）的两条查询。该表日频行**不可删**（`publish_date` = 采集交易日是设计如此，PIT 过滤与 ValueStrategy 历史分位依赖之），故修在索引侧。本地全量副本 A/B 实测：

| 查询 | 无索引 | 加覆盖索引 |
|---|---|---|
| `get_latest_n_financials`（两级去重第一级窗口） | 31,340 ms / 626 万 buffer | **2,323 ms / 41 万 buffer，排序消失** |
| `get_latest_financial` 日频段（`DISTINCT ON`） | 69,673 ms / 625 万 buffer，**external merge 落盘 261 MB** | **25,878 ms / 38 万 buffer，排序消失** |

两个索引均为纯新增（`CONCURRENTLY`，不阻塞 17:30 管线写入），合计约 835 MB。生产为磁盘瓶颈，磁盘读从 288 万次降至 4.6 万次，收益预期高于本地倍数。剩余耗时为 `DISTINCT ON` 仍需扫全部匹配索引项（PG 15 无 skip scan），彻底消除需改写为 loose index scan —— 【设计待定：是否值得为此改写 `get_latest_financial`，待索引上生产后按实测耗时决定】。

**C0-7 daily/aggregate 行在唯一约束上撞车（追平收尾期实测挖出，alembic 0025）**。`factor_ic_window_state` 上 Phase 11（0009）建的全表唯一约束 `(strategy, factor, state, trade_date)` **不含 `row_type`**；Phase 14（0014）加 partial unique `... WHERE row_type='aggregate'` 时注释明确写着与之「并存 …… 冗余但向后兼容，方案 A 设计取舍」——即**有意保留**跨类型唯一性。该取舍是错的：它让 daily 行与 aggregate 行在同一 4 元组上**互斥**。

月末那一天，当日市场状态若恰等于某已有 aggregate 行的 state，两次 upsert 会合并成一行：

| 最后写者 | daily.ic_value | daily.sample_size | aggregate 统计列 |
|---------|---------------|-------------------|-----------------|
| aggregate（生产 156 行的形态） | 残留完好 | **丢失** | 完好 |
| daily（本次追平 2026-06-30 的形态） | 完好 | 完好 | `sample_size` 被覆盖 |

后果：① 该日日级 IC 对 ICIR 窗口不可见（`get_ic_daily_window` 按 `row_type='daily'` 过滤——§14-9 P2-2 只加固了读路径、没治根），每月静默少一个观测；② aggregate 的 `sample_size` 被日级横截面股票数覆盖时，R4（`sample_size < 60` 连续 3 月）在这些行上失效。

**实测**：生产 **156 行 / 39 个月末**（2022-05 ~ 2026-03，源自 §14-9 五年回填）。本次追平的 48 天里命中 1 天（2026-06-30；2026-05-29 侥幸逃过——当日 UPTREND 而该日只有 OSCILLATION 的 aggregate 行）。**这也是追平终态为 47 天 / 188 行而非 48 / 192 的原因**。

**处置**：唯一键改 5 列 `(strategy, factor, state, trade_date, row_type)`（alembic 0025），三处 upsert 的 `index_elements` 同步补列；存量由 `scripts/repair_ic_row_type_collision.py` 拆分（日级 `ic_value` 精确还原；日级 `sample_size` 在「aggregate 最后写」的 156 行上不可还原，写 0 并计数上报——**【降级说明】**：仅影响 `aggregate_monthly` 的 `avg_xs_sample` 展示列，ICIR/权重全链路只读 `ic_value` 故不受影响；恢复条件为在本地算力中心对这些日期跑 `backfill_daily_ic.py --force`）。aggregate 侧被覆盖的统计列交由 `backfill_icir_rebalance.py --force` 重算，精确、无需估算。

**部署期新增风险**：0025 生效后，**旧代码的三处 upsert 会立即失效**（4 列 `index_elements` 推断不到唯一索引）。故迁移与代码部署之间的窗口必须可控——所幸这三处只在 `daily_ic_producer` Job（尚未上线）与月末批（`apply_monthly_rebalance` / 月度质量）触发，日常交易日不写该表，窗口内无实际写入。收尾须避开月末。

**C0-4 可观测**。新增结构化日志 `daily_ic_produced: trade_date=%s strategies=%d rows=%d`（同 A5b `forecast_roe_override_applied` 先例——**激活必须可实证**）+ Prometheus Gauge `factor_ic_daily_lag_days`（= `t - max(daily trade_date)`，> 40 告警）。

**C0-6 零权重策略的 IC 可观测性（追平实测挖出，2026-08-18）**。回填到 2026-06-09 起 `trend` / `momentum` 完全不再产出日级 IC 行（26 天仅 92 行而非 104）。因果已在代码层查证：

1. `check_factor_offline_rules` R1（ICIR < 0 连续 6 月）对二者判 `offline` → 月末 rebalance 权重置 0（生产实证：OSCILLATION 自 2026-05-01 起连续 4 次 rebalance 均为 0.0000，UPTREND 自 2026-08-01 起亦为 0）；
2. `Scorer.aggregate` Step 5 以 `float(w) > 0.0` 过滤出 `valid_weights`，`score_breakdown_raw` **仅**由它构建 → 零权重策略无 `z_raw`；
3. `extract_strategy_z` 抽不到 → `compute_daily_ic` 无输入 → 不写 daily 行 → ICIR 滚动窗口 `[t-252, t-20]` 逐月流失样本。

后果不是权重判错——**R1 的判定本身是对的**（momentum ICIR 稳定在 −0.66 ~ −0.94，样本 144~188，非冷启动噪声）——而是**掐断了复活路径**：`check_factor_offline_rules` 每月从聚合行重新评估，本设计带自动复活能力，但没有新 IC 就永远评估不出「已恢复」。已实证窗口正在冻结：UPTREND 的 2026-06-30 与 2026-07-31 两次聚合 `sample_size` 同为 60、ICIR 数值完全相同（−0.1045 / −0.7306），即同一批旧样本被反复读取，再流失即回落 `default_matrix`。

**处置：把「IC 观测」与「composite 加权」解耦。** `CompositeScore` 新增仅内存字段 `strategy_z_all: dict[str, float]`，记录**全部** `active_strategies` 的 `z_raw`（含权重 0 者）；`extract_strategy_z` 优先消费它，缺失时回退 `score_breakdown_raw`（`aggregate_legacy` / 回测 fallback 路径）。

- 不选「把权重 0 的条目塞进 `score_breakdown_raw`」：`signal_service` 与 `explanation` 均按 `contribution` 降序取「主要驱动」，`contribution=0` 的条目在全负场景下会窜到首位，须连带改多处消费方。
- 不落 `candidate_pool`：日级 IC 的两条路径（`produce_daily_ic` 与 `scripts/backfill_daily_ic.py`）都消费内存 `composites`，无一读 DB JSONB → 无需迁移、无回填语义、attribution / 前端零影响。
- 已知边界：`AttributionService` 的月度暴露仍从 DB `score_breakdown_raw` 读，故零权重策略不进暴露表。这与该表「解释已实现的 composite」的语义自洽（权重 0 的策略确实贡献为 0），**不视为缺陷**。

### 2.3 C0 DoD

- [x] `produce_daily_ic` 单测：前向收益未实现 → 返回 0 不写行；正常日 → 每策略一行；已存在日 → 跳过（幂等）（UT-C0-03a~e）
- [x] `extract_strategy_z` 下沉后策略无关（新策略键自动抽取），且 `scripts/backfill_daily_ic.py` 与生产 Job 共用同一实现（单测以 `is` 断言同一对象，防双实现漂移）（UT-C0-01/06a）
- [x] `ScoringService` 组装同样下沉为 `services/scoring_factory.py::build_default_scoring_service`，脚本与 Job 共用（同上 `is` 断言）；配套 `MarketDataRepository` 补 `get_max_daily_quote_date` / `get_excluded_codes_for_ic`（原为脚本内私有 SQL）（UT-C0-06b）
- [x] 调度 Job 单测：注册存在、trigger 参数正确、`args` 显式传入依赖（APScheduler Job 无法访问 `app.state`）（UT-C0-04/05）
- [x] 集成测试：合成面板 → `produce_daily_ic` 真跑 → `factor_ic_window_state` daily 行按预期落库（精确 `== N` 断言）+ PIT state + 幂等 + 前向窗口未实现零写入 + 追平计划与 DB 真实串联（INT-C0-01~04b，5 例）
- [x] 早退路径测试钉死**原因**（caplog 断言 `daily_ic_forward_window_incomplete`），避免在"空 universe"等其它 0 值路径上假通过
- [x] 评分路径财务查询覆盖索引（alembic 0024，两个）：本地全量副本 A/B 实测有效，迁移 upgrade/downgrade 往返验证，ORM `__table_args__` 与迁移一致（C0-5）
- [x] 零权重策略仍产出日级 IC：`strategy_z_all` 覆盖全部 active 策略、`score_breakdown_raw` 语义不变、composite_z 逐股不受影响、旧对象回退路径可用（UT-C0-07a~e，C0-6）
- [x] 零权重策略退出 Gram-Schmidt（§8.3 陷阱 1 提前实施）：其 NaN 行不再把 composite_z 打成 0 / 假中位分 50，且「存在与否」composite_z 逐股一致；与 C0-6 互不抵消（退出正交化但仍在 `strategy_z_all` 中）（UT-C0-08a/b）
- [x] 断档 48 个交易日在本地算力中心回填完成（C0-3 步骤 1-2）——2026-08-18 跑完，`success=48 fail=0`，2026-05-12 → 2026-07-17；落库 47 天 / 188 行（差额 4 行 = 2026-06-30，由 C0-7 撞车所致，修复后拆出即 48 / 192）
- [x] daily 与 aggregate 行可在同一 4 元组共存：唯一键补 `row_type`（alembic 0025，upgrade/downgrade 往返实测 + downgrade 阻断守卫）、三处 upsert `index_elements` 同步、存量拆分脚本双方向 + 幂等（INT-C0-05a~h，C0-7）
- [x] 结果导入生产（`pg_dump --data-only` 回滚点前置），`max(daily trade_date)` = 2026-07-17（2026-08-19 收尾第 1/4 步：回滚点 `pre_c0_7_ficws_swh_20260819_145219.sql`，两端 sha256 一致；导入 48 天 / 192 行，md5 两端一致、`INSERT 0 1` 恰 192 次，`daily_ic_max` 2026-05-11 → **2026-07-17**）。⚠️ **`factor_ic_daily_lag_days < 30` 这半条当时未达标且阈值本身偏紧**：该指标定义为 `today - max(daily trade_date)`，而日级 IC 必须等 20 交易日前向窗口（≈ 28 日历天）才可算，**下界就在 28~29 天**；导入当日（08-19）实为 ≈ 33 天。稳态下已落到 **29 天**（2026-08-26 快照：max daily = 2026-07-28）。代码注释写的稳态区间 20~25 与该下界不自洽，**阈值待订正**，不是产出有问题
- [x] C0 代码 + alembic 0024 部署生产（**必须在导入之后**，见 C0-3 顺序约束）——2026-08-19 收尾第 2/6 步：先 `alembic 0023 → 0025`（40s，exit=0，两覆盖索引 478/359 MB 与本地实测逐 MB 吻合，`indisvalid/ready/live` 全 t），**再**部署 backend；顺序与 C0-3 约束一致
- [x] 生产实证日志 `daily_ic_produced` 连续 3 个交易日出现且 rows > 0 —— **实测 7 个连续生产日**（2026-08-26 备份还原到 5434 后直接查证，该区间在 C1 面板窗口之外故未被覆盖）：因子日 2026-07-20/21/22/23/24/27/28，**每日恰 4 行**（value / momentum / mean_reversion / trend），`created_at` 依次为 08-19…08-25 —— 每个日历日推进一个因子日，说明 Job 已进入稳态而非追平中

---

## 3. C1 — SDD-EXT-08 风险调整动量 + 策略约束落点统一

> roadmap §3 SDD-EXT-08（P2）：动量策略提升为风险调整动量，「涨幅 / 60 日历史波动率」作 V1.5 默认行为。SDD §7.2.3 注：L2+ 可配置增强选项，减少对高波动标的偏向。

### 3.1 现状与三个问题

**问题 C1-A（新发现，v0.3）：Phase 4 时代写在 `score()` 里的策略硬约束在生产五步管线全部失效。**

Phase 11 五步管线经 `ScoringService.score_universe` → `strategy.compute_strategy_factors(...)` 取因子矩阵，**从不调用 `strategy.score()`**（`score()` 仅剩 `aggregate_legacy` 冷启动 / 回测 fallback / Phase 4 单测三条旧路径）。而 `BaseStrategy.compute_strategy_factors` 默认透传 `compute_raw_factors`。因此以下三处约束在生产**不生效**：

| 失效约束 | 位置 | SDD 依据 | 影响 |
|---------|------|---------|------|
| 动量「近 1 月涨幅前 5% 追高剔除」| `momentum.py` `score()` | §7.2.3 注 | 短期反转效应未被剔除 |
| 动量「价格列 ≤60 → 返回 []」数据不足 guard | `momentum.py` `score()` | Phase 4 防污染 | 冷启动/短窗口下动量因子仍参与 |
| **价值「ROE < 行业中位数 → 得分截断至 50」价值陷阱规避** | `value.py` `score()` | **§7.2.4** | **低 PE/PB + 低 ROE 的价值陷阱未被压制** |

第三条尤其严重：生产当前 ICIR 权重下 value 策略占 composite 权重 **0.626（OSCILLATION）/ 0.818（UPTREND）**（实证 §8.1），即用户看到的买入信号主要由 value 驱动，而 SDD §7.2.4 明文要求的价值陷阱护栏并未在这条路径上生效。

**问题 C1-B：风险调整动量未实现**（SDD §7.2.3 注的 L2+ 增强选项，V1.5 默认行为）。

**问题 C1-C：既有【降级说明】到期**。`momentum.py` / `mean_reversion.py` 类注释写明「V1.0 回看期与 reversal_exclude_pct 仍硬编码；恢复条件：**V1.5 完成 lookback/reversal 窗口全参数化**」——本主题是该恢复条件的兑现点。

**验收锚点（生产实证）**：momentum 聚合 ICIR = **−0.6625（OSCILLATION，n=144）/ −0.7306（UPTREND，n=60）**，trend = −0.2363 / −0.1045 → ICIR 加权已把 momentum、trend 权重压到 **0.000**。即动量策略当前对生产 composite **零贡献**。C1 因此不是锦上添花，而是对一个已被机制自动停用的策略的抢救，且效果可被 ICIR 客观证伪。

### 3.2 设计

**C1-1 约束落点统一（先做，是 C2 的前提）**

- 在 `BaseStrategy` 引入受保护钩子 `apply_constraints(raw: pd.DataFrame, universe, market_data) -> pd.DataFrame`，默认恒等返回。
- `BaseStrategy.compute_strategy_factors` 改为 `return self.apply_constraints(self.compute_raw_factors(universe, market_data), universe, market_data)`。
- `BaseStrategy.score()` 在 `compute_raw_factors` 之后同样调用 `apply_constraints`，保证**两条路径同源**。

  ⚠️ **同源 ≠ 数值等价**（v0.7 订正，原文误写作「旧路径行为等价，不产生回归」）：约束表达从 0-100 分域迁到 raw 因子域后，旧路径的数值表现必然改变，二者不可兼得——
  - 追高股：改造前 `score=0.0` 且带「追高剔除」reason **留在结果列表里**；改造后全因子 NaN → composite 为 NaN → **不出现在结果中**。0-100 分域里的 0 是「最差」，比「不参与」更强的惩罚；语义收敛为后者是有意的。
  - 价值陷阱股：改造前 `min(score, 50)` + reason 后缀；改造后由截断后的横截面 rank 决定，不再有「50」这个固定上界。

  真正要守的契约是「**约束仍然生效、且两条路径生效方式一致**」，不是「分数不变」。
- 各策略把原写在 `score()` 里的约束迁入 `apply_constraints`：
  - Momentum：追高剔除 + 数据不足 guard
  - Value：价值陷阱截断
- **约束的表达方式改变（关键）**：五步管线里因子先 Winsorize→中性化→Z-score，Phase 4 的「置分 0 / 截断到 50」这类**0-100 分域**操作已无意义。统一改为**在 raw 因子域施加**：
  - 「剔除」类（追高剔除、数据不足、C2 的 F-Score 门控）→ 命中行该策略**所有因子列置 NaN**。语义 = 该股票不参与本策略，`Scorer` 的 `strategy_z` 对该行为 NaN，权重由其余策略分担。**禁止置 0**——Z-score 后 0 是横截面均值（中位水平），置 0 等于给了个中性分而非排除。
  - 「截断」类（价值陷阱）→ 对命中行的因子值做**分位截断**：`raw[命中行] = min(raw[命中行], raw.quantile(0.5))`，逐因子列施加，保持「上限为中位水平」的原始语义且在 rank/Z-score 下不变形。
- ⚠️ 迁移必须逐条对照 SDD 原文语义写测试（RED 先行），并在 `aggregate_legacy` 路径跑回归，确认 Phase 4 单测契约不破。

**C1-1 实施期实证（v0.7 回写）**——迁移过程中暴露出三个既有缺陷，均已随 C1-1 修复：

1. **追高剔除会清空整个动量策略**：全体近 1M 收益率相同时（停牌 / 极端一致行情），`quantile(1-pct)` 等于那个唯一值，`>=` 命中**全体** → 因子全 NaN。改造前表现为全体置 0 分，同属静默失效。处置：加无离散度判定（`nunique() <= 1` → 不剔除任何标的）。
2. **「前 5%」的实现不是 5%**：用分位数当阈值在小样本上被线性插值支配——21 只剔 2 只（9.5%）、**2 只剔 1 只（50%）**。处置：改为按名次取前 `floor(n × pct)` 名，`floor` 让「不足 1 只」时不剔除任何标的，这正是「前 X%」应有的语义。生产 ~3000 只上两种算法结果接近，但定义从此精确。该缺陷此前不可见，正是因为被剔除的标的以 `score=0` 留在列表里；语义收敛为「不参与」后立即暴露（两个 2 只股票的 Phase 4 用例随即失败）。
3. **测试数据退化导致的假 RED**：价值陷阱用例初版复用既有 helper，而该 helper 的 pb 历史逐股恒定、pe 历史用了全局 enumerate 下标 → 历史分位全挤成同一值，陷阱股恰等于中位数，截与不截都满足 `<= median`。处置：自建有区分度的历史，并在用例内加**前置判据**（先断言陷阱股严格高于中位数），数据再退化即直接失败而非真空通过。

**同批兑现**（momentum.py【降级说明】恢复条件的一部分）：`lookback_short` 与 `reversal_exclude_pct` 真正传入计算。`lookback_long` 与 RSI/BBands 参数化留在 C1-2。

**C1-2 风险调整动量**

- 波动率定义：`σ60 = std(日对数收益率, 窗口=volatility_window)`，**不年化**（横截面 rank 与 Z-score 对正的常数缩放不变，年化只增计算不增信息）。有效收益数 < `volatility_window × 0.7` → σ 记 NaN。
- 因子改造：`risk_adj_return_3m = return_3m / max(σ60, 1e-6)`，替换原 `return_3m` 列。
- 因子权重保持 SDD §7.2.3 结构：`risk_adj_return_3m 0.40 / rs_6m 0.35 / industry_rs 0.25`。
- 【设计待定：6 月相对强度是否同步风险调整】——`rs_6m` 是否改为 `rs_6m / σ120`，留 C1 实施期用日级 IC 面板（C0 产物 + `scripts/compare_strategy_ic_panels.py`）实测二选一，实测结果写回本节。默认不改（单点改造，便于归因）。
- 配置（`MomentumStrategyConfig`，`strategy_params_momentum` 已在 `CONFIG_KEY_LEVEL` 登记为 **L2** → 天然满足 SDD「L2+ 可配置」）：
  ```python
  lookback_short: int = 60          # 兑现降级说明：真正传入计算
  lookback_long: int = 120          # 同上
  reversal_exclude_pct: float = 0.05  # 同上
  risk_adjusted: bool = True        # V1.5 默认开（SDD-EXT-08）
  volatility_window: int = 60       # 新增
  ```
  `risk_adjusted=False` 时退回原 `return_3m`，保证可一键回退对照。
- 理由模板更新：`"3月风险调整涨幅（涨幅/波动率）排名前{X}%，年化波动率={σ}%，相对指数{超额/落后}{Y}%，行业相对强度={Z}%。"`（理由文本里 σ 年化展示，便于用户理解；计算不年化）。
- 同步更新 `mean_reversion.py` 的 RSI/BBands 参数化（同一【降级说明】的恢复条件，改动量小，一并兑现）。

**C1-3 价格窗口按交易日推导（实施期新增，2026-08-24）**

准备 C1 面板对比时抓到的**自 Initial commit 起就存在**的生产缺陷，且它同时污染面板对比的两侧，故必须先修再测。

- **缺陷**：`ScoringService` 用 `_PRICE_WINDOW_DAYS = 180` **日历天**取后复权价格窗口，注释写「≈ 120 交易日（覆盖 MomentumStrategy 6M 窗口）」。实测 2026-07-17 往前 180 日历天只有 **119 个交易日**，而 `_period_return(adj_prices, 120)` 要求 `shape[1] > 120`（≥ 121 列）→ **`rs_6m` 恒为全 NaN**。即 momentum 三因子中权重 0.35 的那个，在生产每一次评分中都没参与过。
- **同根第二处静默降级**：`index_adj_prices` 列数同样不足 → `idx_return_6m` 回落 `0.0` → 即便 `rs_6m` 算得出来也已退化成**绝对收益**，不再是「相对沪深300」。两处都无任何告警（违反 C-4）。
- **为何 CLAUDE.md §4.4 的 `×1.5` 经验式在此不够**：120 × 1.5 = 180，而 A 股真实系数 365/250 ≈ 1.46，再叠春节/国庆假期聚集就会越过 180。**修法不是调大系数**（下一次窗口变深还会再撞），而是消除整类缺陷：
  - `BaseStrategy.required_history_days`（单位**交易日**）由各策略自报，默认 `DEFAULT_REQUIRED_HISTORY_DAYS = 65`（覆盖 TrendStrategy 的 MA60 warm-up 与 MeanReversion 的 25 日下限）；`MomentumStrategy` 覆写为 `max(lookback_long, lookback_short, volatility_window, _REVERSAL_WINDOW) + 1 = 121`（`+1` 是 `_period_return` 的口径：算 n 日收益要 n+1 列）。
  - `ScoringService` 取**全体策略的最大值**决定窗口深度 → 任一策略把窗口调深，取数窗口自动跟随，不会重演「配置改了、取数没改 → 因子静默全 NaN」。
  - 起点由 `resolve_price_window_start(trade_date, required_days, calendar)` 经 `TradingCalendar` **精确回退交易日**得出（含 `PRICE_WINDOW_SLACK_DAYS = 2` 的整日行情缺失余量）；日历深度不足时降级为日历天近似并**必发 WARNING**（C-4：降级可以，静默不行）。生产 app 日历为 6 年，不会走该分支。
- **同类缺陷一并修在源头**（三个脚本的日历回看缓冲不足以支撑价格窗口，此前只会表现为「跑通了但 momentum 是残缺的」）：`backfill_daily_ic.py` 120 → 300 自然日、`backfill_candidate_pool.py` 120 → 300、`pipeline_multi_date.py` 180 → 300。
- **对面板对比的影响（重要）**：`ic_baseline_pre_c1` 快照与 2026-07-17 的存量日级 IC 行**均产出于修复之前**，其中的 momentum IC 是「`rs_6m` 全 NaN」版本的。故 C1-2 的 before/after 对照**不得**以该快照为基准，须在修复后的同一份代码上以 `--momentum-risk-adjusted off|on` 跑两组，只差这一个变量。
- **对照跑的可复现性**：`build_default_strategies(momentum_risk_adjusted=...)` + `backfill_daily_ic.py --momentum-risk-adjusted {on,off}`，用 `dataclasses.replace` 生成副本、不改模块级单例。设计文档 §3.2 原写「`risk_adjusted=False` 保证可一键回退对照」，此前唯一的实现路径是手改 `config_defaults.py`——改完忘改回来会把对照组配置带进生产，且事后无从判断某批 IC 行出自哪个配置。命令行开关本身即出处记录，脚本横幅打印实际生效值。

**面板口径：`total_equity` 稀疏使 F-4 在窗口中段切换（2026-08-26 实测澄清）**

跑面板时 5434 持续报 `universe_filter_skipped_null_field: total_equity 全为 NULL，F-4 跳过`。
**曾误判为「本地副本陈旧、生产已于 2026-08 回填」——该解释是错的**，实测分布否定了它：

| `report_period` | 总行数 | `total_equity` 非空 |
|---|---|---|
| 2023 全年 | 1268692 | 225（0.02%）|
| 2024 全年 | 1297621 | 16067（1.2%）|
| 2025-06-30 | 358549 | 5515 |
| 2025-09-30 | 326876 | 5500 |
| 2025-12-31 | 311065 | 5523 |
| 2026-03-31 | 330974 | 5503 |

近期每个报告期恰好 ≈ 5500 行非空 ≈ **每股一行**（全库 `count(DISTINCT ts_code) = 5507`）。
即 2026-08 那次回填只补了 **2025-06-30 起的 5 个报告期，未回补历史**。这是**生产数据本身
的属性**，不是副本陈旧——从任何一份新备份重建算力库，结果相同。

对面板的两点后果：

1. **窗口内部不均匀**：面板窗口 2024-07-01 → 2026-07-17 中，PIT 上看不到 2025-06-30 报告
   的那段（约 2024-07 ~ 2025-09）F-4 被跳过，之后 F-4 生效 → **universe 口径在窗口中段
   发生一次切换**。解读绝对 IC 的时间序列时必须把这个断点标出，否则会把口径切换误读成
   因子行为的变化。
2. **不影响 off/on 对照的内部有效性**：两组跑同一份数据、同一个 universe，差值仍然可比
   ——这正是本次要测的量。但**绝对 IC 水平不可直接外推到今日生产**。

### 3.3 C1 DoD

- [x] RED：`apply_constraints` 钩子存在性 + 两路径同源单测先失败
- [x] 单测：追高剔除在 `compute_strategy_factors` 生效（命中行全列 NaN）；数据不足 guard 生效；价值陷阱截断在 raw 域生效（命中行 ≤ 该列中位数）
- [x] 单测：旧路径（`score()` / `aggregate_legacy`）**约束仍然生效且与五步管线同源**——注意不是「数值等价」，见 §3.2 的 v0.7 订正。Phase 4 契约回归的判定标准随之调整为：MOM-02 断言追高股不参与评分（原断言 `score == 0.0`）、VAL-02 断言陷阱股不得排在健康同业之前（原断言 `score <= 50.0`）
- [x] 单测：无离散度时不剔除任何标的；剔除条数 = `floor(n × reversal_exclude_pct)`（§3.2 实施期实证 1/2）
- [x] 单测：`risk_adj_return_3m` = return_3m/σ60；σ 有效样本不足 → NaN；`risk_adjusted=False` 回退原因子（`tests/unit/test_risk_adjusted_momentum.py::test_ut_c1_04a/04b/04d/04e`，另 04c 钉住「同收益下低波动胜出」、04f 钉住 σ 窗口被消费）
- [x] 单测：`lookback_short`、`reversal_exclude_pct` 真正被计算消费（改参数 → 结果变）——C1-1 已交付
- [x] 单测：`lookback_long`、RSI/BBands 参数真正被计算消费（改参数 → 结果变）（`test_ut_c1_05_lookback_long_is_consumed` / `test_ut_c1_06a_rsi_period_is_consumed` / `test_ut_c1_06b_bbands_params_are_consumed`）。配套源码修复：`mean_reversion.py` 的 `ta.bbands()` 由失效的 `std=` 改为 `lower_std=` / `upper_std=`（pandas_ta 0.4.x 把参数拆开后，旧名落进 `**kwargs` 被静默忽略——CLAUDE.md §4.4 记录的那条陷阱）
- [x] C1-3 单测：`required_history_days` 契约（随配置变、四策略均自报、momentum 最深）；恰好给足列数 → `rs_6m` 全有效、少一列 → 全 NaN（钉住临界点本身）；给足列数时 `rs_6m` 真的减掉了指数收益而非回落 `idx=0.0`；解析器按交易日精确回退（`==` 而非上界）；日历不足 → 降级且 WARNING 出声。回归钉子：180 日历天在真实日历下 < 121 交易日
- [x] C1-3 单测：`build_default_strategies(momentum_risk_adjusted=...)` 覆写真正落到策略实例、不污染模块级单例、`None` 为不覆写
- [ ] 本地 5y 面板对比：改造前后 momentum 日级 IC / ICIR（OSCILLATION + UPTREND）。**要求记录真实结果，不设"必须转正"的硬门槛**——若仍为负则如实记录并在 §3.3 结论中写明（负 IC 本身是有效信息，ICIR 机制会继续给 0 权重；禁止为达标调参硬凑）。**对照基准须为 C1-3 修复后的 `off` 组**，不得用 `ic_baseline_pre_c1` 快照（该快照产出于窗口修复之前，momentum 的 `rs_6m` 是全 NaN 的，见 §3.2 C1-3）。回写结论时**必须一并写明 `total_equity` 口径断点**（§3.2 面板口径小节）：窗口中段 F-4 由跳过转为生效，绝对 IC 水平不可外推到生产
- [x] 理由模板更新 + 前端术语表（`glossary.ts`）补「风险调整动量」「历史波动率」（理由模板：`test_ut_c1_07_reason_shows_annualized_sigma` 断言理由文本含年化 σ；术语表：commit `f68b761`，两词均在 `frontend/src/utils/glossary.ts`）
- [x] SDD §7.2.3 / §7.2.4 回写（约束落点 + 风险调整默认行为）——SDD **v1.4-r4**（2026-08-27）：§7.2.3 风险调整动量由「L2+ 可配置增强选项」升为 V1.5 默认行为 + 补短期反转剔除口径（按名次 / 无离散度不剔除 / 全列 NaN 而非置 0）+ 补「窗口按交易日精确回退，禁用日历天近似」；§7.2.4 补约束落点（必须在 `apply_constraints`）与口径订正（「得分上限 50 分」在五步管线下不再准确，改为 raw 域截断至横截面中位数）

---

## 4. C2 — SDD-EXT-04 均值回归 Piotroski F-Score 硬过滤

> roadmap §3 SDD-EXT-04（P1）+ SDD 外部评审 §7.2.2：均值回归评分前，标的须 F-Score（最近一期年报/季报）≥ 6，否则均值回归不计分；金融类（银行/非银）用 ROE > 5% 且不良贷款率未显著上升的替代判断。
> **项数说明**：经典 Piotroski F-Score = **9 项**二元信号（本文以 9 为准）。roadmap §3 原登记「8 项」系笔误，已回修。

### 4.1 9 项信号定义与字段映射

`Δ` 均指**同比上年同期**（report_period 相同月日、年份 −1），非环比——季报口径下环比有季节性偏误。

| # | 类别 | 信号 | 判定 | 所需字段 | financial_data 现状 |
|---|------|------|------|---------|-------------------|
| 1 | 盈利性 | ROA 为正 | `roa > 0` | `roa` | **缺，需新增** |
| 2 | 盈利性 | 经营现金流为正 | `cfo > 0` | `ocfps`（每股经营现金流）| **缺，需新增** |
| 3 | 盈利性 | ROA 同比改善 | `Δroa > 0` | `roa`（两期）| 同 #1 |
| 4 | 盈利性 | 应计质量（现金流优于利润）| `ocfps > roa × 每股净资产` 的等价式，实现取 `ocfps > eps` | `ocfps`, `eps` | **均缺，需新增** |
| 5 | 杠杆 | 长期负债率下降 | `Δdebt_to_asset < 0` | `debt_to_asset`（两期）| **已有** ✓ |
| 6 | 流动性 | 流动比率上升 | `Δcurrent_ratio > 0` | `current_ratio`（两期）| **缺，需新增** |
| 7 | 融资 | 未增发股本 | `total_share(t) <= total_share(t-1y) × (1+ε)`，ε=0.001 容差 | `total_share`（两期）| **缺，且不在 fina_indicator——见下方【设计待定】** |
| 8 | 运营效率 | 毛利率上升 | `Δgrossprofit_margin > 0` | `grossprofit_margin`（两期）| **缺，需新增** |
| 9 | 运营效率 | 资产周转率上升 | `Δassets_turn > 0` | `assets_turn`（两期）| **缺，需新增** |

> #5 采用总资产负债率而非教科书的「长期负债/总资产」：Tushare `fina_indicator` 无稳定的长期负债率字段，且 `debt_to_asset` 已在库（F-6 过滤在用）。这是**有意的口径近似**，须在 `engine/piotroski.py` 模块 docstring 以 `【降级说明】` 标注（当前降级内容 / 原因 / 恢复条件：接入 `balancesheet.total_ncl` 后改精确口径）。

**新增字段（7 列）**：`roa` / `ocfps` / `eps` / `current_ratio` / `grossprofit_margin` / `assets_turn` / `total_share`（v0.2 估「~6」，展开后据实为 7）。

其中前 6 列来自 Tushare `fina_indicator`——现有 `fetch_financial_by_stock` 的 `fields` 仅取 `roe,netprofit_yoy,tr_yoy,debt_to_assets`，扩这 6 列即可，接口调用形态不变。

> **【设计待定：`total_share` 数据源】**——已核对适配器实际 `fields` 与接口用法，`total_share` **不在** `fina_indicator` 返回字段中，需另择来源，两个候选各有代价：
> - **`balancesheet.total_share`**：口径最准（报告期时点股本），但该接口**不支持逗号多码、必须逐单码调用**（2026-08-10 生产 total_equity 回填实证的第 6 号 bug）→ 全市场两期 ≈ 11000 次调用，回填成本高。
> - **`daily_basic.total_share`**：全市场按 trade_date 一次取回，成本低；但口径是「交易日时点股本」，需取两期 `publish_date` 当日的快照作近似，且 `daily_basic` 当前未建表（`daily_quote.float_mkt_cap` 虽源自该接口，但未落 `total_share` 列）。
>
> 判定方式：C2 实施期先用真调脚本核对 `daily_basic` 在历史日的 `total_share` 可得性与缺口率，再二选一。**在此之前，信号 #7 按「不可判」处理**（记 NaN，计入 §4.3 的 `n_missing`），不得用任何占位值假装可判。该待定项不阻断 C2 其余 8 项落地。
>
> **【已判定 · 2026-08-27 真调核对】选 `daily_basic`。** 实测 `total_share` 非空率在
> 全部抽查日均为 **100%**，且覆盖到本地行情数据的最早一天：
>
> | trade_date | 行数 | `total_share` 非空 | 非空率 |
> |---|---|---|---|
> | 20260825 | 5546 | 5546 | 100.0% |
> | 20260630 | 5508 | 5508 | 100.0% |
> | 20251231 | 5458 | 5458 | 100.0% |
> | 20240102 | 5329 | 5329 | 100.0% |
> | 20220104 | 4670 | 4670 | 100.0% |
> | 20210513 | 4284 | 4284 | 100.0% |
>
> 行数随市场扩容单调上升，属正常。成本对比压倒性：`daily_basic` **每个日期 1 次调用**取回全市场，
> `balancesheet` 需**逐单码**（全市场两期 ≈ 11000 次）。
>
> **实施期仍须解决的一点（不是可得性问题）**：`publish_date` 常落在非交易日（周末/节假日），
> 而 `daily_basic` 只在交易日有行 → 取快照必须**回退到 `publish_date` 当日或之前最近的交易日**，
> 并把该回退写进单测。口径近似（交易日时点股本 vs 报告期时点股本）按原文所述保留，
> 须在 `engine/piotroski.py` 以 `【降级说明】` 标注。

### 4.2 数据层

- `models/market.py::FinancialData` 增 7 列（`Numeric` 精度按量纲：比率类 `Numeric(12,6)`，`total_share` `Numeric(20,4)`，`ocfps/eps` `Numeric(12,4)`）。
- alembic `NNNN_add_piotroski_fields.py`：纯 `ADD COLUMN`（可空），前向非破坏。
- `adapters/tushare.py::fetch_financial_data` / `fetch_financial_by_stock` 的 `fields` 字符串扩 6 列（`total_share` 视上方待定结论另接）。⚠️ **Tushare quirk 复核**：`fina_indicator` 是**支持逗号多码**的接口（V1.5-A 实证：单码 11 字段 / 多码 21 字段），现有 50 只/批 + `asyncio.sleep(0.3)` 的调用形态不变；但**必须显式核对新字段在多码模式下也返回**（多码模式字段集与单码不同，正是 total_equity 第 6 号 bug 的成因）——写一个真调 Tushare 的冒烟脚本核对，不靠推断。
- 同比取数：`MarketDataRepository` 新增 `get_financials_yoy_pairs(ts_codes, as_of_date) -> DataFrame(MultiIndex[ts_code, period_tag])`，`period_tag ∈ {current, yoy}`。实现按 PIT 约束取 `publish_date <= as_of_date` 的最新一期作 current，再取 `report_period = current.report_period - 1 year` 且同样满足 PIT 的一期作 yoy。**不可**复用 `get_latest_n_financials(n=2)`（它取最近两期 = 环比）。
- **5y 回填（C-1 门控）**：沿用 `total_equity` 回填先例（逐批 `(list, start, end)`、per-batch savepoint、`_BATCH_SIZE` 分批、NaN→None、数值 clamp 防溢出——7 个连环 bug 的教训全部适用，回填脚本须逐条对照 memory `total-equity-null-refresh-bug` 自检）。

### 4.3 Engine 纯函数

新建 `engine/piotroski.py`：

```python
F_SCORE_ITEMS: tuple[str, ...] = (...)   # 9 项名，稳定顺序

def compute_f_score(
    current: pd.DataFrame,      # index=ts_code，含 7+1 列
    prior: pd.DataFrame,        # 同比上年同期
) -> tuple[pd.Series, pd.DataFrame]:
    """返回 (f_score 0-9 或 NaN, 逐项 0/1/NaN 明细矩阵)。Engine 层纯函数，无 IO。"""
```

- 逐项：输入任一必需字段为 NaN → 该项记 NaN（**不记 0**）。
- 汇总：`n_missing = 明细 NaN 项数`；`n_missing >= 3` → `f_score = NaN`（**不可判**），否则 `f_score = 明细.sum(skipna=True)`，并在 reason 中标注「基于 {9-n_missing} 项可判信号」。
  - 这条是 C-4「不静默掩盖」的直接落地：**缺数据 ≠ 低分**。把缺项算 0 会让数据缺口伪装成基本面恶化，进而错误地把股票踢出均值回归——正是 SDD-EXT-04 想避免的反面。
- 明细矩阵进 lineage（`score_breakdown_raw` 之外，由 C2 决定挂载点，见 §4.5）。

### 4.4 门控落点与降级

- 落点：`MeanReversionStrategy.apply_constraints`（C1-1 的钩子）。命中门控（`f_score < 6`）→ 该行 `rsi_oversold / price_deviation / bb_position` **三列全置 NaN**。
- 金融股（`sw_industry_l1 ∈ {银行, 证券, 保险, 多元金融}`，与 `UniverseFilter.FINANCIAL_INDUSTRIES` 同源常量，勿重复定义）：走替代判据 `roe > 0.05` → 通过；否则命中门控。
  - 【降级说明】：SDD 外评原文含「不良贷款率未显著上升」，Tushare `fina_indicator` **无 NPL 字段**，V1.5-C 仅实现 ROE 分支。当前降级内容 = 金融股仅 ROE>5%；原因 = 数据源无 NPL；恢复条件 = 接入含 NPL 的数据源（AKShare 银行专项或第三方）后补第二判据。
- `f_score = NaN`（不可判）→ **不门控**（保留该股参与均值回归），并计数告警。理由同 §4.3：不可判不等于不合格。
- 【设计待定：Altman Z-Score 是否作为 F-Score 不可判时的备选判据】——SDD 外评 §7.2.2 提供该备选。纳入与否取决于 C2 实施期实测的「不可判股票占比」：若 5y 回填后不可判占比 < 5%，直接放弃备选（复杂度不划算）；≥ 5% 则纳入。**判定数据在 C2 回填后立即可得，不得跨子批推迟**。
- 可观测：`piotroski_gate_applied: date=%s blocked=%d unjudgeable=%d financial_alt=%d`（同 A5b 先例，激活可实证）。

### 4.5 C2 DoD

- [ ] RED：`compute_f_score` 9 项逐项单测（含边界：ε 容差、全字段齐 → 0~9、缺 1~2 项 → 部分可判、缺 ≥3 项 → NaN）
- [ ] 单测：门控在 `compute_strategy_factors` 生效（命中行三列 NaN）；金融股走 ROE 替代；不可判不门控
- [ ] alembic 迁移升/降级测试；ORM `__table_args__` 与迁移一致
- [x] Tushare 多码模式新字段返回性**真调核对**（不靠推断），结果写回本节 —— **2026-08-27 实调，`fina_indicator` 逗号多码无坑**：`period=20251231` + 5 只测试股，逐单码与逗号多码**返回行数与各列非空数完全一致**（均 5 行），6 个新增字段全部随多码模式正常返回：

  | 字段 | 单码非空 | 多码非空 |
  |---|---|---|
  | `roa` / `current_ratio` / `grossprofit_margin` | 4/5 | 4/5 |
  | `ocfps` / `eps` / `assets_turn` | 5/5 | 5/5 |
  | 既有 `roe` / `netprofit_yoy` / `tr_yoy` / `debt_to_assets` | 5/5 | 5/5 |

  **那 1/5 缺值经核对是 `000001.SZ` 平安银行（行业＝银行）**——正是 §4.2 已设计走 ROE 替代判据的金融股分支，属预期而非数据缺陷。故 `fetch_financial_by_stock` 扩这 6 列即可，**调用形态不必变**（无需仿照 `balancesheet` 改逐单码）。

  ⚠️ **顺带实调出第三例「静默忽略」**：向 `fina_indicator` 显式索要 `total_share`，接口**不报错、也不返回该列**——返回列只有 `['ts_code','end_date']`。这与 `balancesheet` 对逗号多码静默返空、pandas_ta 对旧参数名 `std=` 静默吞掉是同一类失败。**判据同 §4.4：接了字段却毫无作用的代码，能跑过任何只验证「不抛异常」的测试**；新增取数字段必须写一条「该字段真的有值/真的影响结果」的断言，而不是只看调用成功。
- [ ] `get_financials_yoy_pairs` 集成测试：PIT 约束（`publish_date <= as_of`）+ 同比而非环比（精确 `== N` 断言）
- [ ] 生产 5y 回填（C-1 授权 + 前置 `pg_dump -t financial_data` 定点备份）；回填后核查 7 列非空率并记录
- [ ] 生产实证日志 `piotroski_gate_applied` 出现且 `blocked > 0`
- [ ] 不可判占比实测 → 据此就地决定 Altman Z-Score 备选纳入与否，结论写回 §4.4
- [ ] SDD §7.2.2 回写

---

## 5. C3 — 低波动策略

> roadmap §1 + SDD §7.3：低历史波动率 + 低 Beta 标的筛选（低波动异象在 A 股有实证支持）。新建 `engine/strategies/low_volatility.py`。

### 5.1 因子设计

| 因子 | 定义 | 方向 | 权重 |
|------|------|------|------|
| `inv_volatility` | `-σ60`（60 交易日日收益标准差，取负号使低波动 → 高值）| 越低越好 | 0.55 |
| `inv_beta` | `-β120`（对 000300.SH 的 120 交易日回归 Beta，取负号）| 越低越好 | 0.45 |

- σ60 与 C1 的 `volatility_window` 共用同一纯函数（`engine/volatility.py` 抽公共实现，避免两处各算一遍）。
- β120 = `cov(r_i, r_m) / var(r_m)`，窗口 120 交易日，有效对齐样本 < 80 → NaN。
- **窗口选择说明**：教科书 Beta 常用 252 交易日；此处取 120 是为了**复用现有 `_PRICE_WINDOW_DAYS = 180` 日历天（≈120 交易日）快照窗口**，使 C3 对数据层零改动、对 2GB 生产机零额外内存。这是有意的口径偏离，须以 `【降级说明】` 标注（恢复条件：价格窗口扩至 ≥400 日历天并实测 2GB 机内存/延迟可接受后，改 `beta_window=252`）。`LowVolatilityStrategyConfig.beta_window` 参数化，改窗口即可切换。
- 无新数据源：`adj_prices`（已在 `MarketSnapshot`）+ `index_adj_prices`（已在，MomentumStrategy 的 `rs_6m` 在用）。

### 5.2 配置与注册

- 新增 `LowVolatilityStrategyConfig(volatility_window=60, beta_window=120, benchmark="000300.SH")` + `config_key = "strategy_params_low_volatility"`，`CONFIG_KEY_LEVEL` 登记 **L2**（与其余 `strategy_params_*` 一致）。
- `ConfigService` 增 getter；`config_snapshot.py` 的 `_CONFIG_MAP` 增一行（Pipeline 快照登记）。
- 策略实例注册三处（**必须同步，漏一处即路径不一致**）：`api/deps.py`、`pipeline/daily_pipeline.py::_cp2_scoring`、`services/backtest_service.py`。
- 入 composite 的权重/正交化/持久化处理见 **§8**（本子批的主要风险都在那里）。

### 5.3 C3 DoD

- [ ] RED：σ60 / β120 纯函数单测（含已知解析解的构造数据、样本不足 → NaN、常数序列 → NaN 而非 0）
- [ ] 单测：因子方向正确（低波动股 `inv_volatility` 更高）
- [ ] 单测：三处注册点齐全（用 import 反射或显式断言列表长度）
- [ ] §8 的 composite 接入项全部通过（含影子模式默认 0 权重、正交化矩阵排除、candidate_pool 扩列）
- [ ] 集成测试：跑通含 low_volatility 的五步管线，断言现有四策略 `z_raw` 与接入前**逐值一致**（零回归证明）
- [ ] 生产上线后观测 `scorer_strategy_skipped_*` 无 low_volatility 异常，且日志可见其参与
- [ ] SDD §7.3 回写（低波动策略由「预留」改为「已实现」）

---

## 6. C4 — 资金动向数据层 + 策略

> roadmap §1 + SDD §7.3：主力资金净流入 + 北向资金变化。SDD §5 数据字段表标「V1.5+ 可选」。**代码库当前零实现**（grep moneyflow / hsgt = 0）。

### 6.1 Tushare 接口选型

| 接口 | 粒度 | 覆盖 | 本设计用途 |
|------|------|------|-----------|
| `moneyflow` | 个股 × 日 | **全市场**（~5000 行/日）| ✅ **主因子源**（主力资金净流入）|
| `hk_hold` | 个股 × 日（沪深港通持股）| **仅 Connect 标的**（~1400-2600）| ✅ 副因子源（北向持股变化）|
| `moneyflow_hsgt` | **市场级** × 日（1 行/日）| — | ❌ 不作个股因子（无横截面区分度）；可留作 MarketState 增强，本主题不做 |
| `hsgt_top10` | 每日仅 10 只 | 极稀疏 | ❌ 不用（v0.2 曾列入，展开后据实排除）|

> v0.2 把 `hsgt_top10` 列为北向数据源，展开核对后改用 `hk_hold`——前者每日仅十大成交股，无法支撑横截面因子。

> ## 🔴【2026-08-28 真调核对：`hk_hold` 的「个股 × 日」前提已不成立】
>
> 上表把 `hk_hold` 标为「个股 × **日**」并作副因子源（北向持股变化）。**实调证明该前提自
> 2024-08-19 起失效**——A 股（北向）持股行**只在季末存在**，日频已停止披露：
>
> | 断点定位 | A 股行数 | | 断点后抽查 | A 股行数 |
> |---|---|---|---|---|
> | 2024-08-15 | 3338 | | 2025-03-31（季末）| 3366 |
> | **2024-08-16** | **3337 ← 最后一个有数据的日子** | | 2025-06-30（季末）| 3788 |
> | **2024-08-19** | **0** | | 2025-09-30（季末）| 3340 |
> | 2024-08-20 / 21 / 23 / 30 | 0 | | 2025-12-31（季末）| 3325 |
> | 2026-07-31 / 08-25 | 0 | | 2025-07-15 / 11-20（非季末）| **0** |
>
> 近端 `hk_hold(trade_date=...)` 仍返回 ~950 行，但**全部是 `.HK` 港股（南向）**，A 股为 0
> ——只看总行数会误判为"有数据"。
>
> **后果**：「北向持股变化」作为**日频横截面因子不可实现**。三条出路，需拍板：
> 1. **砍掉北向副因子**，C4 只做 `moneyflow` 主力资金（主因子源本就是它）；
> 2. 用季末快照 + LOCF —— 但"资金动向"因子携带最长 3 个月的陈旧值，与其语义相悖；
> 3. 另找日频数据源（`moneyflow_hsgt` 是市场级 1 行/日，已在上表排除）。
>
> **倾向 1**：`moneyflow` 全市场日频完整，足以支撑 C4 的因子；北向日频数据在数据源侧已经
> 不存在，不是实现问题。
>
> ### 同批真调核对的另外两项
>
> - ✅ **`moneyflow` 支持逗号多码**（逐单码 5 行 vs 多码 5 行一致）→ 采集形态不受限。
> - ✅ **`net_mf_amount` 单位确认是「万元」**，设计的「万元→元」换算正确。用量级反推：
>   600519.SH 于 2026-08-25 `net_mf_amount = −20,849.83`，当日成交额 27.6 亿元 →
>   按万元解释为 **−2.08 亿元（占成交额 −7.56%）**，按元解释为 −0.0008%，后者荒谬。
>   （不靠文档而靠量级反推，是因为 A5c 曾在 `express.yoy_net_profit` 上栽过：
>   文档写"增长率"，实际是"去年同期净利润"。）
> - 🔴 **`hk_hold` 逗号多码静默返空**（单码 1 行 / 多码 0 行，均不报错）——
>   这是该族的**第四例**（前三例见 `docs/reviews/silent_ignore_audit_2026-08-27.md`）。
>   若保留北向数据采集，**必须按 `trade_date` 全市场取或逐单码取**，禁用逗号多码。
>
> ### 已排除全部替代源（砍之前逐个验过）
>
> | 接口 | 2026-08-25 实测 | 能否支撑横截面因子 |
> |---|---|---|
> | `hk_hold` | 958 行，**A 股 0** | ✗ 日频 A 股已停 |
> | `hsgt_top10` | 20 行，A 股 20 | ✗ **未停，但每日仅十大成交股**——5000 只 universe 覆盖 0.4% |
> | `ggt_top10` | 20 行，A 股 0 | ✗ 那是港股通（南向） |
>
> **→ 决定（2026-08-28，用户拍板）：砍掉北向副因子，C4 只做 `moneyflow` 主力资金。**
> 数据在源头就不存在，不是实现问题。§6.2 的 `hk_hold` 表、§6.3/§6.4 中
> `north_hold_chg` 相关内容一并作废。
>
> ### 🔴 连带发现：`moneyflow_hsgt.north_money` 的**语义在同一天悄悄变了**
>
> 曾考虑用市场级北向资金流做 MarketState 增强。实调发现该字段**字段名、单位、供数都没变，
> 但含义从「净流入」变成了「成交额」**，切换点与上面同为 **2024-08-19**（同一次沪深港通披露改革）：
>
> ```
> 2024-08-16   north_money  −6775.0   ← 最后一个净流入语义的日子（有正有负）
> 2024-08-19   north_money  +88110.6  ← 此后再无负值，量级跳约 100 倍
> ```
>
> 按年看负值占比：2021 **34.2%** / 2022 **47.9%** / 2023 **51.5%** / 2024 35.6% /
> 2025 **0.0%** / 2026 **0.0%**。**这是本族最毒的一种**——不报错、不断流，跨该边界做任何
> 特征工程都会得到无意义的结果。
>
> **实测其预测力（仅用切换后的干净段 2024-09-02 ~ 2026-08-25，464 个交易日）**：
>
> | 特征 | fwd5 | fwd10 | fwd20 |
> |---|---|---|---|
> | `north_money` 原始水平 | −0.129 | −0.220 | **−0.292** |
> | **`z60`（60 日去趋势）** | +0.036 | **+0.007** | −0.004 |
> | `chg5`（5 日变化率） | +0.034 | +0.085 | +0.074 |
>
> 原始水平那个 −0.29 是**水平趋势伪相关**（北向成交额样本期内单调上行 13 万 → 34 万），
> 判据就在表内：同序列 60 日 z-score 去趋势后相关塌到 **0.007**。按状态分组同样为噪声级
> （OSCILLATION −0.042 / n=302，UPTREND −0.112 / n=79，DOWNTREND n=14 不足以判定）。
>
> **→ 决定（2026-08-28）：不纳入 MarketState。** 不是"嫌麻烦"，是**实测无信号**——
> 且可能携带方向性信息的那个量（净流入）已在数据源侧消失，残存的成交额是活跃度指标。
> 若将来北向净流入恢复披露，可重启该评估。

### 6.2 表设计（列裁剪）

```
money_flow(id, ts_code, trade_date,
           net_mf_amount,            # 净流入额（万元→元，adapter 内换算）
           buy_elg_amount, sell_elg_amount,   # 特大单
           buy_lg_amount,  sell_lg_amount,    # 大单
           updated_at)
  UNIQUE(ts_code, trade_date); INDEX(trade_date); INDEX(ts_code, trade_date DESC)

hk_hold(id, ts_code, trade_date, hold_vol, hold_ratio, updated_at)
  UNIQUE(ts_code, trade_date); INDEX(trade_date)
```

**列裁剪依据**：`moneyflow` 原始返回含小单/中单/大单/特大单 × 买卖 × 量额 共 20+ 列；本策略只用「特大+大单净额」与「总净额」，其余列（小单/中单、笔数、成交量口径）不入库。理由见 §6.4 的磁盘预算——每多存一列，5y 回填多约 100-150MB。

### 6.3 采集接线

- `adapters/tushare.py` 新增 `fetch_money_flow(trade_date)` / `fetch_hk_hold(trade_date)`，走现有 `_call()` 异步包装 + Semaphore + 单位换算（Tushare 资金流金额单位为**万元**，adapter 内换算为元，与 `daily_quote.amount` 口径一致）。
- `DataService.ingest_daily` 新增第 5/6 段（在指数段之后），**独立 try/except + `logger.exception` + `_record_exception_metric`**，失败不阻断行情/财务主链路（资金流是增强数据，非信号必需）。
- `ingest_history` 同步接线（per-day 独立 `AsyncSessionLocal` 的既有形态不变）。
- ⚠️ **bulk upsert 分批**：`moneyflow` 单日 ~5000 行 × 7 列 = 35000 占位符 > asyncpg 上限 32767 → **单日即触发**，必须 `_BATCH_SIZE=500` 循环。这是全项目最容易踩的坑（memory `asyncpg 参数上限`），且此处**用真实日数据就会踩到**，不需要合成大数据才暴露。

### 6.4 回填预算（生产实证 2026-08-14）

| 实证项 | 数值 |
|--------|------|
| 生产磁盘 | 50G 总 / 39G 已用 / **8.2G 可用（83%）** |
| DB 总大小 | 3182 MB |
| `daily_quote` | 1581 MB / 6,584,494 行 → **≈ 240 B/行**（含索引）|

按 240 B/行外推：`money_flow` 5y ≈ 1223 交易日 × 5000 股 ≈ **6.1M 行 ≈ 1.5 GB**；`hk_hold` 5y ≈ 1223 × 2600 ≈ 3.2M 行 ≈ 0.7 GB。合计 **≈ 2.2 GB** → 磁盘将从 83% 推到 **≈ 87-88%**，叠加 WAL/备份峰值风险偏高。

**决策：回填窗口取 2 年，不取 5 年。**
- 依据：C4 策略权重激活只需 ICIR warmup = 272 交易日（≈1.15 年）+ `state_min_samples=60` 的分状态余量。2 年（≈488 交易日）足够让三个 market_state 都攒够样本。
- 体量：`money_flow` ≈ 2.4M 行 ≈ 0.6 GB + `hk_hold` ≈ 1.3M 行 ≈ 0.3 GB = **≈ 0.9 GB**，磁盘至 ~85%，可接受。
- 回测深度受限的代价：含资金动向因子的回测最早只能回溯 2 年。**明确记录为已知局限**（写入 SDD §7.7.5 回测局限审计表），恢复条件 = 扩容磁盘后补回填至 5y。
- **C-1 门控 + 前置实测**：回填前先跑 100 个交易日样本，实测真实行宽（`pg_total_relation_size`）校正上述外推，再决定是否维持 2 年。禁止按估算直接开跑。

### 6.5 策略设计（`engine/strategies/money_flow.py`）

| 因子 | 定义 | 覆盖 | 权重 |
|------|------|------|------|
| `main_net_inflow_5d` | 近 5 日「特大+大单」净额合计 / 近 5 日成交额合计（标准化，去市值量纲）| 全市场 | 0.45 |
| `main_net_inflow_20d` | 同上，20 日窗口 | 全市场 | 0.30 |
| `north_hold_chg_20d` | `hold_ratio(t) - hold_ratio(t-20)` | **仅 Connect 标的** | 0.25 |

**覆盖率陷阱与处理（关键）**：`north_hold_chg_20d` 对非 Connect 标的恒为 NaN（占全市场 ~50%+）。`Scorer` 的策略内合成是 `z_df.mean(axis=1, skipna=True)`——只要**至少一个因子有值**，该股的 `money_flow` strategy_z 即非 NaN。因此把全覆盖的主力资金因子设为主因子（合计权重 0.75）可保证 **money_flow 策略本身对全市场几乎零 NaN**。这不是可选优化，而是 §8 陷阱 1 的必要前提：策略级 NaN 会经 Gram-Schmidt 的 `valid_mask` 交集把整行 composite 打到 0。

- `MarketSnapshot` 扩两个可选键 `money_flow: pd.DataFrame | None` / `hk_hold: pd.DataFrame | None`（`total=False` 已允许缺键，冷启动/回测不构造时策略返回全 NaN → `Scorer` 记 `scorer_strategy_skipped_all_nan` 并跳过，行为安全）。
- `ScoringService._build_market_snapshot` 的 `asyncio.gather` 增两个 repo 查询（窗口 = 近 40 交易日，够算 20 日变化）。
- 配置 `MoneyFlowStrategyConfig(short_window=5, long_window=20, north_window=20)`，`config_key = "strategy_params_money_flow"`，L2。
- 注册三处（同 §5.2）。

### 6.6 C4 DoD

- [ ] RED：因子纯函数单测（标准化口径、窗口不足 → NaN、非 Connect 股 `north_hold_chg` NaN 但策略 z 非 NaN）
- [ ] adapter 单测：万元→元换算、空返回处理、`logger.exception` 不静默
- [ ] **bulk upsert 分批单测**：构造 ≥3000 行断言不超 asyncpg 占位符上限（合成小数据会绕过此 bug）
- [ ] alembic 两表迁移升/降级测试
- [ ] 集成测试：`ingest_daily` 新段失败不阻断主链路（精确断言行情/财务仍入库）
- [ ] 回填前 100 日样本实测行宽 → 校正预算 → C-1 授权 → 2y 回填完成，回填后核查覆盖率与磁盘占用并记录
- [ ] §8 的 composite 接入项全部通过（同 C3）
- [ ] SDD §5 数据字段表 + §7.3 回写；SDD §7.7.5 记入「资金动向因子回测仅 2y 可回溯」局限

---

## 7. C5 — 策略插件沙箱

> roadmap §1 + SDD §15.2：L3 用户编写的策略插件必须在受限沙箱环境中运行。安全规范：只走标准数据接口 / 禁文件网络进程 / 单股 ≤100ms、全市场 ≤5min / 内存 ≤100MB / 子进程或容器隔离 / 加载执行输出全审计。

### 7.1 威胁模型与诚实的边界声明

本系统是自托管、注册开放的多用户系统（V1.5-G），L3 用户可提交 Python 代码。**必须先说清楚沙箱能挡什么、不能挡什么**，否则会给出虚假的安全感（C-4 的精神：不静默掩盖）。

| 威胁 | 本设计的处置 |
|------|-------------|
| 插件死循环 / 超时拖垮管线 | ✅ 硬超时 + 子进程 kill |
| 插件内存爆掉 2GB 机 | ✅ `RLIMIT_AS` 硬限 |
| 插件崩溃影响主进程 | ✅ 子进程隔离 |
| 插件读写宿主文件 | ⚠️ 部分（受限 import + 只读工作目录，非内核强制）|
| 插件发起网络请求 | ⚠️ 部分（socket 模块屏蔽，非内核强制）|
| **恶意用户蓄意逃逸（`ctypes` / C 扩展 / `/proc` 等）** | ❌ **挡不住** |

原因：后端容器以非 root 运行，无 `CAP_SYS_ADMIN`，容器内**无法**使用 `seccomp` / `unshare` / 嵌套容器；真正的强隔离需要 gVisor / Firecracker / 独立 worker 主机——2GB 单机生产环境不具备条件。

**因此的产品级决策：插件执行在生产环境默认禁用。** 沿用 V1.5-A/RC 期「回测在生产禁用（`backtest_enabled=false` → 503）、回测走本地算力中心」的既有先例：新增 `plugin_execution_enabled`（生产 `false`），`POST /strategies/plugins/{id}/run` 在生产返回 503 并给出明确文案；插件的编写、上传、管理、审计在生产可用，**执行**在本地算力中心。SDD §15.2 的六条限制项在本地执行路径上全部落地。

### 7.2 隔离机制

- 载体：`multiprocessing.get_context("spawn")` 子进程（非 fork——避免继承父进程的 DB 连接/事件循环/已导入模块）。
- 资源限制（子进程启动后、加载插件前施加）：`RLIMIT_AS = 100MB`（SDD §15.2）、`RLIMIT_NOFILE` 收紧、`RLIMIT_NPROC` 禁止再派生。
- 时限：父进程 `join(timeout)`；单股 100ms 换算为全市场预算 `min(5min, n_stocks × 100ms)`，超时 `terminate()` → `kill()` 两段。
- 导入控制：子进程内安装受限 `__import__`，白名单 `{math, statistics, pandas, numpy}`；显式黑名单 `{os, sys, subprocess, socket, ctypes, importlib, builtins, pathlib, shutil}`。
- 网络：子进程内把 `socket.socket` 替换为抛异常的桩。
- 数据接口：插件只接收**已构造好的 pandas 结构**（universe + 该策略允许的因子输入切片），不传 session / repo / adapter——SDD「只能通过系统提供的标准数据接口获取数据」的落地方式。
- 输出校验：返回值必须是 `index=ts_code`、数值列的 DataFrame，形状/索引/类型全校验；越界值（inf/超长列名/非法列数）拒收。

### 7.3 插件契约

复用而非另起：插件实现 `compute_raw_factors(universe, data) -> pd.DataFrame` 单函数（`BaseStrategy` 的核心抽象方法子集），由 `PluginStrategy(BaseStrategy)` 适配器包装成标准策略——这样插件天然获得五步管线、`apply_constraints`、lineage、ICIR 监控全部能力，兑现 SDD §15.2「新增策略只需实现标准接口，无需修改核心引擎」。插件**不允许**覆写 `score()` / `apply_constraints`（避免绕过约束）。

### 7.4 存储与审计

```
strategy_plugin(id, user_id FK, name, version, source_code TEXT, status,
                created_at, updated_at)            UNIQUE(user_id, name, version)
strategy_plugin_audit(id, plugin_id FK, user_id FK, action, trade_date,
                      duration_ms, peak_memory_kb, exit_status, error_excerpt,
                      created_at)                  INDEX(plugin_id, created_at DESC)
```
`action ∈ {upload, update, delete, load, execute}`，覆盖 SDD「加载、执行、输出均记录审计日志」。`error_excerpt` 落库前必须过 `SecretFilter`（Phase 13）。

### 7.5 端点（全部 L3 + ownership 校验）

| 方法 | 路径 | 说明 | 冒烟编号 |
|------|------|------|---------|
| GET | `/strategies/plugins` | 列出本人插件 | API-115 |
| POST | `/strategies/plugins` | 上传（静态校验：语法、禁用 import、大小上限）| API-116 |
| GET | `/strategies/plugins/{id}` | 详情 + 最近审计 | API-117 |
| DELETE | `/strategies/plugins/{id}` | 删除（软删）| API-118 |
| POST | `/strategies/plugins/{id}/run` | 试运行（生产 503）| API-119 |

DI 全部放 `api/deps.py`（CLAUDE.md §4.2）。ownership 校验复用 V1.5-G 既有机制。

### 7.6 C5 DoD

- [ ] RED：沙箱逃逸用例先失败——`import os` 被拒 / socket 被拒 / 内存超限被杀 / 死循环超时被杀 / 返回值形状非法被拒（每条独立单测）
- [ ] 单测：`PluginStrategy` 适配器产出的因子矩阵能被 `Scorer` 正常消费
- [ ] 端点 5 个冒烟测试（API-115~119）覆盖 401/200/404/422 + 生产 503 分支
- [ ] alembic 两表迁移升/降级测试
- [ ] **专项 security-review**（本模块单独评审，含 §7.1 边界声明的准确性复核）——通过前不合并
- [ ] 生产环境 `plugin_execution_enabled=false` 实证（`docker exec printenv` 确认容器拿到值 + 端点真返 503）
- [ ] SDD §15.2 回写（实现方式 + 生产禁用决策 + 能力边界）
- [ ] `deployment.md` 增「插件执行仅本地算力中心」运维红线条目

---

## 8. 横切：新增策略入 Phase 11 composite

> C3（低波动）+ C4（资金动向）把 composite 策略从 4 增至 6。本节是全主题最大设计风险，独立成节。

### 8.1 生产现状（实证 2026-08-14，`trade_date=2026-08-01` 生效权重）

| state | weights_source | trend | momentum | mean_reversion | value |
|-------|---------------|-------|----------|----------------|-------|
| UPTREND | `icir` | 0.000 | 0.000 | 0.182 | **0.818** |
| OSCILLATION | `icir` | 0.000 | 0.000 | 0.375 | **0.626** |
| DOWNTREND | `default_matrix` | 0.100 | 0.050 | 0.150 | 0.700 |

聚合 ICIR（2026-07-31）：value 0.72/1.79、mean_reversion 0.43/0.40、trend −0.24/−0.10、**momentum −0.66/−0.73**。即 ICIR 机制已自动把两个负 ICIR 策略降权到 0——机制按设计工作，但也意味着 composite 实际是**双策略**。新增两个策略进入这样一个已高度集中的 composite，必须极其小心。

### 8.2 决策：结构入 composite + 影子模式两阶段激活

**采纳方案：C3/C4 作为一等 composite 成员实现（进 `strategy_factors`、走五步管线、被 ICIR 监控），但权重从 0 起步，经 ICIR 验证后由月末 rebalance 自动激活。**

理由：
- 对比方案「作独立 gate / 叠加层」（不入横截面加权）——隔离性好，但新策略永远拿不到 IC 监控与自动降权，等于游离在 Phase 11 的质量机制之外，且与 SDD §7.3 把二者定义为「策略」不符。
- 影子模式让**第一天零回归**成为可证明的性质（§8.3 陷阱 2），激活是一次可观测、可回退的独立事件——与 A5b/F-4 的教训一致：**上线 ≠ 激活，激活必须能实证**。

阶段划分：

| 阶段 | 权重 | 判据 | 可观测 |
|------|------|------|--------|
| 影子 | 0（`default_matrix` 显式登记 0.0；ICIR 路径天然 0）| 上线即进入 | 日级 IC 行开始累积（C0 保障）|
| 激活 | ICIR 归一化权重 | 该策略在某 state 的 `sample_size ≥ 60` 且 `icir > 0` → 月末 rebalance 自动给权 | `strategy_weights_history` 出现该策略非 0 行 + composite 权重表变化 |

激活是**自动**的（复用 `apply_monthly_rebalance` 现有逻辑，无需特判代码），但落地时必须显式核查并记录——不假设。

### 8.3 三个必须处理的机制陷阱

**陷阱 1（头号风险）：零权重策略仍参与 Gram-Schmidt，其 NaN 行会把整行 composite 打到 0。**

`Scorer.aggregate` 目前把**所有** `active_strategies`（= 所有产出非空因子的策略）送进 `orthogonal_matrix = self._orthogonalizer.compute(strategy_z_matrix, effective_order)`，而 `Orthogonalizer.gram_schmidt` 内：

```python
valid_mask = matrix.notna().all(axis=1)   # 所有 order 列同时非 NaN 的行才参与投影
...
return residual_df.reindex(strategy_z_matrix.index)   # 其余行全列 NaN
```

回到 `Scorer`：NaN 残差列经 `z_col.fillna(0.0) * w` → 该行 `weighted_z = 0` → `composite_z = 0`，而 `any_valid` 仍为 True（raw z 存在）→ **该行不会被剔除，而是拿到 `Φ(0)×100 = 50` 分**——一个「正中间」的假分数。

后果：任何新策略只要对某些股票是 NaN，这些股票的 composite 就被打平到中位，**即使新策略权重为 0**。C4 的北向因子、C3 的历史不足股都会命中。

**处置（已于 C0 实施，2026-08-18，提前于原定 C3）**：`Scorer.aggregate` 中把送入正交化的策略集从「所有 `active_strategies`」收敛为「`weights_runtime` 中权重 > 0 的策略」（零权重策略不参与正交化，但仍保留在 `strategy_z_matrix` 中——日级 IC 由 C0-6 的 `strategy_z_all` 保障，旧四标量字段 `trend_score` 等照常取值，`score_breakdown_raw` 语义维持不变、只含参与合成的策略）。

提前实施的理由：本条不是「C3/C4 新增策略才会触发的风险」，而是**当前生产正在犯的缺陷**——trend / momentum 权重自 2026-05-01 起即为 0.000 却仍在正交化矩阵中，凡在这两个策略上有 NaN 的股票，其 composite 此刻正被压平到中位分。与 C0-6 同处 `Scorer.aggregate` Step 4/5，同批修复、同批回归，边际成本接近零。测试见 UT-C0-08a/b（零权重策略「存在与否」composite_z 逐股一致 + 未覆盖股票不再得 50 分）。
- 这同时修复一个**既有生产缺陷**：当前 trend、momentum 权重已是 0.000，却仍在正交化矩阵里，其 NaN 行正在把对应股票的 composite 压到 0。修复后这些股票恢复按真实权重（mean_reversion + value）打分。
- ⚠️ 此改动会**改变生产评分结果**（属修复而非回归），必须：① 单测锁定新旧行为差异；② 本地 5y 面板跑改前/改后 composite 分布对比；③ 上线后首日人工核对 top 信号变化。**不得与 C3/C4 同批上线**——在 C1 阶段单独上线并观察，把变量分开。

**陷阱 2：`default_matrix` 必须显式登记新策略 = 0.0。**

`StrategyWeightsConfig` 的三个 dict 是权重矩阵的冷启动真值。若不登记新策略，`get_active_weights` 的 `weights.setdefault(s, 0.0)`（按 `_STRATEGY_NAMES`）与 `apply_monthly_rebalance` 的 `positive_icirs[s] = ... if s in snapshots else 0.0` 会给 0（安全），但 `_default_weights_for_state` 返回的 dict 将缺键 → 冷启动/DOWNTREND（当前正走 `default_matrix`）路径下新策略直接缺席，行为在不同路径间不一致。
**处置**：`uptrend/downtrend/oscillation` 三个 dict 各显式加 `"low_volatility": 0.0, "money_flow": 0.0`；`_STRATEGY_NAMES` 与 `scorer._STRATEGY_KEYS` 同步扩至 6。
✅ 好消息：`apply_monthly_rebalance` 的 `weights = {s: positive_icirs[s]/total}` 中 `total` 含新策略的 0，**现有四策略的相对权重逐值不变**——影子期零回归在这条路径上是数学保证，可用单测锁定。

**陷阱 3：`candidate_pool` 的 4 个标量列与两处策略名映射需扩。**

- `candidate_pool` 有 `trend_score / momentum_score / reversion_score / value_score` 四列 → 加 `low_volatility_score / money_flow_score`（alembic `ADD COLUMN`，可空，前向非破坏）。
- `scorer.SCORE_COLUMN_MAP` + `scorer._STRATEGY_KEYS` + `factor_monitor_service._FACTOR_MAP` + `_STRATEGY_NAMES` 四处策略名清单必须同步（**建议合并为 `core/strategy_registry.py` 单一事实来源**，从根上消除「改了三处漏第四处」——这类分散清单正是本项目反复踩的坑）。
- `PoolEntry` / `write_candidate_pool` / `schemas` / 前端候选池表格与信号溯源视图同步。
- `AttributionService` 的多因子回归自动纳入新策略（其读 `score_breakdown_raw` 的键，无需硬编码）——需回归测试确认自由度足够（6 因子 + 截距，样本 ~50 行的 pool 仍充裕）。

### 8.4 其余设计约束

- **正交化顺序**：`orthogonalize_order` 由 ICIR/权重降序生成，新策略影子期权重 0 → 自然排最后。陷阱 1 修复后它们根本不进矩阵，顺序问题消解；激活后按 ICIR 排序自动就位。
- **横截面相关性**：低波动与动量在 A 股通常显著负相关，与 value 正相关。Gram-Schmidt 的共线退化检测（残差 std / 原 std < 0.3 → 该列置 NaN，`collinear_skipped`）会自动处理信息被吸收的情形。C3/C4 激活前须跑一次 6 策略 `strategy_z` 相关矩阵并记录在案。
- **不做的事**：本主题**不**引入策略内多因子 ICIR 加权（`Scorer` 现为列向均值合成，Phase 11 已标注为 V1.5+ 可替换项）。混在 6 策略扩容里做会让归因不可分离；单列为后续主题。

### 8.5 §8 DoD

- [ ] 陷阱 1 修复：单测锁定「零权重策略不进正交化矩阵」+ 「NaN 行不再被打到 composite_z=0」
- [ ] 陷阱 1 本地 5y 面板改前/改后对比报告（composite 分布 + top50 重合度），随 C1 单独上线并观察 ≥3 个交易日
- [ ] 陷阱 2 单测：新策略权重 0 时，四策略权重逐值等于接入前（零回归数学保证）
- [ ] 陷阱 3：`strategy_registry.py` 单一事实来源建立，单测断言四处旧清单已全部改为引用它
- [ ] alembic candidate_pool 扩 2 列迁移；前端两个视图适配
- [ ] 6 策略 `strategy_z` 相关矩阵报告归档
- [ ] 激活实证：新策略首次在 `strategy_weights_history` 拿到非 0 权重后，核对当日 composite 与信号变化并记录

---

## 9. 测试计划与收尾门槛

### 9.1 测试矩阵

| 层 | 文件 | 覆盖 |
|----|------|------|
| unit | `test_daily_ic_producer_job.py` | C0-1/C0-2 幂等、滞后消费、追平上限 |
| unit | `test_strategy_constraints.py` | C1-1 `apply_constraints` 两路径同源、三条迁移约束、raw 域语义 |
| unit | `test_momentum_risk_adjusted.py` | C1-2 风险调整因子、参数化生效、回退开关 |
| unit | `test_piotroski.py` | C2 9 项逐项 + 缺项 → NaN + 不可判阈值 |
| unit | `test_low_volatility.py` | C3 σ/β 解析解、样本不足、方向 |
| unit | `test_money_flow_strategy.py` | C4 因子标准化、北向 NaN 不传染策略 z |
| unit | `test_plugin_sandbox.py` | C5 逃逸/超时/内存/输出校验 |
| unit | `test_scorer_zero_weight_orthogonal.py` | §8 陷阱 1 |
| unit | `test_strategy_registry.py` | §8 陷阱 3 单一事实来源 |
| integration | `test_int_daily_ic_producer_job.py` | C0 端到端落库（精确 `== N`）|
| integration | `test_int_financial_yoy_pairs.py` | C2 PIT + 同比取数 |
| integration | `test_int_moneyflow_ingest.py` | C4 采集失败不阻断主链路 + 分批 upsert ≥3000 行 |
| integration | `test_int_six_strategy_pipeline.py` | §8 六策略五步管线 + 四策略零回归 |
| e2e | `test_plugins_api.py` | C5 端点 401/200/404/422 + 503 |
| smoke | `test_api_live.py` | API-115~119 |

- 全部 async 测试用 plain `async def`，**禁止任何 marker**（`@pytest.mark.anyio` 已 regression 两次）。
- 集成测试若触发自建 session 真 commit 的副作用表（审计表、通知），`finally` 必须清理，且推送前跑**全量**集成而非受影响子集。

### 9.2 收尾门槛（CLAUDE.md §5.2）

- [ ] `uv run ruff check src/ tests/` = **0 error**
- [ ] `uv run pytest tests/unit/ tests/e2e/ -q` 全绿；`tests/integration/` 全绿（测试 DB **5433**，绝不对生产跑）
- [ ] 前端 `vue-tsc` 0 error
- [ ] 新增 5 个端点冒烟测试逐行对照 §7.5 场景表
- [ ] 所有模块对照各子批 DoD 全部交付；未交付立即回写 roadmap §6
- [ ] 文档头部版本号与修订历史最新版本一致
- [ ] 新经验检查：写入项目 `CLAUDE.md`（如「策略硬约束必须落在 `compute_strategy_factors`」）或 `~/.claude/CLAUDE.md`（如「零权重列仍参与正交化会污染整行」）

---

## 10. 迁移、部署与风险

### 10.1 alembic 迁移清单（按 FK 依赖分层，序号实施时定）

| 迁移 | 内容 | 破坏性 |
|------|------|--------|
| C2 | `financial_data` ADD 7 列（可空）| 前向非破坏 |
| C3/C4 | `candidate_pool` ADD 2 列（可空）| 前向非破坏 |
| C4 | CREATE `money_flow` / `hk_hold` | 新表 |
| C5 | CREATE `strategy_plugin` / `strategy_plugin_audit` | 新表（FK → user）|

全部为 ADD/CREATE，无 DROP/ALTER TYPE。仍须 `backend/` 目录内执行，且生产升级前 `pg_dump` 定点备份受影响表。

### 10.2 生产部署红线（沿用既有）

- 生产栈操作必须 `docker compose -f docker-compose.prod.yml --env-file .env.prod`
- 新增 env（`plugin_execution_enabled` 等）必须**双写**：`.env.prod` + root `docker-compose.prod.yml` 的 `environment:` 白名单；改完先 `docker exec ... printenv` 确认容器拿到值再验证行为
- 只重建 backend 须补 `nginx -s reload`
- 三次 C-1 门控的生产写：C0 断档追平 / C2 financial_data 7 列 5y 回填 / C4 两表 2y 回填。**各自独立取得用户确认**，「上次批准过」≠「永久授权」
- 磁盘实证 83% 已用——C4 回填前后各记录一次 `df -h` 与 `pg_database_size`；> 90% 立即停手

### 10.3 收尾回写清单

- roadmap `§6` V1.5-C 行：登记 C0 子批 + 估算收敛实测值；`§1` 三个产品功能行标交付；`§3` SDD-EXT-03/04/08 状态
- `system_design` §3/§5（新模块）+ §6（新端点）+ §4（新表）
- SDD §5 / §7.2.2 / §7.2.3 / §7.2.4 / §7.3 / §7.4 / §7.7.5 / §15.2
- `deployment.md`：插件执行本地化红线 + 磁盘水位监控
- memory：策略约束落点、零权重正交化污染两条通用教训

### 10.4 主要风险登记

| 风险 | 影响 | 缓解 |
|------|------|------|
| 陷阱 1 修复改变生产评分结果 | 用户看到的信号变化 | 与 C3/C4 分批上线、面板对比、首日人工核对 |
| C2 5y 回填重演 total_equity 的连环 bug | 回填污染或空转 | 逐条对照既有 7 bug 教训自检；分批 + savepoint + 回填后核查非空率 |
| C4 磁盘触顶 | 生产 DB 写失败 | 2y 窗口 + 列裁剪 + 100 日样本实测 + 水位监控 |
| C5 沙箱被误认为强隔离 | 安全误判 | §7.1 边界声明写入 SDD + 生产禁用执行 + 专项 security-review |
| 新策略长期拿不到正权重 | 投入产出比低 | 影子期即可通过日级 IC 面板评估；ICIR 持续为负则如实记录并考虑下线（禁止调参硬凑） |
| C0 未落地就做 C3/C4 | 新策略永久空转 | 实施序强制 C0 优先，DoD 互锁 |
