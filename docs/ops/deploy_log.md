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
