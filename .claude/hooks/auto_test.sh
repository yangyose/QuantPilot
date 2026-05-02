#!/usr/bin/env bash
# auto_test.sh — PostToolUse 钩子：编辑 Python 文件后自动运行测试
# 输出结果直接反馈给 Claude，失败时 Claude 会自动进入调试

# ---------- 1. 从 stdin 解析被编辑的文件路径 ----------
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "
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

# ---------- 4. 集成测试（仅当需要且 PostgreSQL 容器在运行时） ----------
if $RUN_INTEGRATION; then
    # 检查 PostgreSQL 容器是否运行
    if docker ps --format "{{.Names}}" 2>/dev/null | grep -qiE "postgres|quantpilot.*(db|postgres)"; then
        echo ""
        echo "--- Integration tests ---"
        uv run pytest tests/integration/ -x -q --tb=short --no-header 2>&1
        INT_EXIT=$?
        if [ $INT_EXIT -ne 0 ]; then
            echo "✗ Integration tests FAILED"
        else
            echo "✓ Integration tests passed"
        fi
    else
        echo ""
        echo "⚠ Integration tests SKIPPED (PostgreSQL container not running)"
        echo "  启动方式: docker compose -f docker-compose.dev.yml up -d db"
    fi
fi

exit 0
