#!/usr/bin/env python3
"""auto_test.sh 的回归夹具。

判据：`python .claude/hooks/test_auto_test.py` 输出 `18/18 passed`。

## 为什么补这个夹具

四个钩子里它此前是**唯一没有夹具**的，而 CLAUDE.md §4.12 的标准是
「判据不是装了没，而是跑夹具」。后果在 2026-09-10 显形：本钩子对 **Bash 写入**
完全是瞎的（`tool_input` 只有 `command`、没有 `file_path`），整场会话用 heredoc
改 backend 的 .py，自动测试**一次都没跑过**，且没有任何提示——
与「跑了而且全过」表现完全相同。

## 干跑档

本钩子真触发一次要跑约 2 分钟的 unit+e2e，十几条用例逐个真跑不可行。
故脚本带 `QP_AUTO_TEST_DRY_RUN=1`：只打印 `WOULD_RUN <suites> <path>` 后退出。
夹具验的是**判定逻辑**（该不该触发、要不要带 integration），不是 pytest 本身。

## 正反两面都必须钉

反向用例在这里尤其重要，因为**触发写宽了的代价是每条 Bash 命令都跑两分钟测试**
——那会把人逼着去关掉钩子，比不触发更糟。所以「`grep` 某个 .py 不触发」
这类用例和「heredoc 写 .py 要触发」同等重要。

编码与 bash 探测坑见 test_claude_md_review.py 的模块 docstring（同一组）。
"""

import json
import os
import pathlib
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOOK = pathlib.Path(__file__).resolve().parent / "auto_test.sh"
BS = chr(92)


def _find_bash():
    """挑一个看得见本仓文件的 bash（裸 bash 在 Windows 上极可能是 WSL 的）。"""
    target = HOOK.as_posix()
    for cand in ("C:/Program Files/Git/bin/bash.exe",
                 "C:/Program Files (x86)/Git/bin/bash.exe",
                 (os.environ.get("PROGRAMFILES", "") or "") + "/Git/bin/bash.exe",
                 "bash"):
        if not cand:
            continue
        try:
            p = subprocess.run([cand, "-c", 'test -f "$1"', "_", target],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError):
            continue
        if p.returncode == 0:
            return cand
    return None


BASH = _find_bash()

# (名称, payload, 期望触发, 期望带 integration)
CASES = [
    # ---------- Edit/Write：应触发 ----------
    ("edit_src", {"tool_name": "Edit", "tool_input": {
        "file_path": "backend/src/quantpilot/engine/scorer.py"}}, True, False),
    ("edit_win_sep", {"tool_name": "Edit", "tool_input": {
        "file_path": "D:" + BS + "MyWork" + BS + "QuantPilot" + BS + "backend"
                     + BS + "src" + BS + "quantpilot" + BS + "engine" + BS + "scorer.py"}},
     True, False),
    ("write_unit_test", {"tool_name": "Write", "tool_input": {
        "file_path": "backend/tests/unit/test_x.py"}}, True, False),
    # alembic / integration → 要带 integration
    ("edit_alembic", {"tool_name": "Edit", "tool_input": {
        "file_path": "backend/alembic/versions/0030_x.py"}}, True, True),
    ("edit_integration_test", {"tool_name": "Edit", "tool_input": {
        "file_path": "backend/tests/integration/test_int_x.py"}}, True, True),

    # ---------- Edit/Write：不应触发 ----------
    ("edit_md", {"tool_name": "Edit", "tool_input": {
        "file_path": "docs/design/system_design.md"}}, False, False),
    ("edit_frontend_ts", {"tool_name": "Edit", "tool_input": {
        "file_path": "frontend/src/types/api.ts"}}, False, False),
    # 项目外的 .py（scratchpad 里的一次性分析脚本）不该触发整套测试
    ("edit_scratchpad_py", {"tool_name": "Edit", "tool_input": {
        "file_path": "C:/Users/zm/AppData/Local/Temp/claude/scratchpad/probe.py"}},
     False, False),
    ("edit_hook_py", {"tool_name": "Edit", "tool_input": {
        "file_path": ".claude/hooks/guard.py"}}, False, False),
    ("no_file_path", {"tool_name": "Edit", "tool_input": {}}, False, False),

    # ---------- Bash：应触发（2026-09-10 补的那个洞）----------
    ("bash_heredoc_write_src", {"tool_name": "Bash", "tool_input": {"command":
        "cd backend && python - <<'PY'" + chr(10)
        + "import io" + chr(10)
        + "p='src/quantpilot/engine/strategies/value.py'" + chr(10)
        + "io.open(p,'w',encoding='utf-8').write('x')" + chr(10) + "PY"}}, True, False),
    ("bash_sed_i_src", {"tool_name": "Bash", "tool_input": {"command":
        "sed -i 's/a/b/' backend/src/quantpilot/engine/scorer.py"}}, True, False),
    ("bash_redirect_src", {"tool_name": "Bash", "tool_input": {"command":
        "cat > backend/src/quantpilot/core/x.py <<'PY'" + chr(10) + "pass" + chr(10)
        + "PY"}}, True, False),
    ("bash_write_alembic", {"tool_name": "Bash", "tool_input": {"command":
        "sed -i 's/a/b/' backend/alembic/versions/0030_x.py"}}, True, True),

    # ---------- Bash：不应触发 ----------
    # ⚠️ 最重要的一组：触发写宽了 = 每条 Bash 命令都跑两分钟测试，
    # 那会把人逼着关掉钩子，比不触发更糟。
    ("bash_grep_src", {"tool_name": "Bash", "tool_input": {"command":
        "grep -n 'def aggregate' backend/src/quantpilot/engine/scorer.py"}}, False, False),
    ("bash_read_redirect_elsewhere", {"tool_name": "Bash", "tool_input": {"command":
        "grep -n x backend/src/quantpilot/engine/scorer.py > /tmp/out.txt"}}, False, False),
    ("bash_pytest_only", {"tool_name": "Bash", "tool_input": {"command":
        "cd backend && uv run pytest tests/unit/ -q"}}, False, False),
    ("bash_write_scratchpad_py", {"tool_name": "Bash", "tool_input": {"command":
        "cat > /tmp/probe.py <<'PY'" + chr(10) + "print(1)" + chr(10) + "PY"}}, False, False),
]


def run(payload_bytes):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               QP_AUTO_TEST_DRY_RUN="1",
               CLAUDE_PROJECT_DIR=str(HOOK.resolve().parents[2]))
    p = subprocess.run([BASH, HOOK.as_posix()], input=payload_bytes,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def main():
    if not HOOK.exists():
        print("FATAL: 找不到被测脚本 " + str(HOOK))
        return 1
    if BASH is None:
        print("FATAL: 找不到能看见本仓文件的 bash（裸 bash 可能是 WSL 的）")
        return 1
    print("bash = " + BASH)

    passed = failed = 0
    for name, payload, want_fire, want_int in CASES:
        rc, out, err = run(json.dumps(payload).encode("utf-8"))
        fired = "WOULD_RUN" in out
        with_int = "integration" in out

        problems = []
        if rc != 0:
            problems.append("退出码 " + str(rc) + " != 0（fail-open 被破坏）"
                            " stderr=" + err.strip()[:200])
        if fired != want_fire:
            problems.append("期望触发=" + str(want_fire) + " 实际=" + str(fired))
        if fired and with_int != want_int:
            problems.append("期望带 integration=" + str(want_int) + " 实际=" + str(with_int))
        if not want_fire and out.strip():
            problems.append("不触发时应无输出，实得: " + out.strip()[:120])

        if problems:
            failed += 1
            print("FAIL  " + name.ljust(30) + " | " + "; ".join(problems))
        else:
            passed += 1
            print("PASS  " + name.ljust(30) + " want_fire=" + str(want_fire)
                  + " int=" + str(want_int))

    print("")
    print(str(passed) + "/" + str(passed + failed) + " passed, "
          + str(failed) + " failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
