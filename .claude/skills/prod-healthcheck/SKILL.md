---
name: prod-healthcheck
description: QuantPilot 生产体检与修复运行手册——查每日管线/回填/后台任务状态、检测数据缺口与孤儿 run、跨表核验数据完整性、补跑缺失交易日、回填 OOM 诊断与断点续传恢复。当用户问“看回填状态 / 检查后台任务 / 每日任务跑了吗 / 某天数据在不在 / 数据有没有缺口 / 补数据”时使用。
---

# QuantPilot 生产体检与修复

生产期常驻运维手册。固化了已验证的命令与已踩过的坑。**所有操作遵守 CLAUDE.md §0 宪法 C-1**：生产 DB 是用户资产，写操作前必须取得用户单独确认。

## 环境事实（本机 / 本项目）

- 后端容器 `quantpilot-backend-1`；DB 容器 `quantpilot-db-1`。**这两个名字不是写死在 yml 里的**（服务名只是 `backend`/`db`，无 `container_name:`），而是 compose 自动拼的 `<项目名>-<服务名>-1`，项目名默认 = 目录名小写（`QuantPilot`→`quantpilot`）。本手册命令默认用这两个名字；**换机器/换目录前先用下方「换机适配」第 1 步确认真实名再替换**。
- 当前生产栈 = `docker-compose.prod.yml` + `.env.prod`（已由容器 compose 标签确认）。
- 生产 DB：user=`quantpilot` db=`quantpilot`，端口 5432**仅容器内部，无 host 映射**（测试 DB 才是 5433，**永远别混**）。

## 换机适配（克隆到别的机器/目录时）

本 skill 是**针对当前这套部署**的运维手册，不是通用工具。换环境前按此校正，避免静默失效：

1. **容器名动态发现**（别假设是 `quantpilot-*`）——Bash tool：
   ```bash
   BACKEND=$(docker compose -f docker-compose.prod.yml ps -q backend | xargs -r docker inspect --format '{{.Name}}' | sed 's#^/##')
   DB=$(docker compose -f docker-compose.prod.yml ps -q db | xargs -r docker inspect --format '{{.Name}}' | sed 's#^/##')
   echo "BACKEND=$BACKEND DB=$DB"
   ```
   或快速版：`docker ps --format "{{.Names}}"` 找含 `backend`/`db` 的名字。后续命令把 `quantpilot-backend-1`/`quantpilot-db-1` 换成发现到的值。
2. **DB 凭据来自 `.env.prod`**（gitignore，不入库）——fresh clone 没有它。确认 `POSTGRES_USER`/`POSTGRES_DB`（默认都是 `quantpilot`，但别人可能改）后再用 `psql -U <user> -d <db>`。
3. **本机/平台事实需重判**：OOM 阈值（本机 WSL2 ~3.4GiB VM）、PowerShell vs Git Bash 路径坑（仅 Windows 上跑 Claude Code）、调度器 17:30 北京时间、以及"栈已 up 且有 5y 数据"的前提（新 clone 是空卷，须先 `up` + 回填）。
- 应用日志 `/app/logs/quantpilot.log`（JSON 行）；回填日志 `/app/logs/backfill_candidate_pool_5y.log`。
- 每日管线由 APScheduler 在 **17:30（北京时间）** 触发；`pipeline_run.started_at` 存的是 **UTC**（09:30Z = 17:30 CST），看的时候 +8。
- 评分输入历史（daily_quote / financial_data / index_history）应为满 5 年（2021-05-13 起）；candidate_pool 是评分**输出快照**，不是评分输入。

## 平台坑（务必照做，否则白跑）

- **查 `/app/...` 路径的命令用 PowerShell tool，不要用 Bash tool**——Git Bash 会把 `/app/logs/...` 翻译成 `C:/Program Files/Git/app/...` 导致 “No such file”。
- 容器内**没有 `ps`**，查进程用 `/proc`（见下）。
- **PowerShell 里禁用 `Start-Sleep` + 命令链等待**（harness 会拦），等待用 Bash `run_in_background` 的 `until` 轮询循环。
- 容器内 `sh` 不支持 `<` 重定向经 PowerShell 传入时易解析失败；遍历 `/proc` 用 Bash tool 写 `case` 循环。
- API 登录 JSON 用 **Bash tool** 发（PowerShell 双引号里的 `\"` 会变字面反斜杠 → JSON decode error）。

---

## 1. 后台任务 / 回填状态

**进程是否存活**（Bash tool；`ps` 不可用）：
```bash
docker exec quantpilot-backend-1 sh -c 'self=$$; hit=0; for p in $(ls /proc 2>/dev/null | grep -E "^[0-9]+$"); do [ "$p" = "$self" ] && continue; c=$( (tr "\0" " " < /proc/$p/cmdline) 2>/dev/null ); case "$c" in *candidate_pool.py*) echo "BACKFILL_ALIVE PID=$p"; hit=1;; esac; done; if [ "$hit" = "0" ]; then echo "NO_BACKFILL_RUNNING"; fi'
```
活着 → 多个 `BACKFILL_ALIVE PID=`（setsid 父 + python 子）；`NO_BACKFILL_RUNNING` → 进第 4 节做 OOM 诊断。

> **坑（已踩，两条都跟这条命令有关）**：
> 1. 扫描命令自身的 cmdline 里含被搜字符串，会**自我命中**打出假的 `RUNNING PID=xxx`（PID 还随每次调用递增），让人误判回填在跑。必须 `self=$$` 排除扫描自身 PID；匹配用 `*candidate_pool.py*`（不含 `backfill`，避开命令里的字面 `backfill_`）；并用显式 `NO_BACKFILL_RUNNING` 兜底——"无输出"太容易被误读成"没查到"而非"没在跑"。
> 2. **结尾必须用 `if ...; then ...; fi`，不要用 `[ "$hit" = "0" ] && echo`**：后者在回填**活着**（hit=1）时 `&&` 短路，整个 `sh -c` 退出码 = 1，被 harness 当工具 error；若与别的命令放在**同一个并行批次**里，会连带取消同批的其它调用（白跑一轮）。`if/fi` 形式条件为假时退出码 0，健康路径不再报错。
> 3. 读 cmdline 用 `c=$( (tr ... < /proc/$p/cmdline) 2>/dev/null )` 把重定向包进**子shell**——`/proc` 遍历有竞态（短命子进程在 `<` 打开前就消失），直接 `< ... 2>/dev/null` 挡不住 shell 重定向打开失败的报错（重定向先于 `2>` 生效），会刷一堆 `cannot open /proc/NNN/cmdline` 噪音误导读者；包进子shell后整体 stderr 才被吞净。

**OOM 体检**（PowerShell tool）——容器 `Status=running` 不代表回填活着，子进程可能被 OOM 杀掉而 uvicorn(PID 1) 幸存：
```powershell
docker inspect quantpilot-backend-1 --format "StartedAt={{.State.StartedAt}} RestartCount={{.RestartCount}} OOMKilled={{.State.OOMKilled}} Status={{.State.Status}}"
```
`OOMKilled=true` + 进程查不到 = 回填被 OOM 杀了（VM 内存耗尽，`MemLimit=0` 表示无容器级上限，是 WSL2 VM 级）。

**进度**（PowerShell tool）：
```powershell
docker exec quantpilot-db-1 psql -U quantpilot -d quantpilot -tAc "SELECT COUNT(DISTINCT trade_date) FROM candidate_pool WHERE trade_date BETWEEN '<resume_start>' AND '<resume_end>';"
docker exec quantpilot-backend-1 tail -3 /app/logs/backfill_candidate_pool_5y.log
```
节奏参考：2021 段 ~85s/天，2025-26 段 ~130-140s/天（universe 更大）。

> **坑（已踩）**：上面的 COUNT 只反映"区间内有多少天有 candidate_pool"，**不等于回填进度**——每日管线每天也往 candidate_pool 写当日交易日。若续跑区间末尾覆盖了近几个交易日（如 `--end` 到本月），那几天会被每日管线**先**写进去，COUNT 里多出来的就是它们，不是回填跑出来的。判断回填真实推进看**日志 `tail`**（最后一条 `score_universe_done: date=` 才是回填当前到达的日期），别只看 COUNT。

---

## 2. 每日管线缺口体检

**最近交易日的 run 状态**（PowerShell tool）——找 `RUNNING`（卡死孤儿）或缺失行：
```powershell
docker exec quantpilot-db-1 psql -U quantpilot -d quantpilot -c "SELECT id, trade_date::text, status, started_at, finished_at, signal_count, cp1_data_ready AS cp1, cp2_scoring_done AS cp2, cp3_signals_done AS cp3, error_msg FROM pipeline_run WHERE trade_date >= '<recent_date>' ORDER BY trade_date;"
```
- `RUNNING` 且无 finished_at 多时 = 孤儿（多半是跑到一半容器被重启）。补跑会自愈（见第 3 节）。
- **某交易日完全无行** = 当天 job 没触发或被杀。
- **当天还没到 17:30** = 正常没跑，不是缺口。先确认调度器活着：日志里当天有 `stop_loss_warn_done`（15:05）或别的 job 即证明调度器健康。

---

## 3. 数据完整性核验 + 补跑缺失交易日

**五表核验某交易日**（PowerShell tool）：
```powershell
docker exec quantpilot-db-1 psql -U quantpilot -d quantpilot -c "SELECT 'daily_quote' AS tbl, COUNT(*) FROM daily_quote WHERE trade_date='<D>' UNION ALL SELECT 'financial_data', COUNT(*) FROM financial_data WHERE publish_date='<D>' UNION ALL SELECT 'index_history', COUNT(*) FROM index_history WHERE trade_date='<D>' UNION ALL SELECT 'candidate_pool', COUNT(*) FROM candidate_pool WHERE trade_date='<D>' UNION ALL SELECT 'signal', COUNT(*) FROM signal WHERE trade_date='<D>' UNION ALL SELECT 'market_state', COUNT(*) FROM market_state_history WHERE trade_date='<D>';"
```
健康基线：daily_quote/financial ~5500、index ~4、candidate_pool ~50-62、signal ~48-50、market_state 1。

**补跑（写生产 DB → 先取得用户确认）**：走生产端点 `POST /pipeline/trigger`（= UI“重新触发”，优于另起脚本；CLAUDE.md §8.3）。它会复用已存在的孤儿 run（`_get_or_create_run` 不被 UNIQUE 挡），`run()` 按 `cp1/cp2/cp3=false` 重跑每一步，末尾置 `SUCCESS` → **孤儿自愈，无需手动改 FAILED**；run 的 `config_snapshot` 若已写则不覆盖，保 PIT 配置正确。

登录 + 触发（**Bash tool**，密码见 `.env` ADMIN_PASSWORD_HASH 对应明文，**勿写入本文件**）：
```bash
docker exec quantpilot-backend-1 sh -c '
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"<PWD>\"}" | sed -n "s/.*\"access_token\":\"\([^\"]*\)\".*/\1/p")
curl -s -X POST http://localhost:8000/api/v1/pipeline/trigger -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d "{\"trade_date\":\"<D>\"}" -w "\ntrigger=%{http_code}\n"
'
```
非交易日端点会返回 400（`is_trade_date` 校验）。触发是 BackgroundTask，立即返回；等终态用 Bash `run_in_background` 轮询：
```bash
until s=$(docker exec quantpilot-db-1 psql -U quantpilot -d quantpilot -tAc "SELECT status FROM pipeline_run WHERE trade_date='<D>';"); [ "$s" = "SUCCESS" ] || [ "$s" = "FAILED" ]; do sleep 10; done; echo "FINAL_STATUS=$s"
```
完成后回到本节五表核验确认补齐。

---

## 4. 回填 OOM 诊断与断点续传恢复

1. 确认死因：第 1 节 OOM 体检（`OOMKilled=true` + 进程没了）。
2. 确认数据干净：最后一条 `score_universe_done` 的日期是最后完整日；它之后那天若 candidate_pool 0 行 = 写库前被杀，**无脏数据**（写库在打分完成之后）。
3. 断点续传（`backfill_candidate_pool.py` 双模式）：
   - **默认模式**跳过已存在日期（`get_existing_candidate_pool_dates` 双表交集）。
   - **`--force`** 覆盖（upsert 不删行）。原 5y run 用 `--force` 全量覆盖（含早期 seed 行）→ 续跑仍须 `--force` 才能覆盖剩余范围里的旧 seed 日，否则会被当“已存在”跳过留下陈旧数据。
   - 续跑范围 = 最后完整日的**下一交易日** → 原 `--end`。已完成部分在范围外不受影响；重启重置内存 → 剩余天数少 → 不会再 OOM。
4. 后台重启（Bash tool；`docker exec -d` + nohup setsid，先往日志追一行 RESUME 标记便于区分）：
```bash
docker exec -d quantpilot-backend-1 sh -c 'cd /app && nohup setsid .venv/bin/python scripts/backfill_candidate_pool.py --start <next_day> --end <orig_end> --force --skip-confirm >> /app/logs/backfill_candidate_pool_5y.log 2>&1'
```
5. 验证起来了（第 1 节查进程 + tail 看 `to_process` 计划行）。

回填完成后续接：`backfill_icir_rebalance.py` → `backfill_attribution_history.py`（CLAUDE.md §6 / system_design §9）。

---

## 红线（不可违反）

- 任何写生产 DB 的动作（补跑、回填 `--force`、改 run 状态）**先取得用户单独确认**。
- **严禁**对生产 DB 跑 `pytest tests/integration/`（conftest 会 alembic downgrade base 把表 DROP）。
- 补跑/回填并发跑没问题（已验证），但若叠加多个重打分任务注意 VM 内存（3.4GiB）。
