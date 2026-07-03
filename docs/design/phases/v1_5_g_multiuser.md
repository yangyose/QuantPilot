# V1.5-G：用户注册 + 多用户数据隔离 + L1/L2/L3 用户层级偏好

> 版本：v1.3（G-4d 管线与账户解耦收口，2026-07-03）
> 状态：实施中（V1.0 RC 已发布 v1.0 tag，实施排期解冻）
> 估算：~9-13 pd（roadmap V1.5-G 原 8-10 pd + L1/L2/L3 分层兑现 ~1-3 pd）
> 依据文档：
> - SDD §1.1（产品定义=面向个人投资者）/ §2（用户层级 L1/L2/L3 定义）/ §9.3（信号解释分层）/ §14（用户配置 user_level 过滤）/ §16（版本路线图）
> - system_design §9 Phase 6 行④「user_level 降级（V1.0 全量可见，V1.5 实现分层）」
> - v1_post_release_roadmap §2.2「多账户 + 权限粒度（3 项 P2）→ V1.5-G」+ §6 主题表 V1.5-G 行
> - 决策来源：本次会话用户拍板（2026-06-26）——完整多用户隔离 / 开放自助注册 / L1-L2-L3 自选偏好 / 先写设计文档 / env admin 迁移为首用户后废弃

---

## 修订历史

| 版本 | 日期 | 修订内容 |
|------|------|---------|
| v1.0 | 2026-06-26 | 初版设计草案。整合 roadmap V1.5-G（S4-GAP-01/02/03）+ 兑现 SDD §2/§9.3/§14 被 V1.0 折衷推迟的 L1/L2/L3 分层。锁定 per-user/shared 边界、user 表 + account.user_id、JWT 带真实身份、ownership 强制、开放注册防滥用基线、env admin 迁移 |
| v1.1 | 2026-06-26 | 设计评审收口（`docs/reviews/v1_5_g_multiuser_design_review_2026-06-26.md` 有条件通过，0 P1 / 2 P2 / 3 P3）：**P2** roadmap 总估算同步 ~56-82→~57-85 pd（§6 合计 + §1 + §6 标题三处）+ SDD §16 V1.5 表新增「用户与账户」行登记多用户范围（C-5 权威源回写补齐）；**P3** SDD §1.1 加多用户前向注 + 本文档 §6.3 补 §14 层级标签 `All→L1` 映射规约 + §11 枚举 env admin 涉及全部文档（CLAUDE.md §4.6/§4.7 + dev_setup + `override_admin_password` fixture）+ §1.3 折入实施启动核查 |
| v1.2 | 2026-06-30 | **实施启动**（V1.0 RC 发布 v1.0 tag → 实施排期解冻）。锁定 2 个【设计待定】：§4.4 注册后**跳登录页手动登录**（不签发 token，为 V1.5-F 邮箱验证留位）；§6.4 止损预警 Job 多用户化**本 phase 内实现**（遍历 active users，站内信本就 per-account，不引入新收件人模型）。§2/§12 同步去除"超阈则推迟"对冲 |
| v1.3 | 2026-07-03 | **G-4d 收口**（实施期用户拍板 2026-07-02/03）。§2 待定项「每日管线的持仓保护」锁定为三路：① 管线解耦只产共享信号（BUY 候选 + 客观 pct_above_sell SELL）；② 读路径 API 期 SignalViewService 叠加 is_holding + 仓位建议（只改响应 dict 不写共享 ORM）；③ 账户私有主动推送（回撤 / 私有 SELL / 加仓 BUY）并入每日 Job 按账户通知（拍板=通知路线，不 per-account 落库）。§6.4 同步展开 Job 三分支实现要点 |

---

## 1. 概述

### 1.1 背景

V1.0（Phase 1~15）是**单用户个人系统**：登录走 `.env` 单管理员（`ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH`），JWT 的 `sub` 恒为管理员名，无 `user` 表，`account_id=1` 在前后端硬编码。这是 SDD §1.1「面向个人投资者」前提下的合理取舍（roadmap S4-GAP-01 原话：「V1.0 单管理员是 SDD 明确范围」）。

本 phase 承担两件交织的事，对应 roadmap 已登记但未实施的 **V1.5-G**：

1. **多用户化（新需求）**：开放自助注册 + 每用户账户层数据完整隔离。消费 roadmap §2.2 的 S4-GAP-01（多账户/权限粒度）+ S4-GAP-03（API rate limit 防刷）+ S4-GAP-02（密码策略，按需）。
2. **兑现 L1/L2/L3 分层（推迟项收口）**：SDD §2 早已定义 L1/L2/L3 三级用户、§9.3 要求信号解释按层呈现、§14 要求设置项按 `user_level` 过滤——但 V1.0 显式降级（system_design §9 Phase 6 行④「V1.0 全量可见，V1.5 实现分层」；v1.7 changelog「V1.5 L1/L2/L3 RBAC 折衷为 V1.0 三级前端折叠」）。实证：当前**根本没有"用户的 level"**——`user_config.user_level` 是"该配置项要求的最低层级"而非用户自身层级；`/settings` 注释直言「V1.0 不过滤 user_level」；前端 L1/L2/L3 折叠人人可点、无 gating。本 phase 引入 `user.level` 后，分层才有承载主体。

**关键定性**：L1/L2/L3 是**自选偏好**（用户可随时切换，控制界面复杂度与解释深度），**不是权限/RBAC**。这贴合 SDD §2「按用户特征适配」原文。因此分层 gating 以"用户当前 level"为依据做内容深浅控制，不构成访问控制边界。

### 1.2 Scope 总览

| 子项 | 主题 | pd | 段落 |
|------|------|-----|------|
| G-1 | `user` 表 + `account.user_id` + 数据迁移（env admin→首用户）| 1.5 | §3 |
| G-2 | JWT 带真实身份 + 注册/登录/me 端点 + 开放注册防滥用 | 2-3 | §4 |
| G-3 | 账户层数据隔离 + ownership 强制（删 account_id 硬编码）| 2-3 | §5 |
| G-4 | L1/L2/L3 `user.level` 偏好 + 信号解释/设置按 level gate | 1-3 | §6 |
| G-5 | 前端：注册页 + 路由守卫 + 层级切换 + 删 account_id=1 | 1.5-2 | §7 |
| G-6 | 测试（UT/INT/E2E/冒烟）+ 收尾 | 1 | §9 |

**合计 ~9-13 pd**。

### 1.3 启动核查（CLAUDE.md §5.1）

| 核查项 | 结论 |
|--------|------|
| 读 system_design §9 本 phase 行 | 本 phase 为 RC 之后首个 V1.5 phase，**不在 V1.0 §9 表内**；权威登记在 roadmap §2.2 + §6 V1.5-G 行。§9 仅做 Phase 6 行④推迟标记翻转（指向本 phase）✓ |
| 模块去向决定 | G-1~G-6 全部纳入本 phase；无推迟子项 |
| grep `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + reviews | 本 phase 不接收上游 `R<N>-P<X>` 评审推迟项（那些已在 Phase 14/15 消费）；本 phase 消费的是 roadmap §2.2 的 **S4-GAP-01/02/03** + §9 Phase 6 ④ 的 **user_level 分层**，均非 R 编号 ✓ |
| 孤儿检查（system_design §3/§5 模块 + §6 端点）| 新增模块：`models/user.py::User`、`services/auth_service.py`（注册/用户管理）；新增端点 `POST /auth/register`、`GET/PATCH /auth/me`。全部在本 phase 落位，将回写 system_design §3/§6 + §9 ✓ |
| 推迟模块引言注明 + §9 更新 + 三链 | 本 phase 不新增推迟项；消费推迟项见上 ✓ |
| C-5 范围变更回写顺序 | 先回写 SDD（§1/§2/§16 多用户+分层）+ system_design §9 Phase 6 ④翻转 + roadmap V1.5-G 展开，**再**实施（本文档即设计产出，实施排 RC 后）✓ |

**排期张力说明**：roadmap 将 V1.5-G 排在 RC 后 M+3~M+4，当前 Phase 15（V1.0 RC 验收）尚未启动。用户决策＝**只先写设计文档锁定需求，实现不插队 RC**。本文档不触发任何实现/迁移。

**设计评审收口（v1.1，2026-06-26）**：`docs/reviews/v1_5_g_multiuser_design_review_2026-06-26.md` 有条件通过（0 P1 / 2 P2 / 3 P3）。P2 已修（roadmap 总估算同步 + SDD §16 登记多用户范围）。P3 三项折入**实施启动核查清单**：
- [ ] （已提前兑现）SDD §1.1 多用户前向注 + §6.3 `All→L1` 层级标签映射规约 + §11 env admin 涉及文档枚举
- [ ] 实施期校验 `user_config.user_level` 存量值均规范化为 `L1/L2/L3`（无 `All`/`L2+` 字面，§6.3）
- [ ] 实施期改 `CLAUDE.md §4.6/§4.7` + `dev_setup.md` + `override_admin_password` autouse fixture 多用户化（§11）
- [ ] 实施期回写 system_design §3/§6（`models/user.py::User`、`services/auth_service.py`、`/auth/register`、`/auth/me` 落位）

---

## 2. 架构边界：per-user vs shared（最关键设计决策）

QuantPilot 数据天然分两层。**本 phase 只隔离账户层，全局计算层保持共享**——这是不可动摇的边界，否则要给每个用户单独跑整条每日管线（算力/架构灾难）。

| 层 | 数据 | 归属 | 理由 |
|----|------|------|------|
| **市场/计算层（共享）** | 行情/财务/指数/`stock_info`/`daily_basic`/复权因子；`candidate_pool`、`signal`、因子评分三层产物、`market_state`、`factor_ic_window_state`(IC/ICIR)、`attribution_history`、`trade_calendar`、`data_quality_metric` | **全员共享一份** | 客观市场计算，由每日管线统一生产；与"谁登录"无关 |
| **账户层（隔离）** | `account` → `position`/`trade_record`/`fund_flow`/`daily_portfolio_value`；回测结果、绩效报告、`in_app_notification`、`user_config`（用户偏好/风控参数） | **每用户独立** | 用户的真实组合/资金/偏好；互不可见 |

**派生语义**：信号/候选池是共享的，但"用户对某信号的视图"（已持仓标记、持仓保护、仓位建议）按**该用户自己的账户**计算。即 `SignalGenerator`/`PositionSizer`/`CandidatePoolManager` 的持仓保护输入，从"全局唯一账户"改为"当前请求用户的账户"。

**每日管线的持仓保护（已锁定，G-4d 实施 2026-07-02/03 拍板）** 每日批 `DailyPipeline` 是全局单次跑（无登录用户上下文），原持仓保护依赖全局唯一账户。多用户化方案分三路：

1. **管线与账户解耦**（G-4d-1）：`generate_for_date` 不再读 account，以空持仓上下文跑 `SignalGenerator`，只产**账户无关共享信号**——BUY 候选 + 客观 `pct_above_sell` SELL（评分跌出市场分位是客观事实，对全体持有者有意义；诚实认知：池=top scorers，共享卖出区信号结构上近乎空，真正 per-user SELL 走第 3 路）。仓位建议 `suggested_pct` 依赖账户总资产/现金，不再随管线持久化。
2. **读路径 API 期叠加**（G-4d-2）：`SignalViewService` 在 `GET /signals` 响应组装期按 `get_current_account_id` 叠加 `is_holding` 标记 + `PositionSizer` 仓位建议——**只改响应 dict 不写 ORM 列**（signals 共享表），账户数据加载失败降级为共享信号可见（不 500 整页）。
3. **账户私有主动推送并入每日 Job**（G-4d-3/4，拍板=通知路线而非 per-account 落库）：G-4c 止损预警 Job（每日 15:05 遍历 active 账户）扩展为按账户 ① 距止损 ≤2% 预警；② 回撤 ≥ `max_drawdown_pct` → `notify_risk_warn(account_drawdown)`；③ `SignalService.evaluate_private_signals` 按账户重跑 SignalGenerator（与管线共用输入加载，止损/加仓逻辑单一实现源，**不落库**）产持仓私有信号——私有 SELL（`hard_stop_loss`/`short_term_z_drop`/`mid_term_icir_flip` → `notify_risk_warn`）+ 加仓 BUY（SDD §10.1 `can_add` → `notify("SIGNAL_BUY")`）。均带 `account_id` 站内信，按账户去重。

止损预警 Job 多用户化细节见 §6.4。

---

## 3. 数据模型（G-1）

### 3.1 `user` 表（新增）

```
user
  id            BIGSERIAL PK
  username      VARCHAR(32)  UNIQUE NOT NULL    -- 登录名，大小写敏感；应用层 lower 唯一校验
  email         VARCHAR(254) UNIQUE NOT NULL    -- 注册必填；唯一
  password_hash VARCHAR(72)  NOT NULL           -- bcrypt
  level         VARCHAR(2)   NOT NULL DEFAULT 'L1'  -- CHECK level IN ('L1','L2','L3')；自选偏好
  is_active     BOOLEAN      NOT NULL DEFAULT true   -- 停用账号（保留数据，禁登录）
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
  updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
  CONSTRAINT ck_user_level CHECK (level IN ('L1','L2','L3'))
```

- `username`/`email` 唯一约束在 DB 层（防并发注册竞态，参照 §14-1 deposit 幂等的 IntegrityError 重查模式）。
- ORM `Mapped[]` Python 类型 + `mapped_column()` SQLAlchemy 类型，继承 `Base`（CLAUDE.md §4.1）。

### 3.2 `account.user_id`（新增 FK）

```
ALTER TABLE account ADD COLUMN user_id BIGINT REFERENCES "user"(id);
-- 迁移回填后置 NOT NULL
```

- 本 phase 不变约束：**1 用户 : 1 账户**（注册时自动建一个空账户）。`user_id` FK **不设 UNIQUE**（为未来多账户/多券商预留），但本 phase 业务保证每用户恰一个账户。
- `position`/`trade_record`/`fund_flow`/`daily_portfolio_value` 已挂 `account_id` FK，**无需加 user_id**——隔离经由 account 的 user 归属链路实现（避免冗余列与不一致风险）。

### 3.3 数据迁移（alembic 0018，env admin → 首用户）

迁移步骤（单事务）：
1. 建 `user` 表。
2. `account` 加 `user_id`（nullable）。
3. 从环境读 `ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH`，插入首个 user：`username=<admin>`、`email=<admin>@local`（占位，可后续改）、`password_hash=<admin hash 原样搬入>`、`level='L3'`（管理员=专业用户）、`is_active=true`。
4. `UPDATE account SET user_id=<首用户id> WHERE user_id IS NULL`（生产现存单账户 id=1 归属首用户）。
5. `account.user_id` 置 `NOT NULL`。

> **env admin 去留**：迁移后登录改为查 `user` 表（§4.2）。`ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH` 在迁移中作为"首用户种子"消费后**废弃**（不再被登录路径读取）。保留 env 变量仅为迁移幂等/可重跑；迁移完成后从 `.env`/部署文档移除（部署演练时执行）。**不保留 env admin 后门**（用户决策）。

> **【降级说明】** 迁移依赖 env admin 存在。若生产 `.env` 缺 `ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH`（理论不会，启动校验已强制），迁移中止并报错，不静默建无主账户。

---

## 4. 认证与注册（G-2）

### 4.1 JWT 带真实身份

- `create_token(token_type, subject: str)`：`sub=str(user_id)`（稳定身份，不用 username——username 未来可改）。access + refresh 均带。
- `decode_token(token, expected_type) -> str`：返回 `sub`（user_id 字符串）。签名/类型/过期校验不变。
- 依赖（`api/deps.py`）：
  - `get_current_user_id() -> int`：仅解 token，无 DB。
  - `get_current_user(session) -> User`：按 id 载 User，校验 `is_active`（停用→401）。供需要 level/邮箱的路由。
  - `get_current_account_id(session) -> int`：解析当前用户的账户 id（DB 查 `account WHERE user_id=:uid`）。**所有账户层路由统一依赖此函数取 account_id**，不再从 query 参数收。

### 4.2 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register` | 开放自助注册：校验 → 建 user（level=L1）+ 自动建空 account → 返回（不自动登录，前端跳登录；或返回 token 自动登录，见 §4.4）|
| POST | `/auth/login` | 改查 `user` 表：按 username 取用户 → `verify_password`（bcrypt 先行）→ 校验 is_active → 签发 access+refresh（sub=user_id）|
| POST | `/auth/refresh` | 不变，校验 refresh → 重签 access（sub 沿用）|
| GET | `/auth/me` | 返回 `{username, email, level}`（当前登录用户）|
| PATCH | `/auth/me` | 改 level（L1/L2/L3 自选）+ 可选改 email/密码（密码改走单独校验）|

注册请求体：`{username, email, password}`。响应遵循 `{code,data,msg}` 项目规约。

### 4.3 开放注册防滥用基线（消费 S4-GAP-03）

开放自助注册必须有防刷，否则被批量注册/撞库。基线：

1. **Rate limit**（S4-GAP-03）：`/auth/register` + `/auth/login` 按 IP 限频（如注册 5 次/小时、登录 10 次/分钟）。技术选型 `slowapi` 或 `fastapi-limiter`（Redis 后端，项目已有 Redis）。
2. **密码强度**（S4-GAP-02 联动）：最小长度 8 + 不接受弱口令（纯数字/常见弱密）。注册 + 改密都校验。
3. **唯一性**：username/email DB 唯一约束 + 注册前 app 层预检；并发竞态走 IntegrityError 重查（返回 409「已被注册」）。
4. **计时侧信道**：登录沿用「先 `verify_password` 再比对」防用户名枚举（CLAUDE.md §7）。注册"用户名/邮箱是否存在"的响应**统一文案 + 统一耗时**，避免枚举。

**【设计待定：邮箱验证】** 开放注册理想上需邮箱验证防虚假注册。但邮件发送基础设施属 **V1.5-F「§1 邮件」**，本 phase 无 SMTP 通道 → **邮箱验证推迟，依赖 V1.5-F**（充分理由：依赖外部/跨 phase 基础设施）。本 phase 基线＝rate limit + 密码强度 + 唯一性；邮箱仅作唯一标识与未来验证锚点。推迟落三链：本文档 §12 + roadmap V1.5-F 行 + system_design §9 V1.5-G 引用。

### 4.4 注册后跳登录页手动登录（实施启动核查锁定，2026-06-30）

**锁定决策 (b)**：注册成功**不签发 token**，前端跳 `/login` 手动登录。理由——为 V1.5-F 邮箱验证关卡留位（验证前不应自动登录），且更稳。`POST /auth/register` 响应仅返回 `{code:0, data:{username, email, level}, msg}`，不含 token。前端 `RegisterView.vue` 注册成功后 `router.push('/login')` + toast 提示。

---

## 5. 数据隔离与 ownership 强制（G-3）

### 5.1 account_id 来源切换

所有账户层路由从「query 传 `account_id`」改为「`Depends(get_current_account_id)` 从 token 推」。涉及：
- `/account/*`（概览/sync/cashflow/deposit/withdraw/dividend/trades/void）
- `/positions/*`
- `/performance/*`
- `/reports/*`（需 `report.account_id`——G-3 加 alembic 0019 建列 + 回填首账户）

`account_id` query/body 参数**移除**（`TradeRecordCreate` / `FundFlowCreate` 删 `account_id`），前端同步删 `account_id=1`。

**`/backtest/*` 决策（实施启动 2026-07-01 锁定）**：仅保留登录门槛，**不做 per-user ownership**。理由：回测结果是「策略×区间×初始资金」的可复现模拟，不读取用户真实持仓，非私有账户数据；且生产已 `backtest_enabled=false` 禁用。`backtest_task` 不加归属列。若未来回测引入账户上下文（如按真实持仓回测），再补 per-user 归属。

### 5.2 ownership 越权防护（安全红线）

凡按 **资源 id** 直接访问的账户层端点（`POST /account/trades/{id}/void`、`POST /account/cashflow/{id}/void`、`PATCH /positions/{id}`、`GET /reports/{id}`），必须校验该行的 `account_id` 属于当前用户账户，否则返回 **404**（非 403——不泄露资源是否存在）。回测按 §5.1 决策仅登录门槛，不在此列。

- 实现：Service 层方法签名带 `account_id`，查询 `WHERE id=:id AND account_id=:account_id`；查无 → 404。Route 层注入 `get_current_account_id`。
- 测试必覆盖：用户 A 持 token 访问用户 B 的 position/trade/report id → 404（§9 INT-ISO-*）。

### 5.3 共享层路由不变

`/signals/*`、`/market/*`、`/factor-quality/*`、`/attribution/*`、`/pipeline/*`、`/data/*`、`/health/*`、`/metrics` 仍是共享数据，**不加 account 过滤**（但仍需登录鉴权）。信号视图的"已持仓标记/仓位建议"在响应组装期按 `get_current_account_id` 叠加（§2 派生语义）。

---

## 6. L1/L2/L3 用户层级偏好（G-4，兑现 SDD §2/§9.3/§14 推迟项）

### 6.1 语义：自选偏好，非权限

`user.level`（L1/L2/L3，默认 L1）由用户自选、随时可改（`PATCH /auth/me`）。它控制**内容深浅**，不是访问控制。SDD §2 原则「默认适配 L1，开放 L2/L3 进阶能力」。

### 6.2 信号解释分层（SDD §9.3）

SDD §9.3 已定义 L1/L2/L3 视图（L1 隐藏 ICIR/正交化残差/Hysteresis；L2/L3 逐层展开）。当前前端 `SignalLineageView` 三层折叠**人人可点、无 gating**。本 phase：
- `GET /auth/me` 暴露 level；前端按 level 默认展开/收起对应层（L1 只见 L1 摘要，L2/L3 默认展开更深层），用户仍可手动展开（偏好不是硬墙）。
- 服务端 `SignalLineageResponse` 字段**不按 level 裁剪**（因是偏好非权限，且裁剪会破坏"手动展开"）——分层在前端呈现层做。此为有意决策，记录在案。

### 6.3 设置项分层（SDD §14）

`/settings` 当前「V1.0 不过滤 user_level」。本 phase：按 `current_user.level` 过滤——只返回 `user_config.user_level <= current_user.level` 的配置项（L1 用户看不到 L2/L3 高级参数）。`user_config.user_level` 语义（该配置项要求的最低层级）已存在，本 phase 接通"用户自身 level"做比较。

> **层级标签映射规约**：SDD §14 设置表「适用层级」列用 `All / L2+ / L3` 表示法，而 `user_config.user_level` 列与 `user.level` 用 `L1/L2/L3` 枚举做 `<=` 比较。映射：**`All` → `L1`**（所有人可见）、`L2+` → `L2`、`L3` → `L3`。实施期 user_config 种子/迁移须把存量 `user_level` 值规范化为 `L1/L2/L3` 枚举（不得残留 `All`/`L2+` 字面，否则字符串 `<=` 比较失效）。

### 6.4 L3 高级功能 gating + 止损预警多用户化

- **L3 高级**（自定义权重 UI 等）：本 phase 仅提供**按 level 显隐的 gating 机制**；具体 L3 权重编辑 UI 属 **V1.5-F「L3 权重 UI」**，本 phase 不实现该 UI，只保证 gating 钩子就位。
- **止损预警 Job 多用户化 + 账户私有主动推送**（承 §2 锁定，G-4c/G-4d-3/4 实施）：原止损预警 Job 依赖全局唯一账户。多用户后改为遍历所有 `is_active` 用户的账户（`list_active_user_accounts()`），每账户依次：① 距止损 ≤2% 预警（`notify_stop_loss_warn`）；② 回撤 `get_current_drawdown ≥ risk_limits.max_drawdown_pct` → `notify_risk_warn(account_drawdown)`（原管线 RiskChecker 回撤 WARN 移此）；③ `evaluate_private_signals` 产持仓私有 SELL（→ `notify_risk_warn(trigger_reason)`）+ 加仓 BUY（→ `notify("SIGNAL_BUY")`）。通知收件人天然随 `account_id` 落各用户站内信；各分支 try/except 隔离，单账户/单条失败不影响其余。不引入新通知收件人模型（站内信本就 per-account）。纳入 G-4 测试（UT 覆盖回撤阈值/私有信号路由/异常隔离，INT 覆盖多用户各自止损独立 + hard_stop_loss/加仓 BUY 浮现）。

---

## 7. 前端（G-5）

- **注册页** `RegisterView.vue` + 路由 `/register`；登录页加"注册"入口。
- **路由守卫**：未登录跳 `/login`；已登录访问 `/login`/`/register` 跳首页。
- **Auth store**：登录后拉 `GET /auth/me`，存 `{username, email, level}`；`level` 驱动 §6 的分层显隐。
- **层级切换**：设置页/个人资料加 L1/L2/L3 选择器 → `PATCH /auth/me`。
- **删 account_id=1 硬编码**：`api/positions.ts`、`OnboardingView.vue` 等所有 `account_id=1`/`account_id:1` 移除（后端按 token 决定）。
- **vue-tsc 0 error** 为构建门槛（CLAUDE.md 既有约定）。

---

## 8. API 端点清单（回写 system_design §6）

| 端点 | 归属 | 隔离 |
|------|------|------|
| `POST /auth/register` | 新增 | 公开（限频）|
| `GET /auth/me` | 新增 | 登录 |
| `PATCH /auth/me` | 新增 | 登录 |
| `POST /auth/login` | 改 DB 查询 | 公开（限频）|
| `/account/*`、`/positions/*`、`/performance/*`、`/backtest/*`、`/reports/*` | 改 account_id 来源 | 账户层隔离 + ownership |
| `/signals/*`、`/market/*`、`/factor-quality/*`、`/attribution/*`、`/pipeline/*`、`/data/*` | 不变 | 共享（仅登录）|

---

## 9. 测试计划（G-6）

**单元（UT）**
- 密码哈希/校验、token 带 subject 往返、level CHECK 约束、密码强度校验、username/email 规范化唯一。

**集成（INT）**
- INT-REG-01 注册建 user + 自动建空 account（user_id 绑定）。
- INT-REG-02 注册重复 username/email → 409（含并发竞态 IntegrityError 重查）。
- INT-AUTH-01 登录走 DB、is_active=false → 401。
- INT-ISO-01 用户 A 不能读用户 B 的 positions/trades/cashflow（ownership → 404/空）。
- INT-ISO-02 `void`/`patch` 按 id 跨用户 → 404。
- INT-MIG-01 迁移回填：env admin → 首用户 + 现存账户归属（alembic upgrade/downgrade 往返）。
- INT-LVL-01 `/settings` 按 level 过滤（L1 看不到 L2/L3 项）。

**E2E**
- 注册 200 / 重复 409 / 弱密 422 / 缺字段 422；登录 200/401；`/auth/me` 200/401；跨用户访问 404。

**冒烟**（`tests/smoke/test_api_live.py`，续接现有 API 编号）
- register/login/me 三条 401/200/409 路径。

**回归门槛**：`uv run ruff check src/ tests/` 0 error；unit+e2e+integration 全绿；vue-tsc 0 error。

---

## 10. DoD

- [ ] `user` 表 + `account.user_id` + alembic 0018 迁移（含 env admin→首用户回填，upgrade/downgrade 往返通过）
- [ ] JWT 带 user_id 身份；`get_current_user_id/user/account_id` 依赖；登录改 DB 查询
- [ ] `POST /auth/register`（限频 + 密码强度 + 唯一）+ `GET/PATCH /auth/me`
- [ ] 全部账户层路由 account_id 来源切 token + ownership 404 防护
- [ ] `user.level` 偏好：信号解释前端按 level 显隐 + `/settings` 按 level 过滤
- [ ] 前端注册页 + 路由守卫 + 层级切换 + 删 account_id=1
- [ ] 测试全绿（UT/INT/E2E/冒烟）+ ruff 0 + vue-tsc 0
- [ ] 文档：SDD §1/§2/§16 + system_design §3/§6/§9 + roadmap V1.5-G 回写一致

---

## 11. 迁移与部署

- alembic 0018（§3.3）。生产执行前**单独确认**（C-1：破坏性/结构变更），先 `pg_dump` 受影响表。
- 部署演练：迁移后从 `.env.prod` 移除 `ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH`（已被首用户消费），更新部署文档登录说明。
- 现存生产单账户（id=1）→ 首用户；现有持仓/成交/资金全部自动归属（经 account.user_id），无数据丢失。

**env admin 登录模型涉及的全部文档（实施期须同步改，勿只改部署文档）**：
- `docs/guides/deployment.md`（登录说明 + env 变量）/ `docs/guides/dev_setup.md`（本地登录/环境）
- `CLAUDE.md §4.6`（登录验证顺序 + `override_admin_password` autouse session fixture 多用户化）/ `§4.7`（`ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH` 环境变量项）
- `.env.prod.example` / `.env`（移除或改注为"仅迁移种子，迁移后废弃"）
- 测试夹具 `override_admin_password`（autouse session fixture）需改为多用户 fixture（创建测试 user 行 + 真实 level）
- phase6/phase9 设计文档视作 V1.0 单用户快照，**不追改**（历史文档）。

---

## 12. 推迟项（三链落点见 roadmap）

| 推迟项 | 充分理由 | 去向 |
|-------|---------|------|
| 邮箱验证（注册防虚假）| 依赖 SMTP 基础设施（V1.5-F §1 邮件）| V1.5-F + 本文档 §4.3 |
| L3 权重编辑 UI | 属 V1.5-F「L3 权重 UI」既定 scope | V1.5-F + 本文档 §6.4 |
| 多账户/多券商（1 用户 N 账户）| 本 phase 锁 1:1；多账户是独立增量 | V1.5-G 后续 / roadmap §2.2 延伸 |
| 密码到期/强制更换（S4-GAP-02 完整版）| 个人/小范围可后置 | roadmap §2.2（本 phase 仅做密码强度，不做到期策略）|

---

> **下一步**：本文档为设计草案。按 C-5，同步回写 SDD（§1/§2/§16）+ system_design（§3/§6/§9 Phase 6 ④翻转）+ roadmap（V1.5-G 展开）后，提交设计评审；实现排期至 V1.0 RC 之后。
