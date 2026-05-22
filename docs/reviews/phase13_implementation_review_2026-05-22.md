# Phase 13 实施评审报告（v1.0）

- 评审日期：2026-05-22
- 评审对象：commits `79eb0df` / `31323c6` / `144336f` / `57c3068` / `cd4f255`（P13-A~F）
- 依据文档：`docs/design/phases/phase13_production_observability.md` v1.1 + `docs/reviews/phase13_design_review_2026-05-21.md` v1.0
- 评审范围：6 段交付 P13-A~F 的代码 / 配置 / 测试 / 部署文档
- 实测基线：
  - `uv run pytest tests/unit/ tests/e2e/ -q` → **541 passed**（与 cd4f255 声称一致 ✓）
  - `uv run ruff check src/ tests/` → **All checks passed**（0 error ✓）
  - integration 未在本评审复跑（CLAUDE.md「禁止在含真实数据的 DB 上跑 pytest integration」+ 测试 DB 在 5433 与生产 5432 隔离原则；commit cd4f255 已记录 118 PASS）

---

## 0. 评审结论

**通过 ✓（P0/P1 补丁批 2026-05-22 完成）** ——
6 项 P0/P1 已全部收口（见 §8 修订追踪表）；6 项 P2 归入 Phase 14 启动核查；
5 项 P3 归入 V1.5-A 监控增强批。**Phase 13 §10.4 验收的"前端实时进度 + 仪表盘耗时 panel"现已生产路径连通**。

补丁批回归基线（2026-05-22）：unit+e2e **552 PASS**（+11 from 541）/ integration **118 PASS** / ruff **0 error** / vue-tsc **0 error**。

### 评审风险等级标记说明

- **P0**：阻断核心功能或暴露生产风险（必须立即修）
- **P1**：核心特性退化为占位（实施层未连通设计意图；本批次内修）
- **P2**：实施期收口（不阻断主路径但留隐患；Phase 14 启动前或 V1.5-A 一并处理）
- **P3**：建议（最佳实践 / 文档同步 / 标签维度优化）

---

## 1. 启动核查

| 项 | 结论 | 备注 |
|----|------|------|
| §9 Phase 13 状态行已同步 | ✓ | "完成 ✓ 2026-05-22 + 28 UT/6 E2E/2 INT/6 冒烟" |
| 设计文档 v1.1 已锁定 + 评审 P1 全收口 | ✓ | 见 `phase13_design_review_2026-05-21.md` §8 修订追踪表 |
| 模块孤儿（system_design §3/§5）| ✓ | P13-A~F 全部归属明确 |
| API 端点孤儿（system_design §6）| ✓ | `/icir-timeseries` 在代码层未实现（评审 P1-2 方案 A 已收口）；`/metrics` `/health/scheduler` `/health/data` 全部接入 |
| Phase 12 评审 P1-2 是否真改为严格交易日 | ✓ | `services/attribution_service.py:88-89` 已用 `calendar.get_prev_trade_date(month_end, n=20*lookback_months)` |
| 跨 phase stub 标注 | △ | `PIPELINE_DURATION` / `BACKTEST_QUEUE_DEPTH` 未标 stub，见 P1-1 |

---

## 2. P0 必修（2 项；阻断核心生产路径）

### P0-1：手动触发的 Pipeline 进度推送完全失效（WS 实际不工作）

**证据**：

- `backend/src/quantpilot/api/v1/pipeline.py:122-128` `POST /pipeline/trigger` 构造 `DailyPipeline(...)` 时**未传入 `redis=request.app.state.redis`**：

```python
pipeline = DailyPipeline(
    session_factory=AsyncSessionLocal,
    adapter=adapter,
    validator=DataValidator(),
    calendar=calendar,
)  # ← 没有 redis= / notification_channel=
background_tasks.add_task(pipeline.run, trade_date)
```

- `backend/src/quantpilot/pipeline/daily_pipeline.py:54` 默认 `self._redis = None`；
- `daily_pipeline.py:73-79` `_publish_progress` 在 `self._redis is None` 时降级 `logger.debug(...)` —— **Redis pubsub 永远不被写入**；
- 影响：`POST /pipeline/trigger` 触发的流水线（前端 PipelineView「立即触发」按钮的核心路径）所有 12 个进度点全部静默；WS `/api/v1/pipeline/progress` 端到端**只能消费调度器（每日 17:00 cron）触发的流水线进度**——但 cron 触发场景下用户已经下班，「实时进度卡片」毫无价值。

**修复（~10 分钟）**：

```python
# backend/src/quantpilot/api/v1/pipeline.py
pipeline = DailyPipeline(
    session_factory=AsyncSessionLocal,
    adapter=adapter,
    validator=DataValidator(),
    calendar=calendar,
    redis=getattr(request.app.state, "redis", None),
    notification_channel=getattr(request.app.state, "wxpusher", None),
)
```

**E2E 补漏**：新增 `E2E-P13-D-02 trigger_pipeline_progress_publishes_via_redis` —— 用 fake redis client mock pubsub publish 断言 `redis.publish` 至少被调用 1 次。

**充分理由检查（CLAUDE.md §11）**：
- ❌ 不属"依赖外部决策" / ❌ 不属"大重构跨 phase" / ❌ 不属"验收标准未定义" / ❌ 不属"物理资源约束"
- **结论：禁推迟，必须本批次修**。

---

### P0-2：AKShare 降级路径在生产场景下永远走 "unavailable"

**证据**：

- `services/data_service.py:229` `ingest_daily` 内部调用 `await self._fetch_daily_quotes_with_fallback(trade_date)` —— **不传 ts_codes**；
- `services/data_service.py:87-89` `_fetch_daily_quotes_with_fallback(self, trade_date, ts_codes: list[str] | None = None)`，未传时为 None；
- `services/data_service.py:112` `df = await self._fallback_adapter.fetch_daily_quotes(trade_date, ts_codes)` —— 传 None；
- `data/adapters/akshare.py:71-77` `if ts_codes is None: raise NotImplementedError(...)`；
- 触发链：Tushare 失败 → `_fallback_adapter.fetch_daily_quotes(td, None)` → NotImplementedError → DATA_SOURCE_FALLBACK status="unavailable" → 调 `notify_health_alert` → `ingest_daily` 主路径 `raise` → per-day session 回滚 → 当日无数据入库。
- 结果：**P13-C 标榜的「Tushare 失败时降级 AKShare」在生产 ingest 路径下永远无法成功**。Tushare 故障期间，数据采集 = 全空（与无 fallback 等价），仅多 1 条「data_source_unavailable」站内信。

**修复方案**：

**方案 A（推荐）**：`_fetch_daily_quotes_with_fallback` 在 fallback 前从 `self._repo.get_active_stock_codes_as_of(trade_date)` 取活股列表传给 AKShare：

```python
if ts_codes is None:
    pit_codes = await self._repo.get_active_stock_codes_as_of(trade_date)
    ts_codes_for_fallback = pit_codes[:1000]  # AKShare 上限 1000，超出标 partial
    logger.warning(
        "akshare_fallback_partial total=%d limit=1000", len(pit_codes),
    )
else:
    ts_codes_for_fallback = ts_codes
df = await self._fallback_adapter.fetch_daily_quotes(trade_date, ts_codes_for_fallback)
```

副作用：AKShare 单股调用 ~50ms × 1000 = 50s，比 Tushare 单 API 慢约 50 倍，但仍 < 90s nginx 默认 timeout（且 ingest 走 `/api/v1/data/ingest/` 已有 900s 长任务 timeout）。

**方案 B**：保留 NotImplementedError，但 §3.6.2 设计文档显式标降级路径"V1.0 仅 Tushare 工作时可用；Tushare 故障时数据停采，等待人工干预"——这与 §3.6 设计意图（数据连续性）矛盾，**不推荐**。

**修复（~30 分钟，方案 A）**：含 `INT-P13-A-03 akshare_fallback_partial_when_universe_capped` 集成测试用例。

**充分理由检查**：
- ❌ 全部 4 项均不满足。**结论：禁推迟，本批次修**。

---

## 3. P1 必修（4 项；核心特性纸面完成但生产路径不连通）

### P1-1：3 个 Prometheus 指标定义却完全未接入业务代码

**证据**（grep `PIPELINE_DURATION` / `BACKTEST_QUEUE_DEPTH` / `DATA_LATENCY` 在 `backend/src/quantpilot/` 内的所有引用）：

| 指标 | metrics.py 声明 | 业务代码 set/observe 处 | Grafana panel 状态 |
|------|-----------------|-------------------------|---------------------|
| `quantpilot_pipeline_duration_seconds` | ✓ Histogram, labels=[step] | **无任何 observe 调用** | "Pipeline 单次耗时 p50/p95/p99" 永远空 |
| `quantpilot_backtest_queue_depth` | ✓ Gauge | **无任何 set 调用** | 仪表盘未含 panel，但 metric exposed |
| `quantpilot_data_latency_days` | ✓ Gauge, labels=[data_type] | **无任何 set 调用** | "数据延迟（天）" 永远空 |

`commit 79eb0df` 自陈："PIPELINE_DURATION 各 CP step（design §10 P13-A4 标注分离实施）/ BACKTEST_QUEUE_DEPTH" 未接入。但 `commit cd4f255`（收尾）声称 "Phase 13 ✓ 完成" 时未补上 → 与 SDD 「设计与实施必须一致」原则相悖。

**影响**：
- `infra/grafana/dashboards/quantpilot_overview.json` 7 panels 中 2 个 panel "Pipeline 耗时 p50/p95/p99" 和 "数据延迟（天）" **冷启动到生产期间全部空白** → 仪表盘可信度直接下降。
- /health/data 端点计算了 `latency` dict 写入 response，但**没有同步 `DATA_LATENCY.labels(data_type=...).set(latency_days)`**，导致 Pull 模式（Prometheus 定时抓 /metrics）拿不到。

**修复（~30 分钟）**：

1. `daily_pipeline.py` 每个 CP 入口启 `time.perf_counter()`，CP 出口 observe：
   ```python
   from quantpilot.core.metrics import PIPELINE_DURATION
   _t0 = time.perf_counter()
   await self._cp1_ingest(run, trade_date)
   PIPELINE_DURATION.labels(step="cp1").observe(time.perf_counter() - _t0)
   ```
   覆盖 CP1/CP2/CP3/Step4/Step5/Step6 6 个 step + `pipeline_total`。
2. `services/backtest_service.py:run` 提交任务时 `BACKTEST_QUEUE_DEPTH.inc()`，任务结束（包括异常分支）`.dec()`。
3. `api/v1/health.py:health_data` 计算完 `latency` dict 后顺手 `DATA_LATENCY.labels(data_type=...).set(days)`；并在 `daily_pipeline.py` CP1 入库成功后 `DATA_LATENCY.labels(data_type="daily_quote").set(0)`（即时反馈，不等下次 /health/data 调用）。

**充分理由检查**：
- ❌ 不属外部决策 / ❌ 不属跨 phase 重构 / ❌ 验收已定义（panel 必须有数据）/ ❌ 不属物理资源约束
- **结论：禁推迟**。Phase 13 设计 §10 已明示这些点，commit 自陈"分离实施"违反 CLAUDE.md §11"伪推迟"判例之一（"Phase X 一起做更高效，但 Phase X 未定"）。

### P1-2：`check_persistent_decay` 在生产路径上无调用点（接入孤儿）

**证据**：

- `grep check_persistent_decay backend/src/` → 仅 `factor_monitor_service.py:510` 定义，无任何 `apply_monthly_rebalance` / scheduler / 调用入口；
- 单测 `test_factor_monitor_persistent.py` 5 个用例覆盖方法语义，但无接入测试；
- 设计 v1.1 §3.5 明示「月末 rebalance 后调用」——`apply_monthly_rebalance` 内确实调用了 `rolling_icir_state`（写 FACTOR_ICIR Gauge），但**没有衔接 `check_persistent_decay`** 来触发 `notify_factor_alert("factor_decayed_persistent")` 通知。

**影响**：因子持续 3 个月衰减时用户**永远不会收到告警**——P13-B 标榜的"持续告警"是空函数。

**修复（~15 分钟）**：

在 `factor_monitor_service.py:apply_monthly_rebalance` 中 `rolling_icir_state` 调用后追加：

```python
if snap is not None:
    await self.check_persistent_decay(
        session,
        strategy=strategy, factor=strategy, state=state,
        icir_now=float(snap.icir),
        notifier=notifier,  # 需要 apply_monthly_rebalance 多加一个 notifier 参数
        as_of=month_end_date,
    )
```

`pipeline/monthly_scheduler.py` 调用 `apply_monthly_rebalance` 时注入 NotificationService。

**E2E 补漏**：`INT-P13-B-02 apply_monthly_rebalance_triggers_persistent_decay_alert` —— 真 DB 注入 3 个月 ICIR < 0.05 数据，跑 `apply_monthly_rebalance` 断言 in_app_notification 表新增 1 行 `notify_type="FACTOR_ALERT" + payload.alert_type="factor_decayed_persistent"`。

### P1-3：DailyPipeline 失败告警走 PIPELINE_FAILURE，但 Phase 13 设计要求走 HEALTH_ALERT

**证据**：

- `pipeline/daily_pipeline.py:541` `_notify_pipeline_failure` 调用 `notifier.notify("PIPELINE_FAILURE", ...)`；
- `services/notification_service.py:185` `notify_health_alert(alert_type, ...)` 设计支持 6 个 alert_type 其中第 1 个就是 **`pipeline_failed`**；
- `_TYPE_PREF_MAP` 中 PIPELINE_FAILURE 未登记（line 268-273 fallback 默认放行）→ 行为上能发出去，但：
  1. 站内信 `notify_type` 列是 `"PIPELINE_FAILURE"` 而非 `"HEALTH_ALERT"`，运维仪表盘按 `notify_type` 统计 health 告警时会丢失这条；
  2. Phase 13 §3.4.1 "4+ 类运维告警统一入口" 在 pipeline 失败路径上**未被使用**——设计意图与实施分离。

**影响**：运维侧"近 7 日健康告警数"看板下钻 `notify_type="HEALTH_ALERT"` 漏掉 pipeline 失败计数（最高频的健康告警）。

**修复（~10 分钟）**：

`daily_pipeline.py:_notify_pipeline_failure` 改用：

```python
await notifier.notify_health_alert(
    "pipeline_failed",
    f"交易日 {trade_date} 流水线异常：{exc!r}",
    payload={"run_id": run_id, "trade_date": str(trade_date),
             "error": type(exc).__name__},
)
```

测试 `test_int_pipeline_failure_notification`（若已有）同步更新断言 `notify_type="HEALTH_ALERT"`。

### P1-4：前端 vite dev server 缺 `ws: true` → 开发环境 WS 永不可用

**证据**：

- `frontend/vite.config.ts:13-18`：`/api` proxy 仅 `target+changeOrigin`，**未配 `ws: true`**；
- 后果：前端 `npm run dev`（vite 5173 端口）→ WebSocketClient 构造 URL = `ws://localhost:5173/api/v1/pipeline/progress` → vite 不识别 WS 升级请求 → 404 / connection reset → PipelineProgressCard 永远显示"WebSocket 连接异常";
- 生产 nginx (`nginx/nginx.prod.conf:109-117`) 已正确配 WS 升级 ✓；
- 但前端独立容器构建用的 `frontend/nginx.conf` 也**缺 WS 升级 location**（line 1-24）；如有人单独跑 frontend 容器测试也会失败。

**影响**：开发同学跑 vite dev 时新建的 PipelineProgressCard 直接报错 → 调试 UX 严重退化。

**修复（~5 分钟）**：

`frontend/vite.config.ts`：
```ts
proxy: {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
    ws: true,                        // ← 加这行
  },
},
```

`frontend/nginx.conf` 追加 WS 升级 location（与 `nginx/nginx.prod.conf:109-117` 对齐）。

---

## 4. P2 实施期收口（6 项）

### P2-1：`_record_validation` 直接读 `repo._session` 私有属性 → 违反 CLAUDE.md §6 Service 层规范

`services/data_service.py:163`：`session = repo._session  # Repository 公开 session 访问受限，使用内部属性`。CLAUDE.md §6 Phase 7 评审 C-02 已规定"Service 层禁止直接访问 self._repo._session"。

**修订**：`DataQualityRepository.upsert_metric` 改为实例方法（持 session），DataService 通过 `MarketDataRepository.upsert_data_quality_metric(...)` delegate；或让 DataService 自己接收 `session`/`session_factory` 引用（与 `attribution_service` 同款无状态 repository 调用法但显式传 session）。

同源问题：`data_service.py:595` `fetch_dividends` 同样 `session = self._repo._session` —— Phase 7 评审 C-02 修过一次，本批次重新写入新代码时**未沿用最佳实践**。

### P2-2：`ingest_daily` 异常分支无 metric 写入（与 commit message 声称"errors 为空时仍写 1 行 metric_value=0"不一致）

`data_service.py:228-260`：daily_quote fetch 在 try 块抛异常时 `_record_validation` **不会被调用**（因为它在 vr 求值之后）。`/health/data.recent_violations` 在该日完全没数据，运维看不到该日 ingest 异常。设计 §3.4.1 应当在 except 分支也写一行 `metric_key="exception_occurred", metric_value=1` 占位。

### P2-3：单月 + 持续告警 dedup 仍有理论失效路径（评审 P2-3 未真正收口）

设计 v1.1 §3.5 标注"P2-3 设计层已落地（持续告警）"，但 `_TYPE_PREF_MAP` 中 `FACTOR_ALERT` 单月 alert 和 `FACTOR_ALERT` 持续 alert 同 `notify_type` —— `_is_duplicate` payload 含 `alert_type`（factor_decayed vs factor_decayed_persistent），所以 24h 内分别去重 OK。但同月末批同 (strategy, factor)，两个 alert_type 各发 1 次 = 用户当日仍收 2 条 → 评审 P2-3 设计层未做"持续告警触发时抑制单月告警"逻辑。

**修订**：`apply_monthly_rebalance` 内"先 check persistent，命中则跳过 maybe_alert" 优先级控制（评审 P2-3 方案 A）。

### P2-4：API_REQUEST_DURATION middleware path 标签未模板化 → 路径基数爆炸隐患

`main.py:170-187`：`endpoint=path`（raw URL，如 `/api/v1/signals/{id}/lineage` → `/api/v1/signals/123/lineage`、`/api/v1/signals/124/lineage` ……）。V1.0 数据量小不显，但累计跑数月后 Prometheus 单 backend instance series 数会快速增长（每个 signal_id 产生独立 time series）。

**修订**：用 `request.scope.get("route").path`（FastAPI route template，如 `/api/v1/signals/{signal_id}/lineage`），或简单按前 4 段截断 `/api/v1/signals/_/lineage`。

### P2-5：WS error 帧与 API 统一响应格式 `{code, data, msg}` 不一致

`api/v1/pipeline.py:151`：`{"error": "Redis 未初始化，进度推送不可用"}` —— 前端 PipelineProgressCard 单独处理这种 schema。建议统一为 `{"code": 503, "data": null, "msg": "..."}`（与 REST 响应一致），减少前端兼容代码。

### P2-6：lifespan shutdown 缺 `await app.state.redis.aclose()`

`main.py:140-144`：yield 后只 `scheduler.shutdown(wait=False)`，未释放 Redis client。Connection pool 由 OS 回收无大问题，但若 hot reload / 多 worker 启停 → 连接泄漏。

**修订**：lifespan finally 段加：
```python
if app.state.redis is not None:
    try:
        await app.state.redis.aclose()
    except Exception:
        logger.warning("redis_close_failed", exc_info=True)
```

---

## 5. P3 建议（5 项）

| 编号 | 范围 | 内容 |
|------|------|------|
| P3-1 | 冒烟测试 | API-101 `assert r.status_code in (400, 404, 405, 426)` —— 404 也通过，区分不了"WS 路由存在"和"路由根本没注册"。建议改为：先 `client.get("/api/v1/pipeline/progress", headers={"Connection": "Upgrade", "Upgrade": "websocket"})`，断言**不是 404**（其余 4xx upgrade-required 视为通过）|
| P3-2 | SecretFilter | 不扫描 `extra={"token": "AT_xxx"}` structured logging extra 字段 —— 影响面小（JSONFormatter 未输出 extra），但建议 SecretFilter.filter 内也扫描 `record.__dict__` 中 string 类型 value，覆盖未来 extra 字段被序列化的场景 |
| P3-3 | 阈值常量化 | `PERSISTENT_DECAY_THRESHOLD=0.05` / `PERSISTENT_DECAY_MONTHS=3` 写死在 FactorMonitorService 上 —— commit 自陈"P3-1 推迟 ConfigService"，但 ConfigService 已是 V1.0 既有基础设施，无 defer 理由；建议同批次入 `factor_monitor_params` config_key |
| P3-4 | TUSHARE_CALLS 接入面 | 仅 `daily_quote` 接口埋点 —— 其他 12 个 Tushare 接口（fina_indicator / namechange / dividend / fina_balance ...）应同样在 `TushareAdapter._call` 内统一计数（labels=interface, status），覆盖 Tushare 限流场景的真实诊断需求 |
| P3-5 | Grafana 仪表盘补 panel | 当前 7-panel Overview 未含「健康告警次数（按 alert_type）」「APScheduler 失败计数（按 job_id）」「DataQualityMetric 异常 trend」三个面板；建议 v1.1 Overview 补 3 个 panel |

---

## 6. 设计亮点（保留 / 推广）

- **SchedulerHealthService 监听器**：EVENT_JOB_EXECUTED / EVENT_JOB_ERROR 累积 in-memory dict 同时同步触发 Counter — 让 in-process 健康摘要 + Prometheus 拉取双重视角一致，避免数据漂移。
- **DataQualityRepository 静态方法 + 显式 session**：与 AttributionRepository / FactorICRepository 同款无状态模式，方便 per-day session 调用，是 Phase 7~12 长期演化出的设计共识。
- **SecretFilter 5 类正则 + 整段替换**：覆盖 TUSHARE_TOKEN / bcrypt / Bearer / WxPusher / REDIS_URL 主要泄漏面，整段替换 ***REDACTED*** 比 mask 中间几位更安全。
- **AKShare fetch_daily_quotes 显式 1000 上限 + ts_code=None 拒绝**：对降级路径做了边界保护（虽然如 P0-2 所述 DataService 调用方未传 ts_codes 让这个保护反而打到自己脚上）。
- **lifespan Redis 从 ping 失败优雅降级**：连接失败时 `app.state.redis = None`，DailyPipeline 自动走 `logger.debug` 降级（评审 P1-1 已收口的设计）。

---

## 7. 充分理由检查汇总（CLAUDE.md §11）

| 等级 | 项 | 4 类充分理由 | 处置 |
|------|---|--------------|------|
| P0 | P0-1 trigger 路径 redis 未注入 | 4/4 不满足 | **禁推迟，本批次修** |
| P0 | P0-2 AKShare ts_codes=None 不可用 | 4/4 不满足 | **禁推迟，本批次修** |
| P1 | P1-1 3 个指标无业务接入 | 4/4 不满足 | **禁推迟，本批次修** |
| P1 | P1-2 check_persistent_decay 接入孤儿 | 4/4 不满足 | **禁推迟，本批次修** |
| P1 | P1-3 PIPELINE_FAILURE vs HEALTH_ALERT | 4/4 不满足 | **禁推迟，本批次修** |
| P1 | P1-4 vite dev WS 缺 ws:true | 4/4 不满足 | **禁推迟，本批次修**（5 分钟）|
| P2 | P2-1 ~ P2-6 | 部分满足"大重构跨 phase"（P2-1 需重构 DataQualityRepository instance api）| Phase 14 启动核查前一批处理 |
| P3 | P3-1 ~ P3-5 | "验收标准未定义"或"非必要改进"| V1.5-A 监控增强批 |

---

## 8. 修订追踪表

| 编号 | 等级 | 处置 | 责任 / 截止 | 状态 |
|------|------|------|-------------|------|
| P0-1 | P0 | trigger_pipeline 注入 `redis` + `notification_channel` | Phase 13 补丁批 / 本周内 | ✅ 2026-05-22 |
| P0-2 | P0 | AKShare fallback 注入 PIT ts_codes（方案 A） | Phase 13 补丁批 / 本周内 | ✅ 2026-05-22 |
| P1-1 | P1 | PIPELINE_DURATION / BACKTEST_QUEUE_DEPTH / DATA_LATENCY 业务接入 | Phase 13 补丁批 / 本周内 | ✅ 2026-05-22 |
| P1-2 | P1 | apply_monthly_rebalance 接入 check_persistent_decay | Phase 13 补丁批 / 本周内 | ✅ 2026-05-22 |
| P1-3 | P1 | _notify_pipeline_failure 改 notify_health_alert | Phase 13 补丁批 / 本周内 | ✅ 2026-05-22 |
| P1-4 | P1 | vite.config + frontend/nginx.conf 加 WS proxy | Phase 13 补丁批 / 本周内 | ✅ 2026-05-22 |
| P2-1 | P2 | DataQualityRepository 改 instance + 取消 repo._session | Phase 14 启动核查 | pending |
| P2-2 | P2 | ingest_daily 异常分支也写 metric | Phase 14 启动核查 | pending |
| P2-3 | P2 | apply_monthly_rebalance 优先级控制（持续告警抑制单月）| Phase 14 启动核查 | pending |
| P2-4 | P2 | API_REQUEST_DURATION 用 route template | Phase 14 启动核查 | pending |
| P2-5 | P2 | WS error 帧改 `{code, data, msg}` 格式 | Phase 14 启动核查 | pending |
| P2-6 | P2 | lifespan 加 redis.aclose() | Phase 14 启动核查 | pending |
| P3-1 | P3 | API-101 冒烟改 Upgrade header 探测 | V1.5-A 监控增强批 | pending |
| P3-2 | P3 | SecretFilter 扫 record.__dict__ | V1.5-A 监控增强批 | pending |
| P3-3 | P3 | factor_monitor_params config_key 收纳阈值 | V1.5-A 监控增强批 | pending |
| P3-4 | P3 | TushareAdapter._call 内统一埋点 | V1.5-A 监控增强批 | pending |
| P3-5 | P3 | Grafana 补 3 个 panel | V1.5-A 监控增强批 | pending |

### 8.1 P0/P1 补丁批交付（2026-05-22）

6 项 P0/P1 一次性合入：
- **P0-1** `api/v1/pipeline.py`：trigger 构造 DailyPipeline 时传 `redis` + `notification_channel` + E2E `test_pl_07_trigger_injects_redis_for_progress_publish` 拦截 DailyPipeline 构造断言 kwargs
- **P0-2** `services/data_service.py::_fetch_daily_quotes_with_fallback`：fallback 前从 `repo.get_active_stock_codes_as_of` 取 PIT 活股，≤ 1000 截取 + partial 警告；3 UT 覆盖（注入/截 1000/explicit 不覆盖）
- **P1-1** `daily_pipeline.py` PIPELINE_DURATION 6 step + pipeline_total observe / `backtest_service.py` BACKTEST_QUEUE_DEPTH inc+dec/finally 兜底 / `health.py` + CP1 入库后双路径 DATA_LATENCY.set；4 UT
- **P1-2** `factor_monitor_service.apply_monthly_rebalance` 新增 `notifier` 参数 + 每个 (state, strategy) snap 后调 check_persistent_decay + best-effort 包 try; `monthly_scheduler.run_icir_rebalance` 注入 NotificationService；2 UT
- **P1-3** `daily_pipeline._notify_pipeline_failure` 从 `notify("PIPELINE_FAILURE", ...)` 改 `notify_health_alert("pipeline_failed", ...)`；1 UT
- **P1-4** `vite.config.ts` 加 `ws: true` + `frontend/nginx.conf` 加 `~ ^/(ws|api/v1/.+/progress)` WS 升级 location（与 nginx.prod.conf 对齐）

回归：unit+e2e **552 PASS**（+11 from 541）/ integration **118 PASS** / ruff **0 error** / vue-tsc **0 error**。

P2 6 项归入 Phase 14 启动核查；P3 5 项归入 V1.5-A。

### 8.2 推迟项前向引用位置（防丢失）

为避免「评审报告归档后推迟项被遗忘」，本节列出每条 pending 项在其他权威文档中的对应位置——
Phase 14 启动 / V1.5-A 启动时，从对应文档 grep 即可定位回本评审报告。

| 推迟项 | 前向引用位置（必读） |
|--------|---------------------|
| P2-1 ~ P2-6（共 6 项）| `docs/design/system_design.md` §9 Phase 14 行第 (7) 子项「Phase 13 实施评审 P2 6 项」 |
| P3-1 ~ P3-5（共 5 项）| `docs/design/v1_5_roadmap.md` §4.5「Phase 13 实施评审 P3 推迟项」+ §6 V1.5-A 行已含 §4.5 |

启动 phase14_account_integrity.md 设计文档时，**§1.3 启动核查清单**必须 grep
`R13-P2-` 验证 6 项全部进入 phase 14 scope；漏列即违反 CLAUDE.md §10 文档治理规则。

---

## 9. 评审决策

| 决策项 | 结论 |
|--------|------|
| Phase 13 §9 行已标"完成 ✓"是否需要回滚 | **否**——保留状态 + 追加 "P0/P1 补丁批 2026-05-22 完成 ✓"（已落地）|
| Phase 14 启动是否阻塞 | **不阻塞**——6 项 P0/P1 已全部收口 |
| 设计文档 v1.1 是否需要 v1.2 | **否**——本评审 6 项 P0/P1 均"实施未对齐设计"，设计文档无修订需求 |
| 修订追踪流程 | ✅ 已完成：§8 修订追踪表已勾选 + CLAUDE.md §9 Phase 13 行已追加 "P0/P1 补丁批 2026-05-22 完成" 时间戳 |

---

## 附录 A：评审范围与方法

- 阅读：commit `79eb0df` / `31323c6` / `144336f` / `57c3068` / `cd4f255` 的 diff stat + 关键文件全文
- 核心文件全读（≥ 90% 代码逐行）：
  - `backend/src/quantpilot/core/metrics.py`
  - `backend/src/quantpilot/services/scheduler_health.py`
  - `backend/src/quantpilot/api/v1/health.py`
  - `backend/src/quantpilot/api/v1/metrics.py`
  - `backend/src/quantpilot/api/v1/pipeline.py`
  - `backend/src/quantpilot/core/logging_config.py`
  - `backend/src/quantpilot/data/adapters/akshare.py`
  - `backend/src/quantpilot/data/data_quality_repository.py`
  - `backend/src/quantpilot/services/data_service.py`
  - `backend/src/quantpilot/services/factor_monitor_service.py`
  - `backend/src/quantpilot/services/notification_service.py`
  - `backend/src/quantpilot/pipeline/daily_pipeline.py`
  - `backend/src/quantpilot/main.py`
  - `backend/alembic/versions/0012_phase13_data_quality.py`
  - `infra/prometheus/prometheus.yml`
  - `infra/grafana/dashboards/quantpilot_overview.json`
  - `nginx/nginx.prod.conf`
  - `frontend/nginx.conf`
  - `frontend/vite.config.ts`
  - `frontend/src/api/websocket.ts`
  - `frontend/src/components/PipelineProgressCard.vue`
  - `backend/tests/smoke/test_api_live.py` Phase 13 section
- Grep 校验：12 个 Prometheus metric 各自的业务接入点；`check_persistent_decay`/`notify_health_alert`/`get_recent_aggregates`/`get_prev_trade_date` 调用方
- 真机校验：本地 `uv run python -c "...generate_latest(REGISTRY)..."` 验证冷启动后 /metrics 输出包含所有 metric 名（HELP/TYPE 头），但 PIPELINE_DURATION/DATA_LATENCY 等无 labels 数据样本行
- 回归基线实测：unit+e2e 541 PASS / ruff 0 error
