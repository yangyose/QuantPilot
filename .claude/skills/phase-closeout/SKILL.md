---
name: phase-closeout
description: QuantPilot Phase 收尾核查——phase 实现完成、宣告交付前执行 CLAUDE.md §5.2 门槛：DoD 对照、ruff 0 error、全套测试真基线、新端点冒烟逐行对 §8、集成测试、推迟三链校验、设计文档编号规约、文档头版本一致、经验沉淀。当用户说“Phase N 收尾 / phase 收尾核查 / Phase N 完成了吗 / 收尾门槛”时使用。
---

# Phase 收尾核查（宣告交付前）

落实 CLAUDE.md §0 宪法 **C-2（质量是用户利益前提）** + **C-3（推迟三链）** + **C-5（无孤儿·编号规约）**，执行 §5.2 门槛。任一不过不得宣告完成。

**命令均在 `backend/` 目录执行。**

## 门槛清单

### 1. DoD 对照
本 phase 设计文档所有模块对照 DoD **全部交付**。未交付的**立即**更新 `system_design §9` 移入下一 phase（不可静默漏掉）。

### 2. ruff 0 error（C-2 收尾门槛）
```bash
uv run ruff check src/ tests/
```
必须 **0 error**。

### 3. 全套测试真基线（C-2，不信摘要）
```bash
uv run pytest tests/unit/ tests/e2e/ -q          # 无 DB
uv run pytest tests/integration/ -q              # 需 DB:5433 + alembic upgrade head
```
**不信任摘要里的"X passed"**，亲自跑。集成测试需测试 DB（5433）——**严禁对生产 DB 跑**（conftest 会 downgrade base 灭表，C-1）。容器没起就 `docker compose -f docker-compose.dev.yml up -d db` + `uv run alembic upgrade head`。

### 4. 新增 REST 端点冒烟（C-2）
每个新端点在 `backend/tests/smoke/test_api_live.py` 补冒烟，**逐行对照设计文档 §8 场景表**（不能只核对数量），覆盖 **401/200/404/422** 路径。

### 5. 集成测试跑通
容器自动启动 + `alembic upgrade head` 后 integration 全绿。

### 6. 推迟项三链校验（C-3）
本 phase 若推迟任何项，确认三链齐全（§5.4）：评审报告 §8 修订追踪表 + `system_design §9` 目标行 / `v1_5_roadmap §6`，链 B/C **展开列出**编号 + 一句话，禁止"详见评审报告"占位。用 Grep 扫 `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + `docs/reviews/` 复核。

### 7. 设计文档编号规约（C-5 / §5.5）
设计文档正文 + 修订历史**无外部追踪编号**（评审报告 DESIGN-09/P-3、memory TD-1）。可接受：文档内正式定义的编号（如 P5-PRE-1）。

### 8. 文档头版本一致
phase 设计文档头部 `版本：` 与文末修订历史最新版本号一致。评审报告 §8 修订追踪表对应行已勾选/更新状态。

### 9. 经验沉淀
- 新经验判断归属：项目专属 → 本仓 `CLAUDE.md §4`；跨项目通用（Python/async/DB/pytest）→ `~/.claude/CLAUDE.md`。
- 值得跨会话记的写入 memory（`memory/` + `MEMORY.md` 加一行指针）。
- 更新 `CLAUDE.md §6 当前进度` + `system_design §9` 本 phase 状态。

## 提交
门槛全过后再 commit（用户要求时）。破坏性/推送操作单独确认（C-1）。
