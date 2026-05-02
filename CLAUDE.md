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
- 市场数据读写通过 `MarketDataRepository`；业务服务层（PerformanceService、BacktestService 等）可直接执行 ORM 查询，但禁止在 Route 层绕过 Service 直接操作 ORM
- upsert 使用 `insert(...).on_conflict_do_update()`；`updated_at` 须显式写入 `func.now()`
- 集成测试 `db_engine` fixture 必须 `poolclass=NullPool`（防止跨 event loop 连接复用）

### API 层

- **所有依赖注入函数**（`get_*_service`、`get_repo` 等）统一放在 `api/deps.py`，禁止在路由文件内定义（路由文件只允许 `from quantpilot.api.deps import ...`）

### 数据采集层

- 所有 Tushare 调用通过 `_call()` 异步包装（`asyncio.to_thread` + `Semaphore`），禁止直接调用 SDK
- 单位换算在适配器内完成，repository 收到的永远是最终单位（小数、元、股）
- `ingest_history` 断点续传用 `get_ingested_quote_dates()` 返回的 `set[date]`，禁用 `MAX(trade_date)`

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
| Phase 9 | 前端（Vue 3 仪表盘） | **代码评审修复完成，手动验收进行中** |
| Phase 10 | 配置消费 + 通知 + 部署收尾（ConfigService/12类config_key落地/WxPusher+站内信降级/Settings三级折叠+字段级 tier/术语 Tooltip/OnboardingWizard/YAML导入导出/生产Docker/RotatingFileHandler） | **完成 ✓**（480 tests + ruff 0 error + 冒烟 API-74~84 + 自审 G3/M2/M3 + 2026-04-27 代码评审 C-01~C-09 全部修复） |
| **V1.0 整改批次** | **2026-04-27 V1.0 整体评审** 识别 8 P0 + 12 P1（详见 `docs/reviews/v1_overall_review_2026-04-27.md`），分 3 批修复：**Batch 1 合规链条 ✓ 完成 2026-05-01**（B1-1 重写 DISCLAIMER + B1-2 BacktestLimitationsBanner + B1-3 三视图 DisclaimerBanner + B1-4 SDD §7.7.5）/ **Batch 2 实盘风控+UX ✓ 完成 2026-05-01**（B2-1 CP3 max_drawdown_pct + B2-2 record_dividend 排查 + B2-3 闰年 bug + B2-4 LoginView 合规脚注 + B2-5 HTTPS 警示 + B2-6 INT-ACC-10/11 + INT-SIG-GEN-01d + LEAP-01~04 回归测试）/ **Batch 3 回测引擎重构 ✓ 完成 2026-05-01**（B3-1~10：BacktestDataBundle 全字段 + T+1 撮合 + PE/PB 真实切片 + RiskChecker 集成 + PIT is_st/is_suspended/delist_date + financials_history + DataValidator + 8 处异常合规化 + INT-BE-03~08 集成测试）；**V1.0 整改批次全部完成 ✓**；V1.5 完整 scope（SDD §16 14 项 + V1.0 评审 P2/P3 25 项 + SDD 外部评审 8 项 + Phase 10 评审 3 项 = 50 项）见设计文档 `docs/design/v1_5_roadmap.md` | **完成 ✓** |

**开始新 phase 前**：确认对应设计文档已存在于 `docs/design/phases/`，若不存在先创建。

**V1.5 路线图**：V1.5 完整 scope（产品功能 + 评审推迟项 + 文档同步责任）见设计文档 `docs/design/v1_5_roadmap.md`。V1.5 启动时按该路线图 §6 主题（V1.5-A..J）打包成对应 phase 设计文档。

---

## 10. Phase 文档治理规则
- 禁止 phase 实际范围与 system_design §9 不一致时跳过 §9 更新（范围变更必须立即回写）
- 禁止在新 phase 设计文档中静默跳过 §9 分配的模块（推迟的模块必须显式注明"推迟至 Phase N，原因：……"）
- 禁止存在孤儿模块或孤儿端点——所有 system_design 中的模块（§3/§5）和 API 端点（§6）必须在某个 phase 有且仅有一个明确归属
- 禁止在 phase 设计文档中引用晚于本 phase 实现的接口而不说明 stub 策略——跨 phase 依赖必须在调用处注明"Phase N: no-op stub，Phase M 替换为真实实现"（例：DailyPipeline notifier 在 Phase 7 为 no-op，Phase 10 替换为真实 WxPusher）
- **禁止在设计文档中使用外部追踪编号**：评审报告编号（如 DESIGN-09）、会话内问题编号（如 P-3、G-02、N-01）、仅存在于 memory 文件的技术债编号（如 TD-1/2/3）等，均不得出现在 SDD、system_design.md 或 phase 设计文档的正文及修订历史中——这类编号在文档读者语境中无法追溯。**可接受的跨文档引用**：在对应设计文档中有正式定义的编号，例如 phase5_signals.md §2 定义的 `P5-PRE-1/2/3/4`、phase4 设计文档定义的 `F-1～F-8` 过滤条件编号。推迟的设计问题须以【设计待定：……】形式直接描述问题内容，不得以编号代替。
