#!/usr/bin/env bash
# pick_python.sh — 四个钩子（guard / auto_test / claude_md_review / design_doc_review）
# 共用的解释器选择。用 `. "$DIR/pick_python.sh"` source 进来，结果放在 $PYBIN（空 = 没找到）。
#
# 为什么不再「先探 PATH 上的 python」（2026-09-14 实测，代价：一整个会话）：
#   本机 PATH 上的 python / py / python3 全是 Windows Store「Python Install Manager」
#   （PythonSoftwareFoundation.PythonManager）的别名桩。会话重启后它们**启动即挂死**
#   ——不是报错，是不返回：`python -c "import sys"` 挂住、PowerShell `Start-Process`
#   都不返回、coreutils `timeout 15` 杀不掉（最终 exit 126 "Permission denied"）。
#   四个钩子都以 `python -c "import sys"` 探测可用性，于是每个 Bash/Edit/Write 调用
#   都在 PreToolUse（guard）挂到 600s 超时、再在 PostToolUse（auto_test）挂 600s：
#   **每次工具调用延迟约 20 分钟，且守卫因超时 fail-open 整段失效**。
#   它在会话重启前明明是好的——所以「上次夹具全过」不构成任何保证。
#
# 探测顺序（只要前一项命中就绝不碰后面的）：
#   1. $QP_PYBIN         显式指定（夹具 / 手工调试用）
#   2. backend/.venv     项目 venv 里的真 CPython（uv sync 装的，非别名桩）——
#                        Windows 在 Scripts/python.exe，POSIX 在 bin/python
#   3. PATH 上 python → py → python3（旧逻辑，仅 venv 不存在时兜底；⚠️ 可能挂死）
#
# fail-open 保持不变：全部落空 → $PYBIN 为空，调用方 exit 0 放行。
# 判据：`backend/.venv/Scripts/python.exe .claude/hooks/test_guard.py` 等四个夹具全过，
# 且一次 Bash 调用不再花 20 分钟。

PYBIN=""
_pp_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_pp_root="$(cd "$_pp_dir/../.." && pwd)"

if [ -n "${QP_PYBIN:-}" ] && "$QP_PYBIN" -c "import sys" >/dev/null 2>&1; then
    PYBIN="$QP_PYBIN"
else
    for c in "$_pp_root/backend/.venv/Scripts/python.exe" "$_pp_root/backend/.venv/bin/python"; do
        if [ -x "$c" ] && "$c" -c "import sys" >/dev/null 2>&1; then
            PYBIN="$c"
            break
        fi
    done
fi

if [ -z "$PYBIN" ]; then
    for c in python py python3; do
        if "$c" -c "import sys" >/dev/null 2>&1; then
            PYBIN="$c"
            break
        fi
    done
fi

unset _pp_dir _pp_root