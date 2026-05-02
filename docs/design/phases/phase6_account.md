# Phase 6 设计文档：账户持仓管理

**状态**：设计文档已创建，实现中

**依据文档**：QuantPilot_SDD.md §11（账户与交易管理）、§14（用户设置与配置）；system_design.md §3（文件结构）、§4.3（账户数据表）、§6（API 端点）、§9（Phase 6 行）

---

## 1. 概览

### 1.1 Phase 6 范围

| 模块 | 类型 | 说明 |
|------|------|------|
| `services/account_service.py` | Service | AccountService：成交录入、持仓 CRUD、资金流水、账户同步 |
| `services/settings_service.py` | Service | SettingsService：用户配置 CRUD + 变更历史回溯 |
| `schemas/account.py` | Schema | AccountSummary / PositionItem / TradeRecordCreate / FundFlowItem 等 |
| `schemas/settings.py` | Schema | UserConfigItem / UserConfigHistoryItem |
| `api/v1/account.py` | API | GET/POST /account/*（6 端点） |
| `api/v1/positions.py` | API | GET/POST/PATCH /positions（3 端点） |
| `api/v1/settings.py` | API | GET/PUT/GET/POST /settings/*（4 端点） |

**超出 Phase 6 范围（显式标注）：**

- `performance_service.py`（绩效归因）→ Phase 8
- `account_service.mark_to_market()`（每日盯市）→ Phase 7（DailyPipeline）
- 分红自动处理（`fetch_dividends()` 接入、除权日触发）→ Phase 7（DailyPipeline 扩展）
- `GET /account/trades`（历史成交查询）→ 未在 system_design §6 端点表中，归属待定（Phase 8 或 Phase 9 前端需求驱动补入）
- `POST /account`（创建账户接口）→ V1.0 单账户场景无需，V1.5 多账户扩展时补充

### 1.2 前置条件

- Phase 1：Account / Position / TradeRecord / FundFlow / UserConfig / UserConfigHistory ORM 模型及迁移 ✓
- Phase 2：`daily_quote` 表有最新收盘价（账户同步所需；实现使用 `daily_quote` 而非 `daily_basic`，见 §3.3 降级说明）✓
- Phase 5：SignalService.update_status() 可用（无独立 `mark_acted()` 方法；录入成交后调用 `update_status(signal_id, "ACTED")` 实现信号状态回调）✓

---

## 2. 设计待定事项决策

本节对 system_design §9 Phase 6 行列出的四项设计待定进行明确决策。

### 2.1 position.phase 字段（BUILD/HOLD/REDUCE）

**取值逻辑（系统自动）：**

| 触发动作 | phase 取值 | 说明 |
|----------|-----------|------|
| BUY（新建持仓） | `BUILD` | 首次买入，建仓中 |
| BUY（加仓已有持仓） | `BUILD` | 继续增持，仍处于建仓阶段 |
| SELL（部分减仓） | `REDUCE` | 持仓数量下降，进入减仓阶段 |
| SELL（清仓，shares→0） | — | 持仓记录**删除**（平仓完毕） |

**手动覆盖：** 用户可通过 `PATCH /positions/{id}` 手动将 phase 改为 `BUILD`/`HOLD`/`REDUCE`/`null`。`HOLD`（稳定持有）只能由用户手动设置，系统不自动写入 HOLD。

**责任方：** AccountService.record_trade() 在写入 TradeRecord 后自动更新 Position.phase；用户手动 PATCH 端点覆盖。

### 2.2 PE/PB 每日动态计算与存储

**V1.0 决策：不存储动态 PE/PB，按需从 `daily_basic` 表查询。**

- `daily_basic` 表已包含 `pe_ttm`、`pb` 字段（Phase 2 每日采集写入）
- `GET /account`（账户概览）和 `GET /positions` 不展示 PE/PB 指标
- `account/sync` 从 `daily_quote` 查询最新 `close` 价格更新持仓市值（见 §3.3 降级说明）

【降级说明】持仓动态 PE/PB 展示由 Phase 9（前端）直接从 `daily_basic` 查询，Phase 6 不存储；若后续需聚合展示，在 Phase 8 绩效归因时再统一考虑存储策略。

### 2.3 分红处理链路

**V1.0 决策：手动录入分红，不实现自动分红采集。**

- 用户通过 `POST /account/deposit` 手动录入分红（请求体含 `ts_code` 时路由层自动识别为 DIVIDEND 类型）
- AccountService.record_dividend() 录入分红时同步更新对应持仓成本价：`cost_price -= amount / shares`
- 不自动调用 `fetch_dividends()`，不接入除权日自动触发

【降级说明】自动分红处理（`fetch_dividends()` 接入、除权日触发、批量成本价调整）推迟至 Phase 7 DailyPipeline 扩展实现，届时在 `daily_pipeline.py` 中新增分红步骤。

### 2.4 user_level 分层简化策略

**V1.0 决策：单管理员不实现 user_level 过滤，所有配置项完全可见/可修改。**

- `GET /settings` 返回 `user_config` 表全部记录（等同 L3 权限）
- `PUT /settings` 不校验 user_level 限制，任意 config_key 均可修改
- DB 写入新记录时 `user_level` 固定为 `"L2"`（对应 SDD §14 中 L2 级别的配置默认值）
- `user_level` 字段保留在 DB schema 中，供 V1.5 多用户场景扩展

【降级说明】user_level 分层过滤（L1 仅可见基础配置、L2 可改策略参数、L3 可改权重）推迟至 V1.5 多用户场景实现。

---

## 3. 核心数据流

### 3.1 成交录入流程

```
POST /account/trades
  ├── 校验 account_id 存在
  ├── 校验 ts_code 格式合法（应用层，不查 DB）
  ├── AccountService.record_trade()
  │   ├── 写入 trade_record 行，获取 trade_id
  │   ├── BUY：
  │   │   ├── 查询 position（account_id, ts_code）
  │   │   ├── 存在 → WAC 更新成本价，加仓数量，phase = BUILD
  │   │   ├── 不存在 → 新建 position，phase = BUILD
  │   │   ├── account.cash -= (price * shares + commission)
  │   │   └── 写入 fund_flow（BUY_FEE，amount = -(price*shares + commission)）
  │   └── SELL：
  │       ├── 查询 position（account_id, ts_code），不存在 → 400
  │       ├── shares_after = position.shares - trade_shares
  │       ├── shares_after > 0 → 更新 position，phase = REDUCE
  │       ├── shares_after == 0 → 删除 position 行
  │       ├── shares_after < 0 → 400（超卖）
  │       ├── proceeds = price * shares - commission - stamp_tax
  │       ├── account.cash += proceeds
  │       └── 写入 fund_flow（SELL_PROCEEDS，amount = proceeds）
  └── 若 signal_id 非空 → 路由层调用 signal_service.update_status(signal_id, "ACTED")
```

**成本价计算（加权平均成本 WAC）：**

```
new_cost = (old_shares × old_cost + trade_shares × trade_price + commission) / (old_shares + trade_shares)
```

注意：commission 摊入成本（与实盘核算惯例一致）；stamp_tax 仅 SELL 时产生，不影响成本价。

**事务边界说明：** 路由层从 `deps.py` 注入 `AccountService(session)` 和 `SignalService(repo)`，两者通过 `Depends(get_db)` 共享同一 `AsyncSession`。`update_status()` 采用尽力而为（best-effort）策略：失败时仅记录警告，成交记录**不回滚**（见代码中 `【降级说明】`）——原因是已发生的实盘交易不应因信号状态异常被撤销。

### 3.2 入金 / 出金流程

```
POST /account/deposit（body.ts_code 为空）→ flow_type = DEPOSIT，account.cash += amount
POST /account/deposit（body.ts_code 非空）→ flow_type = DIVIDEND（分红，见下）
POST /account/withdraw → flow_type = WITHDRAW，account.cash -= amount
  └── 若 cash < amount → 400（现金不足）
```

**分红识别规则：** `POST /account/deposit` 路由层判断请求体是否含 `ts_code`：有则调用 `record_dividend()`（DIVIDEND），无则调用 `deposit()`（DEPOSIT）。无需额外 query param，由请求体内容决定业务语义。

**分红成本价调整：**

```
POST /account/deposit（ts_code 非空时）
  ├── 写入 fund_flow（DIVIDEND，amount = 正值入账）
  ├── account.cash += amount
  ├── 查询 position（account_id, ts_code）
  └── 存在 → position.cost_price -= amount / position.shares
       不存在 → 仅写 fund_flow（已平仓后分红，不更新 cost_price）
```

### 3.3 账户同步流程（account/sync）

```
POST /account/sync?account_id=1
  ├── 查询所有 positions（account_id）
  ├── 批量查询 daily_quote 最新可用 close（per ts_code，用 DISTINCT ON）：
  │   SELECT DISTINCT ON (ts_code) ts_code, close
  │   FROM daily_quote
  │   WHERE ts_code = ANY(:codes)
  │   ORDER BY ts_code, trade_date DESC
  ├── 更新 position.current_price, position.market_value = shares * current_price
  ├── 更新 position.pnl_pct = (current_price - cost_price) / cost_price
  ├── 更新 account.total_assets = cash + SUM(market_value)
  └── 更新 account.synced_at = NOW()
```

注意：DISTINCT ON 语法返回每只股票最新交易日的收盘价，正确处理不同股票停牌日期不同的情况。

【降级说明】原设计文档 §2.2/§1.2 指定从 `daily_basic` 查询收盘价，实际使用 `daily_quote`（OHLCV 数据表）——语义更明确，且 `daily_quote.close` 与 `daily_basic.close` 内容等价。恢复条件：若后续需要 `daily_basic` 专属字段（如复权因子），再统一切换。

### 3.4 配置更新流程

```
PUT /settings
  ├── 查询 user_config（config_key）
  ├── 若存在：old_value = current value
  │   不存在：old_value = None
  ├── 写入 user_config_history（old_value, new_value, change_note）
  ├── upsert user_config（config_key, config_value, updated_at = NOW()）
  └── 返回更新后的 UserConfigItem

POST /settings/config-history/{id}/revert
  ├── 查询 user_config_history（id）不存在 → 404
  ├── 读取 history.config_key + history.old_value（恢复到变更前的值）
  ├── 若 history.old_value 为 None → 400（首次创建记录，无前值可回退）
  ├── 等价执行 PUT /settings（config_key, config_value = history.old_value）
  └── 返回恢复后的 UserConfigItem
```

---

## 4. AccountService 接口

```python
class AccountService:
    def __init__(self, session: AsyncSession) -> None: ...

    # 账户
    async def get_account(self, account_id: int) -> Account | None
    async def get_default_account(self) -> Account | None
    """获取第一个账户（V1.0 单账户场景）。"""
    async def sync_account(self, account_id: int) -> Account
    """从 daily_quote（DISTINCT ON）更新持仓当前价/市值/盈亏，重算 total_assets。"""

    # 持仓
    async def get_positions(self, account_id: int) -> list[Position]
    async def get_all_positions(self) -> list[Position]
    """供 Phase 7 DailyPipeline 获取全部活跃持仓（跨账户）。"""
    async def add_position(
        self, account_id: int, ts_code: str, shares: int, cost_price: float,
        open_date: date | None = None, phase: str | None = "BUILD",
    ) -> Position
    """直接新增持仓（不经过 trade_record，用于导入历史持仓）。"""
    async def update_position(
        self, position_id: int,
        current_price: float | None = None,
        phase: str | None = None,
    ) -> Position
    """PATCH /positions/{id} 对应：更新当前价或 phase（用户手动覆盖）。"""

    # 成交录入
    async def record_trade(
        self, account_id: int, ts_code: str, trade_type: str,  # BUY/SELL
        trade_date: date, price: float, shares: int,
        commission: float = 0.0, stamp_tax: float = 0.0,
        signal_id: int | None = None, note: str | None = None,
    ) -> TradeRecord
    """写入 trade_record + 更新 position + 写入 fund_flow + 更新 account.cash。
    BUY：WAC 成本价，phase=BUILD。SELL 超卖 → 抛 ValueError。
    """

    # 资金流水
    async def deposit(
        self, account_id: int, amount: float, trade_date: date,
        note: str | None = None,
    ) -> FundFlow
    async def withdraw(
        self, account_id: int, amount: float, trade_date: date,
        note: str | None = None,
    ) -> FundFlow
    """amount 为正数，内部写入负值 fund_flow。cash 不足 → 抛 ValueError。"""
    async def record_dividend(
        self, account_id: int, ts_code: str, amount: float, trade_date: date,
        note: str | None = None,
    ) -> FundFlow
    """手动录入分红：写入 DIVIDEND fund_flow + cash += amount + 调整 cost_price（若持仓存在）。

    V1.0 整改 Batch 2 — B2-2 排查结论：cost_price -= amount / shares 与 Tushare adj_factor
    （后复权默认含分红调整）**不存在双重计算**——cost_price 仅参与
    AccountService 账户层 pnl_pct 展示（前后均为非复权 daily_quote.close）+
    SignalGenerator 加仓判定（cost_deviation 比较，前后均为非复权 close）；
    **不参与 BacktestEngine（独立 BacktestPosition + adj_close）**
    **不参与 PerformanceService（基于 DailyPortfolioValue 快照）**。
    未来若引入 cost_price 参与绩效或回测，必须先评估前/后复权双轨记录。
    """
    async def get_cashflow(
        self, account_id: int,
        flow_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100, offset: int = 0,
    ) -> tuple[list[FundFlow], int]
    """返回 (流水列表, total_count)，支持分页。"""
```

**AccountService 与 Repository 层的关系：** AccountService 直接持有 `AsyncSession`（无独立 AccountRepository 层），属于账户数据域的设计例外：

- MarketDataRepository 仅管理市场行情/财务/评分数据；账户/持仓/流水是不同的数据域，不共用 Repository
- `record_trade()` 需要原子性写入 trade_record + position + fund_flow + account.cash（4 张表），直接操作 Session 比引入独立 Repository 层更清晰
- 参考：system_design §3 文件结构注释（`account_service.py # 含资金流水 CRUD（FundFlow）`）

---

## 5. SettingsService 接口

```python
class SettingsService:
    def __init__(self, session: AsyncSession) -> None: ...

    async def get_settings(self) -> list[UserConfig]
    """返回全部 user_config 记录（V1.0 不过滤 user_level）。"""

    async def upsert_setting(
        self, config_key: str, config_value: dict,
        change_note: str | None = None,
    ) -> UserConfig
    """写 user_config + 自动写 user_config_history（old_value = 当前值，可为 None）。"""

    async def get_config_history(
        self, config_key: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[UserConfigHistory], int]

    async def revert_config(self, history_id: int) -> UserConfig
    """回退：读取 history.old_value → 等价调用 upsert_setting（恢复到变更前状态）。
    不存在 → 抛 ValueError。old_value 为 None（首次创建记录，无前值）→ 抛 ValueError。
    """
```

---

## 6. Schema 定义

### 6.1 `schemas/account.py`

```python
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

# 账户概览
class AccountSummary(BaseModel):
    id: int
    name: str
    account_type: str          # REAL/PAPER
    broker: str | None
    total_assets: float | None
    cash: float | None
    synced_at: datetime | None

# 持仓明细
class PositionItem(BaseModel):
    id: int
    account_id: int
    ts_code: str
    shares: int
    cost_price: float | None
    current_price: float | None
    market_value: float | None
    pnl_pct: float | None      # 小数，如 0.12 = 12%
    open_date: date | None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None

class PositionCreate(BaseModel):
    """POST /positions：直接导入历史持仓。"""
    account_id: int
    ts_code: str
    shares: int
    cost_price: float
    open_date: date | None = None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None = "BUILD"

class PositionUpdate(BaseModel):
    """PATCH /positions/{id}：用户手动更新当前价或 phase。"""
    current_price: float | None = None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None = None

# 成交录入
class TradeRecordCreate(BaseModel):
    account_id: int
    ts_code: str
    trade_type: Literal["BUY", "SELL"]
    trade_date: date
    price: float
    shares: int
    commission: float = 0.0
    stamp_tax: float = 0.0
    signal_id: int | None = None
    note: str | None = None

class TradeRecordItem(BaseModel):
    id: int
    account_id: int
    ts_code: str
    trade_type: str
    trade_date: date
    price: float | None
    shares: int | None
    amount: float | None
    commission: float | None
    stamp_tax: float | None
    signal_id: int | None
    note: str | None
    created_at: datetime | None

# 资金流水
class FundFlowCreate(BaseModel):
    """POST /account/deposit 和 /account/withdraw 共用。
    deposit 路由：ts_code 有值 → DIVIDEND（分红），无值 → DEPOSIT（入金）。
    withdraw 路由：flow_type 固定为 WITHDRAW。
    """
    account_id: int
    amount: float              # 正数
    trade_date: date
    ts_code: str | None = None # 分红时必填，路由层检查
    note: str | None = None

class FundFlowItem(BaseModel):
    id: int
    account_id: int
    flow_type: str
    amount: float
    trade_date: date
    ts_code: str | None
    related_trade_id: int | None
    note: str | None
    created_at: datetime | None

class CashflowResponse(BaseModel):
    items: list[FundFlowItem]
    total: int
```

### 6.2 `schemas/settings.py`

```python
from datetime import datetime

from pydantic import BaseModel

class UserConfigItem(BaseModel):
    id: int
    config_key: str
    config_value: dict
    user_level: str
    description: str | None
    updated_at: datetime | None

class UserConfigUpdate(BaseModel):
    config_key: str
    config_value: dict
    change_note: str | None = None

class UserConfigHistoryItem(BaseModel):
    id: int
    config_key: str
    old_value: dict | None
    new_value: dict
    changed_at: datetime | None
    change_note: str | None

class ConfigHistoryResponse(BaseModel):
    items: list[UserConfigHistoryItem]
    total: int
```

---

## 7. API 端点规格

所有端点均需 JWT 鉴权（`Authorization: Bearer <token>`）。

### 7.1 持仓管理 `/positions`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/positions` | 获取账户持仓列表 |
| POST | `/positions` | 直接新增持仓（导入历史数据） |
| PATCH | `/positions/{id}` | 更新持仓当前价或 phase |

**GET /positions**

Query params: `account_id: int`（必填）

Response: `{"code": 0, "data": [PositionItem, ...], "msg": "ok"}`

**POST /positions**

Body: `PositionCreate`

Response: `{"code": 0, "data": PositionItem, "msg": "ok"}`

错误：`account_id` 不存在 → 404

**PATCH /positions/{id}**

Body: `PositionUpdate`（`phase` 不在合法值集时 Pydantic 自动返回 422）

Response: `{"code": 0, "data": PositionItem, "msg": "ok"}`

错误：`id` 不存在 → 404

### 7.2 账户管理 `/account`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/account` | 账户概览（总资产/现金/同步时间） |
| POST | `/account/sync` | 触发账户盯市同步（更新持仓当前价/市值） |
| POST | `/account/trades` | 录入成交记录 |
| POST | `/account/deposit` | 录入入金（ts_code 有值时识别为分红，调用 record_dividend） |
| POST | `/account/withdraw` | 录入出金 |
| GET | `/account/cashflow` | 资金流水查询 |

**GET /account**

Query params: `account_id: int | None`（省略时返回第一个账户）

Response: `{"code": 0, "data": AccountSummary, "msg": "ok"}`

错误：无账户记录 → 404（V1.0 账户通过初始化脚本预置，见 §9）

**POST /account/sync**

Query params: `account_id: int`（与 GET /account 保持一致）

Response: `{"code": 0, "data": AccountSummary, "msg": "ok"}`

**POST /account/trades**

Body: `TradeRecordCreate`

Response: `{"code": 0, "data": TradeRecordItem, "msg": "ok"}`

错误：超卖（卖出数量 > 持仓数量）→ 400；`account_id` 不存在 → 404

注意：路由层在 record_trade 成功后，若 signal_id 非空，调用 `signal_service.update_status(signal_id, "ACTED")`；该调用为尽力而为（best-effort），失败时仅记录警告，成交记录**不回滚**（见代码中 `【降级说明】`）。

**POST /account/deposit**

Body: `FundFlowCreate`

- `ts_code` 为空 → 路由层调用 `deposit()`（flow_type=DEPOSIT）
- `ts_code` 非空 → 路由层调用 `record_dividend()`（flow_type=DIVIDEND）

**实现备注（路由层必须添加注释）：** `ts_code` 的存在与否是隐式分支条件——用户若误传 `ts_code` 将静默触发分红逻辑而非普通入金。单管理员场景下接受此风险，但路由层须在分支处添加注释说明，防止维护者误判：

```python
# 隐式分支：ts_code 存在 → 分红（DIVIDEND），否则 → 入金（DEPOSIT）
# 注意：用户误传 ts_code 会静默走分红路径；单管理员场景下可接受
if body.ts_code:
    flow = await account_service.record_dividend(...)
else:
    flow = await account_service.deposit(...)
```

Response: `{"code": 0, "data": FundFlowItem, "msg": "ok"}`

**POST /account/withdraw**

Body: `FundFlowCreate`（`ts_code` 字段忽略；cash 不足 → 400）

Response: `{"code": 0, "data": FundFlowItem, "msg": "ok"}`

**GET /account/cashflow**

Query params: `account_id: int`（必填）、`flow_type: str | None`、`start_date: date | None`、`end_date: date | None`、`limit: int = 50`、`offset: int = 0`

Response: `{"code": 0, "data": CashflowResponse, "msg": "ok"}`

### 7.3 用户配置 `/settings`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/settings` | 获取全部用户配置 |
| PUT | `/settings` | 更新单项配置（自动写入变更历史） |
| GET | `/settings/config-history` | 查询配置变更历史 |
| POST | `/settings/config-history/{id}/revert` | 回退到指定历史配置（恢复到变更前的 old_value） |

**GET /settings**

Query params: 无（V1.0 返回全部配置项）

Response: `{"code": 0, "data": [UserConfigItem, ...], "msg": "ok"}`

**PUT /settings**

Body: `UserConfigUpdate`

Response: `{"code": 0, "data": UserConfigItem, "msg": "ok"}`

**GET /settings/config-history**

Query params: `config_key: str | None`、`limit: int = 50`、`offset: int = 0`

Response: `{"code": 0, "data": ConfigHistoryResponse, "msg": "ok"}`

**POST /settings/config-history/{id}/revert**

恢复到指定历史记录的 `old_value`（变更前的状态）。

Response: `{"code": 0, "data": UserConfigItem, "msg": "ok"}`

错误：`id` 不存在 → 404；`old_value` 为 None（首次创建记录，无前值）→ 400

---

## 8. 跨 Phase 依赖说明

| 调用方 | 被调用方 | Phase | 说明 |
|--------|---------|-------|------|
| 路由层（account.py） | SignalService.update_status(signal_id, "ACTED") | Phase 5 ✓ | 录入成交后回调；无独立 mark_acted 方法 |
| Phase 7 DailyPipeline | AccountService.get_all_positions() | Phase 6（本 Phase）| CP2/CP3 需要持仓数据 |
| Phase 7 DailyPipeline | AccountService.mark_to_market() | Phase 7 实现 | Phase 6 不实现此方法，Phase 7 添加 |
| Phase 7 DailyPipeline | 分红自动处理 | Phase 7 扩展 | Phase 6 仅手动录入分红 |

---

## 9. 数据库（Phase 1 已建立）

以下表已在 Phase 1 完成建表和迁移，Phase 6 **不新增迁移文件**：

| 表 | ORM 文件 | 说明 |
|----|---------|------|
| `account` | `models/account.py` | 账户主表 |
| `position` | `models/account.py` | 持仓（含 phase 字段） |
| `trade_record` | `models/account.py` | 成交记录 |
| `fund_flow` | `models/account.py` | 资金流水 |
| `user_config` | `models/system.py` | 用户业务配置 |
| `user_config_history` | `models/system.py` | 配置变更历史 |

**V1.0 账户初始化：** 账户记录通过初始化脚本预置（单账户，`account_type=REAL`，初始现金由用户配置中 `account_initial_cash` 决定）。不提供 API 创建端点；`POST /account`（创建账户）作为 V1.5 多账户扩展点预留。

---

## 10. 测试计划

### 单元测试 `tests/unit/`

Phase 6 无 Engine 层纯函数，单元测试覆盖可提取为纯函数的成本价计算逻辑：

- `test_account_logic.py`：WAC 成本价计算（参数化：首次建仓、多次加仓、加仓带佣金）；SELL 超卖返回 ValueError

### E2E 测试 `tests/e2e/`

- `test_positions_api.py`：GET /positions 401/200；POST /positions 200/404；PATCH /positions/{id} 200/404/422（非法 phase）
- `test_account_api.py`：GET /account 401/200/404；POST /account/sync 200；POST /account/trades BUY/SELL/超卖/无鉴权；POST /account/deposit DEPOSIT/DIVIDEND；POST /account/withdraw 200/余额不足；GET /account/cashflow 200
- `test_settings_api.py`：GET /settings 200；PUT /settings 200；GET /settings/config-history 200；POST /settings/config-history/{id}/revert 200/404/old_value-None→400

### 集成测试 `tests/integration/`

- `test_int_account_service.py`：BUY→Position创建→cash扣减→fund_flow写入；SELL→Position删除→cash回款；加仓 WAC 验证；分红→cost_price调整；get_all_positions 跨账户；sync_account 价格更新（需 daily_quote 有数据）
  - **V1.0 整改 Batch 2 — B2-6 新增**：INT-ACC-10（已平仓股票分红仅写 fund_flow，不重建 Position；S7-GAP-04 回归）+ INT-ACC-11（`get_current_drawdown` 基于 daily_portfolio_value 计算回撤；B2-1 配套）
- `test_int_settings_service.py`：配置创建→更新（old_value写入history）→历史查询→回退完整流程；old_value=None 回退→ValueError

### 冒烟测试 `tests/smoke/test_api_live.py`

新增 API-34~47，覆盖 Phase 6 全部 13 个端点的认证测试：

| 测试 ID | 端点 | 场景 |
|---------|------|------|
| API-34 | GET /positions | 无鉴权 → 401 |
| API-35 | GET /account | 有鉴权 → 200（AccountSummary 结构）或 404 |
| API-36 | POST /account/trades | 无鉴权 → 401 |
| API-37 | POST /account/trades | 有鉴权，参数缺失 → 422 含 errors |
| API-38 | GET /account/cashflow | 有鉴权 → 200 含 items/total 分页结构 |
| API-39 | GET /settings | 无鉴权 → 401 |
| API-40 | GET /settings | 有鉴权 → 200 含配置列表（list） |
| API-41 | POST /settings/config-history/999/revert | 有鉴权 → 404 |
| API-42 | POST /positions | 无鉴权 → 401 |
| API-43 | PATCH /positions/999 | 无鉴权 → 401 |
| API-44 | POST /account/sync | 无鉴权 → 401 |
| API-45 | POST /account/deposit | 无鉴权 → 401 |
| API-46 | POST /account/withdraw | 无鉴权 → 401 |
| API-47 | PUT /settings | 有鉴权 → 200 含 UserConfigItem 结构 |

---

## 11. 验收标准（DoD）

| 编号 | 验收项 |
|------|--------|
| D-01 | AccountService 实现完整（record_trade / deposit / withdraw / record_dividend / sync_account / get_all_positions） |
| D-02 | SettingsService 实现完整（upsert_setting / get_config_history / revert_config，含自动写入 user_config_history） |
| D-03 | Schemas 定义完整（account.py / settings.py 模块，Literal 约束 phase 和 trade_type） |
| D-04 | REST API /account/* 6 端点全部实现并注册到 main.py |
| D-05 | REST API /positions/* 3 端点全部实现并注册到 main.py |
| D-06 | REST API /settings/* 4 端点全部实现并注册到 main.py |
| D-07 | 单元测试：WAC 成本价计算参数化测试通过（首次/加仓/含佣金/超卖 ValueError） |
| D-08 | E2E 测试全部通过（/positions、/account、/settings 三组，含 401/200/404/422） |
| D-09 | 集成测试全部通过（需 PostgreSQL；BUY→SELL 完整流程；配置 CRUD + 回退；old_value=None 回退→400） |
| D-10 | `tests/smoke/test_api_live.py` 新增 API-34~47 冒烟测试全部通过 |
| D-11 | `uv run ruff check src/ tests/` 输出 0 error |

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-10 | 初稿，Phase 6 启动核查完成，4 项设计待定全部决策 |
| v1.1 | 2026-04-10 | 根据评审报告修复：P1-01（revert_config 读 old_value 非 new_value）、P1-02（mark_acted→update_status）、P2-03（冒烟测试扩展至 API-34~47）、P2-04（Literal 约束 phase 字段）、P2-05（flow_type 由 ts_code 存在与否决定，去掉 query param）、P2-06（AccountService 豁免理由修正）；同步修复 P3-07（事务边界说明）、P3-08（sync 改为 query param）、P3-09（DISTINCT ON SQL）、P3-10（GET /account/trades 归属说明）、P3-11（账户初始化说明） |
| v1.2 | 2026-04-10 | 代码评审修复：C-01（sync_account 使用 daily_quote 代替 daily_basic，§1.2/§2.2/§3.3/§4 同步更新 + 降级说明）、C-02（事务边界 §3.1 修正：update_status 为 best-effort 不回滚成交）、C-03（cashflow 端点 start_date/end_date 改为 date\|None 类型，补 E2E 非法日期→422 测试）、C-07（record_trade 服务层加 trade_type guard）；C-04 经验证为误报（URL 路径已正确使用正斜杠）；C-06 推迟 Phase 7 |
