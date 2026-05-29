#!/usr/bin/env bash
# guard.sh — PreToolUse 红线守卫包装器（强制 CLAUDE.md §0 宪法）。
# 逻辑在 guard.py；本脚本只负责选一个可用的 Python 解释器并把 stdin 透传过去。
# fail-open：找不到 Python 或脚本出错都放行，绝不阻断正常工具调用。
#
# 注意：本机 `python3` 是坏的 Windows Store 别名桩（输出 "Python" exit 49），
# 故按 python → py → python3 顺序探测“真能跑”的解释器。

INPUT=$(cat)
DIR="$(dirname "$0")"

PYBIN=""
for c in python py python3; do
    if "$c" -c "import sys" >/dev/null 2>&1; then
        PYBIN="$c"
        break
    fi
done
[ -z "$PYBIN" ] && exit 0

printf '%s' "$INPUT" | "$PYBIN" "$DIR/guard.py"
