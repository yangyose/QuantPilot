"""PreToolUse 红线守卫逻辑（被 guard.sh 调用，JSON 从 stdin 读）。

四条规则（fail-open：解析失败/不匹配一律放行）：
  1. [C-1] 受保护 DB 上的破坏性动作 → ask（破坏性 AND 命中生产栈/5434 信号）
  1b.[C-1] 无条件确认的破坏性动作 → ask（不依赖 DB 信号：reset --hard / push --force /
     宽泛目标的 rm -rf / sync_local_backtest_db.sh）
  2. [防泄密] git add -A / . / --all → deny
  3. [防 regression] 测试文件写入 @pytest.mark.anyio → deny

⚠️ fail-open 有两个入口：找不到解释器（guard.sh）与 **JSON 解析失败**（本文件）。
   两者都表现为「零输出」，自检时务必用文件重定向而非 echo 管道，见
   docs/guides/machine_migration.md §2.2。
"""
import json
import re
import sys

# 输出流不得因编码而抛异常：guard.py 崩溃 = 非零退出 = **fail-open**（PreToolUse
# 只有 exit 2 才拦截，其余非零码一律放行），所以一个 UnicodeEncodeError 就能把
# deny 悄悄变成放行。ja-JP 机器管道下 stdout 是 cp932，编不出中文即崩。
# 正常路径不依赖这层兜底——下面 json.dumps 保持默认 ensure_ascii=True，输出恒为
# 纯 ASCII（test_guard.py 有一条用例钉死这个不变量）；此处只防将来新增的打印。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def strip_git_heredocs(cmd: str) -> str:
    """去掉**由 git 命令引入的** heredoc 正文——那是数据（提交信息），不是命令。

    为什么必须区分「谁引入的 heredoc」而不是一律剥掉：
    `python - <<'PY' ... open(p,"w") ... PY` 的写操作**就在正文里**，
    一律剥掉会让规则 4 漏掉它自己最该拦的那种形态。

    为什么必须剥 git 那种：描述「刚才用 sed -i 改了 CLAUDE.md」是提交信息里
    极自然的措辞，不剥就会把一次完全正当的 `git commit -F-` 拦下——
    2026-09-09 规则首次启用当天就这么误伤了自己两次。
    """
    lines = cmd.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)(\w+)\1", line)
        if m and re.search(r"\bgit\b", line):
            delim = m.group(2)
            i += 1
            while i < len(lines) and lines[i].strip() != delim:
                i += 1        # 丢弃正文
            if i < len(lines):
                out.append(lines[i])   # 保留结束定界符
        i += 1
    return "\n".join(out)


def emit(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # 解析失败 → 放行

    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    if tool == "Bash":
        cmd = ti.get("command", "") or ""
        low = cmd.lower()

        # 规则 3：用 Bash 改「有评审钩子的文件」= 静默绕过评审（2026-09-09 加）
        #
        # PostToolUse 的 matcher 是 `Edit|Write`，且 claude_md_review.sh /
        # design_doc_review.sh 都靠 `tool_input.file_path` 定位文件。**Bash 的
        # tool_input 只有 `command`、没有 file_path** → path="" → basename 不匹配
        # → 脚本静默 exit 0。于是用 heredoc/sed 改 CLAUDE.md 或设计文档时，
        # 评审**一次都不会触发**，而且没有任何提示——正是 §4.11「接了但没生效」
        # 那一族：钩子配着、看着对、什么也没做。
        #
        # 本会话实测：整场的 CLAUDE.md 与设计文档改动全部走 bash，零次评审被拉起。
        # 判据不是「钩子装了没」，而是「它生效时会留下的痕迹」——agent 有没有被起。
        #
        # 为什么 deny 而不是 ask：2026-08-27 实测，自动放行模式下 ask 不弹确认框
        # （见规则 1c 的注释）。ask 在这里等于放行。
        #
        # ⚠️ 只拦**写**，不拦读：grep/sed -n/cat 这些照常。判据是命令里是否出现
        # 「写构造」——重定向到该路径 / sed -i / tee / Python 以写模式 open。
        _PROT = r"(CLAUDE\.md|docs/(design|spec)/[^\s\"']*\.md)"
        # ⚠️ 先剥掉 git 引入的 heredoc 正文（提交信息是数据，不是命令）。
        # 不剥会把一次完全正当的 `git commit -F-` 拦下——只要信息里提到
        # `sed -i` 和 `CLAUDE.md`（描述刚做过的改动时几乎必然提到）。
        # 2026-09-09 规则首次启用当天连着误伤自己两次，第一次错在「只判命令是否以
        # git 开头」——而实际命令前面还有 `cd ... &&`，永远匹配不上。
        # 这正是 §4.12 说的「规则写宽了没人发现」，只不过这次代价当场发作。
        scan = strip_git_heredocs(cmd)
        if re.search(_PROT, scan):
            wrote = (
                # `> path` / `>> path`（重定向目标就是它）
                re.search(r">>?\s*[\"']?[^\s\"'|]*" + _PROT, scan)
                # 就地编辑 / tee —— **必须与路径同处一个命令段**（不跨 ; && || |），
                # 否则 `sed -i ... other.txt && grep CLAUDE.md` 这种会被误杀
                or re.search(r"\bsed\s+-i\b[^;&|]*" + _PROT, scan)
                or re.search(r"\btee\b[^;&|]*" + _PROT, scan)
                # Python 以写模式打开。⚠️ 这一条**只能扫未剥的正文**——python heredoc
                # 的写操作就在正文里，正是本规则最该拦的形态。
                #
                # ⚠️ 但必须**同时**要求受保护路径以「带引号的字符串字面量」出现
                # （2026-09-10 第三次误伤后加）：只要求「路径在命令里出现过 + 有写模式
                # open」会把「注释里提到 CLAUDE.md、实际写的是别的文件」也拦下——
                # 本条注释所在的这次修改自己就被拦了一回。
                # 判别力来自引号：真要写它必然是 `p='CLAUDE.md'` / `open("docs/design/x.md","w")`；
                # 行文提及则是 `# 见 CLAUDE.md §4.12` 这种裸文本。
                # （重定向 / sed -i / tee 三条不需要这个约束——它们已把路径与构造绑在一起。）
                or (
                    re.search(r"open\s*\([^)]*[\"'](w|a)[\"']|write_text\s*\(", scan)
                    and re.search(r"[\"']" + _PROT + r"[\"']", scan)
                )
            )
            if wrote:
                emit("deny",
                     "C-6 评审绕过：用 Bash 写 CLAUDE.md / 设计文档，会让 PostToolUse 的"
                     "评审钩子静默失效（它只匹配 Edit|Write，且靠 tool_input.file_path "
                     "定位文件，而 Bash 没有该字段）。请改用 **Edit / Write 工具**——"
                     "那样评审才会被触发。读操作（grep / sed -n / cat）不受影响。")

        # 规则 2：git add -A / . / --all（通用防泄密，不限 prod）
        if re.search(r"\bgit\s+add\s+(-A\b|--all\b|\.(\s|$))", cmd):
            emit("deny",
                 "C-1 防误传凭证：禁止 git add -A / . / --all，"
                 "请按文件名逐个 add（防 .env/密钥/大二进制误入仓库）。")

        # 规则 1c：sync 脚本的 --force-wipe → deny（不是 ask）
        # 它销毁的是**生产库里没有、只在 5434 上**的算力产出：ic_baseline_pre_c1
        # 4940 行（重造约 57 小时）+ 面板 IC 行。而 5434 装上这些产出后已禁止再 sync，
        # 所以这个动作不可逆、也无处可恢复。
        # 为什么不用 ask：2026-08-27 实测，在「Bash 自动放行」的权限模式下钩子的 ask
        # **不会浮出确认框**（同会话 deny 仍然生效，git add -A 被正常拦下）——那层
        # "二次确认"在这种模式下是空的。真要执行时由人在终端手敲，自动化不该碰得到。
        if re.search(r"sync_local_backtest_db\.sh", low) and "--force-wipe" in low:
            emit("deny",
                 "C-1 不可逆销毁：sync_local_backtest_db.sh --force-wipe 会毁掉 5434 上"
                 "生产库没有的算力产出（ic_baseline_pre_c1 4940 行 ≈ 57 小时重造 + 面板 IC 行），"
                 "且该库已禁止再 sync，无处恢复。确需执行请由人在终端手动运行；"
                 "先按脚本提示 pg_dump 备份受影响表。")

        # 规则 1b：无条件确认（不依赖 DB 信号）——C-1 列了六类破坏性动作，
        # 原实现只覆盖到「DB 相关」那几类，以下三类此前完全没有拦截。
        always = None
        if re.search(r"sync_local_backtest_db\.sh", low):
            always = ("sync_local_backtest_db.sh（DROP DATABASE 重建 5434）。"
                      "库里若已有 ic_baseline_pre_c1 / 面板 IC 行，重灌即永久丢失"
                      "（重造数十小时）。脚本自身也有拒绝保护，此处二次确认。")
        elif re.search(r"\bgit\s+reset\s+--hard\b", low):
            always = "git reset --hard（丢弃未提交改动——用户资产，且不可撤销）"
        elif re.search(r"\bgit\s+push\b", low) and re.search(
                r"(--force(?!-with-lease)\b|\s-f\b)", low):
            always = "git push --force（改写远端历史，可能覆盖他机已推送的提交）"
        elif re.search(r"\brm\s+-[a-z]*r", low) and re.search(
                # 全局规则：禁止以 根 / 家目录 / 盘符根 / 未解析变量 / 宽泛通配 为递归删除目标
                r"\s(/|~|~/|\$HOME\b|\$\{HOME\}|[a-z]:[\\/])(\s|$)"
                r"|\s\$\{?\w+\}?[/\\]"
                r"|[/\\]\*(\s|$)",
                cmd, re.I):
            always = ("rm -r 的目标是 根/家目录/盘符根/未解析变量/宽泛通配 之一"
                      "（个人全局规则明令禁止）")

        if always:
            emit("ask", f"C-1 破坏性动作：{always}。确认确为本次有意操作后再放行。")

        # 规则 1：受保护 DB 上的破坏性动作 = 破坏性 AND 受保护 DB 信号
        # 5434 是本地算力库：装着生产库里没有的产出（ic_baseline_pre_c1、面板 IC 行），
        # 与生产 5432 同等对待。测试库 5433 故意不在此列（那本就是给 pytest 拆的）。
        prod = re.search(
            r"docker-compose\.prod\.yml|\.env\.prod|quantpilot-(db|backend|redis|nginx)-1"
            r"|docker-compose\.backtest-local\.yml|qp-backtest-db-5434|:5434\b",
            cmd,
        ) is not None

        destructive = None
        if re.search(r"alembic\s+downgrade", low):
            destructive = "alembic downgrade（迁移回滚，可能丢表/数据）"
        elif re.search(r"\bdrop\s+(table|schema|database)\b|\btruncate\b", low):
            destructive = "DROP / TRUNCATE（直接删表 / 清空）"
        elif re.search(r"\bdown\b.*(-v\b|--volumes\b)", low):
            destructive = "compose down -v（删卷，灭 pg_data）"
        elif re.search(r"\bvolume\s+rm\b", low):
            destructive = "docker volume rm（删数据卷）"
        elif re.search(r"\bpytest\b.*integration", low):
            destructive = "pytest integration（conftest 会 alembic downgrade base，DROP 所有表）"

        if prod and destructive:
            emit("ask",
                 f"C-1 生产环境破坏性动作：{destructive}。命令命中生产栈信号"
                 "（docker-compose.prod.yml / .env.prod / quantpilot-*-1）。"
                 "确认确为本次有意操作、且已对用户资产风险知情后再放行。")

        sys.exit(0)  # 非 prod 或非破坏性 → 放行

    if tool in ("Edit", "Write"):
        content = ti.get("new_string") or ti.get("content") or ""
        path = (ti.get("file_path", "") or "").replace("\\", "/")
        if "/tests/" in path and path.endswith(".py") and "@pytest.mark.anyio" in content:
            emit("deny",
                 "项目禁用 @pytest.mark.anyio（asyncio_mode=auto 下 marker 被 anyio runner "
                 "接管，asyncpg waiter 跨 loop → RuntimeError，已 regression 2 次）。"
                 "新写 async 测试用 plain `async def test_xxx()`，不加任何 marker。")
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
