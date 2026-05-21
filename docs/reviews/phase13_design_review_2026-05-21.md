# Phase 13 生产可观测 + 部署评审并入 — 设计评审报告

> **评审日期**：2026-05-21
> **评审对象**：`docs/design/phases/phase13_production_observability.md` v1.0（2026-05-21 落档）
> **评审依据**：
> - CLAUDE.md §5（TDD 启动核查）/ §6（代码规范）/ §10（Phase 文档治理）/ §11（问题处理总原则）
> - SDD v1.4 §15.4 / §15.5 / §16
> - system_design.md §9 Phase 13 行
> - `docs/design/v1_5_roadmap.md` §6 V1.5-H 主题 + §1.x GAP 清单
> - `docs/reviews/v1_overall_review_2026-04-27.md` §6.5（S5-GAP-01/02/03）+ §6.2（S2-GAP-01）+ §8.3（D4-GAP-03）
> - `docs/reviews/phase10_design_review_2026-04-20.md` §3.4（G-1 WS / G-2 AKShare）
> - `docs/reviews/phase12_implementation_review_2026-05-20.md` §8 / §9
> - 代码现状：`backend/src/quantpilot/main.py` / `api/v1/factor_quality.py` / `api/v1/backtest.py` / `data/factor_ic_repository.py` / `pipeline/scheduler.py` / `services/factor_monitor_service.py` / `services/notification_service.py`

---

## 0. 评审结论

**通过 ✓**（4 项 P1 必修 / 5 项 P2 实施期收口 / 7 项 P3 建议）

设计覆盖 V1.5-H 5 项 GAP（S5-GAP-01/02/03 + S2-GAP-01 + D4-GAP-03）+ Phase 10 评审 G-1/G-2（升级 V1.0）+ 因子衰减监控扩展，与 system_design §9 Phase 13 行 scope 完全对齐；推迟项均给出 CLAUDE.md §11 充分理由（OTel / AlertManager / AKShare 财务补全 / APScheduler 集群化均"依赖外部决策"或"大重构跨 phase"）。

**4 项 P1 必修必须在 P13-A1 启动前完成**，主要是孤儿模块 / 端点重复 / 既有方法签名漂移 / 推迟理由不足，30~60 分钟即可收口；P2/P3 进入实施期统一收口。

---

## 1. 评审过程与启动核查

### 1.1 启动核查（CLAUDE.md §5）

| 核查项 | 结果 | 证据 |
|---|---|---|
| 设计文档存在 | ✓ | `docs/design/phases/phase13_production_observability.md` v1.0 |
| system_design §9 Phase 13 行 scope 对齐 | ✓ | §9 引用 V1.5-H 5 项 + G-1/G-2 升级 ✓；§1.3 前序 Phase 推迟项继承清单已覆盖全部 7 项 |
| 推迟项显式注明（§1.2）| ✓ | 8 项推迟均按 CLAUDE.md §11 4 类标准给出充分理由 |
| 模块无孤儿（§1.1 vs system_design §3/§5）| **△ 1 项漂移** | `FactorICRepository.get_recent_icir_state` 在 §3.5.1 引用，但现有 repo 方法名为 `get_recent_aggregates`（详见 P1-3）|
| API 端点无孤儿（§1.1 vs system_design §6）| **△ 1 项重复** | `/factor-quality/icir-timeseries` 与既有 `/ic-history` 业务范围重叠（详见 P1-2）|
| 跨 phase 依赖标注 stub 策略（§10）| ✓ | §1.4 Phase 12 残留 7 项推迟 Phase 14 已标注 |
| 文档未使用外部追踪编号（CLAUDE.md §10 第 5 条）| ✓ | S5-GAP-01/02/03 + S2-GAP-01 + D4-GAP-03 均在 `v1_overall_review_2026-04-27.md` 正式定义，可引用 ✓ |
| Phase 12 实施评审残留处置（§1.4）| **△ 1 项推迟理由不足** | 详见 P1-4 |

### 1.2 范围声明对照

| 维度 | 设计文档 §1.1 范围 | system_design §9 Phase 13 期待 | 偏差 |
|---|---|---|---|
| 指标暴露（P13-A） | MetricsRegistry / /metrics / SchedulerHealthService / /health/scheduler / /health/data + Scheduler 监听器 | S5-GAP-01/02 | ✓ 一致 |
| 数据质量持久化 + 因子衰减（P13-B） | DataQualityMetric / alembic 0012 / Repository / Validator 写回 / FactorMonitor 持续告警 / notify_health_alert | S2-GAP-01 + 因子衰减监控 | ✓ 一致 |
| 日志 SecretFilter + AKShare 降级（P13-C） | SecretFilter / AKShareAdapter 补 2 接口 / DataService fallback | S5-GAP-03 + G-2 | ✓ 一致 |
| WebSocket 前端消费（P13-D） | /pipeline/progress WS + websocket.ts + PipelineView/BacktestRunView 接入 | G-1 | ✓ 一致 |
| 监控 stack + 部署评审（P13-E） | docker-compose.monitoring.yml + prometheus/grafana 配置 + deployment.md §N | D4-GAP-03 | ✓ 一致 |
| 测试 + 冒烟 + 文档同步（P13-F） | UT + INT + E2E 全套 + API-96~105 + 文档四件套同步 + ruff 0 error | CLAUDE.md §5 收尾核查 | ✓ 一致 |

### 1.3 ruff / 测试基线

> 评审仅审设计文档，未跑测试。Phase 12 收尾时已确认 `uv run ruff check src/ tests/` 0 error + 506 unit+e2e PASS（commit `954770c`）—— Phase 13 启动核查需复跑一次建立基线。

---

## 2. P1 必修（4 项；P13-A1 实施启动前完成；总修订时间 ~60 分钟）

### P1-1：Redis 客户端在全代码库未实例化 → P13-D 路径将立即降级为 in-memory no-op

**证据**：
- `main.py:57` `app.state.redis = None` 是唯一赋值；全库 grep `app\.state\.redis\s*=|Redis\(|aioredis|redis_client` 仅命中此一行。
- `api/v1/backtest.py:319~342` WS 端点 `if redis is None: 发 error 关闭`。
- Phase 8 / Phase 10 的 Redis 设计（ConfigService 缓存 + WS 进度推送）**实际在生产从未生效**——一个长期遗留 bug。
- Phase 13 §3.7.1 `/pipeline/progress` WS 复用 `app.state.redis`：在当前代码现状下立即命中 None 分支 → WS 直接关闭 → P13-D 设计意图（前端实时进度条流畅显示 CP1~CP6）100% 无法达成。

**修订方案**：

在 §1.1 P13-D 模块表新增一行：

> | main.py lifespan: Redis 实例化补全 | `main.py` | `from redis.asyncio import from_url` 在 lifespan 创建 client（`app.state.redis = await from_url(settings.redis_url)`）；shutdown 时 `await app.state.redis.aclose()`；REDIS_URL 未配置时保持 None（兼容当前测试环境）|

**充分理由检查（CLAUDE.md §11）**：
- 不属"依赖外部决策"——`REDIS_URL` 已在 `.env.prod.example` 配置 ✓
- 不属"大重构跨 phase"——5 行代码 + 1 import
- 不属"验收标准未定义"——P13-D2 UT-P13-E-01 / 真机 §8.2 验收清晰
- 不属"物理资源约束"——Redis 容器已在 docker-compose.yml

**结论**：**禁推迟**，必须在 P13-D1 完成。否则 P13-D 全部交付物（WS 后端 + 前端 client + PipelineView/BacktestRunView 接入）都是 "纸面交付，实际不工作"。

### P1-2：`/factor-quality/icir-timeseries` 与既有 `/ic-history` 业务范围重叠 → 孤儿端点风险

**证据**：
- `api/v1/factor_quality.py:88` 已有 `GET /factor-quality/ic-history`，支持 `strategy / factor / state / start / end / limit` 过滤，返回 `factor_ic_window_state` 时序聚合行（`ICRollingHistoryItem`），按 trade_date 升序，最多 limit 行（默认 500）。
- Phase 13 §4.1 / §4.2.3 新增 `GET /factor-quality/icir-timeseries`，过滤参数完全相同（`strategy / factor / state / start_date / end_date`），数据源也是 `factor_ic_window_state`，差异仅在响应包装（按 (strategy, factor, state) 分组的 `series[]` vs 扁平 `items[]`）。
- 业务上一个端点足够 — 前端按 (strategy, factor, state) 分组在客户端完成。

**修订方案（二选一）**：

**方案 A（推荐）**：**取消** `/icir-timeseries` 新端点，§4.1 / §4.2.3 / §6.3 E2E-P13-B-02 / §6.4 API-100 / API-101 全部改为扩展 `/ic-history`。Phase 11 设计文档 §9.2 已定义该端点 ✓ 复用即可。

**方案 B**：保留 `/icir-timeseries` 作为 series 分组端点，但 §4.2.3 必须明示"与 `/ic-history` 区别：本端点服务于 Grafana / 前端图表的 series 分组消费，`/ic-history` 服务于 table 展示"，并在 §1.1 P13-A 模块表显式列 "新增 series 分组端点（与既有 /ic-history 并存）"。

**结论**：方案 A 修订量 < 30 分钟。建议方案 A，避免端点维护成本翻倍。

### P1-3：`FactorICRepository.get_recent_icir_state` 是孤儿方法调用

**证据**：
- Phase 13 §3.5.1 设计代码：`history = await self._repo.get_recent_icir_state(strategy_name, factor_name, state, months=3)`
- 现状 `data/factor_ic_repository.py:197` 实际方法签名：`get_recent_aggregates(session, strategy, factor, state, as_of, limit)` —— Phase 11 已有；语义 100% 重叠（按 as_of 倒推近 N 行 ICIR 聚合行），仅参数名不同（months vs limit）。

**修订方案**：

§3.5.1 改写为：

```python
history = await self._repo.get_recent_aggregates(
    self._session,
    strategy=strategy_name,
    factor=factor_name,
    state=state,
    as_of=month_end,
    limit=3,
)
```

或在 §1.1 P13-B 模块表显式列 "FactorICRepository: 新增 `get_recent_icir_state(months=N)` 别名方法" + 单测 UT-P13-B-03 同步更新。

**结论**：方案 1（直接复用现有 method）修订量 < 5 分钟。CLAUDE.md §10 第 3 条孤儿模块禁止 — 必须现在修订设计文档而非实施期 "看到再说"。

### P1-4：Phase 12 评审 P1-2 推迟 Phase 14 缺乏 CLAUDE.md §11 充分理由

**证据**：
- §1.4 写："Phase 12 评审 P1-2 / P2-1 / P2-2 / P2-3 / P2-4 / P2-6 / P2-7 共 7 项已统一推迟 Phase 14 实施期（与 R14-P2-4 ICIR 窗口同源 / batch 分片预案 / limit 评估）。"
- 但 `docs/reviews/phase12_implementation_review_2026-05-20.md` §8 推荐："**下一步建议：** 1. 0.5 pd：本周内完成 P1-1 / P1-2 / P1-3 修订（30 分钟代码 + 测试）"
- P1-2（AttributionService `lookback_months × 30.5` 日历天近似）在 Phase 12 评审定为"30 分钟可修"，§1.4 把它捆绑到 R14-P2-4 严格交易日同批处理。
- 检查 CLAUDE.md §11 4 类充分理由：
  - ❌ 依赖外部决策：30.5 是固定常量，无外部决策依赖
  - △ 大重构跨 phase：与 R14-P2-4 ICIR 窗口改严格交易日**同源**——但 lookback 是月末批查 pool 起点的近似，ICIR 窗口是策略权重计算口径，**两者计算路径独立**，绑定理由弱
  - ❌ 验收标准未定义：Phase 12 评审已给"30 分钟 + 测试"明确口径
  - ❌ 物理资源约束：不依赖 5y 真机数据

**修订方案（二选一）**：

**方案 A（推荐）**：将 Phase 12 P1-2 从 §1.4 移到 §1.3 "前序 Phase 推迟项继承清单"，标注 "Phase 13 启动核查阶段顺带修订（同 API-67/73/83 修复批次）"。修订点：`services/attribution_service.py` lookback 计算改为 `trading_calendar.get_trading_days_back(month_end, n=lookback_months × 21)`，约 30 分钟（含 INT-P12-B-03 用例数据更新）。

**方案 B**：保留 §1.4 推迟，但补强理由——"P1-2 与 R14-P2-4 ICIR 窗口同源是因为 ICIR 历史回算时需复跑 attribution，若 lookback 口径在 R14 才修，两次回算重复"。但此理由仍弱，且违反 CLAUDE.md §11 "推迟不是节省，是债务利息"原则。

**结论**：建议方案 A。Phase 12 评审 P1-1 / P1-3 应同步处置（P1-1 silent truncation logging、P1-3 AttributionPanel DisclaimerBanner 复用）—— 均为 30 分钟内修订项，§1.4 应只保留 P2-1 / P2-2 / P2-3 / P2-4 / P2-6 / P2-7 6 项推迟 Phase 14。

---

## 3. P2 实施期收口（5 项）

### P2-1：`/metrics` 端点公网暴露风险仅靠部署指南软约束

§4.1 标注 "无（nginx 内网限制）"，§9 风险表写 "部署指南强调 nginx 配置"，但 §1.1 P13-E 模块表**没有 nginx 配置项**——仅在 §8.2 真机层验收提到。

**实施期修订**：§1.1 P13-E 表新增一行：

> | nginx `/metrics` location 限制 | `infra/nginx/nginx.prod.conf`（扩展）| 加 `location /metrics { allow 10.0.0.0/8; allow 172.16.0.0/12; allow 192.168.0.0/16; deny all; proxy_pass http://backend:8000/metrics; }`；其他 location 不变 |

避免部署遗漏后 /metrics 含 TUSHARE_TOKEN 调用次数 / 数据延迟 / 用户业务指标全部公网可读。

### P2-2：WS `/pipeline/progress` 公网暴露业务信息

DailyPipeline 进度推送含 `trade_date` / `当前 CP` / `信号数` 等业务字段，敏感度高于 backtest WS 仅推 task_id 进度。设计 §4.1 列无鉴权（"同 backtest WS"），与 backtest WS 模式一致但风险增量未评估。

**实施期修订**：§3.7.1 加段：

> **WS 鉴权说明**：V1.0 同 backtest WS 模式无 JWT；生产 nginx 配置 `location /api/v1/pipeline/progress { allow internal_subnet; deny all; }` 限制内网。前端 PipelineView 仅在登录态下挂 WS（路由守卫 + axios 拦截器）。V1.5-G 评估 WS query token 鉴权。

### P2-3：单月 + 持续告警双触发 dedup 失效

§3.5 注释 "NotificationService.notify 内置 24h dedup（按 notify_type + payload）合并"，但 `notification_service.py:79` 实际 dedup key 含 payload。单月告警 alert_type=`factor_decayed`、持续告警 alert_type=`factor_decayed_persistent`，**payload 字符串不同所以不会去重**。结果：同月末批同一 (strategy, factor) 用户收到 2 条告警。

**实施期修订（二选一）**：

- **方案 A**：§3.5.1 加优先级——"触发 `_check_persistent_decay` 后 `_maybe_alert` 跳过同 (strategy, factor)（标记 `_persistent_already_alerted`）"
- **方案 B**：NotificationService.notify dedup key 改为 `(notify_type, alert_subtype, strategy, factor, calc_month)` 而非全 payload 哈希

建议方案 A（设计层处理，不动 NotificationService）。

### P2-4：DataQualityMetric.metric_value 类型与 metric_key 语义混合

§5.1 列举 metric_key 全是 `*_count`（整数语义），但字段定义 `Numeric(20, 6)`。要么收口 INTEGER，要么补浮点示例。

**实施期修订**：保留 Numeric(20, 6) 前向兼容浮点 metric，§5.1 metric_key 补示例：

> 整数：`completeness_violation_count` / `price_invalid_count` / `pit_violation_count` / `adj_factor_jump_count`
> 浮点：`data_completeness_ratio (0.0~1.0)` / `nan_ratio_*` / `avg_pct_chg_abs`

避免后续误用 INTEGER round-trip 丢失精度。

### P2-5：`/health/data` 端点 latency 计算口径未定义

§4.2.2 响应示例 `daily_quote: 1` 未说明是自然日还是交易日。周末 / 节假日访问会返回 2~3 天延迟即使数据无新交易日产生（正常状态），运维易误判。

**实施期修订**：§3 加 latency 计算口径：

```python
# /api/v1/health.py 内部
last_td = await calendar.get_last_trading_day(today)  # 不含 today 当日
max_quote_date = await repo.get_max_trade_date("daily_quote")
latency_days = (last_td - max_quote_date).days  # 周末 last_td=Fri, 端点 Sun 访问 → latency=0 if Fri quote ingested
```

即 latency 用 `last_trading_day - max(trade_date)`（用 TradingCalendar 而非自然日），周末 / 节假日 latency=0。

---

## 4. P3 建议（7 项）

| 编号 | 章节 | 内容 |
|---|---|---|
| P3-1 | §3.5.1 | 阈值 `0.05` hardcoded，应抽到 `config_defaults.py` 或 `ConfigService.factor_persistent_decay_threshold` |
| P3-2 | §3.7.2 | `progress_pct` 计算公式未给。建议简单均匀（CP1 进=0%, CP1 出=16.7%, CP2 进=16.7%, ..., CP6 出=100%）或注明"实施期决定" |
| P3-3 | §3.1.1 | `PIPELINE_DURATION` buckets `(5, 15, 30, 60, 120, 300, 600, 1800)` — 5s 桶对 daily_pipeline 价值低；可改 `(15, 30, 60, 120, 300, 600, 1800, 3600)`，或单独为各 CP 用不同 buckets |
| P3-4 | §6.4 API-103 | "WS /api/v1/backtest/{task_id}/progress 连接" 是 Phase 8 已有端点冒烟。建议改在 P13-D3 BacktestRunView 接入验收（手动）中体现，或注明"Phase 8 既有端点 + Phase 13 前端消费验证" |
| P3-5 | §3.3.1 | UT-P13-C-04 建议补一行业务日志保留断言：`assert filter("trade_date=2026-05-12 ts_code=600519.SH count=4250 composite_score=99.87")` 不被修改 |
| P3-6 | §1.4 | 引用 Phase 12 评审 P 编号建议明示 `docs/reviews/phase12_implementation_review_2026-05-20.md §章节` 便于回溯（CLAUDE.md §10 第 5 条禁外部追踪编号，但已在评审报告正式定义则可引用，需明示来源避免读者误解）|
| P3-7 | §10 实施序列 | P13-启动核查 阶段建议明示 "顺带处理 Phase 12 P1-1 silent truncation logging + P1-3 AttributionPanel DisclaimerBanner（共 ~60 分钟）" — 与 P1-4 修订对接 |

---

## 5. 设计亮点（值得保留的最佳实践）

1. **§1.3 前序 Phase 推迟项继承清单**：将 V1.5-H 5 项 GAP + G-1/G-2 + Phase 10 评审 G-3 + Phase 12 冒烟基线 API-67/73/83 明确列出，与 system_design §9 / 评审报告 / 前序 Phase 设计文档建立双向追溯。CLAUDE.md §10 治理规则的最佳实践范例。

2. **§1.2 推迟项 8 项每项给充分理由**：OTel / AlertManager / AKShare 财务补全 / APScheduler 集群化 / Pushgateway / API rate limit / 故障注入 / 多账户切换 UI——全部按 CLAUDE.md §11 4 类标准给出充分理由。延续 Phase 11/12 设计文档的良好实践。

3. **§3.1.1 MetricsRegistry 单例 + 模块级常量**：业务 service 通过 import handles 调用 `PIPELINE_RUNS.labels(...).inc()`，无需 service 持有 registry。设计降低了埋点接入心智负担，符合 CLAUDE.md §6 "尽量纯函数" 风格。

4. **§3.5 持续告警与单月告警显式区分**：通过 `_check_persistent_decay` 独立 method + N 月窗口阈值，避免单月异常误报。设计意识到合并风险（虽 P2-3 dedup 实施仍需完善）。

5. **§3.6.1 AKShareAdapter 降级范围严控**：仅补 `fetch_daily_quotes` + `fetch_index_history` 2 类 critical-path 之外的诊断接口；财务 / 分红 / namechange 4 类保持 `NotImplementedError` 推迟 V1.5+。避免实施期范围蔓延（CLAUDE.md §11 "推迟不是节省"反例）。

6. **§9 风险表 8 项 + 缓解措施**：覆盖 prometheus 多 worker / AKShare 包升级断言 / WS 长连接超时 / Grafana JSON 漂移 / SecretFilter 正则误杀 / /metrics 公网暴露 / FactorMonitor dedup 重复 / WS race condition。风险识别比 Phase 11/12 设计完整。

7. **§10 实施序列 6 个 commit 节点划分清晰**：C1（A1+A2+A3+A4）/ C2（B1+B2+B3+B4）/ C3（C1+C2+C3）/ C4（D1+D2+D3）/ C5（E1+E2）/ C6（F1+F2）—— 每个 commit 都可独立验收，符合 Phase 11/12 的小步快跑节奏。

---

## 6. Phase 13 启动前必修清单（与 §1.4 + 本评审 P1 合并）

| 编号 | 修订内容 | 时间 | 责任 |
|---|---|---|---|
| Phase 13 启动核查 | API-1~95 完整冒烟基线 + API-67/73/83 排查修复（§1.3）| ~30 分钟 | Phase 13 实施前 |
| P1-1 | main.py lifespan: 实例化 Redis 客户端（settings.redis_url 配置时）| ~15 分钟 | P13-D1 起点 |
| P1-2 | §4.1/§4.2.3/§6.3/§6.4 `/icir-timeseries` 改复用 `/ic-history`（方案 A）| ~15 分钟 | 设计文档修订 |
| P1-3 | §3.5.1 改用 `get_recent_aggregates(as_of=month_end, limit=3)` | ~5 分钟 | 设计文档修订 |
| P1-4 | §1.4 P1-2 lookback 30.5 → 严格交易日 + Phase 12 评审 P1-1/P1-3 顺带处置 | ~30 分钟（含修订设计 + 实施）| Phase 13 启动核查阶段 |
| ruff baseline | `uv run ruff check src/ tests/` 0 error | ~1 分钟 | 启动前最后一关 |

**总计**：~90 分钟。修订完成后即可进入 P13-A1 实施。

---

## 7. 评审决策

| 项 | 决策 |
|---|---|
| 设计文档是否可作为实施依据 | ✓ 可（4 项 P1 修订 ~60 分钟内完成后）|
| 是否需要 v1.1 版本号 | 视 §1.1 / §3.5.1 / §1.4 修订幅度决定。**建议 v1.1**，理由：P1-1 改 §1.1 模块表 + P1-2 改 §4 端点设计 + P1-3 改 §3.5 代码示例 + P1-4 改 §1.3/§1.4 推迟范围，4 处文档实质变更 |
| 是否阻塞 Phase 14 启动 | ✗ 不阻塞 — Phase 13 与 Phase 14（账户资金链 + ICIR 历史回填 + BacktestEngine 真 5 步）模块独立，可并行规划，但实施序列 Phase 13 在前 |
| 是否需要回写 system_design §9 Phase 13 行 | ✗ 无需 — §1.1 范围与 §9 完全对齐；仅在 Phase 13 收尾时更新 §9 状态为 **完成 ✓ YYYY-MM-DD** |

---

## 8. 修订追踪表

| 编号 | 优先级 | 状态 | 责任阶段 |
|---|---|---|---|
| P1-1 | P1 | ✅ 设计层已收口 2026-05-21（v1.1）— 实施仍在 P13-D1 | P13-D1 启动前 |
| P1-2 | P1 | ✅ 已收口 2026-05-21（v1.1：取消 /icir-timeseries，复用 /ic-history）| 设计 v1.1 |
| P1-3 | P1 | ✅ 已收口 2026-05-21（v1.1：§3.5.1 改用 get_recent_aggregates）| 设计 v1.1 |
| P1-4 | P1 | ✅ 已收口 2026-05-21（v1.1 设计层移动 + attribution_service.py 改 calendar.get_prev_trade_date 严格交易日 + UT-P12-B-06 单测 + 508/508 PASS）| 设计 v1.1 + Phase 13 启动核查 |
| P2-1 | P2 | ⏳ 实施期 | P13-E1 / P13-E2 |
| P2-2 | P2 | ⏳ 实施期 | P13-D1 |
| P2-3 | P2 | ⏳ 实施期 | P13-B3 |
| P2-4 | P2 | ⏳ 实施期 | P13-B1（设计文档同步）|
| P2-5 | P2 | ⏳ 实施期 | P13-A3 |
| P3-1~7 | P3 | ⏳ 实施期 | 对应章节 |

---

## 9. 评审者建议下一步

1. **设计 v1.1 修订**：合并 P1-2 / P1-3 / P1-4 设计层修订 + 修订历史新增 v1.1 行（~30 分钟）
2. **Phase 13 启动核查**：复跑 ruff + 全套回归（unit + e2e + integration 隔离测试 DB），处理 Phase 12 评审 P1 残留 + API-67/73/83 排查（~60 分钟）
3. **进入 P13-A1**：MetricsRegistry + UT-P13-A-01~02

> **CLAUDE.md §11 再确认**：本评审 4 项 P1 均按"现在的问题现在处理"原则要求即时修订；推迟到 Phase 14 / V1.5 的均给出 CLAUDE.md §11 4 类标准充分理由。推迟不是节省，是债务利息。
