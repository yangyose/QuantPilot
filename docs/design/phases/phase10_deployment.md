# Phase 10：配置消费 + 通知 + 部署收尾

> **版本：** v1.2
> **日期：** 2026-04-27
> **依据文档：** QuantPilot_SDD.md §6.5, §13, §14, §15.1, §15.5, 附录 B；system_design.md §3, §5.10, §8, §9

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-04-20 | Phase 10 初版：在原定"通知与部署"基础上扩充 UserConfig 消费链路（解决 Phase 1~9 发现的"配置表存在但消费端未接入"缺陷）、Settings 前端完整化、L1/L2/L3 高级开关替代、新手向导、全量收尾。范围从"通知与部署"扩展为"配置消费 + 通知 + 部署收尾" |
| v1.1 | 2026-04-20 | **评审修订**（`docs/reviews/phase10_design_review_2026-04-20.md` 7 项内部问题 + 8 项覆盖核查）：①通知适配器路径 `data/adapters/wxpusher.py` → `notification/wxpusher.py` + 新增 `notification/base.py`（NotificationChannel ABC），对齐 system_design §5.10（Q-1）；②§4.4 新增 BacktestEngine UserConfig 接入小节——partial-overlay 合并在 `POST /backtest/run` 端点层（Q-2）；③`notification_prefs` 同步 SDD §14.4 扩展至 6 项开关（Q-3，驱动 SDD v1.x 同步）；④§5.2 显式声明 WxPusher 失败/兜底/写库失败三级日志级别（Q-4）；⑤§4.3 明确 `pipeline_run.config_snapshot` 在 Pipeline 启动时一次性写入、所有 CP 从快照读取（Q-5）；⑥§2.3 `strategy_weights` 键名 `reversion` → `mean_reversion`，与 `BaseStrategy.name` 对齐（Q-6）；⑦§2.3 `backtest_defaults.slippage_rate` 注明 SDD 附录 B 未列，待 SDD v1.x 补录（Q-7）；⑧§1.2 新增「SDD 功能点裁决表」覆盖 G-3~G-8（IC 窗口独立为 `factor_monitor_params` 第 12 个 config_key；多账户 UI / 行为分析完整指标 V1.5；OnboardingWizard 4 步在 V1.0 交付）；⑨§1.2 增加 WebSocket 前端消费（G-1）与 AKShare 自动降级（G-2）V1.5 推迟声明 |
| v1.2 | 2026-04-27 | **代码评审修订**（`docs/reviews/phase10_code_review_2026-04-27.md` 10 项问题）：①C-01/02/03 联动——DailyPipeline `_cp2_scoring` / `_cp3_signals` / `_notify_*` 均从 `run.config_snapshot` 反序列化 dataclass 注入 Engine（新建 `services/config_snapshot.py::from_snapshot` + `ConfigService(snapshot=...)` 冻结模式）；BacktestEngine 不再走 lifespan 单例，每次 `_run_backtest_bg` 从 `backtest_task.config_snapshot` 重建；MarketStateEngine 在 CP1 内按 snapshot 重建，DailyPipeline 构造方移除 `market_state_engine` 形参（scheduler/`api/v1/pipeline.py` 同步收敛）；②C-04 推送时段判断改 `Asia/Shanghai` ZoneInfo（生产容器 UTC 时差 8h 屏蔽推送），docker-compose.prod 加 TZ；③C-05 `_get_typed` 引入 `_deep_merge` 支持 nested dict 部分覆盖（避免 `strategy_weights.uptrend` 单格修改清空其它策略权重）；④C-06 Dockerfile.prod / docker-compose.prod 去 `--workers 2`，与 APScheduler in-memory jobstore 单进程模型对齐；⑤C-08 `PUT /settings` 引入 12-key 白名单校验，未知 key→400；OnboardingView "初始资金"改走 `POST /account/deposit`，不再写非法 `initial_account_config`；⑥C-09 `notify_type` 字段长度文档侧改为 `String(32)` 与实现对齐（含 PIPELINE_FAILURE 扩展冗余）；⑦C-10 与 C-01 同步：`_notify_*` 钩子也从 snapshot 解析 `NotificationConfig`，不再各自 new ConfigService；⑧C-07 OnboardingView 新增「初始数据拉取」步：自动 GET `/data/status` 检查新鲜度 → 用户输入回填天数（默认 60，1–365）→ 同步调 `POST /data/ingest/history` → 展示 success_count / fail_count / failed_dates 摘要；503 时分流到「请配置 .env 后重启」提示并允许跳过。配套新增 `frontend/src/api/data.ts` + 4 个 DataStatus / IngestHistoryResult 等 TS 类型 |

---

## 1. 范围声明

### 1.1 本 Phase 纳入模块（system_design §9 Phase 10 更新后）

**P10-A 通知与提醒**

| 模块 | 路径 | 说明 |
|------|------|------|
| NotificationChannel ABC | `notification/base.py`（新增） | 通知渠道抽象基类（对齐 system_design §5.10）；WxPusher/InApp 均实现此接口 |
| WxPusher 适配器 | `notification/wxpusher.py`（新增） | 真实 HTTP 客户端 + 3 次重试（SDD §13.1）；V1.1 评审修订：由 `data/adapters/` 迁出，避免与 `DataSourceAdapter` 语义混淆 |
| NotificationService | `services/notification_service.py` | 替换 no-op stub 为真实实现 + 降级系统内通知 |
| 市场状态变更推送 | `pipeline/scheduler.py` + `services/market_state_service.py` | 识别日调用 notify_market_state_change（SDD §13.2） |
| 止损预警 Job | `pipeline/scheduler.py` | 每日收盘后检查持仓距止损价 ≤ 2%（SDD §13.2） |
| 风险告警推送 | `pipeline/daily_pipeline.py` | 行业/仓位超限时推送（SDD §13.2） |
| 系统内通知表 | `models/business.py` | 新增 `InAppNotification` ORM + 端点 |

**P10-B UserConfig 消费全接入**

| 模块 | 路径 | 说明 |
|------|------|------|
| ConfigService | `services/config_service.py`（新增） | 统一配置访问器，分组 dataclass，Redis 5 分钟缓存 |
| ConfigDefaults | `core/config_defaults.py`（新增） | SDD 附录 B 默认值代码常量（单一事实来源） |
| 各 Engine 改造 | `engine/signal.py`、`engine/risk.py`、`engine/pool.py`、`engine/market_state.py`、`engine/universe.py`、`engine/scorer.py`、`engine/strategies/*.py` | 接收 dataclass 参数（不直接读 DB，保持纯函数） |
| Scorer 权重矩阵消费 | `engine/scorer.py` + `services/strategy_service.py` | 读 `strategy_weights` config_key 三态矩阵，缺失回退 SDD §7.5 |
| Pipeline 配置快照 | `pipeline/daily_pipeline.py` + `models/system.py` | CP1 写 `pipeline_run.config_snapshot` JSONB |
| BacktestService 默认 | `services/backtest_service.py` | 回测任务默认 commission/stamp_tax/slippage 从 `backtest_defaults` 读 |

**P10-C Settings 前端完整化**

| 模块 | 路径 | 说明 |
|------|------|------|
| SettingsView 重构 | `frontend/src/views/SettingsView.vue` | 基础/高级/专家三段折叠；SDD §14.1 全量参数 |
| NotificationSettingsTab | `frontend/src/components/settings/NotificationTab.vue`（新增） | SDD §14.4 四项推送偏好 |
| WatchlistTab | `frontend/src/components/settings/WatchlistTab.vue`（新增） | 黑白名单管理界面（复用 `/watchlist` API） |
| OnboardingWizard | `frontend/src/views/OnboardingView.vue`（新增） | 首次启动向导（Token/数据拉取/初始资金/参数默认） |
| ConfigExport | `frontend/src/api/settings.ts` | YAML 导出/导入 |
| EmptyState 全页覆盖 | `frontend/src/views/*.vue` | 所有空状态引导文案 |
| 术语提示 | `frontend/src/components/Tooltip*.vue` | SDD §15.1 术语悬浮 |

**P10-D 收尾遗留**

| 条目 | 位置 | 处理 |
|------|------|------|
| `SignalService.generate_for_date` 降级 | `services/signal_service.py:260` | 接入 PositionSizer + RiskChecker（取消降级注释） |
| `DailyPipeline.notifier` no-op | `pipeline/daily_pipeline.py:204` | 替换为真实 WxPusher |
| `FactorMonitorService` no-op | `services/factor_monitor_service.py:171` | 告警走 WxPusher |

**P10-E 部署与运维**

| 模块 | 路径 | 说明 |
|------|------|------|
| 生产 Compose | `docker-compose.prod.yml`（新增） | Nginx + SSL + 后端 + 前端 + DB + Redis |
| Nginx 配置 | `nginx/nginx.prod.conf`（新增） | 反代、静态托管、WS 升级、压缩 |
| 部署脚本 | `scripts/deploy.sh`、`scripts/backup_db.sh` | 一键部署 + DB 备份恢复 |
| 日志滚动 | `core/logging_config.py` | RotatingFileHandler + JSON 结构化（SDD §15.5） |
| 部署文档 | `docs/guides/deployment.md`（新增） | 生产部署指南（含首次启动步骤） |
| 全链路冒烟 | `scripts/prod_smoke.sh` | 自动化生产环境冒烟 |

### 1.2 显式排除（明确归 V1.5）

- 用户分层 RBAC（`user_level` 权限校验） — V1.5
- 邮件通道（SDD §13.1） — V1.5
- 策略插件沙箱（`plugin_runner.py`） — V1.5
- 低波动策略（`low_volatility.py`） — V1.5
- 多因子回归归因（`factor_attribution`） — V1.5
- 完整行为分析指标（`stop_loss_execution_rate` / `chase_up_rate`） — V1.5（SDD §12.4，对应评审 G-5）
- `is_st` / `is_suspended` StockInfo 联查补充 — V1.5
- AdjustedPriceProvider 历史批量后复权 — V1.5
- **Pipeline 进度 WebSocket 前端消费** — V1.5（评审 G-1）：system_design §2.7 规划 `/ws/pipeline/progress`；后端 **未实装**；`/ws/backtest/{task_id}/progress` **后端已实装**（`api/v1/backtest.py:246`），V1.0 前端降级为轮询 `GET /backtest/status/{task_id}`，V1.5 前端再接入 WS；system_design §9 Phase 10 不含 WS 前端消费
- **AKShare 自动降级** — V1.5（评审 G-2）：`data/adapters/akshare.py::AKShareAdapter` 类已存在但 DataService 仅依赖 `TushareAdapter`（`main.py:51-56`），Tushare 失败时**无自动切换**；V1.0 仅靠 Tushare 重试 + ERROR 日志人工介入；V1.5 在 DataService 内加"主备双读"路径
- **多账户 UI 切换** — V1.5（评审 G-4）：SDD §11 账户模型支持多账户，后端 `account_id` 已贯通；前端 Phase 9 硬编码 `account_id=1`，V1.0 保持单账户 UI，V1.5 新增账户切换器

### 1.3 SDD 功能点裁决表（评审 G-3~G-8 收敛）

| 功能点 | SDD 章节 | V1.0 Phase 10 裁决 | 理由 |
|--------|---------|------------------|------|
| IC 下期收益窗口参数化 | 附录 B | **纳入**（评审 G-3）：新增第 12 个 config_key `factor_monitor_params`（`ic_window=20` / `ic_alert_threshold=0.02` / `half_life_window=60`）；详见 §2.3 | 用户可调参数精神 + SDD 附录 B 已列窗口默认值，仅缺配置入口 |
| 多账户 UI 切换 | §11 | **推迟 V1.5**（评审 G-4） | 后端已支持，前端改造超出 Phase 10 范围 |
| 完整行为分析指标 | §12.4 | **推迟 V1.5**（评审 G-5） | 已列入 §1.2 排除清单，保持一致 |
| 消息模板 minimum 集合 | §13.3 | **纳入**（评审 G-6）：5 类事件（买入/卖出/市场状态/止损预警/因子告警）均在 §5.3 提供模板字符串；风险告警共用通用模板 | 通知链不可用则通知信息缺失 |
| OnboardingWizard 4 步骤 | §15.1 | **纳入 V1.0 全量 4 步**（评审 G-7）：Token → 数据拉取 → 初始资金 → 参数默认；详见 §6.6 | Phase 9 仅占位，Phase 10 完成 |
| 配置版本管理 UI 覆盖所有 config_key | §14.6 | **纳入**（评审 G-8）：12 个 config_key 的历史查看/回退均由 Phase 6 已交付端点 `/settings/config-history` + `/settings/config-history/{id}/revert` 支持，Phase 10 前端改造将覆盖全部 12 项 | 后端已通，前端跟进即可 |

### 1.4 前序 Phase 推迟项继承清单

| 来源 | 推迟项 | 本 Phase 处理 |
|------|--------|--------------|
| Phase 5 | P5-PRE 手动脚本 `backfill_td123.py` 退役 | 已在 Phase 5 完成，本 Phase 不涉及 |
| Phase 5 | `signal_service.py:260` PositionSizer/RiskChecker 集成 | **P10-D1 处理** |
| Phase 7 | `DailyPipeline.notifier` no-op | **P10-A5 / P10-D2 处理** |
| Phase 7 | `NotificationService` no-op | **P10-A5 处理** |
| Phase 7 | `FactorMonitorService` 告警 no-op | **P10-D3 处理** |
| Phase 9 | Settings 页只有 4 项伪配置 | **P10-C1 处理（全量重构）** |

### 1.5 【设计待定】解析

本 Phase 无 system_design §9 标注的待定项；所有待定均在设计时收敛。

---

## 2. 数据模型

### 2.1 新增迁移（0007）

**pipeline_run.config_snapshot 列**

```python
# alembic/versions/0007_phase10_config_and_notifications.py
op.add_column(
    "pipeline_run",
    sa.Column("config_snapshot", postgresql.JSONB, nullable=True,
              comment="CP1 时生效的所有 *_params 快照（SDD §14.6 归因追溯）")
)
```

**in_app_notification 表（SDD §13.1 兜底渠道）**

| 列 | 类型 | 约束 | 说明 |
|----|------|------|------|
| id | BigInteger | PK autoincrement | |
| notify_type | String(32) | NOT NULL | SIGNAL_BUY / SIGNAL_SELL / MARKET_STATE / STOP_LOSS_WARN / RISK_WARN / FACTOR_ALERT / PIPELINE_FAILURE（实现取 String(32) 留扩展冗余，v1.2 评审 C-09） |
| title | String(200) | NOT NULL | 推送标题 |
| body | Text | NOT NULL | 正文（SDD §13.3 格式） |
| payload | JSONB | nullable | 关联实体（如 signal_id / ts_code） |
| wx_pushed | Boolean | NOT NULL default False | 微信是否推送成功 |
| wx_error | Text | nullable | 微信失败原因（用于 UI 展示"微信推送失败"提示） |
| read_at | TIMESTAMP(tz) | nullable | 用户已读时间 |
| created_at | TIMESTAMP(tz) | server_default=NOW() | |

索引：`idx_notify_unread ON (created_at DESC) WHERE read_at IS NULL`

### 2.2 已有表扩展

- `user_config` 无 schema 变更（仅新增 config_key 行，由用户按需写入）
- `pipeline_run` 新增 `config_snapshot` 列（见 2.1）

### 2.3 ConfigService 分组映射

V1.0 共 **12 个 config_key**（按消费方分组，减少表行数；v1.1 评审补入 `factor_monitor_params`）：

| config_key | dataclass | 消费方 | 字段（默认值来自 SDD 附录 B） |
|-----------|-----------|-------|----------------------------|
| `signal_params` | `SignalConfig` | SignalGenerator | buy_threshold=80 / sell_threshold=40 / strong_threshold=90 / stop_loss_pct=0.08 / add_cost_deviation_pct=0.10 / price_low_mult=0.99 / price_high_mult=1.02 |
| `risk_limits` | `RiskLimitsConfig` | RiskChecker + PositionSizer | max_single_stock_pct=0.20 / max_industry_pct=0.30 / max_total_position_pct=0.80 / single_trade_pct=0.10 |
| `market_state_params` | `MarketStateConfig` | MarketStateEngine | ma_short=20 / ma_long=60 / adx_period=14 / adx_threshold=25 / debounce_days=3 |
| `universe_params` | `UniverseConfig` | UniverseFilter + Pool | min_liquidity_amount=5_000_000 / new_stock_days=60 / pool_capacity=20 / signal_expiry_days=3 |
| `strategy_weights` | `StrategyWeightsConfig` | Scorer | 三态 3×4 矩阵（详见 §4.2）；**子键必须与 `BaseStrategy.name` 逐字一致：`trend / momentum / mean_reversion / value`**（v1.1 评审 Q-6：原草案误写 `reversion`，与 `engine/strategies/mean_reversion.py::name="mean_reversion"` 失配会导致 Scorer 取不到权重回退默认） |
| `strategy_params_trend` | `TrendStrategyConfig` | TrendStrategy | ma_short=20 / ma_long=60 / macd_fast=12 / macd_slow=26 / macd_signal=9 |
| `strategy_params_momentum` | `MomentumStrategyConfig` | MomentumStrategy | lookback_short=60 / lookback_long=120 / reversal_exclude_pct=0.05 |
| `strategy_params_mean_reversion` | `MeanReversionStrategyConfig` | MeanReversionStrategy | rsi_period=14 / rsi_oversold=30 / bbands_period=20 / bbands_std=2.0（v1.1 评审 Q-6：键名与策略 `name="mean_reversion"` 对齐，原草案 `strategy_params_reversion` 更正） |
| `strategy_params_value` | `ValueStrategyConfig` | ValueStrategy | pe_pb_history_years=5 |
| `backtest_defaults` | `BacktestDefaultsConfig` | `POST /backtest/run` 端点层 partial-overlay（详见 §4.4） | commission_rate=0.00025 / stamp_tax_rate=0.0005 / slippage_rate=0.001（v1.1 评审 Q-7：`slippage_rate` 在 SDD 附录 B 未列，按 A 股散户滑点经验值 0.1% 设定；待 SDD v1.x 补录） |
| `notification_prefs` | `NotificationConfig` | NotificationService | wx_enabled=True / push_start_hour=15 / push_end_hour=22 / **6 项事件开关**：notify_signal_buy / notify_signal_sell / notify_market_state / notify_stop_loss_warn / notify_risk_warn / notify_factor_alert（v1.1 评审 Q-3：SDD §14.4 原 4 项 `信号生成/止损触发/管道失败/月报完成` 已同步扩至 6 项——新增"市场状态变化"和"因子告警"对齐 SDD §13.2 五类触发；SDD §14.4 v1.x 需同步更新） |
| `factor_monitor_params` | `FactorMonitorConfig` | FactorMonitorService | ic_window=20 / ic_alert_threshold=0.02 / half_life_window=60（v1.1 评审 G-3 新增：SDD 附录 B 列出"IC 下期收益窗口=20 交易日"但无配置入口，本次补齐） |
| `risk_free_rate` | `{value: 0.03}` | PerformanceService（Phase 6 已存在） | 保持不变 |

> **策略参数说明**：SDD 列出了 RSI/BBands/动量回看期等策略内因子参数。V1.0 打通读取，但 UI 显式提示"修改后从下次 Pipeline 生效；历史因子快照不回溯"（避免用户误解）。

---

## 3. ConfigService 设计

### 3.1 架构约束

- **IO 归 Service 层**：ConfigService 是 Service 层组件，持有 `AsyncSession` + `aioredis.Redis`
- **Engine 层纯函数**：Engine 接收 dataclass 参数，不依赖 ConfigService（符合 CLAUDE.md §6 Engine 层无 IO 规则）
- **缓存策略**：Redis key `config:{config_key}`，TTL 300 秒；PUT `/settings` 后主动失效（`DEL config:{config_key}`）
- **默认值回退**：DB 未命中 → 返回 `core/config_defaults.py` 常量；**DB 行可部分覆盖**（例如 `signal_params` 只存 `{buy_threshold: 85}`，其他字段仍用默认）

### 3.2 接口

```python
# services/config_service.py
from dataclasses import dataclass, asdict, replace
from quantpilot.core.config_defaults import (
    DEFAULT_SIGNAL_CONFIG, DEFAULT_RISK_LIMITS, DEFAULT_MARKET_STATE,
    DEFAULT_UNIVERSE, DEFAULT_STRATEGY_WEIGHTS, DEFAULT_NOTIFICATION,
    DEFAULT_BACKTEST_DEFAULTS, ...
)

class ConfigService:
    def __init__(self, session: AsyncSession, redis: Redis | None = None) -> None:
        self._session = session
        self._redis = redis

    async def get_signal_params(self) -> SignalConfig:
        return await self._get_typed("signal_params", SignalConfig, DEFAULT_SIGNAL_CONFIG)

    async def get_risk_limits(self) -> RiskLimitsConfig:
        return await self._get_typed("risk_limits", RiskLimitsConfig, DEFAULT_RISK_LIMITS)

    # ... 同理 11 个 get_*()

    async def _get_typed[T](self, key: str, cls: type[T], default: T) -> T:
        """读 DB + 部分覆盖默认值 + Redis 缓存"""
        cached = await self._cache_get(key)
        if cached is not None:
            return cls(**{**asdict(default), **cached})
        row = await self._session.execute(
            select(UserConfig.config_value).where(UserConfig.config_key == key)
        )
        db_value = row.scalar_one_or_none() or {}
        merged = {**asdict(default), **db_value}
        await self._cache_set(key, db_value)
        return cls(**merged)

    async def invalidate(self, key: str) -> None:
        """PUT /settings 成功后调用"""
        if self._redis:
            await self._redis.delete(f"config:{key}")
```

### 3.3 SettingsService 集成

`SettingsService.upsert_setting()` 成功后调用 `config_service.invalidate(config_key)`，确保下次读取获取最新值。

### 3.4 依赖注入

```python
# api/deps.py 新增
async def get_config_service(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ConfigService:
    return ConfigService(db, redis)
```

---

## 4. UserConfig 消费改造

### 4.1 Engine 层改造（不变签名风格）

**原** Engine 构造/方法签名暴露扁平参数：

```python
class MarketStateEngine:
    def __init__(self, ma_short: int = 20, ma_long: int = 60, ...): ...
```

**改造后** 接收 dataclass（不引入 IO）：

```python
class MarketStateEngine:
    def __init__(self, config: MarketStateConfig = MarketStateConfig()): ...
```

Service 层调用方：`engine = MarketStateEngine(await config_service.get_market_state_params())`

同理改造：
- `SignalGenerator(signal_cfg: SignalConfig, risk_limits: RiskLimitsConfig)`
- `RiskChecker(risk_limits: RiskLimitsConfig, account_max_drawdown_pct=...)`
- `CandidatePoolManager(universe_cfg: UniverseConfig)`
- `UniverseFilter`（filter 方法参数改为 dataclass）
- `TrendStrategy` / `MomentumStrategy` / `ReversionStrategy` / `ValueStrategy`（`__init__` 接收各自 dataclass）

### 4.2 Scorer 三态权重矩阵接入

**原**：`scorer.py` 硬编码 `WEIGHTS`

**改造**：

```python
# engine/scorer.py
@dataclass(frozen=True)
class StrategyWeightsConfig:
    uptrend: dict[str, float]      # {trend, momentum, mean_reversion, value}
    downtrend: dict[str, float]
    oscillation: dict[str, float]

DEFAULT_STRATEGY_WEIGHTS = StrategyWeightsConfig(
    uptrend={"trend": 0.40, "momentum": 0.25, "mean_reversion": 0.15, "value": 0.20},
    downtrend={"trend": 0.10, "momentum": 0.05, "mean_reversion": 0.15, "value": 0.70},
    oscillation={"trend": 0.15, "momentum": 0.15, "mean_reversion": 0.40, "value": 0.30},
)

class Scorer:
    def __init__(self, weights: StrategyWeightsConfig = DEFAULT_STRATEGY_WEIGHTS): ...

    def aggregate(self, market_state, strategy_scores):
        matrix = {
            MarketStateEnum.UPTREND: self._weights.uptrend,
            MarketStateEnum.DOWNTREND: self._weights.downtrend,
            MarketStateEnum.OSCILLATION: self._weights.oscillation,
        }
        base_weights = matrix[market_state]
        # ... 原逻辑
```

用户在 Settings 配置的 `strategy_weights` config_value：

```json
{
  "uptrend":    {"trend": 0.45, "momentum": 0.25, "mean_reversion": 0.10, "value": 0.20},
  "downtrend":  {"trend": 0.10, "momentum": 0.05, "mean_reversion": 0.15, "value": 0.70},
  "oscillation":{"trend": 0.15, "momentum": 0.15, "mean_reversion": 0.40, "value": 0.30}
}
```

Settings 前端对此 key 提供**每状态的 4 字段滑块 + 合计 = 1.0 校验**。

### 4.3 Pipeline 配置快照

**写入时机（v1.1 评审 Q-5 收敛）**：采用 **"启动时一次性 snapshot"** 语义，而非逐 CP 读取。

- **触发点**：`DailyPipeline.run_for_date` 入口（无论是 `_daily_job` APScheduler 触发还是 `POST /pipeline/trigger` 手动触发），在 CP1 首步调用 `ConfigService.get_all_for_snapshot()` 返回完整 dict，一次性写入 `pipeline_run.config_snapshot` 并 commit。
- **快照内数据源**：`ConfigService.get_all_for_snapshot()` 内部调用全部 12 个 `get_*()` 并组装为 dict（每项 dataclass 经 `asdict()` 转普通 dict）。
- **后续 CP 行为**：CP1/CP2/CP3/Step4/5/6 **禁止** 再次访问 `ConfigService`；所有 Engine 实例构造时使用快照反序列化的 dataclass。
- **好处**：可完整回放（重跑同一 `pipeline_run_id` 保证参数一致）；避免用户运行期间修改 Settings 导致下游 CP 使用新参数而上游用旧参数造成的不一致。

```python
# pipeline/daily_pipeline.py::DailyPipeline.run_for_date
async def run_for_date(self, trade_date: date) -> None:
    async with self._session_factory() as session:
        run = await self._create_pipeline_run(session, trade_date)
        # CP1 首步：一次性读配置 + 写快照
        snapshot = await self._cfg.get_all_for_snapshot()  # dict[config_key, asdict(dataclass)]
        run.config_snapshot = snapshot
        await session.commit()

    # 从快照反序列化 dataclass，贯穿整个 Pipeline
    signal_cfg = SignalConfig(**snapshot["signal_params"])
    risk_limits = RiskLimitsConfig(**snapshot["risk_limits"])
    market_cfg = MarketStateConfig(**snapshot["market_state_params"])
    # ... 后续 CP 使用这些 dataclass 实例构造 Engine，不再访问 ConfigService
```

**ConfigService 新增方法**：

```python
class ConfigService:
    async def get_all_for_snapshot(self) -> dict[str, dict]:
        """Pipeline 启动入口使用：一次性读 12 个 config_key，返回可 JSONB 序列化的 dict。"""
        return {
            "signal_params":        asdict(await self.get_signal_params()),
            "risk_limits":          asdict(await self.get_risk_limits()),
            "market_state_params":  asdict(await self.get_market_state_params()),
            "universe_params":      asdict(await self.get_universe_params()),
            "strategy_weights":     asdict(await self.get_strategy_weights()),
            "strategy_params_trend":           asdict(await self.get_strategy_params_trend()),
            "strategy_params_momentum":        asdict(await self.get_strategy_params_momentum()),
            "strategy_params_mean_reversion":  asdict(await self.get_strategy_params_mean_reversion()),
            "strategy_params_value":           asdict(await self.get_strategy_params_value()),
            "factor_monitor_params": asdict(await self.get_factor_monitor_params()),
            "notification_prefs":    asdict(await self.get_notification_prefs()),
            "_snapshot_at":          datetime.now(timezone.utc).isoformat(),
        }
```

> **注**：`backtest_defaults` 和 `risk_free_rate` 不写入 `pipeline_run.config_snapshot`（前者用于回测端点 partial-overlay，后者用于绩效计算，均不属于 Pipeline 运行时参数）。

### 4.4 BacktestEngine / BacktestService UserConfig 接入（v1.1 评审 Q-2 补强）

**架构决策**：partial-overlay 合并发生在 **`POST /backtest/run` 端点层**，BacktestEngine 内部不访问 UserConfig（继续保持 Engine 层无 IO 规则，CLAUDE.md §6）。

**分工**：

| 组件 | 职责 | UserConfig 依赖 |
|------|------|----------------|
| `POST /backtest/run` 路由 | 解析请求体；对未提供字段调用 `config_service.get_backtest_defaults()` 填充；将**合并后的完整参数**传入 BacktestService | 是（端点层） |
| `BacktestService.start_task` | 创建 backtest_task，传入已合并参数；记录 `backtest_task.config_snapshot`（JSONB） | 否 |
| `BacktestEngine.run` | 接收 `BacktestDataBundle` + 明确的参数（commission/stamp_tax/slippage/initial_cash）；不读 UserConfig；回测期间还需要 Scorer/SignalGenerator/RiskChecker 等消费端的参数 | 否 |

**回测内部 Scorer 等参数来源**：`POST /backtest/run` 端点层除合并 `backtest_defaults` 外，还需调用 `config_service.get_all_for_snapshot()` 获取当前 Scorer/SignalGenerator/RiskChecker 等完整参数快照，随 `BacktestService.start_task` 一并传入；BacktestEngine 用这组快照构造各 Engine 实例。此快照写入 `backtest_task.config_snapshot` 作为**该次回测的参数标识**（与 `pipeline_run.config_snapshot` 同构），支持结果可复现。

**前端表单预填**：`BacktestTab.vue` `onMounted` 时从 `GET /settings` 取出 `backtest_defaults` 的当前值预填到表单（用户可逐项修改）；未修改字段提交时不发送，由后端端点层覆盖默认值。

**迁移影响**：`backtest_task` 表**已有 `config_snapshot` 列**（Phase 8 设计文档 §3 已规划）；若 Phase 8 实现时未建该列，Phase 10 迁移 0007 需同步补建。实施时须 grep 现有 ORM 确认。

```python
# api/v1/backtest.py（伪代码）
@router.post("/run")
async def run_backtest(
    req: BacktestRunRequest,
    cfg: ConfigService = Depends(get_config_service),
    svc: BacktestService = Depends(get_backtest_service),
) -> BacktestRunResponse:
    defaults = await cfg.get_backtest_defaults()
    merged = BacktestParams(
        start_date=req.start_date, end_date=req.end_date, initial_cash=req.initial_cash,
        commission_rate=req.commission_rate if req.commission_rate is not None else defaults.commission_rate,
        stamp_tax_rate=req.stamp_tax_rate if req.stamp_tax_rate is not None else defaults.stamp_tax_rate,
        slippage_rate=req.slippage_rate  if req.slippage_rate  is not None else defaults.slippage_rate,
    )
    engine_snapshot = await cfg.get_all_for_snapshot()
    task = await svc.start_task(merged, engine_snapshot)
    return BacktestRunResponse(task_id=task.task_id, status=task.status)
```

---

## 5. 通知与提醒（P10-A）

### 5.1 NotificationChannel ABC + WxPusher 适配器

**路径调整（v1.1 评审 Q-1）**：`data/adapters/` 专职 `DataSourceAdapter`（数据采集语义）；通知渠道不属于该语义。Phase 10 新建 `backend/src/quantpilot/notification/` 独立目录，对齐 system_design §5.10。

```python
# notification/base.py（新增）
from abc import ABC, abstractmethod

class NotificationChannel(ABC):
    """SDD §13.1 通知渠道抽象；V1.0 仅 WxPusher + InApp，V1.5 可扩 ServerChan/Email/Slack。"""

    @abstractmethod
    async def send(self, title: str, body: str) -> bool:
        ...

# notification/wxpusher.py（新增）
class WxPusherAdapter(NotificationChannel):
    def __init__(self, app_token: str, uid: str, timeout: float = 10.0): ...

    async def send(self, title: str, body: str) -> bool:
        """3 次重试，间隔 30 秒；全部失败返回 False（SDD §13.1）"""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        "https://wxpusher.zjiecode.com/api/send/message",
                        json={"appToken": self._app_token, "content": body,
                              "summary": title[:20], "contentType": 1, "uids": [self._uid]},
                    )
                    if resp.status_code == 200 and resp.json().get("code") == 1000:
                        return True
            except Exception as e:
                logger.warning("wxpusher attempt %d/3 failed: %s", attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(30)
        return False
```

环境变量：`WXPUSHER_APP_TOKEN`、`WXPUSHER_UID`；缺失时 `WxPusherAdapter` 实例化时直接 `logger.warning("WxPusher 未配置，通知将仅走系统内")` 并在 `send()` 返回 False（降级为系统内通知）。

### 5.2 NotificationService 真实实现

```python
# services/notification_service.py（重写）
class NotificationService:
    def __init__(self, session, config_service, wxpusher: WxPusherAdapter | None): ...

    async def notify(self, notify_type: str, title: str, body: str,
                     payload: dict | None = None) -> None:
        """统一入口：写 in_app_notification + 尝试微信推送 + 开关/时段过滤"""
        prefs = await self._cfg.get_notification_prefs()
        if not self._is_enabled(prefs, notify_type):
            return
        # 1. 系统内通知（兜底，始终写入）
        notif = InAppNotification(notify_type=notify_type, title=title, body=body, payload=payload)
        self._session.add(notif)
        await self._session.flush()
        # 2. 微信推送（可选）
        if prefs.wx_enabled and self._wx and self._in_push_window(prefs):
            ok = await self._wx.send(title, body)
            notif.wx_pushed = ok
            if not ok:
                notif.wx_error = "WxPusher 重试 3 次均失败，已降级为系统内通知"
                # v1.1 评审 Q-4：链路级降级必须 ERROR（对齐 CLAUDE.md §6 静默降级禁止）
                logger.error(
                    "notification degraded to in-app: type=%s uid=%s title=%s",
                    notify_type, self._wx.uid, title,
                )
        try:
            await self._session.commit()
        except Exception:
            # v1.1 评审 Q-4：兜底失败属极端降级，必须 ERROR + re-raise 给上层（上层记 best-effort）
            logger.exception("in_app_notification write failed: type=%s title=%s", notify_type, title)
            raise

    async def notify_signal(self, signal: Signal) -> None:
        title, body = self._render_signal_template(signal)  # SDD §13.3
        notify_type = "SIGNAL_BUY" if signal.signal_type == "BUY" else "SIGNAL_SELL"
        await self.notify(notify_type, title, body, {"signal_id": signal.id})

    async def notify_market_state_change(self, old, new) -> None: ...
    async def notify_stop_loss_warn(self, position, distance_pct) -> None: ...
    async def notify_risk_warn(self, event) -> None: ...
    async def notify_factor_alert(self, strategy, factor, ic_mean) -> None: ...
```

### 5.3 消息模板（SDD §13.3）

买入信号模板（严格按 SDD §13.3）：

```
【QuantPilot 买入信号】
标的：{name}({ts_code})
评分：{score}/100（{strength}）
理由：{reason}
建议：买入价区间 {price_low}-{price_high} 元
仓位：总资产的 {suggested_pct}%（约 {amount} 元）
止损：{stop_loss} 元（-{stop_loss_pct}%）
⚠️ 提醒：A股T+1，买入当日不可卖出
```

其他四类（卖出 / 止损预警 / 风险告警 / 市场状态变化 / 因子告警）模板在 `services/notification_service.py` 的 `_render_*` 方法中定义。

**日志级别约定（v1.1 评审 Q-4）**：

| 场景 | 日志级别 | 说明 |
|------|---------|------|
| 单次 WxPusher 请求失败（网络 / HTTP 非 200 / `code != 1000`） | **WARN** | `WxPusherAdapter.send` 内，附 attempt 编号 |
| 3 次重试全部失败并降级到 InAppNotification 成功 | **ERROR** | `NotificationService.notify` 内，附 notify_type / uid / title |
| InAppNotification 写库失败（极端降级） | **ERROR** + re-raise | `logger.exception` 打印堆栈；上层 Pipeline/Service 按 best-effort 处理（不回滚业务事务） |
| WxPusher 未配置（环境变量缺失） | **WARN** | 启动期 `WxPusherAdapter.__init__` 内打印一次，避免每次推送刷屏 |

### 5.4 触发规则（SDD §13.2）

| 事件 | 触发点 | 去重策略 |
|------|--------|---------|
| 新买入信号 | `DailyPipeline.CP3` 写入后，对每条 `NEW` 信号调用 | 同一 (ts_code, signal_type, trade_date) 不重复 |
| 新卖出信号 | 同上 | 同上 |
| 市场状态变化 | `MarketStateService.identify_and_save` 检测到 `state_changed=True` | 同一日状态变更仅一次 |
| 止损预警 | 每日 15:05 独立 Job 扫描所有持仓 | 同一 ts_code 同一日仅一次 |
| 风险告警 | `RiskChecker` 生成 WARN 级别告警时 | 同一告警事件同一日仅一次 |
| 因子告警 | `FactorMonitorService.run_monthly` 检测到 `ic_mean_3m < 0` 或 `|ic| < 0.02` | 按月去重 |

去重实现：查询 `in_app_notification` 表最近 1 天内是否已有同类型 payload。

### 5.5 止损预警 Job

`pipeline/scheduler.py` 新增：

```python
async def _stop_loss_warn_job():
    """每日 15:05 扫描所有持仓，计算距止损价距离 ≤ 2% 的推送"""
    async with AsyncSessionLocal() as session:
        positions = await account_service.get_all_positions()
        for p in positions:
            sig = await signal_service.get_last_buy_signal(p.ts_code)
            if sig and sig.stop_loss_price and p.current_price:
                distance_pct = (p.current_price - sig.stop_loss_price) / p.current_price
                if 0 < distance_pct <= 0.02:
                    await notifier.notify_stop_loss_warn(p, distance_pct)
```

APScheduler 配置：`CronTrigger(hour=15, minute=5)`（A 股收盘 15:00 后 5 分钟）。

### 5.6 InAppNotification API

新增端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/notifications` | 分页列出未读/已读通知 |
| POST | `/api/v1/notifications/{id}/read` | 标记已读 |
| POST | `/api/v1/notifications/read-all` | 全部已读 |
| GET | `/api/v1/notifications/unread-count` | 未读数量（前端导航栏 Badge） |

---

## 6. Settings 前端完整化（P10-C）

### 6.1 三段折叠设计

```
Settings 页
├─ Tab 1：参数配置
│   ├─ [基础]（默认展开）
│   │   ├─ 信号阈值（signal_params：buy/sell/strong + 止损）
│   │   ├─ 仓位与风控（risk_limits + 单笔仓位）
│   │   ├─ 股票池（universe_params：池容量 + 信号有效期）
│   │   └─ 绩效（risk_free_rate）
│   ├─ [高级]（默认折叠）
│   │   ├─ 市场状态（market_state_params：MA 周期）
│   │   ├─ 策略权重矩阵（strategy_weights：三态 3×4）
│   │   ├─ 策略参数（strategy_params_trend/momentum/mean_reversion/value）
│   │   └─ 回测成本默认（backtest_defaults）
│   └─ [专家]（默认折叠）
│       ├─ ADX 阈值 / 状态切换确认天数（market_state_params）
│       ├─ 动量反转剔除阈值 / PE-PB 历史窗口
│       ├─ 流动性阈值 / 次新股排除天数（universe_params）
│       └─ 因子监控（factor_monitor_params：IC 窗口 / 告警阈值 / 半衰期窗口）
├─ Tab 2：提醒设置（notification_prefs）
├─ Tab 3：黑白名单（整合 /watchlist API）
├─ Tab 4：变更历史（已有）
└─ Tab 5：导入/导出（YAML）
```

### 6.2 参数目录详表（前端 `CONFIG_CATALOG`）

每项结构：

```typescript
interface ConfigField {
  key: string
  label: string          // 中文名
  type: 'percent' | 'integer' | 'number' | 'boolean' | 'hour'
  default: unknown
  min?: number; max?: number; step?: number
  unit?: string          // 显示单位（如 "日"、"元"、"%"）
  help?: string          // SDD 原文解释
  impact: 'immediate' | 'next_pipeline'  // 生效时机
  sddRef?: string        // SDD 章节号
  tier: 'basic' | 'advanced' | 'expert'
}

interface ConfigDefinition {
  config_key: string
  title: string
  description: string
  group: string
  consumer: string       // "用于：XX 模块"
  tier: 'basic' | 'advanced' | 'expert'
  fields: ConfigField[]
}
```

**完整目录 12 个 config_key**（字段数 + 默认值严格对齐 SDD 附录 B；v1.1 评审补入 `factor_monitor_params`），编号 CAT-01~12 与 §2.3 映射。

### 6.3 UI 提示原则

- **每字段右侧**显示 SDD 默认值：`默认：80 分`
- **每字段下方**显示影响范围：`⚡ 立即生效` / `⏱ 下次 Pipeline 生效`
- **每 config_key 头部**显示当前状态：`已自定义` / `使用默认值` / `未保存`
- **策略内因子参数**显式警告：`⚠ 修改后从下次 Pipeline 生效；历史因子快照不回溯`
- **"对比 SDD 默认值"** 按钮：弹窗列出所有偏离 SDD 的项
- **"恢复此项默认"** 按钮：每项右上角
- **"全部恢复 SDD 默认"** 按钮：页面底部（带二次确认）

### 6.4 提醒设置 Tab

```
[ ] 开启微信推送             （wx_enabled）
推送时段：[15] 时 — [22] 时  （push_start_hour/push_end_hour）

事件开关：
[ ] 买入信号
[ ] 卖出信号
[ ] 市场状态变化
[ ] 止损预警
[ ] 风险告警
[ ] 因子告警
```

微信未绑定时显示"未配置 WXPUSHER_APP_TOKEN，将仅使用系统内通知"（读 `GET /api/v1/notifications/wx-status`，判断环境变量）。

### 6.5 黑白名单 Tab

复用 `/api/v1/watchlist` 已有端点，在 Settings 内提供：
- 添加股票（代码搜索 + 下拉）
- 列表查看（白/黑分别 Table）
- 批量删除
- CSV 导入

### 6.6 首次启动向导

路由：`/onboarding`，首次登录且 `GET /api/v1/setup/status` 返回 `completed: false` 时自动跳转。

步骤：

1. **欢迎页** — 简介 + 免责声明（SDD §7.7.4）
2. **Tushare Token** — 输入框 + "跳过（使用演示数据）"
3. **初始数据拉取** — 进度条（调用 `/api/v1/data/ingest/stock_info` + `/data/ingest/quotes`）
4. **账户初始资金** — 金额输入 + 默认 100,000 元（SDD §14.1）
5. **参数默认** — 选择"接受 SDD 默认"（推荐）或"进入自定义"
6. **完成** — POST `/api/v1/setup/complete` 标记已完成

### 6.7 EmptyState 全页覆盖

为所有页面空状态提供引导文案：

| 页面 | 空状态场景 | 文案 |
|------|----------|------|
| Dashboard | 无信号 | "暂无新信号。请先完成初始数据采集 → 点击运行 Pipeline" |
| Signals | 无信号 | 同上 |
| Positions | 无持仓 | "请录入首笔交易，或从信号页'录入交易'一键填充" |
| Performance | 无 NAV | "请先运行 Pipeline 生成每日净值数据" |
| Reports | 无报告 | "周报每周日 20:00 自动生成，或点击'手动生成'" |
| FactorQuality | 无 IC | "因子 IC 每月末计算。首次运行 FactorMonitorService 后可查看" |
| Backtest | 无任务 | "点击'新建回测'开始第一次策略回测" |

### 6.8 术语悬浮提示

为信号详情/评分明细页添加 `<a-tooltip>`：
- 夏普比率、最大回撤、胜率、盈亏比、IC、IR、MA、ADX、PE-TTM、PB、Rank IC
- 悬浮解释引自 SDD §2.4 和附录 A。

### 6.9 YAML 导出/导入

新增端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/settings/export` | 导出所有 user_config 为 YAML |
| POST | `/api/v1/settings/import` | 上传 YAML，执行批量 upsert（带差异预览） |

格式：

```yaml
# QuantPilot 配置导出  生成时间: 2026-04-20T10:00:00+08:00
signal_params:
  buy_threshold: 85
  sell_threshold: 40
risk_limits:
  max_single_stock_pct: 0.20
notification_prefs:
  wx_enabled: true
  push_start_hour: 15
```

---

## 7. 收尾遗留（P10-D）

### 7.1 SignalService.generate_for_date 完整化

当前 `services/signal_service.py:260` 降级：仅把 CandidatePool 快照转成 Signal，未调用 PositionSizer/RiskChecker。

改造：

```python
async def generate_for_date(self, trade_date: date) -> list[Signal]:
    composite = await self._get_composite_scores(trade_date)
    positions = await self._account_svc.get_all_positions()
    market_state = await self._get_market_state(trade_date)
    snapshot_quotes = await self._get_snapshot_quotes(trade_date)

    signal_cfg = await self._cfg.get_signal_params()
    risk_limits = await self._cfg.get_risk_limits()

    gen = SignalGenerator(signal_cfg)
    trade_signals = gen.generate(composite, positions, market_state, snapshot_quotes, trade_date)

    sizer = PositionSizer(risk_limits)
    sized = sizer.suggest(trade_signals, positions, market_state)

    checker = RiskChecker(risk_limits)
    checked = [s for s in sized if not checker.blocks(s, positions)]

    return await self._persist(checked)
```

### 7.2 DailyPipeline notifier 真实化

```python
# pipeline/daily_pipeline.py
notifier = NotificationService(session, cfg, wxpusher)
for sig in signals:
    await notifier.notify_signal(sig)
```

同时：市场状态变更 → `notifier.notify_market_state_change`；风险告警 → `notifier.notify_risk_warn`。

### 7.3 FactorMonitorService 告警接入

```python
# services/factor_monitor_service.py
for alert in alerts:
    await self._notifier.notify_factor_alert(
        strategy=alert.strategy, factor=alert.factor, ic_mean=alert.ic_mean_3m,
    )
```

---

## 8. 部署与运维（P10-E）

### 8.1 生产 Docker Compose

新增 `docker-compose.prod.yml`：

```yaml
services:
  nginx:
    image: nginx:1.25-alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx/nginx.prod.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
      - frontend_dist:/usr/share/nginx/html:ro
    depends_on: [backend]

  backend:
    build: { context: ./backend, dockerfile: Dockerfile.prod }
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: ${REDIS_URL}
      TUSHARE_TOKEN: ${TUSHARE_TOKEN}
      WXPUSHER_APP_TOKEN: ${WXPUSHER_APP_TOKEN}
      WXPUSHER_UID: ${WXPUSHER_UID}
      # 见 8.2 完整清单
    depends_on: { db: { condition: service_healthy }, redis: { condition: service_started } }
    restart: unless-stopped

  frontend-builder:
    build: { context: ./frontend, dockerfile: Dockerfile.build }
    volumes: [frontend_dist:/output]

  db:
    image: postgres:15-alpine
    volumes: ["./data/pgdata:/var/lib/postgresql/data", "./scripts:/scripts:ro"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER"], interval: 10s }
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    volumes: ["./data/redis:/data"]
    restart: unless-stopped

volumes:
  frontend_dist:
```

`nginx/nginx.prod.conf`：前端静态 + `/api/*` 反代 `backend:8000` + `/ws/*` WebSocket 升级 + gzip + SSL（Let's Encrypt）。

**V1.0 整改 Batch 2 — B2-5：** `nginx.prod.conf` 头部加红色警示注释块（"公网部署必须启用 HTTPS，未启用 HTTPS 时禁止将服务暴露至公网"），与 `docs/guides/deployment.md §2`「公网部署 HTTPS 强制要求」小节呼应；后者列出未启用 HTTPS 时 JWT 公网明文传输导致账户劫持的风险。内网 NAS 部署可保持默认 HTTP（仍建议启用）。

### 8.2 环境变量清单（.env.prod.example）

```env
# 数据库
DATABASE_URL=postgresql+asyncpg://quantpilot:STRONG_PASSWORD@db:5432/quantpilot
POSTGRES_USER=quantpilot
POSTGRES_PASSWORD=STRONG_PASSWORD
POSTGRES_DB=quantpilot

# Redis
REDIS_URL=redis://redis:6379/0

# JWT
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH='$2b$12$...'
JWT_SECRET_KEY=<64字符随机>
JWT_ALGORITHM=HS256

# 数据源
TUSHARE_TOKEN=<你的 Tushare Pro Token>

# 微信推送（可选，未配置时自动降级系统内通知）
WXPUSHER_APP_TOKEN=<WxPusher APP Token>
WXPUSHER_UID=<接收方 UID>

# 应用
DEBUG=false
CORS_ORIGINS=["https://your-domain.com"]
```

### 8.3 DB 备份/恢复

`scripts/backup_db.sh`：

```bash
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} | gzip > backups/qp_${DATE}.sql.gz
find backups/ -name "qp_*.sql.gz" -mtime +30 -delete
```

`scripts/restore_db.sh`：接收备份文件路径参数，执行 `gunzip -c | psql`。

Cron：`0 2 * * * /path/to/backup_db.sh`（每日 02:00）。

### 8.4 日志滚动（SDD §15.5）

`core/logging_config.py` 新增：

```python
from logging.handlers import RotatingFileHandler

def setup_logging():
    handler = RotatingFileHandler(
        "logs/quantpilot.log", maxBytes=50 * 1024 * 1024, backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(JSONFormatter())  # 结构化 JSON 日志（SDD §15.5）
    logging.getLogger().addHandler(handler)
```

关键业务事件 **ERROR/WARNING/INFO 级别**：信号生成、检查点、因子告警、WxPusher 失败、异常。

### 8.5 部署文档（docs/guides/deployment.md）

章节：
1. 前置要求（Docker 20.10+ / Docker Compose v2 / 2 核 2G 最低）
2. 域名与 SSL（certbot 申请 / 续期）
3. 环境变量准备（`.env.prod` 模板）
4. 首次部署步骤（`docker compose -f docker-compose.prod.yml up -d` → `alembic upgrade head` → 打开首次启动向导）
5. 数据备份与恢复
6. 日志查看（`docker compose logs -f backend`）
7. 监控建议（Prometheus + Grafana，V1.5 规划）
8. 故障排查（含 Tushare Token 失效、WxPusher 接口变更等常见问题）

### 8.6 生产冒烟脚本

`scripts/prod_smoke.sh`：HTTP 探活 + 登录 + 关键 API 返回 200 + WxPusher 测试推送（可选）+ 退出码 0/非 0。部署流水线调用。

---

## 9. API 端点清单（Phase 10 新增）

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET | `/api/v1/notifications` | 通知列表（分页） | ✓ |
| GET | `/api/v1/notifications/unread-count` | 未读数量 | ✓ |
| POST | `/api/v1/notifications/{id}/read` | 标记已读 | ✓ |
| POST | `/api/v1/notifications/read-all` | 全部已读 | ✓ |
| GET | `/api/v1/notifications/wx-status` | 微信推送是否可用 | ✓ |
| GET | `/api/v1/setup/status` | 首次启动向导完成状态 | ✓ |
| POST | `/api/v1/setup/complete` | 标记向导完成 | ✓ |
| GET | `/api/v1/settings/export` | YAML 导出 | ✓ |
| POST | `/api/v1/settings/import` | YAML 导入（预览 + 应用） | ✓ |

---

## 10. TDD 测试策略

### 10.1 单元测试（tests/unit/）

| 编号 | 文件 | 覆盖 |
|------|------|------|
| INV-CFG-01~04 | `test_config_service.py` | 默认值回退 / 部分覆盖 / Redis 缓存命中 / invalidate |
| INV-WX-01~03 | `test_wxpusher_adapter.py` | 成功 / 重试 / 全部失败降级 |
| INV-NTF-01~04 | `test_notification_service.py` | 开关过滤 / 时段过滤 / 去重 / 兜底写 in_app_notification |
| INV-SCR-W-01 | `test_scorer.py` 扩展 | 自定义 strategy_weights 矩阵参与计算 |

### 10.2 E2E 测试（tests/e2e/）

| 编号 | 文件 | 覆盖 |
|------|------|------|
| E2E-NTF-01~04 | `test_notifications_api.py` | 列表 / 未读数 / 标记已读 / wx-status |
| E2E-SETUP-01~02 | `test_setup_api.py` | 向导状态 / 完成 |
| E2E-CFG-01~03 | `test_config_export_api.py` | 导出 YAML / 导入预览 / 导入应用 |

### 10.3 集成测试（tests/integration/）

| 编号 | 文件 | 覆盖 |
|------|------|------|
| INT-CFG-01 | `test_int_config_service.py` | DB 写入 + Redis 失效 + 下次读取获取最新值 |
| INT-CFG-02 | `test_int_config_consumption.py` | 修改 `signal_params.buy_threshold` → DailyPipeline 重跑 → 信号数变化 |
| INT-CFG-03 | 同上 | `pipeline_run.config_snapshot` 正确记录运行时参数 |
| INT-NTF-01 | `test_int_notification_flow.py` | 信号生成后 in_app_notification 入库 |
| INT-SIG-GEN-01 | `test_int_signal_generate_for_date.py` | `generate_for_date` 完整链路（含 PositionSizer/RiskChecker） |

### 10.4 前端测试（frontend/src/__tests__/）

| 编号 | 文件 | 覆盖 |
|------|------|------|
| FE-SET-01~03 | `SettingsView.spec.ts` | 三段折叠 / 恢复默认 / 导入导出 |
| FE-ONB-01 | `OnboardingView.spec.ts` | 首次登录引导跳转 |

### 10.5 自动化测试钩子

CLAUDE.md §5 自动测试钩子覆盖：
- 编辑 backend/*.py → unit + e2e 自动
- 编辑 alembic/ 或 tests/integration/ → 容器在运行则跑 integration
- frontend/*.vue 编辑 → 手动 `npm run test:unit`（不入钩子）

---

## 11. 冒烟测试（tests/smoke/test_api_live.py）

Phase 8 末尾最大编号 **API-73**，Phase 10 从 **API-74 起**：

| 编号 | 端点 | 场景 |
|------|------|------|
| API-74 | GET `/notifications` | 无鉴权 → 401 |
| API-75 | GET `/notifications` | 有鉴权 → 200 |
| API-76 | GET `/notifications/unread-count` | 结构断言 |
| API-77 | POST `/notifications/{id}/read` | 合法 ID → 200 |
| API-78 | POST `/notifications/999999/read` | 不存在 → 404 |
| API-79 | GET `/setup/status` | 200 |
| API-80 | POST `/setup/complete` | 200 + status 变更 |
| API-81 | GET `/settings/export` | Content-Type: text/yaml，结构包含 11 个 key |
| API-82 | POST `/settings/import` | 合法 YAML → 200 + 差异列表 |
| API-83 | POST `/settings/import` | 非法 YAML → 422 |
| API-84 | GET `/notifications/wx-status` | 200 + `wx_configured: bool` |

共 **11 个**（API-74~84）。

---

## 12. 交付清单（DoD）

### 12.1 实现层

**P10-A 通知**
- [x] `notification/base.py` + `NotificationChannel` ABC（v1.1 评审 Q-1 新增）
- [x] `notification/wxpusher.py` + `WxPusherAdapter`（v1.1 评审 Q-1：由 `data/adapters/` 迁出）
- [x] `services/notification_service.py` 重写（真实实现 + 降级 + 日志级别对齐 §5.3 v1.1）
- [x] `models/business.py` 新增 `InAppNotification`
- [x] `api/v1/notifications.py`（5 端点）
- [x] `pipeline/scheduler.py` 新增止损预警 Job（15:05 每日）
- [x] `pipeline/daily_pipeline.py` 接入通知
- [x] `services/market_state_service.py` 状态变更推送

**P10-B UserConfig 消费**
- [x] `core/config_defaults.py`（SDD 附录 B 默认值常量）
- [x] `services/config_service.py`（ConfigService + **12** 个 `get_*` + `get_all_for_snapshot()` / `get_pipeline_snapshot()`；v1.1 评审 Q-5/G-3）
- [x] **12** 个 dataclass 定义（分布在各 Engine 模块，含 `FactorMonitorConfig`）
- [x] `engine/signal.py` / `risk.py` / `pool.py` / `market_state.py` / `universe.py` / `scorer.py` 构造签名改为接收 dataclass
- [x] 4 个策略改造（`engine/strategies/trend|momentum|reversion|value.py`）
- [x] `services/strategy_service.py` / `signal_service.py` / `factor_monitor_service.py` 调用方改为从 ConfigService 读
- [x] `api/v1/backtest.py` `POST /backtest/run` 端点层 partial-overlay 合并 `backtest_defaults`（v1.1 评审 Q-2）
- [x] `models/system.py` 扩展 `pipeline_run.config_snapshot`；`backtest_task.config_snapshot` 列同步补建（v1.1 评审 Q-2）
- [x] `pipeline/daily_pipeline.py::run_for_date` 入口一次性写快照（v1.1 评审 Q-5），所有 CP 从快照读 dataclass 不再访问 ConfigService
- [x] `alembic/versions/0007_phase10_config_and_notifications.py`

**P10-C Settings 前端**
- [x] `frontend/src/views/SettingsView.vue` 重构（三段折叠 + 12 key 全量目录 + 字段级 tier 覆盖 v1.1）
- [x] `frontend/src/components/settings/NotificationTab.vue`（合并入 SettingsView Tab）
- [x] `frontend/src/components/settings/WatchlistTab.vue`（合并入 SettingsView Tab）
- [x] `frontend/src/views/OnboardingView.vue`（首次向导）
- [x] `frontend/src/api/settings.ts` YAML 导入导出
- [x] `frontend/src/api/notifications.ts`
- [x] `frontend/src/api/setup.ts`
- [x] 所有 Views 的 EmptyState 文案
- [x] 术语 Tooltip 系统化（`utils/glossary.ts` 28 项 + `components/TermLabel.vue`；接入 Dashboard / Backtest / FactorQuality / Reports / Settings v1.1 §6.8）
- [x] NavBar 未读通知 Badge

**P10-D 收尾**
- [x] `services/signal_service.py:generate_for_date` 完整化（移除降级；附 INT-SIG-GEN-01 集成测试）
- [x] `services/factor_monitor_service.py` 告警走 NotificationService

**P10-E 部署**
- [x] `docker-compose.prod.yml`
- [x] `nginx/nginx.prod.conf`
- [x] `frontend/Dockerfile.build` + `backend/Dockerfile.prod`
- [x] `scripts/backup_db.sh` + `scripts/restore_db.sh` + `scripts/prod_smoke.sh` + `scripts/deploy.sh`
- [x] `core/logging_config.py`（RotatingFileHandler + JSON）
- [x] `.env.prod.example`
- [x] `docs/guides/deployment.md`

### 12.2 测试层

- [x] `tests/unit/test_config_service.py`（INV-CFG-01~04）
- [x] `tests/unit/test_wxpusher_adapter.py`（INV-WX-01~03，httpx mock）
- [x] `tests/unit/test_notification_service.py`（INV-NTF-01~04）
- [x] `tests/unit/test_scorer.py` 扩展（INV-SCR-W-01）
- [x] `tests/e2e/test_notifications_api.py`（E2E-NTF-01~04）
- [x] `tests/e2e/test_setup_api.py`（E2E-SETUP-01~02）
- [x] `tests/e2e/test_config_export_api.py`（E2E-CFG-01~03）
- [x] `tests/integration/test_int_config_service.py`（INT-CFG-01 a~g 7 cases）
- [x] `tests/integration/test_int_config_consumption.py`（INT-CFG-02/03）
- [x] `tests/integration/test_int_notification_flow.py`（INT-NTF-01 a~e 5 cases）
- [x] `tests/integration/test_int_signal_generate_for_date.py`（INT-SIG-GEN-01 a~c 3 cases）
- [x] `tests/smoke/test_api_live.py` 补充 API-74~84
- [ ] 前端 `SettingsView.spec.ts` + `OnboardingView.spec.ts`（V1.1 推迟：当前覆盖率以 E2E + 集成 + 手动验收为准）

### 12.3 质量门禁

- [x] `uv run ruff check src/ tests/` 输出 0 error
- [x] `uv run pytest tests/unit/ tests/e2e/` 全部通过
- [x] `uv run pytest tests/integration/ -v` 全部通过（466 passed total，含本 phase 新增 17 cases）
- [x] `API_PASSWORD=xxx uv run pytest tests/smoke/` 通过（84 个冒烟）
- [x] `npm run build` 前端构建成功（vue-tsc 0 error）
- [x] `docker compose -f docker-compose.prod.yml up` 本地冒烟通过（curl /health 200）
- [x] Phase 8 冒烟 API-73 兼容（Phase 10 不回归前期端点）

### 12.4 文档同步

- [x] `docs/design/system_design.md` §9 Phase 10 行更新
- [x] `docs/design/system_design.md` §3 目录树保留 `notification/` 独立目录（v1.1 评审 Q-1 采纳）
- [x] `CLAUDE.md` §9 进度表 Phase 10 状态改为"完成"
- [x] `CLAUDE.md` §7 环境变量清单补充 `WXPUSHER_APP_TOKEN` / `WXPUSHER_UID`（已在 v1.1 外围同步完成）
- [x] **SDD §14.4 推送设置** 扩至 6 项开关（v1.1 评审 Q-3）：新增"市场状态变化"与"因子告警"
- [x] **SDD 附录 B** 补录 `backtest_defaults.slippage_rate` 默认值来源（v1.1 评审 Q-7）与 `factor_monitor_params`（v1.1 评审 G-3）条目
- [x] SDD 字段命名与此设计对齐（`mean_reversion`）

---

## 13. 依赖与风险

### 13.1 关键依赖

- **WxPusher 服务号审核**：SDD §13.1 注明"V1.0 开发阶段可先以系统内通知为唯一渠道，待服务号审核通过后启用微信推送"。Phase 10 发布时若未审核通过，WxPusher 模块保持待接入，部署时 `WXPUSHER_APP_TOKEN` 留空即可自动降级。
- **Tushare 生产额度**：生产部署需 Tushare Pro **积分 ≥ 2000**，否则 `fetch_daily_basic` 等接口受限。

### 13.2 主要风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| Engine 层构造签名变更波及多处测试 | 大量 unit/integration 测试更新 | 按模块分批改造；每批跑钩子测试；保留 `__init__` 默认值参数形态以向后兼容旧测试（`Engine(cfg=None)` 时用默认 dataclass） |
| Scorer 权重矩阵格式变更 | Phase 4 `test_scorer.py` 可能失效 | 新增 `DEFAULT_STRATEGY_WEIGHTS` 作为默认参数；旧测试无需传入 |
| Redis 缓存与 DB 不一致 | 设置变更后旧值继续使用 | SettingsService.upsert 成功后**同步** `await config_service.invalidate()`；失败时记 ERROR 日志 |
| 配置快照字段膨胀 pipeline_run 行 | DB 体积增长 | JSONB 压缩；每行 < 5 KB，预估 250 天 × 5KB = 1.25 MB，可接受 |
| 首次向导未完成即绕过 | 用户绕过初始化导致空数据 | `GET /api/v1/setup/status` 作为后端"冷启动标志"，Pipeline/信号生成前检查；冷启动下返回 `service_unavailable` |

---

## 14. 设计对齐检查

### 14.1 SDD 对齐

- SDD §7.5 策略权重矩阵 → `strategy_weights` config_key（§2.3；键名 `trend/momentum/mean_reversion/value` 对齐 `BaseStrategy.name`）
- SDD §13.1 推送降级 → WxPusher + InAppNotification 兜底（§5.1~5.2；v1.1 日志级别 §5.3 末尾）
- SDD §13.2 触发规则 → 5 类事件推送（§5.4）
- SDD §13.3 消息模板 → `_render_signal_template` 等（§5.3）
- SDD §14.1 基础设置 → Settings 基础段（§6.1）
- SDD §14.4 提醒设置 → `notification_prefs` + Tab（§5.2 + §6.4）；v1.1 评审 Q-3 扩至 6 项，**SDD §14.4 需同步 v1.x**
- SDD §14.5 黑白名单 → Settings Tab（§6.5）
- SDD §14.6 配置版本 → `user_config_history`（Phase 6 已完成）+ `pipeline_run.config_snapshot`（本 Phase 新增；v1.1 评审 Q-5 明确启动时一次性写入）
- SDD §15.1 易用性 → 新手向导 4 步 + EmptyState + Tooltip（§6.6~6.8；v1.1 评审 G-7 纳入全量 4 步）
- SDD §15.5 日志 → RotatingFileHandler + JSON（§8.4）
- SDD 附录 B 默认参数总表 → `core/config_defaults.py` 单一事实来源（§3.1）；v1.1 评审 Q-7 `slippage_rate` 待 SDD v1.x 补录；v1.1 评审 G-3 `ic_window` 作为 `factor_monitor_params` 字段纳入

### 14.2 system_design 对齐

- system_design §9 Phase 10 行已在 v1.7 扩展为"配置消费 + 通知 + 部署收尾"（外围 2026-04-20 同步完成）
- system_design §3 目录树含 `notification/` 独立目录，本 Phase v1.1 路径（`notification/base.py` + `notification/wxpusher.py`）与之一致
- system_design §5.10 NotificationChannel ABC 规划 → `notification/base.py` 实现
- system_design §6 API 端点表将新增 Phase 10 行（9 个端点）

### 14.3 CLAUDE.md 对齐

- §7 环境变量清单已新增 `WXPUSHER_APP_TOKEN` / `WXPUSHER_UID` / `REDIS_URL`（v1.1 外围完成）
- §9 进度表 Phase 10 状态标"完成 + 代码审查通过 ✓"（Phase 10 收尾时更新）

### 14.4 评审闭环（v1.1 内部核查表）

| 评审编号 | 级别 | 处置 | 对应修订章节 |
|---------|------|------|-------------|
| Q-1 | P2 | 路径迁移 | §1.1 / §5.1 / §12.1 / §14.2 |
| Q-2 | P2 | BacktestEngine 接入明确化 | §4.4（新） / §12.1 |
| Q-3 | P3 | notification_prefs 6 项（驱动 SDD §14.4 v1.x） | §2.3 / §12.4 |
| Q-4 | P3 | 日志级别三档 | §5.2 / §5.3 末尾表 |
| Q-5 | P3 | 快照启动时一次性写入 | §4.3（重写） |
| Q-6 | P3 | `reversion` → `mean_reversion` | §2.3 / §6.1 |
| Q-7 | P3 | slippage_rate SDD 来源注释 | §2.3 / §12.4 |
| G-1 | P2 | Pipeline WS 后端未实装 / Backtest WS 前端 V1.5 | §1.2 |
| G-2 | P2 | AKShare 自动降级 V1.5 | §1.2 |
| G-3 | P3 | 新增 `factor_monitor_params`（第 12 个 config_key） | §1.3 / §2.3 / §4.3 / §6.1 / §12.1 |
| G-4 | P3 | 多账户 UI V1.5 | §1.2 / §1.3 |
| G-5 | P3 | 完整行为分析 V1.5 | §1.2 / §1.3 |
| G-6 | P3 | 5 类通知模板 V1.0 纳入 | §1.3 / §5.3 |
| G-7 | P3 | OnboardingWizard 4 步 V1.0 纳入 | §1.3 / §6.6 |
| G-8 | P3 | 12 个 config_key 的配置历史 UI V1.0 纳入 | §1.3 / §6.9 |

---

## 15. 收尾核查条目（本 Phase 验收）

1. 全部 84 个冒烟测试通过（API-01~84）
2. 全部集成测试通过（Phase 1~10 所有集成测试，含 INT-CFG-02 配置变更→信号变化闭环）
3. `uv run ruff check src/ tests/` 0 error
4. 生产 Docker Compose 本地启动通过，前端 `/` + 后端 `/api/v1/health` 均 200
5. 部署文档按步骤演练通过（全新服务器）
6. SDD / system_design / CLAUDE.md 三方文档同步更新
7. 所有 no-op stub 清除（grep `no-op` 无命中）
8. 所有"Phase 10 替换"注释清除（grep `Phase 10` 非注释引用外无残留）
