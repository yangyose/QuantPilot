"""BacktestService：回测任务编排（Phase 8，SDD §7.7）。负责 IO，不含回测计算逻辑。"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy import Float, cast, func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.backtest.engine import (
    _FUND_LOOKBACK_DAYS,
    BacktestConfig,
    BacktestDataBundle,
    BacktestEngine,
    _prepare_financials,
    resolve_money_flow_lookback_days,
)
from quantpilot.models.system import BacktestResult, BacktestTask

logger = logging.getLogger(__name__)

# A1（S6-GAP-02）：daily_positions 流式 sink 缓冲批大小。满即 flush 落库 →
# 内存峰值 O(_SINK_BATCH) 常量，不随回测天数 × 持仓数线性增长。asyncpg 二进制协议
# 16-bit 占位符上限 32767 / 每行 6 列 ≈ 5461 行上限，2000 留足余量（CLAUDE.md §3）。
_SINK_BATCH = 2000


class BacktestService:
    """
    编排 IO：
    ① 创建/更新 BacktestTask
    ② 预加载 BacktestDataBundle（adj_prices / stock_info / financials / hs300_history）
    ③ asyncio.to_thread(engine.run, config, data, progress_cb)
    ④ 写 BacktestResult
    ⑤ 更新 BacktestTask.status
    """

    def __init__(
        self, session: AsyncSession, engine: BacktestEngine | None = None,
    ) -> None:
        """构造 BacktestService。

        - REST 查询端点（`get_task`/`get_result`/`create_task`）不需要 engine，可传 None。
        - 仅 `run_task` 需要 engine；后台任务（Phase 10 §4.4 评审 C-02/C-03）会
          根据 `task.config_snapshot` 即时构造 BacktestEngine 并新建 BacktestService 调用。
        """
        self._session = session
        self._engine = engine

    async def create_task(
        self,
        config: BacktestConfig,
        engine_snapshot: dict | None = None,
    ) -> str:
        """创建 BacktestTask（status=PENDING），返回 task_id。

        Phase 10 §4.4：`engine_snapshot` 由端点层通过 `ConfigService.get_all_for_snapshot()`
        预取；写入 `backtest_task.config_snapshot` 作为本次回测参数标识，支持结果可复现。
        """
        task_id = str(uuid.uuid4())
        task = BacktestTask(
            task_id=task_id,
            status="PENDING",
            config_json=_config_to_dict(config),
            config_snapshot=engine_snapshot,
        )
        self._session.add(task)
        # 立即 commit：background task 在 get_db() 自动 commit 之前就可能启动，
        # 若仅 flush 则 task 对后台独立 session 不可见（FK 违约）。
        await self._session.commit()
        logger.info("backtest_task_created task_id=%s", task_id)
        return task_id

    async def has_active_task(self) -> bool:
        """是否已有 RUNNING/PENDING 回测任务（并发护栏，2026-06-16）。

        2GB 生产机同时跑两个回测必 OOM（无论窗口多短，daily_quotes 全量加载叠加）。
        端点据此拒绝并发提交。PENDING 也计入——防止快速重复提交在 BG 任务起跑前漏判。
        """
        count = (await self._session.execute(
            select(func.count()).select_from(BacktestTask).where(
                BacktestTask.status.in_(("RUNNING", "PENDING"))
            )
        )).scalar() or 0
        return count > 0

    async def get_task(self, task_id: str) -> BacktestTask | None:
        return (await self._session.execute(
            select(BacktestTask).where(BacktestTask.task_id == task_id)
        )).scalar_one_or_none()

    async def get_result(self, task_id: str) -> BacktestResult | None:
        return (await self._session.execute(
            select(BacktestResult).where(BacktestResult.task_id == task_id)
        )).scalar_one_or_none()

    async def import_result(
        self,
        *,
        task_id: str,
        config_json: dict,
        config_snapshot: dict | None,
        started_at: datetime | None,
        finished_at: datetime | None,
        performance: dict,
        daily_nav: dict,
        disclaimer: str,
        daily_positions: list[dict] | None = None,
    ) -> bool:
        """回流外部（本地算力中心）回测结果到本 DB（2026-06-15）。

        长区间回测在本地大内存机跑完后经 POST /backtest/import 回流生产 DB，使生产
        Web 也能查看。task+result 两行一起 INSERT（status=SUCCESS）。按 task_id 幂等：
        已存在则跳过返回 False，不覆盖（防重复回流 / 与生产任务 UUID 永不撞号）。

        回测两表无外键指向行情数据 → 纯 INSERT、零引用完整性风险；config_snapshot
        含 data_baseline 标注「本结果基于截至 X 日的数据」。

        V1.5-A A1：``daily_positions``（本地跑出的每日持仓明细）一并回流，经
        _flush_positions upsert 到 backtest_daily_position（供生产结果页查）。
        """
        if await self.get_task(task_id) is not None:
            return False
        self._session.add(BacktestTask(
            task_id=task_id,
            status="SUCCESS",
            config_json=config_json,
            config_snapshot=config_snapshot,
            started_at=started_at,
            finished_at=finished_at,
        ))
        self._session.add(BacktestResult(
            task_id=task_id,
            performance_json=performance,
            daily_nav_json=daily_nav,
            disclaimer=disclaimer,
        ))
        await self._session.commit()
        # A1：回流每日持仓明细（task 行已 commit，FK 满足）
        if daily_positions:
            await self._flush_positions(task_id, daily_positions)
        logger.info(
            "backtest_result_imported task_id=%s positions=%d",
            task_id, len(daily_positions or []),
        )
        return True

    def run_slippage_comparison(
        self,
        config: BacktestConfig,
        data: BacktestDataBundle,
        scenarios: list[float] | None = None,
    ) -> list[dict]:
        """V1.5-A A1b（SDD §16 滑点敏感性）：多滑点情景对比。

        对 ``scenarios`` 每档滑点**复用同一 bundle**（bundle 是内存大头，只加载一次），
        串行跑 ``self._engine.run``（每次覆盖 ``slippage_rate``），产出结构化对比报告：
        ``[{slippage, total_return, max_drawdown, sharpe, annualized_return, pipeline_mode}]``。

        ``scenarios`` 缺省时读 ``config.slippage_scenarios``；均空 → 返回空列表（不跑）。
        同步方法（engine.run 同步）；异步调用方经 ``asyncio.to_thread`` 包装。本地算力
        中心用（生产回测禁用）。情景数应受调用方护栏约束（如 ≤5）防滥用。
        """
        import dataclasses

        scenarios = scenarios if scenarios is not None else config.slippage_scenarios
        if not scenarios:
            return []
        if self._engine is None:
            raise RuntimeError("run_slippage_comparison 需注入 BacktestEngine")

        report: list[dict] = []
        for slip in scenarios:
            cfg = dataclasses.replace(config, slippage_rate=float(slip))
            result = self._engine.run(cfg, data)  # 复用同一 data bundle
            perf = result.performance or {}
            # ⚠️ 输出键名（对外契约：前端表格列 / CSV 表头）与 `BacktestReport.generate`
            # 的键名**不同**，必须逐个对上：`total_return` ← `cumulative_return`、
            # `sharpe` ← `sharpe_ratio`。2026-09-23 发现 `total_return` 原写成
            # `perf.get("total_return", 0.0)`——该键engine 从来不产出 → 前端「累计收益」列
            # **恒为 0.00%**，而 DoD 只看 sharpe 所以一直没人发现（§4.11「接了但没生效」）。
            # `tests/unit/test_slippage_report_keys.py` 用真实 `generate()` 的键集合钉住这层映射。
            report.append({
                "slippage": float(slip),
                "total_return": float(perf.get("cumulative_return", 0.0)),
                "max_drawdown": float(perf.get("max_drawdown", 0.0)),
                "sharpe": float(perf.get("sharpe_ratio", 0.0)),
                "annualized_return": float(perf.get("annualized_return", 0.0)),
                "pipeline_mode": getattr(result, "pipeline_mode", None),
            })
        return report

    async def _flush_positions(self, task_id: str, rows: list[dict]) -> None:
        """A1（S6-GAP-02）：批量 upsert 回测每日持仓到 backtest_daily_position。

        fresh AsyncSessionLocal（不复用 self._session：sink 跨线程投递回主 loop，
        request-scoped session 生命周期不安全）。asyncpg 占位符上限 → 按 _SINK_BATCH
        分批（CLAUDE.md §3）。幂等 upsert（on_conflict (task_id, trade_date, ts_code)）。
        自建 session 必须显式 commit（CLAUDE.md §4.1）。
        """
        if not rows:
            return
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from quantpilot.core.database import AsyncSessionLocal
        from quantpilot.models.system import BacktestDailyPosition

        async with AsyncSessionLocal() as session:
            for i in range(0, len(rows), _SINK_BATCH):
                batch = [
                    {
                        "task_id": task_id,
                        "trade_date": r["trade_date"],
                        "ts_code": r["ts_code"],
                        "shares": int(r["shares"]),
                        "cost_price": r.get("cost_price"),
                        "market_value": r.get("market_value"),
                    }
                    for r in rows[i : i + _SINK_BATCH]
                ]
                stmt = pg_insert(BacktestDailyPosition).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["task_id", "trade_date", "ts_code"],
                    set_={
                        "shares": stmt.excluded.shares,
                        "cost_price": stmt.excluded.cost_price,
                        "market_value": stmt.excluded.market_value,
                    },
                )
                await session.execute(stmt)
            await session.commit()

    async def get_daily_positions(
        self, task_id: str, limit: int = 500, offset: int = 0
    ) -> list[dict]:
        """A1：按 task_id 分页查回测每日持仓明细（trade_date, ts_code 升序）。"""
        from quantpilot.models.system import BacktestDailyPosition

        rows = (await self._session.execute(
            select(BacktestDailyPosition)
            .where(BacktestDailyPosition.task_id == task_id)
            .order_by(BacktestDailyPosition.trade_date, BacktestDailyPosition.ts_code)
            .limit(limit)
            .offset(offset)
        )).scalars().all()
        return [
            {
                "trade_date": str(r.trade_date),
                "ts_code": r.ts_code,
                "shares": r.shares,
                "cost_price": float(r.cost_price) if r.cost_price is not None else None,
                "market_value": (
                    float(r.market_value) if r.market_value is not None else None
                ),
            }
            for r in rows
        ]

    async def run_task(
        self,
        task_id: str,
        config: BacktestConfig,
        progress_cb: Callable[[str, int, float], None] | None = None,
    ) -> None:
        """
        异步编排主流程：
        ① 更新状态 RUNNING
        ② 预加载 BacktestDataBundle
        ③ 在线程池中执行 engine.run()
        ④ 写 BacktestResult
        ⑤ 更新状态 SUCCESS
        异常时更新 FAILED。
        """
        # ① 更新 RUNNING 并立即提交，保证轮询端点可见（flush 在 READ COMMITTED 下不可见）
        await self._update_status(task_id, "RUNNING", started_at=datetime.now(tz=timezone.utc))
        await self._session.commit()

        if self._engine is None:
            raise RuntimeError(
                "BacktestService.run_task 需注入 BacktestEngine"
                "（应由 _run_backtest_bg 根据 task.config_snapshot 构造）"
            )

        # R13-P1-1：BACKTEST_QUEUE_DEPTH inc/dec —— 用 try/finally 保证异常分支也释放
        from quantpilot.core.metrics import BACKTEST_QUEUE_DEPTH
        BACKTEST_QUEUE_DEPTH.inc()
        try:
            # ② 预加载历史数据
            data = await self._load_data_bundle(config)

            # A1（S6-GAP-02）：daily_positions 流式 sink。engine.run 在 to_thread 子线程
            # 跑，sink 从子线程触发 → 用 run_coroutine_threadsafe 把批量 upsert 投回主
            # loop（预捕获 loop，CLAUDE.md §2「线程回调中的 event loop」），fresh
            # AsyncSessionLocal 每批（不复用 self._session：属主线程请求上下文，不可跨线程）。
            # 有界缓冲 _SINK_BATCH 行满即 flush → 内存 O(batch) 常量，不累积 O(N×T)。
            loop = asyncio.get_running_loop()
            buffer: list[dict] = []

            def position_sink(trade_date: date, snapshots: list[dict]) -> None:
                buffer.extend(snapshots)
                if len(buffer) >= _SINK_BATCH:
                    fut = asyncio.run_coroutine_threadsafe(
                        self._flush_positions(task_id, list(buffer)), loop,
                    )
                    fut.result()  # 阻塞子线程直至写入完成（背压，防缓冲无界增长）
                    buffer.clear()

            # ③ 线程池执行（同步 CPU 密集）
            result = await asyncio.to_thread(
                self._engine.run, config, data, progress_cb, position_sink,
            )
            # flush 尾批（不足 _SINK_BATCH 的剩余持仓）
            if buffer:
                await self._flush_positions(task_id, buffer)

            # ④ 写 BacktestResult
            daily_nav_dict = {
                str(d): float(v) for d, v in zip(result.daily_nav.index, result.daily_nav.values)
            }
            br = BacktestResult(
                task_id=task_id,
                performance_json=result.performance,
                daily_nav_json=daily_nav_dict,
                disclaimer=result.disclaimer,
            )
            self._session.add(br)

            # ⑤ 更新 SUCCESS
            await self._update_status(task_id, "SUCCESS", finished_at=datetime.now(tz=timezone.utc))
            await self._session.commit()
            logger.info("backtest_task_success task_id=%s", task_id)

        except Exception as exc:
            logger.exception("backtest_task_failed task_id=%s", task_id)
            await self._update_status(
                task_id, "FAILED",
                finished_at=datetime.now(tz=timezone.utc),
                error_msg=str(exc),
            )
            await self._session.commit()
        finally:
            # R13-P1-1：成功/失败/异常分支都释放 queue depth
            BACKTEST_QUEUE_DEPTH.dec()

    async def _update_status(
        self,
        task_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error_msg: str | None = None,
    ) -> None:
        values: dict = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if finished_at is not None:
            values["finished_at"] = finished_at
        if error_msg is not None:
            values["error_msg"] = error_msg
        await self._session.execute(
            sql_update(BacktestTask).where(BacktestTask.task_id == task_id).values(**values)
        )
        await self._session.flush()

    async def _load_pe_pb_history_arrays(
        self, start_date: date, end_date: date
    ) -> PePbHistoryArrays:
        """把 `financial_data` 窗口内的 (ts_code, publish_date, pe_ttm, pb) 载成紧凑数组。

        走 asyncpg 的 `COPY (SELECT …) TO STDOUT CSV`，**逐块**解析直接写进预分配数组——
        不经过 SQLAlchemy Row（同样 646 万行，`session.stream` 光取行就要 22 s，COPY 约 8 s），
        也不把整段 CSV 攒成一个缓冲：生产 100 日窗口首版那样做（BytesIO 200 MB + `read_csv`
        的 DataFrame + 目标数组三份共存）把容器 `memory.peak` 推到 1926 MiB、离 2 GB 回退线
        只剩 6%（`deploy_log.md` 2026-09-22）；分块后瞬时峰值 ≈ 目标数组（24 B/行）+ 一块。
        日序数在 SQL 里算成整数（`publish_date - DATE '0001-01-01' + 1` ≡ `date.toordinal()`）；
        `ts_code` 按首次出现编号（跨块用一个 dict 维持）。先 `count(*)` 预分配。
        这是本仓唯一一处绕过 SQLAlchemy 直接用驱动的地方：只读、参数化、只为这一条大批量取数。
        """
        import io

        from quantpilot.models.market import FinancialData

        where = (
            FinancialData.publish_date >= start_date,
            FinancialData.publish_date <= end_date,
        )
        n_rows = int(
            (await self._session.execute(
                select(func.count()).select_from(FinancialData).where(*where)
            )).scalar_one()
        )
        code_idx = np.empty(n_rows, dtype=np.int32)
        day_ord = np.empty(n_rows, dtype=np.int32)
        pe = np.empty(n_rows, dtype=np.float64)
        pb = np.empty(n_rows, dtype=np.float64)
        labels: dict[str, int] = {}
        state = {"pos": 0, "tail": b"", "overflow": False}

        def _consume(block: bytes) -> None:
            """解析若干整行 CSV 写入数组；不足一行的尾巴留到下一块。"""
            if not block:
                return
            df = pd.read_csv(
                io.BytesIO(block), header=None, names=["c", "d", "pe", "pb"],
                dtype={"c": object, "d": np.int32, "pe": np.float64, "pb": np.float64},
            )
            k = len(df)
            pos = state["pos"]
            if pos + k > n_rows:
                # 预计数与 COPY 之间有并发写入：多出的行忽略并告警（回测窗口的数据本应静止）
                state["overflow"] = True
                k = n_rows - pos
                if k <= 0:
                    return
                df = df.iloc[:k]
            codes = df["c"]
            new = [c for c in pd.unique(codes) if c not in labels]
            for c in new:
                labels[c] = len(labels)
            code_idx[pos:pos + k] = codes.map(labels).to_numpy(dtype=np.int32)
            day_ord[pos:pos + k] = df["d"].to_numpy(dtype=np.int32)
            pe[pos:pos + k] = df["pe"].to_numpy(dtype=np.float64)
            pb[pos:pos + k] = df["pb"].to_numpy(dtype=np.float64)
            state["pos"] = pos + k

        async def _sink(chunk: bytes) -> None:
            # asyncpg 每次回调只有几十 KB；攒到约 4 MB（≈ 13 万行）再解析，
            # 让 read_csv 的每次固定开销摊薄，瞬时驻留仍只有一块。
            data = state["tail"] + chunk
            if len(data) < 4 << 20:
                state["tail"] = data
                return
            cut = data.rfind(b"\n")
            if cut < 0:
                state["tail"] = data
                return
            state["tail"] = data[cut + 1:]
            _consume(data[: cut + 1])

        conn = await self._session.connection()
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection  # asyncpg.Connection
        await driver.copy_from_query(
            "SELECT ts_code, publish_date - DATE '0001-01-01' + 1, "
            "pe_ttm::float8, pb::float8 "
            "FROM financial_data WHERE publish_date >= $1 AND publish_date <= $2",
            start_date, end_date, output=_sink, format="csv",
        )
        if state["tail"].strip():
            _consume(state["tail"] + b"\n")
        if state["overflow"]:
            logger.warning(
                "pe_pb_history_rows_exceed_precount: precount=%d, extra rows ignored", n_rows
            )
        pos = state["pos"]
        if pos < n_rows:  # 并发删除让行数少于预计数：截短
            code_idx, day_ord, pe, pb = code_idx[:pos], day_ord[:pos], pe[:pos], pb[:pos]
        code_labels = np.empty(len(labels), dtype=object)
        for c, i in labels.items():
            code_labels[i] = c
        return PePbHistoryArrays(
            code_labels=code_labels, code_idx=code_idx, day_ord=day_ord,
            values={"pe_ttm": pe, "pb": pb},
        )

    async def _load_data_bundle(self, config: BacktestConfig) -> BacktestDataBundle:
        """
        预加载全量历史数据。

        V1.0 整改 Batch 3 — B3-1/3/5/6/8 扩展：
        - adj_prices：close × adj_factor 后复权（同前）
        - daily_quotes：B3-1 完整字段（close/open/limit/suspended/st/amount）
        - stock_info：B3-6 含 delist_date；is_st/is_suspended 不再 hardcode False
        - financials：含 publish_date 供 PIT 切片；B3-7 主循环按 trade_date 切片
        - pe_pb_history：B3-3 真实加载（ValueStrategy 真实分位数）
        - index_adj_prices：B3-3 HS300 累计后复权 close（Momentum.rs_6m）
        - hs300_history：保持 OHLC 供 MarketStateEngine 使用
        - DataValidator：B3-8 daily_quotes 加载后走 validate_daily_quotes
        """
        from quantpilot.data.validators import DataValidator
        from quantpilot.models.market import DailyQuote, IndexHistory, StockInfo

        # 前置历史窗口（价格类）：MomentumStrategy 的 rs_6m 需要 **120 交易日** return_6m，
        # 约 168 日历天——原 130 日历天（≈ 90 交易日）不足 → return_6m 全 NaN → rs_6m 全 NaN
        # → 中性化 n_obs=0 退化。取 200 日历天（≈ 138 交易日）覆盖 120 交易日 + buffer。
        lookback_start = config.start_date - timedelta(days=200)
        # 财报 PIT 窗口更宽：某股「最近一期」财报可能已发布数月（季报间隔 + 披露延迟），
        # 130/200 日窗口会漏掉 → financials_t 该股为空 → value/quality NaN。原取 ~400 日历天；
        # 2026-09-22 起对齐生产 `get_latest_financial` 的基本面回看窗口（450 天）——引擎在
        # 内存里复现该 SQL 的 LOCF，切片比它窄时 total_equity 结转会少一期
        # （5434 实测 2026-07-14 有 1 只在 400 与 450 之间，对齐后 0 只不同）。
        fin_lookback_start = config.start_date - timedelta(days=_FUND_LOOKBACK_DAYS)

        # ── 1. daily_quotes 全字段加载（B3-1） ───────────────────────────────
        # 2026-09-16 内存修法：原 `select(DailyQuote)...scalars().all()` 把窗口内 ~76 万行
        # 实例化成 ORM 对象（每个含 identity-map 簿记 + 20 余个 Decimal），再 list-of-dicts
        # 转 DataFrame——两份都是 GB 级，是 6 日回测峰值 3.5 GB 的主项（财务切片改流式后
        # 只降到 3.2 GB，说明大头在这里）。改为列裁剪 + 服务端游标分块 + 每块立即转 float。
        _dq_cols = [
            "trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
            "adj_factor", "is_suspended", "is_st", "limit_up", "limit_down",
            "turnover_rate", "float_mkt_cap",
        ]
        dq_stmt = (
            select(
                DailyQuote.trade_date, DailyQuote.ts_code,
                # NUMERIC 在 SQL 内 cast 成 float8：asyncpg 于是返回 Python float 而不是 Decimal。
                # 2026-09-16 实测：Decimal 版每 5 万行块驻留约 40 MB（数据本身 9 MB），
                # 是分配器碎片——对象释放了、工作集不降；改 cast 后不产生这批对象。
                cast(DailyQuote.open, Float), cast(DailyQuote.high, Float),
                cast(DailyQuote.low, Float), cast(DailyQuote.close, Float),
                cast(DailyQuote.vol, Float), cast(DailyQuote.amount, Float),
                cast(DailyQuote.adj_factor, Float),
                DailyQuote.is_suspended, DailyQuote.is_st, DailyQuote.limit_up,
                DailyQuote.limit_down,
                # SDD-EXT-02s（V1.5-A A2）：无量一字板判定所需换手率（入库为小数）
                cast(DailyQuote.turnover_rate, Float),
                # Phase 14 §14-3：market_cap 中性化所需 PIT 流通市值
                cast(DailyQuote.float_mkt_cap, Float),
            )
            .where(DailyQuote.trade_date >= lookback_start)
            .where(DailyQuote.trade_date <= config.end_date)
        )
        _dq_chunks: list[pd.DataFrame] = []
        _dq_stream = await self._session.stream(dq_stmt.execution_options(yield_per=50_000))
        async for _part in _dq_stream.partitions(50_000):
            _df = pd.DataFrame(_part, columns=_dq_cols)
            for c in ("open", "high", "low", "close", "turnover_rate", "float_mkt_cap"):
                _df[c] = pd.to_numeric(_df[c], errors="coerce")
            # 与原实现的缺省语义一致：vol/amount 缺 → 0，adj_factor 缺 → 1.0
            _df["vol"] = pd.to_numeric(_df["vol"], errors="coerce").fillna(0.0)
            _df["amount"] = pd.to_numeric(_df["amount"], errors="coerce").fillna(0.0)
            _df["adj_factor"] = pd.to_numeric(_df["adj_factor"], errors="coerce").fillna(1.0)
            for c in ("is_suspended", "is_st", "limit_up", "limit_down"):
                _df[c] = _df[c].fillna(False).astype(bool)
            _dq_chunks.append(_df)
        del _dq_stream
        dq_rows = bool(_dq_chunks)   # 下游只当布尔用（原为 ORM 行列表）
        if dq_rows:
            dq_df = pd.concat(_dq_chunks, ignore_index=True)
            del _dq_chunks

            # B3-8：DataValidator 校验 + 剔除无效行
            validator = DataValidator()
            validation = validator.validate_daily_quotes(dq_df, prev_count=len(dq_df))
            if len(validation.invalid_rows) > 0:
                logger.warning(
                    "backtest_data_validator_drops invalid_rows=%d reason=%s",
                    len(validation.invalid_rows),
                    validation.warnings or validation.errors,
                )
                dq_df = dq_df.drop(index=validation.invalid_rows)

            dq_df["adj_close"] = dq_df["close"] * dq_df["adj_factor"]
            adj_prices = dq_df.pivot(
                index="trade_date", columns="ts_code", values="adj_close"
            )
            adj_prices.index = pd.to_datetime(adj_prices.index)
            dq_ts_codes: set[str] = set(dq_df["ts_code"].unique())

            # daily_quotes 完整字段保留（B3-1 主循环按 trade_date+ts_code 切片用）
            daily_quotes = dq_df.set_index(["trade_date", "ts_code"]).sort_index()
        else:
            dq_df = pd.DataFrame()
            adj_prices = pd.DataFrame()
            dq_ts_codes = set()
            daily_quotes = pd.DataFrame()

        # ── 2. stock_info（B3-6 含 delist_date） ─────────────────────────────
        stock_rows = (await self._session.execute(
            select(StockInfo)
        )).scalars().all()
        si_map: dict[str, dict] = {
            r.ts_code: {
                "list_date": r.list_date,
                "delist_date": r.delist_date,  # B3-6：退市日时点过滤
                "sw_industry_l1": r.sw_industry_l1,
            }
            for r in stock_rows
        }
        # 补入 daily_quote 中有行情但 stock_info 缺失的股票
        _DEFAULT_LIST_DATE = date(2000, 1, 1)
        for ts_code in dq_ts_codes:
            if ts_code not in si_map:
                si_map[ts_code] = {
                    "list_date": _DEFAULT_LIST_DATE,
                    "delist_date": None,
                    "sw_industry_l1": None,
                }
        for v in si_map.values():
            if v["list_date"] is None:
                v["list_date"] = _DEFAULT_LIST_DATE
        if si_map:
            stock_info = pd.DataFrame.from_dict(si_map, orient="index")
            stock_info.index.name = "ts_code"
        else:
            stock_info = pd.DataFrame()

        # ── 3. financials（+ 3b. pe_pb_history） ──────────────────────────────
        # 内存优化（2026-06-12）：原实现 `select(FinancialData)` 全表（631 万行）×
        # 两次 list-of-dicts materialize（financials + pe_pb_history），在 2GB 机上
        # 跑长区间回测必爆内存。改为：
        #   (1) 按 [fin_lookback_start, end_date] 切界 publish_date——financial_data 是日级
        #       （每股每交易日一行），PIT 在 trade_date 取 publish_date<=trade_date 的最近
        #       一行；年报发布滞后可达近一年，最近一期有效报表可能早于 start 数百天，故
        #       下界放宽到 start-400d 才能保住这些票的 PIT 行（2026-06-17 回测健康修复：
        #       原 130 天界过紧会漏掉 → value pe/pb 全 NaN 退化）；上界 end_date 本就排除
        #       未来数据。回测 [start,end] 永不引用窗口外行。
        #   (2) 列裁剪 select（只取 8 列、返回轻量 Row 元组，不进 identity map）；
        #   (3) pe_pb_history 从 fin_df 列子集派生，不再二次 materialize 全量。
        from quantpilot.models.market import FinancialData
        _fin_cols = [
            "ts_code", "report_period", "publish_date",
            "net_profit_yoy", "total_equity", "debt_to_asset", "pe_ttm", "pb", "roe",
        ]
        _fin_num = ("net_profit_yoy", "total_equity", "debt_to_asset", "pe_ttm", "pb", "roe")
        fin_stmt = (
            select(
                FinancialData.ts_code,
                FinancialData.report_period,
                FinancialData.publish_date,
                cast(FinancialData.net_profit_yoy, Float),
                cast(FinancialData.total_equity, Float),
                cast(FinancialData.debt_to_asset, Float),
                cast(FinancialData.pe_ttm, Float),
                cast(FinancialData.pb, Float),
                # ⚠️ 别再把 roe 从这里裁掉：列裁剪优化（2026-06-12）漏掉过一次 →
                # 回测 value 策略整个被跳过。原因当时是 roe_quality 因子依赖它；
                # **2026-09-09 起 roe_quality 默认不入合成，但 roe 仍然必需**——
                # `ValueStrategy.apply_constraints` 的价值陷阱护栏（SDD §7.2.4）读它。
                # 即：那条注释的理由变了，结论没变，裁掉照样出事。
                cast(FinancialData.roe, Float),
            )
            .where(FinancialData.publish_date >= fin_lookback_start)
            .where(FinancialData.publish_date <= config.end_date)
        )
        # 2026-09-16 内存修法（回测 6 日峰值 3530 MB 的主项）：原 `.all()` 把 ~150 万行
        # 先实例化成 SQLAlchemy Row（内含 Decimal）再整体转 DataFrame——Row 列表本身就是
        # GB 级。改为服务端游标流式分块，每块立即 to_numeric 成 float 再 concat：
        # 峰值 = 一块 Row（5 万行）+ 已转好的 float 表（150 万 × 9 × 8B ≈ 108 MB）。
        _chunks: list[pd.DataFrame] = []
        _stream = await self._session.stream(fin_stmt.execution_options(yield_per=50_000))
        async for _part in _stream.partitions(50_000):
            _df = pd.DataFrame(_part, columns=_fin_cols)
            for c in _fin_num:
                _df[c] = pd.to_numeric(_df[c], errors="coerce")
            _chunks.append(_df)
        del _stream
        _chunks_seen = bool(_chunks)
        if _chunks:
            fin_df = pd.concat(_chunks, ignore_index=True)
            del _chunks
            # 直接放 `_prepare_financials` 的产物（扁平、已排序、带 `_pub`）：引擎里同名调用
            # 幂等、零拷贝。2026-09-22 首版让引擎自己排，等于 bundle 里多背一份 150 万行
            # ——生产 100 日 `memory.peak` 因此没降（1959 MiB）。
            financials = _prepare_financials(fin_df)
        else:
            financials = pd.DataFrame()
        # 3b. pe_pb_history 不再派生（留空）：分位改在 PostgreSQL 内算，见下方 3d。
        pe_pb_history = pd.DataFrame()

        # ── 3d. PE/PB 历史分位：与生产同语义，回测在内存里算（2026-09-21）────────────
        # 生产每日管线走 `get_latest_financial`（当前 pe/pb）→ `get_pe_pb_percentile_bulk`
        # （5 年窗口、SQL 内 1 - pct_rank）；回测 2026-09-16 起曾按日复用那两条 SQL（把此前
        # ~400 天的内存 pe_pb_history 换掉：既是 3530 MB 峰值主项，也是与生产不同口径的偏差）。
        # 2026-09-21 两步都改内存：
        # (a) 「当前 pe/pb」从上面已在内存的 fin_df 切（`_latest_pe_pb_at`，语义 = 日频段：
        #     每码 publish_date<=td 的最新一行），5434 六个交易日逐码逐值与 SQL 相同；
        # (b) 分位本身：把 5 年窗口的 (ts_code, publish_date, pe_ttm, pb) 以**紧凑数组**流式载入
        #     （24 B/行，640 万行 ≈ 155 MB——当年 3530 MB 的主项是 Row/Decimal 与宽 DataFrame，
        #     不是这些 float），`pe_pb_percentile_in_memory` 每日两次 bincount 算完。
        #     SQL 版每日两列约 4 s，是 6 日回测第一大耗时项，且合并两列 / 覆盖索引都实测无效
        #     （见 repo 方法 docstring）；每日管线一天只算一次，**仍走 SQL**。
        # 交易日 = 窗口内 daily_quote 实际存在的日期（无行情的日子引擎本就不评分）。
        from quantpilot.data.repository import MarketDataRepository
        from quantpilot.services.strategy_service import resolve_pe_pb_history_years

        # 与生产同源：窗口年数读 ValueStrategyConfig（engine 为 None 的纯加载场景回落 5）
        _years = resolve_pe_pb_history_years(
            getattr(self._engine, "_strategies", None) if self._engine is not None else None
        )

        pe_percentile_by_date: dict[date, pd.Series] = {}
        pb_percentile_by_date: dict[date, pd.Series] = {}
        _bt_days = sorted(
            d for d in set(dq_df["trade_date"]) if config.start_date <= d <= config.end_date
        ) if dq_rows else []
        _all_codes = list(stock_info.index) if not stock_info.empty else []
        _pe_pb_src = _latest_pe_pb_source(fin_df) if _chunks_seen else pd.DataFrame()
        if _chunks_seen:
            del fin_df  # 未排序原帧此后无人用；排序副本已在 `financials`
        _pe_pb_hist = await self._load_pe_pb_history_arrays(
            (_bt_days[0] if _bt_days else config.start_date) - timedelta(days=365 * _years),
            config.end_date,
        )
        for _td in _bt_days:
            _fin_t = _latest_pe_pb_at(_pe_pb_src, _td, _all_codes)
            if _fin_t.empty:
                continue
            _start = _td - timedelta(days=365 * _years)
            for _col, _sink in (("pe_ttm", pe_percentile_by_date), ("pb", pb_percentile_by_date)):
                if _col not in _fin_t.columns:
                    continue
                _curr = {
                    str(code): float(v)
                    for code, v in _fin_t[_col].items()
                    if v is not None and not pd.isna(v)
                }
                if not _curr:
                    continue
                _sink[_td] = pe_pb_percentile_in_memory(_pe_pb_hist, _curr, _start, _td, _col)
        del _pe_pb_hist

        # ── 3c. financial_forecast（SDD-EXT-03 A5b 前瞻 ROE 覆盖）─────────────
        # 全量预加载业绩预告/快报（pre_announce_date 在 [fin_lookback_start, end_date]），
        # 交由 BacktestEngine._get_forecast_at 按 trade_date 做 PIT 内存切片（Engine 无 IO）。
        # 与 financials 同用 fin_lookback_start 下界：快报早于回测起点数月发布仍需可见。
        from quantpilot.models.market import FinancialForecast
        fc_rows = (await self._session.execute(
            select(
                FinancialForecast.ts_code,
                FinancialForecast.report_period,
                FinancialForecast.pre_announce_date,
                FinancialForecast.est_net_profit,
                FinancialForecast.data_priority,
            )
            .where(FinancialForecast.pre_announce_date >= fin_lookback_start)
            .where(FinancialForecast.pre_announce_date <= config.end_date)
        )).all()
        if fc_rows:
            forecast = pd.DataFrame(fc_rows, columns=[
                "ts_code", "report_period", "pre_announce_date",
                "est_net_profit", "data_priority",
            ])
            forecast["est_net_profit"] = pd.to_numeric(
                forecast["est_net_profit"], errors="coerce"
            )
        else:
            forecast = pd.DataFrame()

        # ── 4. hs300_history（OHLC + 累计后复权 close） ──────────────────────
        hs300_rows = (await self._session.execute(
            select(IndexHistory)
            .where(IndexHistory.index_code == "000300.SH")
            .where(IndexHistory.trade_date >= lookback_start)
            .where(IndexHistory.trade_date <= config.end_date)
            .order_by(IndexHistory.trade_date)
        )).scalars().all()
        if hs300_rows:
            hs300_history = pd.DataFrame([{
                "trade_date": r.trade_date,
                "open": float(r.open) if r.open is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "close": float(r.close) if r.close is not None else None,
                "vol": float(r.vol) if r.vol is not None else None,
            } for r in hs300_rows])
            # B3-3：index_adj_prices 提供给 Momentum 相对强度计算
            index_adj_prices = hs300_history.set_index("trade_date")["close"].copy()
            index_adj_prices.index = pd.to_datetime(index_adj_prices.index)
        else:
            hs300_history = pd.DataFrame()
            index_adj_prices = pd.Series(dtype=float)

        # ── 5. active_weights_history（Phase 14 §14-3）─────────────────────
        # 注：实际 ORM `StrategyWeightsHistory` schema 是一行一 (state, strategy, trade_date) +
        # weight_used 标量；本处把若干行 group by (state, trade_date) 组装成 dict（与
        # FactorMonitorService.get_active_weights:711 的运行时组装路径同源；orthogonalize_order
        # 由 weights 降序派生，未持久化列）。设计 v1.2 §5.2.2 的 (market_state, effective_date)/
        # weights_json 是模板误判，本处按真 schema 实现。
        from quantpilot.models.business import StrategyWeightsHistory

        sw_rows = (await self._session.execute(
            select(StrategyWeightsHistory)
            .where(StrategyWeightsHistory.trade_date <= config.end_date)
            .order_by(
                StrategyWeightsHistory.state,
                StrategyWeightsHistory.trade_date,
                StrategyWeightsHistory.strategy,
            )
        )).scalars().all()
        active_weights_history: dict[tuple[str, date], dict] = {}
        for r in sw_rows:
            key = (r.state, r.trade_date)
            slot = active_weights_history.setdefault(
                key,
                {
                    "weights": {},
                    "weights_source": r.weights_source,
                    "orthogonalize_order": [],
                    "hysteresis_status": r.hysteresis_status,
                },
            )
            slot["weights"][r.strategy] = float(r.weight_used)
        # orthogonalize_order：与生产 get_active_weights:711 一致——按 weight 降序
        for slot in active_weights_history.values():
            slot["orthogonalize_order"] = sorted(
                slot["weights"], key=lambda s: slot["weights"][s], reverse=True,
            )

        # ── 3f. 资金流向（V1.5-C C4，2026-09-23）──────────────────────────────
        # 复用生产那条 SQL（`get_money_flow_window`，含 INNER JOIN daily_quote 取 amount），
        # 只把窗口从「单日回看 40 天」拉宽成「[start - 40天, end]」——列、类型、排序都与
        # 生产逐字相同，引擎再按日切 `_money_flow_at`（同一个 40 天下界）。
        # 2y 回填未做时该表只有样本区间的数据 → 窗口外的日子行数不足 → 因子 NaN（设计内）。
        _mf_lookback = resolve_money_flow_lookback_days(
            getattr(self._engine, "_strategies", None) if self._engine is not None else None
        )
        _mf_repo = MarketDataRepository(self._session)
        _mf_codes = list(stock_info.index) if not stock_info.empty else []
        money_flow = (
            await _mf_repo.get_money_flow_window(
                _mf_codes,
                config.end_date,
                (config.end_date - config.start_date).days + _mf_lookback,
            )
            if _mf_codes
            else pd.DataFrame()
        )

        return BacktestDataBundle(
            money_flow=money_flow,
            adj_prices=adj_prices,
            stock_info=stock_info,
            financials=financials,
            hs300_history=hs300_history,
            daily_quotes=daily_quotes,
            pe_pb_history=pe_pb_history,
            pe_percentile_by_date=pe_percentile_by_date,
            pb_percentile_by_date=pb_percentile_by_date,
            index_adj_prices=index_adj_prices,
            active_weights_history=active_weights_history,
            forecast=forecast,
        )


def _latest_pe_pb_source(fin_df: pd.DataFrame) -> pd.DataFrame:
    """把 financial 切片压成「按 (ts_code, publish_date↓) 稳定排序的 4 列表」，供逐日切取。

    只留 ts_code / publish_date / pe_ttm / pb，150 万行 × 4 列 ≈ 50 MB，一次排序后每日
    只需一个布尔过滤 + `drop_duplicates`。
    """
    if fin_df.empty:
        return pd.DataFrame(columns=["ts_code", "publish_date", "pe_ttm", "pb"])
    src = fin_df[["ts_code", "publish_date", "pe_ttm", "pb"]]
    return src.sort_values(
        ["ts_code", "publish_date"], ascending=[True, False], kind="mergesort",
    ).reset_index(drop=True)


def _latest_pe_pb_at(src: pd.DataFrame, trade_date: date, ts_codes: list[str]) -> pd.DataFrame:
    """在内存里复现 `MarketDataRepository.get_latest_financial` 的**日频段**。

    语义：每码取 `publish_date <= trade_date` 的最新一行的 pe_ttm / pb，只含 `ts_codes`
    内的码；返回 index=ts_code。同一码同一 publish_date 多行时 SQL 的 `DISTINCT ON`
    取哪行未定义，这里取稳定排序后的首行——5434 实测六个交易日与 SQL 逐码逐值相同
    （`tests/unit/test_backtest_pe_pb_pushdown.py` 用合成数据钉语义）。
    """
    if src.empty or not ts_codes:
        return pd.DataFrame()
    sub = src[src["publish_date"] <= trade_date]
    latest = sub.drop_duplicates("ts_code", keep="first").set_index("ts_code")
    latest = latest[latest.index.isin(set(ts_codes))]
    return latest[["pe_ttm", "pb"]]


@dataclass(frozen=True)
class PePbHistoryArrays:
    """PE/PB 五年历史的紧凑列式存储（回测分位在内存里算，2026-09-21）。

    每行 (ts_code, publish_date, pe_ttm, pb) 存成 int32 码序号 + int32 日序数 + 2 × float64，
    24 B/行；5434 五年窗口 640 万行 ≈ 155 MB。**不是** SQLAlchemy Row / 宽 DataFrame——
    2026-09-14 那次 3530 MB 峰值的主项是 150 万行 Row（内含 Decimal）与整段 5 年 DataFrame，
    不是这些 float 本身。
    """

    code_labels: np.ndarray      # str[n_codes]，序号 → ts_code
    code_idx: np.ndarray         # int32[n_rows]
    day_ord: np.ndarray          # int32[n_rows]，date.toordinal()
    values: dict[str, np.ndarray]  # {"pe_ttm": float64[n_rows], "pb": float64[n_rows]}

    @property
    def n_rows(self) -> int:
        return int(self.code_idx.shape[0])


def pe_pb_percentile_in_memory(
    hist: PePbHistoryArrays,
    current_values: Mapping[str, float],
    start_date: date,
    end_date: date,
    col: str,
) -> pd.Series:
    """`MarketDataRepository.get_pe_pb_percentile_bulk` 的内存等价实现，逐码逐值相同。

    语义（与 SQL 版逐条对应，`tests/unit/test_backtest_pe_pb_in_memory.py` 用同一个
    Python 参考实现 `_compute_historical_percentile` 钉；5434 六个交易日与 SQL 逐码比对
    见 docstring 末尾）：

    - 窗口：`publish_date` ∈ [start_date, end_date] 闭区间
    - 严格 `<`；分母 = 窗口内该列**非 NULL** 条数；返回 `1 - pct_rank`
    - 当前值缺失 / 无历史 → NaN（不是 0）
    - index = `current_values` 的全部 key

    整段向量化：一次窗口掩码 + 两次 `bincount`，与股票数无关地扫一遍 n_rows，
    5434 实测每列约 60 ms（SQL 版 2 s）。

    为什么回测不再走 SQL（2026-09-21）：分位 SQL 是 `financial_data` 五年窗口的并行 seq scan
    + hash join + 逐行 FILTER 聚合，每个交易日两列约 4 s，是 6 日回测第一大耗时项；
    合并两列一次扫表 / 强制覆盖索引都实测无效（见 repo 方法 docstring）。每日管线一天只算
    一次，4 s 无所谓，**仍走 SQL**；回测每天都要算，才值得把 155 MB 的紧凑历史搬进来。
    """
    index = pd.Index([str(k) for k in current_values.keys()], name="ts_code")
    if hist.n_rows == 0 or index.empty:
        return pd.Series(float("nan"), index=index, dtype=float)
    vals = hist.values[col]
    n_codes = int(hist.code_labels.shape[0])
    label_pos = pd.Index(hist.code_labels)
    # 当前值按码序号铺开；无当前值 / NaN / 不在历史里的码 → NaN（下面比较恒 False）
    cur_by_idx = np.full(n_codes, np.nan, dtype=np.float64)
    pos = label_pos.get_indexer(index)
    cur_arr = np.array(
        [float("nan") if v is None else float(v) for v in current_values.values()],
        dtype=np.float64,
    )
    ok = pos >= 0
    cur_by_idx[pos[ok]] = cur_arr[ok]

    in_win = (hist.day_ord >= start_date.toordinal()) & (hist.day_ord <= end_date.toordinal())
    idx = hist.code_idx[in_win]
    v = vals[in_win]
    not_null = ~np.isnan(v)
    denom = np.bincount(idx[not_null], minlength=n_codes).astype(np.float64)
    less = not_null & (v < cur_by_idx[idx])
    numer = np.bincount(idx[less], minlength=n_codes).astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(denom > 0, numer / denom, np.nan)
    out = 1.0 - pct
    out[np.isnan(cur_by_idx)] = np.nan
    result = np.full(index.shape[0], np.nan, dtype=np.float64)
    result[ok] = out[pos[ok]]
    return pd.Series(result, index=index, dtype=float)


def build_engine_from_snapshot(snap: dict, calendar) -> BacktestEngine:
    """从 config_snapshot 构造 BacktestEngine（API 后台任务 + 本地 CLI 共用）。

    Phase 10 §4.4 评审 C-02/C-03：Engine 不再是 app.state 单例，按 task.config_snapshot
    即时构造，确保用户当前策略/风险/池配置被消费。本函数把构造逻辑从 `_run_backtest_bg`
    抽出，供 scripts/run_backtest_local.py 本地回测复用同一构造路径（行为与生产一致）。
    """
    from quantpilot.engine.market_state import MarketStateEngine
    from quantpilot.engine.position import PositionSizer
    from quantpilot.engine.scorer import Scorer
    from quantpilot.engine.signal import SignalGenerator
    from quantpilot.engine.strategies.low_volatility import LowVolatilityStrategy
    from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
    from quantpilot.engine.strategies.momentum import MomentumStrategy
    from quantpilot.engine.strategies.money_flow import MoneyFlowStrategy
    from quantpilot.engine.strategies.trend import TrendStrategy
    from quantpilot.engine.strategies.value import ValueStrategy
    from quantpilot.engine.universe import UniverseFilter
    from quantpilot.services.config_snapshot import from_snapshot

    trend_cfg = from_snapshot(snap, "strategy_params_trend")
    momentum_cfg = from_snapshot(snap, "strategy_params_momentum")
    mr_cfg = from_snapshot(snap, "strategy_params_mean_reversion")
    value_cfg = from_snapshot(snap, "strategy_params_value")
    ms_cfg = from_snapshot(snap, "market_state_params")
    universe_cfg = from_snapshot(snap, "universe_params")
    weights_cfg = from_snapshot(snap, "strategy_weights")
    signal_cfg = from_snapshot(snap, "signal_params")

    return BacktestEngine(
        strategies=[
            TrendStrategy(trend_cfg),
            MomentumStrategy(momentum_cfg),
            MeanReversionStrategy(mr_cfg),
            ValueStrategy(value_cfg),
            # V1.5-C C3：影子模式（权重 0）。四处组装点必须同步。
            LowVolatilityStrategy(),
            # V1.5-C C4：影子模式（权重 0）。回测自 2026-09-23 起也喂 money_flow
            # （bundle + 逐日 PIT 切片），故因子有值、进 composite 但权重 0。
            MoneyFlowStrategy(),
        ],
        market_state_engine=MarketStateEngine(ms_cfg),
        universe_filter=UniverseFilter(universe_cfg),
        scorer=Scorer(weights_cfg),
        signal_engine=SignalGenerator(signal_cfg=signal_cfg, universe_cfg=universe_cfg),
        position_engine=PositionSizer(),
        price_provider=None,
        calendar=calendar,
    )


async def reconcile_orphan_backtests(session_factory) -> int:
    """应用启动时回收孤儿回测任务（残留 RUNNING/PENDING → FAILED）。

    回测在后台 BackgroundTask 中执行；进程因部署/重启/OOM 中断时，`run_task` 来不及
    写 SUCCESS/FAILED，任务会永久卡在 RUNNING/PENDING（轮询端点永远拿不到结果，前端
    表现为"超时"）。启动时把这类残留任务标 FAILED——既清理历史孤儿、也防复发。

    用独立事务（`async with session.begin()`）保证 CLI/启动钩子路径显式提交。
    返回回收条数。
    """
    from datetime import datetime, timezone

    from sqlalchemy import update as sql_update

    from quantpilot.models.system import BacktestTask

    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                sql_update(BacktestTask)
                .where(BacktestTask.status.in_(("RUNNING", "PENDING")))
                .values(
                    status="FAILED",
                    error_msg="进程重启中断（孤儿任务启动回收）",
                    finished_at=datetime.now(tz=timezone.utc),
                )
            )
    count = result.rowcount or 0
    if count:
        logger.info("reconcile_orphan_backtests recovered=%d", count)
    return count


def _config_to_dict(config: BacktestConfig) -> dict:
    return {
        "start_date": str(config.start_date),
        "end_date": str(config.end_date),
        "initial_capital": config.initial_capital,
        "strategy_config": config.strategy_config,
        "account_config": config.account_config,
        "commission_rate": config.commission_rate,
        "stamp_tax_rate": config.stamp_tax_rate,
        "slippage_rate": config.slippage_rate,
    }
