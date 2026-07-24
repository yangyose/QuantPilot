# V1.5-A 设计评审报告（回测引擎深化 + 监控增强 + 市场宽度 + 财务 PIT）

- 评审日期：2026-07-24
- 评审对象：`docs/design/phases/v1_5_a_backtest_monitoring.md` v1.0（初版设计草案）
- 评审依据：
  - roadmap `docs/design/v1_post_release_roadmap.md` §2.1 / §3 / §4.5 / §5 / §6 V1.5-A 行
  - SDD `docs/spec/QuantPilot_SDD.md` §7.7.5 / §5.1 PIT / §6.3 市场环境
  - SDD 外部评审 `docs/reviews/SDD_review_outside_2026-04-22.md` §3.4 / §5.1 / §5.2
  - Phase 13 实施评审 `docs/reviews/phase13_implementation_review_2026-05-22.md` §8.2
  - Phase 8 设计 `docs/design/phases/phase8_backtest.md` §2.1
  - 现网代码：`engine/backtest/engine.py` / `engine/market_state.py` / `models/business.py` / `models/market.py` / `models/system.py` / `api/v1/backtest.py`
- 评审范围：设计文档五子批（A1-A5）的 scope 归属、交叉引用一致性、与现网代码契约的可实现性、CLAUDE.md 宪法（C-1/C-4/C-5 + §5.4 推迟三链 + §5.5 编号规约）符合性
- 基线说明：本评审为**设计文档评审**，不跑测试（无实现代码）；代码引用均对现网 `main` 分支实证。

---

## 0. 评审结论

**有条件通过 ✓**——设计结构清晰、五子批 scope 全部在 roadmap 已登记、交叉引用（估算 / 优先级 / SDD-EXT 编号 / R13-P3 五项 / NH-NL 公式 / 涨停规则 / data_priority）逐项对上游文档核实一致，实施期不确定点均以 `【设计待定】` 显式标注（符合 C-4 反占位），推迟三链完整、无伪推迟。

**放行条件**：下列 **2 项 P2** 须在实施启动前回写设计文档消除内部矛盾（均为「生产写」标注遗漏，风险=部署清单漏建生产表/漏迁移）：

- **Ra-P2-1**：A3 `breadth_weak` 落地生产 Scorer 需 `market_state_history` 新增列 + alembic 迁移，§4.2/§4.4/§9/§1.2 全部漏登记，且 §1.2 误标 A3「无生产写」。
- **Ra-P2-2**：§1.2 Scope 总览误标 A1「无生产写」，与 §9 迁移表 + §2.2（`backtest_daily_position` 须建于生产供回流展示）自相矛盾。

**P3 4 项**（Ra-P3-1~4）建议实施期一并处理，不阻断启动。

| 等级 | 数量 | 编号 |
|------|------|------|
| P2（放行条件）| 2 | Ra-P2-1 / Ra-P2-2 |
| P3（建议）| 4 | Ra-P3-1 / Ra-P3-2 / Ra-P3-3 / Ra-P3-4 |

---

## 1. 启动核查核对（设计文档 §1.3）

| 核查项 | 设计文档结论 | 评审复核 |
|--------|-------------|---------|
| 不占 system_design §9 Phase 行，沿用 roadmap §6 登记 | ✓ | ✓ 与 V1.5-G 先例一致；roadmap §6 V1.5-A 行确存 |
| A1-A5 全纳入、无推迟子项 | ✓ | ✓ SDD-EXT-02f/06f/09f 完整版归 V2.0（roadmap §5 实证在列）|
| R13-P3-1~5 三链完整 | ✓ | ✓ 链 A=phase13 实施评审 §8.2；链 B=本文档 §5；链 C=roadmap §4.5+§6；五项描述与 roadmap §4.5 逐字一致 |
| 孤儿检查 | ✓ | ✓ 新增模块/端点均标注收尾回写 §3/§6 |
| 推迟三链 | 不新增推迟项 | ✓ §10 三项 V2.0 推迟均 roadmap §5 已在，落点属实 |

---

## 2. 交叉引用核验（通过项，实证留痕）

| 校验点 | 设计文档 | 上游/代码实证 | 结论 |
|--------|---------|--------------|------|
| V1.5-A 总估算 | ~6.5-10 pd | roadmap §6 V1.5-A 行 = 6.5-10 | ✓（但见 Ra-P3-4）|
| A1 = S6-GAP-02 + 滑点 = 3 pd | §1.2 | roadmap §2.1 = 1.5+1.5 pd | ✓ |
| A2 SDD-EXT-02s = P0 / 0.5-1 pd | §3 | roadmap §3 + 外部评审 §5.1(P0) | ✓ |
| A3 SDD-EXT-07 = P2 / 1-1.5 pd | §4 | roadmap §3 + 外部评审 §3.4(P2) | ✓ |
| A5 SDD-EXT-03 = P1 / 2-3 pd | §6 | roadmap §3 + 外部评审 §5.2(P1) | ✓ |
| NH-NL 公式 + 0% 分界降级 | §4.1/§4.2 | 外部评审 §3.4 line 123/128-129 逐字 | ✓ |
| 涨停简化版 = 收盘涨停 + 换手率 <1% | §3.2 | 外部评审 §5.1 line 188 | ✓ |
| data_priority 财报3>快报2>预告1 + 子表 4.2.1 | §6.1 | 外部评审 §5.2 line 197-203 | ✓ |
| SDD §7.7.5 四项 P0 已修复（Batch 3）| §2.5 | SDD §7.7.5 line 630-639 全 ✅ + v1.0-r6 | ✓ |
| daily_positions 不持久化 = phase8 §2.1 降级 | §2.1 | phase8 §2.1 line 83 恢复条件「单独持仓明细表」| ✓ A1 兑现该恢复条件 |
| `backtest_task`/`backtest_result` 两表同族 | §2.2 | models/system.py:89/108 | ✓ |
| `POST /backtest/import` 回流端点存在 | §2.2 | api/v1/backtest.py:132 | ✓ |
| `daily_quote.turnover_rate` 存在 | §3.2 | models/market.py:52 Numeric(8,6) | ✓ 单位待定标注恰当 |
| `_execute_signals` BUY 涨停跳过 / SELL 放行 | §3.1 | engine.py:834/847/888-890 | ✓ |
| `MarketStateEngine.determine_raw_state`/`identify` | §4.2/§4.4 | market_state.py:93/129 | ✓ |
| `_get_financials_at`/`_get_market_state` | §4.3/§6.3 | engine.py:566/675 | ✓ |

---

## 3. 评审发现

### Ra-P2-1（P2）A3 `breadth_weak` 生产持久化 + 迁移全链路漏登记，且误标「无生产写」

**证据**：
- `MarketStateRecord` 是 engine 层 dataclass，经 `MarketStateHistory` ORM（`models/business.py:23`，列仅 market_state/trend_strength/adx_value/ma20/ma60/state_changed/description，**无 breadth_weak**）+ `repository.upsert_market_state`（`data/repository.py:673`）持久化，读回经 `market_state_service._orm_to_record`（`services/market_state_service.py:18`）。
- 生产每日管线：`MarketStateService.identify_and_save` 计算+存库 → 下游 Scorer 经 `get_current_state`（`market_state_service.py:117`，**从 DB 取行 → _orm_to_record**）读当前态。故 `breadth_weak` 若只加在 dataclass 而不落库，`get_current_state` 读回即丢失，生产 Scorer **拿不到** breadth_weak。
- 设计文档 §4.2 只述「在 `MarketStateRecord` 加布尔字段 `breadth_weak`」；§4.4 DoD 未列 ORM 列/迁移；§9 迁移表只列 A1/A5；§1.2 Scope 总览 A3「生产写」列 = 「无」。

**影响**：按现设计实施，A3 生产链路断裂（Scorer 读不到 breadth_weak，弱势震荡压制永不生效）或部署时遗漏 `market_state_history` 迁移。且 §1.2「无生产写」误导部署清单——A3 实际含**生产既有表 ALTER**（比 A1 新建表风险更需 C-1 关注）。

**建议**：二选一并回写文档——
- (a) 若 breadth_weak 须持久化（推荐，与现 get_current_state 流一致）：§4.4 DoD 补「`MarketStateHistory` 新增 `breadth_weak` 列 + alembic 迁移 + `_orm_to_record`/`upsert_market_state` 映射」；§9 迁移表加 A3 行；§1.2 A3 生产写改「有（market_state_history ALTER）」。
- (b) 若坚持 breadth_weak 仅瞬态：§4.2/§4.3 须明确生产 Scorer 如何在**不落库**前提下取到 breadth_weak（如同请求内 in-memory 传递，绕开 get_current_state），并说明与现 DB-读取流的兼容。

---

### Ra-P2-2（P2）§1.2 误标 A1「无生产写」，与 §9 迁移表 + §2.2 自相矛盾

**证据**：
- §1.2 Scope 总览 A1「生产写」列 = 「无（本地算力中心 5434）」。
- 但 §2.2：新表 `backtest_daily_position`「本地算力中心 + 生产回流两侧都建」；`GET /backtest/{id}/result` 从该表分页查、`POST /backtest/import` 回流 upsert daily_positions（本地跑完 → 回流生产）。生产 Web 展示结果页 + import 写路径都依赖该表存在于生产。
- §9 迁移表 A1 行本身 = 「生产 alembic upgrade（前向建表，非破坏）」——**与 §1.2 直接冲突**。

**影响**：§1.2 是 scope 速览表（部署排期首读），误标「无生产写」会让部署清单漏掉 A1 的生产建表迁移。虽 §9 正确，但两表冲突须以一处为准。

**建议**：§1.2 A1 生产写改「有（backtest_daily_position 前向建表，回流展示用）」，与 §9 对齐；括注「回测 run 本身不写生产（本地 5434）」以保留原意区分。

---

### Ra-P3-1（P3）A3 回测路径 `_get_market_state` 仅返回 enum，breadth_weak 在回测被丢弃

**证据**：`_get_market_state`（`engine.py:675`）注释（678）「identify_latest 返回 MarketStateRecord | None；本方法抽出 `.market_state` 供 Scorer 使用」——**只返回 `MarketStateEnum`**。breadth_weak 是 record 上与 enum 并列的独立 bool，回测 Scorer 只拿到 enum → breadth_weak 在回测链路丢失。§4.3 述「`_get_market_state` 需扩展算 NH-NL 传入 identify」，但未处理 breadth_weak 如何随之流到回测 Scorer。

**建议**：§4.3/§4.4 补：回测侧 `_get_market_state` 改为返回（enum, breadth_weak）或返回 record，并说明回测 Scorer 权重查找如何消费 breadth_weak（与生产同一压制路径，Ra-P2-1 (a)/(b) 决策后对齐）。

---

### Ra-P3-2（P3）§2.5「保留仍未修复局限」误挂 SDD §7.7.5

**证据**：SDD §7.7.5（line 630-639）列**恰好 4 项**（T+1/quotes 字段/pe_pb+index/RiskChecker），全部 ✅ 已修复（Batch 3）。§2.5「保留：仍未修复的局限（如 A2 之前的涨停一刀切、A5 之前的财务快报缺失）保留条目」——但涨停一刀切/财务快报缺失**不在 §7.7.5 四项内**，它们属 `DISCLAIMER`/`BacktestLimitationsBanner` 文案（v1.0-r4 合规链），非 §7.7.5。

**影响**：实施者按 §2.5 去 §7.7.5 找涨停/快报「待保留条目」会扑空。轻微，§2.5 的 `【设计待定】逐条修复判定表` 实施期可兜底。

**建议**：§2.5 澄清审计范围 = §7.7.5（4 项全删）**+** DISCLAIMER/banner（涨停/快报 caveat 留到 A2/A5 交付后删），二者分列，勿混挂 §7.7.5。

---

### Ra-P3-3（P3）§5.5 编号规约：R13-P3-1~5 属评审报告编号进入设计文档正文

**证据**：CLAUDE.md §5.5 禁「评审报告编号进入设计文档正文」。`R13-P3-1~5` 源自 phase13 实施评审 §8。

**评审判断**：**可接受，倾向不改**——roadmap §编号治理明确「辅助追溯标识保留」并将 R13-P3-1~5 登记为 V1.5-A 子模块 scope 项 ID（§4.5「启动 V1.5-A 时必须把本节作为子模块 scope 纳入」），性质同 SDD-EXT-*（roadmap 背书的跨文档工作项 ID），非 §5.5 所指「一次性评审编号」。若求极致规约洁净，可在 §5 表内为五项定义本地别名（如 A4-1~A4-5）并括注「＝R13-P3-1~5」。**列此项仅为留痕，不作放行条件。**

---

### Ra-P3-4（P3）§1.2 分项 pd 之和（7.6-9.6）≠ 标称总估算（6.5-10）

**证据**：§1.2 分项 A4=1.1 / A2=0.5-1 / A3=1-1.5 / A1=3 / A5=2-3，精确和 = **7.6-9.6 pd**；§1.2 标称「合计 ~6.5-10 pd」（承 roadmap §6）。下界 6.5 < 7.6。

**评审判断**：数值承自 roadmap §6 权威登记，非本文档臆造；但分项与总额不自洽。建议 §1.2 括注「分项精确和 7.6-9.6，标称沿用 roadmap §6 区间」，或回写 roadmap §6 收敛区间。低优先。

---

## 4. 亮点

- §2.1 正确识别 BacktestEngine 严格无 IO 约束（CLAUDE.md §6），流式持久化走**回调 sink 模式**（类比现有 progress_cb）而非引擎内直写库——架构判断准确。
- sink 落库线程模型（`run_coroutine_threadsafe` 回投主 loop vs 独立同步连接）以 `【设计待定】` 标注并要求压测本地 5434 内存峰值，未强行拍板——符合 C-4 反占位 + 反臆断。
- A2 `turnover_rate` 入库单位以 `【设计待定】` 要求实施首步查实际值域定阈值常量——避开 Tushare 百分比/比例陷阱（CLAUDE.md §4.3 同类），NULL 保守跳过 + `logger.warning` 不静默（C-4）。
- A5 明确唯一生产写子批 + C-1 单独确认 + pg_dump 前置 + 本地先验证，运维红线（2GB 内存墙 / 禁生产 POST /backtest/run / 集成测试永不打生产 5432）全程复述——C-1 保护到位。

---

## 5. 建议实施顺序确认

设计文档「A4→A2→A3→A1→A5（先轻后重）」合理：A4/A2 零生产写快速见效，A3 含（修正后）生产表 ALTER，A1 生产建表 + 引擎重构，A5 新表+5y 回填风险最高置末。**修正 Ra-P2-1 后，A3 的「先轻」定位需重估**——A3 实含生产既有表迁移，非纯 Engine 层，部署风险高于设计当前预设，建议 A3 部署单列 C-1 确认。

---

## 6. §8 修订追踪表

| 编号 | 等级 | 处置 | 责任 | 截止 | 状态 |
|------|------|------|------|------|------|
| Ra-P2-1 | P2 | A3 breadth_weak 持久化/瞬态二选一 + 回写 §4.2/§4.4/§9/§1.2 | 主开发 | V1.5-A 实施启动前 | **已处理**（设计 v1.1：选持久化方案 (a)，§4.2 补 MarketStateHistory 加列+ALTER+映射，§1.2/§4.4/§9 改 A3「有生产写」）|
| Ra-P2-2 | P2 | §1.2 A1 生产写「无」→「有（backtest_daily_position 建表）」对齐 §9 | 主开发 | V1.5-A 实施启动前 | **已处理**（设计 v1.1：§1.2 A1 生产写改「有」）|
| Ra-P3-1 | P3 | §4.3/§4.4 补回测侧 breadth_weak 流到 Scorer 的路径 | 主开发 | A3 实施期 | **已处理**（设计 v1.1：§4.3 _get_market_state 改返回 record/元组，§4.4 DoD 加行）|
| Ra-P3-2 | P3 | §2.5 审计范围拆分 §7.7.5（4 删）+ DISCLAIMER/banner（涨停/快报留）| 主开发 | A1 实施期 | **已处理**（设计 v1.1：§2.5 拆两处 + A2/A5 DoD 改指 banner）|
| Ra-P3-3 | P3 | R13-P3 编号规约：保留（留痕）/ 可选定本地别名 | 主开发 | 收尾（可选）| 留痕（评审倾向不改，保留）|
| Ra-P3-4 | P3 | §1.2 括注分项和 7.6-9.6 或回写 roadmap §6 收敛 | 主开发 | 收尾 | **已处理**（设计 v1.1：§1.2 括注分项和 7.6-9.6）|

**说明**：本报告为链 A（历史日志）。放行条件 Ra-P2-1/2 的消费在设计文档本体（链 B 即本 phase 设计文档正文回写）——实施启动前 grep 本报告编号确认逐项落地。无跨 phase 推迟项，故不涉链 C（roadmap）新增。
