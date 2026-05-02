# QuantPilot V1.0 整体评审报告

> **评审日期：** 2026-04-27 起（分批推进，逐批落盘）
> **评审范围：** Phase 1~10 全部交付物 — 后端引擎/服务/API/流水线/调度/通知/回测/绩效；前端 Vue 3 仪表盘；部署（Docker Prod / Nginx / 日志滚动）；测试体系；文档体系
> **评审视角：** 金融 IT 专家视角，11 维度分 3 档（金融正确性 / 系统稳健性 / 交付完整性）
> **评审深度：** 端到端抽样验算（金融正确性）+ 静态审计（系统/产品）+ 文档对照
> **评审版本：** v1.0（Batch 1+2+3+4 全部完成，终版）
> **依据文档：** SDD / system_design / 10 个 phase 设计文档；既有 phase4~phase10 代码评审报告

---

## 0. 报告导读

本报告是 V1.0 版本上线前的整体评审，与既有 phase1~10 单 phase 代码评审形成"纵深"——单 phase 评审聚焦实现细节正确性，本报告聚焦**跨 phase 的金融业务完整性、系统级风险与上线就绪度**。

报告结构：

| 节 | 内容 | 状态 |
|----|------|------|
| 1 | 执行摘要 | ✅ Batch 4 完成 |
| 2 | 整体评级与上线建议 | ✅ Batch 4 完成 |
| 3 | 阻塞性问题清单（P0/P1/P2/P3 排序）| ✅ Batch 4 完成 |
| **4** | **金融正确性专章（F-1~F-5）** | ✅ Batch 1 完成 |
| 5 | 第一档逐维度评审（F-1~F-5） | ✅ Batch 1 完成 |
| 6 | 第二档逐维度评审（S-1~S-7） | ✅ Batch 2 完成 |
| 7 | 第三档逐维度评审（D-1~D-4） | ✅ Batch 3 完成 |
| 8 | V1.5 技术债与路线图建议 | ✅ Batch 4 完成 |
| 9 | 评审方法与覆盖说明 | ✅ Batch 4 完成 |
| 附录 A | 抽样验算用例与预期值 | ✅ Batch 1 完成 |
| 附录 B | 静态审计的代码定位索引 | ✅ Batch 1 完成 |

---

## 1. 执行摘要

QuantPilot V1.0 工程纪律严格、phase 评审循环闭环、文档体系工业级完备，**核心 Pipeline / Signal / Account / 配置消费 / 通知 / 部署链路已具备生产能力**；但**回测引擎存在 4 个 P0 级金融正确性失真**（T+1 违反、quotes 切片缺失、pe_pb_history 空 DF、RiskChecker 不调用），导致回测净值与实盘可达成收益**无系统性对应关系**；同时**前端 SignalsView / Dashboard / Reports 三大核心信息流视图全部无免责**，构成合规硬阻塞。修复这两组 8 个 P0 后即可上线作为个人量化决策辅助工具。

**整体评级**：**试运行可（受限上线）** — 见 §2 上线建议。

**Top 3 风险点**：
1. **回测引擎金融失真**（§5.5 FIN-CRIT-01~04）——用户基于回测做决策可能严重高估策略期望收益
2. **核心信息视图缺免责**（§7.2 D2-GAP-01~02）——合规风险，违反"个人量化辅助工具非投顾"边界
3. **BacktestEngine 8 处静默吞异常**（§6.3 S3-CRIT-01）——直接复刻 memory 已记录的 Demo 零净值根因，违反 CLAUDE.md §6 工程纪律

---

---

## 2. 整体评级与上线建议

### 2.1 11 维度评级矩阵

| 档 | 维度 | 评级 | 关键缺口 |
|----|------|------|---------|
| 🔴 金融正确性 | F-1 未来函数 | ✅ 基本达标 | 1 处闰年 bug、1 处分位采样过疏（均 P2）|
| 🔴 金融正确性 | F-2 生存者偏差 | ⚠️ 部分缺陷 | is_st/is_suspended 强制 False（P1）+ 不过滤 delist_date（P1）|
| 🔴 金融正确性 | F-3 金融语义 | ✅ 达标 | 分红双重计算疑虑（P1）+ 策略参数硬编码（P2）|
| 🔴 金融正确性 | F-4 风险控制 | ⚠️ 部分实现 | DailyPipeline CP3 漏传 max_drawdown_pct（P1）|
| 🔴 金融正确性 | F-5 回测引擎 | ❌ 严重不达标 | **4 个 P0 硬阻塞** |
| 🟡 系统稳健性 | S-1 数据 lineage | ✅ 基本达标 | 因子级溯源（P2，V1.5）|
| 🟡 系统稳健性 | S-2 数据质量护栏 | ⚠️ 有缺口 | BacktestService 加载未走 validator（P1）|
| 🟡 系统稳健性 | S-3 静默降级 | ⚠️ 关键不达标 | **BacktestEngine 8 处违规吞异常（P1）** |
| 🟡 系统稳健性 | S-4 认证与权限 | ✅ 达标 | 多账户/限流是 V1.5 自然延伸 |
| 🟡 系统稳健性 | S-5 可观测性 | ✅ 基本达标 | Prometheus / 调度器健康端点（P2）|
| 🟡 系统稳健性 | S-6 性能 | ✅ 达标 | 多用户/熔断/大规模回测是 V1.5 |
| 🟡 系统稳健性 | S-7 测试体系 | ⚠️ 整体达标 | 回测引擎 + 生存者偏差 + 分红场景测试欠账（P1）|
| 🟢 交付完整性 | D-1 用户 UX | ⚠️ 整体达标 | **BacktestView 缺 V1.0 局限说明（P0）** |
| 🟢 交付完整性 | D-2 合规免责 | ❌ P0 阻塞 | **3 大核心视图无免责 + 措辞与真实能力不符** |
| 🟢 交付完整性 | D-3 文档一致性 | ⚠️ 整体达标 | **SDD §7.7 未注明 V1.0 局限（P0）** |
| 🟢 交付完整性 | D-4 生产就绪度 | ✅ 达标 | 公网 HTTPS 默认关（P1 警示）|

### 2.2 整体评级

**🟡 试运行可（受限上线）**

理由：
- ✅ **可立即上线**模块：DailyPipeline / SignalGenerator / AccountService / ConfigService / NotificationService / SettingsService / WatchlistService / FactorMonitorService / ReportService / 部署链路 / 认证体系 / 性能/可观测性。这些模块已经具备生产可用性。
- ❌ **必须修复后才能上线**：回测引擎（F-5 4 个 P0）+ 合规免责（D-2 3 个 P0）+ SDD 文档同步（D-3 1 个 P0）。
- ⚠️ **修复后才能开放给用户**：所有 P1（共 12 项）应在上线后 1~2 周内修复，否则会持续累积技术债与合规风险。

**评级不到 "Production-Ready" 的核心原因**：回测引擎是金融决策辅助系统的核心可信度来源，4 个 P0 失真使该模块不能作为"真实历史按相同策略所能取得收益"的代理指标。

**评级不到 "内测可"**：内测意味着面对外部用户，当前合规链路（3 大视图无免责）不允许；但 Pipeline/Signal 等核心计算路径质量已超内测水平。

### 2.3 分阶段上线建议

| 阶段 | 时间 | 范围 | 阻塞 |
|------|------|------|------|
| 阶段 1：受限试运行 | 立即（P0 修复后）| 个人单管理员、内网部署、回测功能**禁用** + 信号视图加免责 | 8 个 P0 |
| 阶段 2：完整上线 | +1~2 周 | 启用回测功能、公网 HTTPS | 12 个 P1 + 回测引擎 P0 修复完成 |
| 阶段 3：V1.5 路线 | +3~6 个月 | 多账户、Prometheus、因子级溯源、流式回测 | 见 §8 |

---

---

## 3. 阻塞性问题清单

> 按 P0（必须修复才能上线）/ P1（上线后 1 周内修复）/ P2（V1.5 解决）排序。修复成本估算单位为人天（pd），按单人开发节奏。

### 3.1 P0 — 必须修复才能上线（共 8 项）

| 编号 | 问题摘要 | 影响 | 修复成本 | 阻塞 |
|------|---------|------|---------|------|
| FIN-CRIT-01 | 回测当日 close 撮合，违反 A 股 T+1 | 回测净值/Sharpe 系统性高估 | 2 pd | 🔴 是 |
| FIN-CRIT-02 | 回测 quotes_t 仅含 close，limit_up/is_suspended/avg_amount 全失 | 涨停日成交、停牌日交易、流动性不检查 | 2 pd | 🔴 是 |
| FIN-CRIT-03 | 回测 pe_pb_history / index_adj_prices 给空 DF | ValueStrategy 失效 + Momentum.rs_6m 退化 | 1.5 pd | 🔴 是 |
| FIN-CRIT-04 | 回测不调用 RiskChecker | 集中度/行业/回撤限制全失，与实盘系统性差异 | 1 pd | 🔴 是 |
| D1-GAP-01 | BacktestView 缺 V1.0 局限说明 banner | 用户基于失真数据决策 | 0.5 pd | 🔴 是 |
| D2-GAP-01 | SignalsView/Dashboard/Reports 无免责声明 | 合规风险（量化信号被误认为投资建议） | 0.5 pd | 🔴 是 |
| D2-GAP-02 | DISCLAIMER 措辞与回测真实能力不符 | 用户基于偏弱措辞建立过度信任 | 0.2 pd | 🔴 是 |
| D3-GAP-02 | SDD §7.7 未注明 V1.0 回测 4 个局限 | 文档与实际能力脱节 | 0.3 pd | 🔴 是 |

**P0 修复总成本：约 8 pd**（其中前 4 项可并行 + 同期补 6+ 测试）

### 3.2 P1 — 上线后 1 周内修复（共 12 项）

| 编号 | 问题摘要 | 影响 | 修复成本 | 阻塞 |
|------|---------|------|---------|------|
| FIN-HIGH-05 | 回测 is_st / is_suspended 强制 False | UniverseFilter F-1/F-3 失效 | 1.5 pd | 🟡 否 |
| FIN-HIGH-06 | DailyPipeline CP3 漏传 max_drawdown_pct | 回撤告警从未触发 | 0.5 pd | 🟡 否 |
| FIN-HIGH-07 | BacktestEngine 不过滤 delist_date | 退市股仍可被回测买入 | 0.5 pd | 🟡 否 |
| FIN-HIGH-08 | record_dividend 与 adj_factor 后复权疑似双重计算 | 账户成本价/绩效计算失真 | 1 pd（含排查）| 🟡 否 |
| FIN-HIGH-09 | UniverseFilter 在回测中未传 financials_history | F-5 连续亏损检查降级 | 0.5 pd | 🟡 否 |
| S2-GAP-02 | BacktestService 加载未走 DataValidator | 异常数据污染 adj_close | 0.5 pd | 🟡 否 |
| **S3-CRIT-01** | **BacktestEngine 8 处吞异常违反 CLAUDE.md §6** | 静默降级 → "SUCCESS 但产出为 0" | **0.5 pd** | 🟡 否 |
| S7-GAP-01 | BacktestEngine 集成测试仅 2 cases | 4 个 P0 修复后无回归保护 | 1 pd（与 P0 并行）| 🟡 否 |
| S7-GAP-02 | 生存者偏差无专项测试 | FIN-HIGH-05/07 修复后无回归 | 0.5 pd | 🟡 否 |
| S7-GAP-04 | 分红场景无回归测试 | FIN-HIGH-08 修复后无回归 | 0.3 pd | 🟡 否 |
| D2-GAP-04 | LoginView 无系统性声明 | 用户进入系统前无投顾边界认知 | 0.2 pd | 🟡 否 |
| D4-GAP-04 | 公网 HTTPS 默认关闭无显眼警示 | 公网部署用户可能 JWT 明文传输 | 0.2 pd | 🟡 否 |

**P1 修复总成本：约 7 pd**

### 3.3 P2 — V1.5 解决（共 19 项）

| 编号 | 问题摘要 | 范畴 |
|------|---------|------|
| FIN-MED-10 | 闰年 2-29 日期 bug（5 年一次）| 边缘场景 |
| FIN-MED-11 | ValueStrategy PE/PB 历史分位采样过疏 | 统计有效性 |
| FIN-MED-12 | 4 策略 lookback 参数硬编码 | dataclass 真消费 |
| S1-GAP-01 | 因子级溯源缺失 | LineageService V1.5 |
| S1-GAP-02 | 缺 strategy_version 字段 | 回测对比辨析 |
| S2-GAP-01 | 数据质量监控指标未持久化 | DataQualityMetric 表 |
| S3-HIGH-02 | fetch_*_metadata 静默降级到 is_up_to_date=False | 加 logger.warning |
| S4-GAP-02 | 密码无到期/强制更换 | 个人版可接受 |
| S4-GAP-03 | 无 API rate limit | SlowAPI/fastapi-limiter |
| S5-GAP-01 | 无 Prometheus / OpenTelemetry 指标 | prometheus_client 接入 |
| S5-GAP-02 | 调度器健康端点缺失 | /health/scheduler |
| S5-GAP-03 | 日志中潜在敏感词无 SecretFilter | 中间件 |
| S6-GAP-01 | 集成测试 DB 单进程串行 | per-test schema |
| S6-GAP-02 | BacktestEngine 内存累积 | 流式写 DB |
| S6-GAP-03 | Tushare 限流无熔断退避 | tenacity 接入 |
| S7-GAP-03 | 集成测试无故障注入 | 模拟 Tushare 503 / CP1 失败 |
| D1-GAP-02 | SignalCard 不展示评分决策路径 | 与 S1-GAP-01 同源 |
| D1-GAP-03 | 无错误重试 / 网络中断兜底 | toast + retry |
| D3-GAP-01 / D3-GAP-03 | SDD 与策略源码不一致 / 单进程约束未强调 | 文档对齐 |

**P2 修复总成本：约 25-30 pd**（V1.5 阶段分模块推进）

### 3.4 P3 — 长期改进（4 类共 6 项）

| 编号 | 问题 |
|------|------|
| S4-GAP-01 | 单管理员模型 = 无权限粒度（V1.0 设计范围内） |
| D1-GAP-04 | 多视图空状态文案差异化 |
| D2-GAP-03 | Tushare 数据使用合规声明 |
| D4-GAP-01/02/05 | 多副本部署 / K8s / 备份 SHA256 |

---

---

## 4. 金融正确性专章 ★

本章是金融 IT 系统区别于普通 Web 应用的核心评审项，独立加重。覆盖：未来函数 / 生存者偏差 / 金融语义 / 风险控制 / 回测可信度。本章是摘要 + 关键发现，详细审计明细在第 5 章。

### 4.1 一句话结论

| 维度 | 结论 |
|------|------|
| F-1 未来函数 | **基本达标**（核心 PIT 设计正确：FinancialData.publish_date / `_get_financials_at` / `adj_prices.loc[:td]` / UniverseFilter F-2 用交易日）。存在 1 处闰年日期 bug 与 1 处历史分位时点过疏 |
| F-2 生存者偏差 | **部分缺陷**：BacktestEngine 含历史已退市股（用 stock_info 全表）— 这是好的；但 `is_st`/`is_suspended` 强制 False、未过滤 `delist_date`、`get_active_stock_codes()` 用 is_active=True，**回测中"曾经的 ST/停牌历史"完全丢失**，等同于"历史 ST/停牌股仍可被买入" |
| F-3 金融语义 | **达标**：WAC / NAV / 三态权重归一化 / 市场状态系数 / MACD/RSI/BB/ADX 标准参数全部符合金融教科书与 SDD 附录 B。**1 处疑似 bug**：分红与 adj_factor 后复权可能双重计算 |
| F-4 风险控制 | **部分实现，关键缺口**：实盘 RiskChecker 链路完整（集中度/行业/回撤），但 `account_max_drawdown_pct` **DailyPipeline CP3 从未传入**，回撤告警从设计上就不会触发 |
| F-5 回测可信度 | **❌ 严重不达标**：4 个 P0 级问题构成回测引擎的整体失真——结果**不能用于真实决策参考** |

### 4.2 P0 级问题速览（详见 §3、§5.5）

| 编号 | 描述 | 文件位置 |
|------|------|---------|
| **FIN-CRIT-01** | 回测当日 close 成交，违反 A 股 T+1（应 T 日盘后产生信号、T+1 早盘以开盘价 / VWAP 成交） | `engine/backtest/engine.py:430-490` `_execute_signals` |
| **FIN-CRIT-02** | 回测 `quotes_t` 只含 close，**limit_up / is_suspended / avg_amount 全部丢失** → 涨停日也"成交"、停牌日也"交易"、流动性合格性不被检查 | `engine/backtest/engine.py:364-385` `_get_quotes_at` |
| **FIN-CRIT-03** | 回测 `pe_pb_history` / `index_adj_prices` 直接给空 DataFrame → **ValueStrategy 完全不工作**（PE/PB 历史分位返回 NaN 全部得分一致），**MomentumStrategy.rs_6m 退化为绝对收益**（指数对比基准为 0） | `engine/backtest/engine.py:205-206` |
| **FIN-CRIT-04** | 回测**不调用 RiskChecker**（注释"D8-P3-08：回测无实盘账户上下文"），回测信号集中度/行业/回撤检查全部缺失，与实盘有系统性差异 | `engine/backtest/engine.py:253-254` |

后果：用户在前端 BacktestView 看到的回测净值曲线、Sharpe、最大回撤——**与真实历史按相同策略所能取得的收益无系统性对应关系**。这是 V1.0 回测功能不能上线作为决策辅助的硬阻塞。

### 4.3 P1 级问题速览（详见 §5）

| 编号 | 描述 | 文件位置 |
|------|------|---------|
| FIN-HIGH-05 | `is_st`/`is_suspended` 强制 False，UniverseFilter F-1（非 ST）/ F-3（非停牌）在回测中失效 | `services/backtest_service.py:209-211` |
| FIN-HIGH-06 | DailyPipeline CP3 调 RiskChecker 不传 `account_max_drawdown_pct`，**回撤告警从未触发** | `services/signal_service.py:401-406` |
| FIN-HIGH-07 | BacktestEngine `_get_stock_info_at` 不过滤 `delist_date`，已退市股仍纳入 universe | `engine/backtest/engine.py:330-340` |
| FIN-HIGH-08 | 分红与 adj_factor 后复权疑似双重计算（`record_dividend` 减 cost_price + 后复权 adj_factor 已含分红） | `services/account_service.py:376` |
| FIN-HIGH-09 | UniverseFilter 在回测中调用未传 `financials_history`，F-5 连续亏损检查降级为单期 | `engine/backtest/engine.py:179-184` |

### 4.4 P2 级问题速览

| 编号 | 描述 | 文件位置 |
|------|------|---------|
| FIN-MED-10 | 闰年 2-29 的"5 年前同月同日" `date(yr-5, 2, 29)` 抛 ValueError | `services/strategy_service.py:208` |
| FIN-MED-11 | ValueStrategy 历史分位用 publish_date 索引（一年 4-5 个公告点），样本量小，统计有效性弱 | `engine/strategies/value.py:127-159` |
| FIN-MED-12 | 4 个策略的 lookback 参数（rolling 5/10/20/60、RSI 14、BB 20/2）硬编码，dataclass 仅作 Pipeline 快照登记（已在源码注释降级） | `engine/strategies/*.py` |

### 4.5 已验证 OK 的关键实现

| 编号 | 维度 | 实现 |
|------|------|------|
| FIN-OK-01 | F-1 | `repository.get_latest_financial` DISTINCT ON + `publish_date <= as_of_date` — PIT 严格 |
| FIN-OK-02 | F-1 | BacktestEngine `_get_financials_at` 按 publish_date 过滤 + groupby ts_code 取最新一期 |
| FIN-OK-03 | F-1 | BacktestEngine `adj_prices.loc[:td_ts]` 截至当日历史，正确 |
| FIN-OK-04 | F-1 | UniverseFilter F-2 用 `calendar.get_prev_trade_date(today, 60)` — 60 个**交易日**前，非简单 timedelta(60) |
| FIN-OK-05 | F-1 | `repository.get_avg_amount` WHERE `trade_date < trade_date` 严格小于当日 |
| FIN-OK-06 | F-3 | `compute_wac(old_shares, old_cost, new_shares, new_price, commission)` WAC 公式数学正确 |
| FIN-OK-07 | F-3 | NAV = (cash + position_mv) / initial_capital 公式正确 |
| FIN-OK-08 | F-3 | Scorer 缺失策略权重按比例重新归一化（`raw_total = sum(active); norm = w / raw_total`）数学正确 |
| FIN-OK-09 | F-3 | PositionSizer 三态系数（UPTREND=1.0 / OSCILLATION=0.75 / DOWNTREND=0.5）与 SDD §10.1 一致 |
| FIN-OK-10 | F-3 | 技术指标参数全部符合教科书：MA 5/10/20/60；MACD 12/26/9；RSI 14；BB 20/2σ；ADX 14 |
| FIN-OK-11 | F-3 | MarketStateEngine 暖启动期丢弃（`valid_mask = MA短.notna() & MA长.notna() & ADX.notna()`） |
| FIN-OK-12 | F-3 | MarketStateEngine 防抖动 N 日同 raw state 才确认，避免 ADX/MA 跳变频繁切换 |
| FIN-OK-13 | F-4 | RiskChecker 集中度+行业+回撤三层覆盖；`suggested_pct=None` 时跳过集中度（避免误报 BLOCK 拒绝不可执行信号） |
| FIN-OK-14 | F-4 | SignalService.save() 自动按 BLOCK 过滤 BUY 信号、WARN 追加到 reason，不持久化被阻断信号 |
| FIN-OK-15 | F-5 | 成本模型：`_buy_cost_per_unit = price × (1 + commission + slippage)`、`_sell_proceeds_per_unit = price × (1 − commission − stamp_tax − slippage)` 与 SDD §10.5 一致 |

---

## 5. 第一档：金融正确性逐维度评审（详）

> _与 §4 同步推进，§4 为提炼摘要 + 关键发现，本节为完整审计明细。_

### 5.1 F-1 未来函数 — 详细审计

#### 审计范围
- 实盘流水线（DailyPipeline）所有数据访问点是否使用 `as_of_date` / `trade_date` 限定，避免引用未来数据
- 回测引擎数据切片（`_get_financials_at` / `_get_quotes_at` / `adj_prices.loc[:td]`）的时点严格性
- UniverseFilter 过滤条件（F-2 上市满 60 日、F-7 流动性、F-5 财务）的"过去"数据访问
- 策略层因子计算窗口的截至日（adj_prices truncation / pe_pb_history slicing）

#### 审计方法
对照源码逐行核查每个数据访问点的 SQL/DataFrame 切片，验证：
- WHERE 条件是否含 `<= as_of_date` 或 `< as_of_date`（成交数据用严格小于以排除当日）
- DataFrame `.loc[:td]` 是否使用 inclusive bound（取决于业务语义）
- 时间窗口是否换算为交易日（calendar.get_prev_trade_date），还是简单 timedelta（错误）

#### 关键 OK 实现（已验证）

**FIN-OK-01: 财报 PIT 严格** — `repository.get_latest_financial`
```sql
SELECT DISTINCT ON (ts_code) ... FROM financial_data
WHERE ts_code = ANY(...) AND publish_date <= :as_of_date
ORDER BY ts_code, publish_date DESC, report_period DESC
```
- 用 `publish_date`（公告日）而非 `report_period`（报告期）做截至日，避免使用尚未发布的报告
- DISTINCT ON + ORDER BY DESC 取最新一期
- 评级：✅ 严格符合 PIT 原则

**FIN-OK-02: BacktestEngine `_get_financials_at`** — `engine/backtest/engine.py`
- 按 `publish_date <= td` 过滤后 groupby `ts_code` 取最新
- 与实盘 PIT 语义一致

**FIN-OK-03: BacktestEngine `adj_prices.loc[:td_ts]`** — `engine/backtest/engine.py`
- DatetimeIndex inclusive bound 至当日 td_ts，截取历史价格
- 与策略层"截至当日"语义一致（评分发生在当日盘后，可使用当日 close）

**FIN-OK-04: UniverseFilter F-2 用交易日** — `engine/universe.py:66-71`
```python
prev_60 = calendar.get_prev_trade_date(today, 60)
mask = stock_info["list_date"] <= prev_60
```
- 60 个**交易日**前，非 `today - timedelta(60)`（含周末/节假日会少计算约 18 天）
- 评级：✅ 与 SDD §6.3 一致

**FIN-OK-05: 流动性过滤严格小于当日** — `repository.get_avg_amount`
```sql
WHERE trade_date < :as_of_date AND trade_date >= :as_of_date - INTERVAL ':window days'
```
- 当日 amount 不参与"近 20 日均成交额"计算，避免当日流动性"反向预测"
- 评级：✅ 严格

#### P2 级问题

**FIN-MED-10: 闰年 2-29 日期 bug** — `services/strategy_service.py:208`
```python
start_pepb = date(trade_date.year - _PE_PB_HISTORY_YEARS, trade_date.month, trade_date.day)
```
- 当 `trade_date = 2024-02-29`，构造 `date(2019, 2, 29)` 抛 `ValueError: day is out of range for month`
- 影响：5 年一次（2024、2028…）当日实盘评分流水线异常，触发 except 路径返回空结果
- 修复建议：
  ```python
  try:
      start_pepb = date(trade_date.year - _PE_PB_HISTORY_YEARS, trade_date.month, trade_date.day)
  except ValueError:
      start_pepb = date(trade_date.year - _PE_PB_HISTORY_YEARS, trade_date.month, trade_date.day - 1)
  ```
  或直接用 `trade_date - timedelta(days=365 * _PE_PB_HISTORY_YEARS)`
- 优先级：P2（可上线，V1.5 修复）

**FIN-MED-11: ValueStrategy 历史分位采样过疏** — `engine/strategies/value.py:127-159`
- `_compute_historical_percentile` 使用 `pe_pb_history` 的 publish_date 索引（一年 4-5 个公告点 × 5 年 ≈ 20-25 个样本点）
- 计算分位数时样本量小，统计有效性弱（尤其市场剧烈波动期分位结果不稳定）
- 修复建议：改用 daily_basic 的日度 PE/PB（每年 ~250 点，5 年 ~1250 点），或保留 publish_date 但增加分位置信区间提示
- 优先级：P2（V1.5 改进）

#### F-1 维度评级
**基本达标**。核心 PIT 设计正确（publish_date 过滤 / DISTINCT ON / 交易日换算 / `<` 严格当日小于）。存在 1 处闰年 bug（5 年一次概率）和 1 处分位采样过疏（统计有效性弱，但不导致未来函数）。无 P0/P1 阻塞。

---

### 5.2 F-2 生存者偏差 — 详细审计

#### 审计范围
- 实盘评分宇宙（`get_active_stock_codes()`）
- 回测引擎股票宇宙（`_get_stock_info_at` / BacktestService 加载）
- 历史已退市股是否纳入回测以避免"幸存者偏差"
- ST/停牌历史是否在回测中保留（避免"历史 ST 股仍被买入"）
- IndexComponent 时点成分股（如有）

#### 审计方法
1. 静态审计：BacktestService `_load_data_bundle`、BacktestEngine `_get_stock_info_at`
2. 数据库 schema 核查：StockInfo 是否含 `delist_date`、是否能区分历史 ST 时段

#### 关键发现

**部分缺陷（混合表现）**：
- ✅ BacktestEngine **包含历史已退市股**：BacktestService `_load_data_bundle` 用 `select(StockInfo)` 全表（无 is_active 过滤），加上 daily_quote 中存在但 stock_info 缺失的 ts_code 也补入 universe（line 218-225）—— 这是**正确的反生存者偏差设计**
- ❌ 但**历史 ST/停牌信息完全丢失**：`is_st` / `is_suspended` 在 BacktestService 强制 False（详见 P1 章节）
- ❌ `delist_date` 未过滤：已退市股可被回测引擎在退市后日期"买入"（详见 P1 章节）

#### P1 级问题

**FIN-HIGH-05: is_st / is_suspended 在回测中强制 False** — `services/backtest_service.py:209-211`
```python
si_map: dict[str, dict] = {
    r.ts_code: {
        "list_date": r.list_date,
        "is_st": False,           # ❌ 硬编码
        "sw_industry_l1": r.sw_industry_l1,
        "is_suspended": False,    # ❌ 硬编码
    }
    for r in stock_rows
}
```
- 注释 "【降级说明】V1.0：is_st/is_suspended 存于 DailyQuote，StockInfo 无此列；暂以 False 填充"
- 后果：回测中 UniverseFilter F-1（非 ST）和 F-3（非停牌）**完全失效**，等同于"历史 ST 股仍可被纳入候选池并买入"——是典型的回测前瞻性偏差/生存者偏差混合
- 修复方案：BacktestService 加载 daily_quote 时按 (ts_code, trade_date) 索引保留 is_st/is_suspended 列，BacktestEngine `_get_stock_info_at` 用 (ts_code, td) 时点查找填充
- 优先级：P1（上线后 1 周内修复，回测可信度核心）

**FIN-HIGH-07: BacktestEngine `_get_stock_info_at` 不过滤 delist_date** — `engine/backtest/engine.py:330-340`
```python
def _get_stock_info_at(self, td: date) -> pd.DataFrame:
    info = self._stock_info.copy()
    if "list_date" in info.columns:
        info = info[info["list_date"].fillna(_DEFAULT) <= td]
    return info
    # ❌ 未过滤 delist_date <= td 的退市股
```
- 后果：2020 年退市的股票在 2023 年回测日期仍出现在 universe 中，可能被买入（虽然 adj_prices 切片会有 NaN 价导致下单失败，但仍占用 ranking 名额）
- 修复方案：
  ```python
  if "delist_date" in info.columns:
      info = info[info["delist_date"].isna() | (info["delist_date"] > td)]
  ```
- 前置条件：StockInfo 需有 `delist_date` 字段（V1.0 schema 检查中）
- 优先级：P1

#### F-2 维度评级
**部分缺陷**。整体宇宙设计正确（不限于今日 active 股），但 ST/停牌历史完全丢失（P1）+ 退市日未过滤（P1）。未达"严格反生存者偏差"，回测结果系统性偏向"被选股票池"。

---

### 5.3 F-3 金融语义 — 详细审计与抽样验算

#### 审计范围
- WAC（加权平均成本）公式
- NAV（账户净值）公式
- 三态权重归一化（缺失策略时按比例重分配）
- 市场状态系数（UPTREND/OSCILLATION/DOWNTREND）
- 技术指标参数（MA / MACD / RSI / BB / ADX）
- MarketStateEngine 暖启动期处理与防抖动
- 分红与后复权 adj_factor 是否双重计算

#### 抽样验算

**用例 1（WAC）**：
- 持仓：100 股 @ 10.00 = 1000 元
- 加仓：100 股 @ 12.00，commission = 5 元
- 期望 WAC = (100×10 + 100×12 + 5) / 200 = 11.025
- 源码 `compute_wac(100, 10.00, 100, 12.00, 5)`：
  ```python
  total_cost = 100*10 + 100*12 + 5 = 2205
  total_shares = 200
  wac = 2205 / 200 = 11.025 ✅
  ```

**用例 2（Scorer 三态权重归一化）**：
- 配置：UPTREND 权重 = {trend:0.4, momentum:0.3, reversion:0.2, value:0.1}
- 假设 ValueStrategy 缺失（raw_total = 0.4+0.3+0.2 = 0.9）
- 期望归一化权重 = {trend: 0.4/0.9, momentum: 0.3/0.9, reversion: 0.2/0.9} ≈ {0.444, 0.333, 0.222}（合计 = 1.0）
- 源码 `engine/scorer.py:76-78`：
  ```python
  raw_total = sum(active_weights.values())
  normalized = {k: v / raw_total for k, v in active_weights.items()}  ✅
  ```

**用例 3（NAV 公式）**：
- 现金 cash = 50000，持仓市值 position_mv = 60000，初始资本 100000
- 期望 NAV = (50000 + 60000) / 100000 = 1.10
- 源码 `account_service`：`nav = (cash + position_mv) / initial_capital` ✅

#### 关键 OK 实现

**FIN-OK-06**: `compute_wac` 公式数学正确（含手续费摊入成本）
**FIN-OK-07**: NAV = (cash + position_mv) / initial_capital
**FIN-OK-08**: Scorer 缺失策略权重按比例重新归一化
**FIN-OK-09**: PositionSizer 三态系数（UPTREND=1.0 / OSCILLATION=0.75 / DOWNTREND=0.5），与 SDD §10.1 一致
**FIN-OK-10**: 技术指标参数全部符合教科书：
- MA: 5/10/20/60
- MACD: fast=12 / slow=26 / signal=9
- RSI: window=14
- BB: window=20 / nbdev=2σ
- ADX: window=14

**FIN-OK-11**: MarketStateEngine 暖启动期丢弃
```python
valid_mask = MA短.notna() & MA长.notna() & ADX.notna()
```
**FIN-OK-12**: MarketStateEngine 防抖动 — 连续 N 日同 raw state 才确认（避免 ADX/MA 跳变频繁切换）

#### P1 级问题

**FIN-HIGH-08: 分红与 adj_factor 后复权疑似双重计算** — `services/account_service.py:376`
```python
def record_dividend(self, ...):
    ...
    position.cost_price -= amount / position.shares   # ❌ 减除每股分红金额
```
- 同时回测引擎 `adj_prices = close × adj_factor`（后复权 adj_factor 已含分红的累计调整）
- 后果：实盘账户的成本价被分红减除，但回测的"模拟持仓成本"通过 adj_close 已隐含分红调整 → 当回测对比"实盘已分红股票" 时，**收益可能被双重计入分红**
- 排查方向：
  1. 实盘 cost_price 是否参与回测净值计算？（若不参与，仅是账户层 cost_price 显示问题，影响有限）
  2. 后复权 adj_factor 公式是否含 cash dividend 调整？（Tushare adj_factor 默认含分红与拆分）
- 修复建议：
  - 短期：在 `record_dividend` 注释中写明"此处 cost_price 仅用于账户层成本展示，不参与回测/绩效计算"
  - 长期：评估是否需要前复权与后复权的 cost_price 双轨记录
- 优先级：P1（业务可信度，1 周内核查清楚）

#### P2 级问题

**FIN-MED-12: 策略 lookback 参数硬编码** — `engine/strategies/*.py`
- TrendStrategy: rolling 5/10/20/60 写死
- MomentumStrategy: 60/120 写死
- ReversionStrategy: RSI 14、BB 20/2 写死
- ValueStrategy: history_years 5 写死
- dataclass `*Config` 仅作 Pipeline 快照登记（在源码注释 "降级：参数实际不生效"）
- 影响：用户在 Settings 改窗口期不会真实生效
- 修复优先级：P2（V1.5 真实参数下沉到 Strategy._compute）

#### F-3 维度评级
**达标**。WAC / NAV / 权重归一化 / 三态系数 / 标准技术指标参数 / 暖启动期 / 防抖动全部符合金融教科书与 SDD §10。1 处疑似分红双重计算（P1，需进一步排查）。1 处策略参数硬编码（P2，UI 误导）。

---

### 5.4 F-4 风险控制 — 详细审计与端到端验证

#### 审计范围
- RiskChecker 三层覆盖（集中度 / 行业 / 回撤）
- DailyPipeline CP3 风险检查链路
- BLOCK 信号是否被持久化（应过滤）
- WARN 信号是否在 reason 字段标注
- 回测引擎是否调用 RiskChecker

#### 端到端审计路径

**入口**：`SignalService.generate_for_date` → `_run_risk_checks` → `RiskChecker.check`
- ✅ Step 1：取持仓总市值、行业市值、当日 NAV
- ✅ Step 2：构造 RiskCheckParams（pos_total_pct / industry_pct / drawdown_pct）
- ❌ Step 3：调用 `checker.check(...)` **未传 `account_max_drawdown_pct`**

#### 关键 OK 实现

**FIN-OK-13**: RiskChecker 集中度+行业+回撤三层覆盖
- `position_max_pct=0.15` → BLOCK
- `industry_max_pct=0.30` → BLOCK
- `account_max_drawdown_pct=0.20` → WARN
- 当 `suggested_pct=None`（HOLD/SELL 信号）时跳过集中度检查（避免误报 BLOCK）

**FIN-OK-14**: SignalService.save() 自动按 BLOCK 过滤 BUY 信号、WARN 追加到 reason，不持久化被阻断信号

#### P1 级问题

**FIN-HIGH-06: DailyPipeline CP3 调 RiskChecker 不传 account_max_drawdown_pct** — `services/signal_service.py:401-406`
```python
result = checker.check(
    suggested_pct=signal.suggested_position_pct,
    industry=industry,
    pos_total_pct=...,
    industry_pct=...,
    drawdown_pct=current_drawdown,
    # ❌ 漏传 account_max_drawdown_pct
)
```
- 后果：`RiskChecker.check` 内部用默认值（None / 0），**回撤告警从未触发**
- 验证方式：搜索 logger 关键字 "drawdown_warn" 在生产日志中是否出现
- 修复建议：从 SettingsService 读取 `risk.max_drawdown_pct`（默认 0.20）传入
- 优先级：P1（核心风控告警从设计上就不会触发，必须修）

#### F-4 维度评级
**部分实现，关键缺口**。RiskChecker 设计良好（FIN-OK-13/14），但 DailyPipeline CP3 漏传 max_drawdown_pct 参数，导致回撤告警链路设计上就不工作。回测中完全不调用 RiskChecker（详见 §5.5）。

---

### 5.5 F-5 回测引擎 — 详细审计与公式核对

#### 审计范围
- 成交规则（T+1 / 当日 close 撮合 / 涨跌停约束）
- quotes 切片完整性（close / open / limit_up / is_suspended / avg_amount）
- 数据切片完整性（pe_pb_history / index_adj_prices）
- 风险检查链路（是否调用 RiskChecker）
- 成本模型公式（佣金 / 印花税 / 滑点）
- 净值与绩效计算

#### P0 级严重问题

##### FIN-CRIT-01: 回测违反 A 股 T+1 撮合规则

**位置**：`engine/backtest/engine.py:430-490` `_execute_signals`

**当前实现**：
```python
def _execute_signals(self, td: date, signals, quotes_t, account):
    for sig in signals:
        if sig.action == "BUY":
            price = quotes_t.loc[sig.ts_code, "close"]   # ❌ 当日 close 成交
            shares = math.floor(...)
            account.buy(sig.ts_code, shares, price, ...)
```

**应有规则（A 股 T+1）**：
- T 日盘后产生信号（基于 T 日 close 评分）
- T+1 日早盘以**开盘价 / VWAP** 成交
- 当日买入不可当日卖出

**影响**：
- 回测在信号产生当日的 close 价位"完美成交"，**完全消除了开盘价跳空风险**
- 实盘中 T+1 早盘开盘价相对 T 日 close 平均偏差 ±0.5%，长期累积 → 回测净值显著高于实盘可达成结果
- 这是**回测可信度的核心硬阻塞**

**修复方案**：
1. 改 `_execute_signals` 在下一个交易日（td+1）执行：用 `_get_quotes_at(next_td)` 取下一日 open 价
2. 引入 `BacktestConfig.execution_price`（"open" / "vwap" / "close"）参数，默认 "open"
3. 更新 BacktestEngine.run 主循环：信号产生与撮合分离

**优先级**：P0（**回测功能不能上线作为决策辅助的硬阻塞**）

##### FIN-CRIT-02: quotes_t 只含 close — 涨跌停 / 停牌 / 流动性约束完全失效

**位置**：`engine/backtest/engine.py:364-385` `_get_quotes_at`

**当前实现**：
```python
def _get_quotes_at(self, td: date) -> pd.DataFrame:
    if td_ts not in self._adj_prices.index:
        return pd.DataFrame(columns=["close"])
    row = self._adj_prices.loc[td_ts]
    return pd.DataFrame({"close": row}).dropna()
    # ❌ limit_up / is_suspended / avg_amount 全部丢失
```

**影响**：
- **涨停日仍"成交"**：实盘涨停时无法买入，回测中可顺利买入
- **停牌日仍"交易"**：停牌日无法成交，回测中可顺利下单
- **流动性合格性不被检查**：UniverseFilter F-7 在回测中调 `_get_quotes_at` 取不到 amount/avg_amount 列 → 过滤条件等价跳过
- **量化失真极大**：尤其在高景气行业涨停带板期间，回测会严重高估"理想入场价"

**修复方案**：
1. BacktestService `_load_data_bundle` 新增加载 daily_quote 所有相关列（open / high / low / close / amount / vol / limit_up / limit_down / is_st / is_suspended / adj_factor）
2. BacktestEngine 新增 `_quotes_full: pd.DataFrame`（MultiIndex (trade_date, ts_code)）保存全量 quotes
3. `_get_quotes_at(td)` 返回该日全量字段切片
4. `_execute_signals` 撮合前检查：`limit_up / is_suspended` 拒绝下单

**优先级**：P0

##### FIN-CRIT-03: pe_pb_history / index_adj_prices 直接给空 DataFrame

**位置**：`engine/backtest/engine.py:205-206`

**当前实现**：
```python
market_data: MarketSnapshot = {
    "trade_date": td,
    "adj_prices": adj_prices_slice,
    "daily_quotes": quotes_t,
    "financials": financials_slice,
    "pe_pb_history": pd.DataFrame(),       # ❌ 空 DF
    "index_adj_prices": pd.DataFrame(),    # ❌ 空 DF
}
```

**影响**：
- **ValueStrategy 完全不工作**：
  - PE/PB 历史分位计算需 `pe_pb_history`，传入空 DF → `_compute_historical_percentile` 返回 NaN
  - 全 NaN 因子被 ScoringService 排除，ValueStrategy 在回测中**完全无评分输出**
  - 回测中 Scorer 自动按比例归一化剩余三策略权重 → 与实盘三策略+ValueStrategy 评分**结构性不同**
- **MomentumStrategy.rs_6m 退化为绝对收益**：
  - 6 个月相对强度 = 个股 6M 收益 - 指数 6M 收益
  - 指数空 DF → 指数收益 = 0 → rs_6m = 个股绝对收益
  - 牛市中所有股 rs_6m 全部 > 0（与基准对比意义丢失）
- 回测净值/Sharpe 等指标与实盘不可比

**修复方案**：
1. BacktestService `_load_data_bundle` 新增加载：
   - PE/PB 历史：`select(StockDailyBasic).where(trade_date >= start, trade_date <= end)`
   - 指数历史：已加载 hs300_history（line 256-273），但未传给 BacktestEngine，需加入 BacktestDataBundle
2. BacktestEngine 主循环每日构造 MarketSnapshot 时正确切片传入

**优先级**：P0

##### FIN-CRIT-04: 回测不调用 RiskChecker

**位置**：`engine/backtest/engine.py:253-254`

**当前实现**：
```python
# 评论："D8-P3-08：回测无实盘账户上下文，RiskChecker 不执行"
risk_params = None  # ❌ 完全跳过风控
```

**影响**：
- 回测中信号集中度限制（15% per stock）不执行
- 行业暴露限制（30% per industry）不执行
- 回撤限制（20% account drawdown）不执行
- 与实盘存在系统性差异：实盘有风控阻断的信号，回测中正常成交 → 回测净值高估

**修复方案**：
- 回测引擎构造**模拟账户上下文**（已有 `account` 对象）
- 用账户当前持仓 + 行业暴露计算 `pos_total_pct` / `industry_pct`
- 用 `account.daily_nav_history` 计算 `drawdown_pct`
- 调 RiskChecker.check 与实盘相同链路

**优先级**：P0

#### P1 级问题（已在 §5.2 列）

- FIN-HIGH-05: is_st / is_suspended 强制 False
- FIN-HIGH-07: 不过滤 delist_date

#### P1 补充

**FIN-HIGH-09: UniverseFilter 在回测中未传 financials_history** — `engine/backtest/engine.py:179-184`
- 回测调 `universe_filter.filter(...)` 时省略 `financials_history` 参数
- F-5 连续两期亏损检查降级为单期检查
- 修复：BacktestEngine 维护 `_financials_history_at(td)` 切片函数，构造两期窗口传入

#### 关键 OK 实现

**FIN-OK-15**: 成本模型符合 SDD §10.5
```python
_buy_cost_per_unit = price × (1 + commission_rate + slippage_rate)
_sell_proceeds_per_unit = price × (1 − commission_rate − stamp_tax_rate − slippage_rate)
```
- 与 `account_config = {commission: 0.0003, stamp_tax: 0.001, slippage: 0.0002}` 配合一致

#### F-5 维度评级
**❌ 严重不达标**。4 个 P0 问题构成回测引擎的整体失真——T+1 违反 / quotes 信息丢失 / pe_pb 与指数空 DF / RiskChecker 不调用 → 回测净值曲线、Sharpe、最大回撤**与真实历史按相同策略所能取得的收益无系统性对应关系**。**这是 V1.0 回测功能不能上线作为决策辅助的硬阻塞**。

---

## 6. 第二档：系统稳健性逐维度评审

### 6.1 S-1 数据 lineage 与可审计性

#### 审计范围
- 信号 → 评分 → 因子 → 数据快照 的链路完整性
- PipelineRun.config_snapshot（Phase 10 §4.3）的写入与消费
- BacktestTask.config_snapshot（Phase 10 §4.4）的写入与回测可复现性
- LineageService 实现深度（V1.0 vs V1.5 计划）

#### 关键 OK 实现

**FIN-OK-S1-01**: PipelineRun 4 个时间戳齐全
- `cp1_at` / `cp2_at` / `cp3_at` / `data_snapshot_version` 在每个 CP 完成时写入（独立 session + commit），见 `pipeline/daily_pipeline.py`
- 任何检查点失败均落盘，前端 `/pipeline/status` 可见
- 评级：✅ 满足 SDD §15.6 V1.0 数据血缘最小定义

**FIN-OK-S1-02**: SignalScoreSnapshot 表绑定信号与评分
- 每条 BUY/HOLD/SELL 信号生成时写入 `SignalScoreSnapshot(signal_id, composite_score, market_state, score_breakdown)`
- LineageService 用 signal_id 反查 → 返回 `score_snapshot + pipeline_run`
- 评级：✅ 信号→评分链路可追溯

**FIN-OK-S1-03**: PipelineRun.config_snapshot 写入
- `daily_pipeline.py:_write_config_snapshot`（line 67-69）首次运行时写入完整配置快照
- 断点续传不覆盖（保证回放 / 审计场景一致性）
- BacktestTask 同理（`task.config_snapshot` 由端点层写入，后台任务即时构造 engine）

#### 缺口

**S1-GAP-01: 因子级溯源缺失**（V1.5 计划）
- LineageService 仅返回信号 → composite_score → score_breakdown（4 个策略加权前的 strategy_score）
- 不返回因子层细节：MA60、MACD hist、PE 分位等具体值
- 用户问 "为什么 600519 是 BUY？" 时无法答到 "因为其 MA60 趋势分 = 0.85，处于历史 80% 分位"
- 前端 SignalLineageView 仅展示空数据骨架（V1.0 设计预留）
- 优先级：P2（V1.5 完整因子级溯源，与 SDD §15.6 路线图一致）

**S1-GAP-02: BacktestTask 与 PipelineRun 缺乏 strategy_version 字段**
- 当用户调整 4 个策略的窗口期或权重后再回测，无法区分"不同配置参数下"的相同策略版本
- 当前依赖 `config_snapshot` 的字典内容做 diff，但前端不展示
- 影响：回测结果对比时分辨力弱
- 优先级：P2

#### S-1 维度评级
**基本达标**（V1.0 范围内）。信号→评分→流水线时间戳→快照链路完整。因子级溯源是 V1.5 范畴的明确推迟。前端 SignalLineageView 已预留视图位，待数据接入即可。

---

### 6.2 S-2 数据质量护栏

#### 审计范围
- DataValidator 五大校验规则（SDD §5.5）
- 行情完整性 / 价格有效性 / 复权连续性 / 财报 PIT / 时效性
- 校验失败时的降级路径（行级过滤 vs 整批阻断）

#### 关键 OK 实现

**FIN-OK-S2-01**: DataValidator 五大校验规则全部实现 — `data/validators.py`
- `validate_daily_quotes`: 完整性（≥ prev_count × 0.95）、价格有效性（low ≤ open/close ≤ high）、成交量非负
- `validate_adj_factor_series`: 相邻日 adj_factor 变化率 ≤ 20%
- `validate_financial_data`: PIT 违规过滤（publish_date > as_of_date 行级跳过 + ERROR 记录），日期反序异常（publish_date < report_period）
- `validate_trade_date`: 时效性
- 评级：✅ 全部覆盖 SDD §5.5

**FIN-OK-S2-02**: 数据采集层降级路径清晰
- `data_service.py` `fetch_*` 系列异常时记 `logger.exception`、写入 `errors[]` 返回字典而非抛错
- 调用方（DailyPipeline CP1）按字段判断成功/失败，不会因单一数据源失败导致整个 CP1 中断
- 评级：✅ 隔离设计良好

#### 缺口

**S2-GAP-01: 数据质量监控指标缺失**
- DataValidator 错误数量未持久化为指标，无法看到"过去 30 天每天 PIT 违规数"
- 影响：长期数据漂移（如某只股票财报 publish_date 提前）无法被监控告警
- 优先级：P2（V1.5 增加 DataQualityMetric 表 + 因子监控接入）

**S2-GAP-02: BacktestService 数据加载无 DataValidator 检查**
- `_load_data_bundle` 直接将 daily_quote 转 wide pivot，未调用 `validate_daily_quotes`
- 后果：测试数据中 close 异常值（如 0 或负）会直接污染 adj_close → 净值跳变
- 优先级：P1（应在加载阶段先 validate 再 pivot）

#### S-2 维度评级
**达标但有可观测性缺口**。校验规则齐全，降级路径清晰。但缺数据质量指标持久化（P2）+ 回测加载未走 validator（P1）。

---

### 6.3 S-3 静默降级审计

#### 审计范围
- 全代码 `except Exception` 共 54 处分布
- 每处的日志级别（exception / warning / debug / pass）
- 是否有【降级说明】注释
- 业务上是否会"看起来 SUCCESS 但产出为 0"

#### 全局统计

| 文件 | 数量 | 风险等级 |
|------|------|---------|
| `engine/backtest/engine.py` | 9 | 🔴 高（详见下） |
| `services/data_service.py` | 10 | 🟢 低（全部 logger.exception） |
| `pipeline/daily_pipeline.py` | 8 | 🟡 中（best-effort 模式合理，但 step4~6 用 warning + exc_info=True） |
| `api/v1/backtest.py` | 4 | 🟢 低（Bg task 包装） |
| 其他 23 处 | — | 🟢 大部分有 `logger.exception` 或注释 |

#### 关键 P0/P1 问题

**S3-CRIT-01: BacktestEngine 主循环 8 处违规吞异常**（CLAUDE.md §6 工程纪律）— `engine/backtest/engine.py`

> 全文件共 9 处 `except Exception`，其中 line 182 正确使用 `logger.exception`（OK），下表 8 处违规：

| 行号 | 异常处 | 当前日志级别 | 严重性 |
|------|--------|-------------|--------|
| 147 | `adj_prices.set_index` | `pass`（**完全无日志**） | 🔴 |
| 213-217 | `strategy.score` | `logger.debug` | 🔴 |
| 226-227 | `scorer.aggregate` | **完全无日志** | 🔴 |
| 256-257 | `signal_engine.generate` | `logger.debug` | 🔴 |
| 275-276 | `position_engine.suggest` | `logger.debug` | 🔴 |
| 360-361 | `_get_financials_at` | `pass` | 🟡 |
| 383-384 | `_get_quotes_at` | `pass` | 🟡 |
| 408-409 | `_get_market_state` | 默认返回 OSCILLATION，**无日志** | 🟡 |

- 后果：典型"SUCCESS 但产出为 0"反模式：用户 BacktestView 看到任务 status=SUCCESS，但回测净值恒为 1.0、零交易记录；生产日志中 INFO/WARN 级别全无错误线索；只有把 LOG_LEVEL 调到 DEBUG 才能看到根因
- 与 CLAUDE.md §6 直接冲突：
  > "Engine/Service 层主循环中 `except Exception` 分支若返回空集合/None/默认值，必须 `logger.exception(...)`（不可用 `logger.debug`）"
- 与 memory `feedback_backtest_demo_fix.md` 的根因（MomentumStrategy industry_rs 占位 + _get_market_state 返回类型错）记录一致——这正是当时排查耗时的根本原因
- 修复方案：3 处 `logger.debug`（line 213/256/275）改 `logger.exception` 或 `logger.warning(..., exc_info=True)`；4 处 bare `pass`（line 147/226/360/383）必须加 `logger.exception`；line 408 默认返回 OSCILLATION 时加 `logger.warning(..., exc_info=True)`
- 优先级：**P1**（直接违反 CLAUDE.md 已固化的工程纪律，且有真实历史损害）

**S3-HIGH-02: `data_service.fetch_*_metadata` 静默降级到 raw["is_up_to_date"] = False**

`services/data_service.py:431-433`
```python
except Exception:
    raw["is_up_to_date"] = False
return raw
```
- 无 logger，仅设置标志位
- 调用方仅看到 "数据未更新到最新"，无法区分是 Tushare 503、SQL 异常、还是网络中断
- 修复：加 `logger.warning("metadata_status_check_failed", exc_info=True)`
- 优先级：P2

#### 已正确处理的降级（OK 范例）

| 文件 | 行 | 模式 |
|------|---|------|
| `pipeline/daily_pipeline.py:94` | `logger.exception` | ✅ 主流程失败 |
| `pipeline/daily_pipeline.py:336/358/380` | `logger.warning(..., exc_info=True)` | ✅ Step4~6 best-effort 显式标注 |
| `services/data_service.py:127/145/160/180/265` | `logger.exception` | ✅ 数据采集子任务 |
| `services/notification_service.py:104` | `logger.exception` | ✅ |
| `notification/wxpusher.py:91` | `logger.warning` 含 attempt 编号 + 上层记 ERROR | ✅ 与 SDD §13.1 重试策略一致 |

#### S-3 维度评级
**部分达标，关键不达标**。Pipeline / DataService / 通知均符合 CLAUDE.md §6 规范，但 **BacktestEngine 主循环 8 处违规吞异常**直接复刻了 memory `feedback_backtest_demo_fix.md` 中 Demo 零净值的根本原因。这是上线后最容易引发"前端任务成功，但结果为 0/恒定"用户投诉的代码路径，必须在 V1.5 修复。

---

### 6.4 S-4 认证与权限

#### 审计范围
- JWT 实现（access / refresh / 算法 / 过期时间）
- 密码哈希（bcrypt / 计时侧信道）
- 单管理员模型适用性
- API 路由保护（HTTPBearer 覆盖度）

#### 关键 OK 实现

**FIN-OK-S4-01**: bcrypt + 计时侧信道防护 — `api/v1/auth.py:14-21`
```python
password_ok = verify_password(body.password, settings.admin_password_hash)
username_ok = body.username == settings.admin_username
if not (username_ok and password_ok):
    return 401
```
- 先执行 bcrypt（~100ms），再比对用户名
- 即便用户名错也消耗完整 bcrypt 时间，避免基于响应时间的用户名枚举
- 评级：✅ 严格符合 SDD §3.3 + CLAUDE.md §6 规范

**FIN-OK-S4-02**: JWT 双 Token 模型 — `core/security.py`
- access token 60 分钟、refresh token 7 天（settings 可配）
- `decode_token(token, expected_type)` 严格验证 type 字段（access ≠ refresh），防止 access token 被当 refresh 用
- 算法 HS256（对称密钥），符合单管理员场景

**FIN-OK-S4-03**: 全局 HTTPBearer 依赖 — `api/deps.py:31-37`
- `get_current_user` 抛 HTTPException 401（不暴露具体原因）
- 配合 settings.json hooks 通过路由层 `Depends(get_current_user)` 强制保护

#### 缺口

**S4-GAP-01: 单管理员模型 = 无权限粒度**
- ADMIN_USERNAME / ADMIN_PASSWORD_HASH 是唯一身份凭证
- 无法支持 V1.5 多账户（如"投资经理"+"风控员"）
- 影响：所有 API 端点权限二选一（已登录 / 未登录）
- 优先级：可接受（V1.0 单管理员模式是 SDD §3.3 明确范围）

**S4-GAP-02: settings.admin_password_hash 通过 .env 直接注入**
- 没有"密码到期"概念，无强制更换策略
- 修改密码需手动改 .env + 重启服务（非 API 端点）
- 优先级：P3（个人版可接受）

**S4-GAP-03: 没有 API rate limit / brute force 防护**
- 登录端点无失败次数限制，理论上可暴力破解（bcrypt 慢 + 64 字符 jwt_secret_key 缓解）
- Phase 10 ConfigService 用了 Redis，可顺带加 SlowAPI / fastapi-limiter
- 优先级：P2（V1.5 增加）

#### S-4 维度评级
**达标**（V1.0 单管理员范围内）。bcrypt + 计时侧信道防护 + JWT type 严格校验 + HTTPBearer 全局保护链路完整。多账户/权限粒度/限流是 V1.5 自然延伸。

---

### 6.5 S-5 可观测性

#### 审计范围
- 日志体系：RotatingFileHandler / JSON 结构化 / 第三方库噪声压制
- 健康检查 / 启动失败 fail-fast vs lifespan exception
- 关键链路日志埋点（CP1/CP2/CP3 / 通知 / 回测进度）

#### 关键 OK 实现

**FIN-OK-S5-01**: RotatingFileHandler + JSON 结构化 — `core/logging_config.py`
- 50 MB / 文件，保留 7 个归档（约 350 MB 上限）
- JSONFormatter 输出 timestamp / level / logger / message / module / function / line / exc_info
- 控制台 + 文件双通道；`apscheduler/httpx/httpcore/uvicorn.access` 第三方库压制为 WARNING
- 评级：✅ 与 SDD §15.5 + Phase 10 §8.4 一致

**FIN-OK-S5-02**: 健康检查端点 — `main.py:160-162`
- `GET /health` 返回 `{"status": "ok", "version": "1.0.0"}`
- 适合 K8s liveness / Docker compose healthcheck

**FIN-OK-S5-03**: lifespan 启动失败 fail-fast — `main.py:96-97 / 115-116`
- TushareAdapter / Calendar 初始化失败：`logger.exception("lifespan_init_failed_scheduler_not_started")`
- 不阻塞应用启动（数据 API 返回 503），但日志清晰

#### 缺口

**S5-GAP-01: 无 Prometheus / OpenTelemetry 指标暴露**
- 关键业务指标（信号生成数、Pipeline 执行时长、Tushare 调用 QPS、回测任务排队数）未做 metrics
- 仅依靠日志难以做趋势分析（如"过去 1 个月每日信号数下降趋势"）
- 优先级：P2（V1.5 接入 prometheus_client）

**S5-GAP-02: 调度器健康检查缺失**
- APScheduler 调度状态未暴露（jobs 列表 / 下次运行时间 / 失败计数）
- 当某个 cron job 因 lifespan 异常未注册时，前端 `/pipeline/status` 无法察觉
- 修复：增加 `GET /health/scheduler` 端点返回 jobs 元信息
- 优先级：P2

**S5-GAP-03: 日志中含敏感字段**
- 部分日志可能包含 ts_code 列表（合规性 OK）但需要确认未输出 ADMIN_PASSWORD_HASH / TUSHARE_TOKEN
- 抽查 `core/config.py` settings 字段未在日志中暴露 — ✅
- 但 logging_config.py 的 console handler 用 `%(message)s` 直接输出 record.msg，依赖业务代码自律
- 优先级：P3（建议加 SecretFilter 中间件）

#### S-5 维度评级
**基本达标**。日志体系完整，健康检查存在。缺指标 / 调度器健康端点 / 日志敏感词过滤是 V1.5 增强。

---

### 6.6 S-6 性能与可扩展性

#### 审计范围
- 数据库连接池配置 / 异步 IO 设计
- pandas 计算瓶颈（O(n²) 风险点）
- Tushare 限流（Semaphore）
- 回测引擎大数据量表现

#### 关键 OK 实现

**FIN-OK-S6-01**: SQLAlchemy 异步 + pool_pre_ping — `core/database.py:7-11`
```python
engine = create_async_engine(
    settings.database_url, echo=settings.debug, pool_pre_ping=True
)
```
- pool_pre_ping 确保从池中取出的连接有效（生产环境推荐配置）
- 评级：✅

**FIN-OK-S6-02**: Engine 层 O(1) 优化（Phase 4 评审 C-03）
- `_compute_historical_percentile` 循环外预计算 `available_codes = set(...)`
- O(n²) → O(1)（每次查询）
- 评级：✅ memory `phase4_factor_engine` C-03 已修复

**FIN-OK-S6-03**: ScoringService 轻量快照（Phase 4 评审 C-04）
- `_build_filter_snapshot` 只加载 2 个数据源（快照 + 财报，避开 adj_prices/pe_pb 全量加载）
- 过滤后再 `_build_market_snapshot` 仅加载 universe 内数据
- 评级：✅ 大宇宙（5000+ 股票）下评分耗时大幅下降

**FIN-OK-S6-04**: BacktestService 线程池执行 — `services/backtest_service.py:110`
```python
result = await asyncio.to_thread(self._engine.run, config, data, progress_cb)
```
- 同步 CPU 密集任务用线程池，不阻塞 event loop
- 评级：✅

#### 缺口

**S6-GAP-01: 集成测试 DB 单进程串行（CLAUDE.md §4 已注明）**
- "⚠️ 严禁同时启动多个进程（DB 竞态）"
- 现状：本地单实例 PostgreSQL，集成测试只能串行（17 文件 × 平均 3-5 用例，CI 总时长偏长）
- 优先级：P3（V1.5 改 Docker per-test schema 隔离）

**S6-GAP-02: BacktestEngine 内存使用 O(N × T)**
- `position_snapshots` 在内存中累积每日所有持仓快照（每股 6 字段 × 持仓数 × 交易日数）
- 长周期回测（5 年 × 50 持仓 × 1250 交易日 ≈ 62500 条）尚可承受
- 但 V1.5 若支持多组合并行回测，需流式写入 DB 而非内存累积
- 优先级：P2（V1.5）

**S6-GAP-03: Tushare 限流靠 Semaphore，无熔断**
- `data/adapters/tushare.py` 用 `asyncio.Semaphore` 控并发
- 但 Tushare 临时熔断（503）时无指数退避策略，仍按 retry 重试
- 优先级：P2（V1.5 增加 tenacity 退避）

#### S-6 维度评级
**达标**（V1.0 单用户、单进程范围内）。异步 IO + 连接池 + O(1) 优化 + 线程池执行齐全。多用户并行 / 大规模回测 / 熔断 是 V1.5 范畴。

---

### 6.7 S-7 测试体系真实保护程度

#### 审计范围
- 测试数量分布（unit / e2e / integration / smoke）
- 覆盖深度 vs 数量（true-positive 信号）
- 关键金融正确性是否有对应测试

#### 测试统计

| 类型 | 文件数 | 测试函数数 | 跑动条件 |
|------|--------|-----------|---------|
| unit | 30 | 282 | 钩子自动 |
| e2e | 17 | 161 | 钩子自动 |
| integration | 17 | 79 | 需 PostgreSQL |
| smoke | 2 | 84 | 需服务+密码，不入 CI |
| **合计** | **66** | **606** | — |

memory 显示 480 passed（实际跑动数）—— 差额（606 - 480 = 126）含 smoke（不入 CI）+ skipped 用例。

#### 已验证的关键覆盖

| 维度 | 测试 | 评级 |
|------|------|------|
| WAC 公式 | `test_account_logic.py` 7 cases | ✅ |
| Scorer 三态权重归一化 | `test_scorer.py` 14 cases | ✅ |
| MarketStateEngine 防抖 | `test_market_state_engine.py` 10 cases | ✅ |
| BacktestEngine | `test_backtest_engine.py` 3 + `test_int_backtest_engine.py` 2 | ⚠️ 偏少 |
| RiskChecker | `test_risk_checker.py` 4 cases | ⚠️ 偏少 |
| UniverseFilter F-1~F-8 | `test_universe.py` 10 + `test_universe_restored.py` 4 = 14 | ✅ |
| 策略 4 个 | `test_strategies_impl.py` 12 cases | ✅ |
| 配置消费 | `test_int_config_consumption.py` 3 + `test_int_config_service.py` 7 | ✅ |
| 信号生成端到端 | `test_int_signal_generate_for_date.py` 3 | ⚠️ 偏少 |

#### 缺口

**S7-GAP-01: BacktestEngine 集成测试覆盖不足**
- `test_int_backtest_engine.py` 仅 2 cases
- §5.5 已识别 4 个 P0 问题（T+1 / quotes 切片 / pe_pb_history / RiskChecker）—— 这些都应该有 failing 测试逼回归
- 现状：测试覆盖了 happy-path（数据齐全 → 净值 ≠ 1.0），但未覆盖 limit_up/is_suspended/limit_up 等边缘路径
- 优先级：P1（修复 §5.5 P0 问题时同步补 6+ 测试）

**S7-GAP-02: F-2 生存者偏差无专项测试**
- 没有"已退市股 / 历史 ST 股是否被排除"的端到端验证
- 与 §5.2 P1 缺陷 FIN-HIGH-05 / FIN-HIGH-07 一致
- 优先级：P1

**S7-GAP-03: 集成测试只跑 happy-path，无故障注入**
- DataService 测试不模拟 Tushare 503 / 网络中断
- DailyPipeline 测试不模拟 CP1 失败 → CP2/CP3 跳过路径
- 优先级：P2（V1.5 增加故障注入测试）

**S7-GAP-04: 无回归测试拦截 §5.3 FIN-HIGH-08（分红双重计算）**
- WAC 单测全部覆盖 BUY/SELL，分红场景 0 cases
- `test_account_logic.py` 仅 7 case 不含 record_dividend
- 优先级：P1（与 FIN-HIGH-08 修复一起补）

#### S-7 维度评级
**整体达标**（606 个用例，覆盖核心计算 / API / 集成路径）。但回测引擎集成测试 + 生存者偏差 + 故障注入 + 分红场景四个高敏感点的测试欠账，与 §5 发现的多个 P0/P1 在测试层面无回归保护呼应。修复 §5 问题时必须同步补测试。

---

---

## 7. 第三档：交付完整性逐维度评审

### 7.1 D-1 金融用户 UX

#### 审计范围
- 9 个核心视图（Dashboard / Signals / Positions / Backtest / Reports / FactorQuality / Settings / Onboarding / Login）的可用性
- 术语 Tooltip 系统（glossary.ts）覆盖度
- 基础 UI 健壮性（空状态、加载状态、错误反馈）
- 信号决策辅助场景的工作流闭环

#### 关键 OK 实现

**FIN-OK-D1-01**: 9 视图齐全 + 9 通用组件
- Views: BacktestView / DashboardView / FactorQualityView / LoginView / OnboardingView / PositionsView / ReportsView / SettingsView / SignalsView
- Components: AppLayout / DisclaimerBanner / EmptyState / KlineChart / NavChart / NotificationBell / SignalCard / StatusBadge / TermLabel
- 评级：✅ 与 Phase 9 设计一致

**FIN-OK-D1-02**: 术语 Tooltip 系统 — `frontend/src/utils/glossary.ts`
- 含 27 个术语定义，覆盖绩效指标 / 三态市场 / 因子 / 风控等
- 5 个视图（Dashboard / Backtest / FactorQuality / Reports / Settings）已接入 `<TermLabel term="xxx" />`
- 评级：✅ 个人投资者新手友好性显著优于普通仪表盘

**FIN-OK-D1-03**: OnboardingWizard 6 步引导 — `OnboardingView.vue`
- 欢迎 → Tushare Token → 初始数据拉取（含 60 日回填默认）→ 初始资金 → 参数默认 → 完成
- 数据状态自动检测，503 时给出明确提示并允许跳过
- 评级：✅ Phase 10 §6.6 设计完整落地

**FIN-OK-D1-04**: a-table data-source 强化（Phase 9 评审踩坑）
- memory `phase9_frontend` 记录 "a-table data-source 必须是数组" → 所有列表 API 在前端提取 `.items`
- 评级：✅ 已修复并固化为前端 API 函数约定

#### 缺口

**D1-GAP-01: 前端不显示 V1.0 回测引擎降级说明**
- BacktestEngine 已知 4 个 P0 失真（§5.5 FIN-CRIT-01~04），但 BacktestView 仅展示通用 disclaimer
- 用户在 BacktestView 看到 Sharpe=2.5 时无法察觉"该值由于 T+1 违反 + 涨停日成交 + 无 RiskChecker 而存在系统性高估"
- **金融决策辅助场景下用户极易被误导**
- 修复建议：BacktestView 顶部增加红色 banner，明确列出 V1.0 回测局限：
  > ⚠️ V1.0 回测引擎已知局限：(1) T 日 close 撮合（实盘 T+1 早盘）；(2) 涨停/停牌日不被排除；(3) 无 RiskChecker 拦截。回测净值/Sharpe 可能高于实盘可达成结果，仅作策略**相对排序**参考，不可作为绝对收益预期。
- 优先级：**P0**（与 §5.5 P0 配套修复，作为上线硬条件之一）

**D1-GAP-02: SignalCard 不展示评分来源决策路径**
- 当前 SignalCard 仅显示 `composite_score` 与 action（BUY/HOLD/SELL）
- 用户问"为什么这个信号是 BUY？"时无可视化路径
- 与 §6.1 S1-GAP-01 因子级溯源缺失同根
- 优先级：P2（V1.5）

**D1-GAP-03: 无错误重试 / 网络中断兜底**
- 抽样查 SignalsView / DashboardView：API 失败仅 console.error，前端无 toast 或重试按钮
- 影响：弱网或后端临时 503 时用户面对空白屏，无操作指引
- 优先级：P2

**D1-GAP-04: 多视图缺空状态文案差异化**
- EmptyState 组件存在但部分视图（如 ReportsView 报告列表为空时）使用 a-empty 默认提示
- 一致性可改进
- 优先级：P3

#### D-1 维度评级
**整体达标**（视图齐全 + 术语 Tooltip + Onboarding + a-table 修复），但 **回测视图缺 V1.0 局限说明**是一个直接影响金融决策辅助可信度的 P0 问题，必须随 §5.5 P0 修复同步上线。

---

### 7.2 D-2 合规与免责

#### 审计范围
- 回测结果免责声明（DISCLAIMER 常量）
- 信号 / 评分 / 报告等其他高敏感场景的免责
- "本系统不构成投资建议" 等强制风险提示触达
- 数据使用合规（Tushare ToS）

#### 关键 OK 实现

**FIN-OK-D2-01**: 回测引擎硬编码 DISCLAIMER — `engine/backtest/report.py:11-15`
```python
DISCLAIMER = (
    "回测结果基于历史数据，不代表未来表现。"
    "历史数据已尽力处理幸存者偏差，但仍可能存在数据局限性。"
    "本报告不构成任何投资建议。"
)
```
- 在 `BacktestResult.disclaimer` 字段返回，前端 BacktestView 通过 DisclaimerBanner 展示
- 评级：✅ 符合 SDD §7.7.4 + 中国证券业相关风险提示惯例

**FIN-OK-D2-02**: DisclaimerBanner 组件设计合理 — `components/DisclaimerBanner.vue`
- a-alert type=warning + show-icon
- 默认折叠 80 字符，"展开全文"按钮交互
- 评级：✅ UI 友好

#### 缺口

**D2-GAP-01: SignalsView / DashboardView / ReportsView 全部缺免责**
- 仅 BacktestView 用 DisclaimerBanner
- SignalsView 显示 BUY/HOLD/SELL 信号，**未在视图层加任何免责**
- 用户看到"600519 BUY 评分 0.92"时**没有任何"本系统不构成投资建议"提示**
- 影响：合规风险（个人投资者将 AI 信号误认为推荐而追责）
- 修复建议：
  - SignalsView 顶部固定 DisclaimerBanner: "信号为算法量化结果，不构成投资建议"
  - DashboardView 底部固定一行小字风险提示
  - ReportsView 报告内嵌免责（与回测同源）
- 优先级：**P0**（合规硬条件，上线前必修）

**D2-GAP-02: DISCLAIMER 措辞偏弱**
- 当前措辞"已尽力处理幸存者偏差"——但 §5.2 已发现回测中 is_st/is_suspended/delist_date 全部缺失，措辞与实际能力不符
- 用户基于此措辞建立的信任度过高
- 修复建议：
  > V1.0 回测引擎已知局限：撮合方式与 A 股 T+1 规则存在差异，未排除涨停/停牌/已退市股，无风控阻断。回测结果与实盘可达成收益**无系统性对应关系**，仅供策略相对排序参考，不构成投资建议。
- 优先级：**P0**（与 D1-GAP-01 同源）

**D2-GAP-03: 无 Tushare 数据使用合规声明**
- Tushare Pro Token 使用条款要求"不得二次分发原始数据"
- 当前系统未设访问限制，理论上多人共用一个 deploy 等于变相分发（V1.0 单管理员场景影响有限）
- 优先级：P3（个人版可接受）

**D2-GAP-04: 无登录页 / 首页"个人投资决策辅助工具，非投顾"通用声明**
- LoginView 仅展示登录表单
- 用户无任何前置认知就直接进入系统
- 修复建议：LoginView 底部加固定脚注 "本系统为个人量化交易决策辅助工具，不提供投资建议、不接受委托、不构成投顾服务"
- 优先级：P1

#### D-2 维度评级
**部分达标**。BacktestView + DISCLAIMER 设计良好，但 **SignalsView/Dashboard/Reports 三个核心信息流视图全部无免责** + 现有措辞与回测真实能力不符 + 登录页无系统性声明，是本档**最大合规风险**。**P0 阻塞，上线前必修**。

---

### 7.3 D-3 文档与代码一致性

#### 审计范围
- SDD / system_design / 10 个 phase 设计文档与代码实现的对照
- 既有 phase4~phase10 代码评审报告问题点是否已修复
- CLAUDE.md 工程规范的执行情况

#### 关键 OK 实现

**FIN-OK-D3-01**: 文档体系完整
- SDD: `docs/spec/QuantPilot_SDD.md`（21 个一级章节）
- system_design: `docs/design/system_design.md`（12 个一级章节）
- 10 个 phase 设计：`phase1_infrastructure ~ phase10_deployment`
- 部署指南: `docs/guides/deployment.md`（11 节，覆盖前置 / SSL / 备份 / 故障 / 卸载）
- Phase 评审报告: 9 份（phase1 合并 + phase4~10 + phase1_5 设计 + phase10_design）
- 评级：✅ 文档体系工业级完备

**FIN-OK-D3-02**: Phase 评审循环已闭环
- phase4: C-01~C-12 全部修复（memory 记录）
- phase6: C-01~C-07 全部修复
- phase7: C-01~C-07 全部修复
- phase8: 评审通过（343 tests passed，ruff 0 error）
- phase9: C-01~C-08 修复
- phase10: 2026-04-27 评审 C-01~C-09 全部修复（最近一次）
- 评级：✅ 工程纪律严格

**FIN-OK-D3-03**: CLAUDE.md 持续演进
- §6 代码规范从 Phase 1 确立后随每个 phase 增补关键经验
- 包括 ORM 类型、Mapped[]、O(n) → O(1) 优化、event loop 规范、静默吞异常禁止等
- 评级：✅ 与代码实现紧密同步

#### 缺口

**D3-GAP-01: SDD §10 因子参数与策略源码不一致** — 与 §5.3 FIN-MED-12 同源
- SDD §10 给出"窗口期可调"的 Strategy 参数表
- 但 4 个策略源码注释 "降级：dataclass 仅作 Pipeline 快照登记，参数实际不生效"
- 文档未注明这一降级
- 优先级：P2（修复 FIN-MED-12 时同步更新 SDD）

**D3-GAP-02: SDD 回测章节未注明 V1.0 4 个 P0 局限**
- SDD §7.7 描述回测引擎能力时未声明 T+1 违反 / quotes 缺失等
- 与 D-2 D2-GAP-02 同源
- 修复：SDD §7.7 增加"V1.0 回测引擎已知局限"章节
- 优先级：**P0**（合规链条完整性，与 D2-GAP-02 同期修复）

**D3-GAP-03: 部署指南未提及单进程约束**
- `docker-compose.prod.yml` 注释清晰："APScheduler in-memory，多 worker 重复触发，因此单进程"
- 但 `docs/guides/deployment.md` 未单独章节强调，运维人员可能误改 uvicorn workers 致定时任务重复
- 修复：deployment.md 在第 7 节增加"⚠️ uvicorn 必须单进程"小节
- 优先级：P2

**D3-GAP-04: 既有 phase 评审中"推迟到 V1.5"项缺统一汇总**
- memory `phase4/6/7/9/10` 等多处提到推迟项（如 Phase 6 C-06 ValueError→HTTP 状态码、Phase 4 真实参数下沉等）
- 但没有统一的 V1.5 路线图文档
- 修复：本评审 §8 V1.5 路线图建议章节将做汇总（Batch 4）
- 优先级：P2（Batch 4 自然解决）

#### D-3 维度评级
**整体达标**。文档体系工业级完备，phase 评审循环闭环执行良好，CLAUDE.md 演进规范。但 **SDD §7.7 未注明回测 V1.0 局限**是 D-2 P0 链条延伸，必须同期修复。其他文档不一致项均为 V1.5 范畴可控范围。

---

### 7.4 D-4 生产就绪度

#### 审计范围
- Docker 生产部署链路（docker-compose.prod.yml）
- 健康检查 / 备份 / 恢复 / 故障排查
- 单进程 + APScheduler 限制说明
- HTTPS / SSL / CORS 配置
- 启动顺序 + alembic 迁移自动化

#### 关键 OK 实现

**FIN-OK-D4-01**: docker-compose.prod.yml 生产链路 — 6 个服务正确编排
- db (postgres:15) + redis (redis:7) 双健康检查 + appendonly 持久化
- frontend-builder（一次性构建容器，输出到共享 volume）
- backend（含 alembic upgrade head + uvicorn 单进程）
- nginx（含 frontend_dist 和 nginx_logs volumes）
- 5 个 named volumes（pg_data / redis_data / frontend_dist / backend_logs / nginx_logs）
- TZ 显式设置 Asia/Shanghai
- 评级：✅ 与 Phase 10 §8.1 设计一致

**FIN-OK-D4-02**: deployment.md 部署指南 11 节齐全
- 1 前置 / 2 域名+SSL / 3 .env / 4 首次部署 / 5 备份恢复 / 6 日志 / 7 运维 / 8 监控规划 / 9 故障 / 10 回滚 / 11 卸载
- 含一键脚本 `scripts/deploy.sh / backup_db.sh / restore_db.sh / prod_smoke.sh`
- crontab 每日 02:00 自动备份示例
- 故障排查表 7 行覆盖典型问题（401 / 503 / 推送失败 / 白屏 / alembic 失败 / 磁盘 / WS 超时）
- 评级：✅ 个人版生产部署门槛清晰、运维友好

**FIN-OK-D4-03**: bcrypt 哈希 + JWT 密钥生成命令直接给出
- ADMIN_PASSWORD_HASH 用 `docker run --rm python:3.12-slim ...` 一行命令生成
- JWT_SECRET_KEY 用 `openssl rand -hex 64`
- 评级：✅ 安全敏感参数生成无门槛

**FIN-OK-D4-04**: backend/Dockerfile.prod 与 alembic upgrade 自动衔接
- `command: sh -c "alembic upgrade head && uvicorn ..."`
- 启动时自动应用所有迁移
- 评级：✅ 部署/升级路径自动化

#### 缺口

**D4-GAP-01: 单进程约束仅在 compose yaml 注释，运维易踩坑**
- 注释仅 4 行，且嵌在 backend service `command` 上方
- `docs/guides/deployment.md` 未单独标记
- 影响：运维人员若改 uvicorn `--workers 4`，APScheduler 会触发 4 次定时任务
- 修复：deployment.md §7 加 "⚠️ uvicorn 必须 --workers 1，否则 APScheduler 重复触发" 醒目说明
- 优先级：P2（与 D3-GAP-03 同源）

**D4-GAP-02: 无 K8s / 多副本部署模板**
- 当前仅 docker compose 单机
- V1.5 若需高可用，APScheduler in-memory jobstore 必须改为 SQLAlchemyJobStore
- 影响：扩展性受限于 V1.0 设计选择
- 优先级：P3（V1.5 自然演进）

**D4-GAP-03: 无监控/告警生产配置**
- §8 监控建议章节明确写"V1.5 规划"
- 当前生产仅靠 `docker logs` + `/health` 端点
- 长期运行的"调度器静默不工作"问题难以发现（与 §6.5 S5-GAP-02 同源）
- 优先级：P2

**D4-GAP-04: SSL 默认关闭，文档需用户主动启用**
- nginx.prod.conf HTTPS 默认注释，部署指南给出步骤但需手动操作
- 内网部署 OK；公网部署用户若忘记启用 HTTPS → JWT 明文传输
- 修复建议：deployment.md §2 增加红色警告"公网部署必须启用 HTTPS"
- 优先级：P1（用户教育/合规）

**D4-GAP-05: backups/ 目录无校验机制**
- 备份脚本 `backup_db.sh` 输出 .sql.gz，但无 SHA256 校验
- 备份文件损坏（磁盘错误 / 写入截断）时恢复才发现
- 优先级：P3（V1.5 增加 sha256sum 校验）

#### D-4 维度评级
**达标**（V1.0 单机生产部署范围内）。Docker compose 链路完整、备份恢复脚本齐全、部署指南详尽。单进程约束需要在文档加强（P2），公网 HTTPS 默认关闭需 P1 警示，监控告警是 V1.5 自然延伸。无上线硬阻塞。

---

---

## 8. V1.5 技术债与路线图建议

按主题归集 P2/P3 项 + Phase 评审中已记录的推迟项，给出 V1.5 优先级排序。

### 8.1 回测引擎重构（最高优先级）

**目标**：使回测净值与实盘可达成收益建立**系统性对应关系**。

包含项：
- 已在 P0 完成的 4 个 CRIT 修复（T+1 / quotes / pe_pb / RiskChecker）作为基线
- FIN-HIGH-05/07/09 三项回测数据完整性
- FIN-MED-12 策略参数真实下沉到 Strategy._compute
- S6-GAP-02 内存优化（流式写 DB）
- S7-GAP-01/02/04 测试覆盖

**预估**：8-12 pd

### 8.2 因子级溯源 LineageService 完整化

**目标**：用户问"为什么这个信号是 BUY"时可视化展示 MA60=0.85 → 历史 80% 分位、MACD hist > 0、PE 处于行业 30% 分位等具体因子值。

包含项：
- S1-GAP-01 因子级溯源
- S1-GAP-02 strategy_version 字段
- D1-GAP-02 SignalCard 决策路径展示

**预估**：5-8 pd

### 8.3 可观测性 + 监控告警

**目标**：从"靠日志被动排查"升级为"指标+告警+可视化主动监控"。

包含项：
- S5-GAP-01 Prometheus / OpenTelemetry
- S5-GAP-02 调度器健康端点
- S5-GAP-03 SecretFilter
- S2-GAP-01 数据质量指标持久化
- D4-GAP-03 监控/告警生产配置

**预估**：5-7 pd

### 8.4 多账户 + 权限粒度

**目标**：支持"投资经理 + 风控员"角色分工（如 SDD §3.3 V1.5 路线提及）。

包含项：
- S4-GAP-01 多账户 / 权限粒度
- S4-GAP-03 API rate limit
- S4-GAP-02 密码到期策略

**预估**：8-10 pd（含数据库 schema 变更）

### 8.5 性能与扩展性

包含项：
- S6-GAP-01 测试 per-test schema 隔离
- S6-GAP-03 Tushare tenacity 熔断
- D4-GAP-01/02 多副本部署 / K8s 模板
- APScheduler 改 SQLAlchemyJobStore

**预估**：5-8 pd

### 8.6 UX 与合规增强

包含项：
- D1-GAP-03 错误重试 / 网络中断兜底
- D1-GAP-04 空状态文案差异化
- D2-GAP-03 Tushare 合规声明
- D4-GAP-04 强 HTTPS 引导（如 Caddy 自动证书）
- D4-GAP-05 备份 SHA256 校验

**预估**：3-4 pd

### 8.7 文档同步

包含项：
- D3-GAP-01 SDD §10 策略参数对齐
- D3-GAP-03 部署指南 uvicorn 单进程警示
- D3-GAP-04 V1.5 路线图统一汇总文档（本节是雏形）

**预估**：1-2 pd

### V1.5 总体路线建议

| 主题 | 优先级 | 预估 | 时间窗口 |
|------|--------|------|---------|
| 8.1 回测引擎重构 | 🔴 最高 | 8-12 pd | M+1 |
| 8.2 因子级溯源 | 🟡 高 | 5-8 pd | M+2 |
| 8.3 可观测性 | 🟡 高 | 5-7 pd | M+2 |
| 8.4 多账户 | 🟢 中 | 8-10 pd | M+3~M+4 |
| 8.5 性能扩展 | 🟢 中 | 5-8 pd | M+3 |
| 8.6 UX/合规 | 🟢 中 | 3-4 pd | 持续 |
| 8.7 文档同步 | 🟢 低 | 1-2 pd | 随版本 |
| **合计** | | **~50 pd** | **3-6 个月** |

---

---

## 9. 评审方法与覆盖说明

### 9.1 评审深度

按 11 维度采用差异化深度：

| 档 | 深度 | 方法 |
|----|------|------|
| 🔴 金融正确性（F-1~F-5）| 端到端验算 | 抽样验算（WAC/NAV/权重归一化等 7 类用例）+ 静态审计（PIT/T+1/quotes 切片完整性）+ 公式核对（成本模型/夏普） |
| 🟡 系统稳健性（S-1~S-7）| 静态审计 | 全代码 grep `except Exception`（54 处分布）+ ORM/Service/Pipeline 关键链路代码逐行核查 + 测试统计 |
| 🟢 交付完整性（D-1~D-4）| 文档对照 + 抽样 | SDD/system_design/phase 设计文档对照 + 9 个前端视图 + Docker compose + 部署指南完整阅读 |

### 9.2 覆盖范围

- ✅ Phase 1~10 全部交付物
- ✅ 后端 ~95 个 Python 模块（engine / services / api / data / pipeline / models / schemas / core / notification）
- ✅ 前端 9 视图 + 9 通用组件 + glossary + stores + api 函数
- ✅ 部署链路（docker-compose.dev/prod + Dockerfile + nginx.conf + .env 模板 + deployment.md）
- ✅ 测试体系（unit 30 / e2e 17 / integration 17 / smoke 2 文件，共 606 测试函数）
- ✅ 文档体系（SDD + system_design + 10 phase 设计 + deployment.md + 9 phase 评审报告）

### 9.3 评审依据

- SDD: `docs/spec/QuantPilot_SDD.md`
- system_design: `docs/design/system_design.md`
- 10 phase 设计文档: `docs/design/phases/phase{1..10}_*.md`
- 既有评审报告: `docs/reviews/phase{4..10}_code_review*.md`、`phase{4..10}_design_review*.md`、`phase10_code_review_2026-04-27.md`
- CLAUDE.md 工程规范
- memory 持久化经验：phase4/6/7/9/10 修复要点、Phase 7 关键经验、回测 Demo 零净值根因记录

### 9.4 评审局限

- ❌ **未实际运行端到端业务流程**：未启动完整 Docker stack 跑一遍 DailyPipeline / BacktestEngine 看真实输出（如时间允许应运行 `seed_demo_data.py` 后回放）
- ❌ **未做安全攻击面扫描**：仅静态审计认证/JWT 实现，未做 OWASP Top 10 实际渗透
- ❌ **未做性能压测**：仅静态审计 O(n) → O(1) 优化，未实测大宇宙（5000+ 股票）评分耗时
- ❌ **金融正确性局限**：抽样验算只覆盖核心公式，对 4 个策略的因子组合细节、防抖动状态机等未做完整数学等价证明
- ❌ **前端未做手动验收**：仅静态审计代码，未在浏览器中跑通 9 视图的金路径（与 Phase 9 状态"手动验收进行中"一致）

### 9.5 评审产出

本报告 (`docs/reviews/v1_overall_review_2026-04-27.md` v1.0)：
- §1 执行摘要（1 段总评 + 整体评级 + Top 3 风险）
- §2 整体评级与上线建议（11 维度评级矩阵 + 分阶段上线建议）
- §3 阻塞清单（P0=8 / P1=12 / P2=19 / P3=4，含修复成本估算）
- §4 金融正确性专章（4.1~4.5 摘要）
- §5 第一档详细审计（F-1~F-5 含证据 + 抽样验算）
- §6 第二档详细审计（S-1~S-7）
- §7 第三档详细审计（D-1~D-4）
- §8 V1.5 技术债与路线图（7 主题 ~50 pd）
- 附录 A 抽样验算用例与预期值（7 类）
- 附录 B 静态审计代码定位索引（3 张表）

---

---

## 附录 A：抽样验算用例与预期值

### A.1 WAC（加权平均成本）

| 用例 | 输入 | 期望 | 源码计算 | 一致性 |
|------|------|------|---------|--------|
| 初次买入 | old=(0,0), new=(100,10), commission=5 | (0+1000+5)/100 = 10.05 | `compute_wac(0,0,100,10,5)` = 10.05 | ✅ |
| 加仓 | old=(100,10), new=(100,12), commission=5 | (1000+1200+5)/200 = 11.025 | `compute_wac(100,10,100,12,5)` = 11.025 | ✅ |
| 零手续费 | old=(100,10), new=(100,15), commission=0 | (1000+1500)/200 = 12.50 | `compute_wac(100,10,100,15,0)` = 12.50 | ✅ |

### A.2 NAV（账户净值）

| 用例 | 输入 | 期望 | 一致性 |
|------|------|------|--------|
| 初始 | cash=100000, mv=0, init=100000 | 1.000 | ✅ |
| 浮盈 | cash=50000, mv=60000, init=100000 | 1.100 | ✅ |
| 浮亏 | cash=80000, mv=15000, init=100000 | 0.950 | ✅ |

### A.3 Scorer 三态权重归一化

| 用例 | 配置 | 缺失策略 | 期望归一化 |
|------|------|---------|-----------|
| 全策略 UPTREND | {trend:0.4, momentum:0.3, reversion:0.2, value:0.1} | 无 | 原值（合计=1.0）✅ |
| 缺 ValueStrategy | 同上 | value | trend:0.444, momentum:0.333, reversion:0.222 ✅ |
| 缺 Reversion+Value | 同上 | reversion+value | trend:0.571, momentum:0.429 ✅ |

### A.4 三态市场系数（PositionSizer）

| 市场状态 | 期望系数 | SDD §10.1 | 源码 `engine/position.py` |
|---------|---------|----------|--------------------------|
| UPTREND | 1.0 | 1.0 | uptrend=1.0 ✅ |
| OSCILLATION | 0.75 | 0.75 | oscillation=0.75 ✅ |
| DOWNTREND | 0.5 | 0.5 | downtrend=0.5 ✅ |

### A.5 成本模型

| 公式 | 期望（price=100, commission=0.0003, stamp=0.001, slippage=0.0002） | SDD §10.5 |
|------|--------------------------------------------------------------------|-----------|
| `_buy_cost_per_unit` | 100 × (1+0.0003+0.0002) = 100.05 | ✅ |
| `_sell_proceeds_per_unit` | 100 × (1−0.0003−0.001−0.0002) = 99.85 | ✅ |
| 单次买卖成本占比 | (100.05 − 99.85) / 100 = 0.20% | ✅ |

### A.6 PIT 财报访问

| 场景 | as_of_date | 应返回 |
|------|------------|--------|
| 标的 X 公告 2024-Q1 报告于 2024-04-30 | 2024-04-29 | 2023-Q4（最近已发布）✅ |
| 同上 | 2024-04-30 | 2024-Q1（当日已发布，可使用）✅ |
| 同上 | 2024-05-15 | 2024-Q1 ✅ |

### A.7 UniverseFilter F-2（上市满 60 交易日）

| today | list_date | 期望 |
|-------|-----------|------|
| 2024-12-31 | 2024-09-30 | 入选（60 交易日前 ≈ 2024-10-08）✅ |
| 2024-12-31 | 2024-12-01 | 排除 |
| 2024-12-31 | 2020-01-01 | 入选 |



---

## 附录 B：静态审计的代码定位索引

### B.1 金融正确性相关

| 主题 | 文件 | 行号 | 说明 |
|------|------|------|------|
| PIT 财报查询 | `data/repository.py` `get_latest_financial` | DISTINCT ON + WHERE publish_date<= | ✅ |
| 历史 N 期财报 | `data/repository.py` `get_latest_n_financials` | — | F-5 两期检查 |
| 流动性过滤 | `data/repository.py` `get_avg_amount` | WHERE trade_date < as_of_date | ✅ 严格小于 |
| 活跃股票宇宙 | `data/repository.py` `get_active_stock_codes` | is_active=True | 实盘用 |
| BacktestEngine 主循环 | `engine/backtest/engine.py` `run` | — | 核心 |
| 数据切片 | `engine/backtest/engine.py` `_get_quotes_at` | 364-385 | ❌ 仅 close |
| 财报切片 | `engine/backtest/engine.py` `_get_financials_at` | — | ✅ PIT |
| 成交逻辑 | `engine/backtest/engine.py` `_execute_signals` | 430-490 | ❌ 当日 close |
| pe_pb_history 占位 | `engine/backtest/engine.py` | 205-206 | ❌ 空 DF |
| RiskChecker 跳过 | `engine/backtest/engine.py` | 253-254 | ❌ |
| stock_info 退市过滤 | `engine/backtest/engine.py` `_get_stock_info_at` | 330-340 | ❌ 不过滤 delist_date |
| ValueStrategy 历史分位 | `engine/strategies/value.py` `_compute_historical_percentile` | 127-159 | ⚠️ 样本稀疏 |
| MomentumStrategy rs_6m | `engine/strategies/momentum.py` | — | 依赖 index_adj_prices |
| TrendStrategy 因子 | `engine/strategies/trend.py` | — | MA 5/10/20/60 ✅ |
| ReversionStrategy 因子 | `engine/strategies/reversion.py` | — | RSI 14, BB 20/2 ✅ |
| Scorer 权重归一化 | `engine/scorer.py` | 76-78 | ✅ |
| PositionSizer 三态系数 | `engine/position.py` `PositionConfig` | — | ✅ |
| RiskChecker 链路 | `engine/risk.py` `RiskChecker.check` | — | ✅ |
| MarketStateEngine 暖启动 | `engine/market_state.py` | valid_mask | ✅ |
| MarketStateEngine 防抖 | `engine/market_state.py` | — | ✅ |
| UniverseFilter | `engine/universe.py` `filter` | — | F-1~F-8 |
| F-2 交易日换算 | `engine/universe.py` | 66-71 | ✅ |
| WAC 计算 | `services/account_service.py` `compute_wac` | 22-42 | ✅ |
| 分红记录 | `services/account_service.py` `record_dividend` | 376 | ⚠️ 双重计算疑虑 |
| BacktestService 加载 | `services/backtest_service.py` `_load_data_bundle` | 158-280 | — |
| is_st 硬编码 | `services/backtest_service.py` | 209-211 | ❌ |
| 列表日补全 | `services/backtest_service.py` | 217 | DEFAULT 2000-01-01 |
| ScoringService 闰年 bug | `services/strategy_service.py` | 208 | ⚠️ ValueError |
| 轻量快照加载 | `services/strategy_service.py` `_build_filter_snapshot` | 182-199 | ✅ Phase 4 优化 |
| MarketSnapshot 构造 | `services/strategy_service.py` `_build_market_snapshot` | 201-261 | ✅ |
| SignalService 风险检查 | `services/signal_service.py` | 401-406 | ❌ 漏传 max_drawdown_pct |
| BLOCK 信号过滤 | `services/signal_service.py` `save` | — | ✅ |

### B.2 DailyPipeline 检查点链路

| 检查点 | 文件 | 关键调用 |
|--------|------|---------|
| CP1 行情同步 | `pipeline/daily_pipeline.py` | DataService.run_daily_ingest |
| CP2 评分计算 | `pipeline/daily_pipeline.py` | ScoringService.run_daily_scoring |
| CP3 信号生成 | `pipeline/daily_pipeline.py` | SignalService.generate_for_date |
| Step 4 MTM | `pipeline/daily_pipeline.py` | AccountService.mark_to_market |
| Step 5 Lineage | `pipeline/daily_pipeline.py` | LineageService.write |
| Step 6 通知 | `pipeline/daily_pipeline.py` | NotificationService.send_daily_summary |

### B.3 可疑 / 待二次核查

| 项 | 文件:行 | 待核查 |
|----|---------|--------|
| FIN-HIGH-08 分红双重计算 | `account_service.py:376` | 分红是否进入回测净值；adj_factor 是否含分红 |
| FIN-MED-12 策略参数硬编码 | `engine/strategies/*.py` | dataclass 是否真消费 |
| FIN-MED-11 PE/PB 分位采样 | `engine/strategies/value.py:127` | 样本量是否足以做分位估计 |


