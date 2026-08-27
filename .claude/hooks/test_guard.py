"""guard.py 规则回归夹具（2026-08-26 建）。

    python .claude/hooks/test_guard.py

全绿 = 守卫在本机可用（解释器能跑 + JSON 可解析 + 各规则命中正确）。
这取代了此前文档里那三条手敲的 `echo '{...}' | python guard.py`：那种写法在
cmd.exe 里单引号不是定界符 → guard.py 收到非法 JSON → **静默 exit 0**，
与「守卫已死」表现完全相同（2026-08-26 配第二台机时真踩到过）。

新增/修改 guard.py 规则时**必须**在此补用例，且正反两面都要钉：
「该拦的拦住」单独绿不算数，「不该拦的别拦」同样重要——否则规则写宽了没人发现。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# 输出流强制 UTF-8：管道/重定向时 Python 按 locale 编码写 stdout（ja-JP 机器是
# cp932），下面用例说明里的中文编不出去 → UnicodeEncodeError 在打印途中崩掉，
# 只跑出一半用例就带着 traceback 退出，与「守卫已死」几乎无法区分。
# 控制台直连时 Windows 走 WriteConsoleW 不受 codepage 影响，所以这个崩溃**只在
# 管道/重定向下出现**——而 Claude Code 跑命令恰恰是管道（2026-08-26 第二台机实测）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # 非 TextIOWrapper（已被重定向包装）时跳过
        pass

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"

B = "Bash"
CASES = [
    # (说明, tool, tool_input, 期望 decision；None = 应放行)
    # ---- 规则 1b：无条件确认（不依赖 DB 信号）----
    ("sync 脚本", B, {"command": "bash scripts/sync_local_backtest_db.sh"}, "ask"),
    # --force-wipe 是 deny 不是 ask：ask 在「Bash 自动放行」模式下不弹确认（实测），
    # 而它销毁的算力产出无处恢复。相邻的 --force / 裸调用仍是 ask——写宽了会在下面露馅。
    ("sync 脚本 --force-wipe（不可逆）", B,
     {"command": "bash scripts/sync_local_backtest_db.sh --force-wipe"}, "deny"),
    ("sync 脚本 --force（仍是 ask）", B,
     {"command": "bash scripts/sync_local_backtest_db.sh --force"}, "ask"),
    ("reset --hard", B, {"command": "git reset --hard origin/main"}, "ask"),
    ("push --force", B, {"command": "git push --force origin main"}, "ask"),
    ("push -f", B, {"command": "git push -f origin main"}, "ask"),
    ("push --force-with-lease（较安全，不拦）", B,
     {"command": "git push --force-with-lease origin main"}, None),
    ("rm -rf 根", B, {"command": "rm -rf /"}, "ask"),
    ("rm -rf 家目录", B, {"command": "rm -rf ~"}, "ask"),
    ("rm -rf 未解析变量", B, {"command": "rm -rf $BUILD_DIR/out"}, "ask"),
    ("rm -rf 盘符根", B, {"command": "rm -rf D:/"}, "ask"),
    ("rm -rf 宽泛通配", B, {"command": "rm -rf backups/*"}, "ask"),
    ("rm -rf 具体相对路径（不拦）", B, {"command": "rm -rf backend/.venv"}, None),
    ("rm 非递归（不拦）", B, {"command": "rm -f tmp.log"}, None),
    # ---- 规则 1：受保护 DB（生产 5432 + 算力 5434）----
    ("5434 + pytest integration", B,
     {"command": "DATABASE_URL=postgresql+asyncpg://u:p@localhost:5434/quantpilot "
                 "uv run pytest tests/integration/"}, "ask"),
    ("5434 容器 + DROP", B,
     {"command": "docker exec qp-backtest-db-5434 psql -c \"DROP TABLE x\""}, "ask"),
    ("backtest-local compose down -v", B,
     {"command": "docker compose -f docker-compose.backtest-local.yml down -v"}, "ask"),
    ("生产 DROP", B,
     {"command": "docker exec quantpilot-db-1 psql -c \"DROP TABLE x\""}, "ask"),
    ("prod compose + downgrade", B,
     {"command": "docker compose -f docker-compose.prod.yml exec backend alembic downgrade -1"},
     "ask"),
    # ---- 规则 2：防误传凭证 ----
    ("全量 add", B, {"command": "git add --all"}, "deny"),
    ("全量 add 点号", B, {"command": "git add ."}, "deny"),
    ("按名 add（不拦）", B, {"command": "git add CLAUDE.md"}, None),
    # ---- 放行面（写宽了会在这里露馅）----
    ("5433 测试库 pytest（不拦）", B,
     {"command": "DATABASE_URL=postgresql+asyncpg://u:p@localhost:5433/quantpilot "
                 "uv run pytest tests/integration/"}, None),
    ("裸 pytest integration（不拦）", B,
     {"command": "uv run pytest tests/integration/"}, None),
    ("普通命令（不拦）", B, {"command": "uv run ruff check src/"}, None),
    # ---- 规则 3：防 anyio regression ----
    ("anyio 写入测试文件", "Write",
     {"file_path": "backend/tests/unit/test_x.py",
      "content": "@pytest.mark.anyio\nasync def test_a(): ..."}, "deny"),
    ("anyio 但非测试目录（不拦）", "Write",
     {"file_path": "backend/src/x.py", "content": "@pytest.mark.anyio"}, None),
]


def run(tool, ti):
    p = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": tool, "tool_input": ti}).encode("utf-8"),
        capture_output=True,
    )
    # 取字节再显式解码，不用 text=True：后者按 locale 编码解（ja-JP 机器是 cp932），
    # guard.py 一旦输出非 ASCII，解码就在 subprocess 内部炸掉、p.stdout 变 None →
    # 夹具整体崩溃而非判 FAIL，「守卫坏了」于是伪装成「夹具坏了」。同理 json 解析
    # 失败也要收成 None（= 判 FAIL），不能让它掀掉整轮用例。
    out = p.stdout.decode("utf-8", errors="replace").strip()
    if not out:
        return None, p.returncode
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"], p.returncode
    except (ValueError, KeyError):
        return None, p.returncode


def check_ascii_output():
    """守卫输出必须是纯 ASCII（不变量，不是风格偏好）。

    guard.py 崩溃 = 非零退出 = fail-open（PreToolUse 只有 exit 2 才拦截），所以
    「stdout 写不出去」与「守卫放行」在外部完全不可区分。这里用窄编码环境跑一次
    deny 用例，并**直接验字节**而不是解码后的文本——把 json.dumps 改成
    ensure_ascii=False、或在 emit 路径上新增中文 print，都会在这条露馅，
    而不是等换到一台 locale 不同的机器上才发作。
    """
    payload = json.dumps({"tool_name": "Write", "tool_input": {
        "file_path": "backend/tests/unit/test_x.py",
        "content": "@pytest.mark.anyio\nasync def test_a(): ...",
    }})
    p = subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload.encode("utf-8"), capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )
    try:
        got = json.loads(p.stdout.decode("ascii"))["hookSpecificOutput"]["permissionDecision"]
    except (UnicodeDecodeError, ValueError, KeyError):
        got = None
    return got, p.returncode


def main() -> int:
    if not GUARD.is_file():
        print(f"找不到 guard.py: {GUARD}")
        return 2
    fail = 0
    for desc, tool, ti, want in CASES:
        got, rc = run(tool, ti)
        ok = got == want
        fail += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {desc:34s} "
              f"want={str(want):5s} got={str(got):5s} rc={rc}")

    # 编码不变量：不属于规则命中，单独跑一条
    got, rc = check_ascii_output()
    ok = got == "deny"
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {'窄编码下输出仍为纯 ASCII':34s} "
          f"want={'deny':5s} got={str(got):5s} rc={rc}")

    total = len(CASES) + 1
    print(f"\n{total - fail}/{total} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
