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
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"

B = "Bash"
CASES = [
    # (说明, tool, tool_input, 期望 decision；None = 应放行)
    # ---- 规则 1b：无条件确认（不依赖 DB 信号）----
    ("sync 脚本", B, {"command": "bash scripts/sync_local_backtest_db.sh"}, "ask"),
    ("sync 脚本 --force-wipe", B,
     {"command": "bash scripts/sync_local_backtest_db.sh --force-wipe"}, "ask"),
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
        input=json.dumps({"tool_name": tool, "tool_input": ti}),
        capture_output=True, text=True,
    )
    if not p.stdout.strip():
        return None, p.returncode
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], p.returncode


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
    print(f"\n{len(CASES) - fail}/{len(CASES)} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
