# 交易日历持久化 — 设计 + 代码评审报告

> **日期：** 2026-06-01
> **范围：** V1.0 收尾期「交易日历入库 + 数据完整性核验基准 + 集成测试红线护栏」改动（工作区未提交）
> **触发：** 生产迁移准备期自审，碰生产库（5432）前把设计与代码一并评审留档
> **评审方式：** 单人自审（code-review skill high effort 角度 + 设计一致性核对）
> **基线：** 596 unit+e2e passed（无 DB，2026-06-01）；ruff 0 error；集成测试本轮未在 :5433 跑

---

## 1. 评审范围（改动清单）

| 层 | 文件 | 说明 |
|----|------|------|
| 迁移 | `backend/alembic/versions/0015_trade_calendar.py` | 新建 `trade_calendar` 表 |
| ORM | `models/market.py` `TradeCalendar` | 全历法日 + is_open |
| 日历 | `data/calendar.py` | `from_repo` / `build_calendar_rows` / `missing_trading_days` |
| 仓储 | `data/repository.py` | `upsert_trade_calendar` / `get_trade_calendar_dates` / `get_trade_calendar_coverage` |
| 服务 | `services/data_service.py` | `bootstrap_trade_calendar` + `DataService.refresh_trade_calendar` |
| 调度 | `pipeline/scheduler.py` | 月度 `trade_calendar_refresh` Job |
| 启动 | `main.py` lifespan | DB 优先自愈加载 |
| 审计 | `scripts/audit_data_integrity.py` | 以日历做差集核验三张日频表 |
| 测试 | `tests/unit/test_trade_calendar.py` + `tests/integration/test_int_trade_calendar.py` | UT-CAL-01~03b + INT-CAL-01~02 |
| 安全 | `tests/conftest.py` `_guard_test_db_or_abort` + `.claude/hooks/auto_test.sh` | 集成测试红线护栏（:5433 闸门） |
| 文档 | SDD v1.4-r1 / system_design v2.0 / dev_setup / deployment | 设计同步 |

---

## 2. 代码评审发现

| 编号 | 等级 | 位置 | 问题 | 失败场景 |
|------|------|------|------|----------|
| **CAL-C-01** | **P1** | `audit_data_integrity.py:74-75` | 无参默认范围取 `trade_calendar` 全 extent（today−6y .. today+30~90d），⊋ 数据实际范围 | 未来端：日历含未来交易日（is_open=true 到 +90d），daily_quote 只到最后采集日 → 今天到 +90d 全部交易日在三表被报"缺"。过去端：日历 today−6y vs 5y 回填起点 2021-05 → 中间 ~230 交易日也被报"缺"。**无参跑 audit（首要用法 / 生产第③步）误报数百缺口 + 退出码 1，即使数据干净** |
| **CAL-C-02** | **P2** | `main.py:108-114` + `scheduler.py` | 启动自愈前瞻 `+30d` 与月度 Job `+90d` 不一致；且部分尾段缺也全量重拉 `cal_start..cal_end`(~6y) | 新部署后、月度 Job 跑之前每天重启：`coverage[1]`(seed+30d) < `cal_end`(now+30d) → 每次重启重拉 6y Tushare trade_cal + 重灌 ~2200 行（启动期无谓网络+DB 负载，且阻塞启动等 Tushare） |
| **CAL-C-03** | **P3** | `models/__init__.py` | `TradeCalendar` 既未进 import 列表也未进 `__all__`（其余 5 个 market 模型均在） | 运行时无碍（import `models.market` 模块即把类注册进 `Base.metadata`），但 `from quantpilot.models import TradeCalendar` 失败、星号导入不全，与既有约定不一致 |
| **CAL-C-04** | **P3** | `audit_data_integrity.py:49-55` | `index_history` 缺口为整日粒度（distinct trade_date，某天任一指数有数据即算 present） | 像真实的 2026-05-29 那种"单指数尾部缺"不会被本工具抓到——与当初发现该缺口的场景正相关，docstring 应标一句"仅整日粒度" |
| **CAL-C-05** | **P3** | `data_service.py` `bootstrap_trade_calendar` | `logger.info("...", extra={...})` 结构化字段，若 formatter 不含这些 key 会静默丢弃；同改动别处用 `%` 格式 | 观测性不一致；自愈拉取的 open_days/rows 计数可能在日志里看不到 |

---

## 3. 设计评审发现

| 编号 | 等级 | 位置 | 问题 | 处置方向 |
|------|------|------|------|----------|
| **CAL-D-01** | **P1** | SDD §5.5 + system_design §9 注 | 核验描述为「各日频表入库交易日 = 日历开市日**全集**」，未限定**差集范围 = 数据实际覆盖区间**（不含未来未采集日 / 早于回填起点的日历日）。设计欠规约即 CAL-C-01 的根 | 设计补一句：差集范围限定在 `[max(日历起, 回填起点), min(日历止, 今日/最后采集日)]`；代码随设计修 |
| **CAL-D-02** | **P3** | system_design 修订历史 | 版本 `v1.8 → v2.0` 大版本跳号，与 v1.7/1.8/1.9（均小步、v1.9 仅早 6 天）节奏不符；单表基础设施加固不足以跳 2.0 | 要么改 `v1.10` / `v1.9-r1` 对齐节奏，要么在条目里明确"V1.0 收尾基线定版"以正当化大跳 |
| **CAL-D-03** | **P3** | system_design §9 注 | `trade_calendar` 挂在 §9 自由浮注「不单列 Phase」，未归属任何 Phase，与 C-5「§3/§5 模块须有且仅有一个 phase 归属」略有张力 | 可接受的非 phase 加固例外；为可追溯，考虑显式挂到 Phase 14（在进行中的数据完整性 phase）或标注"收尾批"，而非无主浮注 |

**一致性核对（已通过，记录在案）：**
- system_design v2.0 §4.1 `trade_calendar` 建表语句与 `alembic 0015` + ORM `TradeCalendar` **逐列一致**（列名/类型/默认/PK/索引 `idx_trade_calendar_open`）。
- SDD §4.5 扩写 + §5.5 完整性行与 system_design §9 注、§4.1 DDL **互洽**。
- deployment.md §10.3 运维命令引用的 `refill_history.py --skip-confirm`、`backfill_candidate_pool.py --force` flag **均真实存在**。
- dev_setup.md 集成测试指引已改为强制 :5433 + DROP 警示，与 conftest 护栏一致。
- conftest `_guard_test_db_or_abort` 用的 `settings` 已在模块顶部导入；护栏仅在集成测试 `_ensure_schema` 触发，不影响 unit/e2e。

---

## 4. 处置追踪表（链 A）

| 编号 | 等级 | 处置 | 状态 | 截止 |
|------|------|------|------|------|
| CAL-C-01 | P1 | `resolve_audit_range` 纯函数把默认范围 cap 到 daily_quote 实际区间（夹在日历 coverage 内）+ UT-CAL-04/04b/04c | ✅ 已修 2026-06-01 | — |
| CAL-D-01 | P1 | SDD §4.5/§5.5 + system_design §1.10 §9 注补「差集范围限定数据实际覆盖区间」规约 | ✅ 已修 2026-06-01 | — |
| CAL-C-02 | P2 | main.py 拆 `required_end`(+30d 触发) / `fill_end`(+90d 填充，与月度 Job 一致)，消除月内重启反复重拉 | ✅ 已修 2026-06-01 | — |
| CAL-C-03 | P3 | `models/__init__` 补 `TradeCalendar` import + `__all__` | ✅ 已修 2026-06-01 | — |
| CAL-C-04 | P3 | audit docstring 标注「index_history 整日粒度」 | ✅ 已修 2026-06-01 | — |
| CAL-C-05 | P3 | `bootstrap_trade_calendar` 日志改 `%` 格式（去 `extra={}`） | ✅ 已修 2026-06-01 | — |
| CAL-D-02 | P3 | system_design 版本 `v2.0` → `v1.10` 对齐小步节奏（含 SDD 交叉引用） | ✅ 已修 2026-06-01 | — |
| CAL-D-03 | P3 | system_design §9 注：`trade_calendar` 显式「归 Phase 14 数据完整性收尾批」 | ✅ 已修 2026-06-01 | — |

---

## 5. 结论与建议

- **P1（CAL-C-01 + CAL-D-01）必须在触碰生产库第③步（无参跑 audit）之前修**——否则审计对干净数据直接误报失败，工具失去意义。设计先改（§4.5/§5.5 + §9 注补范围规约），代码随之。
- **P2 + CAL-C-03** 性价比高、零生产风险，提交前一并修。
- **CAL-C-04/05 + CAL-D-02/03** 为可选清理，不阻断。
- 修完重跑基线 + 起 :5433 临时库跑 INT-CAL-01~02，再进入提交 / 生产步骤。

---

## 6. 复审收口（2026-06-01）

用户选定**全部 8 项**修复。设计先行（SDD §4.5/§5.5 + system_design §1.10 §9 注）→ 代码随之（`calendar.resolve_audit_range` / `audit_data_integrity.py` / `main.py` / `models/__init__` / `data_service.py`）→ 验证：

- **ruff**：0 error（全部改动文件）
- **回归**：`599 passed`（unit+e2e，含新增 UT-CAL-04/04b/04c 锁定范围裁剪）
- **集成**：起独立 :5433 临时库跑 `INT-CAL-01~02` `2 passed`，跑完清理容器
- **CAL-C-01 验证点**：UT-CAL-04 断言无参默认范围 = daily_quote 实际区间（不含早于回填起点的 2020-06，不含未来前瞻 2026-08），假缺口根除。

8 项全部 ✅。下一步：提交（日历改动 + 本评审报告，按文件名 add）→ 生产库 5432 三步（需用户单独确认）。

---

## 7. 生产库执行记录（2026-06-02）

提交后用户逐项确认，对生产栈（`docker-compose.prod.yml`，db 5432 内网无 host 映射）执行三步。

**机制更正**：运行中的 backend 镜像 COPY src 进镜像（非挂载），是 0015 之前构建的 stale 镜像（无新迁移 / 模型 / main.py 自愈 / audit 脚本）——记过的「Docker COPY src 镜像 stale」陷阱。故正确路径为**重建镜像 + 重启**（CMD `alembic upgrade head &&` 自动迁移 + 新 main.py lifespan 自愈灌日历），再跑审计：

| 步骤 | 命令 | 结果 |
|------|------|------|
| 重建 | `compose ... build backend` | 镜像刷新 COPY src/alembic/scripts 层 |
| ① 迁移 | 重启 backend，CMD 自动执行 | `Running upgrade 0014 -> 0015` ✅ |
| ② 灌日历 | 新 main.py lifespan 自愈（Tushare→5432） | SSE **2281 历法日行 / 1516 开市日 / 2020-06-03 → 2026-08-31** ✅ |
| ③ 审计 | `exec backend python scripts/audit_data_integrity.py` | **退出码 0，无缺口** ✅ |

**CAL-C-01 真机验证**：无参默认范围自动夹到 `2021-05-13 → 2026-06-01`（daily_quote 实际区间），未取日历全 extent（含未来前瞻 2026-08 / 早于回填起点 2020-06）——三表（daily_quote / candidate_pool / index_history）各 1224 天全 present，假缺口根除。生产库迁移基线就绪。
