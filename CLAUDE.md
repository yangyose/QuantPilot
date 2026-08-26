# QuantPilot — Claude 工作指南

> 量化领航：个人量化交易决策辅助系统
> 单仓 monorepo，后端在 `backend/`。

`~/.claude/CLAUDE.md`（个人全局）只放**跨项目通用的工作原则**——权限边界、最小改动、
验证与汇报、破坏性操作的通则。它刻意不含任何技术栈细节。

因此**所有具体技术陷阱都在本文件**：§4 = 项目工程规范 + 踩过的坑（每条都有实证来源），
§5 = Phase 流程，§6 = 当前进度与运维红线。全局与本文件冲突时，以本文件 §0 宪法为准。

---

## 0. 项目宪法（最高优先级 · 不可妥协）

> 本节压倒后续所有章节、所有文档、所有"惯例"。冲突时以本节为准。

**元目标**：所有决策最终为「**帮助用户获取最大利益**」服务。任何与此目标冲突的便利、习惯、效率取舍都让位于它。

衍生六条不可妥协的原则——它们是元目标在工程实践中的展开：

### C-1：保护用户资产
生产数据、用户配置、未提交的工作都是用户资产。

- 三个 DB 端口**永远不要混**：生产 5432（容器内部，无 host 映射）/ 测试 5433（pytest 会 DROP 全表）/ 本地算力中心 5434（`docker-compose.backtest-local.yml`，全量副本，跑重计算作业）
- **严禁**对含真实数据的 DB 跑 `pytest tests/integration/`（conftest 会 alembic downgrade base 把所有表 DROP）
- 生产栈操作必须显式 `docker compose -f docker-compose.prod.yml --env-file .env.prod ...`（默认 `docker-compose.yml` 是 dev 配置，挂错卷会让 psql 看到空库）
- 破坏性动作（`alembic downgrade` / `DROP` / `rm -rf` / `git reset --hard` / `force push` / 删含 pg_data 卷的容器）执行前**必须**取得用户单独确认。"上次批准过"≠"永久授权"。

### C-2：质量是用户利益的前提
不可靠的系统会误导用户的交易决策——用户的损失是真实的钱。

- **TDD 不可绕过**：每个 phase / 任务先启动核查 → 先 RED（写失败的测试）→ 再 GREEN（写实现）
- 跨会话 / `/compact` 恢复后第一件事：跑 `pytest tests/unit/ tests/e2e/ tests/integration/ -q` 建立真实基线。**不信任**摘要中的"X tests passed"
- 收尾门槛：`uv run ruff check src/ tests/` 输出 **0 error**；新增 REST 端点必须有冒烟测试覆盖 401/200/404/422

### C-3：现在的问题现在处理
**推迟不是节省，是债务利息**——V1.0 评审推迟 50+ 项到 V1.5 → 实际进入 V1.0 时发现核心评分公式缺陷阻断用户达成核心目标 → 重新定位 V1.0 + 5 个补救 phase。

默认立即修。只有四类充分理由之一才允许推迟（依赖外部决策 / 跨 phase 大重构 / 验收标准未定义 / 物理资源约束），且必须落到「推迟三链」防丢失。禁止"伪推迟"（「不影响主路径」「范围外」「Phase X 一起做」「小改进」都不算）。四类理由判定 + 三链机制见 §5.4。

### C-4：不静默掩盖问题
被吞掉的异常最终会以错误的交易信号、消失的持仓、归零的 NAV 形式损害用户。

- `except Exception: return [] / None / {}` 必须 `logger.exception(...)`（**不可用 DEBUG**——生产日志看不见）
- 业务上确需降级时用 `【降级说明】` 注释标明：当前降级内容 / 原因 / 恢复条件；同时在对应 phase 设计文档同步标注
- 应用层禁止用占位值（`50`、`0`、`""`）替代缺失数据而不标注来源

### C-5：SDD 是权威源
设计文档与 SDD 冲突以 SDD 为准；任何范围变更必须先回写 `system_design §9`，再开始写 phase 设计文档。

- 禁止孤儿：`system_design §3/§5` 中所有模块、`§6` 中所有 API 端点必须在某个 phase 有且仅有一个明确归属
- 推迟的模块在新 phase 设计文档引言处显式注明「模块 X 推迟至 Phase N，原因：……」
- 设计文档正文的编号规约（禁止外部追踪编号）见 §5.5。

### C-6：每个错误都要沉淀到「会再被读到的地方」
只在脑子里记 = 必然复发。换会话、换机器、换人之后，同一个坑必须被**同一处**拦住。

任何踩到的错误（自己写的 bug、误判、工具/命令陷阱、环境坑）都不止于"这次改对"：

1. **修在源头**——代码 / 脚本 / skill / hook 命令本身，让错误不可能再犯，而不是靠下次记得
2. **留一句"为什么"**——在对应的代码注释 / 设计文档 / 本文件 §4 / memory 里写清楚成因
3. **判据**：下次同样的错误，会被上面哪一处拦住？答不上来 = 没沉淀完

沉淀本身若引入新错误（修 A 命令时埋了 B 缺陷），同样适用本条——连环吸取，直到该处稳态。

---

## 1. 关键文档

| 文档 | 路径 | 用途 |
|------|------|------|
| SDD | `docs/spec/QuantPilot_SDD.md` | 系统需求与功能规范（权威） |
| 系统设计 | `docs/design/system_design.md` | 架构 + Phase 规划 §9 |
| Phase N 设计 | `docs/design/phases/phaseN_*.md` | 当前 phase 详细设计（开始任务前必读） |
| 开发指南 | `docs/guides/dev_setup.md` | 环境配置 + 命令 |
| 部署指南 | `docs/guides/deployment.md` | HTTPS / 备份 / 故障树 |
| 第二台机迁移 | `docs/guides/machine_migration.md` | 双活纪律（谁是权威方）+ 新机分步 runbook + 验收清单；**§5 记录记忆快照里已被推翻的结论**，与 memory 冲突时以该节为准 |
| 个人全局规则 | `~/.claude/CLAUDE.md` | 跨项目通用**工作原则**（权限 / 最小改动 / 验证 / 汇报）；不含技术细节，勿往里加项目知识 |
| 全局规则历史快照 | `docs/reference/global-claude-md/` | 上条文件不在任何仓库里 → 此处按 `NNNN_` 序号归档历史版本；**换版前**先快照并按 README 规程判定被删内容的去向 |

---

## 2. 项目结构

```
backend/src/quantpilot/
├── engine/        # Engine 层（严格无 IO，纯函数）
├── data/          # adapters / calendar / repository / validators
├── services/      # 编排层，含 IO
├── api/v1/        # REST 路由；所有 DI 在 api/deps.py
├── models/        # SQLAlchemy ORM
├── schemas/       # Pydantic
├── core/          # config / database / security / exceptions
└── pipeline/      # scheduler / daily_pipeline / monthly_scheduler
tests/{unit,e2e,integration,smoke}
alembic/versions/  # NNNN_<描述>.py
```

**技术栈**：Python 3.12 / FastAPI / SQLAlchemy 2.0 asyncio / PostgreSQL 15 + asyncpg / Redis 7 / Alembic / pandas 2.2 + pandas-ta / Tushare Pro（主）+ AKShare（备）/ APScheduler / uv + hatchling / pytest + pytest-asyncio / ruff（line-length=100, py312）

---

## 3. 常用命令（在 `backend/` 目录执行）

```bash
uv sync --group dev
uv run pytest tests/unit/ tests/e2e/ -q       # 无 DB，秒级
uv run pytest tests/integration/ -q           # 需 DB:5433 + alembic upgrade head
uv run ruff check src/ tests/                 # 收尾门槛
uv run alembic upgrade head
docker compose -f docker-compose.dev.yml up -d db redis
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d  # 生产必须显式
```

---

## 4. 工程规范与技术陷阱

> 本章每一条都来自真实事故或代码审查，不是"最佳实践"清单。改动相关代码前先扫对应小节。

### 4.1 ORM / 数据库

- `Mapped[]` 用 Python 类型 / `mapped_column()` 用 SQLAlchemy 类型；用 `DeclarativeBase` 类继承（不用 `declarative_base()`）
- `get_db()` 自动 commit；`async with AsyncSessionLocal()` 创建的 session **必须显式 commit**
- 混合 session 模式：Service 内部用 per-iteration session 同时其他工作单元走 `self._repo` 时，调用方需在 outer 块退出前显式 `await session.commit()`
- 市场数据走 `MarketDataRepository`；Route 层禁止绕过 Service 直接操作 ORM
- upsert：`insert(...).on_conflict_do_update()`；`updated_at` 显式写 `func.now()`
- **bulk upsert 必须分批**：asyncpg 二进制协议 16-bit 占位符总数上限 **32767**，`n_rows × n_cols` 超限直接崩。`_BATCH_SIZE=500` 循环 `pg_insert.values(batch)` 是稳妥下限。合成数据测试绕得过去（< 3000 行不触发），需 ≥ 3000 行场景才抓得到
- **upsert 前 `df.where(pd.notna(df), None)`** 把 NaN/NaT 转 None：pandas NaN 经 `to_dict("records")` 是 `float('nan')`，asyncpg 原样写进 PostgreSQL NUMERIC 是特殊值 `'NaN'`（**≠ NULL**）→ 下游 `IS NOT NULL` 误判、数值过滤失效
- **`on_conflict_do_update().returning()` 拿到旧对象**：SQLAlchemy 身份映射缓存，`flush()` 后需 `await session.refresh(obj)` 强制刷新

### 4.2 API 响应格式

```python
{"code": 0, "data": {...}, "msg": "ok"}                # 成功
{"code": 401, "data": None, "msg": "错误说明"}          # 错误
{"code": 422, "data": None, "msg": "请求参数校验失败",
 "errors": [{"field": "body.username", "reason": "..."}]}
```

**所有 DI 函数**（`get_*_service`、`get_repo`）统一放 `api/deps.py`，禁止在路由文件内定义（路由文件只允许 `from quantpilot.api.deps import ...`）。

### 4.3 Tushare 数据采集（专项 quirks）

- 所有 Tushare 调用走 `_call()` 异步包装（`asyncio.to_thread` + Semaphore）；单位换算在 adapter 内完成
- **`index_weight`** 月度稀疏 → `fetch_index_components` 用 `[trade_date-60d, trade_date]` range query 保 PIT
- **`fina_indicator`** 必须 `period + ts_code` 组合（50 只/批 + `asyncio.sleep(0.3)`）；period-only 全市场调用会静默吞异常把 5 字段填 NULL
- **`namechange`** start/end 是 ann_date（不是 trade_date）→ `ingest_history` 回溯起点必须设 `ingest_start - 5y`，否则早就叫 \*ST 的股票全部缺失
- **`dividend`** 是正确接口名（**不是** `fina_dividend`）→ `pro.dividend(ex_date=...)` 返回 cash_div_tax（税前每股，元）
- **`ingest_history`**：
  - per-day 独立 `AsyncSessionLocal`（共用 outer session 会让 asyncpg 语句级 savepoint 形成「daily_quote 进库 / financial 全空」混合状态）
  - 断点续传查 `repo.get_fully_ingested_dates()`（daily_quote ∩ financial_data 双表交集），禁用单表 `MAX(trade_date)`
- **完整性校验 `prev_count`** 必须 PIT 活股数：用 `get_active_stock_codes_as_of(trade_date)` 而非 `get_active_stock_codes()` 当前快照（5 年前对比当前快照必然 < 95% → 整日 rollback → 5y 回填跑完仍空 DB）
- **`refill_history.py` 双模式**：默认增量（不删，走双表交集断点续传）/ `--force-clean`（DELETE 4 表重灌）/ `--dry-run-plan`（预检）

### 4.4 Engine 层

- 严格无 IO（数据库 / 文件 / 网络），只做纯函数计算；需要 IO 由 Service 层组装数据传入。**也不许在实例上缓存中间结果**（`self._xxx_cache = ...`）：策略实例会被 `ScoringService` 跨调用复用，并发跑两次评分就串数据。需要把中间量带给同一次调用的下游（如理由文本要用 σ），走返回值——见下条
- PostgreSQL `NUMERIC` 列传 pandas_ta 前必须 `.astype(float)`（避免 `isnan` TypeError）
- **pandas_ta 传错参数名不报错**：0.4.x 把 `bbands(std=)` 拆成了 `lower_std=` / `upper_std=`，旧名落进 `**kwargs` 被**静默忽略**——列名与数值完全不变，无告警。本仓 `mean_reversion.py` 的 `std=2.0` 因此长期无效（只因默认值恰好也是 2.0 才没出事）。判据不是"读文档确认签名"，而是**写一条「改参数 → 结果必须变」的测试**：接了配置却毫无作用的代码能跑过任何只验证"不抛异常"的测试
- **策略因子矩阵禁止夹带非因子列**：`Scorer.aggregate` 是 `for col in df.columns` 逐列 Winsorize→中性化→Z-score 后**列向取均值**，**不读 `strategy.weights`**（后者只对旧的 `score()` 路径有效）。多出来的辅助列（σ、中间量、调试列）会被当成一个因子参与合成，方向还可能相反。要带辅助列就在 `compute_strategy_factors` 覆写里 `drop` 掉再返回
- **策略硬约束只能写在 `BaseStrategy.apply_constraints`**（V1.5-C C1-1 引入）：`compute_strategy_factors` 与 `score()` 同源调用它。写在 `score()` 末尾的约束在五步管线里**完全不生效**——生产曾因此让 SDD §7.2.4 的价值陷阱护栏长期失效，而 value 策略占 composite 权重 0.57~0.87。约束须在 **raw 因子域**表达：剔除类 = 命中行全列 NaN（**禁止置 0**，Z-score 后 0 是横截面均值 = 中性分而非排除）；截断类 = 逐列 `min(raw, raw.quantile(0.5))`
- **「取前 X%」不要用 `quantile(1-X)` 当阈值**：小样本上被线性插值支配——21 只剔 2 只（9.5%）、2 只剔 1 只（50%）。用 `nlargest(int(n * pct))` 按名次取，`floor` 让"不足 1 只"时不剔除任何标的。另需挡住无离散度退化（全体值相同 → 阈值等于该值 → `>=` 命中全体 → 整个策略被清空）
- **交易日窗口禁止用日历天近似**（V1.5-C C1-3，代价最大的一条：自 Initial commit 起生产每一次评分都残缺）。`ScoringService` 曾用 `_PRICE_WINDOW_DAYS = 180` 日历天取价格窗口、注释写「≈ 120 交易日」，真机实测 2026-07-01 往前 180 日历天只有 **117 个交易日**，而算 120 日收益要 ≥ 121 列 → momentum 权重 0.35 的 `rs_6m` **有效率 0/2274**（修复后 2246/2274）。同根还静默降级了第二处：`index_adj_prices` 同样不足 → `idx_return_6m` 回落 0.0 → `rs_6m` 从「相对沪深300」退化成绝对收益。两处都无告警
  - `×1.5` 经验式恰好在 120 交易日这个量级失效：A 股真实系数 365/250 ≈ 1.46，再叠春节/国庆假期聚集就越过 180。**调大系数不是修法**（下次窗口变深再撞一次）
  - 正确做法：窗口深度由**各策略自报交易日数**（`BaseStrategy.required_history_days`），Service 取全体最大值，起点用 `TradingCalendar` **精确回退 N 个交易日**（`resolve_price_window_start`）；日历深度不足才降级为日历天近似，且**必发 WARNING**
  - 计数口径：`_period_return(prices, n)` 要 **n + 1** 列（首尾各占一列），自报值记得 +1
  - **测试要钉临界点两侧**：只断言「给足列数 → 有效」时，把 required 写大 10 倍照样绿；必须同时断言「少一列 → 全 NaN」
  - 同类缺陷会成群出现在**脚本的日历回看缓冲**上（`backfill_daily_ic` / `backfill_candidate_pool` / `pipeline_multi_date` / `backfill_icir_rebalance`）：这些脚本走完整评分或 ICIR 路径，缓冲不足只表现为「跑通了但因子是残缺的」，不报错。改任一窗口参数时一并扫这几处
- 其余交易日数 → 日历天的粗略换算（非窗口深度，容错高的场景）：`calendar_days = int(history_days * 1.5)`，禁止直接 `timedelta(days=history_days)`
- APScheduler job 无法访问 `app.state`，Engine 单例须通过 `create_scheduler()` 显式 `args=[...]` 传入

### 4.5 FastAPI 项目特有

- **BackgroundTasks + UNIQUE 约束并存**：必须先 `await session.commit()` 再 `add_task()`（否则 `get_db()` 的隐式 commit 推迟到所有 BG task 跑完 → BG 写同一 UNIQUE 行被外层未 commit 行阻塞 → 循环死锁，`POST /pipeline/trigger` 真机抓到 90s 504）
- **日期查询参数直接声明 `date | None`**：FastAPI 自动解析 + 格式错误返回 422；用 `str` + 手动 `fromisoformat` 会绕过校验，非法输入变 500

### 4.6 安全

- 登录验证：先 `verify_password()`（bcrypt ~100ms）再比对用户名（防计时侧信道）
- 测试密码：用 `from tests.conftest import TEST_PASSWORD`，禁止硬编码
- 需要真实登录用户的集成测试用 `test_user` fixture（tests/integration/conftest.py，
  经 AuthService.register 建 user 行 + 空账户，密码 TEST_PASSWORD，随事务回滚）；
  旧 `override_admin_password` settings 替换方案已随 V1.5-G 登录改 DB 查询退役
- `/auth/login`（10/分钟）+ `/auth/register`（5/小时）按 IP 限频（slowapi，
  `core/rate_limit.py`）；测试全套件经 conftest autouse 关闭，限频专项 e2e 局部打开

### 4.7 环境变量

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/quantpilot
ADMIN_USERNAME=admin                    # V1.5-G 起仅供 alembic 0018 种子首用户；运行时不读
ADMIN_PASSWORD_HASH='$2b$12$...'        # 同上；bcrypt，含 $ 用单引号；0018 跑过后可移除
JWT_SECRET_KEY=<64+ 随机字符>
TUSHARE_TOKEN=...                       # 缺失则数据 API 全 503
REDIS_URL=redis://localhost:6379/0
WXPUSHER_APP_TOKEN / WXPUSHER_UID       # 缺失自动降级仅站内信
RATE_LIMIT_ENABLED / RATE_LIMIT_LOGIN / RATE_LIMIT_REGISTER  # 可选；默认 true / 10/minute / 5/hour
LOG_DIR / LOG_LEVEL / LOG_JSON
DEBUG=false
```

生产专用项（`POSTGRES_*` / `HTTP_PORT` / `CORS_ORIGINS`）见 `.env.prod.example`。

### 4.8 迁移

- 文件名 `NNNN_<描述>.py`（4 位序号）；建表按 FK 依赖分层
- 降序索引 `sa.text("col DESC")`；ORM `__table_args__` 与迁移文件保持一致
- **必须在 `backend/` 目录**执行 alembic（`alembic.ini` 在此）

### 4.9 asyncio

- **线程回调中的 event loop**：async 上下文创建线程回调时，用 `asyncio.get_running_loop()` **预捕获** loop 再传进去；**禁止**在子线程内 `asyncio.get_event_loop()`（Python 3.12 起 DeprecationWarning，且行为不可靠）
- Tushare 等同步 SDK 一律走 `asyncio.to_thread` + Semaphore 包装（见 §4.3）

### 4.10 pandas / 数值

- **MultiIndex `in` 判断 O(n) → O(1)**：循环**外**预计算 `available = set(df.index.get_level_values("ts_code"))`，循环内 `if x not in available`。循环内直接写 `ts_code in index.get_level_values("ts_code")` 是 O(n)，几千只股票 × 几千日 = 几百万次全扫
- **`rank(pct=True)` 边界**：n 个相同值 → rank = `(n+1)/(2n)`，**不是 0.5**。测试断言「全相等」用 `len(set(scores)) == 1`，别断言具体值
- **策略内加权用 `skipna=False`**：`.sum(axis=1, skipna=False)` 才能让「任一因子为 NaN」的样本被排除；默认 `skipna=True` 会把 NaN 当 0 处理，静默污染结果
- PostgreSQL `NUMERIC` 传 pandas_ta 前 `.astype(float)`（见 §4.4）

### 4.11 测试工程（pytest-asyncio 陷阱 + TDD 细则）

**跨 event loop 类（本项目已 regression 两次，最贵的一类）**

- **`asyncio_mode = "auto"` 下禁止 `@pytest.mark.anyio`** 装饰任何 test/fixture：marker 被 anyio runner 接管（loop B），fixture 仍归 pytest-asyncio（loop A）→ asyncpg waiter future 在 A 创建、test body 在 B 唤醒 → `RuntimeError: Future attached to a different loop`。**CI Linux 必现，Windows 偶发不报**。新写 async 测试一律 plain `async def test_xxx()`，不加任何 marker
- **集成测试 async engine fixture**：必须 `poolclass=NullPool`（防跨 loop 复用连接）+ **function scope**（禁 `scope="session"`）。schema 建表用单独的**同步** fixture（`scope="session"`）跑 alembic
- **禁止让全局 app engine（QueuePool）跨 loop**：测试若直接 `from quantpilot...import AsyncSessionLocal`（或调用内部自建该 session 的生产脚本）做真 commit，全局 engine 的 QueuePool + `pool_pre_ping=True` 会把**上一个测试 loop** 的连接留在池里；本测试复用时 pre_ping/close 打到已关闭的旧 loop → asyncpg `'NoneType' object has no attribute 'send'` / `RuntimeError: Event loop is closed`，且**炸在首个 DB 操作处**（极易误读成该处业务 bug）。根治：集成目录 `conftest.py` autouse fixture 每测试前 `await app_engine.dispose(close=False)`（只换池、不在当前 loop 关旧连接，残连交 GC）

**测试隔离类**

- **测试路由动态注册**：在 `client` fixture 内以 `include_in_schema=False` 注册，yield 后移除，避免污染全局路由表
- **触发「自建 session 真 commit」副作用路径时，`finally` 必须清副作用表**：被测代码若在失败/通知分支用 `session_factory()` 自建 session 真 commit（失败告警写站内信、审计流水等），只清主表（`PipelineRun` 等）会把副作用行泄漏给共享 DB 中**按字母序后跑**的测试。本地只跑"受影响子集"抓不到——受影响的是读同一副作用表的**别的测试文件**，推送前须跑全量集成
- **同一测试 DB 严禁并发两个集成 pytest 会话**：conftest 的 session 级 alembic（建表 / downgrade base）会互相拆台，典型症状是**单个**测试随机 `UndefinedTableError` 而前后测试全过（表被另一会话瞬时 DROP）

**墙钟耦合类（2026-08-19 踩到）**

- **限频 / TTL / 缓存过期类断言禁止依赖"这批请求跑得够快"**：`limits` 的 fixed-window 把窗口锚定在该 key 的**首次命中**（`MemoryStorage.incr` 计数 0→1 时写 `expirations[key] = time.time() + expiry`），**不对齐整分钟**。一批 11 次请求只要整体耗时越过 60s，计数被清零，「第 11 次必 429」静默退化成 401。判据是**批次耗时**而非机器负载：实测合跑 `unit/ + e2e/` 耗时 527s（正常 141s）时三个 `10/minute` 用例同时失败、`5/hour` 用例安然无恙——这个分界就是指纹。**单独跑该文件必过**，故极易误判为偶发并放过
- 修法是解除耦合而非放宽断言：把整批请求包进 `_burst()`，测 `monotonic()` 跨度，跨窗口就 reset 重来，连续 N 次跨不过去才判失败（那时慢的是被测系统，属于该暴露的真问题）。见 `tests/e2e/test_rate_limit_api.py`
- **守卫必须被单独钉死**：快机器上有没有守卫都全绿，不测守卫本身它就退化成装饰品、flake 原样回来。RL-06 覆盖"重试满次数后放弃"，RL-07 用可控时钟覆盖"重试后返回次批"——后者才是真正防 flake 的那条路径

**结果判定（2026-08-18 再次踩到）**

- 后台 `uv run pytest` 的"完成"通知**不可信**：可能提前发出（输出文件为空、pytest 沦为孤儿继续跑），也可能 shell 退出码为 0 而 pytest 根本没跑（cwd 不对 → `no tests ran`，exit 5/4）
- **只认两样东西**：落盘 log 里的 pytest summary 行 + 自写的 `pytest_exit=$?` 哨兵行。**不认** shell 管道退出码（`cmd | tail` 的退出码是 `tail` 的）
- 通知与输出不符时，先 `tasklist` / `ps` 确认 pytest 进程真退出，再起下一轮

**TDD 细则**

- **集成测试断言精确**：用 `== N`，不用 `<= N` 宽松上界——宽松断言会掩盖"写多了"的 bug
- **测试命名**：`tests/unit/test_<模块>.py` / `tests/e2e/test_<功能>_api.py` / `tests/integration/test_<主题>.py`
- **合成日期跳周末**（`weekday() < 5`）：交易日序列不含周末，否则数据填充逻辑与真实情况不符
- **批量编辑 / sed 大改后**：推送前必跑 `ruff` + 受影响测试目录，不依赖 CI 兜底

### 4.12 工具链陷阱（Windows / Docker / Git）

- **不要 `git add -A` / `git add .`**：按文件名 add，防误传 `.env` / 凭证 / 大型二进制。本仓长期存在 `.agents/` `.codex/` `AGENTS.md` 三个未跟踪项，`-A` 会把它们一并带走
- **Bash 工具 ≠ PowerShell**：两者语法各不相通。PowerShell here-string `@'...'@` 写进 Bash 会把首尾的 `@` 当字面量混进内容（2026-08-18 混进过 commit message）。Bash 里多行文本一律用 heredoc `<<'EOF'`
- **Bash 工具的 cwd 会漂移**：`cd` 过一次就持续生效，之后在仓库根跑 `uv run ruff/pytest/alembic` 会报 `program not found`（venv 在 `backend/`）。凡 `uv run` 一律前置 `cd .../backend &&`
- **`MSYS_NO_PATHCONV=1` 会连 `--env-file` 一起停止转换**：该参数因此必须传 **Windows 路径**（`C:\...`），否则 docker 报 "cannot find the path"。同一条命令里 `-v` 用 Windows 路径、其余参数也得跟着走
- **`git rev-parse --short HEAD origin/main`（双参数）在本仓 fatal**：改用 `git rev-parse --short HEAD` + `git for-each-ref --format='%(refname:short) %(objectname:short)' refs/remotes/origin/main`
- **`docker exec` 喂 stdin（heredoc / 管道）必须带 `-i`**：不带 `-i` 时容器内进程拿不到 stdin → SQL 完全没执行，而 psql 退出码仍是 0（`set -e` 抓不到），极易误判"已生效"。多语句 SQL 用 `psql -c "stmt1; stmt2; ..."`（单 `-c` 多语句 = 一个隐式事务，配 `-v ON_ERROR_STOP=1`）或 `docker exec -i`
- **系统 Python 是红线守卫的隐藏依赖,缺了 fail-open 且不吭声**：`.claude/hooks/guard.sh` 按 `python`→`py`→`python3` 探测解释器,三个全落空就 `exit 0` 放行一切(`git add -A`、生产 DROP 都不再拦),**无任何提示**。uv 托管的解释器**不进 PATH**,所以"只装 uv 不装 Python"会静默拆掉守卫(2026-08-26 配第二台机时发现)。同理 `~/.claude/settings.json` 的 `statusLine` 也调裸 `python`。判据不是"装了没",而是跑 **`python .claude/hooks/test_guard.py`**(26 条用例,期望 `26/26 passed`)。**别用手敲的 `echo '{...}' | python guard.py` 自检**：`guard.py` 在 JSON 解析失败时**同样静默 `sys.exit(0)`**,而该写法是 Bash 语法、在 cmd.exe 里单引号不是定界符 → JSON 变脏 → 静默放行,与"守卫已死"表现完全相同(2026-08-26 误判过一轮)。改 `guard.py` 规则时必须往夹具补用例,且**正反两面都钉**——只钉"该拦的拦住",规则写宽了没人发现
- **项目解释器由 `backend/.python-version`(=3.12)钉死**,不靠"记得装对版本"：`pyproject` 的 `requires-python = ">=3.12"` 上界开放,系统若装了 3.13/3.14,`uv sync` 可能拿它建 venv → 要么 pandas/asyncpg 无 wheel 现场编译失败,要么**跑起来了但运行时与生产不一致**(算力机上尤其危险:面板 IC 要用于策略决策,数值差异无从归因)
- **破坏性 DB 操作前先做针对性备份**：`pg_dump --data-only -t <表>...` 导出受影响表作精确回滚点（比全库备份快、可定点还原），再在单事务内执行；执行后**必须查行数/状态实证生效**，不信命令退出码

### 4.13 调试范式：SUCCESS 但产出为零

流程状态成功（task status=SUCCESS、无 ERROR 日志）但业务产出**空或恒定**（零信号、NAV 恒为 1.0、空列表、评分全 0）时，**按此顺序**排查：

1. **先查吞异常**——把主循环所有 `try/except Exception` 分支临时去掉 `except`，或把日志级别从 DEBUG 提到 ERROR，看是否有 `KeyError` / `AttributeError` / `TypeError` 被静默捕获。Engine/Service 层的 `except Exception: return []` 是这类问题最常见的来源
2. **主循环 print 二分**——在**真实代码路径**加 `print`（`state` / `len(universe)` / `len(composite)` / `len(signals)`），定位哪一步把数据全拦下
3. **禁止另起脚本重建路径**——手工构造数据极易漏键或漏降级分支，比改真实代码加 print 更慢也更错
4. **最后才查业务层逻辑**（因子 / 策略 / 评分）

根源：静默降级让上游异常**看起来像**业务层无结果，从业务层开始查必然绕远。

---

## 5. Phase 流程

### 5.1 启动核查（创建 phaseN 设计文档前）

1. 读 `system_design §9` 本 phase 行，列出分配的模块
2. 每个模块决定纳入 / 推迟（推迟须在设计文档引言显式注明 + 立即更新 §9 对应行）
3. 孤儿检查（system_design §3/§5 模块 + §6 端点）
4. 设计文档 §1.3 启动核查清单含：
   - [ ] grep `system_design §9` 本 phase 行所有子项（含 `R<N>-P<X>-` 评审追溯）
   - [ ] grep `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + reviews/ 三处确认推迟项消费

### 5.2 收尾核查

- 所有模块对照 DoD 全部交付；未交付立即更新 §9 移入下一 phase
- 文档头部 `版本：` 与修订历史最新版本号一致
- `uv run ruff check src/ tests/` 输出 **0 error**
- 新增 REST API 端点须在 `tests/smoke/test_api_live.py` 补冒烟测试（逐行对照设计文档 §8 场景表，不能只核对数量）
- 集成测试跑通（容器自动启动 + alembic upgrade head）
- 按 **C-6** 沉淀本 phase 踩到的坑：技术陷阱 / 工具坑 → 本文件 §4 对应小节；一次性的操作
  runbook 与事故档案 → memory。**不要往 `~/.claude/CLAUDE.md` 加东西**——那是不含技术细节的
  个人全局规则，且未经用户明确要求不得修改；确有跨项目价值的，向用户提议而不是自行写入

### 5.3 自动测试钩子（`.claude/hooks/auto_test.sh`）

编辑 `backend/*.py` 后自动跑 `tests/unit/` + `tests/e2e/`；编辑 alembic/integration 文件**且** PG 容器在跑时自动跑 integration。测试失败时 Claude 自动进入调试。

### 5.4 推迟判定与三链（C-3 展开）

只有以下四种情况之一才允许推迟：

| 充分理由 | 例 |
|---------|-----|
| 依赖外部决策 | 需要金融专家锁定参数 / 用户对产品策略拍板 |
| 跨 phase 大重构 | 牵动其他 phase 设计文档（如 §14-2 ICIR 历史回填依赖 5y 回填）|
| 验收标准未定义 | 修了无法判定是否对（如覆盖率门槛 ≥ 90% 需所有 phase 跑完）|
| 物理资源约束 | 月末批必须等月末日期 / 5y 真机回填需 12-50h |

**禁止"伪推迟"**：「不影响主路径」「V1.0 范围外」「Phase X 一起做」「只是小改进」均不构成充分理由。

**推迟三链必填**（任一缺失即推迟无效）：

| 推迟去向 | 必填链 |
|---------|-------|
| 下一个 phase | 评审报告 §8 + `system_design §9` 目标 phase 行 |
| V1.5+ 主题 | 评审报告 §8 + `v1_post_release_roadmap §6` 对应主题 |
| 当前 phase 补丁批 | 评审报告 §8 勾选即可（不算推迟） |

链 B/C 子项必须**展开列出**编号 + 一句话描述，禁止"详见评审报告"占位。链 A（评审报告）只是历史日志，链 B/C 才是真正防丢失机制。

### 5.5 设计文档编号规约（C-5 展开）

**禁止外部追踪编号进入设计文档正文**：评审报告编号（DESIGN-09 / P-3）、memory 文件编号（TD-1）不得出现在 SDD / system_design / phase 设计文档正文及修订历史中。可接受的：在对应设计文档中正式定义的编号（如 phase5 §2 定义的 P5-PRE-1）。推迟问题用「**【设计待定：……】**」直接描述内容。

---

## 6. 当前进度

**已完成**：Phase 1~15 ✓（V1.0 RC 验收收口）| **V1.5-G 多用户** ✓ 代码 + 生产部署（2026-07-23）
| **V1.5-A 回测与监控** ✓ 全上线，A5b/F-4 功能级激活已实证 PASS

**进行中：V1.5-C 策略扩展**（设计文档 `docs/design/phases/v1_5_c_strategy_expansion.md` v0.9，
C0~C5 六子批、零推迟，实施序 C0→C1→C2→C3→C4→C5）

- **C0 日级 IC 产出闭环** ✓ 全量上线（2026-08-19 六步生产收尾逐步实证，alembic 至 0025，
  `daily_ic_producer` 19:30 Job 已激活）。收尾 runbook 见 memory `c0_daily_ic_catchup_runbook`
- **收尾硬顺序**（后续任何 IC 回填仍适用）：回填产出 → 导入生产 → **再**部署代码。
  反序会让 `daily_ic_producer` Job 在 19:30 对积压逐日全 universe 评分，正是打挂生产的那条路径
- **C1 策略约束与风险调整动量**：C1-1（约束落点统一，`ac069e5`）/ C1-2（风险调整动量，
  `85df015`）/ C1-3（价格窗口按交易日推导，`be6d6d6`）**均已交付但未部署**——三者都会改变
  选股结果，按设计文档要求在 C1 收口时单独上线并观察。**面板对比待在第二台 24h 算力机起跑**
- C2~C5 待启动

> **运维红线（RC 验收期实证）**：① 生产 2GB 机**禁止一切「全 universe 评分」作业**——判据是代码路径是否调用 `score_universe_for_date` / `ScoringService.score_universe`，**不是功能叫什么名字**。已实证会打挂生产的两例：回测（单个 6 日任务拖垮 11 分钟 → `POST /backtest/run` 已 `backtest_enabled=false` 返 503）、日级 IC 回填（`scripts/backfill_daily_ic.py` **仅跑一个交易日** 即 RSS 1.58G 触发 OOM killer，2026-08-17 致站点 530 共 43 分钟）。此类脚本一律只在本地算力中心跑（`docker-compose.backtest-local.yml` + DB:5434 + `scripts/sync_local_backtest_db.sh`），产出再导入生产；生产端只允许 17:30 每日管线那一次自然评分。**"只跑一天""只是标定"不构成例外**——单日就足够 OOM。**自 2026-08-26 起「本地算力中心」= 第二台 24h 常开机**（双活纪律与新机 runbook 见 `docs/guides/machine_migration.md`）：长任务须在该机 detached 起（`scripts/run_ic_panel.sh`），产出唯一权威；另一台的 5434 降级为可随时丢弃的 scratch，两台各跑一半会产生「谁都不完整、且无法判断某行出自哪台机/哪个配置」的状态。⚠️ `sync_local_backtest_db.sh` 会 DROP 重建 5434，其 `.last_restore` 标记**不足以充当保护**（钩子每天拉新备份 → 标记次日即失配，而面板要跑 31h、跨天必然）；2026-08-26 起脚本自带「库内已有 `ic_baseline_pre_c1` / `factor_ic_window_state` 数据则拒绝执行」，须显式 `--force-wipe` 才继续，`guard.py` 另有二次确认。② 给生产新增 env 变量必须**双写**：`.env.prod` + root `docker-compose.prod.yml` 的 `environment:` **白名单**（非全量透传）；改完先 `docker exec ... printenv` 确认容器拿到值再验证行为。③ 冒烟跑生产用 `API_BASE_URL=https://quant.portableagi.com`，会写虚拟数据（SMOKE01.SZ 黑名单/0.01 入金）须跑后核查并 void 还原。

详细 phase 表 + 历史里程碑（V1.0 整改 3 批次 / V1.0 真机验收 15 bug / Phase 11~15 实施细节）
→ `docs/design/system_design.md §9`。
