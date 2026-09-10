#!/usr/bin/env bash
# auto_test.sh — PostToolUse 钩子：编辑 Python 文件后自动运行测试
# 输出结果直接反馈给 Claude，失败时 Claude 会自动进入调试

# ---------- 0. 选一个真能跑的 Python 解释器 ----------
# 本机 python3 是坏的 Windows Store 别名桩（输出 "Python" exit 49），
# 按 python → py → python3 探测；找不到就放行（不阻断编辑流）。
PYBIN=""
for c in python py python3; do
    if "$c" -c "import sys" >/dev/null 2>&1; then
        PYBIN="$c"
        break
    fi
done
[ -z "$PYBIN" ] && exit 0

# ---------- 1. 解析「本次改了哪个 backend .py」----------
# payload 走**环境变量**、Python 程序走**引号定界的 heredoc**。
# 不用 `python -c "..."`：那种写法里嵌套引号会被 shell 吃掉（见项目规范 4.12），
# 首版就栽在这里——所有正向用例静默不触发，表现与「钩子本来就没装」完全一样，
# 而夹具是唯一照出它的东西。
INPUT=$(cat)
FILE_PATH=$(QP_HOOK_INPUT="$INPUT" "$PYBIN" - <<'PYCODE' 2>/dev/null || echo ""
import json, os, re

# 只读 tool_input.file_path 是不够的（2026-09-10 补）：**Bash 的 tool_input 只有
# command、没有 file_path** -> 用 heredoc / sed -i 改 backend 的 .py 时本钩子静默
# exit 0，自动测试一次都不跑、也没有任何提示。同一个洞让两个评审钩子也瞎了
# （那两个已由 guard.py 规则 4 直接 deny 掉 Bash 写入；这里不能照搬 deny——
# 全拦会挡住正常的多文件机械改写脚本，故改成「识别出来、照常触发」）。
_SRC = re.compile(r"(?:backend/|src/quantpilot/|tests/)[^\s\"'|;&]*\.py")

# 写构造：重定向到 .py / sed -i / tee / Python 以写模式 open。
# **只认写、不认读**——`grep foo src/x.py` 不该触发一轮两分钟的测试；
# 触发写宽了会把人逼着关掉钩子，比不触发更糟。
_WRITE = re.compile(
    r">>?\s*[\"']?[^\s\"'|]*\.py"
    r"|\bsed\s+-i\b"
    r"|\btee\b"
    r"|open\s*\([^)]*[\"'](?:w|a)[\"']"
    r"|write_text\s*\("
)


def target_py_path(data):
    ti = data.get("tool_input") or {}
    if data.get("tool_name") == "Bash":
        cmd = ti.get("command") or ""
        if not _WRITE.search(cmd):
            return ""
        m = _SRC.search(cmd)
        return m.group(0) if m else ""
    path = (ti.get("file_path") or "").replace(chr(92), "/")
    if not path.endswith(".py") or not _SRC.search(path):
        return ""
    return path


try:
    print(target_py_path(json.loads(os.environ.get("QP_HOOK_INPUT") or "{}")))
except Exception:
    print("")
PYCODE
)

# 只处理项目内的 .py 文件（`_target_py_path` 已保证是 backend 源码/测试路径）
[ -n "$FILE_PATH" ] || exit 0

# ---------- 2. 判断需要运行哪些测试 ----------
BACKEND_DIR="$CLAUDE_PROJECT_DIR/backend"
RUN_INTEGRATION=false

# 编辑了迁移文件或集成测试文件 → 需要集成测试
if [[ "$FILE_PATH" == *"alembic"* ]] || [[ "$FILE_PATH" == *"integration"* ]]; then
    RUN_INTEGRATION=true
fi

# ---------- 2b. 干跑档：只报「会跑什么」，不真跑 ----------
# 夹具需要它——本钩子真跑一次 unit+e2e 要约 2 分钟，十几条用例逐个真跑不可行，
# 于是判定逻辑（哪些路径该触发、要不要带 integration）在此可被单独验证。
# ⚠️ 这不是"为测试而测试"：另三个钩子都有夹具、且 CLAUDE.md §4.12 明确
# 「判据不是装了没，而是跑夹具」，本钩子此前是四个里唯一没有夹具的。
if [ "${QP_AUTO_TEST_DRY_RUN:-}" = "1" ]; then
    if $RUN_INTEGRATION; then
        echo "WOULD_RUN unit+e2e+integration $FILE_PATH"
    else
        echo "WOULD_RUN unit+e2e $FILE_PATH"
    fi
    exit 0
fi

# ---------- 3. 运行 unit + e2e 测试（始终运行，不需要 DB） ----------
echo "━━━ Auto Test: $(basename "$FILE_PATH") ━━━"
cd "$BACKEND_DIR" || exit 1

uv run pytest tests/unit/ tests/e2e/ -x -q --tb=short --no-header 2>&1
FAST_EXIT=$?

if [ $FAST_EXIT -ne 0 ]; then
    echo ""
    echo "✗ Unit/E2E tests FAILED — see above"
    exit 0  # 不阻断，但 Claude 会看到失败并调试
fi

echo "✓ Unit/E2E tests passed"

# ---------- 4. 集成测试（仅当需要 且 DATABASE_URL 指向测试库 :5433 时） ----------
# 红线（CLAUDE.md C-1 / feedback_pytest_wipes_db）：集成测试 conftest 收尾会
# `alembic downgrade base` DROP 所有表。绝不能对生产/本地数据库（:5432）跑。
# 仅当 DATABASE_URL 显式指向测试库 :5433 才运行；否则跳过（conftest 另有硬护栏兜底）。
if $RUN_INTEGRATION; then
    if [[ "${DATABASE_URL:-}" == *":5433"* ]]; then
        echo ""
        echo "--- Integration tests (test DB :5433) ---"
        uv run pytest tests/integration/ -x -q --tb=short --no-header 2>&1
        INT_EXIT=$?
        if [ $INT_EXIT -ne 0 ]; then
            echo "✗ Integration tests FAILED"
        else
            echo "✓ Integration tests passed"
        fi
    else
        echo ""
        echo "⚠ Integration tests SKIPPED — DATABASE_URL 未指向测试库 :5433"
        echo "  集成测试会 DROP 全部表，禁止对 :5432 运行。"
        echo "  跑法: 起 :5433 测试库后 DATABASE_URL=...:5433/... uv run pytest tests/integration/"
    fi
fi

exit 0
