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
