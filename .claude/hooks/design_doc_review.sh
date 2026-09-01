#!/usr/bin/env bash
# design_doc_review.sh — PostToolUse 钩子：设计文档被改动后触发第三方评审子 agent。
#
# 触发范围：docs/design/**.md + docs/spec/**.md
#   刻意**不含** docs/reviews/（那是评审报告，是历史日志不是设计）
#   与 docs/guides/（操作指南，读者与失效模式都不同）。
#
# 形态与 claude_md_review.sh 一致：命令钩子 + additionalContext，而非原生 type:"agent"
# 钩子——路径判定放在本脚本内，可离线用管道正反两面验证（见 test_design_doc_review.py）。
# 本仓 2026-08-28 刚因「机制写了但从未真正生效」付出代价（CLAUDE.md §4.11）。
#
# ⚠️ 与 CLAUDE.md 那个钩子的关键差异：**设计文档在一次会话里会被改很多次**
# （2026-09-01 单次会话改了 roadmap 6 次）。逐次起 agent 会非常贵，所以下面的
# additionalContext 明确要求**攒批**：在一个逻辑收口点起一次，而不是每个 Edit 一次。
#
# fail-open：找不到解释器或解析失败一律 exit 0，绝不阻断编辑流。

INPUT=$(cat)

PYBIN=""
for c in python py python3; do
    if "$c" -c "import sys" >/dev/null 2>&1; then PYBIN="$c"; break; fi
done
[ -z "$PYBIN" ] && exit 0

printf '%s' "$INPUT" | PYTHONUTF8=1 "$PYBIN" -c '
import json, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)                      # 解析失败 → 放行

ti = data.get("tool_input") or {}
path = (ti.get("file_path") or "").replace("\\", "/")

if not path.endswith(".md"):
    sys.exit(0)
if ("docs/design/" not in path) and ("docs/spec/" not in path):
    sys.exit(0)                      # 非设计文档 → 静默退出，零开销

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "设计文档刚被修改（" + path + "）。"
            "请在**本轮改动收口后**用 Agent 工具启动 "
            "subagent_type=\"design-doc-reviewer\" 做第三方评审"
            "（冷启动、不带本会话上下文，这正是它的价值）。"
            "⚠️ 攒批：设计文档一次会话常被改多次，逐次起 agent 很贵——"
            "同一批改动只起一次，把本轮改过的文档一并交给它。"
            "它的重点是「文档声称 vs 代码/生产的真实状态」，其次是权威源一致性、"
            "推迟三链、DoD 可验证性。"
            "把报告择要转述给用户——它的最终报告用户看不到。"
            "评审结果仅供参考：修复方案由你评估判断，不要自动照单全改。"
        ),
    }
}))
'
exit 0
