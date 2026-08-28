#!/usr/bin/env python3
"""claude_md_review.sh 的回归夹具。

判据：`python .claude/hooks/test_claude_md_review.py` 输出 `12/12 passed`。

为什么需要它：CLAUDE.md §4.12 对 guard.sh 立的标准是「判据不是装了没，而是跑夹具」。
本钩子最初只做过一次性管道验证，没留下可跑的东西——那正是 §4.11「接了但没生效」一族的
温床：路径匹配逻辑（分隔符 / 大小写 / 后缀）改坏了不会有人发现，钩子静默退出的表现与
「一切正常、只是没触发」完全相同。

正反两面都钉：只钉「该触发的触发了」，判定写宽了（例如误用 `in` 而非 basename 相等）
同样全绿——所以 lookalike / prefix 两类必须显式断言**不**触发。

编码：本文件与被测脚本的输出都含中文，而 Claude Code 跑命令全是管道
（§4.12 cp932 那条）→ 必须强制 UTF-8，且 run() 取字节后显式解码，
否则子进程内部就会炸、p.stdout 变 None，表现为「夹具坏了」而非「用例 FAIL」。
"""

import json
import os
import pathlib
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOOK = pathlib.Path(__file__).resolve().parent / "claude_md_review.sh"
BS = chr(92)  # 反斜杠不写字面量：避免经 shell/heredoc 传递时被再次转义（本会话踩过两次）


def _find_bash():
    """挑一个**看得见本仓文件**的 bash。

    裸 `bash` 在 Windows 上极可能解析到 WSL 的 C:/Windows/System32/bash.exe，
    而 WSL 里 `D:/...` 不存在（需 /mnt/d/...）→ exit 127「No such file or directory」，
    表现与「钩子脚本丢了」完全相同。Claude Code 的钩子实际由 Git Bash 执行，
    故这里按同一口径探测：能 test -f 到被测脚本的才算数。
    """
    target = HOOK.as_posix()
    candidates = [
        "C:/Program Files/Git/bin/bash.exe",
        "C:/Program Files (x86)/Git/bin/bash.exe",
        os.environ.get("PROGRAMFILES", "") + "/Git/bin/bash.exe",
        "bash",
    ]
    for cand in candidates:
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

# (用例名, stdin payload, 期望触发)
CASES = [
    # ---- 应触发 ----
    ("win_abs", {"tool_name": "Edit", "tool_input": {
        "file_path": "D:" + BS + "MyWork" + BS + "10Project" + BS + "RD"
                     + BS + "QuantPilot" + BS + "CLAUDE.md"}}, True),
    ("posix_abs", {"tool_name": "Edit", "tool_input": {
        "file_path": "/d/MyWork/10Project/RD/QuantPilot/CLAUDE.md"}}, True),
    ("relative", {"tool_name": "Write", "tool_input": {"file_path": "CLAUDE.md"}}, True),
    ("user_global", {"tool_name": "Edit", "tool_input": {
        "file_path": "C:" + BS + "Users" + BS + "zm" + BS + ".claude" + BS + "CLAUDE.md"}}, True),
    ("mixed_sep", {"tool_name": "Edit", "tool_input": {
        "file_path": "D:/MyWork" + BS + "RD/CLAUDE.md"}}, True),

    # ---- 不应触发 ----
    ("other_py", {"tool_name": "Edit", "tool_input": {
        "file_path": "backend/src/quantpilot/engine/signal.py"}}, False),
    ("lookalike_bak", {"tool_name": "Edit", "tool_input": {
        "file_path": "docs/CLAUDE.md.bak"}}, False),
    ("prefixed", {"tool_name": "Edit", "tool_input": {
        "file_path": "docs/NOT_CLAUDE.md"}}, False),
    ("dir_named_claude_md", {"tool_name": "Write", "tool_input": {
        "file_path": "docs/CLAUDE.md/inner.txt"}}, False),
    ("no_file_path", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, False),
    ("no_tool_input", {"tool_name": "Edit"}, False),
]


def run(payload_bytes):
    """取字节 + 显式解码——不用 text=True（见模块 docstring 的编码说明）。"""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    # 必须传 posix 形式：Git Bash 会把 Windows 路径里的反斜杠当转义符吃掉
    # （首版用 str(HOOK) → bash: "D:MyWork10Project..." → exit 127，全用例 FAIL）
    p = subprocess.run(
        [BASH, HOOK.as_posix()], input=payload_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def main():
    if not HOOK.exists():
        print("FATAL: 找不到被测脚本 " + str(HOOK))
        return 1
    if BASH is None:
        # 绝不静默跳过：夹具不可用必须比用例 FAIL 更响
        print("FATAL: 找不到能看见本仓文件的 bash（裸 bash 可能是 WSL 的）")
        return 1
    print("bash = " + BASH)

    passed = failed = 0
    for name, payload, want_trigger in CASES:
        rc, out, err = run(json.dumps(payload).encode("utf-8"))
        fired = "claude-md-reviewer" in out

        problems = []
        if rc != 0:
            # fail-open 是硬要求：钩子非零退出会打断编辑流
            problems.append("退出码 " + str(rc) + " != 0（fail-open 被破坏）stderr=" + err.strip()[:200])
        if fired != want_trigger:
            problems.append("期望触发=" + str(want_trigger) + " 实际=" + str(fired))
        if not want_trigger and out.strip():
            problems.append("不触发时应无输出，实得: " + out.strip()[:120])
        if want_trigger:
            try:
                json.loads(out)
            except Exception as exc:
                problems.append("触发时 stdout 必须是合法 JSON: " + str(exc))

        if problems:
            failed += 1
            print("FAIL " + name + ": " + "; ".join(problems))
        else:
            passed += 1
            print("ok   " + name)

    # 畸形输入必须 fail-open（解析失败 → 放行，不阻断编辑）
    rc, out, err = run(b"{not json at all")
    if rc == 0 and not out.strip():
        passed += 1
        print("ok   malformed_json_fail_open")
    else:
        failed += 1
        print("FAIL malformed_json_fail_open: rc=" + str(rc) + " out=" + out.strip()[:120])

    total = passed + failed
    print()
    print(str(passed) + "/" + str(total) + " passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
