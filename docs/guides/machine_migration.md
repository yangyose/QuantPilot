# 迁移到第二台开发/算力机（双活）

> 2026-08-25 建立。场景：局域网内另一台 **24 小时常开的 Windows** 机器，与本机
> **双活**——两边都能开发，靠 GitHub 同步；生产仍留在腾讯 43.134.63.13，不动。
>
> 本文是**新机器的分步 runbook + 双活纪律**。生产部署相关看 `deployment.md`，
> 日常开发环境看 `dev_setup.md`。

---

## 0. 先读这一节：为什么大部分东西不用搬

4.6 GB 的本地算力库（5434）**不需要跨机拷贝**。它是生产库的一份可丢弃副本，
`scripts/sync_local_backtest_db.sh` 能从腾讯每日备份（538 MB gz）重建。

真正必须带过去的只有三类：

| 类别 | 内容 | 走哪条路 |
|------|------|---------|
| 代码 | 仓库全部 | `git clone` |
| **带外**（不在 git，也不该进 git） | `.env` / `.env.prod` / `backend/.env`、`~/.ssh/qp_tencent`、`.claude/settings.local.json`、`~/.claude/CLAUDE.md`、`~/.claude/projects/<slug>/memory/` | 局域网直接拷 |
| **本地独有的算力产出** | `ic_baseline_pre_c1`（4940 行，pre-C1 的 IC 基线快照）| `backups/local-5434/*.sql` |

⚠️ `ic_baseline_pre_c1` **不在生产库里**（它是本机为 C1 对比造的快照），重造要约
**57 小时**。别把它当成 sync 脚本能重建的东西。

---

## 1. 双活纪律（先立规矩，否则两边都跑一半）

| 对象 | 权威方 | 说明 |
|------|--------|------|
| 代码 | **GitHub `yangyose/QuantPilot`** | 两边开工前先 `git pull`，收工前 push。禁止长期分叉——本仓无 CI 兜底，分叉合并时的冲突要人肉解 |
| 生产数据 | **腾讯 43.134.63.13** | 两台机器都只从它拉备份，谁也不是数据源 |
| **算力产出**（IC 面板 / ICIR 回填 / `ic_baseline_pre_c1`）| **24h 机** | 见下 |
| 生产部署操作 | 任一台（都需 `qp_tencent` 密钥）| 但同一时刻只允许一台在操作生产 |

**算力产出只在 24h 机上做。** 本机的 5434 从此视为可随时丢弃的 scratch。
理由：日级 IC 回填是逐日 commit 的长任务（面板对比约 15.4h/组），两台机器各跑一
半会产生「谁都不完整、且无法判断某一天的行出自哪台机/哪个配置」的状态——这正是
C1-3 期间刚踩过的坑（IC 行本身不带配置标记）。

---

## 2. 新机器分步 runbook

### 2.1 前置：路径必须完全一致

```
D:\MyWork\10Project\RD\QuantPilot
```

**不是洁癖，是硬约束**，两个独立理由：

1. **钩子会直接跑不起来**：`.claude/settings.json`（**已入 git**）的 SessionStart 钩子命令是
   硬编码绝对路径 `D:\MyWork\10Project\RD\QuantPilot\.claude\hooks\pull_remote_backup.ps1`，
   且该脚本内部 `$root` 也写死同一路径。路径不同 → 远端备份永远拉不下来（见 §2.6）。
2. **历史记忆认不出来**：Claude Code 的跨会话记忆存在
   `~/.claude/projects/D--MyWork-10Project-RD-QuantPilot/memory/`，目录名由项目路径派生。
   路径不同 = 事故档案 / runbook / 用户反馈全部失联。

### 2.2 装工具链

- **Docker Desktop**（WSL2 后端）
- **uv**（`irm https://astral.sh/uv/install.ps1 | iex`）——**不必单独装 Python**：
  `uv sync` 见 `requires-python = ">=3.12"` 会自动下载并托管一份 3.12
- **Git for Windows**（本仓大量脚本是 bash，`Bash` 工具走的就是 Git Bash）
- **Node.js 20**（本机 v20.12.2）——`frontend/` 是 Vue3 + Vite，「双活：两边都能开发」
  就得两边都能起 `npm run dev` / `vue-tsc` / `vitest`。**只跑算力任务可以先不装**
- Claude Code
- 开工前设好 `git config user.name/user.email` 与 `core.autocrlf`（本仓 `.gitattributes`
  已对 `*.sh` 钉 `eol=lf`）——漏设会让整个仓库显示成「全部已修改」或提交作者错乱

**`%USERPROFILE%\.wslconfig` 按宿主内存决定，不要照抄**：

| 宿主物理内存 | 做法 |
|---|---|
| **≥ 32 GB** | **不设**。WSL2 默认给 50%（实测 15 GB + 4 GB swap），4.6 GB 算力库 + 覆盖索引能全进 page cache，宿主还剩 17 GB |
| 8 GB（本机） | 必须显式压到 `memory=5GB` + `swap=12GB`，否则 WSL2 吃光宿主内存并 swap 抖动——曾让回填某些日慢 **30~70 倍**，且症状伪装成业务层问题（memory `backfill-wsl-memory-thrash`）|

改过 `.wslconfig` 必须 `wsl --shutdown` 才生效；用 `wsl -- free -h` 实证。
注意宿主侧的 Python 评分进程（峰值约 1.4 GB）**不受 `.wslconfig` 管辖**。

**故障指纹**：若某些交易日突然比 111s/日 的基线慢 30~70 倍，先查宿主内存，
不要去查业务逻辑。

### 2.3 拉代码

```bash
cd /d/MyWork/10Project/RD
git clone git@github.com:yangyose/QuantPilot.git QuantPilot
cd QuantPilot
```

需要先把 SSH 密钥放好（见 2.4），或临时用 HTTPS clone。

### 2.4 带外拷贝（局域网，**不要走 git、不要走公网**）

从本机拷到新机的**同名位置**：

| 源（本机） | 目标（新机） |
|---|---|
| `<repo>/.env`、`<repo>/.env.prod`、`<repo>/backend/.env` | 同路径 |
| `<repo>/.claude/settings.local.json` | 同路径（36 KB 权限白名单，省掉大量确认弹窗）|
| `~/.ssh/qp_tencent`（私钥，必需）+ `qp_tencent.pub`（可选）| 同路径，见下方「§2.4.1 `.ssh` 四样」 |
| `~/.ssh/known_hosts` 里 `43.134.63.13` 的 3 行 | **追加**，不可整文件覆盖 |
| `~/.ssh/config` 里的 `qp-tencent` 段 | **追加**（新机通常还没有这个文件）|
| `~/.claude/CLAUDE.md` | 同路径（跨项目全局规则）|
| `~/.claude/projects/D--MyWork-10Project-RD-QuantPilot/memory/`（整个目录）| 同路径 |
| `<repo>/backups/local-5434/`（整个目录，含 `ic_baseline_pre_c1_full.sql`）| 同路径。**`backups/` 被 gitignore，git 不会带它走** |

⚠️ **`known_hosts` 是最容易漏、且失败得最安静的一项。** SessionStart 钩子
`pull_remote_backup.ps1` 用 `StrictHostKeyChecking=yes` + `BatchMode=yes` +
显式 `UserKnownHostsFile=%USERPROFILE%\.ssh\known_hosts`（指纹
`SHA256:uDrxmYGmEiG906ddWMsCNXlRI9N5DrUZCg26KeTxd/0`）。新机若无该条目，ssh 直接拒连、
钩子只往 `backups/remote/pull.log` 写一行就退出，**终端上没有任何提示**。故障链：
备份拉不下来 → `sync_local_backtest_db.sh` 报 `No backup found ... Abort` → 5434 建不起来。

⚠️ **`backups/remote/` 只拷 `qp_*.sql.gz`，不要拷 `.last_pull_date` / `.last_restore`。**
这两个标记文件会让新机的钩子当天以为「已经拉过」而跳过，或让 sync 脚本以为「已经恢复过」
而直接退出。

#### 2.4.1 `.ssh` 四样（2026-08-26 实操补全）

**`known_hosts` 与 `config` 必须逐段追加，不能整文件拷。** 新机有自己的 GitHub 条目，
整文件覆盖会把它们冲掉。在**旧机**提取腾讯那 3 行：

```bash
grep "43.134.63.13" ~/.ssh/known_hosts > /d/_qp_migrate/known_hosts_qp_tencent.txt
ssh-keygen -lf /d/_qp_migrate/known_hosts_qp_tencent.txt   # 核对指纹
```

ED25519 一行必须是 `SHA256:uDrxmYGmEiG906ddWMsCNXlRI9N5DrUZCg26KeTxd/0`
（与 `pull_remote_backup.ps1` 里钉死的一致）。**对不上就别往下走。**
三行分别是 ed25519 / rsa / ecdsa，严格说只有 ed25519 必需（OpenSSH 默认优先协商它），
三行一起拷更稳。在**新机**追加：

```bash
mkdir -p ~/.ssh && cat /d/_qp_migrate/known_hosts_qp_tencent.txt >> ~/.ssh/known_hosts
cat >> ~/.ssh/config <<'EOF'
Host qp-tencent
  HostName 43.134.63.13
  User ubuntu
  IdentityFile ~/.ssh/qp_tencent
  IdentitiesOnly yes
EOF
```

三个容易踩的点：

- **`IdentitiesOnly yes` 不能省。** 没有它，ssh 会把 agent 与默认路径下的所有钥匙挨个
  递过去，新机的 GitHub `id_ed25519` 会先被试；服务端 `MaxAuthTries` 默认 6，钥匙一多就
  可能在轮到 `qp_tencent` 之前被踢掉，报 `Too many authentication failures`。**这个错看
  起来像"密钥不对"，实为"试错了顺序"。**
- **别用记事本建 `config`。** 它会存成 `config.txt`，而资源管理器默认隐藏扩展名 → 你看到
  的还是 `config`，现象是"配置明明写了却完全不生效"。用上面的 heredoc。
- **私钥权限通常不用管。** ACL 不随文件跨机传递，由目标目录的继承决定；而
  `%USERPROFILE%\.ssh\` 默认只有 你 + SYSTEM + Administrators，Windows OpenSSH 接受这组。
  **先直接试，报 `UNPROTECTED PRIVATE KEY FILE` 再收紧**：
  `icacls "$env:USERPROFILE\.ssh\qp_tencent" /inheritance:r` +
  `/grant:r "${env:USERNAME}:(R)"`。注意 Git Bash 里 `ls -la` 显示的 `-rw-r--r--` 是 MSYS
  模拟值，**不反映真实 ACL**，别拿它当判据，要用 `icacls`。
- 若经 `D:\_qp_migrate\` 中转，**拷完删掉那份私钥副本**（该目录未做权限收紧）。
  `known_hosts_qp_tencent.txt` 是公钥指纹，留着无妨。

四样齐了验：

```bash
ssh qp-tencent 'echo ok'    # 出 ok 才算好；这一步同时验证私钥、known_hosts、config
```

ℹ️ 钩子 `pull_remote_backup.ps1` 走 `ubuntu@43.134.63.13` 直连 + 显式 `-i`，**不读 config**，
所以 config 缺失不影响拉备份；它只服务于 `ssh qp-tencent` 简写与手工操作生产。

### 2.5 建 Python 环境

```bash
cd /d/MyWork/10Project/RD/QuantPilot/backend
uv sync --group dev
uv run pytest tests/unit/ tests/e2e/ -q     # 建真实基线，不信任何"应该没问题"
uv run ruff check src/ tests/
```

预期：**841 passed**（2026-08-25 本机 `be6d6d6` 实测基线），ruff **0 error**。
数字对不上先查环境，别改代码。

### 2.6 建算力库（5434）

**前置：`backups/remote/` 里必须先有一个 `qp_*.sql.gz`**，否则 sync 脚本第一步就
`No backup found ... Abort`。两条路：

- **方式 A（推荐，省一次 535 MB 公网下载）**：局域网直接拷本机
  `backups/remote/qp_YYYYMMDD_020001.sql.gz` 到新机同目录（**只拷 `.sql.gz`**，见 §2.4）。
- **方式 B（新机自己拉）**：靠 SessionStart 钩子 `pull_remote_backup.ps1`——每个本地日历日
  **最多拉一次**、只拉远端最新那一个文件、self-detach 成隐藏 worker 传约 **15 分钟**、
  传完比对远端字节数一致才落 marker。它**不是同步的**，所以必须等：

  ```bash
  tail -3 backups/remote/pull.log      # 等到出现 "worker: pull OK <文件名> (<字节数>)"
  ```

  看到 `pull OK` 之前不要跑 sync 脚本。需要 `qp_tencent` **和** `known_hosts`（§2.4）。

```bash
cd /d/MyWork/10Project/RD/QuantPilot
bash scripts/sync_local_backtest_db.sh
```

脚本自己会 `docker compose -f docker-compose.backtest-local.yml up -d` 并等健康，
不必手动起容器。

⚠️ 该脚本会 **`DROP DATABASE ... WITH (FORCE)`** 重建 5434。**只在全新机器上、或明确要
重灌时跑**。5434 上一旦有了不可重建的产出（如下一步导入的基线快照、面板结果），再跑它就是
数据丢失——本机现在就处于「绝不能再跑」的状态。

恢复完确认（期望 `alembic_version` = **0025**）：

```bash
docker exec qp-backtest-db-5434 psql -U quantpilot -d quantpilot \
  -c "SELECT version_num FROM alembic_version;" \
  -c "SELECT COUNT(*) FROM daily_quote;"
```

**连接串的密码是 `quantpilot`**：`.env` 里**没有** `POSTGRES_PASSWORD`（只有 `.env.prod` 有），
compose 走 `${POSTGRES_PASSWORD:-quantpilot}` 的默认值。5434 只绑 `127.0.0.1`，是可丢弃的
算力临时库，故用默认口令：

```
DATABASE_URL=postgresql+asyncpg://quantpilot:quantpilot@localhost:5434/quantpilot
```

ℹ️ compose 里的 `shared_buffers=512MB` / `effective_cache_size=2GB` 是按 8GB 宿主定的。
32GB 机可以调大，但 **111s/交易日的基线就是在这个配置下测出来的**——先按原样把对照基线
跑出来，调优另说，否则失去可比性。

### 2.7 导入本地独有的基线快照

`ic_baseline_pre_c1_full.sql` 是**自带建表语句**的完整快照（863 KB / 4940 行），
一条命令导入即可：

```bash
cat backups/local-5434/ic_baseline_pre_c1_full.sql | \
  docker exec -i qp-backtest-db-5434 psql -U quantpilot -d quantpilot -v ON_ERROR_STOP=1
```

⚠️ `docker exec` 喂 stdin **必须带 `-i`**；不带时容器内进程拿不到输入，SQL 完全没
执行而 psql 退出码仍是 0（`set -e` 抓不到）。

导入后**必须查行数实证**，不认退出码：

```bash
docker exec qp-backtest-db-5434 psql -U quantpilot -d quantpilot \
  -c "SELECT COUNT(*) FROM ic_baseline_pre_c1;"   # 期望 4940
```

（同目录另有 `pre_c1_panel_ic_*.sql`，是三表 data-only 的更大范围备份，
需要连 `factor_ic_window_state` / `strategy_weights_history` 一起还原时才用。）

### 2.8 让 24h 机真的 24 小时可用

- **关闭休眠与硬盘休眠**：`powercfg /change standby-timeout-ac 0`、
  `powercfg /change hibernate-timeout-ac 0`
- **Docker Desktop 设为开机自启**，且勾选启动后不弹窗。5434 的 compose 已是
  `restart: unless-stopped`，Docker 一起来容器就自动跟上，不需要手动 `up -d`
- 长任务**必须 detached 起**，否则随终端/会话退出而死（2026-08-24 面板跑就这么
  死在第 3 天）：

  ```bash
  nohup bash <driver>.sh off <logfile> > /dev/null 2>&1 &
  ```

  或用「任务计划程序」建一个开机触发的任务。判断任务是否真的活着：看**落盘 log 的
  进度行**，不认通知、不认退出码。

---

## 3. 验收清单

在新机上逐条实证，全绿才算迁移完成：

- [ ] 路径是 `D:\MyWork\10Project\RD\QuantPilot`
- [ ] `git log --oneline -1` 与本机一致；`git status` 干净
- [ ] `git config user.name` / `user.email` / `core.autocrlf` 已设
- [ ] `uv run pytest tests/unit/ tests/e2e/ -q` → 841 passed
- [ ] `uv run ruff check src/ tests/` → 0 error
- [ ] `ssh qp-tencent 'echo ok'` 通（同时验证 `known_hosts` 已拷）
- [ ] `backups/remote/pull.log` 有 `worker: pull OK`（或已用方式 A 拷入 `.sql.gz`）
- [ ] 5434 起来了，`SELECT version_num FROM alembic_version` = **0025**
- [ ] `SELECT COUNT(*) FROM ic_baseline_pre_c1` = 4940
- [ ] `wsl -- free -h` 看到的配额与宿主内存相称（32 GB 机用默认即可，勿照抄 5GB）
- [ ] 休眠已关（`powercfg /query` 确认）
- [ ] Claude Code 起会话后能读到历史 memory（问它「C1 面板对比现在什么状态」，
      答得出 5434/off-on 两组那套细节才算通）

---

## 4. 已知会跟着走的坑

- **三个 DB 端口永不混**：生产 5432 / 测试 5433（pytest 会 DROP 全表）/ 算力 5434。
  新机上同样成立，且新机多了「本机也有个 5434」这层混淆——操作前先确认在哪台机。
- **`git add -A` 禁用**：本仓长期有 `.agents/` `.codex/` `AGENTS.md` 三个未跟踪项，
  `-A` 会把它们连同可能的 `.env` 一起带走。按文件名 add。
- **Bash 工具的 cwd 会漂移**：`uv run` 一律前置 `cd .../backend &&`（venv 在 backend/）。
- 其余技术陷阱见 `CLAUDE.md §4`，事故档案见 memory 索引。

---

## 5. 记忆快照里已知的过期结论（**看到就以本节为准**）

新机的 `memory/` 是 **2026-08-25 的一次性快照**，之后不再重新打包。以下条目在快照之后
被推翻或修正过，**记忆与本节冲突时以本节为准**，并请顺手把新机本地的 memory 文件改对
（改本地文件即可，不必回传本机）。

### 5.1 `c1-panel-comparison-runbook`：`total_equity` 的成因判断是错的

快照里写的是「**该副本的历史 `total_equity` 未回填（生产已于 2026-08 补齐）**」。
**这个解释已被实测否定**（2026-08-26，直接查 5434）：

| `report_period` | 总行数 | `total_equity` 非空 |
|---|---|---|
| 2023 全年 | 1268692 | 225（0.02%）|
| 2024 全年 | 1297621 | 16067（1.2%）|
| 2025-06-30 | 358549 | 5515 |
| 2025-09-30 | 326876 | 5500 |
| 2025-12-31 | 311065 | 5523 |
| 2026-03-31 | 330974 | 5503 |

近期每期恰好 ≈ 5500 行 ≈ **每股一行**（全库 `count(DISTINCT ts_code) = 5507`）。2026-08 那次
回填只覆盖 **2025-06-30 起的 5 个报告期，没有回补历史**。

**这意味着三件事**：

1. 它是**生产数据本身的属性**，不是副本陈旧。新机从任何一份新备份重建 5434，**结果完全一样**，
   不要把它当成"重灌就能解决"的问题去折腾。
2. 面板窗口 2024-07 → 2026-07 **中段有一次 universe 口径切换**（F-4 由跳过转为生效），
   不是均匀的 caveat。解读绝对 IC 的时间序列必须标出该断点，否则会把口径切换误读成因子
   行为变化。
3. off/on 两组跑同一份数据，**差值仍然可比**——对照的内部有效性不受影响。

权威表述见 `docs/design/phases/v1_5_c_strategy_expansion.md` §3.2「面板口径」小节（v0.9）。

### 5.2 若发现更多冲突

判据一律是：**代码 / DB 实测 > 设计文档 > memory**。记忆写的是「当时以为的」，
不是「现在为真的」。发现第三条冲突时，往本节追加一小节，别只在会话里说一句。
