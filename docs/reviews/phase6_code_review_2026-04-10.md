# Phase 6 代码评审报告

**评审对象**：Phase 6 实现代码（AccountService / SettingsService / Schemas / API Routes / Tests）  
**评审依据**：`phase6_account.md` v1.1；`CLAUDE.md`（TDD 工作流、代码规范、降级说明规则）  
**评审日期**：2026-04-10  
**评审人**：Claude Code

---

## 1. 总体评价

Phase 6 实现整体质量良好：

| 维度 | 评价 |
|------|------|
| **功能完整性** | AccountService / SettingsService 所有接口全部实现，逻辑正确 ✓ |
| **Schema 约束** | `Literal["BUILD","HOLD","REDUCE"]`、`Literal["BUY","SELL"]` 等枚举约束全部落地 ✓ |
| **事务语义** | `record_trade()` 4 张表原子写入正确；`get_db()` 共享 session 机制使用正确 ✓ |
| **测试覆盖** | 单元/E2E/集成测试覆盖设计要求的全部场景，用例质量高 ✓ |
| **WAC 计算** | `compute_wac()` 提取为纯函数，逻辑和测试均正确 ✓ |
| **revert_config** | 正确读取 `old_value`，`old_value=None` 边界处理正确 ✓ |

**结论：存在 4 个 P2 级、3 个 P3 级问题，其中 C-04（冒烟测试路径 Bug）阻塞 DoD D-10 验收，必须在验收前修复。**

---

## 2. 问题清单

### 2.1 P2 级（影响功能正确性 / 违反强制规范，验收前必须修复）

#### C-01：`sync_account()` 使用 `DailyQuote` 而非设计文档规定的 `daily_basic`，且无降级说明

**位置**：`services/account_service.py:64-107`

**问题描述**：

设计文档 §2.2 和 §3.3 均明确要求：
> `account/sync` 仅从 `daily_basic` 查询最新 `close` 价格

但实现使用 `DailyQuote`（daily_quote 表）：
```python
from quantpilot.models.market import DailyQuote

stmt = (
    select(DailyQuote.ts_code, DailyQuote.close)
    .distinct(DailyQuote.ts_code)
    ...
)
```

docstring 也改写为"从 daily_quote 更新"，而设计文档 §3.3 说 daily_basic，两者不一致。

实现使用 `DailyQuote` 在语义上更合理（OHLCV 数据比基本面表更适合取收盘价），但属于对规格的静默偏离。

CLAUDE.md 明确规定：**"实现与规格有差距时，必须在代码中用 `【降级说明】` 注释标明……禁止静默降级。同时在对应 phase 设计文档中同步标注。"**

**修正方案**：

选项 A（推荐）：在 `sync_account()` 中加 `【降级说明】` 并同步更新设计文档：
```python
# 【降级说明】设计文档 §2.2/§3.3 指定从 daily_basic 查询收盘价，
# 实际使用 DailyQuote（daily_quote 表）——该表语义更明确（OHLCV 数据），
# 且 daily_quote.close 与 daily_basic.close 内容等价。
# 恢复条件：若后续需要 daily_basic 专属字段（如复权因子），再统一切换。
```

同步将 `phase6_account.md` §2.2/§3.3 中的 "daily_basic" 改为 "daily_quote"。

---

#### C-02：`record_trade` 路由对 `update_status()` 异常的处理与设计文档存在双重矛盾

**位置**：`api/v1/account.py:90-96`，`phase6_account.md §3.1/§7.2`

**问题描述**：

**矛盾一**：设计文档 §7.2 声明 "signal_id 非空但不存在 → 404"，实现返回 200 并吞掉异常：

```python
if body.signal_id is not None:
    try:
        await signal_service.update_status(body.signal_id, "ACTED")
    except Exception:
        logger.warning(...)  # ← 吞掉，返回 200
```

**矛盾二**：设计文档 §3.1 末段声称：
> "update_status() 失败时 get_db() 自动回滚，record_trade() 写入同步撤销"

但由于 `except Exception` 吞掉了异常，异常未传播至 `get_db()`，**trade_record 不会被回滚**。设计文档的事务说明是错误的。

代码注释说明了降级的业务理由（"避免已完成交易被回滚"），这是合理的实现选择，但：
1. 缺少 `【降级说明】` 格式注释
2. 设计文档 §3.1 的事务描述错误（未更新）
3. 设计文档 §7.2 的 404 要求未在降级说明中覆盖

**修正方案**：

在 `api/v1/account.py:90-96` 处加 `【降级说明】`：
```python
# 【降级说明】设计文档 §7.2 要求 signal_id 不存在时返回 404，
# 但实现采用"成交优先"策略：trade_record 写入成功后信号状态更新为尽力而为（best-effort），
# 失败仅记录警告，不回滚成交——原因是已发生的实盘交易不应因信号状态异常被撤销。
# 恢复条件：若需强一致性，可在 record_trade() 之前预检 signal_id 存在性（独立事务）。
```

同步将 `phase6_account.md` §3.1 事务说明修正为：
> "`update_status()` 为尽力而为（best-effort）：失败时仅记录警告，成交记录**不回滚**（见代码降级说明）"

---

#### C-03：`get_cashflow()` 路由 `start_date`/`end_date` 类型为 `str` 而非 `date`，非法日期产生 500

**位置**：`api/v1/account.py:157-189`

**问题描述**：

设计文档 §7.2 定义 `start_date: date | None`，但实现为：

```python
async def get_cashflow(
    ...
    start_date: str | None = None,
    end_date: str | None = None,
    ...
) -> dict:
    from datetime import date
    sd = date.fromisoformat(start_date) if start_date else None
```

**后果**：

- FastAPI 原生支持 `date | None` 查询参数，会自动解析 ISO 日期字符串并在格式错误时返回 422
- 当前实现绕过了 FastAPI 的日期校验；传入 `"not-a-date"` 时 `date.fromisoformat()` 抛 `ValueError`，FastAPI 捕获后返回 **500**（而非 422）
- OpenAPI 文档中该参数显示为 `string` 类型而非 `date` 类型

E2E 测试 `test_aapi_17_cashflow_ok` 未覆盖非法日期路径，导致此 Bug 未被测试发现。

**修正方案**：

```python
from datetime import date   # ← 移至模块顶层（见 C-05）

async def get_cashflow(
    account_id: int,
    flow_type: str | None = None,
    start_date: date | None = None,   # ← 改为 date 类型
    end_date: date | None = None,     # ← 改为 date 类型
    limit: int = 50,
    offset: int = 0,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    flows, total = await service.get_cashflow(
        account_id=account_id,
        flow_type=flow_type,
        start_date=start_date,  # FastAPI 已解析为 date 对象
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    ...
```

同步在 E2E 测试中补充非法日期 → 422 的场景。

---

#### C-04：冒烟测试 5 个用例使用 Python 转义字符路径，测试实际访问乱码 URL，将导致断言失败

**位置**：`tests/smoke/test_api_live.py`

**问题描述**：

以下 5 个冒烟测试的 URL 路径使用了反斜杠而非正斜杠：

| 测试 | 错误路径 | 实际字符串（Python 3.12） |
|------|---------|------------------------|
| `test_api_36` | `"\api\v1\account\sync"` | chr(7)+"pi"+chr(11)+"1"+chr(7)+"ccount"+`\s`+"ync" |
| `test_api_38` | `"\api\v1\account\trades"` | chr(7)+"pi"+chr(11)+"1"+chr(7)+"ccount"+`\t`+"rades" |
| `test_api_41` | `"\api\v1\account\cashflow"` | chr(7)+"pi"+chr(11)+"1"+chr(7)+"ccount"+`\c`+"ashflow" |
| `test_api_43` | `"\api\v1\positions"` | chr(7)+"pi"+chr(11)+"1"+`\p`+"ositions" |
| `test_api_45` | `"\api\v1\positions\999"` | chr(7)+"pi"+chr(11)+"1"+`\p`+"ositions"+`\9`+"99" |

说明：在 Python 中 `\a`=BEL(chr(7))，`\v`=VT(chr(11))，`\t`=TAB(chr(9))，`\c`/`\p`/`\s`/`\9` 等在 Python 3.12 生成 `DeprecationWarning` 并保留反斜杠。httpx 发送请求时控制字符被 percent-encode，服务器收到的是乱码路径，返回 **404**，测试断言的 401/422 **必然失败**。

特别严重的是：
- `test_api_38` 断言 422，乱码路径返回 404 → 失败
- `test_api_36/41/43/45` 断言 401，乱码路径返回 404 → 失败

**这导致 DoD D-10 "API-34~47 冒烟测试全部通过" 无法达成。**

**修正方案**（直接替换为正斜杠）：

```python
# test_api_36
r = client.post("/api/v1/account/sync", params={"account_id": 1})

# test_api_38
r = client.post("/api/v1/account/trades", json={"account_id": 1}, headers=auth_headers)

# test_api_41
r = client.get("/api/v1/account/cashflow", params={"account_id": 1})

# test_api_43
r = client.get("/api/v1/positions", params={"account_id": 1})

# test_api_45
r = client.patch("/api/v1/positions/999", json={"phase": "HOLD"})
```

---

### 2.2 P3 级（建议改进，不阻塞验收）

#### C-05：`from datetime import date` 在函数体内导入，应移至模块顶层

**位置**：`api/v1/account.py:169`

```python
async def get_cashflow(...) -> dict:
    from datetime import date   # ← 函数内导入，应移至模块顶层
    sd = date.fromisoformat(start_date) if start_date else None
```

修正 C-03 后此问题自然消失（date 类型参数由 FastAPI 自动解析，无需手动 `fromisoformat`）。

---

#### C-06：`ValueError` → HTTP 状态码路由依赖脆弱字符串匹配

**位置**：`api/v1/account.py:86-88`（record_trade），`api/v1/account.py:150-153`（withdraw）

```python
if "not found" in msg.lower():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
```

此模式依赖 `AccountService` 错误消息中含英文 "not found"，与业务错误消息强耦合。若将来错误消息改为中文，路由逻辑静默失效。

与 Phase 5 C-01 同类问题（`update_status` 中也有类似模式）。

**建议**（P3 不强制）：在 `core/exceptions.py` 中定义 `NotFoundError(ValueError)`，Service 层抛 `NotFoundError`，路由层捕获 `NotFoundError` → 404。这样语义清晰且不依赖字符串内容。

---

#### C-07：`record_trade()` 服务层不校验 `trade_type`，非法值产生数据不一致

**位置**：`services/account_service.py:176`

```python
async def record_trade(self, ..., trade_type: str, ...) -> TradeRecord:
```

服务层接受任意字符串。若传入 `"HOLD"`（非法值），代码逻辑走到 `if trade_type == "BUY": ... elif trade_type == "SELL": ...`，两个分支均不匹配，导致：
- `trade_record` 已写入（数据库有记录）
- `position` 和 `fund_flow` **未更新**（数据不一致）

通过 API 调用因 `Pydantic Literal["BUY","SELL"]` 校验不会发生此问题，但直接调用服务层（如测试或内部调用）存在风险。

**建议**：在服务层加保护：
```python
if trade_type not in ("BUY", "SELL"):
    raise ValueError(f"非法 trade_type：{trade_type}")
```

---

## 3. 整合验证

### 3.1 DoD 逐项核查

| DoD 编号 | 验收项 | 状态 |
|---------|-------|------|
| D-01 | AccountService 全部方法实现（record_trade / deposit / withdraw / record_dividend / sync_account / get_all_positions） | ✓ |
| D-02 | SettingsService 全部方法实现（upsert_setting / get_config_history / revert_config，含 user_config_history） | ✓ |
| D-03 | Schemas 定义完整（Literal 约束 phase 和 trade_type） | ✓ |
| D-04 | REST API /account/* 6 端点全部实现并注册到 main.py | ✓ |
| D-05 | REST API /positions/* 3 端点全部实现并注册到 main.py | ✓ |
| D-06 | REST API /settings/* 4 端点全部实现并注册到 main.py | ✓ |
| D-07 | WAC 成本价计算参数化单元测试通过（首次/加仓/含佣金/超卖 ValueError） | ✓ |
| D-08 | E2E 测试全部通过（/positions、/account、/settings 三组，含 401/200/404/422） | ✓ |
| D-09 | 集成测试全部通过（BUY→SELL 完整流程；配置 CRUD + 回退；old_value=None 回退→400） | ✓ |
| D-10 | tests/smoke/test_api_live.py 新增 API-34~47 冒烟测试全部通过 | **✗ C-04 阻塞** |
| D-11 | `uv run ruff check src/ tests/` 输出 0 error | 待验证（C-05 可能触发 lint 提示） |

### 3.2 关键路径验证

| 设计规格 | 实现 | 验证结果 |
|---------|------|---------|
| BUY：WAC 更新 + phase=BUILD + BUY_FEE fund_flow | ✓ account_service.py:225-252 | ✓ INT-ACC-01/02 验证 |
| SELL：shares_after<0 → ValueError（超卖） | ✓ account_service.py:258-262 | ✓ INT-ACC-05 验证 |
| SELL：shares_after==0 → delete position | ✓ account_service.py:267-268 | ✓ INT-ACC-04 验证 |
| DIVIDEND：cost_price -= amount/shares | ✓ account_service.py:368 | ✓ INT-ACC-06 验证 |
| revert_config 读 old_value（非 new_value） | ✓ settings_service.py:98-105 | ✓ INT-SET-04/05 验证 |
| old_value=None → 400 | ✓ settings_service.py:98-101 / settings.py:80-83 | ✓ test_sapi_08 验证 |
| DISTINCT ON 取各股最新价 | ✓ account_service.py:84-89 | ✓ INT-ACC-08 验证 |
| upsert_setting 写 history.old_value = 当前值 | ✓ settings_service.py:33-45 | ✓ INT-SET-01/02 验证 |

---

## 4. 评审总结

### 问题汇总

| 编号 | 级别 | 标题 | 关键影响 |
|------|------|------|---------|
| C-01 | **P2** | sync_account 使用 DailyQuote 而非 daily_basic，无降级说明 | CLAUDE.md 违规（禁止静默降级） |
| C-02 | **P2** | update_status 异常被吞，信号不回滚，设计文档事务描述错误 | 双重静默降级违规 |
| C-03 | **P2** | start_date/end_date 为 str 类型，非法日期返回 500 | 正确性 Bug |
| C-04 | **P2** | 5 个冒烟测试 URL 含 Python 转义字符，测试必然失败 | D-10 DoD 阻塞 |
| C-05 | P3 | `from datetime import date` 在函数体内导入 | 随 C-03 修复自动消除 |
| C-06 | P3 | ValueError→HTTP 状态码路由依赖字符串匹配，脆弱 | 可维护性问题 |
| C-07 | P3 | record_trade() 服务层不校验 trade_type，非法值数据不一致 | 防御性编程缺失 |

### 建议修复优先级

**验收前必须完成（P2）**：
1. C-04：修复 5 个冒烟测试的反斜杠路径（5 分钟可修复，但 D-10 阻塞）
2. C-03：将 `start_date`/`end_date` 改为 `date | None`，补 E2E 非法日期测试
3. C-01：添加 `【降级说明】` 注释 + 更新设计文档 §2.2/§3.3
4. C-02：添加 `【降级说明】` 注释 + 更新设计文档 §3.1 事务描述

**P3 在 Phase 7 前整改**：C-05/06/07

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-10 | 初版代码评审，共 7 项问题（P2×4、P3×3） |
| v1.1 | 2026-04-10 | 修复核查：C-01/C-02/C-03/C-07 已修复；C-04 确认为评审误报（文件实际为正斜杠，grep 工具在 Windows 显示有误）；C-05 随 C-03 消除；C-06 推迟 Phase 7。所有 P2 关闭，Phase 6 验收通过。 |
