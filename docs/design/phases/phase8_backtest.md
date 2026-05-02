# Phase 8：绩效归因 + 回测引擎

> **版本：** v1.3
> **日期：** 2026-04-14
> **依据文档：** QuantPilot_SDD.md §7.7, §12.1~12.4；system_design.md §2.4, §5.6, §5.8, §6, §9

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-13 | Phase 8 设计文档初版（含 §9 【设计待定】解析） |
| v1.1 | 2026-04-14 | 设计评审修复：D8-P2-01 冒烟测试编号从 API-58 起；D8-P2-02 BacktestEngine 移除 session_factory，引入 BacktestDataBundle + BacktestService；D8-P2-03 BacktestReport.generate() 签名统一；D8-P3-04~08 接口变更标注 + 降级说明 + PositionSizer 伪代码补全 |
| v1.2 | 2026-04-14 | 代码评审修复（C-01~C-07）：`_get_quotes_at` 格式修正（宽表→长表）；`_get_benchmark_return` 改为范围查询；`create_task` 移除手动 commit；`_make_redis_progress_cb` 改用 `asyncio.get_running_loop()`；冒烟测试补充 API-70/71；INT-BE-02 断言修正 + mock_strategy；`BacktestResult.task_id` 补标 `unique=True` |
| v1.3 | 2026-04-14 | 收尾核查：补充 API-72（attribution 正常 200）、API-73（有效 task_id 查状态 200）；§8 测试表更新为实际 16 个（API-58~73）；§9.2 DoD 更新 |
| **v1.4** | 2026-05-01 | **V1.0 整改 Batch 3 — 回测引擎重构 P0+P1**（来自 `docs/reviews/v1_overall_review_2026-04-27.md` §3.1+§3.2，10 项）：B3-1 BacktestDataBundle 扩展（`daily_quotes` 全字段 / `pe_pb_history` / `index_adj_prices` 新字段）；B3-2 T+1 撮合（`BacktestConfig.execution_price="OPEN_T1"` 默认 + `pending_signals` 队列 + `_execute_signals(use_open_price=True)`）；B3-3 PE/PB + HS300 真实切片（`_slice_pe_pb_history_at` / `_slice_index_at`）；B3-4 RiskChecker 集成（新增 `_apply_risk_checker` 主流程方法，BLOCK 信号被剔除、WARN 写入 reason）；B3-5/6 PIT is_st/is_suspended/delist_date 切片（`_get_stock_info_at` 新增 delist_date 过滤）；B3-7 financials_history 时点切片传入 UniverseFilter；B3-8 BacktestService._load_data_bundle 走 DataValidator；B3-9 主循环 8 处 `logger.debug`/`pass` 改 `logger.exception`；B3-10 INT-BE-03~08 集成测试（T+1 / 涨停 / 停牌 / 退市 / RiskChecker BLOCK / open vs close 撮合差异）|

---

## 1. 范围声明

### 1.1 本 Phase 纳入模块（system_design §9 Phase 8）

| 模块 | 路径 | 说明 |
|------|------|------|
| BacktestEngine | `engine/backtest/engine.py` | 回测主引擎，纯计算，无 IO（SDD §7.7；CLAUDE.md §6） |
| BacktestReport | `engine/backtest/report.py` | 绩效指标计算 + 局限性声明（SDD §7.7.4） |
| BacktestService | `services/backtest_service.py` | 编排 IO：加载历史数据 → 构建 BacktestDataBundle → 调用 BacktestEngine.run() → 写 BacktestResult |
| PerformanceService | `services/performance_service.py` | 实盘绩效归因（SDD §12.1~12.4） |
| API 绩效端点 | `api/v1/performance.py` | 4 个端点（summary / history / attribution / behavior） |
| API 回测端点 | `api/v1/backtest.py` | 3 个 REST 端点 + 1 个 WebSocket 端点 |
| ORM 新表 | `models/system.py` | BacktestTask + BacktestResult 模型 |
| 数据库迁移 | `alembic/versions/0006_phase8_backtest_tables.py` | backtest_task / backtest_result 表 |
| Pydantic schemas | `schemas/performance.py` + `schemas/backtest.py` | 请求/响应结构 |
| Redis 集成 | `main.py`（lifespan 扩展） | 初始化 redis 异步客户端（WS 进度推送） |

### 1.2 显式排除

- **因子归因**（SDD §12.3）：V1.5 功能，本 Phase 不实现，API 响应结构预留 `factor_attribution: null`
- **多账户支持**：V1.0 单账户，PerformanceService 取系统中第一个账户（account_id 最小值）
- **策略参数自定义**：`POST /backtest/run` 仅接受日期范围 + 交易成本参数；strategy_config / account_config 从 user_config 表读取，V1.0 不允许用户在回测请求中覆盖

### 1.3 【设计待定】解析（来自 system_design §9 Phase 8 标注）

**待定项：`backtest_task` / `backtest_result` 表定义**

| 决策项 | 选型 | 理由 |
|--------|------|------|
| 任务 ID | `uuid.uuid4()`，存为 `String(36)` | 适合作为 WS URL 路径段，无冲突风险 |
| 状态字段 | `PENDING / RUNNING / SUCCESS / FAILED` | 与 PipelineRun.status 风格一致 |
| 结果持久化 | 分离为两张表（见 §2.1） | backtest_result 可单独查询，避免 backtest_task 行过大 |

---

## 2. 数据模型

### 2.1 新增表（migration 0006）

**backtest_task**

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| task_id | String(36) | PK | UUID4 字符串 |
| status | String(10) | NOT NULL | PENDING / RUNNING / SUCCESS / FAILED |
| config_json | JSONB | NOT NULL | 序列化的 BacktestConfig |
| started_at | TIMESTAMP(tz) | nullable | 开始执行时间 |
| finished_at | TIMESTAMP(tz) | nullable | 完成时间 |
| error_msg | Text | nullable | 失败时错误信息 |
| created_at | TIMESTAMP(tz) | server_default=NOW() | |

**backtest_result**

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | BigInteger | PK autoincrement | |
| task_id | String(36) | FK(backtest_task.task_id) UNIQUE | |
| performance_json | JSONB | NOT NULL | 标准绩效报告（SDD 附录 C 全部指标） |
| daily_nav_json | JSONB | NOT NULL | `{trade_date_str: nav_value}` 每日净值序列 |
| disclaimer | Text | NOT NULL | SDD §7.7.4 局限性声明 |
| created_at | TIMESTAMP(tz) | server_default=NOW() | |

**【降级说明】** `BacktestResult.daily_positions` 每日持仓明细（约 N×T 行）数据量较大，V1.0 不持久化；仅在任务执行期间驻留内存供 `run()` 循环使用，完成后丢弃。`GET /backtest/{id}/result` 不返回持仓明细。恢复条件：V1.5 可新增 `daily_positions_json JSONB` 列或单独持仓明细表，按需开启。

### 2.2 依赖的已有表

| 表 | Phase | 用途 |
|----|-------|------|
| `daily_portfolio_value` | Phase 7 | 实盘净值曲线（get_summary / get_history） |
| `trade_record`, `fund_flow` | Phase 6 | 实盘成交与资金流水（绩效计算基础） |
| `signal`, `signal_score_snapshot` | Phase 5 | 信号血缘（策略归因 by_strategy） |
| `index_history` | Phase 2 | HS300 基准行情（benchmark_return） |
| `user_config` | Phase 6 | `risk_free_rate` 配置（夏普比率） |
| `account` | Phase 6 | 账户初始资金参考 |

---

## 3. BacktestEngine 设计

### 3.1 架构约束（SDD §7.7.1）

- **共用 Engine 层**：BacktestEngine 注入与 DailyPipeline 相同的策略/打分/信号实例；严禁在回测中独立实现策略逻辑
- **后复权价格**：使用 `AdjustedPriceProvider.backward_adjusted()` 序列（SDD §7.7.3）
- **PIT 原则**：标的池基于历史时点可投资宇宙（含退市股），调用 `universe_filter.filter(stock_info_at_date, financials_at_date, ...)` 构建每日宇宙（SDD §5.2）

### 3.2 接口（engine/backtest/engine.py）

```python
@dataclass
class BacktestConfig:
    start_date: date
    end_date: date
    initial_capital: float
    strategy_config: dict        # 与实盘配置结构完全相同（SDD §7.7.2）
    account_config: dict         # 仓位控制参数
    commission_rate: float = 0.00025   # 双向佣金 0.025%（SDD §10.5）
    stamp_tax_rate: float = 0.0005     # 印花税 0.05%（仅卖出，SDD §10.5）
    slippage_rate: float = 0.001       # 滑点估算 0.1%（SDD §10.5）

@dataclass
class BacktestDataBundle:
    """由 BacktestService 预加载的全量历史数据，BacktestEngine 不含 IO。"""
    adj_prices: pd.DataFrame       # index=trade_date, columns=ts_code（后复权价格序列）
    stock_info: pd.DataFrame       # index=ts_code，含 list_date/is_st/sw_industry_l1
    financials: pd.DataFrame       # MultiIndex(ts_code, report_period)，含 PIT 公告日
    hs300_history: pd.DataFrame    # HS300 OHLCV 历史（市场状态识别用）

@dataclass
class BacktestResult:
    daily_nav: pd.Series            # index=trade_date, values=净值（初始=1.0）
    daily_positions: pd.DataFrame   # columns=[trade_date, ts_code, shares, cost, market_value]（不持久化）
    signal_history: list[dict]      # 每日信号记录列表
    performance: dict               # 绩效报告（SDD 附录 C 全部指标）
    disclaimer: str                 # SDD §7.7.4 声明文本

class BacktestEngine:
    """
    核心约束：
    - 严格无 IO（CLAUDE.md §6）；全部历史数据由 BacktestService 预加载后通过 BacktestDataBundle 传入。
    - 必须注入与 DailyPipeline 相同的 strategies/scorer/signal_engine 实例（SDD §7.7.1）。
    - 使用 AdjustedPriceProvider.backward_adjusted() 后复权价格（SDD §7.7.3）。
    """
    def __init__(
        self,
        strategies: list[BaseStrategy],
        market_state_engine: MarketStateEngine,
        universe_filter: UniverseFilter,   # 接口变更：system_design §5.8 原名 universe_engine，Phase 8 统一改名（见 D8-P3-04）
        scorer: Scorer,
        signal_engine: SignalGenerator,
        position_engine: PositionSizer,
        price_provider: AdjustedPriceProvider,
        calendar: TradingCalendar,
        # 无 session_factory：IO 编排职责移交 BacktestService（D8-P2-02 修复）
    ): ...

    def run(  # 同步；由 BacktestService 用 asyncio.to_thread 包装以避免阻塞事件循环
        self,
        config: BacktestConfig,
        data: BacktestDataBundle,          # 预加载的全量历史数据（由 BacktestService 填充）
        progress_cb: Callable[[str, int, float], None] | None = None,
        # progress_cb(trade_date_str, progress_pct, current_nav) 每 100 日调用一次
    ) -> BacktestResult: ...
```

**接口变更说明（相对 system_design §5.8）**：
- `universe_engine` 重命名为 `universe_filter: UniverseFilter`（与 Phase 4 实际类名统一）
- 移除 `session_factory`：历史数据加载职责移入 `BacktestService`（遵守 CLAUDE.md §6 no-IO 规约），`run()` 改为接收预加载的 `BacktestDataBundle`
- `run()` 新增 `data: BacktestDataBundle` 参数，`progress_cb` 参数保持（解耦 Redis 依赖）
- system_design §5.8 已同步更新（见 D8-P3-04）

**BacktestService 职责（services/backtest_service.py）**：

```python
class BacktestService:
    def __init__(self, session: AsyncSession, engine: BacktestEngine): ...

    async def run_task(self, task_id: str, config: BacktestConfig) -> None:
        """
        ① 更新 BacktestTask(status=RUNNING)
        ② 加载 BacktestDataBundle（adj_prices / stock_info / financials / hs300_history）
        ③ progress_cb = make_redis_publish_cb(task_id, redis)（若 redis 可用）
        ④ result = await asyncio.to_thread(engine.run, config, data, progress_cb)
        ⑤ 写 BacktestResult（performance_json, daily_nav_json, disclaimer）
        ⑥ 更新 BacktestTask(status=SUCCESS, finished_at=now())
        异常：更新 BacktestTask(status=FAILED, error_msg=str(exc))
        """
```

### 3.3 run() 主流程

`run()` 为同步函数，由 `BacktestService` 通过 `asyncio.to_thread` 包装，避免阻塞事件循环。全量历史数据由 `BacktestService` 预加载后通过 `BacktestDataBundle` 传入（不在 `run()` 内做任何 IO）。

```
输入：config: BacktestConfig，data: BacktestDataBundle，progress_cb（可选）

1. trade_dates = calendar.get_trade_dates(config.start_date, config.end_date)
2. 从 data（BacktestDataBundle）解包预加载数据：
   a. adj_prices  ← data.adj_prices（后复权价格序列，全股票 × 全日期）
   b. stock_info  ← data.stock_info（list_date / is_st / sw_industry_l1）
   c. financials  ← data.financials（MultiIndex，PIT 公告日 + 财务指标）
   d. hs300_hist  ← data.hs300_history
   【数据由 BacktestService.run_task() 在调用 run() 前加载，BacktestEngine 不含 IO】

3. virtual_positions: dict[str, Position] = {}   # key=ts_code，value=虚拟持仓对象
   nav = {}; all_signals = []

4. for i, trade_date in enumerate(trade_dates):
   a. stock_info_t = stock_info[上市早于 trade_date]（PIT 过滤）
   b. financials_t = financials[公告日 <= trade_date 的最近一期]（PIT）
   c. quotes_t = adj_prices.loc[trade_date]（当日后复权行情 Series）
   d. universe = universe_filter.filter(stock_info_t, financials_t, quotes_t, trade_date, calendar)
   e. market_state = market_state_engine.identify_latest(hs300_hist[hs300_hist.trade_date <= trade_date])
   f. strategy_scores = [s.score(universe_df, quotes_t) for s in strategies]（同步）
   g. composite = scorer.aggregate(strategy_scores, market_state)
   h. virtual_position_list = list(virtual_positions.values())  # list[Position]，适配 SignalGenerator 接口
      signals = signal_engine.generate(composite, virtual_position_list, market_state, risk_params=None)
      # 【降级说明】risk_params=None：回测无真实账户上下文，RiskChecker 跳过集中度检查（D8-P3-08）。
      # 恢复条件：若需模拟风控约束，可在 BacktestConfig 中加入 risk_config 并传入。
   h2. signals = position_engine.suggest(signals, _make_virtual_account(virtual_positions, config), market_state, config.account_config)
      # 调用 PositionSizer 填充 suggested_pct，与 DailyPipeline CP3 流程一致（D8-P3-07，SDD §7.7.2）
   i. virtual_positions = _execute_signals(signals, virtual_positions, quotes_t, config)
   j. nav[trade_date] = _calc_nav(virtual_positions, quotes_t, config.initial_capital)
   k. all_signals.extend(signals)
   l. 每 100 日：若 progress_cb：
      progress_pct = (i+1)*100//len(trade_dates)
      progress_cb(str(trade_date), progress_pct, nav[trade_date])

5. result_dict = BacktestReport.generate(nav, all_signals, config)
   # 签名修正（D8-P2-03）：参数为 (nav, signal_history, config)；initial_capital 从 config.initial_capital 读取
6. return BacktestResult(
       daily_nav=pd.Series(nav),
       daily_positions=_virtual_positions_to_df(virtual_positions),
       signal_history=all_signals,
       performance=result_dict,
       disclaimer=DISCLAIMER,
   )
```

### 3.4 交易成本扣除（_execute_signals）

```
BUY 每股实际成本  = price × (1 + commission_rate + slippage_rate)
SELL 每股净收入   = price × (1 - commission_rate - stamp_tax_rate - slippage_rate)
从模拟账户现金中扣除（成本）/ 增加（收入）对应金额
```

### 3.5 Redis 进度推送（WS 集成）

Redis channel: `backtest:{task_id}:progress`

```json
{"trade_date": "2024-01-15", "progress_pct": 42, "current_nav": 1.23}
```

推送频率：每 100 个交易日一次（system_design §2.7）

API 层将 `progress_cb` 绑定为 Redis PUBLISH 操作；BacktestEngine 不直接依赖 Redis，保持可测试性。

### 3.6 BacktestReport（engine/backtest/report.py）

```python
# V1.0 整改 Batch 1 — B1-1（重写 DISCLAIMER 反映回测引擎已知局限，与 SDD §7.7.4 / 前端 BacktestLimitationsBanner 同步）
DISCLAIMER = (
    "V1.0 回测引擎已知局限：撮合方式与 A 股 T+1 规则存在差异（当日 close 撮合，"
    "实盘为次日开盘），未排除涨停/停牌/已退市股，PE/PB 历史分位与指数收益数据切片"
    "在回测中以空集合降级，主流程不调用 RiskChecker（集中度/行业/回撤限制不生效）。"
    "回测净值、Sharpe 等指标与实盘可达成收益**无系统性对应关系**，仅供策略相对排序"
    "参考，不构成任何投资建议。"
)

class BacktestReport:
    @staticmethod
    def generate(
        nav: dict[date, float],
        signal_history: list[dict],
        config: BacktestConfig,
        # initial_capital 已移除（从 config.initial_capital 读取，D8-P2-03）
    ) -> dict:
        """生成标准绩效报告（SDD 附录 C）：
        cumulative_return / annualized_return / max_drawdown /
        sharpe_ratio（rf=0.03）/ win_rate / profit_loss_ratio
        initial_capital 从 config.initial_capital 读取，无冗余参数。
        """
```

---

## 4. PerformanceService 设计

### 4.1 接口（services/performance_service.py）

```python
class PerformanceService:
    def __init__(self, session: AsyncSession): ...

    async def get_summary(self, account_id: int) -> dict | None:
        """7 项基础绩效指标（SDD §12.1）；账户无数据返回 None。"""

    async def get_history(self, account_id: int, limit: int = 252) -> dict:
        """净值曲线历史（daily_portfolio_value）+ HS300 基准同期收益率序列。"""

    async def get_attribution(
        self,
        account_id: int,
        period_start: date,
        period_end: date,
        # 接口变更说明（相对 system_design §5.6）：
        # period: DateRange → period_start/period_end（FastAPI 查询参数更友好；DateRange 类型未在代码中定义）；
        # 返回类型 Attribution（Pydantic）→ dict（Pydantic 序列化由 API 层 schemas/performance.py 负责）。
        # system_design §5.6 已同步更新（D8-P3-05）。
    ) -> dict:
        """三维归因（SDD §12.2）：by_stock / by_industry / by_strategy。"""

    async def get_behavioral_analysis(self, account_id: int) -> dict:
        """行为分析 6 项指标（SDD §12.4）。"""
```

### 4.2 get_summary 计算规格

| 指标 | 数据源 | 计算方式 |
|------|--------|----------|
| cumulative_return | `fund_flow` + `daily_portfolio_value` | (latest total_value − net_invested) / net_invested |
| annualized_return | 同上 | (1 + cumulative_return)^(365/days) − 1；days = first dpv → latest dpv 自然日数 |
| max_drawdown | `daily_portfolio_value` 序列 | max(1 − nav_t / running_max_nav_t) |
| sharpe_ratio | `daily_portfolio_value` + `user_config` | (ann_return − rf) / ann_volatility；rf 来自 user_config["risk_free_rate"]，缺失时默认 0.03 |
| win_rate | `trade_record` | 已平仓标的中 PnL > 0 的比例（WAC 成本基础） |
| profit_loss_ratio | `trade_record` | avg(winning_pnl) / avg(abs(losing_pnl))；仅已平仓标的 |
| benchmark_return | `index_history`（HS300, ts_code='000300.SH'） | 同账户存续期间 close 涨跌幅 |

**net_invested 计算**：
```
net_invested = Σ(DEPOSIT flow_type 金额) + Σ(WITHDRAW flow_type 金额)
# WITHDRAW 金额在 fund_flow 中存储为负值，因此直接求和
```

**【降级说明】** win_rate / profit_loss_ratio 仅统计有完整 BUY+SELL 交易记录的标的（已平仓）；持仓中标的暂不计入。V1.5 可扩展为基于 WAC 的逐日浮动盈亏统计。

### 4.3 get_attribution 计算规格

**by_stock**：
- 对每个有 SELL 记录的 ts_code，计算 realized_pnl = Σ(SELL amount) − Σ(BUY cost WAC)
- 返回 `[{ts_code, holding_days, realized_pnl, realized_pnl_pct}]`，按 realized_pnl DESC 排序

**by_industry**：
- trade_record.ts_code → `stock_basic`（StockInfo）→ sw_industry_l1
- 汇总每行业已平仓 realized_pnl 合计
- 无行业信息的 ts_code 归入"其他"组

**by_strategy**：
```
trade_record (signal_id IS NOT NULL)
  → JOIN signal ON trade_record.signal_id = signal.id
  → JOIN signal_score_snapshot ON signal.id = signal_score_snapshot.signal_id
  → score_breakdown JSONB: {"TrendStrategy": 72, "MomentumStrategy": 68, ...}
  → 主导策略 = argmax(score_breakdown)
  → GROUP BY 主导策略 → COUNT/SUM/WIN_RATE
```

**【降级说明】** 行业归因依赖 StockInfo.sw_industry_l1 字段；若 ts_code 无对应记录（已退市、数据未入库），归入"其他"。

### 4.4 get_behavioral_analysis 计算规格

| 指标 | 计算逻辑 |
|------|----------|
| avg_holding_days | 已平仓标的（BUY→SELL）持有天数均值（自然日） |
| monthly_trade_count | 月均 trade_record 条数 |
| signal_compliance_rate | trade_record 中 signal_id IS NOT NULL 比例 |
| stop_loss_execution_rate | 信号 stop_loss_price 在持仓期间触发 → 3 个交易日内有 SELL 记录的比例 |
| chase_up_rate | Signal.status=EXPIRED 后 3 个交易日内有对应 ts_code BUY 记录的比例 |
| pnl_distribution | 已平仓标的 realized_pnl_pct 按 10 分位分桶（-∞,−30%),(−30%,−20%),...,(+30%,+∞)，返回桶边界+频次 |

---

## 5. API 端点设计

### 5.1 绩效端点（api/v1/performance.py）

| 方法 | 路径 | 请求参数 | 响应 |
|------|------|----------|------|
| GET | `/performance/summary` | `account_id`（可选，缺省取最小 id） | `{code:0, data:{...7_metrics} \| null}` |
| GET | `/performance/history` | `account_id`, `limit`（默认 252） | `{code:0, data:{nav_series:[...], benchmark:[...]}}` |
| GET | `/performance/attribution` | `account_id`, `period_start`（必填）, `period_end`（必填） | `{code:0, data:{by_stock:[...], by_industry:[...], by_strategy:[...]}}` |
| GET | `/performance/behavior` | `account_id` | `{code:0, data:{...6_metrics}}` |

`period_start` / `period_end` 为 date 类型；缺失时 FastAPI 返回 422。

### 5.2 回测端点（api/v1/backtest.py）

| 方法 | 路径 | 请求 / 参数 | 响应 |
|------|------|------------|------|
| POST | `/backtest/run` | JSON body（见下） | `{code:0, data:{task_id:"...", status:"PENDING"}}` |
| GET | `/backtest/{task_id}/status` | path param | `{code:0, data:{task_id, status, progress_pct, started_at, finished_at}}` |
| GET | `/backtest/{task_id}/result` | path param | `{code:0, data:{performance:{...}, daily_nav:{...}, disclaimer:"..."}}` |
| WS | `/ws/backtest/{task_id}/progress` | path param | WebSocket（JSON 消息流，Phase 9 前端消费） |

**POST /backtest/run 请求体（BacktestRunRequest）：**
```json
{
  "start_date": "2022-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 1000000.0,
  "commission_rate": 0.00025,
  "stamp_tax_rate": 0.0005,
  "slippage_rate": 0.001
}
```

**错误规范：**
- `GET /backtest/{task_id}/status`：task_id 不存在 → 404
- `GET /backtest/{task_id}/result`：status = PENDING/RUNNING → 409（任务未完成）
- `GET /backtest/{task_id}/result`：status = FAILED → 422（含 error_msg）

### 5.3 WebSocket 进度端点

`WS /ws/backtest/{task_id}/progress`：

- 连接后，服务端订阅 Redis channel `backtest:{task_id}:progress`
- 收到 Redis 消息时转发给 WS 客户端
- BacktestEngine 每 100 个交易日向 Redis PUBLISH 一次进度消息
- 任务完成（SUCCESS/FAILED）时推送最终状态后关闭连接

**注**：WS 端点 Phase 8 实现后端逻辑（Redis 订阅），Phase 9（前端）为主要消费方。

---

## 6. 数据流

### 6.1 回测任务异步流程

```
POST /backtest/run
  ① 创建 BacktestTask(status=PENDING)，flush 得 task_id
  ② BackgroundTasks.add_task(run_backtest_task, task_id, config, app.state)
  ③ 返回 {task_id, status:"PENDING"}

run_backtest_task(task_id, config, app_state):
  【IO 编排由 BacktestService.run_task() 承担，BacktestEngine 不含任何 IO（D8-P2-02）】
  ① async with session_factory() as session:
       service = BacktestService(session, app_state.backtest_engine)
       await service.run_task(task_id, config)
  
  BacktestService.run_task(task_id, config) 内部：
  ① 更新 BacktestTask(status=RUNNING, started_at=now())
  ② 加载 BacktestDataBundle（adj_prices / stock_info / financials / hs300_history）
  ③ progress_cb = make_redis_publish_cb(task_id, redis)  # 若 redis 可用，否则 None
  ④ result = await asyncio.to_thread(engine.run, config, data, progress_cb)
  ⑤ 写入 BacktestResult（performance_json, daily_nav_json, disclaimer）
  ⑥ 更新 BacktestTask(status=SUCCESS, finished_at=now())
  异常：更新 BacktestTask(status=FAILED, error_msg=str(exc))
```

### 6.2 实盘绩效数据流

```
GET /performance/summary
  → PerformanceService.get_summary(account_id)
  → 读取 daily_portfolio_value（最新净值 + 历史序列）
  → 读取 fund_flow（计算 net_invested）
  → 读取 trade_record（win_rate / profit_loss_ratio）
  → 读取 index_history HS300（benchmark_return）
  → 读取 user_config["risk_free_rate"]（sharpe 无风险利率）
  → 返回 7 项指标字典
```

---

## 7. TDD 测试策略

### 7.1 单元测试（tests/unit/）

**test_backtest_report.py**（BacktestReport 纯函数）：

| ID | 场景 | 验证 |
|----|------|------|
| INV-BR-01 | 已知 nav 序列 [1.0, 1.1, 0.99, 1.05] | max_drawdown = (1.1−0.99)/1.1 |
| INV-BR-02 | nav 持续上升序列 | max_drawdown = 0 |
| INV-BR-03 | 已知收益率序列 → sharpe | 公式验证（rf=0.03） |

**test_backtest_engine.py**（BacktestEngine 成本计算纯函数部分）：

| ID | 场景 | 验证 |
|----|------|------|
| INV-BT-01 | BUY 10000 元 | 实际成本 = 10000 × (1 + 0.00025 + 0.001) |
| INV-BT-02 | SELL 10000 元 | 净收入 = 10000 × (1 − 0.00025 − 0.0005 − 0.001) |
| INV-BT-03 | commission_rate=0 stamp_tax_rate=0 slippage_rate=0 | BUY cost == SELL proceeds（无成本对称） |

### 7.2 E2E 测试（tests/e2e/）

**test_performance_api.py**：

| ID | 路径 | 场景 |
|----|------|------|
| E2E-PF-01 | GET /performance/summary | 无鉴权 → 401 |
| E2E-PF-02 | GET /performance/summary | 有鉴权，无数据 → 200 data:null |
| E2E-PF-03 | GET /performance/history | 有鉴权 → 200，data.nav_series 为 list |
| E2E-PF-04 | GET /performance/attribution | 缺 period_start/period_end → 422 |
| E2E-PF-05 | GET /performance/attribution | 有鉴权 + 参数 → 200，data 含 by_stock/by_industry/by_strategy |
| E2E-PF-06 | GET /performance/behavior | 有鉴权 → 200 |

**test_backtest_api.py**：

| ID | 路径 | 场景 |
|----|------|------|
| E2E-BT-01 | POST /backtest/run | 无鉴权 → 401 |
| E2E-BT-02 | POST /backtest/run | 有鉴权 + valid body → 200，data.task_id 非空 |
| E2E-BT-03 | GET /backtest/{id}/status | 有效 task_id → 200，data.status = PENDING |
| E2E-BT-04 | GET /backtest/{id}/status | 无效 task_id → 404 |
| E2E-BT-05 | GET /backtest/{id}/result | PENDING 状态 → 409 |
| E2E-BT-06 | POST /backtest/run | body 缺 start_date → 422 |

### 7.3 集成测试（tests/integration/）

**test_int_performance_service.py**：

| ID | 场景 | 验证 |
|----|------|------|
| INT-PS-01 | 插入 account + DEPOSIT fund_flow + 2 条 daily_portfolio_value | get_summary 返回 7 项指标，cumulative_return 数值可计算 |
| INT-PS-02 | 插入 BUY+SELL trade_record + SignalScoreSnapshot | get_attribution.by_strategy 按 score_breakdown 正确聚合 |
| INT-PS-03 | trade_record 中一半有 signal_id | get_behavioral_analysis.signal_compliance_rate = 0.5 |

**test_int_backtest_engine.py**：

| ID | 场景 | 验证 |
|----|------|------|
| INT-BE-01 | mock 全部依赖（strategies/scorer 均返回空分/空信号），3 个交易日 | BacktestResult 结构完整，daily_nav 长度 = 3 |
| INT-BE-02 | 有交易成本 vs. 无交易成本（commission=0/0/0），同一信号 | 有成本时 nav < 无成本时 nav |

---

## 8. 冒烟测试（tests/smoke/test_api_live.py）

**编号说明**：Phase 7 已占用 API-48～API-57（pipeline/factor-quality/reports 端点），Phase 8 从 API-58 起（D8-P2-01 修复）。

补充冒烟测试 API-58~73（16 个，含 C-05 补充 API-70/71 及后续补齐 API-72/73）：

| 编号 | 端点 | 场景 |
|------|------|------|
| API-58 | GET /performance/summary | 无鉴权 → 401 |
| API-59 | GET /performance/summary | 有鉴权 → 200，data 含指标字典或 null |
| API-60 | GET /performance/history | 无鉴权 → 401 |
| API-61 | GET /performance/history | 有鉴权 → 200，data 含 nav_series/benchmark_series |
| API-62 | GET /performance/attribution | 无鉴权 → 401 |
| API-63 | GET /performance/attribution | 缺参 → 422 |
| API-64 | GET /performance/behavior | 无鉴权 → 401 |
| API-65 | GET /performance/behavior | 有鉴权 → 200，data 为字典 |
| API-66 | POST /backtest/run | 无鉴权 → 401 |
| API-67 | POST /backtest/run | 有鉴权 + valid body → 200，data 含 task_id，status=PENDING |
| API-68 | GET /backtest/{id}/status | 无鉴权 → 401 |
| API-69 | GET /backtest/{id}/status | 不存在 task_id → 404 |
| API-70 | GET /backtest/{id}/result | 不存在 task_id → 404 |
| API-71 | GET /backtest/{id}/result | PENDING 状态 → 409（xfail，时序敏感） |
| API-72 | GET /performance/attribution | 有鉴权 + period_start/end → 200，data 含 by_stock 列表 |
| API-73 | GET /backtest/{valid_id}/status | 真实 task_id → 200，status 在合法集合内 |

---

## 9. 交付清单（DoD）

### 9.1 实现层

- [x] `engine/backtest/__init__.py`
- [x] `engine/backtest/engine.py`（BacktestConfig / BacktestDataBundle / BacktestResult / BacktestEngine）
- [x] `engine/backtest/report.py`（BacktestReport.generate() + DISCLAIMER 常量）
- [x] `services/backtest_service.py`（BacktestService.run_task()：IO 编排 + 数据预加载 + asyncio.to_thread 包装）
- [x] `services/performance_service.py`（get_summary / get_history / get_attribution / get_behavioral_analysis）
- [x] `models/system.py`（新增 BacktestTask + BacktestResult ORM）
- [x] `schemas/performance.py`（PerformanceSummary / PerformanceHistory / Attribution / BehavioralAnalysis）
- [x] `schemas/backtest.py`（BacktestRunRequest / BacktestStatusResponse / BacktestResultResponse）
- [x] `api/v1/performance.py`（4 个端点）
- [x] `api/v1/backtest.py`（3 个 REST 端点 + 1 个 WS 端点）
- [x] `api/deps.py`（新增 get_performance_service、get_backtest_service 等依赖注入函数）
- [x] `main.py`（lifespan 新增 redis 异步客户端初始化；include_router performance + backtest）
- [x] `alembic/versions/0006_phase8_backtest_tables.py`（backtest_task + backtest_result 表）

### 9.2 测试层

- [x] `tests/unit/test_backtest_report.py`（INV-BR-01~03）
- [x] `tests/unit/test_backtest_engine.py`（INV-BT-01~03）
- [x] `tests/e2e/test_performance_api.py`（E2E-PF-01~06）
- [x] `tests/e2e/test_backtest_api.py`（E2E-BT-01~06）
- [x] `tests/integration/test_int_performance_service.py`（INT-PS-01~03）
- [x] `tests/integration/test_int_backtest_engine.py`（INT-BE-01~02）
- [x] `tests/smoke/test_api_live.py` 补充 API-58~73（16 个，从 API-58 起以避免与 Phase 7 API-48~57 重叠；C-05 补充 API-70/71，收尾补齐 API-72/73）

### 9.3 质量门禁

- [x] `uv run ruff check src/ tests/` 输出 0 error
- [x] `uv run pytest tests/unit/ tests/e2e/` 全部通过（284 tests passed）
- [x] `uv run pytest tests/integration/` 全部通过（59 tests passed，修复 IndexHistory.ts_code → index_code）
