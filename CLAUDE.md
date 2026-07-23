# QuantPilot — Claude 工作指南

> 量化领航：个人量化交易决策辅助系统
> 单仓 monorepo，后端在 `backend/`。

通用工程经验（Python / async / DB / pytest 等跨项目复用）见 `~/.claude/CLAUDE.md`。
本文件只放**项目宪法 + QuantPilot 专属规则**。

---

## 0. 项目宪法（最高优先级 · 不可妥协）

> 本节压倒后续所有章节、所有文档、所有"惯例"。冲突时以本节为准。

**元目标**：所有决策最终为「**帮助用户获取最大利益**」服务。任何与此目标冲突的便利、习惯、效率取舍都让位于它。

衍生五条不可妥协的原则——它们是元目标在工程实践中的展开：

### C-1：保护用户资产
生产数据、用户配置、未提交的工作都是用户资产。

- 生产 DB 端口 5432（容器内部，无 host 映射）；测试 DB 端口 5433。**永远不要混。**
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

---

## 1. 关键文档

| 文档 | 路径 | 用途 |
|------|------|------|
| SDD | `docs/spec/QuantPilot_SDD.md` | 系统需求与功能规范（权威） |
| 系统设计 | `docs/design/system_design.md` | 架构 + Phase 规划 §9 |
| Phase N 设计 | `docs/design/phases/phaseN_*.md` | 当前 phase 详细设计（开始任务前必读） |
| 开发指南 | `docs/guides/dev_setup.md` | 环境配置 + 命令 |
| 部署指南 | `docs/guides/deployment.md` | HTTPS / 备份 / 故障树 |
| 通用工程经验 | `~/.claude/CLAUDE.md` | 跨项目复用的 Python/async/DB/pytest 教训 |

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

## 4. 项目特定规范

### 4.1 ORM / 数据库

- `Mapped[]` 用 Python 类型 / `mapped_column()` 用 SQLAlchemy 类型；用 `DeclarativeBase` 类继承（不用 `declarative_base()`）
- `get_db()` 自动 commit；`async with AsyncSessionLocal()` 创建的 session **必须显式 commit**
- 混合 session 模式：Service 内部用 per-iteration session 同时其他工作单元走 `self._repo` 时，调用方需在 outer 块退出前显式 `await session.commit()`
- 市场数据走 `MarketDataRepository`；Route 层禁止绕过 Service 直接操作 ORM
- upsert：`insert(...).on_conflict_do_update()`；`updated_at` 显式写 `func.now()`

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

- 严格无 IO（数据库 / 文件 / 网络），只做纯函数计算；需要 IO 由 Service 层组装数据传入
- PostgreSQL `NUMERIC` 列传 pandas_ta 前必须 `.astype(float)`（避免 `isnan` TypeError）
- 交易日数 → 日历天：`calendar_days = int(history_days * 1.5)`，禁止直接 `timedelta(days=history_days)`
- APScheduler job 无法访问 `app.state`，Engine 单例须通过 `create_scheduler()` 显式 `args=[...]` 传入

### 4.5 FastAPI 项目特有

- **BackgroundTasks + UNIQUE 约束并存**：必须先 `await session.commit()` 再 `add_task()`（否则 `get_db()` 的隐式 commit 推迟到所有 BG task 跑完 → BG 写同一 UNIQUE 行被外层未 commit 行阻塞 → 循环死锁，`POST /pipeline/trigger` 真机抓到 90s 504）

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
- 检查新经验是否需写入 CLAUDE.md（项目专属）或 `~/.claude/CLAUDE.md`（跨项目通用）

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

V1.0 收尾批次：Phase 11 / 12 / 13 / **14 ✓**（§14-1~§14-10 全交付：账户幂等 + 5y candidate_pool 回填 + 日级 IC/ICIR 历史回算 + BacktestEngine 真 5 步 + 共表拆分 + Phase 13/12 评审 P2 + 交易日历持久化 CAL-1~6 + §14-10 成交/资金流水作废订正）| **Phase 15 ✓**（V1.0 RC 验收：8 子项全交付 + 收尾门槛全过 + 冒烟对生产 105 PASS；RC 期根治回测 2GB OOM——生产经 `backtest_enabled=false` 禁用回测 503，回测走本地算力中心）| **V1.5-G 多用户 代码交付完成 ✓**（2026-07-23：G-1 数据模型 / G-2a 认证 / G-2b 限频+EmailStr / G-3 账户隔离+ownership / G-4 level 分层+通知隔离+Job 多用户化+管线解耦 / G-5 前端 / G-6 测试冒烟文档收尾；unit+e2e 726 / integration 201 / ruff 0 / vue-tsc 0；设计文档 v1.4 DoD 全勾）| **V1.5-G 生产部署完成 ✓**（2026-07-23：pg_dump 前置 + 0017→0020 迁移实证 + ADMIN_* 退役 + 冒烟对生产 112 PASS/写入已 void 还原 + 前端新 bundle 上线；只重建 backend 须补 `nginx -s reload`，见 deployment.md §4.2）| **下一步**：观察今晚 17:30 管线首次以解耦代码自然运行 → 之后按 roadmap 择下一 V1.5 主题

> **运维红线（RC 验收期实证）**：① 回测在生产**禁用**（`POST /backtest/run`→503），任何"验证回测"必在本地算力中心（`scripts/run_backtest_local.py`）跑，**绝不对生产 POST /backtest/run**——单个 6 日回测即拖垮 2GB 机 11 分钟。② 给生产新增 env 变量必须**双写**：`.env.prod` + root `docker-compose.prod.yml` 的 `environment:` **白名单**（非全量透传）；改完先 `docker exec ... printenv` 确认容器拿到值再验证行为。③ 冒烟跑生产用 `API_BASE_URL=https://quant.portableagi.com`，会写虚拟数据（SMOKE01.SZ 黑名单/0.01 入金）须跑后核查并 void 还原。

详细 phase 表 + 历史里程碑（V1.0 整改 3 批次 / V1.0 真机验收 15 bug / Phase 11~13 实施细节）→ `docs/design/system_design.md §9`。
