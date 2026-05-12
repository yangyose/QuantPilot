# QuantPilot V1.5 路线图

> **文档类型：** V1.5 范围与主题规划设计文档
> **版本：** v1.1
> **创建日期：** 2026-04-30
> **范围来源（四合一）：**
> ① SDD §16 V1.5 产品功能（14 项产品需求）
> ② `docs/reviews/v1_overall_review_2026-04-27.md` V1.0 整体评审 P2/P3 推迟项（25 项质量改进）
> ③ `docs/reviews/phase10_design_review_2026-04-20.md` Phase 10 评审推迟项（3 项）
> ④ `docs/reviews/SDD_review_outside_2026-04-22.md` SDD 外部专家评审合入项（8 项策略/数据/风控强化）
> **配套文档：** V1.0 上线前整改批次（P0/P1）见 `v1_overall_review_2026-04-27.md` §3.1/3.2，本文档 §7 仅落实其文档同步责任。

## 修订历史

| 版本 | 日期 | 变更 |
|------|------|------|
| **v1.0** | 2026-04-30 | 初版创建（方案 A）：合并 SDD §16 14 项产品功能 + V1.0 评审 P2/P3 25 项 + Phase 10 评审 G-1/G-2/G-4 共 42 项，按 10 主题打包；归类为设计文档（原 `docs/reviews/v1_5_roadmap_2026-04-30.md` 已废弃）|
| **v1.1** | 2026-04-30 | 合入 SDD 外部专家评审（`docs/reviews/SDD_review_outside_2026-04-22.md`）：8 项 SDD-EXT-* 进入 V1.5（V1.5-A 4 项 / V1.5-C 2 项 / V1.5-D 2 项），3 项推迟 V2.0；新增 §3 SDD 外部评审合入项 + §5 推迟 V2.0 项；§4 Phase 10 评审章节由 §3 后移；§6 V1.5 主题包估算上调至 ~91-115 pd（+8 项；外部评审建议 #5"信号强度→非线性仓位"在已确认决策中删除，并入分数凯利不单独立项）|
| **v1.2** | 2026-05-12 | 合入 V1.0 真机验收（2026-05-11~12 生产 Docker + 真实 Tushare 端到端）推迟项：新增 §2.9 4 项 RM-13/15/16/17（deposit 幂等 / dividend API 名 / is_st PIT / 评分退化）；§0 总览合计 50→54、估算 91-115 pd → 94-120 pd；修复顺序约束 RM-15→RM-16→重灌→RM-17 记录在 §2.9 |

---

## 编号与治理说明

- **本文档为 V1.5 范围归集设计文档**——其编辑目的就是将分散在 SDD、评审报告中的 V1.5 项归为单一权威清单。
- **辅助追溯标识保留**：本文档在"评审依据"列引用 `v1_overall_review_2026-04-27.md` 中已正式定义的 FIN-/S-/D- 编号、`phase10_design_review_2026-04-20.md` 中的 G-1/G-2/G-4 编号、`docs/reviews/SDD_review_outside_2026-04-22.md` 中的章节锚点（§3.1/§5.1 等），以保持向上游评审报告的可追溯。本文档原创定义 **SDD-EXT-NN** 编号承载 SDD 外部评审项（NN 为顺序号；后缀 `s` = 简化版 V1.5；`f` = 完整版推迟 V2.0）。CLAUDE.md §10 关于"评审编号不入设计文档"的治理规则**针对**：评审编号污染 SDD/system_design/phase 设计文档导致读者无法追溯；本文档显式承担"V1.5 范围归集"角色，编号在头部已标明源文档，不构成治理规则禁止的场景。
- **主题级编号 V1.5-A..J 为本文档原创定义**——当 V1.5 启动时，每个 V1.5-* 主题打包成对应 phase 设计文档；其他设计文档引用 V1.5 范围时，使用 V1.5-A..J 主题名（在本文档 §6 有正式定义），**不应**直接引用 FIN-/S-/D-/SDD-EXT- 编号。

---

## 0. 总览

| 类别 | 数量 | 来源 |
|------|------|------|
| 产品功能（SDD §16）| 14 | `QuantPilot_SDD.md` §16 V1.5 路线图 |
| V1.0 评审推迟（P2）| 19 | `v1_overall_review_2026-04-27.md` §3.3 |
| V1.0 评审推迟（P3）| 6 | `v1_overall_review_2026-04-27.md` §3.4 |
| SDD 外部评审合入 | 8 | `docs/reviews/SDD_review_outside_2026-04-22.md`（SDD-EXT-01/02s/03/04/06s/07/08/09s）|
| Phase 10 评审推迟 | 3 | `phase10_design_review_2026-04-20.md`（G-1/G-2/G-4）|
| V1.0 真机验收推迟 | 4 | 本文档 §2.9（RM-13/15/16/17）|
| **V1.5 合计** | **54** | |
| 推迟 V2.0 项（外部评审）| 3 | SDD-EXT-02f / 06f / 09f（详见 §5）|

V1.5 主题划分（10 主题）见本文档 §6，估算合计 **~94-120 pd（6-8 个月）**（含 §2.9 真机验收 4 项 ~3-5.5 pd）。

---

## 1. 产品功能（SDD §16 V1.5）

来自 `docs/spec/QuantPilot_SDD.md` §16 "版本路线图 → V1.5 专业增强"表，共 14 项。

| 模块 | 描述 | SDD 锚点 | 所属主题 |
|------|------|---------|---------|
| 资金动向策略 | 主力资金净流入、北向资金变化（数据源 V1.5+ 接入）| §7.3、§4.x（数据扩展）| V1.5-C 策略扩展 |
| 低波动策略 | 低历史波动率、低 Beta 标的筛选（A 股低波动异象） | §7.3、占位文件 `engine/strategies/low_volatility.py` | V1.5-C 策略扩展 |
| 策略插件沙箱 | L3 用户自定义策略，受限运行环境 | §7.3、§15.2、占位文件 `engine/strategies/plugin_runner.py` | V1.5-C 策略扩展 |
| 因子监控自动降权 | 因子衰减严重时自动降低对应策略权重（升级自 V1.0 监控展示） | §7.4 V1.5 行为 | V1.5-C 策略扩展 |
| 分数凯利模型 | 基于胜率与盈亏比的分数凯利仓位计算 | §8.x、附录 C.3 | V1.5-D 仓位与风控 |
| 移动止损 | 从持仓最高盈利回撤 ≥ Y%（默认 15%）触发止损 | §9.x | V1.5-D 仓位与风控 |
| 时间止损 | 持有 N 个交易日（默认 30 日）后仍无盈利则提示 | §9.x | V1.5-D 仓位与风控 |
| 市值风格监控 | 大/中/小盘持仓占比监控 + 偏离提示 | §10.x | V1.5-D 仓位与风控 |
| 滑点敏感性分析 | 基于流动性动态调整滑点 + 回测多滑点情景对比 | §11.x | V1.5-A 回测引擎深化 |
| 因子归因（多因子回归）| OLS 回归分解收益至各因子贡献 | §12.3 | V1.5-E 绩效归因深化 |
| 行业归因 | 行业配置偏离基准的超额收益分解 | §12.x | V1.5-E 绩效归因深化 |
| 邮件渠道 | 通知备用渠道（与 V1.0 站内信/微信并列） | §13.x | V1.5-F 通知与配置 |
| L3 权重自定义 | 用户可调策略权重 / 因子权重 | §14、附录 B L3 项 | V1.5-F 通知与配置 |
| 配置版本管理 | 多版本配置历史管理（与现有 user_config_history 表升级） | §14 | V1.5-F 通知与配置 |

> 说明：完整因子级溯源（SDD §15.6）= 评审推迟项 S1-GAP-01，统一在 §2 / V1.5-B 主题下登记，避免重复。

---

## 2. V1.0 评审推迟项（P2 + P3）

来自 `docs/reviews/v1_overall_review_2026-04-27.md` §3.3（19 项 P2）+ §3.4（6 项 P3），按主题归集。

### 2.1 回测引擎深化（5 项 P2）→ V1.5-A

V1.0 完成 P0+P1 修复后回测可信度建立基线，V1.5 在此基线上深化。

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 | 文档同步 |
|---------|------|---------|---------|------|---------|
| FIN-MED-12 | 4 策略 lookback 参数硬编码（rolling 5/10/20/60、RSI 14、BB 20/2 写死，dataclass 仅作 Pipeline 快照登记） | 真实参数下沉到 `Strategy._compute` 需重构 4 个策略类 + 配套测试 | dataclass 真消费实现完成；Settings 中改窗口期能真实生效 | 2 pd | SDD §10 / system_design.md §3 / phase4_factor_engine.md / CLAUDE.md §6 |
| FIN-MED-11 | ValueStrategy PE/PB 历史分位采样过疏（一年 4-5 个公告点） | 需切换到 daily_basic 日度 PE/PB 数据源 | DataValidator 接入；测试覆盖样本量 ≥ 1250（5 年 × 250） | 1.5 pd | phase4_factor_engine.md / SDD §10.4 |
| S1-GAP-01 | 因子级溯源缺失（LineageService 仅返回 composite_score → strategy_score，不返回因子层细节） | 需 LineageService 重构 + 数据库 schema 扩展 + 前端 SignalLineageView 接入 | 用户问"为什么 BUY"时可视化展示 MA60、MACD hist、PE 分位等 | 3 pd | SDD §15.6 / system_design.md §5 / phase7_pipeline.md |
| S1-GAP-02 | 缺 strategy_version 字段（无法区分相同策略不同配置参数下的回测对比） | 数据库 schema 变更 + UI diff 视图 | BacktestTask + PipelineRun 含 strategy_version 字段；前端展示 diff | 1 pd | system_design.md §4 / phase8_backtest.md |
| S6-GAP-02 | BacktestEngine 内存累积 O(N×T)（V1.0 单组合可承受，V1.5 多组合并行需流式写 DB） | 多组合并行回测启用前的硬条件 | BacktestEngine 改为流式持久化 daily_positions | 1.5 pd | phase8_backtest.md §3.3 |

> S1-GAP-01 和 D1-GAP-02 共同对应 V1.5-B 因子级溯源主题，详见 §2.6。

### 2.2 可观测性（5 项 P2）→ V1.5-H

从"靠日志被动排查"升级为"指标+告警+可视化主动监控"。

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 | 文档同步 |
|---------|------|---------|---------|------|---------|
| S5-GAP-01 | 无 Prometheus / OpenTelemetry 指标 | 需引入 prometheus_client 依赖 + 关键业务指标埋点 | 信号数 / Pipeline 时长 / Tushare QPS / 回测排队数 等可在仪表盘查询 | 2.5 pd | SDD §15.5 / system_design.md §10 / phase10_deployment.md |
| S5-GAP-02 | 调度器健康端点缺失（APScheduler 调度状态未暴露） | 需新增 `/health/scheduler` 端点 + 前端展示 | jobs 列表 / 下次运行时间 / 失败计数 可见 | 0.5 pd | system_design.md §6 / phase10_deployment.md / docs/guides/deployment.md |
| S5-GAP-03 | 日志中潜在敏感词无 SecretFilter 中间件 | logging_config.py 增加 SecretFilter | TUSHARE_TOKEN / JWT 等敏感词不会出现在 console/file 日志 | 0.5 pd | core/logging_config.py + 单元测试 / phase10_deployment.md |
| S2-GAP-01 | 数据质量监控指标缺失（DataValidator 错误数量未持久化） | 需新增 DataQualityMetric 表 + 因子监控接入 | 仪表盘可见过去 30 天每天 PIT 违规数 | 1.5 pd | system_design.md §4 / phase2_data_pipeline.md |
| D4-GAP-03 | 无监控/告警生产配置 | 与 S5-GAP-01 配套（Grafana / Prometheus 部署模板） | docker-compose.prod.yml 增 monitoring stack | 1 pd | docs/guides/deployment.md §8 |

### 2.3 多账户 + 权限粒度（3 项 P2）→ V1.5-G

V1.0 单管理员模型（SDD §3.3 范围内）；V1.5 自然延伸到多角色。

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 | 文档同步 |
|---------|------|---------|---------|------|---------|
| S4-GAP-03 | 无 API rate limit / brute force 防护 | 引入 SlowAPI / fastapi-limiter（Redis 已就绪） | 登录端点失败次数限制；敏感端点 QPS 限流 | 1 pd | SDD §3.3 / phase10_deployment.md |
| S4-GAP-02 | 密码无到期 / 强制更换策略 | 个人版可接受，多账户上线时再做 | 与 S4-GAP-01 联动；Setting 中可配 | 0.5 pd | SDD §3.3 |
| S4-GAP-01（联动延伸）| 多账户 / 权限粒度（SDD §3.3 V1.5 路线明确） | V1.0 单管理员是 SDD 明确范围；V1.5 数据库 schema 大改 | User/Role 表设计；JWT 含 role 字段；端点 RBAC | 6-8 pd | SDD §3.3 / system_design.md §4 / phase1_infrastructure.md（再版）|

### 2.4 性能与扩展性（3 项 P2）→ V1.5-I

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 | 文档同步 |
|---------|------|---------|---------|------|---------|
| S6-GAP-01 | 集成测试 DB 单进程串行（CLAUDE.md §4 已注明）| 改 Docker per-test schema 隔离 | 集成测试可并发，CI 时长下降 ≥ 30% | 2 pd | CLAUDE.md §4 / phase1_infrastructure.md |
| S6-GAP-03 | Tushare 限流靠 Semaphore，无熔断退避 | 需引入 tenacity 或自实现指数退避 | Tushare 503 时退避重试，超过阈值后熔断 | 1 pd | data/adapters/tushare.py / SDD §13.1 / phase2_data_pipeline.md |
| S3-HIGH-02 | `data_service.fetch_*_metadata` 静默降级到 raw["is_up_to_date"]=False（无 logger） | 加 `logger.warning(..., exc_info=True)` | 调用方可区分 Tushare 503 / SQL / 网络异常 | 0.3 pd | services/data_service.py + 单元测试 |

### 2.5 测试体系增强（1 项 P2）→ V1.5-J

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 | 文档同步 |
|---------|------|---------|---------|------|---------|
| S7-GAP-03 | 集成测试只跑 happy-path，无故障注入 | 需要 mock Tushare 503 / CP1 失败 / 网络中断等场景 | DataService / DailyPipeline 至少各 3 个故障注入 case | 1.5 pd | phase2_data_pipeline.md / phase7_pipeline.md / CLAUDE.md §5 |

### 2.6 UX 增强（2 项 P2）→ V1.5-B / V1.5-J

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 | 所属主题 |
|---------|------|---------|---------|------|---------|
| D1-GAP-02 | SignalCard 不展示评分决策路径 | 与 S1-GAP-01 同源（依赖因子级溯源数据） | SignalCard 可展开看到 MA60=0.85 → 历史 80% 分位等具体值 | 1.5 pd | V1.5-B（与 S1-GAP-01 同主题）|
| D1-GAP-03 | 无错误重试 / 网络中断兜底 | 需统一 axios 拦截器 + toast + retry 按钮 | 弱网或 503 时用户看到友好提示与操作指引 | 1 pd | V1.5-J UX/合规 |

文档同步：phase9_frontend.md §3.1 / §6.2 / §7.4。

### 2.7 文档同步（3 项 P2）→ V1.5-J

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 |
|---------|------|---------|---------|------|
| D3-GAP-01 | SDD §10 因子参数与策略源码不一致 | 与 FIN-MED-12 同源；策略参数下沉后同步 | SDD §10 表与策略 dataclass 一致 | 0.3 pd |
| D3-GAP-03 | 部署指南未提及单进程约束 | deployment.md §7 增"⚠️ uvicorn 必须 --workers 1" | 运维误改 workers 不再发生 | 0.2 pd |
| D3-GAP-04 | 既有 phase 评审中"推迟到 V1.5"项缺统一汇总 | **本文档创建即解决** | — | 0 pd（本文档）|

### 2.9 V1.0 真机验收推迟项（4 项）→ 各主题分散

来自 2026-05-11~12 真机端到端验收（生产 Docker + 真实 Tushare）。核心通路 11 个 bug 已修，剩余 4 项因依赖大或验收标准未定推迟。

| 编号 | 描述 | 推迟原因 | 修复条件 | 估算 | 所属主题 |
|------|------|---------|---------|------|---------|
| RM-13 | Wizard Step 3 初始资金 deposit 不幂等：用户三连点导致 3× 入金 | 需 idempotency_key 机制设计（前端 UUID + 后端去重表）| `/account/deposit` 接受 `idempotency_key`；24h 内同 key 返回首次结果；前端 Wizard 自动注入 | 0.5-1 pd | V1.5-J UX/合规 |
| RM-15 | `fetch_dividend_data` Tushare API 名错误：调用返回"请指定正确的接口名" | 需查 Tushare 实际接口名（`dividend` vs `pro.dividend` vs `cashflow`）+ 字段映射重对 | adapter 切到正确接口；定增/送股/分红字段单独入 `dividend` 表；DailyPipeline Step 5 自动分红入账验证 | 0.5-1 pd | V1.5-A 回测引擎深化（与 FIN-MED-11 PIT 数据扩展一并） |
| RM-16 | `daily_quote.is_st` 全部 FALSE：namechange 历史回填的 PIT 映射未应用到 daily_quote upsert | st_map 构造正确但 `ingest_history` 在 ingest_daily 之前才注入 `_st_codes`，需校验链路是否真生效 | INT-DATA-** 集成测试构造已知 ST 历史 → 回填后 daily_quote.is_st 与历史 namechange 一致；冒烟 API 抽样验证 | 1-1.5 pd | V1.5-A（与 FIN-MED-11 同主题）/ V1.5-J 测试加固 |
| RM-17 | 评分退化：ROE NULL 导致 ValueStrategy 跳过价值陷阱过滤 → market_state 空降级为 OSCILLATION → 反转策略主导 → 真机 top 20 信号全部为 ST 股票（分 99.4-99.97） | 链式降级根因排查（前置依赖 RM-15/RM-16/Bug 9 修复后的财务数据重灌）+ 评分质量验收标准未定 | 财务全表重灌（NaN→NULL 已清理 213k 行）后真机评分中 ST 占比 ≤ 5%；MarketState 在 60 天回填后能稳定输出 UP/OSC/DOWN | 1-2 pd（依赖前置完成）| V1.5-A 回测引擎深化（重定义评分质量基线）|

> **修复顺序约束**：RM-15 → RM-16 → 财务数据重灌 → RM-17 验收。RM-13 独立可并行。
> **追溯路径**：Bug 编号与本文档 RM-* 映射、详细症状与诊断链路见 `CLAUDE.md §9` V1.0 真机验收行。

### 2.8 P3 长期改进（6 项）→ 各主题分散

无明确时间窗口，按主题分散。

| 评审依据 | 描述 | 范畴 | 所属主题 |
|---------|------|------|---------|
| S4-GAP-01（V1.5 不做实施）| 单管理员模型 = 无权限粒度 | V1.0 SDD §3.3 范围内可接受；多账户实施在 V1.5-G | V1.5-G |
| D1-GAP-04 | 多视图空状态文案差异化 | UX 一致性优化 | V1.5-J |
| D2-GAP-03 | Tushare 数据使用合规声明 | 个人版影响有限；多人共用 deploy 时考虑 | V1.5-J |
| D4-GAP-01 | 多副本部署模板（K8s） | APScheduler 必须改 SQLAlchemyJobStore | V1.5-I |
| D4-GAP-02 | K8s Helm Chart | 与 D4-GAP-01 同源 | V1.5-I |
| D4-GAP-05 | 备份 SHA256 校验 | backup_db.sh 增加 sha256sum | V1.5-J |

---

## 3. SDD 外部评审合入项（8 项）

来自 `docs/reviews/SDD_review_outside_2026-04-22.md`（机构级量化投资体系视角评审），9 项原始建议中合入 V1.5 的为 8 项（# 5 信号强度→非线性仓位映射在用户决策中删除，并入 V1.5-D 分数凯利不单独立项）；3 项推迟 V2.0 见 §5。

| 编号 | 描述 | 评审优先级 | SDD 现状 | V1.5 实现要点 | 估算 | 所属主题 |
|------|------|-----------|---------|--------------|------|---------|
| SDD-EXT-01 | 趋势策略因子共线性研究与重构（MA + MACD + 突破三因子相关性 >0.8）| P0 | §7.2.1 = 0.4 MA + 0.3 MACD + 0.3 突破 | **第一阶段：实证**——计算三因子在 5 年截面上的相关性矩阵；**第二阶段：重构**——基于实证选定具体形式（候选：(Close-MA60)/ATR + ADX 门、PCA 降维、IC 加权）；**禁止预先锁定形式** | 1.5-2.5 pd | V1.5-A 回测引擎深化 |
| SDD-EXT-02s | 涨停板成交可行性建模（简化版）| P0 | §5.3 仅排除"一字涨停"；§7.7.4 已声明收盘价成交局限 | 回测引擎在信号触发日：当日收盘涨停 + 当日换手率 < 1% → 判定不可成交，资金保留为现金（一字板特征近似） | 0.5-1 pd | V1.5-A 回测引擎深化 |
| SDD-EXT-03 | 业绩预告/快报 PIT 数据接入（与 FIN-MED-11 合并扩展）| P1 | §5.1 仅"财报公告日"为可用时点；A 股年报 1 月预告 / 3-4 月正式有信息真空期 | 接入 Tushare `forecast` / `express`；新增 `data_priority` 字段（正式 3 / 快报 2 / 预告 1）；ValueStrategy 计算 PE(TTM) 时按优先级使用最新可用利润 | 2-3 pd（与 FIN-MED-11 合并） | V1.5-A 回测引擎深化 |
| SDD-EXT-04 | 均值回归策略 Piotroski F-Score 硬性前置过滤 | P1 | §7.2.2 仅技术指标（RSI/乖离率/布林带）；§5.4 universe 级过滤不覆盖策略级 | F-Score 8 项财务指标计算（ROA / ΔROA / CFO / CFO>NI / ΔLeverage / ΔLiquidity / ΔShares / ΔGrossMargin / ΔAssetTurnover）；F-Score < 6 则 mean_reversion_score = 0；金融类（银行/非银）改用 ROE > 5% 替代判断 | 2-3 pd | V1.5-C 策略扩展 |
| SDD-EXT-06s | 行业集中度细化至申万三级行业（SW3）| P2 | §10.2 仅 SW1 ≤ 30%；同 SW1 内可能存在高相关子板块（如白酒同属食品饮料）| 数据层补 `sw_industry_l3` 字段；RiskChecker 新增规则：同 SW3 持仓合计 ≤ 15%（可配置）；保留 SW1 ≤ 30% 双层校验 | 1-1.5 pd | V1.5-D 仓位与风控扩展 |
| SDD-EXT-07 | 市场宽度指标 NH-NL（创 60 日新高 - 新低）| P2 | §6.3 仅 HS300 趋势；结构性行情（如 2024 权重护盘 / 小盘阴跌）易误判 | MarketStateEngine 新增 NH-NL 计算（每日扫描可投资宇宙）；判定规则：ADX>25 + MA20>MA60 + NH-NL≤0% → 降级为"弱势震荡"（压制趋势策略权重） | 1-1.5 pd | V1.5-A 回测引擎深化 |
| SDD-EXT-08 | 动量策略提升为风险调整动量（V1.5 默认行为）| P2 | §7.2.3 排除前 5%（短期反转）；注脚已建议"风险调整动量"作为 L2+ 增强 | 把"涨幅 / 60 日历史波动率"提升为 V1.5 默认动量公式（替代单纯涨幅排名）；原始动量公式保留为 L2+ 可选回退 | 0.5-1 pd | V1.5-C 策略扩展 |
| SDD-EXT-09s | 流动性压力测试（轻量版） | P2 | §10.4 是 V2.0 占位；目前仅指数下跌情景 | RiskChecker 新增检查：持仓数量 / 近 20 日日均成交量 > 3 → 标记"流动性风险组合"；下跌趋势下提示优先减仓 | 1-1.5 pd | V1.5-D 仓位与风控扩展 |

> **决策记录（2026-04-30 用户确认）：**
> 1. SDD-EXT-01 趋势策略重构形式不预先锁定，先做实证再选具体方案。
> 2. 评审建议 # 5（信号强度→非线性仓位 5%/10%/15%）**删除**：与 V1.5-D 分数凯利方法重叠（凯利已含"信号强度越高越接近 0.5 倍凯利"），不做中间态。
> 3. SDD-EXT-08 风险调整动量从 SDD §7.2.3 注脚的"L2+ 可选"提升为 V1.5 默认行为；L2+ 改为"可选回退到原始动量"。

---

## 4. Phase 10 评审推迟项（3 项）

来自 `docs/reviews/phase10_design_review_2026-04-20.md`（同步至 system_design.md v1.8 修订记录）。

| 评审依据 | 描述 | 推迟原因 | 修复条件 | 估算 | 所属主题 |
|---------|------|---------|---------|------|---------|
| G-1 | WS 前端消费推迟（Backtest WS 后端已实装但前端推迟；Pipeline WS 后端未实装） | V1.0 范围聚焦后端配置消费链路与生产部署 | 前端 WebSocket 客户端 + 状态管理；Pipeline WS 后端实装 | 1-2 pd | V1.5-J UX/合规 |
| G-2 | AKShare 自动降级（`akshare.py::AKShareAdapter` 已存在但 DataService 未接降级路径）| Tushare → AKShare 路由策略需稳态 + 单测 | DataService.fetch_* 在 Tushare 503 时自动切 AKShare，单测覆盖 | 1-1.5 pd | V1.5-I 性能扩展（与 S6-GAP-03 限流退避同类）|
| G-4 | 多账户 UI 切换 | 与 S4-GAP-01 同源；多账户后端先行，前端后接 | 顶部账户切换器 + 各视图按 account_id 过滤 | 0.5 pd | V1.5-G 多账户 |

---

## 5. 推迟 V2.0 项（3 项）

SDD 外部评审中工程复杂度高 / 数据源依赖大的子项推迟到 V2.0；登记于此避免 V1.5 启动时遗漏，V2.0 规划时统一并入。

| 编号 | 描述 | 推迟原因 | 完整版修复条件 | 估算 |
|------|------|---------|---------------|------|
| SDD-EXT-02f | 涨停板成交完整版（首次封板时间 + 封单强度 / 当日成交额）| Tushare Pro 标准套餐**不含** `first_limit_time` / `limit_up_vol` 字段，需 Level-2 数据源；V1.5 用 SDD-EXT-02s 简化版替代 | 数据源升级到含 Level-2 的套餐；新增 `first_limit_time` / `limit_up_vol` 字段；规则：首次封板时间 ≤ 14:00 或 封单/成交额 > 0.5 → 判定不可成交 | 3-4 pd |
| SDD-EXT-06f | 边际 VaR 风险贡献分析 | 需协方差矩阵估计 + 历史窗口选择 + 数值稳定性处理；与 SDD §10.2 V2.0 已有"因子暴露 / 相关性分析"占位同质 | 协方差估计实装；RiskChecker 在产生买入信号时计算边际 VaR；增幅 > 20% → 自动减半本次建议仓位 | 3-5 pd |
| SDD-EXT-09f | 因子拥挤度告警 | 需协方差/暴露估计；2021 核心资产崩盘类风险量化建模复杂；V1.5 已有 SDD-EXT-09s 流动性压力作为轻量替代 | 计算持仓在价值因子（PE/PB 分位）和动量因子上的加权暴露分位；任一 > 90% 历史分位 → "风格拥挤"告警 | 3-4 pd |

> **V2.0 总计新增推迟项 ~9-13 pd**——V2.0 规划时与 SDD §10.4（压力测试）+ §10.2（因子暴露 / 相关性 V2.0 占位）整合。

---

## 6. V1.5 主题打包（10 主题，~91-115 pd）

| 主题 | 包含项 | 优先级 | 估算（pd）| 时间窗口 |
|------|-------|--------|----------|---------|
| **V1.5-A 回测引擎深化** | §2.1 全部 5 P2 + §1 滑点敏感性分析 + §3 SDD-EXT-01/02s/03/07（4 项 SDD 外部评审）| 🔴 最高 | **16-22** | M+1 |
| **V1.5-B 因子级溯源** | S1-GAP-01 / S1-GAP-02 / D1-GAP-02 + §1 完整因子级溯源（同 S1-GAP-01）| 🟡 高 | 5-8 | M+2 |
| **V1.5-C 策略扩展** | §1 资金动向 + 低波动 + 插件沙箱 + 因子自动降权 + §3 SDD-EXT-04/08（F-Score + 风险调整动量）| 🟡 高 | **14.5-21** | M+3~M+4 |
| **V1.5-D 仓位与风控扩展** | §1 分数凯利 + 移动止损 + 时间止损 + 市值风格 + §3 SDD-EXT-06s/09s（SW3 + 流动性压力）| 🟢 中 | **7-10** | M+3 |
| **V1.5-E 绩效归因深化** | §1 多因子回归 + 行业归因 | 🟢 中 | 5-7 | M+4 |
| **V1.5-F 通知与配置** | §1 邮件 + L3 权重 + 配置版本 | 🟢 中 | 4-7 | M+4 |
| **V1.5-G 多账户 + 权限** | §2.3 全部 3 P2 + §4 G-4 | 🟢 中 | 8-10 | M+3~M+4 |
| **V1.5-H 可观测性** | §2.2 全部 5 P2 | 🟡 高 | 5-7 | M+2 |
| **V1.5-I 性能扩展** | §2.4 全部 3 P2 + §4 G-2 + §2.8 D4-GAP-01/02 | 🟢 中 | 6-10 | M+3 |
| **V1.5-J UX/合规/测试/文档** | §2.5 / §2.6 D1-GAP-03 / §2.7 / §2.8 D1-GAP-04/D2-GAP-03/D4-GAP-05 + §4 G-1 | 🟢 低 | 8-12 | 持续 |
| **合计** | **50 项** | | **~91-115 pd** | **6-8 个月** |

---

## 7. V1.0 整改批次的文档同步责任表 ★

> 为防止 V1.0 三批 P0/P1 修复时遗漏文档同步，每批改动须对照下表更新对应文档。
> 文档清单按 CLAUDE.md §10 治理规则筛选——`v1_overall_review_2026-04-27.md` 中的 FIN-/S-/D- 编号**仅**保留在评审报告与本路线图，不进入 SDD/system_design/phase 设计文档正文。

### 7.1 Batch 1 — 合规链条 P0（4 项，~1.5 pd）

| 子任务 | 主要代码改动 | 文档同步责任 |
|--------|-------------|-------------|
| B1-1 重写 DISCLAIMER | `engine/backtest/report.py:11-15` | phase8_backtest.md §3.6 BacktestReport 章节 |
| B1-2 BacktestView 局限 banner | 新建 `frontend/src/components/BacktestLimitationsBanner.vue` + BacktestView.vue | phase9_frontend.md §6.6 BacktestView / §7（新增组件章节）|
| B1-3 三视图加 DisclaimerBanner | SignalsView / DashboardView / ReportsView | phase9_frontend.md §6.1/6.2/6.5 |
| B1-4 SDD §7.7 增 V1.0 局限 | `docs/spec/QuantPilot_SDD.md` §7.7.4 | SDD 直接修改；CLAUDE.md §10 进度表追加修订记录 |

**收尾必检：**
- [ ] SDD §7.7.4 含"V1.0 已知局限"小节（4 条）
- [ ] phase8_backtest.md §3.6 与 SDD §7.7.4 一致
- [ ] phase9_frontend.md §6.1/6.2/6.5/6.6 含 DisclaimerBanner / Limitations banner 接入说明
- [ ] CLAUDE.md §9 进度表 V1.0 状态行追加"V1.0 整改批次完成"标记（Batch 全部完成时）

### 7.2 Batch 2 — 实盘风控 + UX P1（6 项，~2 pd）

| 子任务 | 主要代码改动 | 文档同步责任 |
|--------|-------------|-------------|
| B2-1 CP3 传 max_drawdown_pct | `services/signal_service.py:_run_risk_checks` | phase5_signals.md §4.3 RiskChecker 调用 / phase7_pipeline.md §4 CP3 章节 |
| B2-2 record_dividend 排查 | `services/account_service.py:376` 注释 / 可能逻辑调整 | phase6_account.md §3 AccountService.record_dividend 章节 + 设计决策记录 |
| B2-3 闰年 bug | `services/strategy_service.py:208` | phase4_factor_engine.md §（_build_market_snapshot 章节）|
| B2-4 LoginView 合规声明 | `frontend/src/views/LoginView.vue` | phase9_frontend.md §6（LoginView 章节，若无则新增）|
| B2-5 HTTPS 警示 | `nginx/nginx.prod.conf` 注释 + `docs/guides/deployment.md` §2 | docs/guides/deployment.md §2 / phase10_deployment.md §8.2 |
| B2-6 测试 | unit 分红场景 + 集成 max_drawdown 触发 + 闰年 | phase4/5/6/7 各 phase 设计文档 §测试策略章节追加用例编号 |

**收尾必检：**
- [ ] phase5_signals.md §4.3 与 signal_service.py 调用参数一致
- [ ] phase6_account.md §3 record_dividend 含 cost_price 语义说明
- [ ] phase4_factor_engine.md 含闰年降级处理说明
- [ ] phase9_frontend.md 含 LoginView 合规脚注章节
- [ ] docs/guides/deployment.md §2 含公网 HTTPS 红色警示

### 7.3 Batch 3 — 回测引擎重构 P0+P1（10 项，~8-10 pd）

**这是最大的文档同步面**。phase8_backtest.md 几乎全章需要更新。

| 子任务 | 主要代码改动 | 文档同步责任 |
|--------|-------------|-------------|
| B3-1 quotes_t 全量字段 | `BacktestService._load_data_bundle` + `BacktestDataBundle` + `_get_quotes_at` | phase8_backtest.md §2.2（依赖表）/ §3.2（接口）/ §3.3（主流程）|
| B3-2 T+1 撮合 | `BacktestEngine.run` 信号产生与撮合分离 + `BacktestConfig.execution_price` | phase8_backtest.md §3.2 / §3.3 / §3.4 / SDD §7.7.2（接口契约新增字段） |
| B3-3 pe_pb_history / index 真实切片 | `BacktestService._load_data_bundle` + 主循环切片 | phase8_backtest.md §2.2 / §3.3 |
| B3-4 调用 RiskChecker | 主循环构造虚拟账户上下文 + 调 RiskChecker | phase8_backtest.md §3.3 / §3.5（与实盘一致性）/ SDD §10.2 |
| B3-5 is_st/is_suspended 时点切片 | `BacktestService` + `_get_stock_info_at` | phase8_backtest.md §2.2 / §3.3（PIT 切片说明）|
| B3-6 过滤 delist_date | `_get_stock_info_at` | phase8_backtest.md §3.3（生存者偏差章节）|
| B3-7 financials_history 传入 | `BacktestEngine` 维护切片函数 | phase8_backtest.md §3.3 / phase4_factor_engine.md（UniverseFilter F-5 真实启用）|
| B3-8 走 DataValidator | `_load_data_bundle` 调 `validate_daily_quotes` | phase8_backtest.md §2.2 / phase2_data_pipeline.md（DataValidator 跨 phase 复用说明）|
| B3-9 8 处吞异常合规化 | `engine/backtest/engine.py` 5 处 logger.debug + 3 处 pass 改 logger.exception/warning | CLAUDE.md §6（追加经验）/ phase8_backtest.md §3.3 |
| B3-10 测试补齐（6+ 集成 case）| `tests/integration/test_int_backtest_engine.py` 新增 6+ 场景 | phase8_backtest.md §7.3 / §9.2（DoD）|

**收尾必检：**
- [ ] phase8_backtest.md 修订历史新增 v1.x 记录（覆盖 §2/§3 主要章节）
- [ ] phase8_backtest.md §3.3 与代码主循环顺序一致（数据切片 → universe → 评分 → 信号 → **撮合在 T+1** → 风控 → 净值）
- [ ] SDD §7.7.2 接口契约新增 `execution_price` 字段
- [ ] SDD §7.7.4 移除"V1.0 已知局限" 4 项中已修复者（Batch 3 完成后）
- [ ] CLAUDE.md §6 追加"BacktestEngine 异常吞嗯禁止 + 撮合规则 T+1"经验
- [ ] CLAUDE.md §9 V1.0 进度表标 Batch 3 完成

---

## 8. 路线图维护规则

1. **本文档为 V1.5 启动前的唯一权威 scope 清单**——所有"推迟到 V1.5"陈述应回链此文档。SDD §16 / system_design.md §9 / phase 设计文档若需引用 V1.5 范围，使用主题级编号 V1.5-A..J（在 §6 有正式定义）。
2. **每完成一项 V1.5 修复**，从本文档对应章节移除条目，并在 system_design.md / SDD 对应章节注明完成日期；若主题全部完成，§6 主题表整行删除。
3. **V1.5 启动时**创建 `docs/design/phases/v1_5/` 目录，将本文档 §6 主题打包转换为对应 phase 设计文档（V1.5-A.md / V1.5-B.md ……），新 phase 设计文档**不再**引用 FIN-/S-/D-/SDD-EXT- 评审编号，改用主题编号 + 描述。
4. **新增 V1.5 范围项**（无论来自新评审、SDD 增补、还是用户反馈）须先回写本文档对应主题，再进入实施阶段。
5. **本文档是设计文档**：可被 system_design.md / SDD 修订历史中引用（v1.x 修订记录可链此文档），但其他设计文档正文中**不应**展开本文档 §1-§4 的具体编号，仅引用 §6 主题级编号。

---

## 附录 A：与既有文档的对应关系

| 信息源 | 包含内容 | 与本文档关系 |
|--------|---------|------------|
| `docs/spec/QuantPilot_SDD.md` §16 V1.5 | 14 项产品功能模块 | 本文档 §1 完整复盘 |
| `docs/spec/QuantPilot_SDD.md` §3.3 / §15.2 / §15.6 | V1.5 多账户 / 插件沙箱 / 因子级溯源占位 | 本文档 §1 / §2.3 / §2.6 已纳入 |
| `docs/reviews/v1_overall_review_2026-04-27.md` §3.3 / §3.4 | V1.0 整体评审 P2/P3 全清单（19 P2 + 6 P3）| 本文档 §2 完整归集 |
| `docs/reviews/v1_overall_review_2026-04-27.md` §3.1/3.2 | V1.0 P0/P1 清单（8 + 12 项）| 本文档 §7 文档同步责任表覆盖 |
| `docs/reviews/v1_overall_review_2026-04-27.md` §8 | V1.5 路线图（7 主题，~50 pd） | 已被本文档 §6（10 主题，~91-115 pd）扩展替代 |
| `docs/reviews/phase10_design_review_2026-04-20.md` G-1/G-2/G-4 | Phase 10 评审推迟 3 项 | 本文档 §4 完整纳入 |
| `docs/reviews/SDD_review_outside_2026-04-22.md` 9 项 SDD 外部专家评审 | 策略/数据/风控强化建议 | 本文档 §3 合入 8 项（SDD-EXT-01/02s/03/04/06s/07/08/09s）；§5 推迟 3 项至 V2.0（SDD-EXT-02f/06f/09f）；建议 #5 已确认删除（并入分数凯利）|
| `docs/design/system_design.md` v1.8 修订记录 | 已对 G-1/G-2/G-4 同步 | 本文档作为详细范围说明被 §9 注释引用 |
| `docs/design/system_design.md` §9 注释 | V1.5 文件清单（low_volatility.py / plugin_runner.py） | 本文档 §1 已收录"占位文件"列；冗余信息保留双向 |
| `memory/MEMORY.md` 各 phase 关键经验 | phase4/6/7/9/10 推迟项与修复要点 | 本文档不重复，仅在条目"推迟原因"中按需引用 |

## 附录 B：评审编号 → V1.5 主题速查

仅用于追溯评审报告中编号对应的 V1.5 主题，正文请使用主题级编号（V1.5-A..J）。

| 评审编号 | V1.5 主题 |
|---------|----------|
| FIN-MED-11 / FIN-MED-12 / S1-GAP-02 / S6-GAP-02 | V1.5-A 回测引擎深化 |
| SDD-EXT-01 / SDD-EXT-02s / SDD-EXT-03 / SDD-EXT-07 | V1.5-A 回测引擎深化（SDD 外部评审）|
| S1-GAP-01 / D1-GAP-02 | V1.5-B 因子级溯源 |
| SDD-EXT-04 / SDD-EXT-08 | V1.5-C 策略扩展（SDD 外部评审）|
| SDD-EXT-06s / SDD-EXT-09s | V1.5-D 仓位与风控（SDD 外部评审）|
| S5-GAP-01/02/03 / S2-GAP-01 / D4-GAP-03 | V1.5-H 可观测性 |
| S4-GAP-01/02/03 / G-4 | V1.5-G 多账户 |
| S6-GAP-01/03 / S3-HIGH-02 / G-2 / D4-GAP-01/02 | V1.5-I 性能扩展 |
| S7-GAP-03 | V1.5-J（测试） |
| D1-GAP-03/04 / G-1 | V1.5-J（UX） |
| D2-GAP-03 / D4-GAP-05 | V1.5-J（合规/运维）|
| D3-GAP-01/03/04 | V1.5-J（文档）|
