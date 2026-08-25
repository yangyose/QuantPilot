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

**不是洁癖，是硬约束**：Claude Code 的跨会话记忆存在
`~/.claude/projects/D--MyWork-10Project-RD-QuantPilot/memory/`，目录名由项目
路径派生。路径不同 = 全部历史记忆（事故档案、runbook、用户反馈）在新机上认不出来。

### 2.2 装工具链

- **Docker Desktop**（WSL2 后端）
- **Python 3.12** + **uv**
- **Git for Windows**（本仓大量脚本是 bash，`Bash` 工具走的就是 Git Bash）
- Claude Code

⚠️ **建好 `%USERPROFILE%\.wslconfig`**：

```ini
[wsl2]
memory=5GB
```

不设的话 WSL2 会吃光宿主内存并 swap 抖动——曾让回填某些日慢 **30~70 倍**，且
症状伪装成业务层问题（详见 memory `backfill-wsl-memory-thrash`）。24h 机跑长任务，
这条比在本机更要紧。

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
| `~/.ssh/qp_tencent`（+ `~/.ssh/config` 里的 `qp-tencent` 段）| 同路径 |
| `~/.claude/CLAUDE.md` | 同路径（跨项目全局规则）|
| `~/.claude/projects/D--MyWork-10Project-RD-QuantPilot/memory/`（整个目录）| 同路径 |
| `<repo>/backups/local-5434/`（整个目录，含 `ic_baseline_pre_c1_full.sql`）| 同路径。**`backups/` 被 gitignore，git 不会带它走** |

拷完检查 SSH 私钥权限（Windows 下 OpenSSH 会拒绝过宽的 ACL）：

```bash
ssh qp-tencent 'echo ok'    # 通了才算好
```

### 2.5 建 Python 环境

```bash
cd /d/MyWork/10Project/RD/QuantPilot/backend
uv sync --group dev
uv run pytest tests/unit/ tests/e2e/ -q     # 建真实基线，不信任何"应该没问题"
uv run ruff check src/ tests/
```

预期：**837 passed**（2026-08-25 本机基线），ruff **0 error**。数字对不上先查环境，
别改代码。

### 2.6 建算力库（5434）

```bash
cd /d/MyWork/10Project/RD/QuantPilot
# 方式 A：直接从局域网拷一份已下载的备份，省一次 538 MB 公网下载
#   把本机 backups/remote/qp_YYYYMMDD_020001.sql.gz 拷到新机同目录
# 方式 B：让新机自己从腾讯拉（需 qp_tencent 密钥）

bash scripts/sync_local_backtest_db.sh
```

⚠️ 该脚本会 **`DROP DATABASE`** 重建 5434。**只在全新机器上、或明确要重灌时跑**。
5434 上一旦有了不可重建的产出（如下一步导入的基线快照、面板结果），再跑它就是数据
丢失——本机现在就处于「绝不能再跑」的状态。

恢复完确认：

```bash
docker exec qp-backtest-db-5434 psql -U quantpilot -d quantpilot \
  -c "SELECT version_num FROM alembic_version;" \
  -c "SELECT COUNT(*) FROM daily_quote;"
```

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
- **Docker Desktop 设为开机自启**，且勾选启动后不弹窗
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
- [ ] `uv run pytest tests/unit/ tests/e2e/ -q` → 837 passed
- [ ] `uv run ruff check src/ tests/` → 0 error
- [ ] `ssh qp-tencent 'echo ok'` 通
- [ ] 5434 起来了，`alembic_version` 与生产一致
- [ ] `SELECT COUNT(*) FROM ic_baseline_pre_c1` = 4940
- [ ] `%USERPROFILE%\.wslconfig` 有 `memory=5GB`
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
