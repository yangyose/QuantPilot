# 生产部署记录

> 生产实例：腾讯云 43.134.63.13 · `/home/ubuntu/QuantPilot`
> 由 `scripts/deploy_prod.sh` 自动追加。**每次部署后必须提交本文件。**

## 这个文件为什么存在

生产服务器**不是 git 仓库**（`/home/ubuntu/QuantPilot` 由 `git archive | tar -x` 同步，
只覆盖不带 `.git`）。所以「上次部署的是哪个 commit」在系统里**无处可查**——
2026-08-31 部署 C1+P0 时，确认基线只能靠逐文件 checksum 比对反推，而该信息当时
只存在于一份个人 memory 文件里，换台机器、换个人就没了。

两条补救合起来才闭环：

1. **运行时自报** —— `backend/VERSION` 由部署脚本写入，`GET /health` 读它。
   问一次 `curl https://quant.portableagi.com/health` 就知道生产在跑哪个 sha。
   （此前 `/health` 返回 Phase 10 写死的 `"1.0.0"`，从未变过，问它等于没问。）
2. **历史留档** —— 本文件，进 git，谁都能查、换机器不丢。

## 基线核验方法（服务器无版本戳时的兜底）

⚠️ **必须 `--strip-trailing-cr`**：服务器上的 `.py` 是 **CRLF**（`git archive` 在
Windows 端按 `core.autocrlf` 转换过），而 `git show <sha>:path` 出来的 blob 是 LF
→ **裸 md5/diff 必然全部对不上**，看起来像「服务器不在任何已知 commit 上」。

```bash
ssh qp-tencent "cat /home/ubuntu/QuantPilot/backend/$f" > p.tmp
git show <sha>:backend/$f > b.tmp
diff -q --strip-trailing-cr p.tmp b.tmp     # 相同 = 基线确认
```

CRLF 对 `.py` 无害（Python 照常解析），`.sh` 由 `.gitattributes` 的 `*.sh text eol=lf`
保护，**不要为了"统一行尾"去动生产文件**。

---

## 历史记录

### `2bab523` — 2026-08-31T06:21Z（14:21 CST）

**首条记录，手工执行**（`deploy_prod.sh` 是这次之后才写的，本条按实际过程补录）。

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `22a6f24` —— **实证核验**，非假设：6 个关键文件 `--strip-trailing-cr` 后逐字节一致 |
| 回滚点 | `/home/ubuntu/backups/backend_pre_c1_20260831_141702.tar.gz`（794K）|
| delta | 8 个 commit |
| alembic | 无新迁移，未跑 upgrade |
| 新 env 变量 | 无，未触发运维红线②双写 |
| 结果 | backend healthy / nginx reload OK / `/health` 200 / 保护端点 401 / 日志无 ERROR |
| 部署后资源 | backend RSS 320 MiB、available 981 MB、磁盘 83%（构建前 77%）|

```
2bab523 docs(roadmap): 算法框架体检 §9.1 全部落链 + 新增 V1.5-L；退出域判据补作用域
e798e7a fix(exit): 退出域取全体持仓并集，而非候选池          ← P0
4eb18df chore: 钉死项目解释器 3.12 + 记录系统 Python 是红线守卫的隐藏依赖
be6d6d6 fix(v1.5-c): C1-3 价格窗口按交易日推导                ← C1-3
85df015 feat(v1.5-c): C1-2 风险调整动量（SDD-EXT-08）          ← C1-2
ac069e5 fix(v1.5-c): C1-1 策略硬约束落点统一                   ← C1-1
3b0dee2 fix(v1.5-c): backfill_icir_rebalance 日历缓冲须覆盖 ICIR 回看深度
ae64667 test: 解除限频 e2e 对墙钟的耦合
```

**部署前 before 基线**（用于对比生效判据，2026-08-31 14:16 实测）：

| 判据 | before |
|---|---|
| `momentum.z_raw` 非空 / 当日池 | **0** / 58~64（2026-08-21~28 每一天）|
| `candidate_pool.is_holding=true` | **0 / 88109** |
| 持仓浮亏 | −39.24% / −10.96% / −10.03% / −9.75%（4 只**全部**超 −8%）|
| `SELL/hard_stop_loss` | 累计 4 条，最后一次 **2026-01-08** |
| `SELL/pct_above_sell` | **0 条**（历史从未触发）|

> 该次部署的服务器上**没有** `backend/VERSION`（版本戳机制是本次之后才加的）。
> 下次部署时脚本会因此要求 `--baseline 2bab523`，之后即可自动接续。

### 更早的部署

`22a6f24`（C0 日级 IC，2026-08-19）及更早的部署没有留下机器可读的记录，
散落在各 phase 进度档与 memory 中。**不再往回补**——从本文件起向前有记录即可。

## a9b7378 — 2026-09-02T16:52:41Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `0869e1e` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_a9b7378_20260903_015045.tar.gz` |
| delta | 0 个 commit |

```
a9b7378 fix(ops): 版本戳加到生产实际使用的 Dockerfile.prod——此前从未进入镜像
```

### 本次部署的完整上下文（2026-09-03 CST，手工补录）

脚本自动记录的只有最后一次调用（`0869e1e → a9b7378`）。实际这一晚做了两轮部署
加三项配置变更，全部记在这里——只看上面的表会以为只上了一个 commit。

**第一轮 `2bab523 → 0869e1e`**（4 个 commit）：

```
0869e1e fix(universe): suspend_d 参数名 trade_date + 只认 suspend_type=S
c3c4943 refactor(notify): NotificationService 收敛到 ABC 契约
54aa3fd fix(notify): 「未配置」不再伪装成「发送失败」
397af23 feat(ops): 生产版本管理——运行时版本戳 + 部署记录进仓库 + 部署脚本重写
```

⚠️ **该轮在第 8 步闸门处被拒**：`/health` 报 `"unknown"` 而非 sha。
代码本身已上线（旧代码返回硬编码 `1.0.0`，变成 `unknown` 即证明新代码在跑），
但版本戳读不到文件——`COPY VERSION .` 只加在了 `backend/Dockerfile`，
而 compose 用的是 **`Dockerfile.prod`**。修复见 `a9b7378` 与单测 VER-06。
**闸门做对了**：它拒绝把这次记成成功部署，否则版本戳会永远报 unknown 而无人知。

**第二轮 `0869e1e → a9b7378`**：修复上述问题，`/health` 正常自报 sha。

**三项配置变更（脚本不碰这些，手工执行）**：

| 项 | 前 | 后 |
|---|---|---|
| 服务器 compose | `BACKTEST_ENABLED:-true` / 硬编码 `127.0.0.1:` | **与仓库逐字节一致**（`:-false` / `${HTTP_BIND:-127.0.0.1}`）|
| `.env.prod` WxPusher | 两键为空 | 已填（`AT_` 35 位 / `UID` 32 位）|
| `.env.prod` ADMIN_* | 缺失 | 补空占位（仓库 compose 引用它们；新装时 alembic 0018 要用）|

生效值未变（`BACKTEST_ENABLED=false` 由 `.env.prod` 显式给定，nginx 仍绑 `127.0.0.1:80`）；
改的是**漏配时的默认方向**——运维红线②要求生产开关默认取失效方向。

**历史数据回补**（`is_suspended` 缺陷，见 `docs/reviews/universe_suspension_defect_2026-09-02.md`）：

```sql
UPDATE daily_quote SET is_suspended = false WHERE is_suspended AND amount > 0;
-- UPDATE 1103596，执行后 still_marked = 0，total_rows 6662108 未变
```

- 回滚点：`/home/ubuntu/backups/pre_suspfix_is_suspended_20260903_005705.sql`
  （1,103,597 行含表头 / 33MB / sha256 `e2a4427855267de8`；含受影响行的
  `id, ts_code, trade_date, is_suspended` 旧值，可定点还原）
- 加 `amount > 0` 守卫而非无条件置 false：若真存在零成交行，它不会被动到而是留下暴露。
  执行前重验 `will_fix=1103596 / zero_vol_untouched=0`，与预期精确一致。

**before 基线（2026-09-03 00:2x CST，用于明日对比）**：

| 项 | 值 |
|---|---|
| `/health` | `1.0.0`（无版本戳）|
| 最近管线 | run 240 / 2026-09-02 / SUCCESS / sig=50 |
| `candidate_pool` 9-02 | 69 行，`is_holding=6` |
| `is_suspended` 9-02 | **818 / 5547** |
| `is_suspended` 全表 | **1,103,596 / 6,662,108** |
| `wx_pushed=true` | **0 / 6305**（微信从未成功推送过）|
| `notification_degraded` ERROR / 24h | 53 |
| 内存 | used 1561 MB |

**观察重点（次日 17:30 管线后）**：universe 预计扩约 +17%（约 2276 → 2658），
`composite_pct_in_market` 是相对 universe 的分位 → **每只股票分位全部重算**，
买入清单会明显不同。这是预期内的，不是异常。另需看内存峰值（2GB 机余量薄）
与 WxPusher 是否真发出（`wx_pushed=true` 首次出现）。

---

## 2026-09-03（傍晚）：首个受 `is_suspended` 修复影响的管线 → OOM → 机器升配 → 补跑成功

上一节「观察重点」的结论。**没有部署任何 commit**，本节记的是运行时事件与一次机器变更。

### 1. 17:30 管线被 OOM killer 杀死

| 时刻（CST）| 事件 |
|---|---|
| 17:30:00 | run 241 启动 |
| 17:33:55 | cp1 完成，耗时 **3m55s**（9/2 是 1m20s、9/1 是 1m11s）|
| 17:34:26 | `scoring_universe_phase11: size=3212` |
| 17:45~18:11 | swap 从 1036MB 涨到 **1987/1987MB（顶满）**，可用内存 108~197MB 区间震荡 |
| **18:11:45** | `Out of memory: Killed process 677827 (uvicorn) anon-rss:1409308kB` |
| 18:12:16 | 容器自动重启完成（`RestartCount=1`），站点中断约 **31 秒** |

`pipeline_run` 停在 `RUNNING` / `cp2_scoring_done=false` → 当日 0 信号。

⚠️ 本次 OOM 触发在 anon-rss **1.34 GiB**（1409308 kB），明显低于 7 月那批的 **1.55~1.58 GiB**（1626516~1658356 kB）——
因为 swap 先被吃光，内核已无腾挪空间。**「上次 1.6G 才死」不能当安全线用。**

### 2. universe 实测 3212，此前预估 2658 是错的

`universe_suspension_defect_2026-09-02.md` §3.3 估「2276 → 2658（+16.8%）」，实测 **3212**。

成因：该估算用 SQL 近似复算 F-1/F-5/F-6/F-7，而报告中**已自行标注**
「F-5 的 PIT 两期逻辑无法在 SQL 中精确复现」。近似比真实过滤严，
于是基线与增量**同时被低估**。方向和量级对，绝对值不对。

⚠️ **无法实证「扩大了百分之几」**：生产没有任何表持久化每日 universe 规模，
容器重启后日志只剩当日一行。这是一个可观测性缺口——
「机制生效时会留下的痕迹」在这里恰好不存在（CLAUDE.md §4.11 元判据）。

### 3. 机器升配 2C2G → 2C4G

轻量应用服务器套餐升级（**不支持降级，单向门**）。实例 `ins-qv317jjc` /
`ap-singapore-3` / `POSTPAID_BY_HOUR`。IP、防火墙、密钥、快照均不受影响。

操作前：`docker compose ... stop`（用 `stop` 不用 `down`，保留 `pg_data` 卷）。
PostgreSQL 干净关闭——`checkpoint complete` → `database system is shut down`，
`quantpilot-db-1` 退出码 **0**，无需 WAL 恢复。

| 项 | 前 | 后 |
|---|---|---|
| 内存 | 1967 MB | **3723 MB** |
| 可用 | 121~250 MB | **3178 MB** |
| 磁盘 | 50G / 79% | **60G**（分区自动扩容）/ 66% |
| CPU | 2 核 | 2 核（不变）|

⚠️ 开机后容器**不会自启**——`docker compose stop` 是显式停止，
`restart: unless-stopped` 按定义不覆盖它。需手动 `docker compose ... start`。

### 4. 补跑当日管线（实证升配有效）

`pipeline/trigger` 需 JWT 且前端无调用入口，故在容器内直接调
`scheduler._daily_pipeline_job`——**与 17:30 定时任务同一代码路径**，不自建编排。

`_get_or_create_run` 按 `trade_date` 复用 run 241 →
`pipeline_resume: cp1=True cp2=False cp3=False` → **CP1 跳过，从 CP2 续跑**。
故未重打 Tushare、未重写行情。孤儿 run 不需要标 FAILED，resume 是设计内路径。

| 项 | 结果 |
|---|---|
| 状态 | `SUCCESS`，signal_count = **52**（50 BUY + 2 SELL）|
| CP2 耗时 | **3 分 09 秒**（同一步此前 thrash 37 分钟后被杀）|
| 内存峰值 | 可用 2246M → 996M，swap 仅动 400M → 评分峰值约 **1.65 GB**（差值推算，非 RSS 直读）|

那个 1.65 GB 正解释了旧机必死：2GB 机扣掉 PG/redis/nginx/系统后仅约 1.4 GB 可用，**差约 250 MB**。

### 5. 对照 before 基线

| 项 | before（9-02）| after（9-03）| 判定 |
|---|---|---|---|
| universe | ~2600（无留痕）| **3212** | 扩大，幅度大于预估 |
| `is_suspended` 当日 | 818 / 5547 | **0 / 5549** | 修复生效 |
| `candidate_pool` | 69 行 / `is_holding=6` | 67 行 / **`is_holding=6`** | C1 修复在扩大 universe 上仍正常 |
| SELL 信号 | 0（9-02）| **2** | P0 退出修复仍正常 |
| `wx_pushed=true` | **0 / 6305**（历史全部）| **55 / 55，err=0** | 微信推送**首次真正成功** |

`in_app_notification` 逐日：08-28 `0/51`、08-31 `0/59`、09-01 `0/52`、09-02 `0/53`，
**09-03 `55/55`**。此前每条都带「重试 3 次均失败」的假消息（实为从未配置）。

### 6. 遗留

- **`get_pe_pb_history_bulk` 拉约 380 万行只为算 3212 个分位数**（5 年窗口 ×
  universe）。它是峰值内存的主项，升配只是买到余量、没有抬高地板；
  universe 与 5 年窗口都会继续长。SQL 下推是真正的修法，**尚未实施**。
- 运维红线①的措辞写死「生产 2GB 机」，已与现实脱节，需随本次变更修订。

---

## 2026-09-04：PE/PB 分位下推的算力机实测（部署前验证）

`b76f3d0` 提交时标注「收益尚未实测」，此处补上。在第二台算力机（31.5G 内存）
对 5434 全量副本只读实测，universe 取 3212 只以对齐 2026-09-03 生产实测规模，
交易日 2026-08-25（副本最新），窗口 `[2021-08-26, 2026-08-25]`。

### 内存与耗时

| | 旧 `get_pe_pb_history_bulk` | 新 `get_pe_pb_percentile_bulk` ×2 |
|---|---|---|
| 返回行数 | **3,655,923** | 2,454 + 3,208 |
| tracemalloc 峰值 | **2,313 MB** | **1.5 MB** |
| 驻留 | 1,397 MB | 0.9 MB |
| 耗时 | 57.6 s | 5.7 s |

峰值降至 **0.07%**（省 2,312 MB），耗时快约 10 倍。

⚠️ **此前的推算低估了**：CLAUDE.md 红线①与 `b76f3d0` 提交说明按「约 380 万行、
占峰值主项」描述，但没给量级；实测单这一个调用瞬时分配 **2.3 GB** Python 对象。

这顺带修正了一个归因：CP2 在 2GB 机上耗时 28~37 分钟，此前只归因于「swap 抖动」，
实际抖动的主体就是这 2.3 GB。升配后降到 3 分 09 秒，下推后可再省掉这 57.6 秒。

### 等价性（真实数据逐股对照）

2,454 只真实股票（当前 `pe_ttm` 非空者；3212 只中有 758 只因负收益 PE 为 NULL）
对照 SQL 与 `value._compute_historical_percentile`：

```
NaN 归属不一致 = 0
最大绝对差     = 0.000e+00
差值 > 1e-9    = 0
```

**逐位相同**，非「在误差范围内」。这比仓内合成种子的证据强——真实数据含负 PE 造成的
NULL、并列值、五年跨度极值，合成用例编不全。

> 方法：只读脚本，不写任何表、不跑 alembic、不跑 pytest（5434 装着
> `ic_baseline_pre_c1`，`--force-wipe` 已被 `guard.py` deny）。

## ad69278 — 2026-09-04T06:04:09Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `a9b7378` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_ad69278_20260904_150149.tar.gz` |
| delta | 7 个 commit |

```
b76f3d0 perf(scoring): PE/PB 历史分位下推 PostgreSQL——每日管线不再拉 380 万行
8bfa66a test(value): PE/PB 历史分位数值语义特征测试（SQL 下推前置件）
39bc484 fix(signal): 收盘后复评持仓私有信号——修硬止损的一个交易日延迟
c736a72 fix(notify): SecretFilter 按 URL 形状脱敏——按键名匹配 4 个月从未生效
c7e5497 feat(v1.5-k): K-6 因子级 IC 与有效率计算 + §3 门槛 3 增设 valid_ratio 例外
f182e6a feat(v1.5-k): K-6 factor_panel_stat 建表（ORM + alembic 0026）
76e8d31 feat(v1.5-k): K-1 HAC/子采样/配对检验入仓 + K-0 与 §6 状态回写
```

## 2d2c93f — 2026-09-04T16:15:09Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `ad69278` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_2d2c93f_20260905_011318.tar.gz` |
| delta | 11 个 commit |

```
2d2c93f fix(signal): 复评改由管线末尾触发（定时降为兜底）+ sent 只计真正落库的
81b755b feat(ops): 历史 total_equity 回填脚本——F-4 净资产过滤此前在早期日期整段跳过
74dde99 feat(v1.5-k): K-6 面板重跑驱动脚本——日历缓冲改为启动即校验
666927d feat(v1.5-k): collect_factor_panel 开关贯通 ScoringService → Scorer.aggregate
756fc9a feat(v1.5-k): K-7 holdout 纪律机制化——默认只给开发集，看 holdout 须显式解锁
2f802c2 feat(v1.5-k): K-6 面板统计量组装与扇出——无前向概念的指标不得跟 horizon 循环
fbe087a feat(v1.5-k): Scorer 导出因子级 raw/z 两版（默认关闭）+ 订正 §2.2 一处失实
ebf56a2 feat(v1.5-k): K-5 换手成本拖累——1−J 不是换手率，会高估近一倍
a295b9d feat(v1.5-k): K-4 top 5% 日间 Jaccard 换手代理 + 头部选取与 K-2 收敛到单一实现
ebe88c6 feat(v1.5-k): K-3 多前向窗口解析——两种「不可用」必须可区分
ab58c4d feat(v1.5-k): K-2 十分位收益阶梯 + top 5% 头部超额（组合口径）
d2af940 feat(v1.5-k): K-6 写库方 upsert_factor_panel_stat_bulk（分批，≥2979 行才测得出）
```

## 85438a8 — 2026-09-08T02:37:37Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `2d2c93f` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_85438a8_20260908_113509.tar.gz` |
| delta | 7 个 commit |

```
5f7d57a fix(data): 按公告日做 PIT 截断——回填历史不得再写入未公告的基本面
63731f5 feat(universe): 每日选股面落库 + 🔴 修复 F-5「连续两期亏损」名存实亡
80b2251 fix(config): CP2 改用冻结快照 + 撤回一条我自己写错的结论
384d683 fix(config): F-SI 配置静默失效——13 个字段接线 + 堵住「加了参数没人传」
1b05bc8 fix(universe): 基本面覆盖率告警改按阈值——「100% 才响」报不出「几乎全死」
6bdd623 fix(data): total_equity 按字段 LOCF——F-4 净资产过滤每逢季初首日整段跳过
fb33d57 feat(v1.5-k): 补组合级（composite）统计量——因子级答不了「系统实际买什么」
8c27c51 fix(v1.5-k): 面板窗口起点按行情历史深度订正 + 补价格覆盖守卫；全 5y 面板已跑完
```

## af94e57 — 2026-09-09T04:53:16Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `85438a8` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_af94e57_20260909_135036.tar.gz` |
| delta | 5 个 commit |

```
af94e57 fix(v1.5-c): low_volatility_score 全链路死代码——契约测试断言字段存在≠值到了终点
805c988 feat(v1.5-c): C2 回填激活 + 门控实测为负 → 影子模式；Tushare 行数截断守卫
e133d41 feat(v1.5-c): C3 低波动策略（影子模式）+ 策略名单一事实来源
83a8f79 feat(v1.5-c): C2 Piotroski F-Score 硬过滤——六块完成五块，差 5y 回填激活
4bf6084 feat(audit): 前视偏差的数据层检测——源头拦「产生」，它拦「存在」
6f89d74 fix(script): 修复脚本误设 updated_at + 5434 存量修复已执行完毕
```

**部署后核验（2026-09-09 12:53 CST）**：

- `/health` = `af94e57 2026-09-09T04:50:46Z main` ✅
- `alembic_version` = **0029**（0028 与 0029 均已跑）；9 个新列全部到位：
  `financial_data` 的 7 个 Piotroski 列 + `candidate_pool` / `signal_score_snapshot`
  各一个 `low_volatility_score`
- `scheduler_started`，backend 日志无 error/traceback；可用内存 2524M
- ⚠️ **本批选股行为应为零变化**：`low_volatility` 影子权重 0、
  `piotroski_gate_enabled=False`（只算、只记日志，不剔除）。今晚 17:30 管线的
  `universe` / `signal_count` 若与前一日同量级即符合预期；**出现明显跳变反而是
  异常信号**，说明有未预期的行为变更，要查而不是庆祝。
- **生产 7 列尚未回填**（全 NULL）→ F-Score 全判「不可判」→ 门控日志会报
  `piotroski_gate_shadow` 且 `unjudgeable` 处于高位。这是**设计内的可见降级**
  （C-4），且门控本就是影子模式，对选股无影响。回填是另一件事，
  需单独的 C-1 确认 + `pg_dump -t financial_data` 定点备份。

**观察期第 1 个交易日（2026-09-09 17:30 CST 管线，部署后首跑）**：

| 项 | 部署前 09-08 | 部署后 09-09 | 判读 |
|---|---|---|---|
| `pipeline_run.status` | SUCCESS | **SUCCESS** | ✅ |
| `signal_count` | 51 | **51** | ✅ 完全相同 |
| 耗时 | 413s | **434s** | +21s（+5%），与多算 F-Score + 低波动两组因子相符 |
| `universe_daily_stat.total_out` | 3211 | **3210** | ✅ 同量级 |
| `candidate_pool.is_holding` | 6 | **6** | ✅ = 实际持仓数 |
| backend error / OOM | — | **无**，可用内存 2150M | ✅ |

**判据是「没有变化」，实测确实没有变化** —— 影子权重 0 + 门控不剔除，本批不该改变选股，
而 signal_count 与 universe 都对上了。

**`low_volatility_score` 落库已实证**（这是 `af94e57` 那个死代码修复的痕迹判据）：

| trade_date | 行数 | `low_volatility_score` 非空 | `value_score` 非空 |
|---|---|---|---|
| 2026-09-09 | 69 | **54** | 54 |
| 2026-09-08 | 67 | 0 | 54 |

09-07/09-08 为 0 是因为该列由 alembic 0029 在 09-09 才加上（`ADD COLUMN` 后既有行必然 NULL），
**不构成对修复的证据**；证据是 09-09 那行的 **54 = `value_score` 的 54**。
若死代码未修，09-09 同样会是 0——那才是修复前的形态。

**影子门控日志已出现**：`piotroski_gate_shadow: blocked=71 unjudgeable=3210 financial_alt=119 threshold=6.0`，
同时 `piotroski_f_score: judged=0 unjudgeable=3210`（生产 7 列未回填，属设计内可见降级）。

### ⚠️ 由此发现一个「提前激活门控」的陷阱

`judged=0` 却 `blocked=71`——这 71 只**只能来自金融股的 ROE 替代分支**
（`financial_alt=119` 中 roe ≤ 5% 的那些）。原因是该分支读 `financials["roe"]`，
**不依赖那 7 个待回填的列**。

即：**在生产回填完成之前激活门控，会变成「只门控金融股、其余全部 fail-open」**——
一个谁都没设计过的不对称行为（金融股被按替代判据严格筛，非金融股完全不筛）。
影子模式下无害，但这条必须在激活前解掉：
**顺序是「先回填 7 列 → 确认 `judged` 接近 universe → 再谈激活」**，不能只看「门控代码已上线」。

**观察期第 2 个交易日（2026-09-10）**：

| 项 | 09-08 部署前 | 09-09 第1日 | 09-10 第2日 |
|---|---|---|---|
| status | SUCCESS | SUCCESS | **SUCCESS** |
| `signal_count` | 51 | 51 | **51** |
| `universe_daily_stat.total_out` | 3211 | 3210 | **3210** |
| `candidate_pool` 行 / `low_volatility_score` 非空 | 67 / 0 | 69 / 54 | **64 / 55** |
| `is_holding` | 6 | 6 | **6** |
| 耗时 | 413s | 434s | **192s** |

判据「没有变化」继续成立：signal_count 三日连续 51、universe 与前日同为 3210。

⚠️ **耗时 434s → 192s 是 2.3 倍的跳变，按自己立的规矩（跳变才是异常，要查不要庆祝）查了**：
三个检查点都产出了东西 —— `universe_daily_stat` 有行（cp2 写）、`candidate_pool` 64 行
且 `low_volatility_score` 55 个非空（cp2 写）、`signal_count=51`（cp3 写），
universe 规模与前日**逐个相同**。故**不是「某一步被跳过」**，是同样的工作做完了但更快。
未进一步归因（DB 缓存/计划、宿主负载都可能），**记在这里是为了留痕**：
若后续某日耗时再次大幅变化且伴随 universe 或 signal_count 变动，那才是真问题。

**观察期还剩 1 个交易日**（2026-09-11 17:30 CST）。

### af94e57 观察期收口（3 个交易日全过，2026-09-14 判定）

| 项 | 09-08 部署前 | 09-09 第1日 | 09-10 第2日 | 09-11 第3日 |
|---|---|---|---|---|
| `status` | SUCCESS | SUCCESS | SUCCESS | **SUCCESS** |
| `signal_count` | 51 | 51 | 51 | **53** |
| `universe_daily_stat.total_out` | 3211 | 3210 | 3210 | **3211** |
| `candidate_pool` 行 | 67 | 69 | 64 | **71** |
| ↳ `low_volatility_score` 非空 | 0 | 54 | 55 | **56** |
| `is_holding` | 6 | 6 | 6 | **6** |
| 耗时 | 413s | 434s | 192s | **182s** |
| error / OOM | — | 无 | 无 | **无**（可用 2164M）|

**判据「没有变化」成立**：universe 三日 3210/3210/3211 与部署前 3211 持平；
`is_holding` 恒等于实际持仓数 6；无 error、无 OOM。

⚠️ **`signal_count` 51 → 53 不算跳变**：53 正是部署前 09-07 与 09-04 的值
（序列 53/53/51/51/51/53），属市场驱动的正常波动，且 universe 未变。
判据是「与前一日同量级」，不是「逐日完全相同」——后者对一个随市场变化的量
本就不可能成立，把它当判据只会逼人去解释噪声。

**C3 DoD「生产上线后观测 `scorer_strategy_skipped_*` 无 low_volatility 异常」已满足**，
且不是靠「没看到告警」这种弱证据——`low_volatility_score` 非空数逐日 54→55→56
是**正面痕迹**：该策略确实在参与评分并落库。⚠️ 只靠「查不到告警」下结论是不够的
（日志可能轮转、机制可能压根不记），要找的是**生效时会留下的东西**（§4.11 元判据）。

**耗时 434s → 192s → 182s** 已在第 2 日记录里查证过不是跳步（三个检查点均有产出、
universe 逐个相同），此处不再重复归因。

### ⚠️ 观察期之后积压了一批**未部署**的改动

生产自 2026-09-09 起一直是 `af94e57`。其后这些**均在仓库、未上生产**：

| commit | 内容 | 用户可见 |
|---|---|---|
| `c013944` | `liquidity_note` 流动性提示（SDD §9.1 规定却从未产生过）| ✅ |
| `dd6f4e9` | `funding_note` 满仓提示（50 条推荐全不可执行时告知）| ✅ |
| `4efd7d6` | 信号详情显示「判断依据」（`reason` 本就落库却从未渲染）| ✅ |
| `4115ee0` | 买入理由补「主要驱动：价值 · 均值回归」（同时改善 WxPusher 推送）| ✅ |

⚠️ **纠正一处我自己的错误陈述**：此前我写过「今晚管线会第一次带着新
`liquidity_note` 跑」——**那是错的，它从未部署**。实测 09-11 的 53 条信号
`liquidity_note` 非空 **0** 条，正因为生产仍是 af94e57。
判据始终是 `/health` 自报的 sha，不是「我记得提交过」。

## 769e33b — 2026-09-14T04:07:26Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `af94e57` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_769e33b_20260914_130433.tar.gz` |
| delta | 7 个 commit |

```
4115ee0 feat(signal): 买入理由补上「主要驱动」——J-EXPL 的实质那一半，并修掉 scorer 的真 bug
dd6f4e9 feat(signal): 满仓时给出列表级 funding_note——50 条推荐全不可执行而界面只留白
c013944 feat(signal): 实现 SDD §9.1 的 liquidity_note——规定了却从未产生过值
76beb60 fix(test): 补上「停牌股不发买入信号」这条唯一防线的测试；F-3/F-8 全量剔除画像
a2a51b8 docs(review): F-5 实现不符 SDD，但按规范改正会更差——只订正注释，不动过滤行为
82d5cc0 docs(ops): af94e57 观察期第 1 日全过 + 登记 K-WEIGHT 策略内因子权重不符 SDD
dcac2e4 fix(guard+docs): 堵住「Bash 改文件绕过评审钩子」+ roe_quality 实测结论反转
84f6913 feat(value): roe_quality 开关就位（默认不变）——剔除被 SDD 冲突与能力缺口拦下
```

**部署后核验（2026-09-14 12:07 CST）**：

- `/health` = `769e33b 2026-09-14T04:04:38Z main` ✅
- `alembic_version` = **0029**（本批**无新迁移**，预期不变）
- backend 近 10 分钟日志 error/traceback **0** 条；`scheduler_started` 已出现
- `nginx -s reload` 已执行（脚本第 7 步，防 backend 换 IP 后 502）
- 可用内存 **2534M**
- 回滚点 `backend_pre_769e33b_20260914_130433.tar.gz`

**本批预期**：仍是**选股行为零变化**（`low_volatility` 影子权重 0、
`piotroski_gate_enabled=False` 不剔除）。新增的**可验证痕迹**有两条：
① 买入 `reason` 里应出现「，主要驱动：…」；② `signal.liquidity_note` 非空数
应由 0 变为约等于 BUY 条数。两条都在今晚 17:30 管线后核对。

### 🔴 `deploy_prod.sh` 只同步 backend/——本批有三项的 UI 部分**没有上线**

| 改动 | 后端 | 前端渲染 | 本次是否送达用户 |
|---|---|---|---|
| `4115ee0` 买入理由「主要驱动」| ✅ | 无需前端 | ✅ **WxPusher 推送即可见** |
| `c013944` `liquidity_note` | ✅ 落库 + API | ✗ | ⚠️ 只到 API |
| `dd6f4e9` `funding_note` | ✅ API | ✗ | ⚠️ 只到 API |
| `4efd7d6` 信号详情「判断依据」| 纯前端 | ✗ | ❌ 完全未上 |

生产前端由服务器上的 `frontend-builder` 容器从 `./frontend` 构建进 `frontend_dist`
卷、再由 nginx 提供，而 **`deploy_prod.sh` 从不同步 `frontend/`**。

⚠️ **没有改用 `scripts/deploy.sh`**：`deploy_prod.sh` 文件头已记明那个脚本
**对当前生产是错的**（带 `--pull` / 用 compose 起 nginx 覆盖就地改过的配置 /
不做 `nginx -s reload` / 完全不同步代码），它本身就属 §4.11「接了但没生效」一族、
从未用于这套生产。且 `deployment.md` 记着小机上 vite/npm 构建**有 OOM 风险**
（"需本地预构建 dist 再传"），而运维红线明确「**升配至 2C4G 不解除本条**」。

**即：这套生产目前没有一条经过验证的前端部署路径。** 这是个真实缺口，
不该在一台有 OOM 红线的机器上临场发明——2026-08-17 打挂站点 43 分钟就是那类操作。
建议路径（待单独设计与验证）：**本地构建 dist → 传产物 → 换卷 → `nginx -s reload`**，
本地构建规避 OOM 且产物可校验。

## 前端 52e22c0 — 2026-09-14T04:22Z（首次使用 `scripts/deploy_frontend.sh`）

**此前这套生产没有任何前端部署路径**：`deploy_prod.sh` 只同步 `backend/`，
而 `frontend_dist` 卷里的产物停在 **2026-09-03**。此后 5 个 commit 改过 `frontend/`——
其中 **`af94e57` 的 `low_volatility` 溯源展示**在后端上线后**又卡了 11 天**。
即一个结构性的「**后端部署成功 ≠ 功能上线**」缺口。

| 项 | 值 |
|---|---|
| 入口 JS（部署前 → 后）| `index-Pe0BR4Xj.js` → **`index-uG6xYbOC.js`** |
| assets 文件数 | 39（与线上结构一致，仅哈希不同）|
| 回滚点 | `/home/ubuntu/backups/frontend_dist_pre_52e22c0_20260914_132209.tar.gz` |

**生效判据（两层，都过）**：
1. **公网取回的 `index.html` 引用的入口 JS == 本次构建的入口 JS**，且该文件 200 可取回。
   ⚠️ 判据不用「文件复制成功」——那在 nginx 缓存旧 fd / 换错卷 / CDN 缓存旧页面时都假阳性；
   vite 的 asset 名带内容哈希，哈希比对不会。
2. **功能级痕迹**：新文案确实在线上分片里（且各自在对的分片，说明代码分割没被破坏）

   | 文案 | 分片 | 来自 |
   |---|---|---|
   | 判断依据 | `SignalsView-TngAeLvn.js`（原 `BiYQarF-`）| `4efd7d6` |
   | 流动性提示 | `TermLabel-DX29A-Jw.js` | `c013944` 术语表 |
   | 低波动 | `lineage-D34s0GEb.js` | `af94e57`（卡了 11 天）|

### 做法与为什么

**本地构建 → 传产物 → 换卷 → `nginx -s reload`**。三条约束决定了这个形状：

- **不在生产机跑 vite**：`deployment.md` 记着小机 npm/vite 构建有 OOM 风险，
  而红线明确「升配至 2C4G **不解除本条**」
- **不用 `scripts/deploy.sh`**：它对当前生产是错的（带 `--pull` / 用 compose 起 nginx
  会覆盖服务器上就地改过的配置 / 不 reload / 完全不同步代码），见 `deploy_prod.sh` 文件头
- **nginx 以只读挂该卷** ⇒ 必须用一次性容器以读写方式挂同一个卷来替换内容。
  而 `frontend-builder` 的全部作用本就是 `rm -rf /output/* && cp -r /dist/. /output/`
  ——它只是搬运工，产物从别处来不冲突，故**无需改 compose**

### 🔴 回退陷阱

`frontend-builder` 镜像里**烤着一份构建期的 dist**。谁若在前端部署之后执行
`docker compose up -d frontend-builder`，它会清空卷再把**那份旧产物**拷回去，
**静默回滚且不报错**。真要用它，先重建镜像。判据同上：比对线上入口 JS 哈希。

### 脚本自检（首跑即抓到两个问题，都在碰生产之前）

- 我写的 de-hash `sed` 多了一个 `/` 被当成标志位 → 首次 `--dry-run` 在第 3 步中止。
  已改用 `|` 作分隔符并就地注明（替换内容本身是 `.`，沿用 `/` 极易多写一个）
- 专门验证过闸门**真的会拦**：故意制造未提交的 `frontend/` 改动，脚本在第 1 步
  就拒绝且不进入构建。**未测过的闸门等于装饰品**

## f1b93c1 — 2026-09-16T04:24:05Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `769e33b` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_f1b93c1_20260916_132123.tar.gz` |
| delta | 2 个 commit |

```
c297d7c fix(signal): 生产 liquidity_note 全 NULL——快照行情从不含 avg_amount，service 层补取 20 日均成交额
a26b922 fix(review): 订正 4GB 复查自身的过期前提——(b) 早在复查前 10 天就已被推翻
c26aa1a docs(review): 2C2G → 2C4G 后逐条复查以 2GB 为前提的约束——不放宽任何一条
```

### 为什么部署（769e33b 的观察期判据）

769e33b（09-14 部署）上线两次管线后查痕迹：`reason` 含「主要驱动」**52/52、52/52 ✓**；
`liquidity_note` 非空 **0/104 ✗**——快照行情从不含 `avg_amount`，文案从未生成、
signal.py 的流动性门槛也从未生效。修复 `c297d7c`，本次只此一个功能 commit。

### 本次判据（下一次 17:30 管线后看）

- `select count(*), count(liquidity_note) from signal where trade_date = <当日> and signal_type='BUY'`
  → 两数应相等（或差额 = 当日 `get_avg_amount` 无数据的新股数）
- `signal_count` 若较 54 下降，先查「池内 20 日均成交额 < 500 万」的股数——门槛首次生效属预期，
  **不是**异常；跳变超过该数才是
- 部署前读到的 4GB 管线峰值（cgroup `memory.peak` 754 MiB，覆盖 09-14/09-15）已入档
  `docs/reviews/memory_premise_after_4gb_2026-09-14.md` §3；本次重建容器后计数器归零，
  下一次读到的是新基线

## 57bc966 — 2026-09-16T07:37:34Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `f1b93c1` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_57bc966_20260916_163532.tar.gz` |
| delta | 0 个 commit |

```
57bc966 feat(c4): 资金动向数据层 + 策略（V1.5-C C4，影子权重 0）——并修两个 C3 同样中招的既有缺陷
```

### 本次上下文（2026-09-16 15:35~15:38 CST）

- **alembic 0029 → 0030 → 0031** 由 backend 启动自动执行，日志确认；`alembic_version=0031`，
  `money_flow` 表建成（0 行）、两表 `money_flow_score` 列存在（information_schema 计 2）。
- **回滚点（DB）**：0031 改动 `candidate_pool` / `signal_score_snapshot` 两表，部署前定点导出
  `/home/ubuntu/backups/pre_0031_pool_snapshot_20260916_153504.sql.gz`（30 MB，两段 COPY）。
  0030 是新表，无需备份。磁盘部署前 76%（14 G 可用）。
- **⚠️ 选股行为应为零变化**：`money_flow` 影子权重 0；表未回填前策略全 NaN → `Scorer` 跳过。
  观察期看到 signal_count / universe 跳变才是异常。
- **2y 回填（C4 步骤 2）在本会话中未能执行**：用户已授权（「都推」），但执行环境的
  自动分类器两次拒绝「经 ssh 向生产写入」这一动作。**需人在终端手敲**（下条）。
  在此之前每日管线的 `ingest_daily` 第 5 段会从当天起逐日采集；20 个交易日后策略自然开始有值。

**待人工执行的回填命令**（backend 容器内，约 484 次调用 / 20 分钟，+0.57 GB；避开 17:30 管线）：

```bash
ssh qp-tencent 'cd /home/ubuntu/QuantPilot && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  exec -d backend sh -c "cd /app && PYTHONUTF8=1 python scripts/backfill_money_flow.py \
  --start 2024-09-16 --end 2026-09-16 --skip-confirm > /app/logs/backfill_money_flow_prod.log 2>&1"'
# 看进度 / 结果：
ssh qp-tencent 'cd /home/ubuntu/QuantPilot && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  exec -T backend tail -3 /app/logs/backfill_money_flow_prod.log'
```

**✅ 回填已由用户手敲执行（2026-09-16 15:44 → 16:29 CST，5.6 s/日）**：`ok=484 fail=0`；
`money_flow` **2,503,091 行 / 484 个交易日（2024-09-18 ~ 2026-09-15）/ 536 MB**（225 B/行，
与 5434 样本实测 212~227 一致）；无任何交易日 < 4500 行、`net_mf_amount` 无 NULL；磁盘 79%（13 G 可用）；
回填期间 available 最低 2201 MB。⚠️ 回填结束时间早于 17:30 管线 1 小时，未与评分作业重叠。

**✅ 17:30 管线痕迹（09-16，17:47 CST 核）**：SUCCESS / signal_count 54（50 BUY + 4 SELL）/ universe 3208 = 昨日；
`liquidity_note` **50/50**（昨 0/52）；池内 `money_flow_score` **55/55**（昨 0/56）；当日 `money_flow` 日采 5550 行；
容器 `memory.peak` 629 MiB。管线耗时 7m52s（昨 3m02s，多出资金流窗口查询与日采，观察是否稳定）。

## a730ac2 — 2026-09-16T09:01Z（17:01 CST）：回测有条件放开（用户拍板选项 B）

| 项 | 值 |
|---|---|
| 基线 | `57bc966` |
| delta | 1 个 commit：`a730ac2` 作业时段禁提交护栏 + compose/.env.prod.example 双写 |
| 配置（脚本不碰，手工）| `.env.prod`：`BACKTEST_ENABLED=true` / `BACKTEST_MAX_WINDOW_DAYS=100`（原 7）/ 新增 `BACKTEST_BLACKOUT_WINDOWS=17:15-18:30,19:15-20:15`；服务器 compose 与仓库 md5 一致 `9f807444…`；两文件改前均备份至 `backups/*.pre_backtest_enable_*` |
| 生效核验 | 容器 `printenv` 三项到位；`/health` = `a730ac2` |

### 验收（20:23~21:20 CST，禁提交时段之外，容器内 `run_backtest_local.py` 不带 `--push`）

| 窗口 | 耗时 | 容器 `memory.peak` | 判据 ≤ 2 GB |
|---|---|---|---|
| 基线（含当日 17:30 管线 + 19:30 IC Job）| — | 769 MiB | — |
| 6 交易日（09-05 ~ 09-12）| 4m33s | **1619 MiB**（增量 ≈ 1.03 GB，与本机实测 1056 MB 一致）| ✅ |
| **100 日历天上限**（06-04 ~ 09-12，68 交易日）| **52 min**（≈ 45 s/交易日，2 核）| **1686 MiB** | ✅ |

结论：**开关维持 true、上限维持 100**。窗口加长几乎不抬峰值（+67 MiB），只加时间；
swap 期间用到 671 MB、无 OOM 压力信号，`/health` 全程 200，容器无重启。
⚠️ 上限窗口一次要 52 分钟——若用户体感太慢，是性能问题不是内存问题，可后续做
（每日 3 次 SQL 分位查询各 ~3.5 s 是主项）。
⚠️ 教训：>10 分钟的 ssh 长命令会被对端重置（本次 100 日那条），进程在容器内照常跑完，
但**下次一律 `exec -d` + 落盘日志再轮询**，别把 ssh 当执行器。

**本次判据（回填完成后 + 下一次 17:30 管线后）**：
- `select count(*), count(distinct trade_date) from money_flow` → 约 2.5M / 484（+ 每日新增一日）
- 管线后 `select count(money_flow_score) from candidate_pool where trade_date=<当日> and in_pool`
  → 应 ≈ 池内行数（回填前为 0 属预期）；`signal_count` 与 universe 不跳变

## a730ac2 — 2026-09-16T09:03:49Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `57bc966` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_a730ac2_20260916_180135.tar.gz` |
| delta | 1 个 commit |

```
a730ac2 feat(backtest): 作业时段禁提交护栏 + 生产有条件放开回测（用户拍板选项 B）
e99c86b perf(backtest): PE/PB 分位下推 + 流式加载，6 日回测峰值 3530 → 1056 MB；守卫覆盖 PowerShell 工具
```

## 5d20c34 — 2026-09-17T02:36:29Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `a730ac2` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_5d20c34_20260917_113417.tar.gz` |
| delta | 1 个 commit |

```
5d20c34 test(backtest): BT-09c 出窗那一跳给日历 mock 确定性 400，不再碰真实 DB（本机 5432 关着时假红）
5dc1e24 fix(tushare): fina_indicator 定期调用同样受 100 行截断——命中上限即对半拆批重取，两条路径共用
```

### 本次上下文：生产 Piotroski 7 列回填（用户 2026-09-16 拍板「回填」，C-1 已确认）

**回滚点**：`/home/ubuntu/backups/pre_piotroski_financial_data_20260916_170434.sql.gz`
（`--data-only -t financial_data`，156 MB，6,743,620 行与表一致）。回填前 7 列非空基线：
roa 27,249 / ocfps 27,741 / total_share 27,747（均只来自 C2 上线后的每日采集）。

**第一次（09-17 09:44 CST，用户手敲，代码 `a730ac2`）被我停掉**：3 期各只写 ~4,900 行，
`tushare_row_cap_suspected(fina_indicator, 100)` 302 次。真调复现：**`period=` 定期调用同样被
100 行截断**——80 码一批恰好 100 行、只剩 55 个 ts_code；50 码 × 每码 2 行（update_flag 0/1）
恰好 100 就是边界。⚠️ **每日 17:30 管线的 `fetch_financial_data` 走的是同一形态**，此前每天都可能
悄悄丢若干只股票的基本面。修法 `5dc1e24`：命中上限即对半拆批重取，两条路径共用一个入口
（TD-13/14 用「>100 行只返前 100」替身钉死、变异验证）。已随 `5d20c34` 部署。

**第二次（10:37 → 11:51 CST，代码 `5d20c34`，我起的）**：22 期全部 `ok≈5,500`（首次截断时 ~4,900），
拆批告警 606 次（= 拆批次数，不是丢行）。**总行数 6,743,620 → 6,776,518**（+32,898：拆批取回的
第二行 update_flag 与 2021 年后上市股的历史期）。

**独立核验（不信脚本自报，按 `(ts_code, report_period)` 粒度，121,192 对）**：
roa **96.4%** / ocfps 96.3 / eps 96.5 / current_ratio 94.8 / grossprofit_margin 96.1 / assets_turn 96.5
——与 5434 的 94.7~99.9% 一致。`total_share` 回填前 4.6%（它来自 `daily_basic`，另一脚本
`backfill_total_share.py`，12:19 CST 起跑、22 次调用约 3 分钟）。

**`total_share`（12:19 → 12:23 CST，`backfill_total_share.py`，22 次 `daily_basic` 调用）**：
逐期 4,217 → 5,482；按 `(ts_code, report_period)` 粒度 **92.7%**（分母含该期末尚未上市的码），
**7 列同时非空 89.3%**（121,208 对）。总行数 6,776,518 → **6,866,343**（+89,825 期末快照行）。
F-Score 「不可判」自此不再是「缺数据」，今晚 17:30 起 `piotroski_f_score: judged` 应由 0 变为约 3,000+
（门控仍是影子模式，不剔除）。

**日志轮转（用户 2026-09-17 拍板「清」）**：`/app/logs/quantpilot.log` 2026-09-03 SecretFilter 修复前的
10,194 行（含 `redis://:<密码>@` 明文）切走归档到 `backups/quantpilot.log.pre20260903_20260917_125740`
（600 权限）；用「截断 + 追加」而非 `mv`，uvicorn 的 FileHandler 仍写同一 inode（切完后新行照常落盘）。
切后活文件 390 行、明文匹配 **0**。

## 4aead26 — 2026-09-17T08:41:32Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `5d20c34` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_4aead26_20260917_173930.tar.gz` |
| delta | 6 个 commit |

```
4aead26 feat(backtest): universe 取数对齐生产口径——F-5 传两期财务历史、F-7 用 20 日均成交额（L-FID，用户拍板 6-A）
d7eb790 fix(test): e2e 禁触 DB 护栏改 function scope——session 级在 CI 单会话里活到进程结束，拦掉了集成测试
e339ec9 feat(config): F-SI 收口——ma_short/ma_long 接线为 MA 阶梯（默认逐位不变），FactorMonitor 四个旧字段摘掉（用户拍板 5a-A / 5b-A）
3e37db1 perf(backtest): PIT 日期掩码向量化——6 日回测 910 万次逐行 lambda（24 s）归零，结果逐位不变
1cc8580 test(e2e): 结构性禁止 e2e 触达真实 DB——engine do_connect 监听器统一报 E2ETouchedDatabase
479373c fix(config): ValueStrategyConfig.pe_pb_history_years 真正决定 PE/PB 分位窗口（F-SI 欠账收口）
52a9c4f fix(tushare): index_weight 恰好 1000 行是结构性的（CSI500 × 2 月末快照），豁免每日假告警；roadmap J-EXPL 收口；dev_setup 5433 旧容器凭证提示
```

### 本次上下文（2026-09-17 16:39~16:44 CST，后端 + 前端同批）

**选股行为预期零变化**：`ma_short/ma_long` 接线在默认 20/60 下与旧阶梯逐位一致，且生产
`system_config` / `user_config` 对 `strategy_params_trend` / `factor_monitor_params` /
`strategy_params_value` **无任何覆盖值**（部署前查过）；`pe_pb_history_years` 默认 5 = 原常量。
今晚 17:30 管线若 universe / signal_count 跳变即异常。

**回测口径变化（有意）**：`4aead26` 起网站回测的 universe 与生产同口径（F-5 两期 / F-7 20 日均量），
此前提交的回测任务结果**不再可比**。前端：设置页删掉「因子质量监控」整段（三个零引用旋钮）、
信号溯源页新增「资金动向」一行。

**判据（17:49 CST 核）**：管线 SUCCESS、universe ≈ 3208、signal_count 不跳变、`liquidity_note` 与
`money_flow_score` 继续满、`piotroski_f_score judged` 由 0 变为约 3000+（7 列回填后首次）、
`tushare_row_cap_suspected` 只剩 `fina_indicator` 拆批告警（`index_weight 1000` 已豁免）。

## 2026-09-21（运维，无部署）：生产磁盘 83% → 65%

`docker system df`：**Build Cache 14.9 GB / 可回收 13.1 GB**——一周 5 次 `docker compose build`
留下的层。`docker builder prune -f --keep-storage 2GB` 回收 10.99 GB，`df` 由 47G/59G（83%）降到
37G（65%），20 G 可用。镜像层、容器、卷（pg_data 6.9 G）均未动。
修在源头：`deploy_prod.sh` 第 6 步构建后自动 prune（留 2 GB）并打印 `df`，部署记录自此带磁盘水位。

## 95eac81 — 2026-09-22T03:03:36Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `4aead26` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_95eac81_20260922_120013.tar.gz` |
| delta | 2 个 commit |

```
95eac81 perf(backtest): PE/PB 分位改在内存里算（紧凑数组 + bincount），6 日 75→58 s、30 日 12min→177 s
064fae7 docs: 冷启动评审订正三处 + 记下分位查询两条无效提速
62a8bec perf(engine): 评分链四处向量化/去逐行取值，6 日回测 2m15s → 1m15s，结果逐元素不变
```

### 验收（2026-09-22 11:00~11:30 CST，黑窗之外；容器内 `run_backtest_local.py` 不带 `--push`，`exec -d` + 短 ssh 轮询）

backend 刚重建，cgroup `memory.peak` 从 347 MiB 起算（不含当日管线）。

| 窗口 | 耗时 | 容器 `memory.peak` | 判据 ≤ 2 GB |
|---|---|---|---|
| 6 交易日（09-05 ~ 09-12）| **87 s**（上次 4m33s）| **1649 MiB**（上次 1619）| ✅ |
| 100 日历天上限（06-04 ~ 09-12，68 交易日）| **10.6 min**（上次 52 min；≈ 9 s/交易日）| **1926 MiB**（上次 1686）| ✅ 但余量仅 6% |

`/health` 全程 200，容器无重启，宿主 swap 用 176 MB。两批 perf 改动（`62a8bec` + `95eac81`）
在生产上的净效果：回测提速约 5 倍；每日管线的因子 / F-5 / 评分三项也在同一条路上，
看 09-22 17:30 管线耗时。

⚠️ **100 日峰值比上次高 240 MiB 的来源是载入瞬间的三份共存**：asyncpg `COPY` 的 CSV
缓冲（6.9M 行 ≈ 200 MB）+ `read_csv` 出来的 DataFrame + 目标紧凑数组（165 MB），
稳态只多 165 MB（6 日窗口 +30 MiB 即为证）。分块解析可把瞬时峰值消掉，下一批处理；
在那之前 100 日上限**仍在判据内，不回退开关**。

## 0a5dcc4 — 2026-09-22T05:03:33Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `95eac81` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_0a5dcc4_20260922_140105.tar.gz` |
| delta | 1 个 commit |

```
0a5dcc4 fix(backtest): PIT 财务快照不再依赖帧序——groupby.last() 取错 97% 股票的行（L-FID 第二步）
f153ab4 perf(backtest): PE/PB 历史 COPY 改逐块解析直写数组——消掉载入瞬间 CSV 缓冲 + DataFrame + 数组三份共存
```

### 验收（2026-09-22 13:00~13:15 CST，黑窗之外；同前一批测法，backend 重建后 `memory.peak` 从 303 MiB 起算）

| 窗口 | 耗时 | 容器 `memory.peak` | 判据 ≤ 2 GB |
|---|---|---|---|
| 6 交易日（09-05 ~ 09-12）| 82 s | 1660 MiB | ✅ |
| 100 日历天上限（06-04 ~ 09-12）| **522 s**（上次 635）| **1959 MiB**（上次 1926）| ✅ 余量 4% |

指标因财务快照修复而变（6 日 `max_drawdown` 0.0440 → 0.0361、100 日 0.0815 → 0.0849）——
这是缺陷修正，不是回归。`/health` 全程 200，容器无重启。

⚠️ **100 日峰值没有降**：COPY 分块解析省下的那份被 `0a5dcc4` 引擎内 `_prepare_financials`
多出的一份排序副本（100 日切片约 150 万行）吃掉了。修法已写好（Service 直接把排好序的
扁平帧放进 bundle，引擎零拷贝），随下一批部署复测。

## eb8ea63 — 2026-09-22T06:05:41Z

| 项 | 值 |
|---|---|
| 分支 | `main` |
| 基线（部署前） | `0a5dcc4` |
| 回滚点 | `/home/ubuntu/backups/backend_pre_eb8ea63_20260922_150329.tar.gz` |
| delta | 0 个 commit |

```
eb8ea63 perf(backtest): bundle 直接放排好序的财务帧（引擎零拷贝）+ 宽表 EWM 改行递推
```

### 验收（2026-09-22 14:05~14:20 CST，黑窗之外；同前测法，`memory.peak` 从 305 MiB 起算）

| 窗口 | 耗时 | 容器 `memory.peak` | 判据 ≤ 2 GB |
|---|---|---|---|
| 100 日历天上限（06-04 ~ 09-12）| 606 s（上次 522；2 核机波动）| **1812 MiB**（上次 1959）| ✅ 余量 11.5% |

`max_drawdown` 0.084945 与 `0a5dcc4` 逐位相同——numpy EWM 递推没有改变结果；峰值回落 147 MiB
= 引擎不再多背那份排序副本。`/health` 200，容器无重启。三批（`62a8bec` → `eb8ea63`）合计：
100 日回测 52 min → ~9~10 min，峰值 1686 → 1812 MiB（+126 MiB 换来内存分位 + 正确的 PIT 财务快照）。

### 验收跑的副作用：生产 `backtest_task` / `backtest_result` 会留行（2026-09-23 记录）

`run_backtest_local.py` 在容器内跑会**写生产两张回测表**（09-16 两行 + 09-22 五行都是验收跑）。
核实过风险边界：后端**没有**列表端点（只有 `/{task_id}/status` 与 `/{task_id}/result`），
前端 store 只跟踪本次提交的 task_id → 这些行在界面上取不到，不会被误读为用户自己的回测。
但两件事要记住：

1. **跨版本比同一窗口的数字前先看它出自哪个 sha**——`0a5dcc4` 之前的回测（含 09-16、09-22
   前两次验收）用的是帧序依赖的 PIT 财务快照（CLAUDE.md §4.10），与之后的不可比；
   09-22 的 `a6f32a3e` / `74ce78f5`（`0a5dcc4`）与 `e6b67719`（`eb8ea63`）才是修后口径，
   后两者 100 日 `max_drawdown` 逐位相同（0.084945）。
2. 要清理这些验收行属**生产写操作**，须单独取得用户确认（C-1），且没有 UI 暴露面 = 不急。

## 修后回测基线（2026-09-23，5434，引擎 = `eb8ea63` + 键名订正）

后续策略改动的对照点。**修前的任何回测数字都不能当基线**（帧序依赖的 PIT 财务快照，
CLAUDE.md §4.10；更早还叠着 L-PIT 前视偏差）。

| 项 | 值 |
|---|---|
| 窗口 | 2026-06-04 ~ 2026-09-12（100 日历天 / **71 个 NAV 日**）|
| task_id（5434）| `a14b8680-6ac3-427c-a3b0-2b3171354f7a` |
| cumulative_return | **−0.001227** |
| annualized_return | −0.004411 |
| sharpe_ratio | −0.16257 |
| max_drawdown | **0.084945**（与生产 `eb8ea63` 验收跑逐位相同）|
| win_rate / profit_loss_ratio | null（窗口内无已平仓交易）|
| 耗时 / 峰值 | 本机约 7 min；生产 606 s / `memory.peak` 1812 MiB |

⚠️ `money_flow` 在回测里**逐日被跳过**（bundle 无该数据，影子权重 0 故不影响本基线）——
C4 转正前必须先补，判据见 `tests/unit/test_backtest_feeds_weighted_strategies.py`。

## C4 `money_flow` 2y 回填审计（2026-09-23，用户拍板 10-B）

回填本身早已完成（5434 2026-09-16、生产 2026-09-17，见上文对应节），但 V1.5-C §6.6 那条
「回填后核查覆盖率与磁盘占用并记录」的 DoD **一直没做** —— 此处补齐。两库均为只读查询。

| 项 | 5434 | 生产 |
|---|---|---|
| 区间 | 2024-08-26 ~ 2026-08-25 | 2024-09-18 ~ 2026-09-22 |
| 交易日 | 484 | 489 |
| 行数 | 2,496,170 | 2,530,854 |
| 表大小 | 541 MB | 540 MB（§6.4 外推 0.57 GB，吻合）|
| 对交易日历缺日 | 0 | **0**（489/489）|
| 每日行数 | — | min 5084 / avg 5176 / max 5554 |
| 磁盘 | — | 生产 `/` 64% 已用、21 G 可用 |

🔴 **审计照出一条此前没人量过的数据边界：历史覆盖缺北交所。** 对「当日有行情股」的覆盖率：
2024-09-18 95.26% / 2025-06-30 95.06% / 2026-03-31 94.49%，而 2026-09-22（每日管线实时采的
那天）**100.00%**。逐只核对 2026-03-31 的 302 个缺口——**全部是 BJ**（当日 BJ 共 303 只）；
按月看 BJ 行数：2026-04~07 每月都是「N 个交易日 / N 行」= **每天只有 1 只**，2026-08 起
21 日 / 7049 行 ≈ 336/日才是全量。⇒ **Tushare 的 BJ 资金流历史实质自 2026-08 起。**

为什么这条重要：BJ 不是被流动性挡掉的边角——09-22 有 **310/345** 只 BJ 过 F-7 阈值（5M 元），
当日候选池 68 只里 **10 只是 BJ**。所以 C4 的历史 IC 与回测因子在 BJ 子集上**恒 NaN、无观测**，
转正评估（K-7 等）必须声明这个缺口，或等 BJ 历史可得后重算。影子期权重 0 → 不影响当前选股。
