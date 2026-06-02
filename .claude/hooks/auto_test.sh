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

# ---------- 1. 从 stdin 解析被编辑的文件路径 ----------
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | "$PYBIN" -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

# 只处理 backend/ 下的 .py 文件
[[ "$FILE_PATH" =~ \.py$ ]]     || exit 0
[[ "$FILE_PATH" == *"backend"* ]] || exit 0

# ---------- 2. 判断需要运行哪些测试 ----------
BACKEND_DIR="$CLAUDE_PROJECT_DIR/backend"
RUN_INTEGRATION=false

# 编辑了迁移文件或集成测试文件 → 需要集成测试
if [[ "$FILE_PATH" == *"alembic"* ]] || [[ "$FILE_PATH" == *"integration"* ]]; then
    RUN_INTEGRATION=true
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
