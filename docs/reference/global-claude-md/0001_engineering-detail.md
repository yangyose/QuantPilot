# 通用工程经验

> 跨项目复用。本文不含任何项目专属规则。
> 项目专属规则放在每个项目根 `CLAUDE.md`，与本文互不重叠。

---

## 1. 通用原则

- **从每个错误吸取经验防复发**：任何踩到的错误（自己写的 bug、误判、工具/命令陷阱、环境坑）都不止于"这次改对"，必须把教训沉淀到**会再被读到的地方**——修在源头（代码/脚本/skill/hook 命令本身）+ 在对应文档/注释/CLAUDE.md/memory 留一句"为什么"。判据：同样的错误下次（换会话、换机器、换人）能否被同一处拦住。只在脑子里记 = 必然复发。沉淀本身若引入新错误（如修 A 命令时埋了 B 缺陷），同样适用本条——连环吸取，直到该处稳态。
- **禁止静默吞异常**：`except Exception: return [] / None / {}` 必须 `logger.exception(...)`（**不可用 DEBUG**，生产日志看不见）。业务上确需降级时用 `【降级说明】` 注释标明：当前降级内容 / 原因 / 恢复条件
- **`/compact` 或跨会话恢复后**：先跑完整测试建立真实基线，**不信任**摘要里的「X tests passed / 代码审查通过」（代码可能在恢复之前漂移；上游 bug 抛异常被吞会让坏测试"通过"）
- **集成测试断言精确**（`== N`，不用 `<= N` 宽松上界）——宽松断言会掩盖超出预期的写入 bug
- **设计文档禁外部追踪编号**：评审报告编号 / 会话内问题编号 / memory 文件编号均不得出现在正式设计文档正文。推迟用「**【设计待定：……】**」直接描述内容
- **不要 `git add -A` / `git add .`**：按文件名 add，防误传 `.env` / 凭证 / 大型二进制

---

## 2. Python / asyncio 陷阱

- **线程回调中的 event loop**：async 上下文创建线程回调时用 `asyncio.get_running_loop()` 预捕获 loop，**禁止**在子线程内 `asyncio.get_event_loop()`（Python 3.12 DeprecationWarning + 行为不可靠）
- **后台任务 session 生命周期**：框架托管的 session（FastAPI `Depends(get_db)`）自动 commit；`async with AsyncSessionLocal()` 直接创建的 session（`asyncio.create_task`、CLI 脚本、APScheduler job）**必须显式 commit**，否则写入随 close 丢失
- **混合 session 模式**：Service 方法对部分工作单元用 per-iteration `AsyncSessionLocal`、其他批量写走 `self._repo` 时，调用方必须在 outer 块退出前显式 `await session.commit()`——否则 `self._repo` 部分写入随 close 丢失

---

## 3. SQLAlchemy / asyncpg

- **bulk upsert 必须分批**：asyncpg 二进制协议 16-bit 占位符总数上限 **32767**，`n_rows × n_cols` 超限直接崩。`_BATCH_SIZE=500` 循环 `pg_insert.values(batch)` 是稳妥下限。合成数据测试容易绕过（< 3000 行不触发），需 ≥ 3000 行场景才能抓
- **upsert 前 `df.where(pd.notna(df), None)`** 把 NaN/NaT 转 None：pandas NaN 经 `to_dict("records")` 是 `float('nan')`，asyncpg 原样写入 PostgreSQL NUMERIC 字段作为特殊值 `'NaN'`（≠ NULL）→ 下游 `IS NOT NULL` 误判 / 数值过滤失效
- **upsert** 用 `insert(...).on_conflict_do_update()`；`updated_at` 显式写 `func.now()`
- **`on_conflict_do_update().returning()` 缓存问题**：SQLAlchemy 身份映射可能返回旧对象，`flush()` 后需 `await session.refresh(obj)` 强制刷新

---

## 4. pytest-asyncio

- **配置 `asyncio_mode = "auto"` 后禁止 `@pytest.mark.anyio`** 装饰任何 test/fixture：marker 被 anyio runner 接管（loop B），fixture 仍归 pytest-asyncio（loop A）→ asyncpg waiter future 在 loop A 创建、test body 在 loop B 唤醒 → `RuntimeError: Future attached to a different loop`。CI Linux 必现、Windows 偶发不报。**新写 async 测试一律 plain `async def test_xxx()` 不加任何 marker**
- **集成测试 async engine fixture**：必须 `poolclass=NullPool`（防跨 event loop 连接复用）+ **function scope**（禁 `scope="session"`，否则 anyio 每测试一个新 loop 会触发 `Future attached to a different loop`）。schema 建表用单独的**同步** fixture（`scope="session"`）跑 alembic
- **测试里禁用全局 app engine（QueuePool）跨 loop**：测试若直接 `from app...import AsyncSessionLocal`（或调用内部新建该 session 的生产脚本）做真 commit，全局 engine 默认 QueuePool + `pool_pre_ping=True` 会把**上一个测试 loop** 的连接留池；本测试 function-scoped loop 复用时 pre_ping/close 打到已关闭旧 loop → asyncpg `'NoneType' object has no attribute 'send'` / `RuntimeError: Event loop is closed`，在**首个 DB 操作**处炸（常误读为该处业务 bug）。e2e 阶段先经 `get_db` 填池 → 首个用全局 engine 的集成测试最易中招；单测时池空反而不复现。根治：集成目录 `conftest.py` autouse fixture 每测试前 `await app_engine.dispose(close=False)`（只换池、不在当前 loop 关旧连接，残连交 GC）。优于逐个把测试改用 NullPool 工厂
- **测试路由动态注册**：在 `client` fixture 内 `include_in_schema=False` 注册 + yield 后移除，避免污染全局路由表
- **集成测试触发"自建 session 真 commit"副作用路径时，finally 必须清理副作用表**：被测代码若在失败/通知分支用 `session_factory()` 自建 session 真 commit（如失败告警写站内信、审计流水），只清主表（PipelineRun 等）会把副作用行泄漏给共享 DB 中按字母序后跑的测试（断言精确计数/集合的测试必炸）。且本地只跑"受影响子集"抓不到——受影响的是**读同一副作用表的别的测试文件**，推送前须跑全量集成
- **同一测试 DB 严禁并发两个集成 pytest 会话**：conftest 的 session 级 alembic（建表/downgrade base）会互相拆台，典型症状=单个测试随机 `UndefinedTableError` 而前后测试全过（表被另一会话的 schema 迁移瞬时 DROP）。诱因：Windows 下后台 `uv run pytest` 的"完成"通知可能提前发出（输出文件为空、pytest 沦为孤儿继续跑），误以为跑完便再起一轮 → 并发。防复发协议：① 后台完成通知与输出不符（无 summary 行）时**先 `tasklist`/`ps` 确认 pytest 进程真退出**再起下一轮；② 判定结果只认落盘 log 里的 pytest summary + 自写的 `pytest_exit=` 哨兵行，不认 shell 管道退出码（`cmd | tail` 的退出码是 tail 的）

---

## 5. FastAPI

- **BackgroundTasks + UNIQUE 约束并存**：必须先 `await session.commit()` 再 `add_task()`。Starlette BackgroundTasks 在请求 async 上下文内 await，`get_db()` 的隐式 commit 推迟到所有 BG task 跑完 → BG 内写同一 UNIQUE 行被外层未 commit 行阻塞，外层 commit 又必须等 BG 完成 → 循环死锁
- **DI 函数统一**：所有依赖注入函数（`get_*_service`、`get_repo`）放在一个 `deps.py`，禁止散在路由文件——便于一处替换 / 测试覆盖
- **FastAPI 日期查询参数**：直接声明 `date | None`，FastAPI 自动解析 + 格式错误返回 422；用 `str` + 手动 `fromisoformat` 会绕过校验导致 500

---

## 6. pandas

- **MultiIndex `in` 判断 O(n) → O(1)**：循环外预计算 `available = set(df.index.get_level_values("col"))`，循环内用 `if x not in available`（O(1)）。循环内 `ts_code in index.get_level_values("col")` 是 O(n)，几千只股票 × 几千日 = 几百万次扫描
- **PostgreSQL `NUMERIC` 列传 pandas_ta 前 `.astype(float)`**：DB Decimal 类型直接传 pandas_ta 会触发 `isnan` TypeError
- **rank(pct=True) 边界**：n 个相同值 → rank = `(n+1)/(2n)`，不是 0.5。测试断言用 `len(set(scores)) == 1` 验证「全相等」而非具体值
- **skipna=False 在策略加权**：`.sum(axis=1, skipna=False)` 确保有任意 NaN 因子的样本被排除（默认 skipna=True 会按 0 处理，污染结果）

---

## 7. 安全

- **登录验证顺序**：先 `verify_password()`（bcrypt ~100ms）再比对用户名，防止「用户名存在性」通过响应时间泄露的计时侧信道
- **测试密码**：禁止硬编码；用统一的 `TEST_PASSWORD` 常量；autouse session fixture 自动替换 settings 哈希
- **环境变量含 `$`** 用单引号包裹（如 bcrypt 哈希 `'$2b$12$...'`），否则 shell/dotenv 会做变量展开

---

## 8. 调试范式（SUCCESS 但产出为零）

流程状态成功（task status=SUCCESS、无 ERROR 日志）但业务产出**空或恒定**（零信号、NAV 恒为 1.0、空列表、所有评分为 0）时按以下顺序排查：

1. **先查吞异常**：在主循环所有 `try/except Exception` 分支临时去掉 `except` 或把日志级别 DEBUG → ERROR，观察是否有 `KeyError` / `AttributeError` / `TypeError` 被静默捕获。Engine/Service 层的 `except Exception: return []` 是这类问题的最常见来源
2. **主循环 print 二分**：在真实代码路径加 `print`（`state` / `len(universe)` / `len(composite)` / `len(signals)`）快速定位哪一步把数据全拦下
3. **禁止另起脚本重建路径**：手工构造数据极易漏键或漏降级分支，比改真实代码加 print 更慢更错
4. **最后才查业务层逻辑**（因子 / 策略 / 评分等）

根源：静默降级让上游异常看起来像业务层无结果，直接从业务层开始查会绕远。

---

## 9. TDD 工作流

- **不跳过 RED**：先写失败的测试 → 再写实现 → 再回归 → 再 commit
- **测试命名规范**：`tests/unit/test_<模块>.py` / `tests/e2e/test_<功能>_api.py` / `tests/integration/test_<主题>.py`
- **集成测试合成日期跳周末**（`weekday() < 5`）——交易日序列不含周末，否则数据填充逻辑会和真实情况不符
- **批量编辑 / sed 大改后**：推送前必跑 lint + 受影响测试目录，不依赖 CI 兜底
- **收尾门槛**：lint 0 error；新增 API 必须有冒烟测试覆盖 401 / 200 / 404 / 422 路径

---

## 10. 推迟项防丢失三链原则

「推迟」≠「记在评审报告就完事」。评审报告归档后没人天天翻，必须在主开发者**下一次必读的文档**里留下前向引用，否则推迟项 = 隐形债务。

每个推迟项必须同时落到三个位置（缺一即推迟无效）：

- **链 A：评审报告 §8 修订追踪表** — 编号 / 等级 / 处置 / 责任 / 截止 / 状态 6 列
- **链 B：下一阶段必读文档**（如系统设计 §X 的目标 phase 行）— 展开列出编号 + 一句话描述
- **链 C：远期路线图** — 主题打包表对应主题行 scope 列追加 `+ §X.Y 评审 Z 项`，并相应调大估算

**关键认知**：链 A 只是历史日志，不会被人主动读；链 B/C 才是真正的防丢失机制。链 A 内容若不投影到 B/C，等同于把推迟项扔进黑洞。

---

## 11. Git / 运维

- **生产容器操作必须显式 `-f docker-compose.prod.yml --env-file .env.prod`**：默认 `docker-compose.yml` 通常是 dev 配置（空卷），误用会重建容器挂错卷（数据不丢但 psql 显示 0 表）
- **执行前确认作用域**：用户批准一次特定操作 ≠ 永久授权同类操作。每次破坏性 / 共享状态变更（push、force push、alembic downgrade、drop table）需要单独确认
- **集成测试 vs 生产 DB 隔离**：生产 DB 端口与测试 DB 端口必须不同；conftest session-end 的 `alembic downgrade base` 会 DROP 所有表，跑错 DB 会灭真实数据
- **`docker exec` 喂 stdin（heredoc / 管道）必须带 `-i`**：`docker exec container psql <<SQL ... SQL` 不带 `-i` 时容器内进程拿不到 stdin → SQL 完全没执行，psql 退出码仍 0（`set -e` 抓不到），易误判"已生效"。多语句 SQL 改用 `psql -c "stmt1; stmt2; ..."`（单 `-c` 多语句 = 一个隐式事务，配 `-v ON_ERROR_STOP=1`）或 `docker exec -i`。破坏性 DB 操作后**必须查行数/状态实证生效**，不信命令退出码
- **破坏性 DB 操作前先针对性备份**：删/改生产数据前 `pg_dump --data-only -t <表>...` 导出受影响表作精确回滚点（比全库备份快、可定点还原），再在单事务内执行

