"""PreToolUse 红线守卫逻辑（被 guard.sh 调用，JSON 从 stdin 读）。

三条规则（fail-open：解析失败/不匹配一律放行）：
  1. [C-1] 生产环境破坏性动作 → permissionDecision=ask（破坏性 AND 命中生产栈信号）
  2. [防泄密] git add -A / . / --all → deny
  3. [防 regression] 测试文件写入 @pytest.mark.anyio → deny
"""
import json
import re
import sys


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

        # 规则 1：生产环境破坏性动作 = 破坏性 AND prod 信号
        prod = re.search(
            r"docker-compose\.prod\.yml|\.env\.prod|quantpilot-(db|backend|redis|nginx)-1",
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
