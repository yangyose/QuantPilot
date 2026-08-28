#!/usr/bin/env bash
# claude_md_review.sh — PostToolUse 钩子：CLAUDE.md 被改动后触发第三方评审子 agent。
#
# 为什么用「命令钩子 + additionalContext」而不是原生 type:"agent" 钩子：
#   原生 agent 钩子无法在写入前用管道验证，且若 `if` 过滤语义与预期不符，就会对**每一次**
#   Edit/Write 都起一个 agent（本仓一次会话动几十个文件）。本仓 2026-08-28 刚因「机制写了
#   但从未真正生效」付出代价（CLAUDE.md §4.11「接了但没生效」一族），故这里选**能被正反两面
#   管道验证**的形态：路径判定在本脚本内完成，可离线测；不匹配时静默退出，零开销。
#
# fail-open：找不到解释器或解析失败一律 exit 0，绝不阻断编辑流。

INPUT=$(cat)

PYBIN=""
for c in python py python3; do
    if "$c" -c "import sys" >/dev/null 2>&1; then PYBIN="$c"; break; fi
done
[ -z "$PYBIN" ] && exit 0

printf '%s' "$INPUT" | PYTHONUTF8=1 "$PYBIN" -c '
import json, os, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)                      # 解析失败 → 放行

ti = data.get("tool_input") or {}
path = (ti.get("file_path") or "").replace("\\", "/")
if os.path.basename(path) != "CLAUDE.md":
    sys.exit(0)                      # 非 CLAUDE.md → 静默退出，零开销

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "CLAUDE.md 刚被修改（" + path + "）。"
            "请立即用 Agent 工具启动 subagent_type=\"claude-md-reviewer\" 做第三方评审"
            "（该 agent 冷启动、不带本会话上下文，这正是它的价值）。"
            "把它的报告择要转述给用户——它的最终报告用户看不到。"
            "评审结果仅供参考：采纳与否由你和用户判断，不要自动照单全改。"
        ),
    }
}))
'
exit 0
