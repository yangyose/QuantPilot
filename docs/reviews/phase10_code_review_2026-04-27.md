# Phase 10 代码评审报告

> **评审日期：** 2026-04-27
> **评审范围：** Phase 10 实现（`backend/src/quantpilot/notification/` + `services/{config,notification}_service.py` + `services/signal_service.py:generate_for_date` + `pipeline/daily_pipeline.py` + `pipeline/scheduler.py` + `api/v1/{notifications,setup,settings,backtest}.py` + `core/{config_defaults,logging_config}.py` + `main.py` lifespan + `alembic/versions/0007_*.py` + 部署文件 + `frontend/src/views/{SettingsView,OnboardingView}.vue` + `frontend/src/components/NotificationBell.vue`）
> **依据文档：** `docs/design/phases/phase10_deployment.md` v1.1（含 Q-1~Q-7 + G-1~G-8 修订）
> **评审版本：** v1.0

---

## 评审概要

| 维度 | 结论 |
|------|------|
| UserConfig 消费链路完整性 | **不达标**：`config_snapshot` 列已建、写入正确，但 DailyPipeline / BacktestService 仍使用启动期默认实例化的 Engine 单例，UserConfig 实际未被消费 |
| 通知链路（NotificationService + WxPusher） | 基本达标：渠道抽象、3 次重试、5 类业务模板、站内信兜底、去重与时段判断均落地；存在 1 处时区 bug |
| ConfigService（12 类型化 getter + 快照） | 实现完整，类型化 getter / 快照拼装 / Redis 失效均符合设计；存在 1 处 nested dict partial-overlay 缺陷 |
| 新增 REST 端点（notifications / setup / settings 导入导出） | 实现合规，Schema 与白名单一致；`PUT /settings` 缺白名单校验 |
| Alembic 0007 / ORM | 迁移与 ORM 对齐，索引（含部分索引）齐全；存在 1 处字段长度文档漂移 |
| 前端（SettingsView / OnboardingView / NotificationBell） | SettingsView 三段折叠 + 12 类配置 + 矩阵编辑 + YAML 导出/导入符合设计；OnboardingView 缺数据拉取步骤；NotificationBell 完整 |
| 部署（Dockerfile.prod / docker-compose.prod / nginx / 日志滚动） | nginx + RotatingFileHandler + JSONFormatter 正确；`uvicorn --workers 2` 与 APScheduler 单进程模型冲突 |

发现问题 **10 条**（P1×3，P2×4，P3×3）。

> **核心结论：** Phase 10 设计 v1.1 的关键原则（Q-2 端点层 partial-overlay、Q-5 启动期一次性 snapshot、§4.3 CP 仅从 `pipeline_run.config_snapshot` 读、§7.3 BacktestEngine 不读 UserConfig）在编码侧只完成了"写快照"和"端点拼默认"两半，**Engine 实例化路径仍走启动期默认值**，导致快照写入了 traceability 表，却没有真正驱动后续计算。这 3 个 P1（C-01/C-02/C-03）联动构成 UserConfig 消费链路的功能性回归。

---

## 问题清单

### C-01 【P1】DailyPipeline CP2 全部 Engine/Service 默认实例化，UserConfig 不生效

**文件：** `backend/src/quantpilot/pipeline/daily_pipeline.py`（第 216–230 行）

**问题描述：**

`_cp2_scoring` 直接以默认值实例化所有评分组件：

```python
scoring_service = ScoringService(
    repo=repo,
    universe_filter=UniverseFilter(),                 # 不读 universe_params
    strategies=[
        TrendStrategy(),                              # 不读 strategy_params_trend
        MomentumStrategy(),                           # 不读 strategy_params_momentum
        MeanReversionStrategy(),                      # 不读 strategy_params_mean_reversion
        ValueStrategy(),                              # 不读 strategy_params_value
    ],
    scorer=Scorer(),                                  # 不读 strategy_weights
    pool_manager=CandidatePoolManager(pool_capacity=20),
    calendar=self._calendar,
)
```

设计文档 §4.3：CP1 启动时取 `cfg.get_pipeline_snapshot()` 写入 `pipeline_run.config_snapshot`，CP2/CP3 应**从 snapshot 中读 dataclass，注入 Engine 构造**——当前实现完全跳过这一步，run.config_snapshot 写了但没用。

**影响：**

- 用户在 `SettingsView` 修改 `strategy_weights`（如把动量从 0.30 改为 0.50）、`universe_params`（如下调 `min_market_cap_yi`）、四个 `strategy_params_*`（如 `MomentumStrategy.return_window`）后，**DailyPipeline 永不消费**——评分始终基于代码里的默认值。
- `pool_manager=CandidatePoolManager(pool_capacity=20)` 硬编码 20，连默认 dataclass `UniverseConfig.candidate_pool_size` 都没读到。
- 这是 Phase 10 P10-B 工作包"UserConfig 消费"的核心交付项，**实质性未交付**。

**修复建议：**

```python
async def _cp2_scoring(self, run: PipelineRun, trade_date: date) -> None:
    snapshot = run.config_snapshot or {}  # CP1 已写入
    weights = StrategyWeightsConfig(**snapshot.get("strategy_weights", {}))
    universe = UniverseConfig(**snapshot.get("universe_params", {}))
    trend_cfg = TrendStrategyConfig(**snapshot.get("strategy_params_trend", {}))
    momentum_cfg = MomentumStrategyConfig(**snapshot.get("strategy_params_momentum", {}))
    mr_cfg = MeanReversionStrategyConfig(**snapshot.get("strategy_params_mean_reversion", {}))
    value_cfg = ValueStrategyConfig(**snapshot.get("strategy_params_value", {}))

    async with self._session_factory() as session:
        scoring_service = ScoringService(
            repo=MarketDataRepository(session),
            universe_filter=UniverseFilter(universe),
            strategies=[
                TrendStrategy(trend_cfg),
                MomentumStrategy(momentum_cfg),
                MeanReversionStrategy(mr_cfg),
                ValueStrategy(value_cfg),
            ],
            scorer=Scorer(weights=weights),
            pool_manager=CandidatePoolManager(universe),
            calendar=self._calendar,
        )
        ...
```

需配套确认 4 个 Strategy 类与 `UniverseFilter` 的 `__init__` 已接受对应 dataclass（评审过程中已确认 `Scorer`、`CandidatePoolManager` 已支持，但 4 个 Strategy 与 `UniverseFilter` 的实际签名需复核）。

---

### C-02 【P1】`main.py` lifespan 用默认参数构造单例 BacktestEngine，启动后再无重建路径

**文件：** `backend/src/quantpilot/main.py`（第 50–51 行、93–106 行、145–157 行）

**问题描述：**

lifespan 在启动时实例化两个全局单例：

```python
# line 50-51
app.state.market_state_engine = MarketStateEngine()         # 不读 market_state_params

# line 93-106 / line 145-157（两条 tushare token 分支各一份）
app.state.backtest_engine = BacktestEngine(
    strategies=[TrendStrategy(), MomentumStrategy(),
                MeanReversionStrategy(), ValueStrategy()],   # 全部默认
    market_state_engine=app.state.market_state_engine,
    universe_filter=UniverseFilter(),                        # 默认
    scorer=Scorer(),                                         # 默认权重
    signal_engine=SignalGenerator(),                         # 默认
    position_engine=PositionSizer(),                         # 默认
    ...
)
```

启动后，回测端点（C-03）和 MarketStateService 一直复用这两个单例，**没有从 ConfigService/snapshot 重建子 Engine 的路径**。

设计文档 §7.3 第 2 条明确："BacktestEngine 不读 UserConfig；端点层 partial-overlay；engine_snapshot 写入 backtest_task.config_snapshot 用于复现"——意味着每次回测应基于最新 UserConfig **重建**一个 BacktestEngine（或至少重建子组件），而非复用单例。

**影响：**

- MarketStateService（Phase 3）持续读默认 ADX 阈值 / MA 窗口，用户改 `market_state_params` 不生效。
- BacktestEngine 单例的子 Engine（`Scorer` / 4 个 Strategy / `UniverseFilter` / `SignalGenerator` / `PositionSizer`）全部锁定为启动时默认。即使 C-03 修复"端点层 partial-overlay 进 BacktestConfig"，子 Engine 内部的 `strategy_params_*` / `strategy_weights` / `universe_params` 仍然走默认。
- 这与 C-01 是同一根因（"snapshot 写得对但 Engine 实例化路径未改造"）的两个分身。

**修复建议：**

将 BacktestEngine 改为"每次 `POST /backtest/run` 后台任务内基于 engine_snapshot **新建实例**"——即 lifespan 不再持有 `app.state.backtest_engine`，改为持有 `app.state.market_state_engine`（无配置依赖的常量化部分）+ "calendar"。`_run_backtest_bg` 内根据 `task.config_snapshot` 重新拼装。

或者：保留单例，但在 `_run_backtest_bg` 内基于 snapshot 临时构造一组子 Engine 后**替换**单例上的引用——风险更高（多任务并发会互踩）。推荐前者。

`MarketStateEngine` 类似处理：要么改为接受 `MarketStateConfig` 的 dataclass、由 MarketStateService 每次读 ConfigService 后重建，要么把配置移到调用点（`evaluate(snapshot, *, params: MarketStateConfig)`）。

---

### C-03 【P1】`_run_backtest_bg` 用单例 BacktestEngine，engine_snapshot 写入但未消费

**文件：** `backend/src/quantpilot/api/v1/backtest.py`（第 134–166 行）、`backend/src/quantpilot/services/backtest_service.py:run_task`

**问题描述：**

`POST /backtest/run` 端点层正确做了 `BacktestConfig` 的 partial-overlay（设计 §7.3 Q-2），并将 `engine_snapshot = await cfg.get_all_for_snapshot()` 写入 `backtest_task.config_snapshot`，但后台任务执行时：

```python
# api/v1/backtest.py line 136
backtest_engine = getattr(app_state, "backtest_engine", None)   # 启动期默认单例
...
async with AsyncSessionLocal() as session:
    svc = BacktestService(session, backtest_engine)
    await svc.run_task(task_id, config, combined_progress_cb)   # 引擎已锁定
```

`BacktestService.run_task` 内进一步 `self._engine.run(config, data, progress_cb)` ——一路把这个**默认参数实例**传到底。`engine_snapshot` 只是被记到 `backtest_task.config_snapshot` 用于"看一眼用户当时配置是什么"，**没有任何路径用它驱动实际计算**。

**影响：**

- 在用户视角：改了 `strategy_weights` 后跑回测，结果与未改时**完全一致**——这违背 Phase 10 P10-B 与设计 §7.3 的核心承诺。
- 复现性表面上达标（snapshot 列存在），实际上"复现谁"是模糊的——既不是用户当时的 UserConfig，也不是实际运行时的 Engine 配置。

**修复建议：**

与 C-02 联动：lifespan 不持有 `backtest_engine` 单例；`_run_backtest_bg` 从 task.config_snapshot 读出 11+1 个 dataclass，重新构造一次 `BacktestEngine`：

```python
snapshot = task.config_snapshot or {}
backtest_engine = BacktestEngine(
    strategies=[
        TrendStrategy(TrendStrategyConfig(**snapshot.get("strategy_params_trend", {}))),
        MomentumStrategy(MomentumStrategyConfig(**snapshot.get("strategy_params_momentum", {}))),
        MeanReversionStrategy(MeanReversionStrategyConfig(**snapshot.get("strategy_params_mean_reversion", {}))),
        ValueStrategy(ValueStrategyConfig(**snapshot.get("strategy_params_value", {}))),
    ],
    market_state_engine=app_state.market_state_engine,
    universe_filter=UniverseFilter(UniverseConfig(**snapshot.get("universe_params", {}))),
    scorer=Scorer(StrategyWeightsConfig(**snapshot.get("strategy_weights", {}))),
    signal_engine=SignalGenerator(SignalConfig(**snapshot.get("signal_params", {}))),
    position_engine=PositionSizer(RiskLimitsConfig(**snapshot.get("risk_limits", {}))),
    calendar=app_state.calendar,
)
```

如果某些子 Engine（如 `SignalGenerator` / `PositionSizer`）当前并未支持 dataclass 注入，需在本次修复中补全（评审过程已发现 `Scorer`/`CandidatePoolManager`/`SignalService` 路径已通，剩余子 Engine 需逐一确认）。

---

### C-04 【P2】NotificationService 推送时段判断使用进程本地时间，生产容器为 UTC，A 股交易时段判断会偏 8 小时

**文件：** `backend/src/quantpilot/services/notification_service.py`（第 233–241 行）

**问题描述：**

```python
@staticmethod
def _in_push_window(prefs: NotificationConfig, now: datetime | None = None) -> bool:
    """当前小时是否在 [push_start_hour, push_end_hour) 内（按系统本地时间）。"""
    h = (now or datetime.now()).hour
    ...
```

注释说"按系统本地时间"，而生产 Dockerfile (`python:3.12-slim`) 默认 TZ=UTC。`docker-compose.prod.yml` 也未设置 `TZ` 环境变量。

`NotificationConfig.push_start_hour=9, push_end_hour=22` 对应**沪深交易日 9:00–22:00 Asia/Shanghai**——但容器里 `datetime.now().hour` 实际返回 UTC 小时。北京时间 9:00 时，容器内 `h=1` 落在区间外，**信号推送被时段判断屏蔽**。

`scheduler.py` 中其他 Job 都已用 `CronTrigger(timezone=ZoneInfo("Asia/Shanghai"))` 显式 TZ，唯独此处依赖 naive datetime。

**影响：**

- 通知在交易时段被过滤掉，主功能（信号推送）静默失效。
- 因 InAppNotification 同步写入（不走 push_window 过滤），用户在站内信能看到，**但 WxPusher 推送丢失**——表象是"app 里有，微信没收到"，定位困难。

**修复建议：**

```python
from zoneinfo import ZoneInfo
_PUSH_TZ = ZoneInfo("Asia/Shanghai")

@staticmethod
def _in_push_window(prefs: NotificationConfig, now: datetime | None = None) -> bool:
    h = (now or datetime.now(tz=_PUSH_TZ)).hour
    ...
```

同时建议在 `docker-compose.prod.yml` 给 backend 服务追加 `TZ: Asia/Shanghai` 防御性设置，并补一个单元测试 `tests/unit/test_notification_push_window.py` 覆盖跨日（22→6）边界与 TZ 切换。

---

### C-05 【P2】`ConfigService._get_typed` partial-overlay 仅 1 层深，nested dict 无法部分覆盖

**文件：** `backend/src/quantpilot/services/config_service.py`（第 196–199 行）

**问题描述：**

```python
merged = {**asdict(default), **db_value}        # 1 层 dict 浅合并
valid_fields = {f for f in asdict(default).keys()}
merged = {k: v for k, v in merged.items() if k in valid_fields}
```

对于 `StrategyWeightsConfig`，其 dataclass 含 3 个 nested dict（`uptrend` / `downtrend` / `oscillation`）：

```python
@dataclass(frozen=True)
class StrategyWeightsConfig:
    uptrend: dict[str, float] = field(default_factory=lambda: {
        "trend": 0.40, "momentum": 0.30, "mean_reversion": 0.10, "value": 0.20
    })
    downtrend: dict[str, float] = ...
    oscillation: dict[str, float] = ...
```

如果用户通过 `PUT /settings` 仅写入 `{"strategy_weights": {"uptrend": {"trend": 0.5}}}`（用户视角"我只想调上涨态的 trend"），当前逻辑会以**整个**残缺 `uptrend` dict 覆盖默认 `uptrend`：用户得到 `uptrend = {"trend": 0.5}`，`momentum/mean_reversion/value` 全部消失，下游 `Scorer` 在归一化时把这三个策略权重视作 0。

**影响：**

- 用户"小步迭代调权重"是高频路径（前端 SettingsView 矩阵编辑器允许单格修改但前端有兜底），但通过 YAML 导入时不一定走完整三态——一旦用户写不全，残值落库后 partial-overlay 不能补齐。
- `risk_limits` / `signal_params` / `factor_monitor_params` 当前皆为 flat dict，本问题主要影响 `strategy_weights`，未来若 `signal_params` / `notification_prefs` 演化出 nested 结构会扩散。

**修复建议：**

`_get_typed` 在 `cls=StrategyWeightsConfig` 分支做 2 层深合并；或者更通用的递归深合并（仅在 default 字段的值仍是 dict 时递归）：

```python
def _deep_merge(default: dict, override: dict) -> dict:
    out = dict(default)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
```

或者在 schema 层对 `strategy_weights` 增校验：要求 `uptrend/downtrend/oscillation` 三个 dict 每个 value 字段 4 个策略全在，缺少则 422——但这会让"局部修改"失败，UX 差。深合并更合适。

---

### C-06 【P2】Dockerfile.prod / docker-compose.prod 用 `uvicorn --workers 2`，与 APScheduler 单进程模型冲突，定时任务会重复执行

**文件：** `backend/Dockerfile.prod`（第 42 行）、`docker-compose.prod.yml`（第 78–80 行）

**问题描述：**

```dockerfile
# Dockerfile.prod line 42
CMD ["uvicorn", "quantpilot.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

```yaml
# docker-compose.prod.yml line 78-80
command: >
  sh -c "alembic upgrade head &&
         uvicorn quantpilot.main:app --host 0.0.0.0 --port 8000 --workers 2"
```

`main.py:lifespan` 在每个 worker 内部启动 `AsyncIOScheduler`（in-memory jobstore），4 个定时任务（`daily_pipeline_job` / `monthly_job` / `weekly_report_job` / `stop_loss_warn_job`）会在 **2 个 worker 中各自跑一次**——

- `daily_pipeline_job` 会重复执行：CP1 入库幂等（upsert）→ OK，但 CP2 评分写盘、CP3 信号生成、Step4/5/6 通知会出现"两遍"，包括**对用户重复推送同一条 SIGNAL_BUY**（去重窗口 5 分钟可救一部分，但跨 workers 间隔可能 >5 分钟）。
- `monthly_job` / `weekly_report_job` 同理：报表表无 UNIQUE 约束（CLAUDE.md 中已记录），同月会有 2 份相同 Report 行。
- `stop_loss_warn_job` 在 15:05 触发，会推送两份 STOP_LOSS_WARN。

**影响：**

- 通知重复 → 用户体验差，可能误以为系统出故障。
- 报表/信号重复入库 → 数据冗余，且占据 lineage / pipeline_run 编号。
- 与设计 §8.1（生产 Compose）"单后端实例"假设冲突。

**修复建议：**

二选一：

**方案 A（推荐，最小修改）**：去掉 `--workers 2`，回到单进程：
```dockerfile
CMD ["uvicorn", "quantpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
单管理员场景吞吐够用。

**方案 B（保多进程）**：将 APScheduler 从 backend 容器分离为独立 sidecar 容器（`docker-compose.prod.yml` 增加 `scheduler` service，仅运行 scheduler.start()），backend 只跑 uvicorn 多 worker。需新增独立入口脚本 + `apscheduler-sqlalchemy-jobstore` 共享 Jobstore。

二选一前先确认 Phase 10 §8.1 设计意图：CLAUDE.md 中 Phase 10 进度显示设计原意是"单实例"，应该走方案 A。

同时建议在 `main.py:lifespan` 起 scheduler 前加保护：检测 `os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"`，运维可在多 worker 场景下显式只让一个进程开启。

---

### C-07 【P2】OnboardingView 缺数据拉取步骤，与 Phase 10 §6.6 / DoD §1.3 不符

**文件：** `frontend/src/views/OnboardingView.vue`

**问题描述：**

Phase 10 设计文档 §6.6 与 DoD §1.3 规定 Onboarding 向导含 **4 个实质步骤**：

1. Tushare Token 配置
2. **数据拉取**（调用 `POST /api/v1/data/ingest/stock_info` + `POST /api/v1/data/ingest/quotes` 触发首次入库；进度条显示）
3. 初始资金
4. 参数默认（`StrategyWeightsConfig` 等关键 config_key 的引导默认值）

当前实现只有 5 个 UI 步：欢迎 / Token / 初始资金 / 参数默认 / 完成——**完全跳过了数据拉取步**。新用户走完向导后，数据库内仍无任何行情/财务数据，进首页时所有图表为空、`POST /pipeline/trigger` 也跑不动（universe 为空）。

`backend/src/quantpilot/api/v1/setup.py` 的 setup_state 表内已记录数据拉取的状态字段，但前端没有对应触发面板。

**影响：**

- 新用户首次登录后体验断裂：向导提示"已完成"，但首页空白，用户不知道下一步该手动到 SettingsView 还是 DataIngestionView 触发拉取。
- 从 LoginView → OnboardingView → Dashboard 的"开箱即用"承诺未兑现。

**修复建议：**

OnboardingView 在 Token 步与初始资金步之间插入"数据拉取"步：
- 调用 `POST /api/v1/data/ingest/stock_info`（同步，几秒）
- 调用 `POST /api/v1/data/ingest/quotes` 拉最近 60 个交易日（异步任务 + 进度轮询，与 BacktestView 进度面板复用 `redis_progress_cb` 模式）
- 失败提示用户去 DataIngestionView 重试，但允许跳过继续后续步骤
- 同时在 `setup.py` 的 setup_state 中追加 `data_ingest_done` 字段，`POST /api/v1/setup/complete` 校验

也需要在 `frontend/src/api/setup.ts` 中补对应触发函数，在 `tests/smoke/test_api_live.py` 增加 API-85 冒烟覆盖。

---

### C-08 【P3】`PUT /settings` 缺 12-key 白名单校验，未知 config_key 会被静默写入 user_config 表

**文件：** `backend/src/quantpilot/api/v1/settings.py`（第 52–66 行）

**问题描述：**

`/settings/export` 与 `/settings/import` 都已用 `_VALID_CONFIG_KEYS` (12 项 frozen set) 过滤，但 `PUT /settings`：

```python
@router.put("")
async def update_setting(
    body: UserConfigUpdate,
    service: SettingsService = Depends(get_settings_service),
    cfg: ConfigService = Depends(get_config_service),
    _: str = Depends(get_current_user),
) -> dict:
    config = await service.upsert_setting(
        config_key=body.config_key,             # 任意字符串均被接受
        config_value=body.config_value,
        change_note=body.change_note,
    )
    await cfg.invalidate(body.config_key)
    return {...}
```

任何字符串 key 都会进库，并占据一条 `user_config_history` 记录。前端 OnboardingView 实际就利用这一点写入了一个非合法 key `initial_account_config`（用于保存初始资金，但这条记录 ConfigService 永远不会消费）。

**影响：**

- 数据卫生：DB 累积"僵尸"配置（前端 OnboardingView 已经在写）。
- 接口约束破坏：设计承诺"12 个 config_key + 1 个 risk_free_rate scalar"，但 PUT 通道实际放行任意 key。
- 反向：如果前端写错 key（如 `signal_param`，少 's'），用户改了半天没生效，难以诊断。

**修复建议：**

```python
if body.config_key not in _VALID_CONFIG_KEYS:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"未知 config_key={body.config_key}（合法集合见 /settings/export）",
    )
```

并修复 OnboardingView：把"初始资金"改为真接 `POST /account` 的 `initial_capital` 字段（已存在），不要写 user_config。
也补 `tests/e2e/test_settings_api.py` 一个非法 key → 400 的负向用例。

---

### C-09 【P3】Alembic 0007 中 `notify_type` 字段长度 String(32)，与设计文档 §2.1 标注的 String(20) 不一致

**文件：** `backend/alembic/versions/0007_phase10_config_and_notifications.py`、`backend/src/quantpilot/models/business.py`（InAppNotification 第 183–220 行）

**问题描述：**

设计文档 phase10 §2.1 InAppNotification 表中 `notify_type` 列标注 `String(20)`，但实际迁移与 ORM 都用 `String(32)`。当前 7 类 enum 字符串最长为 `STOP_LOSS_WARN`（14 字符）+ `PIPELINE_FAILURE`（16 字符），32 留有冗余。

**影响：**

- 无功能影响（实现更宽松）。
- 文档漂移：未来若 enum 新增类型，开发者按设计文档 String(20) 检查会得到错误结论。

**修复建议：**

二选一：

A. 更新 `docs/design/phases/phase10_deployment.md` §2.1，将 `notify_type` 标为 `String(32)`，并在修订历史追加一笔说明。
B. 加一个 alembic ALTER COLUMN 把列改回 `String(20)`（V1.0 数据少，迁移成本低）。

推荐方案 A——String(32) 更经得起未来扩展；同时把这条记入"phase 收尾文档同步"经验。

---

### C-10 【P3】CP1 / CP3 / 通知钩子仍创建独立 ConfigService 实例，未从 `pipeline_run.config_snapshot` 读取，违反设计 §4.3 "snapshot once"

**文件：** `backend/src/quantpilot/pipeline/daily_pipeline.py`（第 176 行 `_cp1_ingest`、第 267 行 `_cp3_signals`、第 403 行 `_notify_new_signals`、第 433 行 `_notify_pipeline_failure`）

**问题描述：**

设计文档 §4.3 明确：

> CP1 启动时调用 `ConfigService.get_pipeline_snapshot()`，写入 `pipeline_run.config_snapshot`；
> CP2/CP3 及后续 Step4/5/6 **仅从 run.config_snapshot 取值**，不再访问 ConfigService。
> 目的：本次 Pipeline 期间配置不变（防止用户中途修改导致 CP2/CP3 行为分裂）。

实际实现中，CP1 写了 snapshot，但 CP3 与两个 _notify_* 钩子各自 `ConfigService(session, self._redis)` 重新实例化、重新走类型化 getter 路径——即每个 CP / 通知钩子各读一次 DB（缓存命中也是各自走一次）。

**影响：**

- 一致性破坏：极端情况下用户在 CP1 后、CP3 前 PUT /settings 修改了 risk_limits，CP3 走的是新值而不是 run.config_snapshot 里的旧值。pipeline_run.config_snapshot 字段失去"复现真相"的保证。
- 性能：每个 CP / Step 都去打 Redis + DB，与"snapshot once"违背。
- 影响范围有限：当前 CP1/CP3 主要使用 NotificationService（间接读 notification_prefs），用户在交易日内中途改配置概率不高。

**修复建议：**

将 daily_pipeline 内所有 NotificationService / SignalService 实例化路径改为接受 snapshot：

```python
# 公共解析（在 _cp2_scoring 以及更早，run.config_snapshot 已可用）
def _build_configs_from_snapshot(snapshot: dict) -> Configs:
    return Configs(
        signal=SignalConfig(**snapshot.get("signal_params", {})),
        risk=RiskLimitsConfig(**snapshot.get("risk_limits", {})),
        notification=NotificationConfig(**snapshot.get("notification_prefs", {})),
        ...
    )

# CP3
configs = _build_configs_from_snapshot(run.config_snapshot or {})
notifier = NotificationService(session, configs.notification, self._notification_channel)
signal_service = SignalService(repo, account_service, configs, notifier)
```

需要调整 `NotificationService.__init__` 与 `SignalService.__init__` 签名：从"持有 ConfigService"改为"持有所需 dataclass"。

---

## 已验证 OK 项

以下实现完整、与设计文档一致，无需调整：

1. **`notification/base.py` NotificationChannel ABC**：抽象方法 `send(self, *, subject, body, summary)`，签名清晰；`configured` 属性为后续渠道扩展（邮件/Slack）预留接口。
2. **`notification/wxpusher.py` WxPusherAdapter**：3 次重试 @ 30s，每次失败记 WARN 含 attempt 编号；`app_token`/`uid` 缺失时实例化不抛错、`configured=False`，调用 `send` 直接降级返回 False；网络层 `httpx.AsyncClient(timeout=10)` 边界条件清晰。
3. **`services/notification_service.py` 5 类业务模板**：`notify_signal_buy/sell` / `notify_market_state_change` / `notify_stop_loss_warn` / `notify_factor_alert` / `notify_pipeline_failure` 模板与 SDD §13.3 完全对齐；去重窗口（5 分钟内 payload 完全相等→丢弃）使用 PostgreSQL JSONB `=` 规范化比较，正确；commit 由 caller 控制（flush-only）符合 CLAUDE.md "后台任务 session 必须显式 commit"约束。
4. **`services/config_service.py` 12 个类型化 getter + `get_pipeline_snapshot`/`get_all_for_snapshot`**：覆盖完整，键名与 DEFAULT_* 常量、SDD 附录 B 一致；Redis 缓存 5 分钟 TTL + `invalidate(key)` 失效路径正确；DB 值结构损坏时 ERROR 日志 + 回退默认（不静默吞）。
5. **`services/signal_service.py:generate_for_date`**：在没有 `account_svc` / `cfg` 时显式 RuntimeError（不静默退化为"空信号"）；正确串联 `SignalGenerator` → `PositionSizer` → `RiskChecker`；`risk_warn` 通过 `notification_service.notify_risk_warn` 推送；返回值仅含本次新建信号（C-05 修复留存）。
6. **`engine/scorer.py:Scorer.__init__(weights: StrategyWeightsConfig = DEFAULT_STRATEGY_WEIGHTS)`** + **`engine/pool.py:CandidatePoolManager.__init__(config | pool_capacity)`**：dataclass 注入路径已实现，但 daily_pipeline 未利用（见 C-01）。
7. **`api/v1/notifications.py` 5 端点**（`/notifications` 列表 / `/unread-count` / `/wx-status` / `/{id}/read` / `/read-all`）：响应 schema 一致、未鉴权 401、负向路径覆盖。
8. **`api/v1/setup.py`**：2 端点 `/setup/status` + `/setup/complete` 简洁；`SetupState` ORM 单行模式，标记完成幂等。
9. **`api/v1/settings.py` YAML 导出/导入**：`_VALID_CONFIG_KEYS` 12 项 frozen set 与 `ConfigService.get_all_for_snapshot` 对齐；导入未知 key 计 `skipped_keys` 而非报错；`dry_run=True` 不改库，`POST /import` 成功后 `cfg.invalidate(key)` 主动失效缓存。
10. **`alembic/versions/0007_phase10_config_and_notifications.py`**：3 个新增结构（pipeline_run.config_snapshot / backtest_task.config_snapshot / in_app_notification 表）齐全；`idx_notify_unread` 用 `WHERE read_at IS NULL` 部分索引（仅索引未读行），性能优；`idx_notify_type_created` 复合索引覆盖按类型筛选场景。
11. **`pipeline/scheduler.py` 4 个 Job 注册**：`daily_pipeline_job` / `monthly_job` / `weekly_report_job` / `stop_loss_warn_job` 全部 `CronTrigger(timezone=ZoneInfo("Asia/Shanghai"))` 显式时区；`stop_loss_warn_job` 用 calendar.is_trade_date 过滤非交易日；NotificationService + ConfigService 注入正确（仅遗留 C-06 多 worker 重复触发问题）。
12. **`core/logging_config.py`**：`RotatingFileHandler(maxBytes=50MB, backupCount=7)` + `JSONFormatter`（timestamp/level/logger/message/module/function/line/exc_info）；第三方库噪声压制（apscheduler/httpx/uvicorn.access → WARNING）；`enable_json` 开关支持 dev/prod 分离。
13. **`nginx/nginx.prod.conf`**：SPA 回退、静态资源 30d 长缓存、`/api/` 反代、`/ws|api/v1/.+/progress` WebSocket 升级（含 3600s 长连读超时）、`/health` 直通 backend、gzip 配置完整。
14. **`frontend/src/views/SettingsView.vue`**：12 类 config_key catalog；3 段折叠（基础/进阶/专家）；字段级 tier override；`tooltipTerm` 接入术语字典（`glossary.ts`）；`strategy_weights` 3 状态 × 4 策略矩阵编辑器；YAML 导出/导入 + dry_run 预览；通知偏好独立 Tab；watchlist 独立编辑。
15. **`frontend/src/components/NotificationBell.vue`**：a-badge 数字徽标 + 30s 轮询 unread-count；下拉抽屉懒加载 listNotifications；标记单条/全部已读乐观更新；类型颜色映射含 7 个 notify_type；`fmtTime` 切片 19 位避免毫秒/时区噪声。
16. **`backend/Dockerfile.prod`**：multi-stage（builder + runtime），`uv sync --no-dev --frozen` 优先（lock 缺失自动 fallback），非 root（`useradd -u 1000 quantpilot`），`HEALTHCHECK` 走 `/health`，`PYTHONPATH=/app/src` 与 src layout 一致。

---

## 整体评估

Phase 10 在"通知链路"（C-04 时区 bug 除外）和"配置存储/快照"层面已经达到设计标准；前端 SettingsView / NotificationBell 也实现完整。**真正未交付的是 UserConfig → Engine 的消费链路**：design v1.1 articulate 的"启动期 snapshot once + 端点层 partial-overlay + Engine 不读 ConfigService"原则只完成了"写 snapshot 列"和"端点拼默认值"两半，**Engine 实例化路径仍走启动期默认值**——三个 P1（C-01/C-02/C-03）联动构成 P10-B 工作包的功能性回归。

**修复优先级建议：**

| 顺位 | 问题编号 | 工作量估计 | 风险 |
|------|---------|-----------|-----|
| 1 | **C-01 + C-02 + C-03 联动** | 1-2 天（含 4 个 Strategy 类签名复核 + 集成测试） | 涉及 lifespan / daily_pipeline / backtest 三处主路径，需补集成测试覆盖"修改配置 → 跑 pipeline → 验证评分变化" |
| 2 | C-06 多 worker | 5 分钟（去掉 `--workers 2`） | 低 |
| 3 | C-04 推送时段时区 | 30 分钟（含单测） | 低 |
| 4 | C-07 OnboardingView 数据拉取步 | 半天（前端 + 后端 setup_state 字段 + 冒烟） | 中（涉及 redis 进度复用） |
| 5 | C-05 nested partial-overlay | 1 小时（_deep_merge + 单测） | 低 |
| 6 | C-08 / C-10 | 各 30 分钟 | 低 |
| 7 | C-09 文档同步 | 5 分钟 | 极低 |

完成 C-01/C-02/C-03/C-06/C-04/C-07 后，Phase 10 可视为达到设计文档承诺的交付标准；C-05/C-08/C-10 属可在后续 phase 一并清理的小幅改进；C-09 仅涉及文档对齐。

---

## 附：评审用对照清单

| 设计原则（v1.1） | 实现状态 |
|------------------|---------|
| Q-2 BacktestEngine 不读 UserConfig；端点层 partial-overlay；engine_snapshot 写入 backtest_task.config_snapshot | 端点层 ✓ / 写 snapshot ✓ / **不读 UserConfig ✗（C-03）** |
| Q-5 Pipeline 启动一次性 snapshot，CP 仅从 run.config_snapshot 读 | 写 snapshot ✓ / **CP 仅从 snapshot 读 ✗（C-01/C-10）** |
| Q-6 strategy_weights 键名统一 `mean_reversion`（非 `reversion`） | ✓ |
| Q-7 settings 12 key 白名单 + YAML 导入跳过未知 key | YAML ✓ / **PUT /settings ✗（C-08）** |
| §5.4 RiskChecker WARN/BLOCK 推 RISK_WARN | ✓（generate_for_date 已注入 notifier） |
| §6.6 / DoD §1.3 Onboarding 4 实质步含数据拉取 | **缺数据拉取步 ✗（C-07）** |
| §7.3 Engine 层接收 dataclass + 仍纯函数（CLAUDE.md §6） | dataclass 接口 ✓ / **daily_pipeline 不利用 ✗（C-01）** |
| §8.1 生产 Compose 单实例 | **uvicorn --workers 2 与 APScheduler 冲突（C-06）** |
| §8.4 RotatingFileHandler 50MB × 7 + JSONFormatter | ✓ |
