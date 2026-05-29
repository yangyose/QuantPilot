---
name: phase-kickoff
description: QuantPilot Phase 启动核查——创建 phaseN 设计文档前执行 CLAUDE.md §5.1 清单：读 system_design §9 列模块、逐模块定纳入/推迟、孤儿检查、grep 推迟项三处确认消费，产出设计文档 §1.3 启动核查清单。当用户说“启动 Phase N / 开始 Phase N / 写 phaseN 设计文档 / phase 启动核查”时使用。
---

# Phase 启动核查（创建 phaseN 设计文档前）

落实 CLAUDE.md §0 宪法 **C-5（SDD 权威·无孤儿）** + **C-3（推迟需充分理由+三链）**，执行 §5.1 清单。目标：开工前锁定本 phase 范围，不漏模块、不丢上游推迟项。

**关键路径**：
- `docs/spec/QuantPilot_SDD.md`（权威需求）
- `docs/design/system_design.md`（§3/§5 模块 · §6 端点 · §9 phase 表）
- `docs/design/v1_5_roadmap.md`（V1.5+ 主题）
- `docs/reviews/phaseN_*_review_*.md`（评审报告，§8 修订追踪表）
- `docs/design/phases/phaseN_*.md`（产出物）

## 步骤

### 1. 读 §9 本 phase 行，列出分配模块
Read `docs/design/system_design.md` §9，定位本 phase 行，列出分配的所有模块/子项（含 `R<N>-P<X>-` 评审追溯标记）。

### 2. 逐模块决定 纳入 / 推迟
每个模块明确归属本 phase 或推迟。**推迟必须**（C-3）：
- 命中四类充分理由之一（依赖外部决策 / 跨 phase 大重构 / 验收标准未定义 / 物理资源约束；见 CLAUDE.md §5.4）。禁止伪推迟（「不影响主路径」「范围外」「一起做」「小改进」）。
- 在新设计文档**引言处显式注明**「模块 X 推迟至 Phase N，原因：……」。
- **立即**更新 §9 对应行（不等收尾）。
- 落「推迟三链」（§5.4）：评审报告 §8 + §9 目标 phase 行 / v1_5_roadmap §6，链 B/C 子项展开编号 + 一句话。

### 3. 孤儿检查（C-5）
确认 system_design §3/§5 每个模块、§6 每个 API 端点，都在某个 phase **有且仅有一个**明确归属。本 phase 新引入的模块/端点必须在 §9 落位。

### 4. grep 推迟项三处确认消费
确认上游 phase 推迟到本 phase 的项没漏（用 Grep 工具）：
```
# 4a. 跨 system_design + roadmap + reviews 三处扫推迟项编号
pattern: R\d+-P[2-3]-\d+
path: docs/design/system_design.md, docs/design/v1_5_roadmap.md, docs/reviews/
# 4b. 本 phase 行所有子项（含 R<N>-P<X>- 评审追溯）逐条核对是否已规划进设计文档
```
对每个命中编号判断：本 phase 消费 / 仍推迟（须有三链）/ 已完成。

### 5. 产出设计文档 §1.3 启动核查清单
在 phaseN 设计文档 §1.3 写入本次核查结论 + 勾选项：
- [ ] §9 本 phase 行所有子项已列（含 `R<N>-P<X>-` 追溯）
- [ ] `R\d+-P[2-3]-\d+` 跨 system_design + roadmap + reviews 三处确认消费
- [ ] 孤儿检查通过（§3/§5 模块 + §6 端点归属唯一）
- [ ] 推迟模块已在引言注明 + §9 已更新 + 三链已落

## 红线
- **范围变更先回写 `system_design §9`，再写 phase 设计文档**（C-5）——不可反向。
- 设计文档正文**禁止外部追踪编号**（评审报告/memory 编号），见 §5.5；推迟问题用「**【设计待定：……】**」直接描述内容。
