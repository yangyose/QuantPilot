# Phase 6 设计文档评审报告

**评审对象**：`docs/design/phases/phase6_account.md` v1.0（2026-04-10）  
**评审依据**：`QuantPilot_SDD.md`（§11 账户与交易、§14 用户配置）；`system_design.md`（§3 文件结构、§4.3 账户数据表、§5.9 信号生成接口、§6 API 端点、§9 Phase 6 行）；`phase5_signals.md`  
**评审日期**：2026-04-10  
**评审人**：Claude Code

---

## 1. 总体判断

Phase 6 设计文档整体质量良好，在以下方面表现正确：

| 维度 | 评价 |
|------|------|
| **范围对齐** | §9 Phase 6 行所有交付物均已纳入 scope ✓ |
| **设计待定决策** | §9 标注的四项设计待定（position.phase / PE_PB / 分红 / user_level）全部在 §2 作出明确决策 ✓ |
| **API 端点完整性** | system_design §6 归属 Phase 6 的 13 个端点（/positions × 3、/account × 6、/settings × 4）全部覆盖 ✓ |
| **跨 Phase 边界** | mark_to_market 推迟 Phase 7、性能归因推迟 Phase 8、分红自动化推迟 Phase 7，均有明确注记 ✓ |
| **降级注释规范** | PE/PB 降级、分红降级、user_level 降级均使用【降级说明】格式 ✓ |
| **跨 Phase 依赖表（§8）** | 三条依赖关系表述准确，Phase 属性清晰 ✓ |

**结论：设计文档在范围与整合性方面合格，但存在 2 个 P1 级、4 个 P2 级问题，需在编码前全部解决。**

---

## 2. 问题清单

### 2.1 P1 级（阻塞实现，必须在编码前修正）

#### D6-P1-01：`revert_config` 语义错误——使用 `new_value` 应为 `old_value`

**位置**：§3.4 配置更新流程、§5 SettingsService 接口注释

**问题描述**：

设计文档 §3.4 和 §5 均明确写：
```
读取 history.config_key + history.new_value（目标值）
等价执行 PUT /settings（config_key, config_value = history.new_value）
```

但"回退（revert）"的语义是**撤销某次变更，恢复到变更前的状态**，应读取 `history.old_value`（变更前的值），而非 `history.new_value`（变更后写入的值）。

**具体示例说明**：

| 历史记录 | config_key | old_value | new_value |
|---------|-----------|-----------|-----------|
| #3 | scoring_weights | `{trend: 0.3}` | `{trend: 0.5}` |

若当前值为 `{trend: 0.7}`，调用 `POST /settings/config-history/3/revert`：
- 当前设计（用 new_value）：设置为 `{trend: 0.5}` → 重放了 #3 的变更，不是回退
- 正确行为（用 old_value）：设置为 `{trend: 0.3}` → 回到 #3 变更之前的状态

**SDD 依据**：SDD §14.6 "支持 API 回退"、system_design §10.4 "支持 API 回退"，中文"回退"明确含义是"回到之前状态"。

**修正方案**：

```python
# §3.4 修正
POST /settings/config-history/{id}/revert
  ├── 查询 user_config_history（id）不存在 → 404
  ├── 读取 history.config_key + history.old_value（恢复目标值）
  ├── 若 history.old_value 为 None → 400（首次创建的记录无法回退，无前值）
  ├── 等价执行 PUT /settings（config_key, config_value = history.old_value）
  └── 返回恢复后的 UserConfigItem

# §5 修正
async def revert_config(self, history_id: int) -> UserConfig
    """回退：读取 history.old_value → 等价调用 upsert_setting。
    不存在 → 抛 ValueError。old_value 为 None（首次创建记录）→ 抛 ValueError。"""
```

---

#### D6-P1-02：`SignalService.mark_acted()` 前置条件存疑

**位置**：§1.2 前置条件

**问题描述**：

设计文档 §1.2 将以下前置条件标记为已满足（✓）：

> Phase 5：SignalService.mark_acted() 方法（信号录入时回调）✓

但 Phase 5 代码审查报告（`docs/reviews/phase5_code_review_2026-04-08.md`）显示，Phase 5 SignalService 实现的是 `update_status()` 方法（含状态机 `_VALID_TRANSITIONS`），未见 `mark_acted()` 作为独立方法存在。

如果 `mark_acted()` 不存在，Phase 6 路由层的以下调用将在运行时失败：

```python
# §3.1 中的调用
await signal_service.mark_acted(signal_id)
```

**验证要求（二选一）**：

选项 A：**核实 mark_acted() 已在 Phase 5 实现**
- 检查 `backend/src/quantpilot/services/signal_service.py` 是否存在 `async def mark_acted(self, signal_id: int)` 方法
- 若存在，将 ✓ 保留，同时在 Phase 6 设计文档补充该方法签名

选项 B：**修正调用方式为 `update_status()`**
- 若 Phase 5 仅有 `update_status()`，则将 §1.2 注记改为：
  > Phase 5：SignalService.update_status(signal_id, "ACTED") 可用（无 mark_acted 独立方法）
- 将 §3.1 和路由层实现改为：
  ```python
  await signal_service.update_status(signal_id, "ACTED")
  ```

---

### 2.2 P2 级（应在实现前解决，否则影响验收）

#### D6-P2-03：冒烟测试覆盖不足

**位置**：§10 测试计划

**问题描述**：

CLAUDE.md 收尾核查要求"新增 REST API 端点须在 `tests/smoke/test_api_live.py` 补充冒烟测试，至少覆盖：无鉴权 → 401、有鉴权 → 200 含响应结构断言、关键错误路径如 404/422"。

Phase 6 新增 13 个端点，当前设计的 API-34~41（8 个场景）仅覆盖 6 个端点的认证测试：

| 端点 | 无鉴权→401 | 有鉴权→200 | 错误路径 |
|------|-----------|-----------|---------|
| GET /positions | API-34 ✓ | — | — |
| POST /positions | **未测试** | — | — |
| PATCH /positions/{id} | **未测试** | — | — |
| GET /account | — | API-35 ✓ | — |
| POST /account/sync | **未测试** | — | — |
| POST /account/trades | API-36 ✓ | — | API-37（422）✓ |
| POST /account/deposit | **未测试** | — | — |
| POST /account/withdraw | **未测试** | — | — |
| GET /account/cashflow | — | API-38 ✓ | — |
| GET /settings | API-39 ✓ | API-40 ✓ | — |
| PUT /settings | **未测试** | — | — |
| GET /settings/config-history | **未测试** | — | — |
| POST /settings/config-history/{id}/revert | — | — | API-41（404）✓ |

**修正方案**：将 API-34~41 扩展为 API-34~47，补充以下场景（至少）：

| 新增 ID | 端点 | 场景 |
|---------|------|------|
| API-42 | POST /positions | 无鉴权 → 401 |
| API-43 | PATCH /positions/{id} | 无鉴权 → 401 |
| API-44 | POST /account/sync | 无鉴权 → 401 |
| API-45 | POST /account/deposit | 无鉴权 → 401 |
| API-46 | POST /account/withdraw | 无鉴权 → 401 |
| API-47 | PUT /settings | 有鉴权 → 200 含结构断言 |

---

#### D6-P2-04：`PositionUpdate.phase` 缺少 Pydantic 枚举约束

**位置**：§6.1 Schema 定义，§7.1 PATCH /positions/{id} 规格

**问题描述**：

设计文档 §7.1 声明"phase 不在合法值集 → 422"，但 §6.1 的 Schema 定义为：

```python
class PositionUpdate(BaseModel):
    current_price: float | None = None
    phase: str | None = None    # ← 无约束，任何字符串均通过 Pydantic 校验
```

`str | None` 无法自动产生 422 验证错误，需要 `Literal` 类型约束。

**修正方案**：

```python
from typing import Literal

class PositionUpdate(BaseModel):
    current_price: float | None = None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None = None
```

同样，`PositionCreate.phase` 也应做相同约束：

```python
class PositionCreate(BaseModel):
    ...
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None = "BUILD"
```

---

#### D6-P2-05：`flow_type` 通过查询参数控制违反 REST 约定

**位置**：§7.2 POST /account/deposit 规格

**问题描述**：

设计文档写：
> Body: FundFlowCreate（`flow_type` 由查询参数 `?flow_type=DEPOSIT` 或 `?flow_type=DIVIDEND` 控制，默认 DEPOSIT）

但 `FundFlowCreate` 不含 `flow_type` 字段。这导致：
1. 资源类型（DEPOSIT vs DIVIDEND）由 URL 参数决定，而非请求体，违反 REST 资源语义
2. POST /account/deposit 路径本身已暗示 DEPOSIT，再允许 `?flow_type=DIVIDEND` 传入分红使路径语义模糊
3. DIVIDEND 是分红（需要 ts_code 必填），DEPOSIT 是普通入金（ts_code 可选），两者业务逻辑不同

**修正方案（二选一）**：

选项 A（推荐）：将 `flow_type` 移入请求体，两条路由各自只接受对应类型：
```python
# POST /account/deposit: 只接受 DEPOSIT
# POST /account/deposit（分红）: 路由层检查 ts_code 非空时调用 record_dividend()

class FundFlowCreate(BaseModel):
    account_id: int
    amount: float
    trade_date: date
    ts_code: str | None = None   # 分红时必填，路由层校验
    note: str | None = None
```

选项 B：为分红单独设立端点：
```
POST /account/dividend  → flow_type=DIVIDEND（ts_code 必填）
POST /account/deposit   → flow_type=DEPOSIT（ts_code 禁填）
```

选项 B 更语义清晰，与 SDD §11.4 的分类对齐。

---

#### D6-P2-06：AccountService 直接操作 Session 缺乏充分设计说明

**位置**：§4 AccountService 接口（末尾注释）

**问题描述**：

设计文档末尾注释：
> AccountService 直接操作 Session（无独立 Repository 层），依据 system_design §5.2 的"含资金流水 CRUD"说明

存在两个子问题：

**子问题 A**：章节引用错误。  
`system_design §5.2` 是 AdjustedPriceProvider 接口，不含"含资金流水 CRUD"说明。正确引用应为 `system_design §3`（文件结构注释：`account_service.py # 含资金流水 CRUD（FundFlow）`）。

**子问题 B**：与 CLAUDE.md 规则的张力未说明。  
CLAUDE.md 规定"读写均通过 `MarketDataRepository`，禁止在 Service/Route 层直接操作 ORM"。AccountService 直接操作 Session 构成例外，设计文档应明确豁免理由。

**修正方案**：将注释改为：

> AccountService 直接持有 AsyncSession（无独立 AccountRepository 层），属于账户数据域的豁免设计：
> - 账户/持仓/流水数据不经过 MarketDataRepository（后者仅管理市场行情/财务/评分数据）
> - 账户数据的 CRUD 逻辑简单且内聚（record_trade 需原子性写入 trade_record + position + fund_flow + account.cash），直接操作 Session 比引入独立 Repository 层更清晰
> - 参考：system_design §3 文件结构注释

---

### 2.3 P3 级（建议改进，不阻塞实现）

#### D6-P3-07：信号回调原子性未说明

**位置**：§3.1 成交录入流程、§8 跨 Phase 依赖

**问题描述**：

路由层在 `record_trade()` 成功后调用 `signal_service.mark_acted(signal_id)`（或 `update_status()`），若 `mark_acted()` 抛出异常，依赖 `get_db()` 的自动回滚机制可以同时回滚 `record_trade()` 写入——但这一事务保证依赖"两个 Service 注入同一 `AsyncSession`"的前提。

设计文档未说明此前提，实现者可能错误地给两个 Service 注入不同 session，导致事务语义丢失。

**修正方案**：在 §3.1 或 §8 中增加说明：
> 事务边界：路由层从 `deps.py` 注入 `AccountService(session)` 和 `SignalService(repo)`，两者通过 `Depends(get_db)` 共享同一 `AsyncSession`；`mark_acted()` 失败时 `get_db()` 自动回滚，`record_trade()` 写入同步撤销，无需手动管理事务。

---

#### D6-P3-08：`POST /account/sync` body 风格与 `GET /account` 不一致

**位置**：§7.2 POST /account/sync 规格

**问题描述**：

```
GET /account?account_id=1       → account_id 作为 query param
POST /account/sync              → Body: {"account_id": int}
```

两个针对同一资源（account）的端点，传递 account_id 的方式不一致。POST sync 是"对某账户执行同步操作"，account_id 作为 query param 或路径参数（`/account/{id}/sync`）更符合 REST 惯例。

**建议**：将 POST /account/sync 改为接受 query param `account_id`，与 GET /account 保持一致；或统一改为路径参数设计（超出当前 Phase 范围，可标注为设计待定）。

---

#### D6-P3-09：`account/sync` SQL 伪代码有歧义

**位置**：§3.3 账户同步流程

**问题描述**：

```sql
SELECT ts_code, close FROM daily_basic
WHERE ts_code = ANY(:codes) AND trade_date = (
  SELECT MAX(trade_date) FROM daily_basic WHERE ts_code = ...
)
```

子查询中 `WHERE ts_code = ...` 使用了 `...` 占位符，实际实现时如果处理不当，子查询会返回全表最大日期而非各股最新日期（某些股票可能停牌，最新交易日不同）。

**建议**：将伪 SQL 替换为明确的实现参考：
```sql
-- 推荐：DISTINCT ON（PostgreSQL 特有，高效）
SELECT DISTINCT ON (ts_code) ts_code, close
FROM daily_basic
WHERE ts_code = ANY(:codes)
ORDER BY ts_code, trade_date DESC;
```

---

#### D6-P3-10：无 `GET /account/trades` 查询端点

**位置**：§7.2 账户管理端点，system_design §6

**问题描述**：

Phase 6 提供 `POST /account/trades` 录入成交，但用户无法通过 API 查询历史成交记录。  
SDD §11.2 的"成交录入"功能暗示需要查询能力，但 `system_design §6` 未列出此端点。

**建议**：在设计文档 §1.1 超出范围部分增加注记：
> `GET /account/trades`（历史成交查询）→ 未在 system_design §6 端点表中，归属待定（建议 Phase 8 或 Phase 9 前端需求驱动补入）

---

#### D6-P3-11：缺少账户创建入口说明

**位置**：§7.2 GET /account 规格

**问题描述**：

`GET /account` 无账户时返回 404 并提示"请先创建账户"，但 Phase 6（乃至 system_design §6）没有 `POST /account` 创建端点。应说明账户如何被初始化（如 DB 种子脚本、管理员手动 INSERT）。

**建议**：在 §9 数据库章节或 §1.1 范围说明中补充：
> 账户记录通过初始化脚本预置（V1.0 单账户场景），不提供 API 创建接口。`POST /account` 端点作为 V1.5 多账户扩展点预留。

---

## 3. 设计整合性验证

### 3.1 system_design §9 Phase 6 行逐项对齐

| system_design §9 要求 | 设计文档覆盖 | 状态 |
|----------------------|------------|------|
| Account/Position/Trade CRUD | §4 AccountService + §7.1/7.2 | ✓ |
| FundFlow CRUD（入金/出金/分红） | §3.2 + §4 deposit/withdraw/record_dividend | ✓ |
| 手动录入 | POST /account/trades + POST /positions | ✓ |
| 一键从信号录入（signal_id 关联） | §3.1 末行 + §7.2 POST /account/trades signal_id 参数 | ✓（待 D6-P1-02 确认）|
| /settings/* API（4 端点） | §5 SettingsService + §7.3 | ✓ |
| 设计待定①：position.phase 逻辑 | §2.1 完整决策 | ✓ |
| 设计待定②：PE/PB 存储 | §2.2 降级决策（不存储，按需查） | ✓ |
| 设计待定③：分红处理链路 | §2.3 降级决策（手动录入） | ✓ |
| 设计待定④：user_level 简化 | §2.4 降级决策（L3 全量可见） | ✓ |

### 3.2 跨 Phase 接口对齐

| 接口 | 调用方（Phase 6）| 提供方 | 验证状态 |
|------|----------------|--------|---------|
| `SignalService.mark_acted()` | 路由层（§3.1）| Phase 5 | **待确认（D6-P1-02）** |
| `AccountService.get_all_positions()` | Phase 7 DailyPipeline CP2/CP3 | Phase 6（§4）| ✓ 接口已定义 |
| `AccountService.mark_to_market()` | Phase 7 DailyPipeline | **Phase 7 实现** | ✓ 正确推迟 |
| `daily_basic.close` 价格 | `sync_account()` | Phase 2 已采集 | ✓ |

### 3.3 ORM 模型对齐（Phase 1 建表）

| 表 | ORM 文件 | Phase 6 使用的字段 | 对齐状态 |
|----|---------|-----------------|---------|
| account | account.py | total_assets, cash, synced_at | ✓ |
| position | account.py | shares, cost_price, current_price, market_value, pnl_pct, phase | ✓ |
| trade_record | account.py | trade_type, price, shares, amount, commission, stamp_tax, signal_id | ✓ |
| fund_flow | account.py | flow_type, amount, related_trade_id, ts_code | ✓ |
| user_config | system.py | config_key, config_value, user_level | ✓ |
| user_config_history | system.py | old_value, new_value, config_key, changed_at | ✓ |

---

## 4. 评审总结

### 问题汇总

| 编号 | 级别 | 标题 | 涉及章节 |
|------|------|------|---------|
| D6-P1-01 | **P1** | revert_config 使用 new_value 而非 old_value，语义错误 | §3.4、§5 |
| D6-P1-02 | **P1** | mark_acted() 前置条件需核实，接口可能不存在 | §1.2、§3.1 |
| D6-P2-03 | **P2** | 冒烟测试覆盖 13 端点中仅 6 端点，缺 7 个认证测试 | §10 |
| D6-P2-04 | **P2** | PositionUpdate/PositionCreate.phase 无 Literal 约束，422 无法触发 | §6.1 |
| D6-P2-05 | **P2** | flow_type 通过 query param 控制，违反 REST 语义 | §7.2 |
| D6-P2-06 | **P2** | AccountService 绕过 Repository 层，豁免理由和章节引用均有误 | §4 |
| D6-P3-07 | P3 | 信号回调的事务原子性依赖共享 session，设计未说明 | §3.1、§8 |
| D6-P3-08 | P3 | account_id 传递方式：sync 用 body，GET 用 query，风格不一致 | §7.2 |
| D6-P3-09 | P3 | sync_account 的 SQL 伪代码歧义，可能产生错误最新价格 | §3.3 |
| D6-P3-10 | P3 | 缺少 GET /account/trades 端点，无法查询历史成交 | §7.2 |
| D6-P3-11 | P3 | 无账户创建入口，首次使用场景未说明 | §7.2、§9 |

### 建议的修改优先级

**编码前必须完成（P1）**：
1. 修正 `revert_config` 读取 `old_value`（并处理 old_value=None 边界情况）
2. 核实 `mark_acted()` 存在，或将设计改为调用 `update_status()`

**编码中同步完成（P2）**：
3. 扩展冒烟测试至 API-34~47
4. PositionUpdate/PositionCreate.phase 改为 `Literal["BUILD", "HOLD", "REDUCE"] | None`
5. 明确 flow_type 处理方案（推荐：分红单独端点 `/account/dividend`）
6. 修正 §4 注释中的章节引用及豁免理由

**DoD 不变，P3 在验收后整改（P3）**：7~11 项记录为技术债，在 Phase 7 或 Phase 9 启动前解决。

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-10 | 初版评审报告，共 11 项问题（P1×2、P2×4、P3×5） |
