# QuantPilot — Claude 工作指南

> 量化领航：个人量化交易决策辅助系统
> 工作目录：`D:\MyWork\10Project\RD\QuantPilot`

---

## 1. 关键文档（每次任务前必读相关文档）

| 文档 | 路径 | 用途 |
|------|------|------|
| SDD 规范 | `docs/spec/QuantPilot_SDD.md` | 系统需求与功能规范（权威来源） |
| 系统设计 | `docs/design/system_design.md` | 技术架构与模块划分 |
| Phase N 设计 | `docs/design/phases/phaseN_*.md` | 当前 phase 详细设计（开始任务前必读） |
| 开发指南 | `docs/guides/dev_setup.md` | 项目整体开发环境配置与命令参考 |
| 部署指南 | `docs/guides/deployment.md` | 生产部署（含 HTTPS / 备份 / 故障排查） |

**规则**：实现前先读对应 phase 设计文档。设计文档与 SDD 冲突时，以 SDD 为准。

---

## 2. 项目结构（关键目录）

```
backend/src/quantpilot/
├── engine/        # Engine 层（纯函数，无 IO）
├── data/          # 数据采集层（adapters / calendar / repository / validators）
├── services/      # Service 层（编排，含 IO）
├── api/v1/        # REST 路由；api/deps.py 统一存放依赖注入函数
├── models/        # SQLAlchemy ORM（23 张表；MarketStateHistory 在 business.py）
├── schemas/       # Pydantic schemas
├── core/          # config / database / security / exceptions
└── pipeline/      # scheduler.py / daily_pipeline.py / monthly_scheduler.py
tests/
├── unit/          # 纯函数，无 DB
├── e2e/           # ASGI，无 DB
├── integration/   # 需 PostgreSQL
└── smoke/         # 需运行中的服务 + API_PASSWORD，不入 CI
alembic/versions/  # NNNN_<描述>.py，按序号管理
```

---

## 3. 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| Web 框架 | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0（asyncio） |
| 数据库 | PostgreSQL 15 + asyncpg |
| 缓存 | Redis 7 |
| 迁移 | Alembic |
| 认证 | PyJWT + bcrypt（单管理员用户） |
| 量化计算 | pandas 2.2 / numpy / pandas-ta / scipy / statsmodels |
| 数据源 | Tushare Pro（主）/ AKShare（备） |
| 调度 | APScheduler 3.10 |
| 包管理 | uv + hatchling（src layout） |
| 测试 | pytest + anyio + pytest-cov |
| Lint | ruff（line-length=100, py312） |

---

## 4. 常用命令（均在 `backend/` 目录执行）

```bash
uv sync --group dev
uv run pytest tests/ --cov=quantpilot --cov-report=term-missing -v
uv run pytest tests/unit/        # 无 DB，最快
uv run pytest tests/e2e/         # ASGI，无 DB
uv run pytest tests/integration/ # 需要真实 DB；⚠️ 严禁同时启动多个进程（DB 竞态）
API_PASSWORD=xxx uv run pytest tests/smoke/ -v  # 需服务运行中（uvicorn）
uv run ruff check src/ tests/
uv run ruff check --fix src/ tests/
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "描述"
docker compose -f docker-compose.dev.yml up -d db redis
docker compose -f docker-compose.dev.yml up --build
```

---

## 5. TDD 工作流（所有 phase 强制遵守）

**禁止跳过 TDD 流程直接写实现。** 每个 phase 必须先完成启动核查、再写测试（RED）、再写实现（GREEN）。

**测试命名**：`tests/unit/test_<模块>.py`、`tests/e2e/test_<功能>_api.py`、`tests/integration/test_<主题>.py`

### Phase 启动核查（创建 phaseN 设计文档前必须完成）

**启动前必须逐项确认：**

1. **读 system_design §9**，找到本 phase 在规划表中的那一行，列出所有分配给本 phase 的模块。
2. **决定每个模块的去向**：
   - 纳入本 phase → 写入设计文档 scope
   - 推迟 → 在设计文档引言处**显式注明**"模块 X 推迟至 Phase N，原因：……"，并立即更新 system_design §9 对应行
3. **孤儿检查**：以下两类对象各自核查，确保每个都有明确的 phase 归属：
   - **模块**：system_design §3/§5 中列出的所有 Engine/Service/Manager 类
   - **API 端点**：system_design §6 中属于本 phase 业务范围的端点（Phase 重构后容易产生孤儿端点）
4. **更新 system_design §9**：只要本 phase 实际范围与 §9 原文不同，必须先更新 §9，再开始写设计文档。

### Phase 收尾核查（phase 验收后执行）

1. 检查本 phase 设计文档中所有模块是否全部交付（对照 DoD）。
2. 若有未交付模块，立即更新 system_design §9，将其显式移入下一 phase。
3. 确认 phase 设计文档的"依据文档"引用章节号与实际实现范围一致（例如：不实现策略的 phase 不应引用 §5.3 策略基类）；确认文档头部 `版本：` 标注与修订历史最新版本号一致。
4. **`uv run ruff check src/ tests/` 输出 0 error**——这是交付门槛，不满足不得合并/进入下一 phase。
5. **新增 REST API 端点须在 `tests/smoke/test_api_live.py` 补充冒烟测试**（至少覆盖：无鉴权 → 401、有鉴权 → 200 含响应结构断言、关键错误路径如 404/422），并在本 phase 设计文档 DoD 中列为独立验收项。收尾时须**逐行**对照设计文档 §8 场景表与实际测试函数——不能只核对数量，编号漂移会静默丢失场景（如插入额外无鉴权测试导致正向场景被替换）。
6. **集成测试须通过**：先启动容器（`docker compose -f docker-compose.dev.yml up -d db redis && uv run alembic upgrade head`），再运行 `uv run pytest tests/integration/ -v`；PostgreSQL 容器不在线时自动启动，无需等待人工指示。
7. **检查是否有新经验需要写入 CLAUDE.md**：特别是代码评审中发现的通用规律（如 session 生命周期、asyncio event loop 规范）和收尾时发现的流程漏洞（如场景漂移）。

### 自动测试钩子（.claude/hooks/auto_test.sh）

编辑 `backend/` 下任意 `.py` 文件后，钩子自动触发：
- **始终运行**：`tests/unit/` + `tests/e2e/`（无需 DB，秒级完成）
- **条件运行**：`tests/integration/`（仅当编辑了 alembic/集成测试文件 **且** PostgreSQL 容器在运行时）
- 测试结果实时反馈给 Claude → 失败时 Claude 自动进入调试

### 会话恢复核查（/compact 或跨会话恢复后必做）

会话压缩、跨 session 恢复、或长对话中途接手任务时，**继续动代码前先跑一次完整测试建立真实基线**：

```bash
uv run pytest tests/unit/ tests/e2e/ tests/integration/ -q
```

原因：压缩摘要里的"X tests passed / 代码审查通过"是历史快照，代码/测试在恢复之前的时间内可能已漂移。以摘要为事实继续开发，会在真实的坏测试上叠加新 bug，事后难以追溯（本次就遇到过：摘要标 343 passed，实际 `test_int_be_02` 的 mock 早已与真实 Scorer 契约不符，只因上游 bug 抛异常被吞才"通过"）。

### 调试规范（"SUCCESS 但产出为零"类问题）

遇到流程状态成功（task status=SUCCESS、无 ERROR 日志）但业务产出为空或恒定（零信号、NAV 恒为 1.0、空列表、所有评分为 0 等）时，按以下顺序排查：

1. **先查吞异常**：在主循环所有 `try/except Exception` 分支临时去掉 except 或把日志级别从 DEBUG 提到 ERROR，观察是否有 KeyError/AttributeError/TypeError 被静默捕获。Engine 层的 `except Exception: return []` 是这类问题的最常见来源。
2. **主循环打印二分**：在真实代码路径加 print（如 `state` / `len(universe)` / `len(composite)` / `len(signals)`），快速定位哪一步把数据全拦下。
3. **禁止另起脚本重建 Engine 路径**：手工构造 MarketSnapshot/DataBundle 极易漏键（如本次漏 `daily_quotes`）或漏降级分支，比改真实代码加 print 更慢更错。
4. **最后才细查因子/策略层逻辑**。

根源：Engine 层静默降级（见 §6）会让上游异常看起来像因子层无结果，直接从因子层开始查会绕远。

---

## 6. 代码规范（Phase 1 确立，后续 phase 遵守）

### ORM 模型

```python
# ✅ 正确：Mapped[] 用 Python 运行时类型，mapped_column() 用 SQLAlchemy 类型
trade_date: Mapped[date] = mapped_column(Date, nullable=False)
created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
# ❌ 错误：Mapped[Date]（mypy 报错）
```

禁止使用 `declarative_base()` 函数，已统一改用 `DeclarativeBase` 类继承。

### 统一 API 响应格式

```python
{"code": 0, "data": {...}, "msg": "ok"}                # 成功
{"code": 401, "data": None, "msg": "错误说明"}          # 错误
{"code": 422, "data": None, "msg": "请求参数校验失败",  # 422 额外含 errors 字段
 "errors": [{"field": "body.username", "reason": "Field required"}]}
```

### 安全规范

- 登录验证：先执行 `verify_password()`（bcrypt ~100ms），再比对用户名，避免计时侧信道
- 测试密码：禁止硬编码；使用 `from tests.conftest import TEST_PASSWORD`
- `override_admin_password` fixture（autouse+session）自动替换 settings 哈希，CI 无需精确哈希值

### 数据库

- `get_db()` yield 后自动 commit，异常自动 rollback；路由中无需手动 commit/rollback
- **后台任务 session 生命周期**：`get_db()` 会话由框架自动 commit；`async with AsyncSessionLocal()` 直接创建的会话（后台任务 `asyncio.create_task` 等）**必须显式 commit**，否则写入不持久
- **混合模式 Service 方法的 outer session**：Service 方法内部如果对部分工作单元用了自管理 per-iteration `AsyncSessionLocal`（如 `ingest_history` 的 per-day session），但其他批量写操作走 `self._repo`，则调用方必须在 outer `async with AsyncSessionLocal() as session: ... service.foo(...)` 块退出前显式 `await session.commit()`——否则 `self._repo` 那部分写入随 close 丢失。2026-05-12 真机验收抓到：`ingest_history` 内 index_history/index_components 走 `self._repo`，refill 脚本未 commit → 索引数据全空
- 市场数据读写通过 `MarketDataRepository`；业务服务层（PerformanceService、BacktestService 等）可直接执行 ORM 查询，但禁止在 Route 层绕过 Service 直接操作 ORM
- upsert 使用 `insert(...).on_conflict_do_update()`；`updated_at` 须显式写入 `func.now()`
- 集成测试 `db_engine` fixture 必须 `poolclass=NullPool`（防止跨 event loop 连接复用），并且**禁止 `scope="session"`**：anyio 每个测试一个新 event loop，session 级 async engine 会触发 `Future attached to a different loop`。schema 建表用单独的同步 fixture（`_ensure_schema`，scope=session）跑 alembic；engine 改成函数级，每个测试独立创建/销毁。本地 Windows 偶发不报，CI ubuntu 必现。

### API 层

- **所有依赖注入函数**（`get_*_service`、`get_repo` 等）统一放在 `api/deps.py`，禁止在路由文件内定义（路由文件只允许 `from quantpilot.api.deps import ...`）
- **BackgroundTasks 与 UNIQUE 约束并存时必须先显式 `await session.commit()` 再 `add_task()`**：Starlette `BackgroundTasks` 在请求 async 上下文内 await，`get_db()` yield 后的隐式 commit 推迟到所有 BG task 跑完；BG 内若需写同一 UNIQUE 行会被外层未 commit 行阻塞，外层 commit 又必须等 BG task 完成 → 循环死锁。`POST /pipeline/trigger` 真机验收抓到 90s 卡死 504

### 数据采集层

- 所有 Tushare 调用通过 `_call()` 异步包装（`asyncio.to_thread` + `Semaphore`），禁止直接调用 SDK
- 单位换算在适配器内完成，repository 收到的永远是最终单位（小数、元、股）
- **bulk upsert 必须 `_BATCH_SIZE=500` 循环 `pg_insert.values(batch)`**：asyncpg 协议 16-bit 占位符总数上限 32767，5491 行 × 11 列直接超限。合成数据测试 < 3000 行容易绕过此约束，集成测试需 ≥ 3000 行场景才能抓到（2026-05-11 Bug：3 个 upsert 漏分批被真机验收抓到）
- **upsert 前必须 `df.where(pd.notna(df), None)`** 把 NaN 转 None：pandas NaN/NaT 经 `to_dict("records")` 后是 `float('nan')`，asyncpg 原样写入 PostgreSQL NUMERIC 字段作为特殊值 `'NaN'`（≠ NULL），下游 `IS NOT NULL` 误判、数值过滤失效（2026-05-11 真机验收抓到 ROE 列 213k 行 `'NaN'`）
- **`ingest_history` per-day 独立 `AsyncSessionLocal`**：跨日共用 outer session 时，asyncpg 语句级 savepoint 会让单条 upsert 失败只回滚自己那条、其他表照常 commit，形成「daily_quote 进库 / financial_data 全空」混合状态。每个交易日 errors 非空整日 rollback、否则整日 commit；调用 `ingest_daily(_repo=day_repo)` 注入 per-day repo
- **`ingest_history` 断点续传查 `repo.get_fully_ingested_dates()`** 返回 `daily_quote ∩ financial_data` 双表交集，禁用单表 `get_ingested_quote_dates()` 或 `MAX(trade_date)`（Bug 6：上一轮 savepoint 半 commit 的日期会被错误判定为完成）
- **Tushare `index_weight` 为月度稀疏接口**（仅 rebalance 日有 snapshot）：按 `trade_date` 单日查询大概率返回空。`fetch_index_components` 改用 `[trade_date-60d, trade_date]` range query 取 ≤ `trade_date` 的最近 snapshot 保 PIT；`fetch_index_components_range` 供 ingest_history 一次性批量加载
- **Tushare `fina_indicator` 不支持 period-only 全市场调用**：必须 `period + ts_code` 组合查询（50 只/批 + `asyncio.sleep(0.3)`），单日 ingest 多 ~33s、ingest_history 60 天多 ~30 分钟，但换来真实 roe/yoy 字段。原 period-only 调用静默吞异常把 5 个字段填 NULL 是 RM-17 评分退化根因（2026-05-12 真机验收抓到）
- **Tushare `namechange` 接口 start/end 是公告日期（ann_date）**，仅传 ingest 窗口只能拿到「窗口内被公告改名」的股票——早就叫 \*ST 的股票（公告在几年前）会全部缺失。`ingest_history` 必须把 namechange 回溯起点设为 `ingest_start - 5y`（覆盖绝大多数当前 ST 命名公告日，3 年净亏损实施 ST、超 5 年通常已强制退市）。RM-16（2026-05-12 真机验收）
- **Tushare 分红接口名是 `dividend` 不是 `fina_dividend`**：`pro.dividend(ex_date=...)` 返回 cash_div_tax（税前每股，元）。错误接口名（如 `fina_dividend`）会被 Tushare 服务端返回「请指定正确的接口名」——单元测试用 mock 抓不到这类 typo，必须真机抽测一次或对方法名加 `is adapter._pro.dividend` 契约断言。RM-15（2026-05-12 真机验收）
- **完整性校验 `prev_count` 必须 PIT 活股数**：`DataService.ingest_daily` 调 `validate_daily_quotes(quote_df, prev_count)` 时必须用 `repo.get_active_stock_codes_as_of(trade_date)`（按 list_date/delist_date PIT 过滤），不能用 `get_active_stock_codes()` 的当前 is_active 快照。后者含 2026 年新上市股 5840 只，5 年前 fetch 返回 ~4300 只必然 < 5840×0.95 → 完整性校验失败 → per-day session 整日 rollback → 5 年回填跑完仍是空 DB。单元测试无法暴露（mock 总传任意 prev_count），需 5 年级别真机回填或专门构造 PIT 整合测试。RM-18（2026-05-13 真机验收）
- **`refill_history.py` 双模式（2026-05-13）**：默认 = 扩存量（不删，走 `get_fully_ingested_dates` 双表交集断点续传）；`--force-clean` = 修脏（先 DELETE 4 表再重灌）；`--dry-run-plan` = 预检（仅打印 trade_dates / 已入库 / 待补，不删不拉）。早期默认 DELETE 是为"修脏"设计的，但"按需扩大历史窗口"（90 天 → 5 年）是更高频运维操作，两语义通过 flag 区分到一个脚本，避免重复维护

### Engine 层

- Engine 层（`engine/`）严格无 IO（数据库、文件、网络），只做纯函数计算
- PostgreSQL `NUMERIC` 列传入 pandas_ta 前必须 `.astype(float)`（避免 `isnan` TypeError）
- 交易日数换算日历天：`calendar_days = int(history_days * 1.5)`，禁止直接 `timedelta(days=history_days)`
- APScheduler job 无法访问 `app.state`；Engine 单例须通过 `create_scheduler()` 显式 `args=[...]` 传入
- pandas MultiIndex 的 `in` 判断（`ts_code in index.get_level_values("col")`）在循环内是 **O(n)**；必须在循环外预计算 `available = set(index.get_level_values("col"))`，循环内用 `ts_code not in available`（O(1)）
- **线程回调中的 event loop**：在 async 上下文创建线程回调时，用 `asyncio.get_running_loop()` 预捕获 loop，禁止在子线程内调用 `asyncio.get_event_loop()`（Python 3.12 DeprecationWarning，且行为不可靠）
- **静默吞异常禁止**：Engine/Service 层主循环中 `except Exception` 分支若返回空集合/None/默认值，必须 `logger.exception(...)`（不可用 `logger.debug`）。DEBUG 级被吞的异常在生产日志中不可见，调用方只看到"空输出"，无法区分"合法无数据"和"内部 bug"。业务上确实要降级时用 `【降级说明】` 注释标明，见下方「规格降级」。

### 规格降级

实现与规格有差距时（任何层均适用），必须在代码中用 `【降级说明】` 注释标明：当前降级内容、原因、恢复条件。**禁止静默降级**，包括但不限于：
- `except Exception: return []`（或 `None`、`{}`）后不记 ERROR 日志
- 用占位值（50、0、`""`）替代缺失数据不标注来源
- 降级路径只在 DEBUG 级写日志

同时在对应 phase 设计文档中同步标注。

### 测试

- 测试路由在 `client` fixture 内动态注册（`include_in_schema=False`），yield 后移除
- 集成测试合成 trade_date 序列须跳过周末（`weekday() < 5`）
- 集成测试断言须精确（如 `assert len(pool_codes) == 3`），不使用宽松上界（如 `<= 3+1`）——宽松断言掩盖超出预期的写入 bug

---

## 7. 环境配置

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/quantpilot
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH='$2b$12$...'   # bcrypt 哈希，含 $ 时须用单引号
JWT_SECRET_KEY=<64字符以上随机字符串>
JWT_ALGORITHM=HS256
TUSHARE_TOKEN=your_tushare_token   # 无此项则数据采集 API 全部不可用（返回 503）
REDIS_URL=redis://localhost:6379/0      # ConfigService 缓存与限流
WXPUSHER_APP_TOKEN=AT_xxx               # Phase 10 通知；缺失时自动降级为仅站内信
WXPUSHER_UID=UID_xxx                    # Phase 10 通知接收方 UID（单管理员）
LOG_DIR=logs                            # Phase 10 §8.4 RotatingFileHandler 目录
LOG_LEVEL=INFO                          # Phase 10 根 logger 级别
LOG_JSON=true                           # Phase 10 生产默认 JSON 结构化；DEBUG 可关闭
DEBUG=false
```

**生产部署**：另见 `.env.prod.example`（Phase 10 §8.2）含 `POSTGRES_*` / `HTTP_PORT` / `CORS_ORIGINS` 等生产专用项。

---

## 8. 迁移文件规范

- 文件名：`NNNN_<描述>.py`（4 位序号）；建表顺序按 FK 依赖分层
- 降序索引：`sa.text("col DESC")`；ORM `__table_args__` 与迁移文件保持一致
- **必须在 `backend/` 目录执行** alembic 命令（`alembic.ini` 在此）

---

## 9. 当前进度

| Phase | 名称 | 状态 |
|-------|------|------|
| Phase 1 | 基础设施（认证、ORM、迁移、测试框架） | **完成 + 代码审查通过 ✓** |
| Phase 2 | 数据采集层（Tushare 接入 / 行情 / 财务 / 指数 / 调度） | **完成 + 冒烟测试通过 ✓** |
| Phase 3 | 市场状态识别（ADX/MA 三态 + 防抖动 + REST API） | **完成 + 代码审查通过 ✓** |
| Phase 4 | 因子计算引擎 | **完成 + 代码审查通过 ✓** |
| Phase 5 | 信号生成（SignalGenerator/PositionSizer/RiskChecker + signals API） | **完成 + 代码审查通过 ✓** |
| Phase 6 | 账户持仓管理（AccountService/SettingsService + REST API） | **完成 + 代码审查通过 ✓** |
| Phase 7 | Pipeline + 因子监控 + 报告（DailyPipeline/MonthlyScheduler/FactorMonitorService/ReportService） | **完成 + 代码审查通过 ✓** |
| Phase 8 | 绩效归因 + 回测引擎（BacktestEngine + PerformanceService + /performance/* API + /backtest/* API） | **完成 + 代码审查通过 ✓** |
| Phase 9 | 前端（Vue 3 仪表盘） | **完成 ✓**（代码评审修复 + 手动验收完成，2026-05-14 收尾确认）|
| Phase 10 | 配置消费 + 通知 + 部署收尾（ConfigService/12类config_key落地/WxPusher+站内信降级/Settings三级折叠+字段级 tier/术语 Tooltip/OnboardingWizard/YAML导入导出/生产Docker/RotatingFileHandler） | **完成 ✓**（480 tests + ruff 0 error + 冒烟 API-74~84 + 自审 G3/M2/M3 + 2026-04-27 代码评审 C-01~C-09 全部修复） |
| **V1.0 整改批次** | **2026-04-27 V1.0 整体评审** 识别 8 P0 + 12 P1（详见 `docs/reviews/v1_overall_review_2026-04-27.md`），分 3 批修复：**Batch 1 合规链条 ✓ 完成 2026-05-01**（B1-1 重写 DISCLAIMER + B1-2 BacktestLimitationsBanner + B1-3 三视图 DisclaimerBanner + B1-4 SDD §7.7.5）/ **Batch 2 实盘风控+UX ✓ 完成 2026-05-01**（B2-1 CP3 max_drawdown_pct + B2-2 record_dividend 排查 + B2-3 闰年 bug + B2-4 LoginView 合规脚注 + B2-5 HTTPS 警示 + B2-6 INT-ACC-10/11 + INT-SIG-GEN-01d + LEAP-01~04 回归测试）/ **Batch 3 回测引擎重构 ✓ 完成 2026-05-01**（B3-1~10：BacktestDataBundle 全字段 + T+1 撮合 + PE/PB 真实切片 + RiskChecker 集成 + PIT is_st/is_suspended/delist_date + financials_history + DataValidator + 8 处异常合规化 + INT-BE-03~08 集成测试）；**V1.0 整改批次全部完成 ✓**；V1.5 完整 scope（SDD §16 14 项 + V1.0 评审 P2/P3 25 项 + SDD 外部评审 8 项 + Phase 10 评审 3 项 = 50 项）见设计文档 `docs/design/v1_5_roadmap.md` | **完成 ✓** |
| **V1.0 真机验收** | **2026-05-11~13** 用生产 Docker 栈 + 真实 Tushare token 跑端到端验收。**第一轮 2026-05-11~12**：识别 5 类共 11 bug + RM-13/15/16/17 评分退化链（4 项原推迟 V1.5）；**第二轮 2026-05-12**：修复 RM-15/16/17 信号退化，跑出 78 交易日基线 + 评分质量 PASS（顶分 70.10、value 100% 填充、is_st 5.89%）；**第三轮 2026-05-13**：扩到 5 年回填 → **RM-18 真机暴露完整性校验 PIT bug**（DataValidator.validate_daily_quotes 的 prev_count 用 get_active_stock_codes() 当前快照 5840 只，对比 2021 年实际 ~4300 只必然 < 95% → 5 年内每日 rollback 跑完仍空 DB）。**第四轮 2026-05-13 5y 收尾**：PG 调优（shared_buffers 256MB / effective_cache_size 1GB / work_mem 8MB / WAL 优化）后增量续传完成 1210 交易日 × 4 表（daily_quote 6.22M / financial 6.19M / index_history 4×1210 / index_component 70 月度 snapshot），roe_fill **98.58%** / is_st PIT 一致性早年 6.00% vs 近年 7.24% / 跨制度 pipeline 识别 3 种 market_state（DOWNTREND/OSCILLATION/UPTREND）；refill_history.py 拆分双模式（默认增量、--force-clean 修脏）。**全部 bug**：**数据层**（Bug 5 per-day 原子性 / Bug 6 双表交集断点续传 / Bug 7a-b index_weight 月度稀疏 / Bug 9 NaN→SQL `'NaN'` / asyncpg 32767 漏分批 / RM-15 dividend API 名 / RM-16 namechange 5y 回溯 / RM-17 fina_indicator 按 ts_code 批 + get_latest_financial set_index / **RM-18 prev_count PIT**）/ **API 层**（Bug 14 BG task UNIQUE 死锁）/ **迁移**（Bug 12 0008 幂等默认账户）/ **前端**（Bug 1 router 守卫 / Bug 4 Wizard refresh_stock_list / Bug 8 axios 超时 / Bug 10 回填天数语义）/ **运维**（Bug 11 nginx 超时 + PG 默认 shared_buffers 在 2M+ 行规模下颠簸）；**RM-13（deposit 不幂等）原推迟 V1.5，2026-05-14 升级到 V1.0 收尾 Phase 14**（详见下方 V1.0 收尾批次）| **15 bug 修复 + 5y 真机验收 PASS ✓** |
| **V1.0 收尾批次（Phase 11~15）** | **2026-05-14 V1.0 重新定位** — 2026-05-13 5y 真机验收后用 2026-05-13 当日 pipeline 验证发现历史所有 trade_date 的 `signal` 表 0 行（候选池顶分 71.19 < buy_threshold 80），根因是核心评分公式存在三个数学缺陷：① rank-pct 跨期不可比；② 4 策略横截面天然反相关（trend ↔ reversion）锁死 composite 顶分 70~75；③ 绝对阈值 80 与实际评分分布脱钩。V1.0 重新定位为"**所有阻断用户达成核心目标的问题修复完成才能发布**"，原 V1.5 中评分链路 / 因子级溯源 / 数据质量监控 / 部署评审 / 账户资金链项目升级 V1.0 必修，新增 Phase 11~15：**Phase 11 评分工业化 ✓ 完成 2026-05-18**（流派 1 完整版交付：5 步管线 Winsorize / 行业+市值中性化 / Z-score / Gram-Schmidt 正交化 / 三层输出 + ICIR 滚动加权 + Hysteresis + 月末 rebalance + 分位阈值 + 双重失效止损；5y 真机跨制度回归 4 trade_date × 3 state 全 PASS：**composite_z=3.2~4.6**（v1.4 修复后落 N(0,1) top 0.05% 合理）/ composite_score ≥ 99.94 / pct=0.0004~0.0005 / pool_count=50 / BUY signals 43~50 每日 / market_state PIT 100% 一致；**实施期发现并修复 4 处 bug**：(1) P0 PIT bug——`_run_phase11_pipeline` 原用 `get_latest_market_state()` 无 before_date 取全表最大日 → 改 `before_date=trade_date+1d`（集成测试 INT-P11-SC-05 覆盖）；(2) Scorer Robust Z-score 漏标准化——策略内 `z_df.mean(axis=1)` 后未再 standardize，momentum 因子 NaN 率 33% 导致 strategy_z 顶值 11.24 → 加再 standardize + clip ±3.5σ；(3) Orthogonalizer 共线退化阈值 1e-12 太松——momentum 残差被前序投影吸收 99.99% renormalize 把 z=-0.089 放大 270 倍 → 加 collinear_residual_ratio=0.3 阈值（R²>91% 视为共线整列剔除）；(4) pool_capacity 默认 20 截断 STRONG → 升 50；docker COPY src/ 镜像 stale 教训写入 memory；**known issue**：weights_source 全 default_matrix（factor_ic_window_state/strategy_weights_history 仍空，需 5y candidate_pool 历史回填 + ICIR 滚动累积 272 日 → Phase 14 §14-2）/ STRONG count 18~23 vs 设计 30 偏差（universe 过滤后 ~2400 只，top 1% ≈ 24，绝对数基线 30 假设全市场 3200，留 Phase 15 RC 复审）/ BacktestEngine 仍走 aggregate_legacy + 派生 z（不影响实盘 critical path，Phase 14 §14-2 接入真 5 步）；§10.4 "3 state × 10 trade_date = 30 日完整版"拆分 Phase 15 RC，理由：5y 数据上每日 pipeline 130~250s（buffer 热），30 日全跑 ~1.5 小时与 5y 全 pipeline 回填一并处理更合理）/ **Phase 12 因子级溯源**（吸收 V1.5-B 全部 + 多因子回归归因）/ **Phase 13 生产可观测**（吸收 V1.5-H 全部 + G-1/G-2）/ **Phase 14 账户资金链 + 5y candidate_pool 回填 + ICIR 历史回算 + BacktestEngine 真 5 步**（含 RM-13 + Phase 11 实施评审 §6.3 推迟两项：ICIR 窗口改严格交易日 + factor_ic_window_state daily/aggregate 共表评估拆分）/ **Phase 15 RC 验收 + 文档同步**；估算 ~38-55 pd（8-11 周）；V1.0 发布后 V1.5+ 路线见 `docs/design/v1_5_roadmap.md`（v2.0 重构后 34 项 ~56-82 pd）| **Phase 11 ✓ / 12~15 待启动** |

**开始新 phase 前**：确认对应设计文档已存在于 `docs/design/phases/`，若不存在先创建。

**V1.0 收尾路线（Phase 11~15）**：当前进行中。详细范围见 `docs/design/system_design.md` §9 Phase 11~15 行 + 各 phase 设计文档；SDD §7-10 修订草案见 `docs/design/sdd_7_10_revision_draft_2026-05-14.md`（v1.3 已锁定）。
**V1.0 发布后 V1.5+ 路线图**：V1.5+ scope（产品功能 + 剩余评审推迟项）见设计文档 `docs/design/v1_5_roadmap.md`（v2.0 重构后承担 V1.0 发布后路线）。V1.5 启动（V1.0 RC 后）按该路线图 §6 主题（V1.5-A..J）打包成对应 phase 设计文档。

---

## 10. Phase 文档治理规则
- 禁止 phase 实际范围与 system_design §9 不一致时跳过 §9 更新（范围变更必须立即回写）
- 禁止在新 phase 设计文档中静默跳过 §9 分配的模块（推迟的模块必须显式注明"推迟至 Phase N，原因：……"）
- 禁止存在孤儿模块或孤儿端点——所有 system_design 中的模块（§3/§5）和 API 端点（§6）必须在某个 phase 有且仅有一个明确归属
- 禁止在 phase 设计文档中引用晚于本 phase 实现的接口而不说明 stub 策略——跨 phase 依赖必须在调用处注明"Phase N: no-op stub，Phase M 替换为真实实现"（例：DailyPipeline notifier 在 Phase 7 为 no-op，Phase 10 替换为真实 WxPusher）
- **禁止在设计文档中使用外部追踪编号**：评审报告编号（如 DESIGN-09）、会话内问题编号（如 P-3、G-02、N-01）、仅存在于 memory 文件的技术债编号（如 TD-1/2/3）等，均不得出现在 SDD、system_design.md 或 phase 设计文档的正文及修订历史中——这类编号在文档读者语境中无法追溯。**可接受的跨文档引用**：在对应设计文档中有正式定义的编号，例如 phase5_signals.md §2 定义的 `P5-PRE-1/2/3/4`、phase4 设计文档定义的 `F-1～F-8` 过滤条件编号。推迟的设计问题须以【设计待定：……】形式直接描述问题内容，不得以编号代替。
