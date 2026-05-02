# Phase 10 设计评审报告

**评审对象**：Phase 10 设计文档 `docs/design/phases/phase10_deployment.md` v1.0
**评审依据**：`QuantPilot_SDD.md`（§11~§16、附录B）、`system_design.md`（§2.7、§3、§5、§6、§9）、Phase 1~9 已交付实现
**评审日期**：2026-04-20
**评审人**：Claude Code
**评审范围**：Phase 10 设计本身 + Phase 1~10 对 SDD / system_design 规划机能的总体覆盖核查
**总体评级**：B+（设计总体扎实，存在 7 项需修订的问题 + 8 项 V1.0 覆盖层面待确认事项）

---

## 1. 总体评价

| 维度 | 评价 |
|------|------|
| **范围完整性** | 5 个工作包（P10-A 通知 / P10-B UserConfig 消费 / P10-C Settings 前端 / P10-D 收尾遗留 / P10-E 部署）清晰划分，与 system_design §9 Phase 10 分配一致 |
| **配置消费闭环** | 11 个 config_key 定义完备，Redis 缓存 + 5min TTL + partial-overlay 默认合并策略明确；pipeline_run.config_snapshot 用于归因追溯的设计合理 |
| **通知降级** | WxPusher 3 重试 + InAppNotification 兜底的降级链清晰，NotificationChannel ABC 接入 system_design §5.10 |
| **Settings 前端** | 三级折叠（基础 / 高级 / 专家）+ OnboardingWizard + YAML 导出满足 SDD §14 与 §15.1 |
| **部署与收尾** | Docker 生产镜像、Alembic 链路、运维 Runbook 覆盖完整 |
| **存在问题** | 7 个 Phase 10 内部问题（2 个 P2、5 个 P3），8 个 Phase 1~10 覆盖层面待确认事项（2 个 P2、6 个 P3） |

**结论**：Phase 10 设计在通知降级、配置消费、Settings 三级折叠三条主线上基本到位；但存在路径命名与 system_design §3 不一致（Q-1）、BacktestEngine 是否接入 UserConfig 未显式化（Q-2）等需澄清的结构性问题；同时 Phase 1~10 总体覆盖中，WebSocket 前端消费端（G-1）、AKShare 备用数据源完整度（G-2）、IC 窗口配置化（G-3）三项需核实落实情况。

---

## 2. Phase 10 设计文档本身的问题

### 2.1 问题清单

| 编号 | 级别 | 位置 | 问题摘要 |
|------|------|------|----------|
| Q-1 | P2 | §3 项目结构 / §4 模块详细设计 | 通知适配器路径 `data/adapters/wxpusher.py` 与 system_design §3 规划的 `notification/` 独立目录不一致 |
| Q-2 | P2 | §4.1 ConfigService + §4.5 BacktestEngine 接入 | BacktestEngine 是否消费 UserConfig（风险参数、回测滑点默认值）未在设计中显式声明 |
| Q-3 | P3 | §2.3 config_keys 表 notification_prefs | 6 个开关项（daily_signals / stop_loss / pipeline_failure / factor_alerts / report_ready / risk_limit_breach）与 SDD §14.4 只有 4 项（信号生成 / 止损触发 / 管道失败 / 月报完成）不一致 |
| Q-4 | P3 | §4.2 NotificationService 降级路径 | WxPusher 3 次重试失败后降级到 InAppNotification 的日志级别未标明；按 CLAUDE.md §6「静默吞异常禁止」应为 ERROR 级 |
| Q-5 | P3 | §5.3 pipeline_run.config_snapshot 写入时机 | 写入时机（CP1 首步 vs 管道启动时 vs 逐 CP 写入）语义未明，归因追溯对"某次回放的参数是管道启动时还是执行时生效的参数"敏感 |
| Q-6 | P3 | §2.3 strategy_weights 键名 | `trend / momentum / reversion / value` 四项权重的键名需与 SDD §7.5 / Phase 4 设计文档 §2 中的策略 ID 逐字对齐 |
| Q-7 | P3 | §2.3 backtest_defaults | 回测默认滑点（slippage_bps）在 SDD 附录 B 默认参数总表中未定义，需补充或引用来源 |

### 2.2 详述

#### Q-1（P2）：通知适配器路径与 system_design §3 不一致

**位置**：Phase 10 §3 项目结构、§4.2 NotificationService 实现路径

**问题**：
- Phase 10 §3 将 WxPusher 适配器放在 `data/adapters/wxpusher.py`
- system_design §3 明确规划 `notification/` 独立目录（含 `base.py`（NotificationChannel ABC）+ `wxpusher.py`）

`data/` 目录在现有代码中专门承载数据采集（Tushare / AKShare / 行情 / 财务），适配器（Adapter）语义在此特指 DataSourceAdapter。将通知渠道塞入 `data/adapters/` 会造成：
1. 语义混乱：NotificationChannel 不是 DataSourceAdapter 的子类
2. 违反 system_design §3 分层
3. 后续若新增 ServerChan / Slack / Email 渠道，在 `data/adapters/` 下继续堆叠会加深错位

**修正方案**：在 Phase 10 §3 将路径改为 `backend/src/quantpilot/notification/wxpusher.py`，并新增 `backend/src/quantpilot/notification/base.py`（NotificationChannel ABC，对应 system_design §5.10）。若确实要改变 system_design 规划，需同步更新 system_design §3 并在 Phase 10 §1.1「依据文档」下说明偏离原因。

---

#### Q-2（P2）：BacktestEngine 是否接入 UserConfig 未显式声明

**位置**：Phase 10 §4.1 ConfigService 消费方列表、§4.5 BacktestEngine UserConfig 接入

**问题**：
- §4.1 ConfigService 消费方列表列出 SignalGenerator / RiskChecker / MarketStateEngine / Scorer / Strategies，但未列 BacktestEngine
- Phase 8 BacktestEngine 目前的滑点 / 手续费 / 初始资金通过构造参数传入，没有从 UserConfig 读取
- Phase 10 §2.3 新增 `backtest_defaults` config_key（起始资金 / 手续费率 / 滑点 bps），却未说明：
  - 谁读取 `backtest_defaults`？是 `/backtest/run` 端点的默认值解析，还是 BacktestEngine 本身
  - 用户在 `POST /backtest/run` 显式传了 initial_cash 时，UserConfig 默认值该被覆盖（这是 partial-overlay 的关键语义）

**影响**：回测的"默认参数"与"本次运行参数"如果语义不清，会导致 Phase 10 §5.3 config_snapshot 记录的是用户本次输入还是 UserConfig 默认值难以追溯。

**修正方案**：在 Phase 10 §4.5 增加一节明确：
1. `POST /backtest/run` 端点在解析请求体时，对未提供的字段用 `config_service.get("backtest_defaults")` 填充
2. BacktestEngine 接收已合并的完整参数（不再访问 UserConfig）
3. 最终参数写入 `BacktestResult.config_snapshot`（如果 Phase 8 设计中有该字段；如无需 Phase 10 补齐）

---

#### Q-3（P3）：notification_prefs 6 项开关与 SDD §14.4 的 4 项不一致

**位置**：Phase 10 §2.3 notification_prefs 定义

**问题**：
- Phase 10 §2.3 列 6 个开关：daily_signals / stop_loss / pipeline_failure / factor_alerts / report_ready / risk_limit_breach
- SDD §14.4 推送设置只列 4 项：信号生成 / 止损触发 / 管道失败 / 月报完成
- SDD §13 提醒与交互 列出 5 类触发事件；SDD §14.4 省略了 "因子质量告警" 与 "风控限额突破"

**影响**：SDD 是权威来源（CLAUDE.md §1 规则），设计文档扩展超出 SDD 范围需显式说明，否则 Phase 10 验收时难以判定哪些是规格内、哪些是扩展。

**修正方案**（任选其一）：
- **方案 A**：Phase 10 §2.3 只保留 SDD §14.4 的 4 项，其余 2 项延后到 V1.5
- **方案 B**：同步更新 SDD §14.4 为 6 项，并在 Phase 10 §1.1 明确标注 "SDD §14.4 已同步至 v1.x"

推荐方案 B，因为 SDD §13 已涵盖这些事件。

---

#### Q-4（P3）：WxPusher 降级日志级别未声明

**位置**：Phase 10 §4.2 NotificationService 降级链

**问题**：设计文档描述"WxPusher 3 次重试失败 → InAppNotification 兜底"，但未说明：
- 3 次失败时的日志级别（WARN / ERROR？）
- InAppNotification 写入成功后是否额外记录 ERROR 日志标识"WxPusher 链路异常"

CLAUDE.md §6 规定：Engine/Service 层 `except Exception` 分支返回降级值时必须 `logger.exception(...)`（不可用 DEBUG）。通知链是 Service 层，应遵循此规则。

**修正方案**：在 §4.2 明确：
- 单次 WxPusher 失败：WARN 级（附重试次数）
- 3 次全部失败并降级到 InAppNotification：ERROR 级（含用户 UID 与消息类型，便于运维追溯）
- InAppNotification 写库失败：ERROR 级（极端降级，需立即关注）

---

#### Q-5（P3）：config_snapshot 写入时机语义模糊

**位置**：Phase 10 §5.3 pipeline_run.config_snapshot

**问题**：设计文档描述"pipeline_run 写入当时生效的 config 快照用于归因追溯"，但未明确：
- **时机 A**：Pipeline 启动（`POST /pipeline/trigger` 或 APScheduler 触发）时 snapshot 当时的 config，整个 Pipeline 运行期间用此 snapshot
- **时机 B**：每个 CP 独立读取 config（可能中途被用户修改）

时机 A 支持回放（重跑某次 Pipeline 必定得同结果），时机 B 更接近实时。两者语义差异很大。

**修正方案**：在 §5.3 明确采用时机 A（启动时一次性 snapshot），并在 Pipeline 启动入口（`DailyPipeline.run_for_date` / `_daily_job` / `POST /pipeline/trigger`）调用 `config_service.get_all_for_snapshot()` 返回完整 dict 写入 `pipeline_run.config_snapshot`，所有 CP 内部从此 snapshot 读取参数而非再次访问 ConfigService。

---

#### Q-6（P3）：strategy_weights 键名需与 Phase 4 对齐

**位置**：Phase 10 §2.3 strategy_weights

**问题**：`{"trend": 0.35, "momentum": 0.25, "reversion": 0.2, "value": 0.2}` 的键名需与 Phase 4 `strategies/` 目录下的策略 ID 精确一致。Phase 4 设计文档 §2 定义的策略 ID 若为 `trend_following` 而非 `trend`，键名失配会导致 `Scorer._load_strategy_weights` 取不到值降级为默认。

**修正方案**：在 §2.3 附注 "键名与 BaseStrategy.strategy_id 逐字对应，参见 Phase 4 设计文档 §2 策略目录表"。评审修订时交叉核对 Phase 4 设计文档。

---

#### Q-7（P3）：backtest_defaults 滑点缺 SDD 依据

**位置**：Phase 10 §2.3 backtest_defaults.slippage_bps

**问题**：SDD 附录 B 默认参数总表列出了大多数参数（MA 窗口 / ADX 阈值 / 换手率阈值 / PE 阈值 / IC 窗口 / 止损 / 资金使用率），但未涵盖回测滑点（slippage_bps）。Phase 10 给出具体数值（如 5 bps）而无 SDD 来源，未来若 SDD 新增该项可能出现冲突。

**修正方案**：要么在 Phase 10 §2.3 注明 "本项在 SDD 附录 B 未定义，按行业惯例 5 bps，待 SDD v1.x 补录"，要么同步更新 SDD 附录 B 新增此项。

---

## 3. Phase 1~10 对 SDD / system_design 覆盖核查

### 3.1 覆盖核查方法

- 对照 `system_design.md §9` V1.0 开发阶段规划表 10 行，逐行核对每个分配模块是否已在对应 Phase 交付或显式推迟
- 对照 `system_design.md §3/§5` 所有模块、`§6` 所有 API 端点，检查是否有孤儿
- 对照 `QuantPilot_SDD.md §11~§16` 功能范围，检查是否有功能在 Phase 1~10 中无归属
- 对照 `QuantPilot_SDD.md §16 V1.0 核心可用` 范围，确认未被推迟到 V1.5 的功能都有 Phase 归属

### 3.2 已覆盖项（节选）

| 类别 | 覆盖情况 |
|------|----------|
| 认证 / 用户（SDD §11） | Phase 1 基础设施 ✓ |
| 数据采集（SDD §3~§5） | Phase 2（Tushare / AKShare 备用 / 日历 / repository / 调度）✓ |
| 市场状态（SDD §6） | Phase 3（ADX/MA 三态 + 防抖动）✓ |
| 因子工程（SDD §7） | Phase 4（8 因子 + filter + Scorer）✓ |
| 信号生成（SDD §8） | Phase 5（SignalGenerator / PositionSizer / RiskChecker）✓ |
| 账户持仓（SDD §9） | Phase 6（WAC + 成交 + 资金流水）✓ |
| Pipeline 与监控（SDD §10、§12） | Phase 7（DailyPipeline / MonthlyScheduler / FactorMonitor / Report）✓ |
| 绩效 / 回测（SDD §12） | Phase 8（PerformanceService + BacktestEngine）✓ |
| 前端仪表盘（SDD §11 交互） | Phase 9（Vue 3 + 8 视图 + OnboardingWizard 基础）✓ |
| 配置 / 通知 / 部署（SDD §13、§14、§16） | Phase 10（ConfigService + WxPusher + Settings 三级折叠 + Docker）✓ |

### 3.3 待确认事项清单（G-1 ~ G-8）

| 编号 | 级别 | 范畴 | 问题摘要 |
|------|------|------|----------|
| G-1 | P2 | Phase 9 / Phase 10 | system_design §2.7 规划的 2 个 WebSocket 端点（`/ws/pipeline/progress`、`/ws/backtest/{id}/progress`）后端实装状态与前端消费状态需核查 |
| G-2 | P2 | Phase 2 | AKShare 备用数据源在 Phase 2 设计中列为 fallback，需核实当前 DataService 是否已有完整降级路径（TushareAdapter 失败 → AKShareAdapter） |
| G-3 | P3 | Phase 10 | SDD 附录 B 的「IC 下期收益窗口 20 交易日」未出现在 Phase 10 §2.3 的 11 个 config_keys 中，用户无法通过 Settings 调整 |
| G-4 | P3 | Phase 10 / SDD §11 | 多账户 UI 切换在 Phase 9 前端中未实现（后端已支持 account_id 参数），Phase 10 §1.2 需显式声明推迟 V1.5 |
| G-5 | P3 | Phase 10 §1.2 | SDD §12.4 行为分析（stop_loss_execution_rate、chase_up_rate）V1.0 是否交付，Phase 10 §1.2 需显式 include 或推迟 V1.5 |
| G-6 | P3 | Phase 10 §1.2 | SDD §13.3 消息模板体系（分事件 / 分渠道模板库）Phase 10 是否交付 minimum 模板集，需显式 include 或推迟 V1.5 |
| G-7 | P3 | Phase 10 §1.2 | SDD §15.1 易用性—新手引导 OnboardingWizard 是否在 V1.0 完成全部 4 步骤，需对照 Phase 9 实际交付 |
| G-8 | P3 | Phase 10 §1.2 | SDD §14.6 配置版本管理（revert / diff）在 Phase 6 已实现基础版，Phase 10 新增三级折叠 UI 后是否覆盖所有配置项的版本历史 |

### 3.4 详述

#### G-1（P2）：WebSocket 端点落地状态需核查

**位置**：system_design §2.7、Phase 7 设计（Pipeline 触发）、Phase 8 设计（Backtest 触发）、Phase 9 前端

**问题**：system_design §2.7 规划 2 个 WebSocket 端点用于实时进度推送：
- `/ws/pipeline/progress` — 每日 Pipeline 实时进度
- `/ws/backtest/{id}/progress` — 回测进度推送

需要核实：
1. 这 2 个端点的后端 FastAPI 路由是否已实装（可能在 Phase 7/8 列为推迟，或已实现但文档未同步）
2. Phase 9 前端 `api/` 或 `stores/` 是否有 WS 客户端消费代码
3. 若后端未实装，应在 Phase 10 §1.2 或 §9 明确"推迟至 V1.5，V1.0 降级为轮询 `GET /pipeline/status`"

**修正方案**：Phase 10 启动实现前，对照 backend 代码确认 WS 端点实装状态；若未实装，同步更新 system_design §9 将 WS 端点移至 V1.5，或在 Phase 10 §9 新增作为 P10-D 的交付项。

---

#### G-2（P2）：AKShare 备用数据源完整性

**位置**：Phase 2 设计 §3 / `data/adapters/akshare.py`

**问题**：SDD §5 与 system_design §5.1 描述 Tushare 主 / AKShare 备的双源架构。Phase 2 设计中 AKShareAdapter 存在，但需核实：
1. 当前 DataService 在 Tushare 失败（例如返回空 / token 过期）时是否自动降级到 AKShare
2. AKShare 是否覆盖 Tushare 的全部 6 类数据（日线 / 分红 / 财务 / 指数 / 股本 / 行业）
3. 降级是否有日志（按 CLAUDE.md §6 不可静默降级）

若覆盖不完整，Phase 10 §1.2 应显式列为 V1.5 技术债。

---

#### G-3（P3）：IC 窗口（20 交易日）未纳入 11 个 config_keys

**位置**：Phase 10 §2.3 config_keys 表 / SDD 附录 B

**问题**：SDD 附录 B 明确"IC 下期收益窗口 = 20 交易日"是默认参数之一，FactorMonitorEngine 的 IC 计算强依赖此窗口。Phase 10 §2.3 定义的 11 个 config_key 中：
- signal_params / risk_limits / market_state_params / universe_params / strategy_weights
- strategy_params_{trend,momentum,reversion,value}
- backtest_defaults / notification_prefs / risk_free_rate

没有因子监控参数（ic_window / ic_alert_threshold / half_life_window）。按 SDD "用户可调参数" 精神，IC 窗口应可配置。

**修正方案**（任选其一）：
- **方案 A**：Phase 10 §2.3 新增第 12 个 config_key `factor_monitor_params`（含 ic_window / ic_alert_threshold / half_life_window）
- **方案 B**：在 §2.3 末尾注明 "IC 窗口等因子监控参数 V1.0 保持硬编码（20 交易日），V1.5 纳入配置化"

---

#### G-4 ~ G-8（P3）：SDD 功能点需在 Phase 10 §1.2 逐项确认 include/defer

**统一问题**：Phase 10 §1.2「不在范围」章节应对以下 SDD 功能点给出明确 include-or-defer 裁决，避免模糊落地：

- **G-4** 多账户 UI 切换（SDD §11 账户模型支持多账户，后端已支持 account_id，前端 Phase 9 单账户 hardcode = 1）
- **G-5** 行为分析指标（SDD §12.4 stop_loss_execution_rate / chase_up_rate）是否在 Phase 10 报告页面展示
- **G-6** 消息模板体系（SDD §13.3）是否在 Phase 10 交付 minimum 模板集（每类事件一个模板）
- **G-7** OnboardingWizard 4 步骤（SDD §15.1）是否在 V1.0 完成全部步骤，或仅交付部分步骤
- **G-8** 配置版本管理 UI（SDD §14.6 diff / revert）在 Phase 6 后端已实现，Phase 10 Settings 三级折叠前端是否覆盖全部 11 个 config_keys 的历史查看与回退

**修正方案**：Phase 10 §1.2 新增一张「SDD 功能点裁决表」逐条明示 include / defer 与理由，避免验收时 "这是不是 V1.0 范围" 成为争议点。

---

## 4. 结论与修订要求

### 4.1 总体评级

**B+**。Phase 10 设计文档在配置消费、通知降级、Settings 三级折叠三条主线上基本到位，项目结构清晰，DoD 完整；但存在 2 个 P2 级结构性问题（Q-1 路径偏离、Q-2 BacktestEngine 接入未显式），以及 2 个 P2 级覆盖层面核查事项（G-1 WS 端点、G-2 AKShare 降级）。建议修订至 v1.1 后再启动实现。

### 4.2 必须在 Phase 10 v1.1 修订的项

1. **Q-1**：通知适配器路径从 `data/adapters/wxpusher.py` 改为 `notification/wxpusher.py`，并新增 `notification/base.py`；若保持 `data/adapters/`，需同步更新 system_design §3 说明偏离
2. **Q-2**：§4.5 新增「BacktestEngine UserConfig 接入」小节，明确 partial-overlay 合并发生在 `POST /backtest/run` 端点，而非 BacktestEngine 内部
3. **Q-3**：notification_prefs 的 6 项开关与 SDD §14.4 对齐 —— 要么 Phase 10 砍回 4 项，要么同步更新 SDD §14.4
4. **Q-4**：§4.2 明确 WxPusher 降级链的日志级别（失败 WARN / 链路异常 ERROR / 兜底失败 ERROR）
5. **Q-5**：§5.3 明确 config_snapshot 在 Pipeline 启动时一次性写入，CP 内部不再读 ConfigService
6. **Q-6**：§2.3 strategy_weights 键名交叉核对 Phase 4 策略 ID 并附注来源
7. **Q-7**：§2.3 backtest_defaults.slippage_bps 标注 SDD 来源或 "行业惯例 + 待 SDD 补录"

### 4.3 需同步更新的其他文档

- **system_design.md §3**：若保留 `notification/` 独立目录（推荐），无需改动；若 Phase 10 坚持用 `data/adapters/`，则 §3 需同步
- **system_design.md §9**：Phase 10 范围确认 + 推迟项（如 WS 端点）更新
- **QuantPilot_SDD.md §14.4**：若采 Q-3 方案 B，扩展至 6 项开关
- **QuantPilot_SDD.md §12.4 / §13.3 / §14.6 / §15.1**：若 G-5/G-6/G-7/G-8 中任何项推迟，需在 SDD 相应章节加 "V1.5 实施" 标注
- **QuantPilot_SDD.md 附录 B**：若采 Q-7 同步方案，新增 slippage_bps 条目；若采 G-3 方案 A，新增 ic_window 条目

### 4.4 启动 Phase 10 实现前的前置条件

1. 完成上述 7 项 Phase 10 v1.1 修订
2. 核查 G-1（WebSocket）G-2（AKShare）真实实装状态，在 Phase 10 §9 或 §1.2 落地裁决
3. 对 G-3 ~ G-8 在 §1.2 新增裁决表，明确每项 include / defer 与理由
4. CLAUDE.md §9 进度表 Phase 10 行从"设计文档已就位，待启动实现"更新为"v1.1 评审通过，进入 TDD 实现"

---

**评审结论**：Phase 10 作为 V1.0 收官 phase，设计总体成熟度较高，但作为最后一块拼图，其范围裁决直接影响 V1.0 是否"规格完整"。建议严格完成上述 7+8 项修订后再启动实现，避免实装阶段回头修设计。

**附录**：本评审未涉及 `docs/reviews/` 历史评审已覆盖的 Phase 1~9 问题，仅聚焦 Phase 10 本身与 SDD / system_design 总体覆盖。
