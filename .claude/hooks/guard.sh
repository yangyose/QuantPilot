#!/usr/bin/env bash
# guard.sh — PreToolUse 红线守卫包装器（强制 CLAUDE.md §0 宪法）。
# 逻辑在 guard.py；本脚本只负责选一个可用的 Python 解释器并把 stdin 透传过去。
# fail-open：找不到 Python 或脚本出错都放行，绝不阻断正常工具调用。
#
# 解释器选择统一在 pick_python.sh（2026-09-14 起）：优先项目 venv 的真 CPython，
# 不再先探 PATH 上的 python——那个探测本身会挂死 10 分钟，见该文件头注。

INPUT=$(cat)
DIR="$(dirname "$0")"

. "$(dirname "${BASH_SOURCE[0]}")/pick_python.sh"
[ -z "$PYBIN" ] && exit 0

printf '%s' "$INPUT" | "$PYBIN" "$DIR/guard.py"
