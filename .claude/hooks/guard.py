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

        # 规则 2：git add -A / . / --all（通用防泄密，不限 prod）
        if re.search(r"\bgit\s+add\s+(-A\b|--all\b|\.(\s|$))", cmd):
            emit("deny",
                 "C-1 防误传凭证：禁止 git add -A / . / --all，"
                 "请按文件名逐个 add（防 .env/密钥/大二进制误入仓库）。")

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
