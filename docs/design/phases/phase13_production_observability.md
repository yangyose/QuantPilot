# Phase 13：生产可观测 + 部署评审并入（V1.0 收尾批次）

> **版本：** v1.1
> **日期：** 2026-05-21
> **依据文档：** QuantPilot_SDD.md v1.4 §15.4（部署）/ §15.5（日志）/ §16（V1.5+ 路线图：可观测性栈 V1.5-H 已升级 V1.0 Phase 13）；system_design.md §9 Phase 13 行；docs/reviews/v1_overall_review_2026-04-27.md §6.5（S5-GAP-01/02/03）+ §6.2（S2-GAP-01）+ §8.3（D4-GAP-03 + V1.5-H 主题汇总）；docs/reviews/phase10_design_review_2026-04-20.md §3.4（G-1 WebSocket 前端消费 / G-2 AKShare 自动降级）；docs/design/v1_post_release_roadmap.md §1.x（V1.5-H 5 项 + G-1/G-2 升级 V1.0 Phase 13）；docs/design/phases/phase10_deployment.md §8（生产 Docker / 日志 / 通知降级现状）

---

## 修订历史

| 版本 | 日期 | 说明 |
|------|------|------|
| **v1.1** | **2026-05-21** | **v1.0 设计评审 P1 修订**（依据 `docs/reviews/phase13_design_review_2026-05-21.md` §2 4 项 P1）：(1) **P1-1**：§1.1 P13-D 模块表新增"main.py lifespan Redis 客户端实例化"行——全库 grep `app\.state\.redis\s*=` 仅命中 `main.py:57` 唯一赋值 `= None`，Phase 8/10 Redis 设计（ConfigService 缓存 + WS 进度推送）实际从未生效；不补此项 P13-D 全部交付物（WS 后端 + 前端 client + PipelineView/BacktestRunView 接入）都是纸面交付；(2) **P1-2**：取消新端点 `/factor-quality/icir-timeseries`——与既有 `/factor-quality/ic-history` 业务范围 100% 重叠（同源 factor_ic_window_state + 同过滤参数 strategy/factor/state/start/end），改为扩展 `/ic-history` 的 series 分组消费由前端客户端完成；§4.1 / §4.2.3 / §6.3 E2E-P13-B-02 / §6.4 API-100/101 全部改挂 `/ic-history`；(3) **P1-3**：§3.5.1 `FactorICRepository.get_recent_icir_state` 改为 `get_recent_aggregates(as_of=month_end, limit=3)`——现有 method 语义 100% 重叠（按 as_of 倒推近 N 行 ICIR 聚合行），避免孤儿方法调用（CLAUDE.md §10 第 3 条）；(4) **P1-4**：Phase 12 评审 P1-2（AttributionService.run_monthly lookback `timedelta(30.5 × n)` 日历天近似）从 §1.4 移到 §1.3，标注 "Phase 13 启动核查阶段顺带处置"——评审证明 lookback 与 R14-P2-4 ICIR 窗口计算路径独立，捆绑推迟不满足 CLAUDE.md §11 4 类充分理由；Phase 12 评审 P1-1（silent truncation logging）+ P1-3（AttributionPanel DisclaimerBanner 复用 V1.0 Batch 1 组件）同批顺带处置（评审 §4 P3-7 建议） |
| v1.0 | 2026-05-21 | Phase 13 设计文档初版。基于 system_design §9 Phase 13 行 + 5 项 GAP（S5-GAP-01/02/03 / S2-GAP-01 / D4-GAP-03）+ G-1/G-2（升级 Phase 10 评审）+ 因子衰减监控展开模块/数据流/API/DoD/测试用例 |

---

## 1. 范围声明

### 1.1 本 Phase 纳入模块（system_design §9 Phase 13 行）

**P13-A 指标暴露 + 调度器健康（S5-GAP-01 + S5-GAP-02）**

| 模块 | 路径 | 说明 |
|------|------|------|
| MetricsRegistry | `core/metrics.py`（新增） | `prometheus_client.CollectorRegistry` 单例；统一注册 Counter/Gauge/Histogram；提供 `record_pipeline_duration` / `record_signal_count` / `record_tushare_call` / `record_validator_error` / `record_backtest_queue_depth` / `set_factor_icir` 等便捷写入函数 |
| /metrics 端点 | `api/v1/metrics.py`（新增） | Prometheus exposition 端点（text/plain），**无 JWT 鉴权**（生产 nginx 限制内网访问）；用 `generate_latest(registry)` 输出 |
| MetricsService | `services/metrics_service.py`（新增） | Pipeline / Tushare / Validator / NotificationService / FactorMonitorService 6 个埋点入口的协作封装；service 持有 registry handle，被各 service 调用 |
| SchedulerHealthService | `services/scheduler_health.py`（新增） | 从 `app.state.scheduler` 取 `scheduler.get_jobs()`；返回 `[{job_id, next_run_time, trigger, last_run_status, failure_count}]`；失败计数靠 `scheduler.add_listener(EVENT_JOB_ERROR)` 累积到内存 dict |
| /health/scheduler 端点 | `api/v1/health.py`（新增） | JWT 鉴权；返回 SchedulerHealthService 序列化结果；scheduler 未初始化 → 200 + 空 jobs 列表 + `running=false` |
| /health/data 端点 | `api/v1/health.py`（新增） | JWT 鉴权；返回近 1 个交易日数据延迟（`max(daily_quote.trade_date)` vs `today`）+ 近 30 日 DataValidator 错误数 |
| Scheduler 监听器 | `pipeline/scheduler.py` | `scheduler.add_listener(_record_metric, EVENT_JOB_EXECUTED \| EVENT_JOB_ERROR)`：执行时写 Counter `quantpilot_scheduler_jobs_total{job_id, status}` |

**P13-B 数据质量持久化 + 因子衰减告警（S2-GAP-01 + 因子衰减监控）**

| 模块 | 路径 | 说明 |
|------|------|------|
| DataQualityMetric ORM | `models/business.py`（扩展） | 新表 `data_quality_metric`（id / metric_date / data_type / metric_key / metric_value / details JSONB / created_at），UNIQUE (metric_date, data_type, metric_key) |
| Alembic 0012 | `alembic/versions/0012_phase13_data_quality.py`（新增） | 创建 `data_quality_metric` 表 + 索引 |
| DataQualityRepository | `data/data_quality_repository.py`（新增） | `upsert_metric(metric_date, data_type, metric_key, value, details)` / `get_metrics_by_range(start, end, data_type=None)` / `get_recent_violations(days=30)` |
| DataValidator 持久化包装 | `services/data_service.py::ingest_daily` | 调 `validator.validate_*` 后把 `errors` / `invalid_rows.size` 写 `DataQualityRepository.upsert_metric`，分 `daily_quote` / `financial_data` / `index_history` 三个 data_type；is_valid=False 时同步触发 NotificationService.notify_health_alert |
| FactorMonitorService ICIR 持续告警 | `services/factor_monitor_service.py`（扩展） | 月末批结束后查 `factor_ic_window_state`：若同一 (strategy, factor, state) 连续 N 月（默认 3）icir < 0.05 → 触发 `notify_factor_alert("factor_decayed", ...)` |
| NotificationService.notify_health_alert | `services/notification_service.py`（扩展） | 新增类型 `HEALTH_ALERT`（Pipeline 失败 / DB 不可达 / 数据延迟 > 2 个交易日 / DataValidator is_valid=False 4 类触发条件）；接 WxPusher + 站内信降级 |

**P13-C 日志 SecretFilter + AKShare 自动降级（S5-GAP-03 + G-2）**

| 模块 | 路径 | 说明 |
|------|------|------|
| SecretFilter | `core/logging_config.py`（扩展） | `logging.Filter` 子类，正则匹配 `TUSHARE_TOKEN` / `ADMIN_PASSWORD_HASH` / `JWT_SECRET_KEY` / `WXPUSHER_APP_TOKEN` / `bcrypt:\$2[abxy]\$.+` / `^Bearer\s+[A-Za-z0-9._-]+`，匹配后替换为 `***REDACTED***`；setup_logging 内 root logger 同时挂在 console + file 两个 handler 上 |
| AKShareAdapter 补全 | `data/adapters/akshare.py`（扩展） | 实现 `fetch_daily_quotes` / `fetch_index_history`（namechange / financial / index_components 4 类保持 NotImplementedError，留 V1.5+；【降级说明】Phase 13 仅覆盖日线 + 指数行情，财务/分红/股东仅用于 critical-path 之外的诊断）|
| DataService Tushare→AKShare 降级 | `services/data_service.py::ingest_daily` | 调 TushareAdapter 失败（NetworkError / 空返回 / 接口限流）时 `try AKShareAdapter`；失败/成功均 `logger.warning("data_source_fallback ...")` + Counter `quantpilot_data_source_fallback_total{from, to, status}`；NotImplementedError 路径返回 None 不重抛 |

**P13-D WebSocket 前端消费（G-1）**

| 模块 | 路径 | 说明 |
|------|------|------|
| **main.py lifespan Redis 客户端实例化**（v1.1 P1-1 必修）| `main.py` | `from redis.asyncio import from_url`；lifespan 启动时 `if settings.redis_url: app.state.redis = await from_url(settings.redis_url, decode_responses=True)`；shutdown 时 `await app.state.redis.aclose()`；REDIS_URL 未配置时保持 None（兼容当前测试环境）。**前置必修**：当前全库 `app.state.redis = None` 是唯一赋值，Phase 8/10 设计的 ConfigService 缓存 + WS 进度推送实际从未生效；不补此项 P13-D 全部交付物（WS 后端 + 前端 client + PipelineView/BacktestRunView 接入）都是纸面交付 |
| /pipeline/progress WS 后端 | `api/v1/pipeline.py`（扩展） | 与 `/backtest/{task_id}/progress` 同模式：Redis Pub/Sub channel `quantpilot:pipeline:progress`；前端订阅；DailyPipeline 各 CP 跨越时 `redis.publish(channel, {step, status, progress_pct, message})` |
| frontend/src/api/websocket.ts | `frontend/src/api/websocket.ts`（新增） | 复用 baseURL；自动重连（5s 间隔，最多 5 次）；onMessage 回调 |
| Pipeline 实时进度组件 | `frontend/src/views/PipelineView.vue`（扩展） | 触发 Pipeline 后挂 WS；展示 progress bar + 当前 CP；任务结束 / 错误关闭 WS |
| Backtest 进度消费 | `frontend/src/views/BacktestRunView.vue`（扩展） | 复用 WebSocketClient 接 `/backtest/{task_id}/progress`；替代当前轮询 `/status` 端点 |
| DailyPipeline 进度上报 | `pipeline/daily_pipeline.py`（扩展） | 在每个 CP 进入 / 退出时 `redis.publish` 写当前进度 |

**P13-E 监控 stack + 部署评审（D4-GAP-03）**

| 模块 | 路径 | 说明 |
|------|------|------|
| docker-compose.monitoring.yml | `docker-compose.monitoring.yml`（新增） | Prometheus + Grafana 单机 stack（profile=`monitoring`，默认不启动；`docker compose --profile monitoring up`）|
| prometheus 配置 | `infra/prometheus/prometheus.yml`（新增） | scrape_configs：每 30s 抓 `backend:8000/metrics`；alerts 留 V1.5+ |
| Grafana dashboard JSON | `infra/grafana/dashboards/quantpilot.json`（新增） | 5 个面板：Pipeline 执行时长 / 信号数 / Tushare QPS / Validator 错误率 / Factor ICIR 时序 |
| 部署指南章节扩展 | `docs/guides/deployment.md`（扩展） | 新增"§N 监控栈启动 + 告警接入"段：监控 stack 启动命令 / 端口映射（Prometheus 9090 / Grafana 3001 内网）/ 默认 dashboard 导入步骤 / 备份策略 |

**P13-F 测试 / 冒烟 / 文档同步**

| 任务 | 内容 |
|------|------|
| 单元 + 集成 + E2E 回归 | UT-P13-A-01~04 + UT-P13-B-01~03 + UT-P13-C-01~05 + UT-P13-D-01~02 + UT-P13-E-01~02 + UT-P13-F-01 + INT-P13-A-01~02 + INT-P13-B-01~02 + INT-P13-C-01 + E2E-P13-A-01~03 + E2E-P13-B-01~02（详见 §6）|
| 冒烟 API-96~105 | `tests/smoke/test_api_live.py` 续接 Phase 12 API-95 |
| 文档同步 | SDD §15.4/§15.5/§16 + system_design.md §9 + CLAUDE.md §9 + memory/MEMORY.md |
| ruff 收尾 | `uv run ruff check src/ tests/` → 0 error |

### 1.2 推迟项 / 不在本 Phase 范围

| 项 | 推迟到 | 充分理由（CLAUDE.md §11 标准）|
|---|---|---|
| **OpenTelemetry trace 接入** | V1.5+ | "依赖外部决策"：V1.0 单机部署仅 prometheus_client 已覆盖核心 metrics 需求；OTel 需 collector + 后端（Jaeger/Tempo），属可观测性 V2 范畴。本 Phase 仅做 `prometheus_client` |
| **AlertManager + 告警路由规则** | V1.5+ | "验收标准未定义"：V1.0 仅靠 NotificationService.notify_health_alert（WxPusher + 站内信）覆盖运营告警；AlertManager 需独立部署 + 告警规则梳理，5 个 P2 项中 D4-GAP-03 已说明"V1.0 仅生产配置 + Grafana 可视化" |
| **AKShare 财务 / 分红 / namechange 补全** | V1.5+ | "大重构跨 phase"：4 类接口字段映射 + 单位换算 + 测试数据准备 ~3 pd；critical-path（评分链路）当前不依赖这 4 类备用源（Tushare 5y 真机已 PASS）；仅留 daily_quotes + index_history 2 类支持诊断告警 |
| **APScheduler 集群化（SQLAlchemyJobStore）** | V1.5+ | "依赖外部决策"：V1.0 单机部署 in-memory 已足；集群化需多副本 + jobstore 数据库选型 |
| **Prometheus AlertRules + 推送 Pushgateway** | V1.5+ | 同 AlertManager 理由 |
| **API rate limit（S4-GAP-03）** | V1.5+ | 不在 V1.5-H 主题内（属 V1.5-G 安全主题）；V1.0 单管理员场景无需 |
| **集成测试故障注入 / DB 单进程串行** | V1.5+ | 不在 V1.5-H 主题内（属 V1.5-I 性能扩展主题）；V1.0 单机 OK |
| **多账户切换 UI（G-4）** | V1.5-G | Phase 10 评审 G-4 推迟决议；V1.0 单管理员账户 |

### 1.3 前序 Phase 推迟项继承清单

| 继承自 | 项 | 在 Phase 13 的处理 |
|---|---|---|
| Phase 10 评审 G-1 | WebSocket 前端消费 | P13-D：完整实施（含 /pipeline/progress WS 后端补 + 前端 websocket.ts + BacktestRunView/PipelineView 接入）|
| Phase 10 评审 G-2 | AKShare 自动降级 | P13-C：补 fetch_daily_quotes + fetch_index_history + DataService fallback 路径 + 日志可见 |
| V1.5-H S5-GAP-01 | Prometheus / OpenTelemetry | P13-A：实施 prometheus_client；OTel 推迟（§1.2）|
| V1.5-H S5-GAP-02 | 调度器健康端点缺失 | P13-A：/health/scheduler 端点 |
| V1.5-H S5-GAP-03 | 日志 SecretFilter 缺失 | P13-C：SecretFilter 子类 + setup_logging 挂载 |
| V1.5-H S2-GAP-01 | 数据质量监控指标未持久化 | P13-B：DataQualityMetric 表 + Repository + DataValidator 写回 |
| V1.5-H D4-GAP-03 | 监控/告警生产配置 | P13-E：docker-compose.monitoring.yml + Grafana dashboard |
| Phase 12 P12-D 冒烟基线 | API-67 / API-73 / API-83 三项失败 | **API-83 ✅ 启动核查阶段已修**（2026-05-21）：原冒烟用 `"::::invalid::::\n  - bad"` 被 yaml.safe_load 解析成合法 dict `{':::invalid:::': ['bad']}`，按 SDD §10.3 best-effort skip 未知 key 返回 200——改用与 e2e CFG-IMP-04 一致的 `":::\ninvalid: [unclosed"` 真正触发 `yaml.YAMLError` → 422。**API-67 / API-73 ⏳ P13-F1 实施期 live 冒烟阶段复核**：已确认 `tests/e2e/test_backtest_api.py` 8/8 PASS（ASGI 客户端代码路径健全），离线无法复现 live 失败；按 CLAUDE.md §11 "物理资源约束"——根因复现需生产 docker stack + Tushare token + 5y 真机数据 + uvicorn 在线，留 P13-F1 真机冒烟跑通后排查（可能为 calendar 启动顺序 / 日期范围内无交易日 / BacktestService 入参校验回归）|
| **Phase 12 实施评审 P1-1**（v1.1 P1-4 新增继承） | AttributionService forward_returns 部分截断无日志 | **Phase 13 启动核查阶段顺带处置**——`services/attribution_service.py:132-152` 加 `ratio < 0.8` 时 `logger.info("attribution_truncation_partial reason=missing/quote_unavailable/holiday_boundary ratio=...")`；评审报告原 P1-1 处置标记"已收口 2026-05-20"，本评审复核确认 logger 加在了 `attribution_service.py` 但需复跑 `INT-P12-B-03` 验证日志可见 |
| **Phase 12 实施评审 P1-2**（v1.1 P1-4 必修） | `services/attribution_service.py:79` `lookback = timedelta(days=int(30.5 × lookback_months))` 日历天近似 | **Phase 13 启动核查阶段顺带处置**——改用 `TradingCalendar.get_prev_trade_date(month_end, n=20 × lookback_months)` 严格交易日；原 v1.0 §1.4 推迟 Phase 14 不满足 CLAUDE.md §11 充分理由（lookback 与 R14-P2-4 ICIR 窗口计算路径独立），本评审 P1-4 必修；预计 30 分钟（含 INT-P12-B-03 用例数据更新）|
| **Phase 12 实施评审 P1-3**（v1.1 P1-4 新增继承） | `AttributionPanel.vue` 未复用 `DisclaimerBanner` 合规组件 | **Phase 13 启动核查阶段顺带处置**——`frontend/src/components/AttributionPanel.vue` 改 `<DisclaimerBanner :text="..." />` 替代手写 `<small>`；评审报告原 P1-3 处置标记"已收口 2026-05-20"，本评审复核确认前端 commit `954770c` 已含改动，但需启动核查抽测一次 UI 显示一致 |

### 1.4 Phase 12 实施评审残留处置（2026-05-20 评审，v1.1 P1-4 修订）

> 依据 `docs/reviews/phase12_implementation_review_2026-05-20.md` §9 修订追踪表 + memory `v1_finalize_deferred_items.md` "Phase 14 推迟项"。
>
> **v1.1 P1-4 修订**：原 v1.0 §1.4 列 "Phase 12 评审 P1-2 / P2-1 / P2-2 / P2-3 / P2-4 / P2-6 / P2-7 共 7 项统一推迟 Phase 14"。Phase 13 v1.0 评审复核：P1-2（lookback 30.5 日历天）与 R14-P2-4 ICIR 窗口**计算路径独立**，捆绑推迟不满足 CLAUDE.md §11 4 类充分理由，已移至 §1.3 启动核查阶段必修；P1-1（silent truncation logging）+ P1-3（AttributionPanel DisclaimerBanner）同批顺带处置（评审 §4 P3-7 建议）。
>
> **剩余推迟 Phase 14 仅 6 项 P2**（P2-1 / P2-2 / P2-3 / P2-4 / P2-6 / P2-7）——理由保持不变（与 R14-P2-4 ICIR 窗口同源 / batch 分片预案与历史回算同批 / limit 评估待数据量到位）；
> **Phase 13 不再承接这 6 项 P2**——若 Phase 14 实施需绕回 Phase 12 模块，独立 commit + 评审追踪表更新。

---

## 2. 数据流

### 2.1 指标暴露数据流（P13-A）

```
Pipeline / Service 调用点  ─┐
                            ├─→ MetricsRegistry (CollectorRegistry 单例)  ─→ /metrics 端点 (text/plain)
Scheduler 监听器 (EVENT_*) ─┘                                              ↓
                                                                          Prometheus scrape (30s)
                                                                          ↓
                                                                          Grafana dashboard
```

**埋点清单（V1.0 优先级）**：

| Counter | 标签 | 触发点 |
|---|---|---|
| `quantpilot_pipeline_runs_total` | `{status}` (success/failed/partial) | DailyPipeline 退出时 |
| `quantpilot_signals_generated_total` | `{type}` (BUY/SELL/HOLD) | SignalService.upsert_signals 后 |
| `quantpilot_tushare_calls_total` | `{interface, status}` | TushareAdapter `_call()` 包装 |
| `quantpilot_validator_errors_total` | `{data_type, error_type}` | DataValidator.validate_* errors 非空时 |
| `quantpilot_data_source_fallback_total` | `{from, to, status}` | DataService Tushare→AKShare 降级时 |
| `quantpilot_scheduler_jobs_total` | `{job_id, status}` | scheduler 事件监听 |
| `quantpilot_notifications_sent_total` | `{notify_type, channel, status}` | NotificationService.notify 后 |

| Gauge | 标签 | 更新点 |
|---|---|---|
| `quantpilot_factor_icir` | `{strategy, factor, state}` | FactorMonitorService.rolling_icir_state 后 |
| `quantpilot_backtest_queue_depth` | — | BacktestService.run 提交时 |
| `quantpilot_data_latency_days` | `{data_type}` | DailyPipeline CP1 完成后（`today - max(trade_date)`）|

| Histogram | 标签 | 触发点 |
|---|---|---|
| `quantpilot_pipeline_duration_seconds` | `{step}` (cp1/cp2/cp3/cp4/cp5/cp6) | DailyPipeline 各 CP 退出 |
| `quantpilot_api_request_duration_seconds` | `{endpoint, method, status}` | FastAPI middleware（V1.0 仅核心端点）|

### 2.2 调度器健康数据流（P13-A）

```
GET /health/scheduler  (JWT)
  ↓
SchedulerHealthService.snapshot()
  ↓ 1. scheduler = app.state.scheduler (or None)
  ↓ 2. for job in scheduler.get_jobs():
  ↓       collect {id, next_run_time, trigger.__str__(),
  ↓                last_run_status, last_error_at, failure_count}
  ↓ 3. failure_count 从内存 dict 取（scheduler.add_listener EVENT_JOB_ERROR 累积）
  ↓ 4. running = scheduler.running (or False)
  ↓
{
  "running": true,
  "jobs": [
    {"id": "daily_pipeline", "next_run_time": "2026-05-22T09:30:00+08:00",
     "trigger": "cron[hour=09, minute=30]", "last_run_status": "success",
     "last_error_at": null, "failure_count": 0},
    ...
  ],
  "total_jobs": 5
}
```

### 2.3 数据质量持久化数据流（P13-B）

```
DailyPipeline CP1 → DataService.ingest_daily(trade_date)
  ↓
TushareAdapter.fetch_daily_quotes(trade_date)
  ↓
DataValidator.validate_daily_quotes(df, prev_count) → ValidationResult
  ↓
async with AsyncSessionLocal():
  data_quality_repo.upsert_metric(
    metric_date=trade_date,
    data_type="daily_quote",
    metric_key="completeness_violation_count" / "price_invalid_count" / ...,
    metric_value=len(invalid_rows) or 1.0 if errors else 0.0,
    details={"errors": result.errors, "warnings": result.warnings,
             "invalid_count": len(invalid_rows)},
  )
  ↓ (若 is_valid=False)
  notification_service.notify_health_alert(
    "data_validation_failed",
    body=f"{trade_date} {data_type} 校验失败：{result.errors[0]}"
  )

Counter: quantpilot_validator_errors_total{data_type, error_type} += 1
```

### 2.4 因子衰减监控数据流（P13-B）

```
MonthlyScheduler (cron[day='last', hour=01])
  ↓
FactorMonitorService.run_monthly(month_end)
  ↓ Phase 11 已有路径：
  ↓ 1. _apply_ic_metric / _apply_icir_metric → upsert factor_ic_window_state
  ↓ 2. _maybe_alert → notify_factor_alert(alert_type, ...)（已存在）
  ↓
Phase 13 扩展：
  ↓ 3. for each (strategy, factor, state):
  ↓     recent_3_months = repo.get_recent_icir_state(strategy, factor, state, months=3)
  ↓     if all(s.icir < 0.05 for s in recent_3_months) and len(recent_3_months) >= 3:
  ↓         notify_factor_alert("factor_decayed_persistent",
  ↓                             strategy_name, factor_name,
  ↓                             ic_mean=recent_3_months[-1].icir)
  ↓         Gauge: quantpilot_factor_icir{strategy, factor, state} = icir
```

### 2.5 AKShare 降级数据流（P13-C）

```
DataService.ingest_daily(trade_date)
  ↓ try:
  ↓   df = await tushare_adapter.fetch_daily_quotes(trade_date)
  ↓   if df is None or df.empty: raise ValueError("empty_response")
  ↓ except (NetworkError, TushareRateLimit, ValueError) as exc:
  ↓   logger.warning("data_source_fallback from=tushare to=akshare reason=%s", exc)
  ↓   Counter quantpilot_data_source_fallback_total{from=tushare, to=akshare, status=trying} += 1
  ↓   try:
  ↓     df = await akshare_adapter.fetch_daily_quotes(trade_date)
  ↓     Counter ...{status=success} += 1
  ↓   except NotImplementedError:
  ↓     logger.error("data_source_fallback_unavailable interface=fetch_daily_quotes")
  ↓     Counter ...{status=failed} += 1
  ↓     notify_health_alert("tushare_failed_no_fallback", ...)
  ↓     raise  # 让 CP1 决定是否阻断
```

### 2.6 WebSocket 前端消费数据流（P13-D）

```
DailyPipeline.run_daily(trade_date)
  ↓ for cp in [CP1, CP2, CP3, CP4, CP5, CP6]:
  ↓   await self._redis.publish(
  ↓       "quantpilot:pipeline:progress",
  ↓       json.dumps({"trade_date": str(trade_date),
  ↓                   "step": cp, "status": "running",
  ↓                   "progress_pct": progress_pct})
  ↓   )

Frontend PipelineView.vue (Vue 3 Composition):
  ↓ const ws = new WebSocketClient(`${WS_BASE}/api/v1/pipeline/progress`)
  ↓ ws.onMessage((data) => {
  ↓   pipelineStore.updateProgress(data.step, data.status, data.progress_pct)
  ↓ })

Backend api/v1/pipeline.py:
  @router.websocket("/progress")
  async def ws_pipeline_progress(websocket: WebSocket):
      await websocket.accept()
      redis = websocket.app.state.redis
      pubsub = redis.pubsub()
      await pubsub.subscribe("quantpilot:pipeline:progress")
      async for msg in pubsub.listen():
          if msg["type"] == "message":
              await websocket.send_text(msg["data"])
```

---

## 3. 模块详细设计

### 3.1 MetricsRegistry + MetricsService（P13-A）

#### 3.1.1 `core/metrics.py`（新增）

```python
"""Phase 13 Prometheus 指标注册中心。

设计原则：
- 单例 CollectorRegistry，进程级唯一；多 worker 部署需用 multiprocess mode（V1.5+）
- 业务 service 通过模块级常量 import metric handles，避免 service 持有 registry
- 标签维度受控（V1.0 仅 7 个核心 Counter + 3 Gauge + 2 Histogram）
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry

REGISTRY = CollectorRegistry()

PIPELINE_RUNS = Counter(
    "quantpilot_pipeline_runs_total",
    "DailyPipeline 执行计数",
    ["status"],
    registry=REGISTRY,
)
SIGNALS_GENERATED = Counter(
    "quantpilot_signals_generated_total",
    "信号生成计数",
    ["type"],  # BUY / SELL / HOLD
    registry=REGISTRY,
)
TUSHARE_CALLS = Counter(
    "quantpilot_tushare_calls_total",
    "Tushare 接口调用计数",
    ["interface", "status"],  # status: success / rate_limit / error
    registry=REGISTRY,
)
VALIDATOR_ERRORS = Counter(
    "quantpilot_validator_errors_total",
    "DataValidator 错误计数",
    ["data_type", "error_type"],
    registry=REGISTRY,
)
DATA_SOURCE_FALLBACK = Counter(
    "quantpilot_data_source_fallback_total",
    "数据源降级计数",
    ["from_source", "to_source", "status"],
    registry=REGISTRY,
)
SCHEDULER_JOBS = Counter(
    "quantpilot_scheduler_jobs_total",
    "调度器 Job 执行计数",
    ["job_id", "status"],
    registry=REGISTRY,
)
NOTIFICATIONS_SENT = Counter(
    "quantpilot_notifications_sent_total",
    "通知发送计数",
    ["notify_type", "channel", "status"],
    registry=REGISTRY,
)

FACTOR_ICIR = Gauge(
    "quantpilot_factor_icir",
    "因子 ICIR（月末批后更新）",
    ["strategy", "factor", "state"],
    registry=REGISTRY,
)
BACKTEST_QUEUE_DEPTH = Gauge(
    "quantpilot_backtest_queue_depth",
    "回测任务队列深度",
    registry=REGISTRY,
)
DATA_LATENCY = Gauge(
    "quantpilot_data_latency_days",
    "数据延迟（today - max(trade_date)）",
    ["data_type"],
    registry=REGISTRY,
)

PIPELINE_DURATION = Histogram(
    "quantpilot_pipeline_duration_seconds",
    "Pipeline 各 CP 执行时长",
    ["step"],
    buckets=(5, 15, 30, 60, 120, 300, 600, 1800),
    registry=REGISTRY,
)
API_REQUEST_DURATION = Histogram(
    "quantpilot_api_request_duration_seconds",
    "API 请求耗时",
    ["endpoint", "method", "status"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY,
)
```

#### 3.1.2 埋点接入点

- `pipeline/daily_pipeline.py::run_daily` 退出时 `PIPELINE_RUNS.labels(status).inc()` + 各 CP 退出 `PIPELINE_DURATION.labels(step).observe(duration)`
- `services/signal_service.py::upsert_signals` 后 `SIGNALS_GENERATED.labels(type).inc(count)`
- `data/adapters/tushare.py::_call` 包装内 `TUSHARE_CALLS.labels(interface, status).inc()`
- `services/data_service.py::ingest_daily` validator 调用后 `VALIDATOR_ERRORS.labels(data_type, error_type).inc(len(errors))`
- DataService fallback 路径埋 `DATA_SOURCE_FALLBACK`
- `pipeline/scheduler.py::create_scheduler` 加 listener
- `services/notification_service.py::notify` 后埋 `NOTIFICATIONS_SENT`
- `services/factor_monitor_service.py::rolling_icir_state` 后 `FACTOR_ICIR.labels(...).set(icir)`
- `services/backtest_service.py::run` 入队时 `BACKTEST_QUEUE_DEPTH.inc()`，完成时 `.dec()`
- FastAPI middleware（在 `main.py`）测 `API_REQUEST_DURATION`，仅 `/api/v1/*` 路径

### 3.2 SchedulerHealthService + /health/scheduler（P13-A）

#### 3.2.1 `services/scheduler_health.py`（新增）

```python
"""Phase 13 调度器健康摘要。

不持久化失败计数（V1.0 单机进程）；重启后清零。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED


class SchedulerHealthService:
    def __init__(self, scheduler: AsyncIOScheduler | None) -> None:
        self._scheduler = scheduler
        self._failure_counts: dict[str, int] = {}
        self._last_run_status: dict[str, str] = {}
        self._last_error_at: dict[str, datetime] = {}
        if scheduler is not None:
            scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
            scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)

    def _on_job_executed(self, event: Any) -> None:
        self._last_run_status[event.job_id] = "success"

    def _on_job_error(self, event: Any) -> None:
        self._last_run_status[event.job_id] = "failed"
        self._failure_counts[event.job_id] = self._failure_counts.get(event.job_id, 0) + 1
        self._last_error_at[event.job_id] = datetime.now()

    def snapshot(self) -> dict[str, Any]:
        if self._scheduler is None:
            return {"running": False, "jobs": [], "total_jobs": 0}
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
                "last_run_status": self._last_run_status.get(job.id, "unknown"),
                "last_error_at": (
                    self._last_error_at[job.id].isoformat()
                    if job.id in self._last_error_at else None
                ),
                "failure_count": self._failure_counts.get(job.id, 0),
            })
        return {"running": self._scheduler.running, "jobs": jobs, "total_jobs": len(jobs)}
```

#### 3.2.2 主流程集成

- `main.py::lifespan` 创建 scheduler 后实例化 `app.state.scheduler_health = SchedulerHealthService(scheduler)`
- `api/deps.py::get_scheduler_health` 提供依赖注入
- `api/v1/health.py` 提供端点

### 3.3 SecretFilter（P13-C）

#### 3.3.1 `core/logging_config.py`（扩展）

```python
import logging
import re

_SECRET_PATTERNS = [
    re.compile(r"(TUSHARE_TOKEN|ADMIN_PASSWORD_HASH|JWT_SECRET_KEY|WXPUSHER_APP_TOKEN|REDIS_URL)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\$2[abxy]\$[0-9]{2}\$[./A-Za-z0-9]{53}"),  # bcrypt hash
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),  # JWT Bearer
    re.compile(r"AT_[A-Za-z0-9]{16,}"),  # WxPusher app token
    re.compile(r"UID_[A-Za-z0-9]{16,}"),  # WxPusher uid
]


class SecretFilter(logging.Filter):
    """Phase 13 S5-GAP-03：过滤日志中潜在敏感字段。

    匹配后整段替换为 ***REDACTED***。仅扫描 record.msg + record.args 的字符串表示，
    不修改原 dict/对象引用。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in _SECRET_PATTERNS:
            msg = pat.sub("***REDACTED***", msg)
        record.msg = msg
        record.args = ()  # 已经合并到 msg 里
        return True
```

在 `setup_logging` 内挂载：

```python
secret_filter = SecretFilter()
for handler in (console, file_handler):
    handler.addFilter(secret_filter)
```

### 3.4 DataQualityMetric + Repository（P13-B）

#### 3.4.1 ORM 模型（`models/business.py` 扩展）

```python
class DataQualityMetric(Base):
    __tablename__ = "data_quality_metric"
    __table_args__ = (
        UniqueConstraint("metric_date", "data_type", "metric_key",
                         name="uq_data_quality_date_type_key"),
        Index("idx_data_quality_date_desc", text("metric_date DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # daily_quote / financial_data / index_history / namechange
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # completeness_violation_count / price_invalid_count /
    # pit_violation_count / adj_factor_jump_count / ...
    metric_value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
```

#### 3.4.2 `data/data_quality_repository.py`（新增）

```python
class DataQualityRepository:
    @staticmethod
    async def upsert_metric(
        session: AsyncSession,
        metric_date: date,
        data_type: str,
        metric_key: str,
        metric_value: float,
        details: dict | None = None,
    ) -> None: ...

    @staticmethod
    async def get_metrics_by_range(
        session: AsyncSession,
        start: date,
        end: date,
        data_type: str | None = None,
    ) -> list[DataQualityMetric]: ...

    @staticmethod
    async def get_recent_violations(
        session: AsyncSession,
        days: int = 30,
    ) -> dict[str, dict[str, float]]:
        """返回近 N 日各 (data_type, metric_key) 累积值，用于 /health/data 端点。"""
```

### 3.5 FactorMonitorService 持续告警扩展（P13-B）

#### 3.5.1 触发条件

```python
# services/factor_monitor_service.py
async def _check_persistent_decay(
    self,
    strategy_name: str,
    factor_name: str,
    state: str,
    icir_now: float,
) -> None:
    """连续 3 个月末 icir < 0.05 → 触发 factor_decayed_persistent 告警。"""
    if icir_now is None or icir_now >= 0.05:
        return
    # v1.1 P1-3 修订：用 Phase 11 既有方法 get_recent_aggregates（语义 100% 重叠：
    # 按 as_of 倒推近 N 行 ICIR 聚合行），避免孤儿方法 get_recent_icir_state。
    history = await self._repo.get_recent_aggregates(
        self._session,
        strategy=strategy_name,
        factor=factor_name,
        state=state,
        as_of=month_end,
        limit=3,
    )
    if len(history) < 3:
        return  # 历史不足，不触发
    if all(h.icir is not None and h.icir < 0.05 for h in history):
        await self._notifier.notify_factor_alert(
            "factor_decayed_persistent",
            strategy_name, factor_name,
            ic_mean=icir_now,
        )
```

> **持续告警与单月告警的关系**：Phase 11 `_maybe_alert` 仅检查单月 ic_mean 是否 < 阈值；P13-B 新增 `_check_persistent_decay` 检查 N 月连续低 icir，避免单月异常误报。两者独立触发，可同时发出但 NotificationService.notify 去重逻辑会合并。

### 3.6 AKShare 降级 + DataService 路径（P13-C）

#### 3.6.1 AKShareAdapter.fetch_daily_quotes（实现）

```python
async def fetch_daily_quotes(
    self, trade_date: date, ts_codes: list[str] | None = None,
) -> pd.DataFrame:
    """AKShare 日线降级路径（V1.0 Phase 13 实现）。

    【降级说明】仅在 TushareAdapter 失败时由 DataService 自动调用，覆盖：
    - ts_code / trade_date / open / high / low / close / volume / amount
    - adj_factor 字段 AKShare 无对应接口，置 1.0；CP1 已知此降级数据精度低
    - 单次最多 1000 行；超过则记 logger.error + raise NotImplementedError
    """
    import akshare as ak
    df = await asyncio.to_thread(ak.stock_zh_a_daily, ...)
    # 字段映射 + 单位换算（成交量手→股 × 100；成交额万元→元 × 10000）
    return ...
```

#### 3.6.2 DataService fallback 编排

```python
async def _ingest_daily_quotes(self, trade_date: date) -> pd.DataFrame:
    try:
        df = await self._tushare.fetch_daily_quotes(trade_date)
        if df is None or df.empty:
            raise ValueError("tushare_empty_response")
        TUSHARE_CALLS.labels("daily_quote", "success").inc()
        return df
    except Exception as exc:
        logger.warning(
            "data_source_fallback from=tushare to=akshare reason=%s", exc,
            exc_info=True,
        )
        DATA_SOURCE_FALLBACK.labels("tushare", "akshare", "trying").inc()
        TUSHARE_CALLS.labels("daily_quote", "error").inc()
        try:
            df = await self._akshare.fetch_daily_quotes(trade_date)
            DATA_SOURCE_FALLBACK.labels("tushare", "akshare", "success").inc()
            return df
        except NotImplementedError:
            logger.error("data_source_fallback_unavailable interface=daily_quote")
            DATA_SOURCE_FALLBACK.labels("tushare", "akshare", "unavailable").inc()
            raise  # CP1 决定是否阻断
        except Exception as fallback_exc:
            logger.exception("data_source_fallback_failed reason=%s", fallback_exc)
            DATA_SOURCE_FALLBACK.labels("tushare", "akshare", "failed").inc()
            await self._notifier.notify_health_alert(
                "data_source_unavailable",
                f"Tushare + AKShare 均失败：{fallback_exc}",
            )
            raise
```

### 3.7 WebSocket 前端消费（P13-D）

#### 3.7.1 后端：`/pipeline/progress` WS 端点

```python
# api/v1/pipeline.py
@router.websocket("/progress")
async def ws_pipeline_progress(websocket: WebSocket) -> None:
    """WS /pipeline/progress — 订阅 DailyPipeline 实时进度。"""
    await websocket.accept()
    redis = getattr(websocket.app.state, "redis", None)
    if redis is None:
        await websocket.send_json({"error": "Redis 未初始化"})
        await websocket.close()
        return
    pubsub = redis.pubsub()
    await pubsub.subscribe("quantpilot:pipeline:progress")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except Exception as exc:
        logger.debug("ws_pipeline_progress_closed reason=%s", exc)
    finally:
        await websocket.close()
```

#### 3.7.2 DailyPipeline 进度上报点

`pipeline/daily_pipeline.py` 加 `_publish_progress(step, status, progress_pct)` 私有方法；在 CP1 进入 / CP1 完成 / CP2 进入 ... CP6 完成共 12 个点调用；redis 为 None 时降级为 `logger.debug`。

#### 3.7.3 前端：`api/websocket.ts`

```typescript
export class WebSocketClient {
  private ws: WebSocket | null = null
  private url: string
  private retries = 0
  private maxRetries = 5
  private retryInterval = 5000
  private onMessageCallback: ((data: unknown) => void) | null = null

  constructor(path: string) {
    const base = import.meta.env.VITE_API_BASE_URL.replace(/^http/, 'ws')
    this.url = `${base}${path}`
  }

  connect(): void {
    this.ws = new WebSocket(this.url)
    this.ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        this.onMessageCallback?.(data)
      } catch (err) { console.warn('WS parse error', err) }
    }
    this.ws.onclose = () => this._maybeReconnect()
    this.ws.onerror = (e) => console.error('WS error', e)
  }

  onMessage(cb: (data: unknown) => void): void { this.onMessageCallback = cb }
  close(): void { this.maxRetries = 0; this.ws?.close() }

  private _maybeReconnect(): void {
    if (this.retries < this.maxRetries) {
      this.retries++
      setTimeout(() => this.connect(), this.retryInterval)
    }
  }
}
```

#### 3.7.4 前端：PipelineView.vue 接入

```vue
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { WebSocketClient } from '@/api/websocket'

const progress = ref<{ step: string; status: string; progress_pct: number } | null>(null)
let wsClient: WebSocketClient | null = null

onMounted(() => {
  wsClient = new WebSocketClient('/api/v1/pipeline/progress')
  wsClient.onMessage((data) => { progress.value = data as typeof progress.value })
  wsClient.connect()
})

onUnmounted(() => { wsClient?.close() })
</script>
```

### 3.8 监控 Stack（P13-E）

#### 3.8.1 `docker-compose.monitoring.yml`

```yaml
services:
  prometheus:
    image: prom/prometheus:v2.49.0
    profiles: ["monitoring"]
    ports: ["9090:9090"]
    volumes:
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    networks: [quantpilot_net]

  grafana:
    image: grafana/grafana-oss:10.4.0
    profiles: ["monitoring"]
    ports: ["3001:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - ./infra/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./infra/grafana/datasources:/etc/grafana/provisioning/datasources:ro
      - grafana_data:/var/lib/grafana
    networks: [quantpilot_net]
    depends_on: [prometheus]

volumes:
  prometheus_data:
  grafana_data:

networks:
  quantpilot_net:
    external: true
```

启动：`docker compose -f docker-compose.prod.yml -f docker-compose.monitoring.yml --profile monitoring up -d`

---

## 4. API 端点设计

### 4.1 新增端点

| Method | 路径 | 鉴权 | 用途 | 失败码 |
|---|---|---|---|---|
| GET | `/metrics` | 无（nginx 内网限制） | Prometheus exposition | 500（registry 异常）|
| GET | `/api/v1/health/scheduler` | JWT | 调度器健康摘要 | 401 |
| GET | `/api/v1/health/data` | JWT | 数据延迟 + 近 30 日 validator 错误数 | 401 / 500 |
| WS | `/api/v1/pipeline/progress` | 无（同 backtest WS）| DailyPipeline 实时进度 | 关闭 |

> **v1.1 P1-2 修订**：原计划新增 `GET /factor-quality/icir-timeseries` 已取消——与 Phase 11 既有 `GET /factor-quality/ic-history` 业务范围 100% 重叠（同源 `factor_ic_window_state` + 同过滤参数 `strategy / factor / state / start / end`）。前端 Grafana 图表 / 时序面板的 series 分组消费由客户端按 `(strategy, factor, state)` 分组完成，无需后端独立端点。

### 4.2 端点响应示例

#### 4.2.1 `GET /health/scheduler`

```json
{
  "code": 0,
  "data": {
    "running": true,
    "jobs": [
      {
        "id": "daily_pipeline",
        "next_run_time": "2026-05-22T09:30:00+08:00",
        "trigger": "cron[hour=09, minute=30]",
        "last_run_status": "success",
        "last_error_at": null,
        "failure_count": 0
      },
      {
        "id": "monthly_icir_rebalance",
        "next_run_time": "2026-05-31T01:00:00+08:00",
        "trigger": "cron[day='last', hour=01]",
        "last_run_status": "unknown",
        "last_error_at": null,
        "failure_count": 0
      }
    ],
    "total_jobs": 5
  },
  "msg": "ok"
}
```

#### 4.2.2 `GET /health/data`

```json
{
  "code": 0,
  "data": {
    "data_latency_days": {
      "daily_quote": 1,
      "financial_data": 0,
      "index_history": 1
    },
    "recent_violations": {
      "daily_quote": {
        "completeness_violation_count": 0.0,
        "price_invalid_count": 0.0
      },
      "financial_data": {
        "pit_violation_count": 0.0
      }
    },
    "window_days": 30
  },
  "msg": "ok"
}
```

#### 4.2.3 `GET /factor-quality/ic-history`（Phase 11 既有端点，v1.1 P1-2 复用 — 不新增）

查询参数（既有）：`strategy`, `factor`, `state`（可选），`start`, `end`, `limit`（默认 500）

响应（既有 `ICRollingHistoryItem` 扁平结构）：

```json
{
  "code": 0,
  "data": {
    "items": [
      {"strategy": "trend", "factor": "macd_hist", "state": "UPTREND",
       "trade_date": "2026-04-30", "ic_value": 0.08, "icir": 0.12, "sample_size": 4200},
      {"strategy": "trend", "factor": "macd_hist", "state": "UPTREND",
       "trade_date": "2026-05-31", "ic_value": 0.06, "icir": 0.09, "sample_size": 4250}
    ]
  },
  "msg": "ok"
}
```

> Grafana / 前端时序面板按 `(strategy, factor, state)` 分组在客户端完成 — Phase 13 不新增端点。

---

## 5. 数据库 schema

### 5.1 Alembic 0012：data_quality_metric 表（P13-B）

```python
"""Phase 13 P13-B 数据质量监控指标表。

包含 DataValidator 错误（PIT 违规 / 完整性不足 / 价格异常 / adj_factor 跳跃）
+ 数据延迟指标。
"""
def upgrade() -> None:
    op.create_table(
        "data_quality_metric",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("data_type", sa.String(32), nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "metric_date", "data_type", "metric_key",
            name="uq_data_quality_date_type_key",
        ),
    )
    op.create_index(
        "idx_data_quality_date_desc",
        "data_quality_metric",
        [sa.text("metric_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_data_quality_date_desc", table_name="data_quality_metric")
    op.drop_table("data_quality_metric")
```

### 5.2 无其他表变更

Phase 13 不修改既有表；`factor_ic_window_state` / `signal` / `candidate_pool` / `pipeline_run` 等保持现状。

---

## 6. 测试用例编号

### 6.1 单元测试（`tests/unit/`）

| 编号 | 文件 | 用途 |
|---|---|---|
| UT-P13-A-01 | `test_metrics_registry.py` | MetricsRegistry 单例 + Counter/Gauge/Histogram 标签合法性 |
| UT-P13-A-02 | `test_metrics_registry.py` | `generate_latest(REGISTRY)` 输出格式 = Prometheus exposition |
| UT-P13-A-03 | `test_scheduler_health.py` | SchedulerHealthService.snapshot() 在 scheduler=None 时返回 running=False + 空 jobs |
| UT-P13-A-04 | `test_scheduler_health.py` | EVENT_JOB_ERROR / EVENT_JOB_EXECUTED 监听后 failure_count / last_run_status 正确累积 |
| UT-P13-B-01 | `test_data_quality_repository.py` | upsert_metric 幂等（同 (date, type, key) 第二次 update value）|
| UT-P13-B-02 | `test_data_quality_repository.py` | get_recent_violations(days=30) 聚合返回结构 |
| UT-P13-B-03 | `test_factor_monitor_persistent.py` | _check_persistent_decay 触发条件：连续 3 月末 icir < 0.05 |
| UT-P13-B-04 | `test_factor_monitor_persistent.py` | _check_persistent_decay 不触发：仅 2 月 / 历史不足 / 中间月 ≥ 0.05 |
| UT-P13-C-01 | `test_secret_filter.py` | TUSHARE_TOKEN 匹配 + 替换为 ***REDACTED*** |
| UT-P13-C-02 | `test_secret_filter.py` | bcrypt hash 匹配 |
| UT-P13-C-03 | `test_secret_filter.py` | Bearer JWT 匹配 |
| UT-P13-C-04 | `test_secret_filter.py` | 普通业务日志不被误杀 |
| UT-P13-C-05 | `test_secret_filter.py` | record.args 被清空（避免格式化时重新插入）|
| UT-P13-D-01 | `test_akshare_adapter.py` | fetch_daily_quotes 字段契约（含 adj_factor=1.0 placeholder）|
| UT-P13-D-02 | `test_data_service_fallback.py` | TushareAdapter 抛 NetworkError → AKShareAdapter 调用 |
| UT-P13-E-01 | `test_ws_websocket_client.py`（前端 vitest）| WebSocketClient 自动重连 5 次后停止 |
| UT-P13-F-01 | `test_notification_health_alert.py` | notify_health_alert 走 HEALTH_ALERT 类型 |

### 6.2 集成测试（`tests/integration/`）

| 编号 | 用途 |
|---|---|
| INT-P13-A-01 | DataValidator 错误自动写入 data_quality_metric 表（真 DB）|
| INT-P13-A-02 | DataValidator is_valid=False 触发 NotificationService.notify_health_alert |
| INT-P13-B-01 | FactorMonitorService.run_monthly 连续 3 月写 factor_ic_window_state 后触发持续告警 |
| INT-P13-B-02 | DailyPipeline CP1 失败 + 数据延迟 > 2 日 → notify_health_alert |
| INT-P13-C-01 | Scheduler 事件监听器接入后 EVENT_JOB_ERROR 持久化到 SchedulerHealthService 内存 |

### 6.3 E2E 测试（`tests/e2e/`）

| 编号 | 用途 |
|---|---|
| E2E-P13-A-01 | GET /api/v1/health/scheduler 无鉴权 → 401 |
| E2E-P13-A-02 | GET /api/v1/health/scheduler 有鉴权 → 200 + jobs 列表结构 |
| E2E-P13-A-03 | GET /api/v1/health/data 鉴权 + 返回结构 |
| E2E-P13-B-01 | GET /metrics 无鉴权 → 200 text/plain，含 quantpilot_pipeline_runs_total |
| E2E-P13-B-02 | GET /api/v1/factor-quality/ic-history 鉴权 + 参数校验 422（v1.1 P1-2：复用 Phase 11 既有端点，原计划新增 /icir-timeseries 取消）|

### 6.4 冒烟测试（`tests/smoke/test_api_live.py`）

| 编号 | 端点 | 期望 |
|---|---|---|
| API-96 | GET /metrics（无鉴权）| 200 text/plain |
| API-97 | GET /api/v1/health/scheduler 无鉴权 | 401 |
| API-98 | GET /api/v1/health/scheduler 有鉴权 | 200 + running/jobs |
| API-99 | GET /api/v1/health/data 鉴权 | 200 + data_latency_days |
| API-100 | （v1.1 P1-2 释放）— 原 /icir-timeseries 取消，Phase 11 既有 API-? `/ic-history` 已覆盖 series 数据消费 | — |
| API-101 | （v1.1 P1-2 释放）— 同上 | — |
| API-102 | WS /api/v1/pipeline/progress 连接 | 101 Switching Protocols（或 httpx-ws 库测试）|
| API-103 | WS /api/v1/backtest/{task_id}/progress 连接 | 同上 |
| API-104 | GET /metrics 含 quantpilot_signals_generated_total 标签 | 字符串包含 |
| API-105 | GET /api/v1/health（基础）扩展后 | 200 + status="ok"（保持向后兼容）|

> **冒烟前置**：执行前先全量跑一次 API-1~95 建立基线；如 API-67/73/83 失败先排查（见 §1.3）。

---

## 7. 验收基线

### 7.1 指标暴露

| 项 | 通过标准 |
|---|---|
| `/metrics` 端点返回 200 + text/plain | curl 验证 |
| 7 个 Counter + 3 Gauge + 2 Histogram 全部在 registry 注册 | UT-P13-A-01 |
| 真机一次 daily_pipeline 后 metrics 含 `quantpilot_pipeline_runs_total{status="success"} ≥ 1` + `quantpilot_signals_generated_total{type=...} ≥ 1` | 集成测试 + 真机抽测 |

### 7.2 调度器健康

| 项 | 通过标准 |
|---|---|
| `/health/scheduler` 返回所有注册 Job（daily_pipeline / monthly_icir_rebalance / monthly_attribution / quarterly_index_components / stop_loss_warn）| E2E-P13-A-02 + 真机 5 个 jobs |
| scheduler 未启动时返回 running=false + 空 jobs 列表（不报错）| UT-P13-A-03 |
| EVENT_JOB_ERROR 后 failure_count + last_run_status 正确累积 | UT-P13-A-04 |

### 7.3 日志 SecretFilter

| 项 | 通过标准 |
|---|---|
| 日志含 TUSHARE_TOKEN / JWT Bearer / bcrypt hash 时被替换 ***REDACTED*** | UT-P13-C-01~03 |
| 业务日志（ts_code / trade_date / count 等）不被误杀 | UT-P13-C-04 |
| 真机抽查 logs/quantpilot.log 无敏感字段泄漏 | 手动 grep 验收 |

### 7.4 数据质量持久化

| 项 | 通过标准 |
|---|---|
| DailyPipeline CP1 完成后 `data_quality_metric` 表有当日 daily_quote / financial_data 行 | INT-P13-A-01 |
| validator errors 非空时 details JSONB 含 errors 列表 | INT-P13-A-01 |
| `/health/data` 返回近 30 日 recent_violations 聚合 | E2E + 真机 |

### 7.5 因子衰减持续告警

| 项 | 通过标准 |
|---|---|
| 连续 3 月末 icir < 0.05 → 触发 notify_factor_alert("factor_decayed_persistent") | UT-P13-B-03 + INT-P13-B-01 |
| 仅 2 月 / 单月低 icir / 中间月反弹 → 不触发 | UT-P13-B-04 |

### 7.6 AKShare 降级

| 项 | 通过标准 |
|---|---|
| TushareAdapter.fetch_daily_quotes 抛异常 → AKShareAdapter 被调用 + 日志可见 | UT-P13-D-02 |
| AKShare 失败 → notify_health_alert("data_source_unavailable") | INT-P13-A-02 变体 |
| `quantpilot_data_source_fallback_total{status="success"}` Counter 在降级成功时递增 | metrics 端点抽查 |

### 7.7 WebSocket 前端消费

| 项 | 通过标准 |
|---|---|
| WS /pipeline/progress 后端 + 前端订阅链路打通 | 手动验收：触发 daily_pipeline 后 PipelineView 实时显示 CP1~CP6 |
| WS /backtest/{id}/progress 前端订阅替代轮询 | 手动验收：BacktestRunView 进度条流畅更新 |
| WebSocketClient 自动重连 5 次后停止 | UT-P13-E-01 |

### 7.8 监控 Stack

| 项 | 通过标准 |
|---|---|
| `docker compose --profile monitoring up` 启动 Prometheus + Grafana | 手动验收 |
| Grafana 自动加载 quantpilot dashboard（5 个面板）| 手动验收 |
| Prometheus 30s 抓 backend:8000/metrics 成功 | 手动验收 |

---

## 8. DoD（Phase 收尾验收）

### 8.1 测试

- [ ] 单元测试：UT-P13-A-01~04 / UT-P13-B-01~04 / UT-P13-C-01~05 / UT-P13-D-01~02 / UT-P13-E-01 / UT-P13-F-01 全部 PASS
- [ ] 集成测试：INT-P13-A-01~02 / INT-P13-B-01~02 / INT-P13-C-01 全部 PASS（PostgreSQL 容器在线）
- [ ] E2E 测试：E2E-P13-A-01~03 / E2E-P13-B-01~02 全部 PASS
- [ ] 全套回归：`uv run pytest tests/unit/ tests/e2e/ tests/integration/ -q` 无新增 fail
- [ ] 冒烟测试：API-96~105 全部 PASS（且 API-1~95 完整复跑 PASS，含 §1.3 推迟的 API-67/73/83 三项排查并修复）

### 8.2 真机层

- [ ] 生产 Docker compose 启动后 `/metrics` 端点可访问
- [ ] daily_pipeline 一次跑完 metrics 端点含 7 个核心 Counter 标签
- [ ] `/health/scheduler` 真机返回 5 个注册 Job 元信息
- [ ] `/health/data` 真机返回近 30 日聚合（5y 真机基线 ≥ 100 行 metric 落库）
- [ ] 前端 PipelineView 触发后实时进度条流畅；BacktestRunView 替代轮询
- [ ] monitoring stack `docker compose --profile monitoring up` 启动正常 + Grafana dashboard 5 面板加载
- [ ] 日志抽查无敏感字段泄漏（grep -i 'TUSHARE_TOKEN\|JWT_SECRET\|ADMIN_PASSWORD_HASH' logs/quantpilot.log → 0 hit）

### 8.3 文档层

- [ ] SDD §15.4 / §15.5 / §16 标注"V1.5-H 已合入 V1.0 Phase 13"
- [ ] system_design.md §9 Phase 13 行更新为 **完成 ✓ YYYY-MM-DD**
- [ ] CLAUDE.md §9 进度表 Phase 13 行同步
- [ ] `docs/guides/deployment.md` 新增 §N 监控栈章节 + 告警接入
- [ ] memory/MEMORY.md 添加 Phase 13 行 + 关联 memory 文件
- [ ] memory/v1_finalize_deferred_items.md "Phase 13 启动时需查的预存在冒烟回归"段落处置标记

### 8.4 Phase 13 收尾必检（CLAUDE.md §5 收尾核查）

- [ ] 本 Phase 设计文档中所有模块（P13-A/B/C/D/E/F）已交付（对照 §1.1）
- [ ] 未交付模块（如有）已显式移入下一 phase 设计文档 + system_design §9
- [ ] `uv run ruff check src/ tests/` 输出 **0 error**
- [ ] 新增 5 REST 端点 + 1 WS 端点已在 `tests/smoke/test_api_live.py` 覆盖（API-96~105）
- [ ] 集成测试通过（先启动容器 `docker compose -f docker-compose.dev.yml up -d db redis && uv run alembic upgrade head`）
- [ ] 检查是否有新经验需要写入 CLAUDE.md（特别是 prometheus_client 接入 + asyncio scheduler 事件监听器 + WebSocket 重连规范）

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| prometheus_client 单进程 registry vs 多 worker 部署冲突 | 中 | API metrics 标签维度爆炸 | V1.0 单 worker uvicorn；多 worker（V1.5）用 multiprocess mode |
| AKShare 接口签名变动 / akshare 包升级断言变化 | 高 | 降级路径失效 | 降级是 best-effort；失败时 NotificationService 推送；冒烟测试不强依赖 akshare 包安装 |
| WS 长连接 + uvicorn 默认超时 60s | 中 | 进度条断连 | 后端 keep-alive ping 30s；前端 WebSocketClient 自动重连 |
| Grafana dashboard JSON 与 prometheus 字段漂移 | 低 | 面板空白 | dashboard JSON 与 metrics.py 同 commit；变更时同步 |
| SecretFilter 正则误杀业务日志（ts_code 含 6/0 开头） | 低 | 信号 ts_code 在日志中被遮蔽 | 单元测试 UT-P13-C-04 覆盖业务日志保留场景；正则只匹配明显的 token 模式 |
| /metrics 暴露公网（无鉴权）| 中 | 指标泄漏 | 部署指南强调 nginx 配置 `allow internal_subnet; deny all;`；同 OpenAPI 文档 |
| FactorMonitorService 持续告警与 Phase 11 _maybe_alert 重复触发 | 低 | 告警刷屏 | NotificationService.notify 内置 24h dedup（按 notify_type + payload） |
| WS 前端消费替代轮询后 BacktestRunView 出现 race condition | 中 | 进度跳动 | 前端用 Pinia store 集中状态；事件按 task_id + step 单调推进 |

---

## 10. 实施序列

| 阶段 | 任务 | 依赖 |
|---|---|---|
| **P13-启动核查** | (1) ruff baseline + 全套回归 unit/e2e/integration；(2) 冒烟基线 API-1~95 + API-67/73/83 修复；(3) **v1.1 P1-4 必修同批**：Phase 12 评审 P1-2 严格交易日（attribution_service.py lookback）+ P1-1 silent truncation logging 复核 + P1-3 AttributionPanel DisclaimerBanner 复核 | Phase 12 commit `954770c` |
| **P13-A1** | MetricsRegistry + 7 Counter + 3 Gauge + 2 Histogram + UT-P13-A-01~02 | 启动核查 PASS |
| **P13-A2** | SchedulerHealthService + scheduler listener + UT-P13-A-03~04 | P13-A1 |
| **P13-A3** | `/metrics` + `/health/scheduler` + `/health/data` 端点 + E2E-P13-A-01~03 + E2E-P13-B-01 | P13-A2 |
| **P13-A4** | DailyPipeline / SignalService / Tushare / NotificationService 埋点接入 | P13-A1 |
| **P13-B1** | alembic 0012 + DataQualityMetric ORM + DataQualityRepository + UT-P13-B-01~02 | P13-A1 |
| **P13-B2** | DataService.ingest_daily 写 DataQualityMetric + INT-P13-A-01 | P13-B1 |
| **P13-B3** | FactorMonitorService._check_persistent_decay + UT-P13-B-03~04 + INT-P13-B-01 | P13-A1 |
| **P13-B4** | NotificationService.notify_health_alert 类型 + UT-P13-F-01 + INT-P13-B-02 | P13-B1 + P13-B2 |
| **P13-C1** | core/logging_config.py SecretFilter + UT-P13-C-01~05 | P13-A1 |
| **P13-C2** | AKShareAdapter.fetch_daily_quotes + fetch_index_history + UT-P13-D-01 | P13-A1 |
| **P13-C3** | DataService Tushare→AKShare 降级 + UT-P13-D-02 + INT-P13-A-02 | P13-C2 + P13-B4 |
| **P13-D1** | `/api/v1/pipeline/progress` WS 后端 + DailyPipeline 进度上报 | P13-A1 |
| **P13-D2** | frontend `api/websocket.ts` + UT-P13-E-01 | P13-D1 |
| **P13-D3** | PipelineView + BacktestRunView 接入 WS | P13-D2 |
| **P13-E1** | docker-compose.monitoring.yml + prometheus/grafana 配置 + dashboard JSON | P13-A3 |
| **P13-E2** | deployment.md §N 监控栈章节 | P13-E1 |
| **P13-F1** | API-96~105 冒烟 + 文档同步（SDD / system_design / CLAUDE.md / memory）| 全部前置 |
| **P13-F2** | 收尾核查 + commit | P13-F1 |

**估算**：~8-12 pd（参照 system_design §9 区间）

**关键 commit 节点**：
- C1：A1+A2+A3+A4（指标暴露 + 调度器健康，可独立验收）
- C2：B1+B2+B3+B4（数据质量 + 因子衰减）
- C3：C1+C2+C3（日志过滤 + AKShare 降级）
- C4：D1+D2+D3（WebSocket 前端消费）
- C5：E1+E2（监控 stack）
- C6：F1+F2（冒烟 + 文档同步 + 收尾）

---

> **依据本设计文档进入实施前的最后核查**（CLAUDE.md §5 启动核查最终一关；v1.1 修订后）：
> 1. 本设计文档列出 **3 个新增 REST 端点**（`/metrics` / `/health/scheduler` / `/health/data`；v1.1 P1-2 取消 `/factor-quality/icir-timeseries`，复用 Phase 11 既有 `/ic-history`）+ 1 个 WS 端点（`/pipeline/progress`）+ 1 个表（`data_quality_metric`）+ 1 个 ORM（`DataQualityMetric`）+ 5 个 Service（`MetricsRegistry` + `SchedulerHealthService` + `DataQualityRepository` + `SecretFilter` + `FactorMonitorService._check_persistent_decay`）+ 2 个 Adapter 方法补全（`AKShareAdapter.fetch_daily_quotes` + `fetch_index_history`）+ 1 个 Adapter 降级（`DataService.ingest_daily` Tushare→AKShare）+ **1 个 main.py lifespan 补丁**（v1.1 P1-1：Redis 客户端实例化）+ 监控栈（`docker-compose.monitoring.yml` + `infra/prometheus/` + `infra/grafana/`）。
> 2. 全部归属 Phase 13，无孤儿模块、无孤儿端点（v1.1 P1-3 已修：`FactorICRepository.get_recent_aggregates` 复用 Phase 11 既有方法）。
> 3. system_design §9 Phase 13 行 scope 与本文档 §1.1 完全对齐，**无需先回写 §9**（v1.1 修订仅减项 + 内部模块表调整，不变更外部 scope）。
> 4. **Phase 12 实施评审残留处置（v1.1 P1-4 修订后）**：P1-1（logging 复核）+ P1-2（严格交易日 lookback 必修）+ P1-3（AttributionPanel 复核）已移至 §1.3 启动核查阶段同批处置；剩余 6 项 P2 推迟 Phase 14，满足 CLAUDE.md §11 充分理由（与 R14-P2-4 ICIR 窗口同源 / batch 预案与历史回算同批 / limit 待数据量到位评估）。
