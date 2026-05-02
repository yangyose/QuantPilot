# Phase 8 设计评审报告

**评审对象**：`docs/design/phases/phase8_backtest.md` v1.0  
**依据文档**：`QuantPilot_SDD.md §7.7, §12.1~12.4`；`system_design.md §2.4, §5.6, §5.8, §6, §9`；`CLAUDE.md`；`phase7_pipeline.md` v1.1  
**评审日期**：2026-04-13  
**评审人**：Claude Code  
**状态**：已关闭（2026-04-14 全部 8 项修复完成）

---

## 1. 总体评价

| 维度 | 评价 |
|------|------|
| **DoD 完整性** | 实现层/测试层交付清单完整；冒烟测试编号与 Phase 7 冲突（见 D8-P2-01） |
| **Phase 边界** | 依赖表（§2.2）清晰列出 Phase 2/5/6/7 交付物；Redis 初始化归 Phase 8 正确；推迟至 Phase 9 的 WS 消费方已标注 |
| **设计符合度** | SDD §7.7 共用 Engine 层约束、PIT 原则均有体现；BacktestEngine 架构违反 CLAUDE.md §6 no-IO 规约（见 D8-P2-02） |
| **接口一致性** | BacktestReport.generate() 调用与定义不一致（见 D8-P2-03）；system_design 多处未同步（见 D8-P3-04/05） |
| **降级说明** | PerformanceService win_rate/profit_loss_ratio 已有降级说明；daily_positions 不持久化未标注（见 D8-P3-06） |

**结论：存在 3 个 P2 级缺陷（必须修复）、5 个 P3 级问题（建议修复），实现前须全部处理。**

---

## 2. 问题清单

### 2.1 P2 级（必须修复）

#### D8-P2-01：冒烟测试编号与 Phase 7 重叠

**位置**：`phase8_backtest.md §8`

**问题**：

Phase 7 设计文档（phase7_pipeline.md §5 及 DoD D-14）已占用 **API-48 ~ API-57**：

| 编号 | Phase 7 端点 |
|------|-------------|
| API-48 | GET /pipeline/status（无鉴权 → 401） |
| API-49 | GET /pipeline/status（有鉴权 → 200） |
| API-50 | POST /pipeline/trigger（无鉴权 → 401） |
| API-51 | GET /factor-quality（无鉴权 → 401） |
| API-52 | GET /factor-quality（有鉴权 → 200） |
| API-53 | GET /factor-quality/history（无鉴权 → 401） |
| API-54 | GET /reports（无鉴权 → 401） |
| API-55 | GET /reports（有鉴权 → 200） |
| API-56 | GET /reports/999（有鉴权 → 404） |
| API-57 | POST /reports/generate（无鉴权 → 401） |

Phase 8 设计文档 §8 同样从 **API-48** 开始，10 个编号完全重叠（API-48 ~ API-57），导致 `test_api_live.py` 中无法区分两个 Phase 的冒烟测试。

**修正方案**：Phase 8 冒烟测试从 **API-58** 开始，原 12 个编号改为 API-58 ~ API-69：

| 原编号 | 新编号 | 端点 | 场景 |
|--------|--------|------|------|
| API-48 | API-58 | GET /performance/summary | 无鉴权 → 401 |
| API-49 | API-59 | GET /performance/summary | 有鉴权 → 200，data 含 cumulative_return 字段 |
| API-50 | API-60 | GET /performance/history | 有鉴权 → 200，data 含 nav_series |
| API-51 | API-61 | GET /performance/attribution | 缺参 → 422 |
| API-52 | API-62 | GET /performance/attribution | 有鉴权 + period_start/end → 200，data 含 by_stock |
| API-53 | API-63 | GET /performance/behavior | 有鉴权 → 200，data 含 signal_compliance_rate |
| API-54 | API-64 | POST /backtest/run | 无鉴权 → 401 |
| API-55 | API-65 | POST /backtest/run | 有鉴权 + valid body → 200，data 含 task_id |
| API-56 | API-66 | GET /backtest/{id}/status | 有效 task_id → 200，data.status 在合法集合内 |
| API-57 | API-67 | GET /backtest/{id}/status | 随机 UUID（不存在） → 404 |
| API-58 | API-68 | GET /backtest/{id}/result | PENDING 状态 → 409 |
| API-59 | API-69 | GET /backtest/{id}/result | （暂跳过，需实际完成的回测任务，标记 xfail） |

同步更新 §9.2 DoD 中对冒烟测试的引用（API-58~69）。

---

#### D8-P2-02：BacktestEngine 在 engine/ 层使用 session_factory，违反 CLAUDE.md §6 no-IO 规约

**位置**：`phase8_backtest.md §3.2 / §3.3`

**问题**：

CLAUDE.md §6 明确规定："Engine 层（`engine/`）严格无 IO（数据库、文件、网络），只做纯函数计算"。

Phase 8 设计将 BacktestEngine 放置于 `engine/backtest/engine.py`，但其构造函数接受 `session_factory` 参数，且 `run()` 的步骤 2 执行大量一次性 DB 查询（后复权价格序列、股票基本信息、历史财务数据、HS300 指数历史）。这与 Phase 7 的正确分层模式相反——Phase 7 的 FactorMonitorEngine 是纯函数，由 FactorMonitorService（服务层）编排 IO 并调用 Engine。

设计文档 §3.2 的注释仅说明"以最小化接口变更"补入 `session_factory`，但未处理规约冲突。

**修正方案**：仿照 FactorMonitorEngine/FactorMonitorService 的分层模式：

```
BacktestEngine（engine/backtest/engine.py）
  职责：纯计算，接收预加载数据，不持有 session_factory
  新增参数：接收 BacktestDataBundle（包含已加载的价格/基本面/指数序列）
  
BacktestService（services/backtest_service.py）
  职责：编排 IO：加载历史数据 → 构建 BacktestDataBundle → 调用 BacktestEngine.run()
        管理 BacktestTask/BacktestResult 的 DB 写入
```

具体接口调整：

```python
# engine/backtest/engine.py（IO-free）
@dataclass
class BacktestDataBundle:
    """预加载的历史数据，由 BacktestService 填充"""
    adj_prices: pd.DataFrame          # index=trade_date, columns=ts_code
    stock_info: pd.DataFrame          # 股票基本信息（list_date, is_st, sw_industry_l1）
    financials: pd.DataFrame          # 历史财务（publish_date, ts_code, ...）
    hs300_history: pd.DataFrame       # HS300 OHLCV

class BacktestEngine:
    def __init__(
        self,
        strategies: list[BaseStrategy],
        market_state_engine: MarketStateEngine,
        universe_filter: UniverseFilter,
        scorer: Scorer,
        signal_engine: SignalGenerator,
        position_engine: PositionSizer,
        price_provider: AdjustedPriceProvider,
        calendar: TradingCalendar,
        # 移除 session_factory
    ): ...

    def run(  # 同步，由 BacktestService 用 asyncio.to_thread 包装
        self,
        config: BacktestConfig,
        data: BacktestDataBundle,      # 新增：预加载数据
        progress_cb: Callable[[str, int, float], None] | None = None,
    ) -> BacktestResult: ...

# services/backtest_service.py（含 IO）
class BacktestService:
    def __init__(self, session: AsyncSession, engine: BacktestEngine): ...

    async def run_task(self, task_id: str, config: BacktestConfig) -> None:
        """① 更新状态 RUNNING ② 预加载 BacktestDataBundle ③ asyncio.to_thread(engine.run) ④ 写 BacktestResult ⑤ 更新状态 SUCCESS"""
```

DoD §9.1 需同步：将 `services/backtest_service.py` 加入交付清单，并在 `api/deps.py` 中将 `get_backtest_engine` 改为 `get_backtest_service`。

---

#### D8-P2-03：BacktestReport.generate() 调用签名与接口定义不一致

**位置**：`phase8_backtest.md §3.3 step 5` 与 `§3.6`

**问题**：

§3.3 run() 流程第 5 步调用：
```python
result_dict = BacktestReport.generate(nav, virtual_positions, all_signals, config)
#                                          ↑ pd.DataFrame    ↑ list[dict]  ↑ BacktestConfig
```

§3.6 接口定义：
```python
@staticmethod
def generate(
    nav: dict[date, float],
    signal_history: list[dict],    # 位置 2
    config: BacktestConfig,        # 位置 3
    initial_capital: float,        # 位置 4
) -> dict:
```

两处不一致：
1. **位置 2**：调用传 `virtual_positions`（pd.DataFrame），接口期望 `signal_history`（list[dict]）
2. **位置 3**：调用传 `all_signals`（list[dict]），接口期望 `config`（BacktestConfig）
3. **参数缺失**：调用没有 `initial_capital`，而 `BacktestConfig.initial_capital` 已包含该值，接口的独立参数重复
4. **`virtual_positions` 未出现在接口中**：BacktestResult 包含 `daily_positions`，但 BacktestReport.generate() 不接收 virtual_positions，矛盾

**修正方案**：统一接口，删除冗余的 `initial_capital`（从 `config.initial_capital` 取），修正参数顺序：

```python
@staticmethod
def generate(
    nav: dict[date, float],
    signal_history: list[dict],
    config: BacktestConfig,
) -> dict:
    """生成标准绩效报告（SDD 附录 C）：
    cumulative_return / annualized_return / max_drawdown /
    sharpe_ratio（rf=0.03）/ win_rate / profit_loss_ratio
    initial_capital 从 config.initial_capital 读取。
    """
```

同步 §3.3 step 5 为：
```python
result_dict = BacktestReport.generate(nav, all_signals, config)
return BacktestResult(
    daily_nav=pd.Series(nav),
    daily_positions=virtual_positions_to_df(virtual_positions),
    signal_history=all_signals,
    performance=result_dict,
    disclaimer=DISCLAIMER,
)
```

---

### 2.2 P3 级（建议修复）

#### D8-P3-04：system_design §5.8 未同步 BacktestEngine 接口变更

**位置**：`system_design.md §5.8`

**问题**：

Phase 8 设计文档 §3.2 对 BacktestEngine 做了以下接口变更，但 system_design §5.8 均未同步：

| 变更项 | system_design §5.8 | Phase 8 设计 |
|--------|-------------------|--------------|
| 参数名 | `universe_engine`（无类型注解） | `universe_filter: UniverseFilter`（重命名） |
| 新增构造函数参数 | 无 | `session_factory`（D8-P2-02 修复后应移除） |
| run() 签名 | `async def run(self, config: BacktestConfig) -> BacktestResult` | 新增 `task_id: str` 和 `progress_cb` 参数 |

**修正方案**：修复 D8-P2-02 后，同步更新 system_design §5.8 为最终接口（含 universe_filter 重命名、run() 新参数），并在修订历史中注明"Phase 8 接口细化"。

---

#### D8-P3-05：get_attribution() 签名变更未标注为接口变更，system_design §5.6 未同步

**位置**：`phase8_backtest.md §4.1` 与 `system_design.md §5.6`

**问题**：

system_design §5.6 定义：
```python
async def get_attribution(self, account_id: int, period: DateRange) -> Attribution:
```

Phase 8 设计 §4.1 拆分为：
```python
async def get_attribution(
    self,
    account_id: int,
    period_start: date,
    period_end: date,
) -> dict:
```

`period: DateRange` → `period_start, period_end` 是接口变更，Phase 8 设计文档未以接口变更说明形式标注（类似 §3.2 对 session_factory 的注释）。返回类型 `Attribution`（Pydantic）→ `dict` 也是变更。

**修正方案**：在 §4.1 接口定义后补充注释：

```
# 接口变更说明（相对 system_design §5.6）：
# period: DateRange 拆分为 period_start/period_end（FastAPI 查询参数友好）；
# 返回类型改为 dict（Pydantic schema 由 API 层 schemas/performance.py 负责序列化）。
```

同步更新 system_design §5.6。

---

#### D8-P3-06：BacktestResult.daily_positions 不持久化，缺少【降级说明】

**位置**：`phase8_backtest.md §2.1` 与 `§3.2`

**问题**：

`BacktestResult` dataclass 包含 `daily_positions: pd.DataFrame`，但 `backtest_result` 表（§2.1）仅持久化 `performance_json` 和 `daily_nav_json`，`daily_positions` 不入库。API `/backtest/{id}/result` 响应也仅含 `performance / daily_nav / disclaimer`，用户无法事后查询每日持仓明细。

设计文档未对"不持久化 daily_positions"做出解释。

**修正方案**：在 §2.1 `backtest_result` 表定义后添加：

```
【降级说明】daily_positions 序列每日持仓明细（约 N×T 行）数据量较大，V1.0 不持久化；
仅在任务执行期间驻留内存供 run() 循环使用，完成后丢弃。
API /backtest/{id}/result 不返回持仓明细。
恢复条件：V1.5 可新增 daily_positions_json JSONB 列或单独持仓明细表，按需开启。
```

---

#### D8-P3-07：run() 伪代码未调用 PositionSizer，与 §3.1 "共用 Engine 层"约束矛盾

**位置**：`phase8_backtest.md §3.3 step h-i`

**问题**：

§3.1 架构约束："BacktestEngine 注入与 DailyPipeline 相同的策略/打分/信号实例；**严禁在回测中独立实现策略逻辑**"。

BacktestEngine 构造函数包含 `position_engine: PositionSizer`，但 §3.3 run() 伪代码步骤中：
- step h：仅调用 `signal_engine.generate()`，未调用 `position_engine.suggest()` 填充 `suggested_pct`
- step i：`_execute_signals` 直接以未经 PositionSizer 处理的信号决定仓位

DailyPipeline CP3 流程中，PositionSizer.suggest() 是信号处理的必要步骤（填充 `suggested_pct`），回测跳过此步骤导致实盘与回测的仓位逻辑不一致，违反 SDD §7.7.2。

**修正方案**：在 step h 和 step i 之间补充 PositionSizer 调用：

```
h. signals = signal_engine.generate(composite, list(virtual_positions.values()), market_state, risk_params=None)
   # 【降级说明】回测中 risk_params=None，RiskChecker 不执行（无实盘账户上下文）
h2. signals = position_engine.suggest(signals, virtual_account, market_state, position_config)
    # virtual_account 由 virtual_positions 和 config.initial_capital 构造
i. virtual_positions = _execute_signals(signals, virtual_positions, quotes_t, config)
```

---

#### D8-P3-08：SignalGenerator.generate() 调用缺少 risk_params，virtual_positions 类型不匹配

**位置**：`phase8_backtest.md §3.3 step h`

**问题**：

system_design §5.9 定义 SignalGenerator.generate() 签名：
```python
def generate(
    self,
    composite_scores: pd.DataFrame,
    current_positions: list[Position],  # list[Position]，非 dict
    market_state: MarketState,
    risk_params: RiskParams              # 必填
) -> list[Signal]:
```

§3.3 step h 调用：
```python
signals = signal_engine.generate(composite, virtual_positions, market_state)
#                                            ↑ dict（不是 list[Position]）  ↑ 缺少 risk_params
```

两处问题：
1. `virtual_positions` 在 step 3 定义为 `{}`（dict），而接口期望 `list[Position]`
2. 缺少 `risk_params` 参数

**修正方案**：

```python
# step h
virtual_position_list = list(virtual_positions.values())  # 转为 list[Position] 或兼容结构
signals = signal_engine.generate(composite, virtual_position_list, market_state, risk_params=None)
# 【降级说明】risk_params=None：回测无真实账户上下文，RiskChecker 不执行集中度检查。
# 恢复条件：若回测需模拟风控约束，可在 BacktestConfig 中加入 risk_config 并传入。
```

同时在 §3.3 伪代码 step 3 处明确 virtual_positions 的数据结构：
```
3. virtual_positions: dict[str, Position] = {}   # key=ts_code，value=虚拟持仓对象
```

---

## 3. Phase 边界核查

| 依赖项 | 来源 Phase | Phase 8 设计引用位置 | 核查结果 |
|--------|-----------|---------------------|---------|
| `daily_portfolio_value` | Phase 7 | §2.2 | ✓ 正确 |
| `trade_record`, `fund_flow` | Phase 6 | §2.2 | ✓ 正确 |
| `signal`, `signal_score_snapshot` | Phase 5 | §2.2 | ✓ 正确 |
| `index_history`（HS300） | Phase 2 | §2.2 | ✓ 正确 |
| `user_config`（risk_free_rate） | Phase 6 | §2.2 | ✓ 正确 |
| Redis 初始化 | Phase 8（新增） | §1.1、§6.1 | ✓ Phase 7 未涉及 Redis，Phase 8 首次引入正确 |
| WS 前端消费方 | Phase 9 | §5.3 注 | ✓ 已标注"Phase 9 前端为主要消费方" |
| PerformanceService 推迟至 Phase 8 | Phase 7 推迟 | 与 phase7 §1.1 一致 | ✓ 正确 |
| 冒烟测试编号 | Phase 7 已用 API-48~57 | Phase 8 §8 错误从 API-48 起 | ✗ **见 D8-P2-01** |

---

## 4. 设计符合度核查

| SDD / system_design 要求 | Phase 8 设计 | 结果 |
|--------------------------|-------------|------|
| SDD §7.7.1：共用 Engine 层（strategies/scorer/signal_engine 同一实例） | §3.1 架构约束明确 | ✓ |
| SDD §7.7.2：回测与实盘策略逻辑不分离 | PositionSizer 注入但未调用 | ✗ **见 D8-P3-07** |
| SDD §7.7.3：后复权价格 | §3.1 / §3.3 step 2a | ✓ |
| SDD §7.7.4：局限性声明 | BacktestReport.DISCLAIMER + backtest_result.disclaimer | ✓ |
| SDD §10.5：交易成本参数 | BacktestConfig 含 commission/stamp_tax/slippage | ✓ |
| SDD §12.1：7 项基础绩效指标 | get_summary §4.2 含 benchmark_return | ✓ |
| SDD §12.2：三维归因 | get_attribution §4.3 by_stock/industry/strategy | ✓ |
| SDD §12.4：行为分析 6 项 | get_behavioral_analysis §4.4 | ✓ |
| CLAUDE.md §6：Engine 层 no-IO | BacktestEngine 使用 session_factory | ✗ **见 D8-P2-02** |
| CLAUDE.md §6：读写通过 Repository | BacktestEngine 直接使用 session_factory | ✗ **见 D8-P2-02** |
| CLAUDE.md §10：禁止静默降级 | daily_positions 不持久化未注明 | ✗ **见 D8-P3-06** |

---

## 5. 评审总结

| 编号 | 级别 | 位置 | 标题 | 状态 |
|------|------|------|------|------|
| D8-P2-01 | **P2** | §8 冒烟测试 | 编号与 Phase 7 API-48~57 重叠，Phase 8 应从 API-58 起（API-58~69） | **已修复** |
| D8-P2-02 | **P2** | §3.2 | BacktestEngine 在 engine/ 层使用 session_factory，违反 CLAUDE.md §6 no-IO 规约；建议提取 BacktestService 编排 IO | **已修复** |
| D8-P2-03 | **P2** | §3.3 step 5 vs §3.6 | BacktestReport.generate() 调用（virtual_positions/all_signals/config）与接口定义（signal_history/config/initial_capital）不一致 | **已修复** |
| D8-P3-04 | P3 | system_design §5.8 | BacktestEngine 接口变更（universe_filter 重命名、task_id/progress_cb 新增）未同步至 system_design | **已修复** |
| D8-P3-05 | P3 | §4.1 vs system_design §5.6 | get_attribution() 签名变更（period:DateRange → period_start/period_end）未标注为接口变更，system_design §5.6 未同步 | **已修复** |
| D8-P3-06 | P3 | §2.1 | BacktestResult.daily_positions 不持久化，缺少【降级说明】 | **已修复** |
| D8-P3-07 | P3 | §3.3 step h-i | run() 伪代码未调用 PositionSizer，与 §3.1 "共用 Engine 层"约束矛盾 | **已修复** |
| D8-P3-08 | P3 | §3.3 step h | SignalGenerator.generate() 调用缺 risk_params，virtual_positions 类型为 dict 而非 list[Position] | **已修复** |

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-13 | 初版设计评审，共 8 项问题（P2×3、P3×5） |
| v1.1 | 2026-04-14 | 全部 8 项修复完成，评审关闭 |
