# V1.5-C：策略扩展（风险调整动量 + Piotroski 过滤 + 低波动 + 资金动向 + 插件沙箱）

> 版本：v0.2（启动核查完成 + 设计评审收口，设计待展开，2026-08-10）
> 状态：**启动核查通过（本文件 §1.3）**；scope 锁定全 5 模块纳入、无推迟；§2-§6 详细设计待展开
> 估算：roadmap §6 登记 **9-13 pd**；本文件按「资金动向数据层 + 插件沙箱完整纳入」重估 **~12.5-20 pd**（分项见 §1.2），已在 roadmap §6 V1.5-C 行加前向说明（启动放行时），收尾收敛实测值
> 实施顺序（沿用 V1.5-A「先轻后重」先例，待用户确认）：**C1 → C2 → C3 → C4 → C5**
> 依据文档：
> - roadmap `v1_post_release_roadmap.md` §1（资金动向 / 低波动 / 插件沙箱 3 产品功能行）+ §3（SDD-EXT-04/08）+ §6 主题表 V1.5-C 行
> - SDD `QuantPilot_SDD.md` §7.2.3（动量策略 + 风险调整动量注）/ §7.2.2（均值回归）/ §7.3（策略扩展 V1.5+：资金动向 / 低波动）/ §15.2（策略插件沙箱安全规范）/ §16（版本路线图）
> - SDD 外部评审 `docs/reviews/SDD_review_outside_2026-04-22.md` §7.2.2（Piotroski F-Score 硬过滤）/ §7.2.3（波动率惩罚系数）
> - Phase 11 评分工业化 `docs/design/phases/phase11_scoring_industrialization.md`（新增策略入 composite 牵动 strategy_weights 矩阵 / 逐策略 ICIR / Gram-Schmidt 正交化——本 phase 最大设计风险，见 §7）

---

## 修订历史

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| v0.1 | 2026-08-10 | 初版（启动核查）。执行 CLAUDE.md §5.1：确认 V1.5 主题不占 system_design §9（沿用 V1.5-A/G 先例）；辨明「V1.5-C 因子监控自动降权」为 v2.0 重构前旧标签、已被 Phase 11 §4.1/§4.4 ICIR 自动加权消费，与本主题（策略扩展）无关；grep 推迟项三处确认；**用户拍板两项范围决策（2026-08-10）**：① 资金动向策略的 moneyflow/北向数据层在本主题内一并建（非拆分推迟）；② 策略插件沙箱完整纳入本主题（非拆分/推迟）→ 全 5 模块纳入、零推迟。§2-§6 详细设计待展开 |
| v0.2 | 2026-08-10 | **设计评审收口**（启动核查门评审通过 ✓，0 阻断 / 1 P2 / 4 P3）。P2：估算上修（~50%）的 roadmap §6 权威登记从「收尾回写」提前到启动放行时——已在 roadmap §6 V1.5-C 行加前向说明（→ ~12.5-20 pd）。P3 全部就地纠正：① §1.2 分项和下界据实 13→**12.5**；② C3 5y strategy_weights 历史回填补挂 **C-1 门控**（与 C2/C4 一致）；③ Piotroski 项数明确以经典 **9 项**为准 + 回修 roadmap §3「8 项」笔误；④ §3 financial_data 字段枚举据 market.py 补 revenue_yoy / pe_ttm 实名 + 金融股数据不全降级纳入 Altman Z-Score 备选 |

---

## 1. 概述

### 1.1 背景

V1.5-C 是 V1.0 RC + V1.5-G 多用户 + V1.5-A 回测/数据收尾之后的**策略广度扩展**主题批。V1.0~V1.5-A 的信号系统建立在四大策略（趋势 / 动量 / 均值回归 / 价值）之上；本主题从三个方向扩展策略广度与质量：

- **既有策略增强**（C1/C2）：动量策略引入风险调整（涨幅/波动率）避免偏向高波动标的（SDD §7.2.3 注 + 外部评审 §7.2.3）；均值回归引入 Piotroski F-Score 硬性前置过滤，避免下跌趋势中选中「价值毁灭」标的（SDD 外部评审 P1，§7.2.2）。
- **新增策略**（C3/C4）：低波动策略（低历史波动率 + 低 Beta，A 股低波动异象有实证支持，SDD §7.3）；资金动向策略（主力资金净流入 + 北向资金变化，SDD §7.3——**需先建 moneyflow/北向数据层**，代码库当前零实现）。
- **平台能力**（C5）：策略插件沙箱（SDD §15.2），让 L3 用户在受限隔离环境中编写/挂载自定义策略。

**关键设计风险（§7 专述）**：C3/C4 把 composite 策略数从 4 增至 6，直接牵动 Phase 11 评分管线——strategy_weights_history 权重矩阵（当前 4 策略 × 3 market_state）、逐策略 ICIR 滚动加权、Gram-Schmidt 因子正交化顺序、横截面相关性。新增策略不是「加个文件」而是「改评分骨架」。

### 1.2 Scope 总览

| 子批 | 主题 | pd | 段落 | 生产写 | 数据依赖 | 实施序 |
|------|------|-----|------|--------|---------|--------|
| **C1** | SDD-EXT-08 风险调整动量（涨幅/60 日波动率，V1.5 默认，L2+ 可配）| 0.5-1 | §2 | 无（Engine 纯函数改 momentum.py）| 无（daily_quote 可算）| 1 |
| **C2** | SDD-EXT-04 均值回归 Piotroski F-Score 硬过滤（F-Score<6 → mean_reversion=0；金融股 ROE>5% 替代）| 2-3 | §3 | **有**（financial_data 扩 ~6 fina_indicator 字段 + 5y 回填，C-1 门控）| financial_data 字段扩展（同 Tushare fina_indicator 接口增量）| 2 |
| **C3** | 低波动策略 `low_volatility.py`（低历史波动率 + 低 Beta）| 2-3 | §4 | **有**（新策略入 composite → strategy_weights 矩阵扩行 + 5y 历史回填，**C-1 门控**，见 §7）| 无（daily_quote + index_history 可算 Beta）| 3 |
| **C4** | 资金动向数据层 + 策略（moneyflow/北向 adapter+表+采集 + `money_flow.py`）| 3-5 | §5 | **有**（新表 alembic + 采集管线接线 + 5y 回填，C-1 门控 + 新策略入 composite）| **新 Tushare 接口**（moneyflow / moneyflow_hsgt / hsgt_top10）| 4 |
| **C5** | 策略插件沙箱 `plugin_runner.py`（SDD §15.2 受限隔离运行时 + 标准接口 + 审计）| 5-8 | §6 | **有**（插件存储/审计表 + L3 用户插件管理端点）| 无 | 5 |

**合计 ~12.5-20 pd**（分项精确和：下界 0.5+2+2+3+5=12.5，上界 1+3+3+5+8=20；roadmap §6 登记 9-13 系「资金动向 + 沙箱」按轻量假设，本 phase 用户拍板完整数据层 + 完整沙箱后上修，已在 roadmap §6 加前向说明、收尾收敛）。「先轻后重」＝先做零生产写、快速见效的 C1，再做含 financial_data 扩展的 C2（P1 优先级）、新增策略 C3，再做需新数据层的 C4，最后做安全敏感、需专项 security-review 的 C5。

### 1.3 启动核查（CLAUDE.md §5.1）

| 核查项 | 结论 |
|--------|------|
| 读 system_design §9 本 phase 行 | V1.5-C 是 RC 后 V1.5 主题，**不在 V1.0 §9 Phase 表内**（§9 note line 1455/1459 明确 low_volatility.py/plugin_runner.py 属 V1.5+、Phase 1-15 不创建；V1.5 按 roadmap §6 主题打包）。权威登记在 roadmap §1/§3/§6 V1.5-C 行。沿用 V1.5-A/G 先例（`v1_5_<letter>_<topic>.md` + roadmap §6 登记，不占 §9 Phase 编号）✓ |
| 模块去向决定 | 全 5 模块（C1-C5）纳入本 phase；**零推迟**（用户 2026-08-10 拍板：资金动向数据层本主题内建 + 插件沙箱完整纳入）。无需推迟三链 ✓ |
| 命名撞车辨明 | system_design:1444 / phase11:112 / phase11 评审:17 的「V1.5-C 因子监控自动降权」= v2.0 重构前旧 V1.5-C 标签，已升级并被 Phase 11 §4.1 ICIR 滚动加权 + §4.4 自动下线消费 ≠ 本主题「V1.5-C 策略扩展」。已确认无残留孤儿引用误指本主题 ✓ |
| grep `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + reviews | 本主题源自 SDD §16 产品功能（资金动向/低波动/沙箱）+ SDD-EXT-04/08，非 V1.0 phase 评审 P2/P3 推迟项，无 `R<N>-P<X>-` 追溯编号需消费。已核 reviews/ 无指向 V1.5-C 的未消费评审项 ✓ |
| 孤儿检查（system_design §3/§5 模块 + §6 端点）| 新增模块：`engine/strategies/{low_volatility,money_flow,plugin_runner}.py`（新文件）+ momentum.py/mean_reversion.py 扩展 + moneyflow/北向数据层（adapter + models + repository + 采集管线）+ 沙箱运行时 + 插件/审计表。新增端点：L3 插件管理（`/strategies/plugins/*`，数量待 §6 定）。**均须在设计展开时回写 system_design §3/§5/§6 落位**（收尾）✓ |
| C-5 范围变更回写顺序 | scope（全 5）与 roadmap §6 既有打包一致，**无范围变更**（估算上修属再估、非 scope 变更，收尾回写 roadmap §6）。触及 SDD §7.2.2/§7.2.3/§7.3/§15.2——设计展开时回写 SDD 对应节 ✓ |

**启动核查清单（收尾逐项勾选）**：
- [ ] grep `system_design §9` 无 V1.5-C 专行（确认沿用 roadmap 登记先例，不误建 §9 Phase 行）
- [ ] 命名撞车「因子监控自动降权」旧标签在 system_design/phase11 语境保持指向 Phase 11，无文档误把本主题接线到旧标签
- [ ] 新增模块/端点全部回写 system_design §3/§5/§6 + SDD §7.2.2/§7.2.3/§7.3/§15.2
- [ ] 收尾回写 roadmap §6 V1.5-C 估算 9-13 → 实际值收敛

---

## 2. C1 — SDD-EXT-08 风险调整动量

> roadmap §3 SDD-EXT-08（P2，0.5-1 pd）：动量策略提升为风险调整动量，「涨幅 / 60 日历史波动率」作 V1.5 默认行为。SDD §7.2.3 注：L2+ 可配置增强选项，减少对高波动标的偏向。

**待展开**：现状 momentum.py 因子（3 月涨幅 / 6 月相对强度 / 行业相对强度）→ 引入 60 日历史波动率惩罚（涨幅/σ）；V1.5 默认开 + L2+ 可配开关（UserConfig）；剔除近 1 月涨幅前 5%（SDD §7.2.3 短期反转，可配）；理由模板更新。零数据依赖、零生产写。

---

## 3. C2 — SDD-EXT-04 均值回归 Piotroski F-Score 硬过滤

> roadmap §3 SDD-EXT-04（P1，2-3 pd）+ SDD 外部评审 §7.2.2：均值回归评分前，标的须 F-Score（最近一期年报/季报）≥ 6，否则 `mean_reversion_score = 0`；金融类（银行/非银）用 ROE > 5% 且不良贷款率未显著上升的替代判断。

> **项数说明**：经典 Piotroski F-Score = **9 项**二元信号（本文以 9 为准）。roadmap §3 SDD-EXT-04 登记「8 项」系笔误，已回修 roadmap 为 9。

**待展开 + 【设计待定】**：
- 【设计待定：Piotroski 9 项与 Tushare fina_indicator 字段映射】——盈利性（ROA>0 / CFO>0 / ΔROA>0 / 应计=CFO>ROA）、杠杆流动性（Δ长期负债率<0 / Δ流动比率>0 / 无增发股本）、运营效率（Δ毛利率>0 / Δ资产周转率>0）共 9 项各需哪些 fina_indicator 字段。financial_data 现存字段（据 market.py:77-84）= roe / net_profit_yoy / **revenue_yoy** / dividend_yield / total_equity / debt_to_asset / **pe_ttm** / pb（其中 revenue_yoy 非 Piotroski 输入）；需扩 ~6 字段（roa / ocfps 或 cfps / current_ratio / grossprofit_margin / assets_turn / total_share）。schema 扩展 + 采集 + 5y 回填（C-1 门控，同 total_equity 回填先例）。
- 【设计待定：金融股 / 数据不全降级】——Tushare fina_indicator 无不良贷款率（NPL）字段；金融类（银行/非银）确定替代判据数据源，或降级为「金融股仅 ROE>5%」并加降级说明。**数据不全备选**：外部评审 §7.2.2（外评:93）提供 Altman Z-Score 作 F-Score 数据缺项时的临时替代，展开时评估纳入。
- F-Score 计算 engine 纯函数（Engine 层无 IO）+ mean_reversion.py 前置门。

---

## 4. C3 — 低波动策略

> roadmap §1 + SDD §7.3：低历史波动率 + 低 Beta 标的筛选（A 股低波动异象）。占位文件 `engine/strategies/low_volatility.py`（当前未创建）。

**待展开**：因子（历史波动率分位 + Beta 分位，均越低越高分）；Beta vs 沪深300（index_history 已有）；zero 新数据源。**核心风险见 §7**（第 5 个 composite 策略）。

---

## 5. C4 — 资金动向数据层 + 策略

> roadmap §1 + SDD §7.3：主力资金净流入 + 北向资金变化。SDD §5 数据字段表 line 200-201 标「V1.5+ 可选」。**代码库当前零实现**（grep moneyflow/北向/hsgt = 0）。

**待展开 + 【设计待定】**：
- 数据层：Tushare `moneyflow`（个股资金流）/ `moneyflow_hsgt`（沪深港通资金流）/ `hsgt_top10`（北向十大成交股）adapter + 新表（models + alembic）+ repository + 采集管线接线（日级 ingest）。
- 【设计待定：北向数据 PIT 与稀疏性】——北向持仓/成交的披露频率与滞后，PIT 取数策略（参照 index_weight 月度稀疏的 range query 先例）。
- 策略 `money_flow.py`（主力净流入 + 北向变化因子）。**核心风险见 §7**（第 6 个 composite 策略）。

---

## 6. C5 — 策略插件沙箱

> roadmap §1 + SDD §15.2：L3 用户编写的策略插件在受限沙箱环境运行。SDD §15.2 安全规范：只走标准数据接口 / 禁文件网络进程 / 单股 ≤100ms 全市场 ≤5min / 内存 ≤100MB / 子进程或容器隔离 / 审计日志。

**待展开 + 【设计待定】**：
- 【设计待定：隔离机制】——受限子进程（seccomp/resource ulimit）vs 容器（docker-in-docker / gVisor）；2GB 生产内存墙下的可行性（沙箱 100MB × 并发数 vs 主栈内存）。
- 策略插件标准接口契约（SDD §15.2「策略插件化：实现标准接口无需改核心引擎」）——复用 base.py `Strategy` ABC 还是独立契约。
- L3 用户插件上传/存储/版本/管理端点（`/strategies/plugins/*`）+ 插件加载/执行/输出审计表。
- 资源限制强制（执行时限 / 内存 / 系统调用禁用）的落地技术。
- **须专项 security-review**（`/security-review` 或独立评审）——本模块是全 V1.5-C 安全面最大处，L3 用户代码执行。

---

## 7. 横切设计风险：新增策略入 Phase 11 composite

> C3（低波动）+ C4（资金动向）把 composite 策略从 4 增至 6，是本 phase 最大设计风险，独立成节。

**待展开 + 【设计待定】**：
- 【设计待定：低波动/资金动向是否 composite 正式成员】——作为第 5/6 个横截面加权策略（改 strategy_weights_history 矩阵 4→6 × 3 market_state + 逐策略 ICIR 滚动加权 + Gram-Schmidt 正交化顺序 + 冷启动 default_matrix），还是作为独立 gate/可选叠加层（不入横截面加权）。前者与 Phase 11 §4 深度耦合、需 5y 权重历史回填（同 §14-2 先例）；后者隔离但语义不同。
- 若入 composite：strategy_weights 矩阵扩维的迁移 + 6 策略横截面相关性（Phase 11 曾遇 4 策略反相关锁死顶分的教训，见 system_design §9 v2.0 重构因）+ ICIR 每策略最小样本 60 的冷启动。
- market_state 各态（UPTREND/OSCILLATION/DOWNTREND）下 6 策略权重的初始 default_matrix 定义。

---

## 8. DoD（待 §2-§7 展开后细化）

（占位：各子批 C1-C5 的验收标准 + 测试矩阵 + 冒烟 API 编号续接 + 收尾门槛，待详细设计展开后填。）
