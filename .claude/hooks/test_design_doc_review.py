#!/usr/bin/env python3
"""design_doc_review.sh 的回归夹具。

判据：`python .claude/hooks/test_design_doc_review.py` 输出 `15/15 passed`。

同 guard.sh / claude_md_review.sh 一个标准——"已手工验证过"不算判据。
路径匹配逻辑改坏了（分隔符 / 前缀边界 / 后缀）不会有人发现：钩子静默退出的表现与
「一切正常、只是没触发」完全相同。

正反两面都钉。反向用例尤其重要：判定写宽（例如把 `docs/design/` 的尾斜杠去掉）
会让 `docs/designer/`、`docs/reviews/` 一并触发，而只钉「该触发的触发了」照样全绿。

编码与 bash 探测见 test_claude_md_review.py 的模块 docstring（同一组坑）。
"""

import json
import os
import pathlib
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOOK = pathlib.Path(__file__).resolve().parent / "design_doc_review.sh"
BS = chr(92)


def _find_bash():
    """挑一个看得见本仓文件的 bash（裸 bash 在 Windows 上极可能是 WSL 的）。"""
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

CASES = [
    # ---- 应触发 ----
    ("win_abs_design", "D:" + BS + "MyWork" + BS + "10Project" + BS + "RD" + BS
     + "QuantPilot" + BS + "docs" + BS + "design" + BS + "system_design.md", True),
    ("posix_design", "/d/MyWork/10Project/RD/QuantPilot/docs/design/system_design.md", True),
    ("rel_design", "docs/design/system_design.md", True),
    ("phases_subdir", "docs/design/phases/v1_5_k_factor_validation.md", True),
    ("roadmap", "docs/design/v1_post_release_roadmap.md", True),
    ("spec_sdd", "docs/spec/QuantPilot_SDD.md", True),
    ("mixed_sep", "D:/MyWork" + BS + "docs/design" + BS + "system_design.md", True),

    # ---- 不应触发 ----
    # 评审报告是历史日志，不是设计
    ("reviews_excluded", "docs/reviews/algo_framework_audit_2026-08-28.md", False),
    # 操作指南，读者与失效模式都不同
    ("guides_excluded", "docs/guides/deployment.md", False),
    # 前缀边界：designer 不是 design
    ("designer_prefix", "docs/designer/mockup.md", False),
    # 后缀边界
    ("md_bak", "docs/design/system_design.md.bak", False),
    ("py_in_design", "docs/design/gen_diagram.py", False),
    # 代码文件
    ("source_file", "backend/src/quantpilot/engine/signal.py", False),
    ("no_file_path", None, False),
]


def run(payload_bytes):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
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
        print("FATAL: 找不到能看见本仓文件的 bash（裸 bash 可能是 WSL 的）")
        return 1
    print("bash = " + BASH)

    passed = failed = 0
    for name, path, want in CASES:
        ti = {} if path is None else {"file_path": path}
        payload = {"tool_name": "Edit", "tool_input": ti}
        rc, out, err = run(json.dumps(payload).encode("utf-8"))
        fired = "design-doc-reviewer" in out

        problems = []
        if rc != 0:
            problems.append("退出码 " + str(rc) + " != 0（fail-open 被破坏）"
                            + " stderr=" + err.strip()[:200])
        if fired != want:
            problems.append("期望触发=" + str(want) + " 实际=" + str(fired))
        if not want and out.strip():
            problems.append("不触发时应无输出，实得: " + out.strip()[:120])
        if want:
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
