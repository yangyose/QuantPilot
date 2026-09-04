from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, nullslast, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.diagnostics.factor_ic import PanelStatPoint
from quantpilot.engine.market_state import MarketStateRecord
from quantpilot.models.account import Account, Position
from quantpilot.models.business import (
    CandidatePool,
    FactorPanelStat,
    MarketStateHistory,
    Signal,
    SignalScoreSnapshot,
    UserWatchlist,
)
from quantpilot.models.market import (
    DailyQuote,
    FinancialData,
    FinancialForecast,
    IndexComponent,
    IndexHistory,
    StockInfo,
    TradeCalendar,
)

_STOCK_UPDATE_COLS = [
    "name", "sw_industry_l1", "sw_industry_l2", "market", "delist_date", "is_active",
]
_QUOTE_UPDATE_COLS = [
    "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount",
    "turnover_rate", "float_mkt_cap", "adj_factor",
    "is_suspended", "is_st", "limit_up", "limit_down",
]
def _clamp_to_schema_bounds(df: pd.DataFrame, bounds: dict[str, float]) -> pd.DataFrame:
    """超出 PostgreSQL NUMERIC 列允许范围的值置 NaN（→ NULL）。

    Tushare 偶尔返回极端值（如净资产接近 0 的微盘股 ROE > 100，新上市股
    net_profit_yoy 在千百分位），asyncpg 写入 NUMERIC 列直接抛 OutOfRange
    导致整个 batch 失败。本函数把这类异常值置 NaN，由 _df_to_dict_with_nulls
    转 None → SQL NULL，下游因子计算自动跳过。

    bounds: 列名 → 允许绝对值上界（含等号视为越界）
    """
    out = df.copy()
    for col, upper in bounds.items():
        if col in out.columns:
            mask = out[col].abs() >= upper
            if mask.any():
                out.loc[mask, col] = float("nan")
    return out


_FINANCIAL_BOUNDS = {
    # PostgreSQL Numeric(P, S) max abs value = 10 ** (P - S)
    "pe_ttm": 10 ** (10 - 4),       # 1e6
    "pb": 10 ** (8 - 4),            # 1e4
    "roe": 10 ** (8 - 6),           # 1e2
    "net_profit_yoy": 10 ** (8 - 4),  # 1e4
    "revenue_yoy": 10 ** (8 - 4),     # 1e4
    "dividend_yield": 10 ** (8 - 6),  # 1e2
    "debt_to_asset": 10 ** (8 - 6),   # 1e2
    # total_equity Numeric(18,2) → max abs 1e16（元）。真实最大 ~4e12（工行级），
    # 远低于界；Tushare 偶发脏值（错误巨数）≥1e16 会溢出整批 INSERT（2026-08-10 生产
    # 回填实证 NumericValueOutOfRangeError，savepoint 隔离下仍损含该股的整 50-批 ≈10%）。
    # 未 clamp 是历史遗漏（total_equity 由 refresh 路径独有，日常 ingest 不写此字段）。
    "total_equity": 10 ** (18 - 2),   # 1e16
}
_QUOTE_BOUNDS = {
    # 行情字段一般不会超界但留接口；turnover_rate Numeric(8,6) 见 market.py
    "turnover_rate": 10 ** (8 - 6),
    "pct_chg": 10 ** (8 - 6),
}


def _df_to_dict_with_nulls(df: pd.DataFrame) -> list[dict]:
    """Bug 9 修复 v2：把 DataFrame 转 list[dict]，所有 NaN/NaT/pd.NA 转 None。

    早期实现用 `df.where(pd.notna(df), None).to_dict(orient="records")`，但
    pandas 对 float64 列做 .where(..., None) 时 None 会被回退为 NaN（pandas
    保持 dtype 一致性），结果 to_dict 后仍是 float('nan')；asyncpg 把它写入
    PostgreSQL NUMERIC 字段作为特殊值 'NaN'（≠ NULL），下游 IS NOT NULL 误判。
    解决：先 to_dict，再在 dict 层逐项检查 isinstance(v, float) and isnan(v)
    显式转 None。也覆盖 pd.NaT（pd.isna 对其返回 True）。
    """
    import math as _m
    records = df.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if v is None:
                continue
            if isinstance(v, float) and _m.isnan(v):
                row[k] = None
            elif pd.isna(v):  # 处理 pd.NaT / pd.NA / np.datetime64 NaT
                row[k] = None
    return records


_FINANCIAL_UPDATE_COLS = [
    "pe_ttm", "pb", "roe", "net_profit_yoy", "revenue_yoy",
    "dividend_yield", "total_equity", "debt_to_asset",
]
_INDEX_UPDATE_COLS = ["open", "high", "low", "close", "vol", "pct_chg"]

_BATCH_SIZE = 500

# get_latest_financial 基本面段 LOCF 回看窗口（天）：只回看最近 ~450 日的报告期，
# 界定 GROUP BY 扫描量（6.5M 行 / 2GB 机性能）。代价：停报 > 450 天的股取不到基本面
# （活跃池极少）。450 ≈ 1 年 + 一个季度余量，确保跨季度末真空期能回落到上一披露期。
_FUND_LOOKBACK_DAYS = 450

# 基本面字段（报告期属性，走 LOCF 取最近有值报告期）；pe/pb/dividend_yield 是日频字段。
_FUND_FIELDS = ("roe", "net_profit_yoy", "revenue_yoy", "debt_to_asset", "total_equity")


class MarketDataRepository:
    """市场数据仓库，所有写操作使用幂等 upsert。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """显式 session 访问点。

        Phase 7 C-02 原意是禁止 Service 层 raw SQL 绕过 Repository 接口；本属性
        仅供同 Service 内部把 session 透传给 Engine 层或其他无状态 Repository
        （如 ``ScoringService.score_universe`` 把 session 透传给
        ``FactorMonitorService.get_active_weights`` / ``FactorICRepository.*``），
        不得用于 Service 自身的 ``await self._repo.session.execute(stmt)``——
        新增查询请改为 ``MarketDataRepository`` 方法。
        """
        return self._session

    # ── stock_info ─────────────────────────────────────────────────────────────

    async def upsert_stock_list(self, df: pd.DataFrame) -> int:
        """批量 upsert stock_info，500 行/批（asyncpg 单 SQL 参数上限 32767）。
        ON CONFLICT (ts_code) DO UPDATE"""
        total = 0
        # Bug 9 修复 v2：dict 层显式 NaN→None；详见 _df_to_dict_with_nulls 注释
        rows = _df_to_dict_with_nulls(df)
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(StockInfo).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ts_code"],
                set_={
                    **{col: stmt.excluded[col] for col in _STOCK_UPDATE_COLS if col in df.columns},
                    "updated_at": func.now(),
                },
            )
            result = await self._session.execute(stmt)
            total += result.rowcount
        return total

    async def get_active_stock_codes(self) -> list[str]:
        """返回所有 is_active=True 的 ts_code 列表"""
        result = await self._session.execute(
            select(StockInfo.ts_code).where(StockInfo.is_active.is_(True))
        )
        return [row[0] for row in result.all()]

    async def get_active_stock_codes_as_of(self, trade_date: date) -> list[str]:
        """返回截至 trade_date 时实际上市且未退市的股票 ts_code 列表（PIT）。

        与 get_active_stock_codes() 的区别：后者读取当前 is_active 快照，含 2026 年
        新上市股。本方法按 list_date / delist_date 做 PIT 过滤，正确返回当时的活股
        集合。历史回填的完整性校验（DataValidator.validate_daily_quotes 的 prev_count）
        必须用 PIT 版本——否则 5 年前的 fetch_daily_quotes 返回 ~4300 只对比当前
        ~5840 必然校验失败（RM-18，2026-05-13 真机验收）。
        """
        result = await self._session.execute(
            select(StockInfo.ts_code).where(
                StockInfo.list_date.is_not(None),
                StockInfo.list_date <= trade_date,
                or_(
                    StockInfo.delist_date.is_(None),
                    StockInfo.delist_date > trade_date,
                ),
            )
        )
        return [row[0] for row in result.all()]

    async def get_stock_info_bulk(
        self, ts_codes: list[str] | None = None
    ) -> pd.DataFrame:
        """返回 stock_info 基础信息（index=ts_code）。ts_codes=None 时返回所有 is_active。"""
        q = select(
            StockInfo.ts_code,
            StockInfo.name,
            StockInfo.list_date,
            StockInfo.sw_industry_l1,
        )
        if ts_codes is not None:
            q = q.where(StockInfo.ts_code.in_(ts_codes))
        else:
            q = q.where(StockInfo.is_active.is_(True))
        result = await self._session.execute(q)
        rows = result.all()
        if not rows:
            return pd.DataFrame(columns=["name", "list_date", "sw_industry_l1"])
        df = pd.DataFrame(rows, columns=["ts_code", "name", "list_date", "sw_industry_l1"])
        return df.set_index("ts_code")

    # ── daily_quote ────────────────────────────────────────────────────────────

    async def upsert_daily_quotes(self, df: pd.DataFrame) -> int:
        """批量 upsert daily_quote，500 行/批"""
        total = 0
        # 极端值（如 turnover_rate >= 100，理论不该发生但 Tushare 偶发）→ NaN → NULL
        df = _clamp_to_schema_bounds(df, _QUOTE_BOUNDS)
        # Bug 9 修复 v2：dict 层显式 NaN→None；详见 _df_to_dict_with_nulls 注释
        rows = _df_to_dict_with_nulls(df)
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(DailyQuote).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ts_code", "trade_date"],
                set_={col: stmt.excluded[col] for col in _QUOTE_UPDATE_COLS if col in df.columns},
            )
            result = await self._session.execute(stmt)
            total += result.rowcount
        return total

    async def get_latest_quote_date(self) -> date | None:
        """返回 daily_quote 中最新的 trade_date"""
        row = await self._session.scalar(
            select(func.max(DailyQuote.trade_date))
        )
        return row if row else None

    async def get_ingested_quote_dates(self, start_date: date, end_date: date) -> set[date]:
        """返回 [start_date, end_date] 范围内 daily_quote 已有数据的交易日集合。

        供 ingest_history 断点续传使用：只跳过实际已入库的日期，避免用 MAX(trade_date)
        覆盖导致中间失败日期被误判为"已完成"。
        """
        result = await self._session.execute(
            select(DailyQuote.trade_date)
            .distinct()
            .where(
                DailyQuote.trade_date >= start_date,
                DailyQuote.trade_date <= end_date,
            )
        )
        return {row[0] for row in result.all()}

    async def get_fully_ingested_dates(self, start_date: date, end_date: date) -> set[date]:
        """返回 daily_quote ∩ financial_data 两表均有数据的交易日集合。

        Bug 6 修复：原 get_ingested_quote_dates 只查 daily_quote。当 SQLAlchemy savepoint
        让 daily_quote 提交而 financial_data 失败时（Bug 5），下次重跑 resume 会误判已完成
        而跳过补拉。本方法要求两表都有数据才算"已完成"。
        """
        quote_dates = await self.get_ingested_quote_dates(start_date, end_date)
        fin_result = await self._session.execute(
            select(FinancialData.publish_date)
            .distinct()
            .where(
                FinancialData.publish_date >= start_date,
                FinancialData.publish_date <= end_date,
            )
        )
        fin_dates = {row[0] for row in fin_result.all()}
        return quote_dates & fin_dates

    async def get_daily_quotes(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """查询单只股票日线序列（含 close, adj_factor），用于复权计算"""
        result = await self._session.execute(
            select(DailyQuote)
            .where(
                DailyQuote.ts_code == ts_code,
                DailyQuote.trade_date >= start_date,
                DailyQuote.trade_date <= end_date,
            )
            .order_by(DailyQuote.trade_date)
        )
        rows = result.scalars().all()
        if not rows:
            return pd.DataFrame(columns=["trade_date", "close", "adj_factor"])
        return pd.DataFrame(
            [
                {"trade_date": r.trade_date, "close": r.close, "adj_factor": r.adj_factor}
                for r in rows
            ]
        )

    async def get_close_matrix(
        self, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """V1.5-A A3：返回 [start_date, end_date] 全市场 close 宽表（index=trade_date
        升序，columns=ts_code）。供 NH-NL 市场宽度计算（60 日新高/新低）。

        约 60 交易日 × ~5000 股 = ~30 万行；仅取 trade_date/ts_code/close 三列。
        无数据 → 空 DataFrame。
        """
        result = await self._session.execute(
            select(DailyQuote.trade_date, DailyQuote.ts_code, DailyQuote.close)
            .where(
                DailyQuote.trade_date >= start_date,
                DailyQuote.trade_date <= end_date,
            )
        )
        rows = result.all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "ts_code", "close"])
        df["close"] = df["close"].astype(float)
        return df.pivot(index="trade_date", columns="ts_code", values="close").sort_index()

    async def get_snapshot_quotes(
        self, ts_codes: list[str], trade_date: date
    ) -> pd.DataFrame:
        """返回指定日期多只股票的行情快照（index=ts_code），含 StockInfo 字段。

        结果列：close, adj_factor, amount, vol, limit_up, is_st, is_suspended,
                list_date, sw_industry_l1, name
        """
        if not ts_codes:
            return pd.DataFrame()
        result = await self._session.execute(
            select(
                DailyQuote.ts_code,
                DailyQuote.close,
                DailyQuote.adj_factor,
                DailyQuote.amount,
                DailyQuote.vol,
                DailyQuote.limit_up,
                DailyQuote.is_st,
                DailyQuote.is_suspended,
                StockInfo.list_date,
                StockInfo.sw_industry_l1,
                StockInfo.name,
            )
            .outerjoin(StockInfo, StockInfo.ts_code == DailyQuote.ts_code)
            .where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date == trade_date,
            )
        )
        rows = result.all()
        if not rows:
            return pd.DataFrame()
        cols = ["ts_code", "close", "adj_factor", "amount", "vol", "limit_up",
                "is_st", "is_suspended", "list_date", "sw_industry_l1", "name"]
        df = pd.DataFrame(rows, columns=cols)
        return df.set_index("ts_code")

    async def get_market_cap_pit(
        self, ts_codes: list[str], trade_date: date
    ) -> pd.Series:
        """Phase 11 §3.0 P0-3：返回 trade_date PIT 切片的 float_mkt_cap（流通市值）。

        优先取 trade_date 当日；若当日缺失则取 trade_date 前最近一日（最多回看 7 个
        交易日）。返回 Series：index=ts_code，values=float_mkt_cap（元，pandas float64）。
        FactorPipeline.neutralize 在管线内取 ``np.log`` 入回归。
        """
        if not ts_codes:
            return pd.Series(dtype=float)
        # 先尝试当日精确切片
        result = await self._session.execute(
            select(DailyQuote.ts_code, DailyQuote.float_mkt_cap)
            .where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date == trade_date,
                DailyQuote.float_mkt_cap.isnot(None),
            )
        )
        rows = result.all()
        if rows:
            df = pd.DataFrame(rows, columns=["ts_code", "float_mkt_cap"])
            return df.set_index("ts_code")["float_mkt_cap"].astype(float)
        # Fallback：trade_date 不在交易日（如周末），回看 7 个日历日内最近一行
        from datetime import timedelta as _td
        result = await self._session.execute(
            select(
                DailyQuote.ts_code,
                DailyQuote.trade_date,
                DailyQuote.float_mkt_cap,
            )
            .where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date <= trade_date,
                DailyQuote.trade_date >= trade_date - _td(days=7),
                DailyQuote.float_mkt_cap.isnot(None),
            )
            .order_by(DailyQuote.ts_code, DailyQuote.trade_date.desc())
        )
        rows = result.all()
        if not rows:
            return pd.Series(dtype=float)
        df = pd.DataFrame(rows, columns=["ts_code", "trade_date", "float_mkt_cap"])
        # 每 ts_code 取最近一行
        df = df.drop_duplicates(subset=["ts_code"], keep="first")
        return df.set_index("ts_code")["float_mkt_cap"].astype(float)

    async def get_adj_prices_bulk(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> pd.DataFrame:
        """返回多只股票的后复权收盘价矩阵（index=ts_code，columns=trade_date，升序）。

        adj_close = close * adj_factor
        """
        if not ts_codes:
            return pd.DataFrame()
        result = await self._session.execute(
            select(
                DailyQuote.ts_code, DailyQuote.trade_date,
                DailyQuote.close, DailyQuote.adj_factor,
            )
            .where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date >= start_date,
                DailyQuote.trade_date <= end_date,
            )
            .order_by(DailyQuote.trade_date)
        )
        rows = result.all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["ts_code", "trade_date", "close", "adj_factor"])
        df["adj_close"] = df["close"].astype(float) * df["adj_factor"].astype(float)
        return df.pivot(index="ts_code", columns="trade_date", values="adj_close")

    async def get_pe_pb_history_bulk(
        self, ts_codes: list[str], start_date: date, end_date: date
    ) -> pd.DataFrame:
        """返回多只股票的 PE/PB 历史（MultiIndex(ts_code, trade_date)，columns=pe_ttm/pb）。

        ValueStrategy 用于历史分位计算；trade_date 即 publish_date。
        """
        if not ts_codes:
            return pd.DataFrame()
        result = await self._session.execute(
            select(
                FinancialData.ts_code,
                FinancialData.publish_date,
                FinancialData.pe_ttm,
                FinancialData.pb,
            )
            .where(
                FinancialData.ts_code.in_(ts_codes),
                FinancialData.publish_date >= start_date,
                FinancialData.publish_date <= end_date,
            )
            .order_by(FinancialData.ts_code, FinancialData.publish_date)
        )
        rows = result.all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["ts_code", "trade_date", "pe_ttm", "pb"])
        df = df.set_index(["ts_code", "trade_date"])
        df.index.names = ["ts_code", "trade_date"]
        return df

    # PE/PB 历史分位可下推的列白名单——列名要拼进 SQL（不能走 bind 参数），
    # 不白名单就是注入面。
    _PERCENTILE_COLS = frozenset({"pe_ttm", "pb"})

    async def get_pe_pb_percentile_bulk(
        self,
        current_values: Mapping[str, float],
        start_date: date,
        end_date: date,
        col: str,
    ) -> pd.Series:
        """在 PostgreSQL 内算「当前值在该股自身历史中的分位」，返回 1 - pct_rank。

        ## 为什么下推

        `get_pe_pb_history_bulk` 为算约 3212 个分位数要拉约 **380 万行**
        （5 年窗口 × universe），且 `result.all()` 先把它们实例化成 SQLAlchemy Row
        （内含 Decimal）再转 DataFrame——每日管线内存峰值的主项，
        2026-09-03 生产 OOM 的直接推手。下推后返回约 3212 行，降三个数量级。

        ## 当前值为什么由调用方传入，而不在 SQL 里重算

        当前 pe/pb 出自 `get_latest_financial` 的日频段
        （`DISTINCT ON (ts_code) ORDER BY publish_date DESC`）。若这里另写一份
        「取最新行」的 SQL，两边只要有一丁点出入，分位就会**静默偏移**且无从发现。
        传入之后本方法只负责分位算术一件事，可验证面随之收窄。

        ## 语义必须与 `value._compute_historical_percentile` 逐股相等

        - 严格 `<`（非 `<=`）；等值样本不计入分子
        - 分母 = 窗口内**非 NULL** 条数（`WHERE col IS NOT NULL` 后的 `count(*)`）
        - 返回 `1 - pct_rank`：低估 → 高值 → 横截面 rank 后高分
        - 无历史 / 当前值缺失 → **NaN 而非 0**（0 意味着「算出来就是最贵」）
        由 `tests/integration/test_int_pe_pb_percentile_pushdown.py` 逐股对照钉死。

        Args:
            current_values: ts_code → 当前 pe_ttm/pb。NaN 项跳过（结果为 NaN）。
            start_date/end_date: `publish_date` 闭区间窗口。
            col: ``'pe_ttm'`` 或 ``'pb'``。

        Returns:
            index = `current_values` 的全部 key（缺历史者为 NaN）的 float Series。

        Raises:
            ValueError: `col` 不在白名单内。
        """
        if col not in self._PERCENTILE_COLS:
            raise ValueError(
                f"col 必须是 {sorted(self._PERCENTILE_COLS)} 之一，实得 {col!r}"
            )

        index = pd.Index(list(current_values.keys()), name="ts_code")
        codes: list[str] = []
        vals: list[float] = []
        for ts_code, v in current_values.items():
            if v is None:
                continue
            fv = float(v)
            if math.isnan(fv):
                continue
            codes.append(str(ts_code))
            vals.append(fv)
        if not codes:
            return pd.Series(float("nan"), index=index, dtype=float)

        # unnest(text[], float8[]) 而非 VALUES 列表：**两个数组参数**，与股票数无关，
        # 直接绕开 asyncpg 单 SQL 32767 占位符上限（§4.1），无需分批。
        # col 已过白名单，可安全内插。
        stmt = text(
            f"""
            SELECT f.ts_code AS ts_code,
                   (count(*) FILTER (WHERE f.{col}::float8 < c.v))::float8
                       / count(*)::float8 AS pct_rank
            FROM financial_data f
            JOIN unnest(CAST(:codes AS text[]), CAST(:vals AS float8[]))
                 AS c(ts_code, v)
              ON c.ts_code = f.ts_code
            WHERE f.publish_date >= :start_date
              AND f.publish_date <= :end_date
              AND f.{col} IS NOT NULL
            GROUP BY f.ts_code
            """
        )
        rows = (
            await self._session.execute(
                stmt,
                {
                    "codes": codes, "vals": vals,
                    "start_date": start_date, "end_date": end_date,
                },
            )
        ).all()
        if not rows:
            return pd.Series(float("nan"), index=index, dtype=float)

        pct = pd.Series(
            {str(r.ts_code): float(r.pct_rank) for r in rows}, dtype=float
        )
        return (1.0 - pct).reindex(index)

    async def upsert_factor_panel_stat_bulk(
        self,
        panel_run: str,
        trade_date: date,
        points: Sequence[PanelStatPoint],
        *,
        batch_size: int = _BATCH_SIZE,
    ) -> int:
        """批量 upsert `factor_panel_stat`（V1.5-K K-6 研究数据落点）。

        ⚠️ **必须分批**：本表 11 个业务列，asyncpg 二进制协议 16-bit 占位符上限
        32767 → 单条 SQL 最多 `32767 / 11 = 2978` 行。而面板重跑一次要写
        497 交易日 × 4 策略 × 多因子 × 2 stage × 4 horizon × 2 metric，
        轻易上十万行。这个缺陷**合成小数据测不出来**——100 行、1000 行都能过，
        只有 ≥ 2979 行才触发（§4.1），故回归用例用 3200 行钉死。

        `panel_run` / `trade_date` 是**批次维度**，由本方法统一盖章——
        `PanelStatPoint` 刻意不带它们，避免在计算侧逐点重复填写造成口径漂移。

        冲突处理：命中九元组唯一键则覆盖 `value` / `sample_size`，令整批重跑可重入。

        ⚠️ `value` 的 NaN 必须转 None：pandas/numpy 的 NaN 经 asyncpg 会原样写成
        PostgreSQL NUMERIC 的特殊值 `'NaN'`（**≠ NULL**）→ 下游 `IS NOT NULL`
        误判、数值过滤失效（§4.1 同族陷阱）。

        Returns:
            实际写入（含覆盖）的行数。
        """
        if not points:
            return 0

        rows: list[dict] = []
        for pt in points:
            v = pt.value
            if v is not None and math.isnan(float(v)):
                v = None
            rows.append({
                "panel_run": panel_run,
                "trade_date": trade_date,
                "strategy": pt.strategy,
                "factor": pt.factor,
                "stage": pt.stage,
                "state": pt.state,
                "horizon": int(pt.horizon),
                "metric": pt.metric,
                "bucket": int(pt.bucket),
                "value": None if v is None else float(v),
                "sample_size": int(pt.sample_size),
            })

        written = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = pg_insert(FactorPanelStat).values(batch)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_factor_panel_stat",
                set_={
                    "value": stmt.excluded.value,
                    "sample_size": stmt.excluded.sample_size,
                },
            )
            await self._session.execute(stmt)
            written += len(batch)
        await self._session.flush()
        return written

    # ── financial_data ─────────────────────────────────────────────────────────

    async def upsert_financial_data(self, df: pd.DataFrame) -> int:
        """批量 upsert financial_data，500 行/批（asyncpg 单 SQL 参数上限 32767）。

        ON CONFLICT (ts_code, report_period, publish_date) 使用 COALESCE 保留已有非 NULL 值：
        两次分别 upsert fin_df 和 bal_df 时，后者不会将前者写入的 roe 等字段
        覆盖为 NULL（C-04 修复）。
        """
        total = 0
        # 极端值 → NaN → NULL（Tushare 微盘股 ROE/net_profit_yoy 偶发超 schema 范围）
        df = _clamp_to_schema_bounds(df, _FINANCIAL_BOUNDS)
        _key_cols = [
            c for c in ("ts_code", "report_period", "publish_date") if c in df.columns
        ]
        # ① 冲突键（三者均 NOT NULL）任一为 NULL 的行先丢弃：fina_indicator/balancesheet
        # 全量按区间逐股取时，部分报告期 ann_date 为 NULL（未公告占位行）→ publish_date
        # NULL → 直接 upsert 触发 NotNullViolationError 整批 fail。无 ann_date = 无 PIT
        # 锚点，本就不可入库。日常 ingest 走 fetch_financial_data（publish_date=入库时点，
        # 永不 NULL）不触发，故长期潜伏；2026-08-10 生产 total_equity 回填实证暴露。
        if _key_cols:
            df = df.dropna(subset=_key_cols)
        # ② 批内按冲突键去重（保留 last=修订/最新行）：fina_indicator 对同一
        # (ts_code, report_period, publish_date) 会返回重复行（原始+修订，ann_date 相同）。
        # 若不去重，同一条 INSERT...ON CONFLICT 出现重复约束键 → asyncpg
        # CardinalityViolationError（cannot affect row a second time），整批失败。同样
        # 日常 ingest 单 trade_date 不触发，2026-08-07 生产回填实证暴露。守全部调用方。
        if _key_cols:
            df = df.drop_duplicates(subset=_key_cols, keep="last")
        # Bug 9 修复 v2：dict 层显式 NaN→None；详见 _df_to_dict_with_nulls 注释
        rows = _df_to_dict_with_nulls(df)
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(FinancialData).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ts_code", "report_period", "publish_date"],
                set_={
                    col: func.coalesce(stmt.excluded[col], FinancialData.__table__.c[col])
                    for col in _FINANCIAL_UPDATE_COLS if col in df.columns
                },
            )
            result = await self._session.execute(stmt)
            total += result.rowcount
        return total

    async def get_latest_financial(
        self, ts_codes: list[str], as_of_date: date
    ) -> pd.DataFrame:
        """PIT 查询：日频字段取最新交易日行，报告期基本面走 LOCF（最近有值报告期）。

        返回 index=ts_code，列 = publish_date/pe_ttm/pb/dividend_yield（日频）+
        report_period/roe/net_profit_yoy/revenue_yoy/debt_to_asset/total_equity（基本面）。
        无任何日频行 → 空 DataFrame。

        背景（2026-08 生产实证，方案 A）：financial_data 一行 =(ts_code, report_period,
        publish_date=交易日)。日频快照 `fetch_financial_data` 对未披露报告期每日写一条
        全 NULL 基本面的行（report_period=日历季末）。旧实现 `DISTINCT ON (ts_code)
        ORDER BY publish_date DESC` 永远取当日快照行 → 跨季度末真空期 roe 被 NULL 盖过
        （生产 latest 行 roe 非空仅 2.4% / total_equity 0%）→ 价值因子季节性退化 +
        F-4 净资产过滤 + A5b 前瞻 ROE 覆盖全部失效。改为拆分两段取数：

        - 日频段：最新 publish_date 行的 pe/pb/dividend_yield（PIT 正确，随行情每日刷新）。
        - 基本面段（LOCF）：`GROUP BY (ts_code, report_period) + max(每字段)`。必须 max 聚合
          而非"每期取最新 publish_date 那行"——回填后同一报告期的 roe 落在日频行、
          total_equity 落在 balancesheet 公告日行（不同 publish_date），max 把两处非空值
          并成一条（roe 每期恒定，max=coalesce 语义安全）。`HAVING 有任一非空` 后
          `DISTINCT ON (ts_code) ORDER BY report_period DESC` 取最近有值一期：真空股回落
          上一披露期（A5b 据 forecast.report_period > 本期判定真空）；已披露股取当期。

        回看窗口 `_FUND_LOOKBACK_DAYS` 界定 GROUP BY 扫描量（6.5M 行 / 2GB 机）。
        """
        if not ts_codes:
            return pd.DataFrame()
        # ── 日频段：最新交易日行 → pe/pb/dividend_yield ──────────────────────────
        daily_stmt = (
            select(
                FinancialData.ts_code,
                FinancialData.publish_date,
                FinancialData.pe_ttm,
                FinancialData.pb,
                FinancialData.dividend_yield,
            )
            .distinct(FinancialData.ts_code)
            .where(
                FinancialData.ts_code.in_(ts_codes),
                FinancialData.publish_date <= as_of_date,
            )
            .order_by(FinancialData.ts_code, FinancialData.publish_date.desc())
        )
        # ── 基本面段：最近有值报告期 → roe/yoy/debt/total_equity（LOCF）─────────────
        lookback = as_of_date - timedelta(days=_FUND_LOOKBACK_DAYS)
        fund_agg = (
            select(
                FinancialData.ts_code,
                FinancialData.report_period,
                func.max(FinancialData.roe).label("roe"),
                func.max(FinancialData.net_profit_yoy).label("net_profit_yoy"),
                func.max(FinancialData.revenue_yoy).label("revenue_yoy"),
                func.max(FinancialData.debt_to_asset).label("debt_to_asset"),
                func.max(FinancialData.total_equity).label("total_equity"),
            )
            .where(
                FinancialData.ts_code.in_(ts_codes),
                FinancialData.publish_date <= as_of_date,
                FinancialData.publish_date >= lookback,
            )
            .group_by(FinancialData.ts_code, FinancialData.report_period)
            .having(
                or_(
                    func.max(FinancialData.roe).is_not(None),
                    func.max(FinancialData.net_profit_yoy).is_not(None),
                    func.max(FinancialData.revenue_yoy).is_not(None),
                    func.max(FinancialData.total_equity).is_not(None),
                )
            )
            .subquery()
        )
        fund_stmt = (
            select(
                fund_agg.c.ts_code,
                fund_agg.c.report_period,
                fund_agg.c.roe,
                fund_agg.c.net_profit_yoy,
                fund_agg.c.revenue_yoy,
                fund_agg.c.debt_to_asset,
                fund_agg.c.total_equity,
            )
            .distinct(fund_agg.c.ts_code)
            .order_by(fund_agg.c.ts_code, fund_agg.c.report_period.desc())
        )

        daily_rows = (await self._session.execute(daily_stmt)).all()
        if not daily_rows:
            return pd.DataFrame()
        # RM-17：下游 strategy_service / ValueStrategy 全部 .reindex(universe=ts_code)，
        # 故必须以 ts_code 为索引（否则 reindex 全 NaN → value_score 全 NULL）。
        daily_df = pd.DataFrame(
            daily_rows,
            columns=["ts_code", "publish_date", "pe_ttm", "pb", "dividend_yield"],
        ).set_index("ts_code")

        fund_rows = (await self._session.execute(fund_stmt)).all()
        fund_cols = ["ts_code", "report_period", "roe", "net_profit_yoy",
                     "revenue_yoy", "debt_to_asset", "total_equity"]
        if fund_rows:
            fund_df = pd.DataFrame(fund_rows, columns=fund_cols).set_index("ts_code")
        else:
            fund_df = pd.DataFrame(
                columns=fund_cols[1:], index=pd.Index([], name="ts_code")
            )
        # 日频左连基本面：停报 > lookback 的股基本面列 NaN、report_period NaT（下游降级）
        return daily_df.join(fund_df, how="left")

    # ── financial_forecast（V1.5-A A5 / SDD-EXT-03）────────────────────────────

    async def upsert_financial_forecast(self, df: pd.DataFrame) -> int:
        """批量 upsert financial_forecast，_BATCH_SIZE 行/批（asyncpg 32767 占位符上限）。
        ON CONFLICT (ts_code, report_period, source_type) DO UPDATE。est_net_profit_yoy
        clip 到 Numeric(10,4) 范围防 OutOfRange（换手扭亏股 yoy 可能极端）。"""
        if df.empty:
            return 0
        df = df.copy()
        if "est_net_profit_yoy" in df.columns:
            df["est_net_profit_yoy"] = pd.to_numeric(
                df["est_net_profit_yoy"], errors="coerce"
            ).clip(lower=-999999.0, upper=999999.0)
        rows = _df_to_dict_with_nulls(df)
        total = 0
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(FinancialForecast).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ts_code", "report_period", "source_type"],
                set_={
                    "pre_announce_date": stmt.excluded.pre_announce_date,
                    "est_net_profit": stmt.excluded.est_net_profit,
                    "est_net_profit_yoy": stmt.excluded.est_net_profit_yoy,
                    "data_priority": stmt.excluded.data_priority,
                },
            )
            result = await self._session.execute(stmt)
            total += result.rowcount
        return total

    async def get_latest_forecast(
        self, ts_codes: list[str], as_of_date: date
    ) -> pd.DataFrame:
        """PIT 查询：每股取 ``pre_announce_date <= as_of_date`` 中**报告期最新**、
        同报告期取 data_priority 高者（express 2 > forecast 1）的一行。

        DISTINCT ON (ts_code) ORDER BY ts_code, report_period DESC, data_priority DESC,
        pre_announce_date DESC。返回 index=ts_code；无数据 → 空 DataFrame。供 A5b 前瞻
        ROE 覆盖判定「快报报告期是否晚于最近一期正式财报报告期（真空期）」。
        """
        if not ts_codes:
            return pd.DataFrame()
        stmt = (
            select(
                FinancialForecast.ts_code,
                FinancialForecast.report_period,
                FinancialForecast.pre_announce_date,
                FinancialForecast.est_net_profit,
                FinancialForecast.est_net_profit_yoy,
                FinancialForecast.data_priority,
                FinancialForecast.source_type,
            )
            .distinct(FinancialForecast.ts_code)
            .where(
                FinancialForecast.ts_code.in_(ts_codes),
                FinancialForecast.pre_announce_date <= as_of_date,
            )
            .order_by(
                FinancialForecast.ts_code,
                FinancialForecast.report_period.desc(),
                FinancialForecast.data_priority.desc(),
                FinancialForecast.pre_announce_date.desc(),
            )
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=result.keys()).set_index("ts_code")

    # ── index_history ──────────────────────────────────────────────────────────

    async def upsert_index_history(self, df: pd.DataFrame) -> int:
        """批量 upsert index_history，500 行/批（asyncpg 单 SQL 参数上限 32767）。
        ON CONFLICT (index_code, trade_date) DO UPDATE"""
        total = 0
        # Bug 9 修复 v2：dict 层显式 NaN→None；详见 _df_to_dict_with_nulls 注释
        rows = _df_to_dict_with_nulls(df)
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(IndexHistory).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["index_code", "trade_date"],
                set_={col: stmt.excluded[col] for col in _INDEX_UPDATE_COLS if col in df.columns},
            )
            result = await self._session.execute(stmt)
            total += result.rowcount
        return total

    async def get_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """查询指数历史（含 OHLCV，Phase 3 计算 ADX 使用 high/low）"""
        result = await self._session.execute(
            select(IndexHistory)
            .where(
                IndexHistory.index_code == index_code,
                IndexHistory.trade_date >= start_date,
                IndexHistory.trade_date <= end_date,
            )
            .order_by(IndexHistory.trade_date)
        )
        rows = result.scalars().all()
        if not rows:
            return pd.DataFrame(
                columns=[
                    "index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg",
                ]
            )
        return pd.DataFrame(
            [
                {
                    "index_code": r.index_code,
                    "trade_date": r.trade_date,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "vol": r.vol,
                    "pct_chg": r.pct_chg,
                }
                for r in rows
            ]
        )

    # ── trade_calendar（权威交易日历，数据完整性核验基准）─────────────────────

    async def upsert_trade_calendar(self, rows: list[dict]) -> int:
        """批量 upsert trade_calendar，500 行/批（asyncpg 单 SQL 参数上限 32767）。

        rows: [{"exchange": "SSE", "cal_date": date, "is_open": bool}, ...]
        ON CONFLICT (exchange, cal_date) DO UPDATE is_open + updated_at。
        """
        if not rows:
            return 0
        total = 0
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            stmt = pg_insert(TradeCalendar).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["exchange", "cal_date"],
                set_={"is_open": stmt.excluded.is_open, "updated_at": func.now()},
            )
            result = await self._session.execute(stmt)
            total += result.rowcount
        return total

    async def get_trade_calendar_dates(
        self,
        start: date,
        end: date,
        *,
        only_open: bool = True,
        exchange: str = "SSE",
    ) -> list[date]:
        """查 [start, end] 范围日历日（升序）。only_open=True 仅返回开市日。"""
        stmt = select(TradeCalendar.cal_date).where(
            TradeCalendar.exchange == exchange,
            TradeCalendar.cal_date >= start,
            TradeCalendar.cal_date <= end,
        )
        if only_open:
            stmt = stmt.where(TradeCalendar.is_open.is_(True))
        stmt = stmt.order_by(TradeCalendar.cal_date)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_trade_calendar_coverage(
        self, exchange: str = "SSE"
    ) -> tuple[date, date] | None:
        """返回已落库日历的 (min_cal_date, max_cal_date)；空表返回 None。"""
        result = await self._session.execute(
            select(
                func.min(TradeCalendar.cal_date),
                func.max(TradeCalendar.cal_date),
            ).where(TradeCalendar.exchange == exchange)
        )
        row = result.one()
        if row[0] is None:
            return None
        return (row[0], row[1])

    # ── index_component ────────────────────────────────────────────────────────

    async def upsert_index_components(
        self, index_code: str, trade_date: date, ts_codes: list[str]
    ) -> int:
        """批量 upsert index_component。ON CONFLICT DO NOTHING（幂等）"""
        if not ts_codes:
            return 0
        rows = [
            {"index_code": index_code, "ts_code": code, "trade_date": trade_date}
            for code in ts_codes
        ]
        stmt = pg_insert(IndexComponent).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["index_code", "ts_code", "trade_date"]
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def get_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        """查询指定指数在 trade_date 的成分股列表。

        若该日期无数据，向前回溯最多 30 个交易日。
        """
        result = await self._session.execute(
            select(IndexComponent.ts_code)
            .where(
                IndexComponent.index_code == index_code,
                IndexComponent.trade_date == trade_date,
            )
            .order_by(IndexComponent.ts_code)
        )
        codes = [row[0] for row in result.all()]
        if codes:
            return codes

        # 向前回溯最多 30 天（按日历天数，不精确到交易日）
        from datetime import timedelta

        fallback_start = trade_date - timedelta(days=30)
        result2 = await self._session.execute(
            select(IndexComponent.trade_date)
            .where(
                IndexComponent.index_code == index_code,
                IndexComponent.trade_date >= fallback_start,
                IndexComponent.trade_date < trade_date,
            )
            .order_by(IndexComponent.trade_date.desc())
            .limit(1)
        )
        latest_date = result2.scalar()
        if not latest_date:
            return []

        result3 = await self._session.execute(
            select(IndexComponent.ts_code)
            .where(
                IndexComponent.index_code == index_code,
                IndexComponent.trade_date == latest_date,
            )
            .order_by(IndexComponent.ts_code)
        )
        return [row[0] for row in result3.all()]

    # ── market_state_history ───────────────────────────────────────────────────

    async def upsert_market_state(self, record: MarketStateRecord) -> None:
        """INSERT ... ON CONFLICT (trade_date) DO UPDATE SET all fields"""
        stmt = pg_insert(MarketStateHistory).values(
            trade_date=record.trade_date,
            market_state=str(record.market_state),
            trend_strength=record.trend_strength,
            adx_value=record.adx_value,
            ma20=record.ma20,
            ma60=record.ma60,
            state_changed=record.state_changed,
            description=record.description,
            # V1.5-A A3：市场宽度弱信号（getattr 兼容旧构造的 record 无此属性）
            breadth_weak=getattr(record, "breadth_weak", False),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_date"],
            set_={
                "market_state": stmt.excluded.market_state,
                "trend_strength": stmt.excluded.trend_strength,
                "adx_value": stmt.excluded.adx_value,
                "ma20": stmt.excluded.ma20,
                "ma60": stmt.excluded.ma60,
                "state_changed": stmt.excluded.state_changed,
                "description": stmt.excluded.description,
                "breadth_weak": stmt.excluded.breadth_weak,
            },
        )
        await self._session.execute(stmt)

    async def get_latest_market_state(
        self, before_date: date | None = None
    ) -> MarketStateHistory | None:
        """
        返回最新的 market_state_history 行。
        before_date 不为 None 时，仅返回 trade_date < before_date 的行（用于批量回填）。
        """
        q = select(MarketStateHistory)
        if before_date is not None:
            q = q.where(MarketStateHistory.trade_date < before_date)
        q = q.order_by(MarketStateHistory.trade_date.desc()).limit(1)
        result = await self._session.execute(q)
        return result.scalar_one_or_none()

    async def get_market_state_history(
        self, start_date: date, end_date: date
    ) -> list[MarketStateHistory]:
        """返回 [start_date, end_date] 范围内的历史记录，按 trade_date 升序。"""
        result = await self._session.execute(
            select(MarketStateHistory)
            .where(
                MarketStateHistory.trade_date >= start_date,
                MarketStateHistory.trade_date <= end_date,
            )
            .order_by(MarketStateHistory.trade_date)
        )
        return list(result.scalars().all())

    # ── data status ────────────────────────────────────────────────────────────

    # ── Phase 4：candidate_pool / watchlist / stock_industry ─────────────────

    async def update_stock_industry(self, df: pd.DataFrame) -> int:
        """TD-3：批量更新 stock_info 的申万行业分类字段。
        df 列：ts_code, sw_industry_l1, sw_industry_l2
        返回更新行数。
        """
        if df.empty:
            return 0
        count = 0
        for row in df.itertuples(index=False):
            await self._session.execute(
                select(StockInfo).where(StockInfo.ts_code == row.ts_code).limit(1)
            )
            stmt = (
                pg_insert(StockInfo)
                .values(
                    ts_code=row.ts_code,
                    sw_industry_l1=row.sw_industry_l1,
                    sw_industry_l2=getattr(row, "sw_industry_l2", None),
                )
                .on_conflict_do_update(
                    index_elements=["ts_code"],
                    set_={
                        "sw_industry_l1": pg_insert(StockInfo).excluded.sw_industry_l1,
                        "sw_industry_l2": pg_insert(StockInfo).excluded.sw_industry_l2,
                    },
                )
            )
            await self._session.execute(stmt)
            count += 1
        return count

    async def upsert_candidate_pool(
        self,
        ts_code: str,
        trade_date: date,
        composite_score: float | None,
        trend_score: float | None,
        momentum_score: float | None,
        reversion_score: float | None,
        value_score: float | None,
        market_state: str | None,
        in_pool: bool,
        is_holding: bool,
    ) -> None:
        """按 (ts_code, trade_date) 唯一键 upsert candidate_pool 表。"""
        stmt = pg_insert(CandidatePool).values(
            ts_code=ts_code,
            trade_date=trade_date,
            composite_score=composite_score,
            trend_score=trend_score,
            momentum_score=momentum_score,
            reversion_score=reversion_score,
            value_score=value_score,
            market_state=market_state,
            in_pool=in_pool,
            is_holding=is_holding,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candidate_pool_code_date",
            set_={
                "composite_score": stmt.excluded.composite_score,
                "trend_score": stmt.excluded.trend_score,
                "momentum_score": stmt.excluded.momentum_score,
                "reversion_score": stmt.excluded.reversion_score,
                "value_score": stmt.excluded.value_score,
                "market_state": stmt.excluded.market_state,
                "in_pool": stmt.excluded.in_pool,
                "is_holding": stmt.excluded.is_holding,
            },
        )
        await self._session.execute(stmt)

    async def upsert_candidate_pool_bulk(self, entries: list[dict]) -> None:
        """批量 upsert candidate_pool 表（单次 SQL，事务原子写入，无部分写入风险）。

        Phase 11 §3.4：自动识别条目中的 Phase 11 新列（composite_z /
        composite_pct_in_market / weights_source / hysteresis_status /
        score_breakdown_raw / score_breakdown_residual），存在时一并 upsert，
        缺失时仅写旧 4 列（兼容旧调用方）。
        """
        if not entries:
            return
        stmt = pg_insert(CandidatePool).values(entries)
        set_clause = {
            "composite_score": stmt.excluded.composite_score,
            "trend_score": stmt.excluded.trend_score,
            "momentum_score": stmt.excluded.momentum_score,
            "reversion_score": stmt.excluded.reversion_score,
            "value_score": stmt.excluded.value_score,
            "market_state": stmt.excluded.market_state,
            "in_pool": stmt.excluded.in_pool,
            "is_holding": stmt.excluded.is_holding,
        }
        # Phase 11 新列：仅在任一条目带新键时纳入 SET（避免旧调用方误覆盖为 NULL）
        phase11_cols = [
            "composite_z", "composite_pct_in_market", "weights_source",
            "hysteresis_status", "score_breakdown_raw", "score_breakdown_residual",
        ]
        has_phase11 = any(col in entries[0] for col in phase11_cols)
        if has_phase11:
            for col in phase11_cols:
                set_clause[col] = stmt.excluded[col]
        # Phase 12 §3.1.2 评审 P1-3/P1-4 修订：5 步管线产物 3 列（同样按需纳入）
        phase12_cols = ["factor_winsorized", "factor_neutralized", "factor_orthogonal"]
        has_phase12 = any(col in entries[0] for col in phase12_cols)
        if has_phase12:
            for col in phase12_cols:
                set_clause[col] = stmt.excluded[col]
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candidate_pool_code_date",
            set_=set_clause,
        )
        await self._session.execute(stmt)

    async def get_pool_codes(self, trade_date: date) -> set[str]:
        """返回指定交易日 candidate_pool 中 in_pool=True 的 ts_code 集合。"""
        result = await self._session.execute(
            select(CandidatePool.ts_code).where(
                CandidatePool.trade_date == trade_date,
                CandidatePool.in_pool.is_(True),
            )
        )
        return {row[0] for row in result.all()}

    async def get_existing_candidate_pool_dates(
        self, start_date: date, end_date: date,
    ) -> set[date]:
        """返回 [start_date, end_date] 区间内 candidate_pool 已写入的 trade_date 集合。

        Phase 14 §14-2：仅查 candidate_pool **单表**（与 `get_fully_ingested_dates`
        双表交集语义不同）。回填脚本用此判断已存在日 + 跳过，区分语义：
        - `get_fully_ingested_dates`：daily_quote ∩ financial_data 双表（保护原始数据
          per-day 原子性，见 Bug 6 / RM-18）
        - 本方法：仅 candidate_pool 单表（5 步管线产物，是否已算）

        in_pool 状态不参与过滤——当日全市场跑完 ScoringService 后 in_pool=True/False
        都算"已计算"。
        """
        result = await self._session.execute(
            select(CandidatePool.trade_date).distinct().where(
                CandidatePool.trade_date >= start_date,
                CandidatePool.trade_date <= end_date,
            )
        )
        return {row[0] for row in result.all()}

    async def get_latest_pool_date(self, as_of: date) -> date | None:
        """返回 ≤ as_of 的最新 candidate_pool trade_date（无任何池数据 → None）。

        V1.5-G G-4d-4：止损 Job（15:05）早于每日管线（17:30），当日池尚不存在；
        `evaluate_private_signals` 用此方法回落到最近一次管线产出的评分上下文。
        """
        result = await self._session.execute(
            select(func.max(CandidatePool.trade_date)).where(
                CandidatePool.trade_date <= as_of
            )
        )
        return result.scalar_one_or_none()

    async def get_pool(
        self,
        trade_date: date | None = None,
        in_pool_only: bool = True,
    ) -> list[CandidatePool]:
        """返回候选池条目列表，默认取最新交易日 in_pool=True 的标的。"""
        q = select(CandidatePool)
        if trade_date is not None:
            q = q.where(CandidatePool.trade_date == trade_date)
        if in_pool_only:
            q = q.where(CandidatePool.in_pool.is_(True))
        q = q.order_by(CandidatePool.trade_date.desc(), CandidatePool.composite_score.desc())
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def get_stock_scores(
        self,
        ts_code: str,
        limit: int = 30,
    ) -> list[CandidatePool]:
        """返回指定股票最近 N 个交易日的评分历史（按日期降序）。"""
        result = await self._session.execute(
            select(CandidatePool)
            .where(CandidatePool.ts_code == ts_code)
            .order_by(CandidatePool.trade_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_watchlist(
        self,
        list_type: str | None = None,
    ) -> list[UserWatchlist]:
        """返回黑白名单条目，list_type=None 时返回全部。"""
        q = select(UserWatchlist)
        if list_type is not None:
            q = q.where(UserWatchlist.list_type == list_type)
        q = q.order_by(UserWatchlist.created_at.desc())
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def get_whitelist_codes(self) -> set[str]:
        """返回 WHITELIST 中所有 ts_code 的集合。"""
        result = await self._session.execute(
            select(UserWatchlist.ts_code).where(UserWatchlist.list_type == "WHITELIST")
        )
        return {row[0] for row in result.all()}

    async def get_blacklist_codes(self) -> set[str]:
        """返回 BLACKLIST 中所有 ts_code 的集合。"""
        result = await self._session.execute(
            select(UserWatchlist.ts_code).where(UserWatchlist.list_type == "BLACKLIST")
        )
        return {row[0] for row in result.all()}

    async def add_watchlist(
        self,
        ts_code: str,
        list_type: str,
        note: str = "",
    ) -> UserWatchlist:
        """幂等添加黑白名单条目（ts_code + list_type 唯一约束，重复时返回现有记录）。"""
        stmt = pg_insert(UserWatchlist).values(
            ts_code=ts_code,
            list_type=list_type,
            reason=note or None,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_watchlist_code_type",
            set_={"reason": stmt.excluded.reason},
        ).returning(UserWatchlist)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def remove_watchlist(
        self,
        ts_code: str,
        list_type: str,
    ) -> None:
        """幂等删除黑白名单条目（不存在时静默成功）。"""
        from sqlalchemy import delete as sa_delete
        await self._session.execute(
            sa_delete(UserWatchlist).where(
                UserWatchlist.ts_code == ts_code,
                UserWatchlist.list_type == list_type,
            )
        )

    async def get_data_status(self) -> dict:
        """返回各表数据新鲜度摘要"""
        latest_quote = await self.get_latest_quote_date()
        stock_count = await self._session.scalar(
            select(func.count()).select_from(StockInfo).where(StockInfo.is_active.is_(True))
        ) or 0
        latest_fin = await self._session.scalar(
            select(func.max(FinancialData.publish_date))
        )
        index_codes_result = await self._session.execute(
            select(IndexHistory.index_code).distinct().order_by(IndexHistory.index_code)
        )
        index_codes = [row[0] for row in index_codes_result.all()]
        return {
            "latest_quote_date": latest_quote,
            "stock_count": int(stock_count),
            "index_codes": index_codes,
            "latest_financial_date": latest_fin,
        }

    # ─── P5-PRE-4: 均量与财务历史查询 ──────────────────────────────────────────

    async def get_avg_amount(
        self,
        ts_codes: list[str],
        trade_date: date,
        window: int = 20,
    ) -> pd.DataFrame:
        """返回各股票在 trade_date 之前 window 个自然交易日（不含当日）的日均成交额。
        index=ts_code，columns=['avg_amount']（元）。
        """
        if not ts_codes:
            return pd.DataFrame(
                {"avg_amount": pd.Series(dtype=float)},
                index=pd.Index([], name="ts_code"),
            )
        subq = (
            select(
                DailyQuote.ts_code,
                DailyQuote.amount,
                func.row_number().over(
                    partition_by=DailyQuote.ts_code,
                    order_by=DailyQuote.trade_date.desc(),
                ).label("rn"),
            )
            .where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date < trade_date,
                DailyQuote.amount.is_not(None),
            )
            .subquery()
        )
        stmt = (
            select(
                subq.c.ts_code,
                func.avg(subq.c.amount).label("avg_amount"),
            )
            .where(subq.c.rn <= window)
            .group_by(subq.c.ts_code)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        if not rows:
            return pd.DataFrame(
                {"avg_amount": pd.Series(dtype=float)},
                index=pd.Index([], name="ts_code"),
            )
        df = pd.DataFrame(rows, columns=["ts_code", "avg_amount"])
        return df.set_index("ts_code")

    async def get_latest_n_financials(
        self,
        ts_codes: list[str],
        as_of_date: date,
        n: int = 2,
    ) -> pd.DataFrame:
        """按 PIT 原则返回每只股票最近 n 个**报告期**的财务数据。
        index=(ts_code, report_period)，columns=FinancialData 各字段。

        两级去重（2026-07 生产事故修复）：先按 (ts_code, report_period) 保留
        publish_date 最新的一行，再对不同报告期倒序取前 n。
        必须去重的原因：每日快照 `fetch_financial_data` 对未披露报告期（如 7 月的
        Q2 2026-06-30）每天写一条 publish_date=当日 的全 NULL 行，UNIQUE 含
        publish_date → 同一报告期堆积多条重复行。若直接按 report_period 排序取前 n
        行，重复的同期 NULL 行会占满 n=2 窗口、挤出真实的上一期数据 → F-5 连续亏损
        过滤失效 → 候选池近翻倍 → 生产评分卡死（连续 12 交易日无信号）。
        这些每日行携带合法的当日 pe/pb（get_pe_pb_history_bulk 依赖），不能删，故
        修在此消费端。
        """
        if not ts_codes:
            return pd.DataFrame()
        # 第一级：每个 (ts_code, report_period) 只保留 publish_date 最新一行
        deduped = (
            select(
                FinancialData.ts_code,
                FinancialData.report_period,
                FinancialData.net_profit_yoy,
                FinancialData.roe,
                FinancialData.revenue_yoy,
                FinancialData.debt_to_asset,
                FinancialData.total_equity,
                func.row_number().over(
                    partition_by=(FinancialData.ts_code, FinancialData.report_period),
                    order_by=FinancialData.publish_date.desc(),
                ).label("prn"),
            )
            .where(
                FinancialData.ts_code.in_(ts_codes),
                FinancialData.publish_date <= as_of_date,
            )
            .subquery()
        )
        # 第二级：在去重后的报告期上倒序取前 n 期
        subq = (
            select(
                deduped.c.ts_code,
                deduped.c.report_period,
                deduped.c.net_profit_yoy,
                deduped.c.roe,
                deduped.c.revenue_yoy,
                deduped.c.debt_to_asset,
                deduped.c.total_equity,
                func.row_number().over(
                    partition_by=deduped.c.ts_code,
                    order_by=deduped.c.report_period.desc(),
                ).label("rn"),
            )
            .where(deduped.c.prn == 1)
            .subquery()
        )
        stmt = select(subq).where(subq.c.rn <= n)
        result = await self._session.execute(stmt)
        rows = result.all()
        if not rows:
            return pd.DataFrame()
        cols = ["ts_code", "report_period", "net_profit_yoy", "roe",
                "revenue_yoy", "debt_to_asset", "total_equity", "rn"]
        df = pd.DataFrame(rows, columns=cols)
        df = df.drop(columns=["rn"])
        df = df.set_index(["ts_code", "report_period"])
        return df

    # ─── 信号 CRUD ──────────────────────────────────────────────────────────────

    async def upsert_signals(self, rows: list[dict]) -> list[dict]:
        """批量 upsert signal 表（ON CONFLICT ts_code, trade_date, signal_type）。

        返回每行的 {id, ts_code, signal_type} 字典列表（RETURNING 子句）。
        调用方可用 len(returned) 获取写入数量，并按 (ts_code, signal_type) 查询 signal_id
        以便关联写入 SignalScoreSnapshot（C-02 修复）。

        status 不在 DO UPDATE SET 中（C-03 修复）：
        对于已有 VIEWED/ACTED 的信号，重复 save() 不会将状态重置为 NEW。
        新插入的信号 status 仍由 rows 中的 'status' 字段决定（默认 'NEW'）。
        """
        if not rows:
            return []
        stmt = pg_insert(Signal).values(rows)
        set_clause = {
            "score": stmt.excluded.score,
            "suggested_pct": stmt.excluded.suggested_pct,
            "suggested_price_low": stmt.excluded.suggested_price_low,
            "suggested_price_high": stmt.excluded.suggested_price_high,
            "stop_loss_price": stmt.excluded.stop_loss_price,
            "signal_strength": stmt.excluded.signal_strength,
            "liquidity_note": stmt.excluded.liquidity_note,
            "t1_warning": stmt.excluded.t1_warning,
            "reason": stmt.excluded.reason,
            # status 不更新（C-03）：保留用户已操作的 VIEWED/ACTED 状态
        }
        # Phase 11 §5：新列存在时一并 upsert（旧调用方不传时仅写旧列，保持兼容）
        phase11_cols = ["composite_z", "composite_pct_in_market", "trigger_reason"]
        has_phase11 = any(col in rows[0] for col in phase11_cols)
        if has_phase11:
            for col in phase11_cols:
                set_clause[col] = stmt.excluded[col]
        stmt = stmt.on_conflict_do_update(
            constraint="uq_signal_code_date_type",
            set_=set_clause,
        ).returning(Signal.id, Signal.ts_code, Signal.signal_type)
        result = await self._session.execute(stmt)
        returned = result.all()
        return [{"id": r.id, "ts_code": r.ts_code, "signal_type": r.signal_type} for r in returned]

    async def get_signals_by_date(
        self,
        trade_date: date,
        signal_type: str | None = None,
        status: str | None = None,
    ) -> list[Signal]:
        """查询指定交易日的信号列表，支持按类型/状态过滤。"""
        q = select(Signal).where(Signal.trade_date == trade_date)
        if signal_type:
            q = q.where(Signal.signal_type == signal_type)
        if status:
            q = q.where(Signal.status == status)
        q = q.order_by(nullslast(Signal.score.desc()), Signal.id)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def get_latest_signal_date(self) -> date | None:
        """返回 signal 表中最新的 trade_date（无任何信号时返回 None）。

        用于"最新可用信号"默认视图：信号是收盘后每日一次产出，缺省查字面今天
        在盘中/周末/节假日必然为空，故回退到最近一个有信号的交易日。
        """
        result = await self._session.execute(select(func.max(Signal.trade_date)))
        return result.scalar_one_or_none()

    async def get_signal_history(
        self,
        ts_code: str | None = None,
        signal_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Signal]:
        """查询历史信号（分页），支持多条件过滤。"""
        q = select(Signal)
        if ts_code:
            q = q.where(Signal.ts_code == ts_code)
        if signal_type:
            q = q.where(Signal.signal_type == signal_type)
        if status:
            q = q.where(Signal.status == status)
        q = q.order_by(Signal.trade_date.desc(), Signal.id.desc()).limit(limit).offset(offset)
        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def get_kline_bars(self, ts_code: str, limit: int = 60) -> list[DailyQuote]:
        """查询单股 K 线数据（按日期升序返回最近 limit 条）。"""
        result = await self._session.execute(
            select(DailyQuote)
            .where(DailyQuote.ts_code == ts_code)
            .order_by(DailyQuote.trade_date.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.sort(key=lambda r: r.trade_date)
        return rows

    async def get_signal_by_id(self, signal_id: int) -> Signal | None:
        """按 ID 查询单条信号。"""
        result = await self._session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        return result.scalar_one_or_none()

    async def get_last_buy_signal(
        self,
        ts_code: str,
        as_of_date: date | None = None,
    ) -> Signal | None:
        """按 ts_code 查询最近一条 BUY 信号（Phase 10 §5.5 止损预警）。

        as_of_date：可选上界（包含），未指定则取全历史。
        """
        stmt = (
            select(Signal)
            .where(Signal.ts_code == ts_code, Signal.signal_type == "BUY")
            .order_by(Signal.trade_date.desc(), Signal.id.desc())
            .limit(1)
        )
        if as_of_date is not None:
            stmt = stmt.where(Signal.trade_date <= as_of_date)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_signal_status(self, signal_id: int, status: str) -> Signal | None:
        """更新信号状态字段。"""
        from sqlalchemy import update as sa_update
        await self._session.execute(
            sa_update(Signal).where(Signal.id == signal_id).values(status=status)
        )
        return await self.get_signal_by_id(signal_id)

    async def expire_signals_before(self, cutoff_date: date) -> int:
        """将 (NEW/VIEWED) 状态且 trade_date < cutoff_date 的信号改为 EXPIRED。"""
        from sqlalchemy import update as sa_update
        result = await self._session.execute(
            sa_update(Signal)
            .where(
                Signal.status.in_(["NEW", "VIEWED"]),
                Signal.trade_date < cutoff_date,
            )
            .values(status="EXPIRED")
        )
        return result.rowcount

    async def upsert_signal_snapshots(self, rows: list[dict]) -> int:
        """批量 upsert signal_score_snapshot。"""
        if not rows:
            return 0
        stmt = pg_insert(SignalScoreSnapshot).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["signal_id"],
            set_={
                "composite_score": stmt.excluded.composite_score,
                "trend_score": stmt.excluded.trend_score,
                "reversion_score": stmt.excluded.reversion_score,
                "momentum_score": stmt.excluded.momentum_score,
                "value_score": stmt.excluded.value_score,
                "market_state": stmt.excluded.market_state,
                "score_breakdown": stmt.excluded.score_breakdown,
                "raw_factors": stmt.excluded.raw_factors,
                # Phase 12 §3.1.2（评审 P1-4 修订）：5 步管线 3 列重跑也刷
                "factor_winsorized": stmt.excluded.factor_winsorized,
                "factor_neutralized": stmt.excluded.factor_neutralized,
                "factor_orthogonal": stmt.excluded.factor_orthogonal,
            },
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def get_signal_snapshot(self, signal_id: int) -> SignalScoreSnapshot | None:
        """返回信号对应的评分快照（若存在）。"""
        result = await self._session.execute(
            select(SignalScoreSnapshot).where(SignalScoreSnapshot.signal_id == signal_id)
        )
        return result.scalar_one_or_none()

    async def get_recent_score_snapshots_for_holdings(
        self,
        holding_codes: list[str],
        trade_date: date,
    ) -> list[tuple]:
        """查询给定持仓股每只最近 N 条 SignalScoreSnapshot（按 trade_date desc）。

        供 ``SignalService._compute_holding_signal_states`` Phase 11 §5.2 双重失效
        止损"短期 z 降幅"判定使用：每只持仓股取最新 2 条 ``factor_orthogonal`` +
        ``score_breakdown``，比较核心策略 ``z_orthogonal_normalized`` 差值。

        Args:
            holding_codes: 持仓 ts_code 列表（空则返回空 list）。
            trade_date: 上界 trade_date（含）。

        Returns:
            行元组 ``(ts_code, trade_date, factor_orthogonal, score_breakdown)``，
            按 ``ts_code asc, trade_date desc`` 排序——调用方按 ``ts_code`` group
            后取每组前 2 条做今 / 昨对比。
        """
        if not holding_codes:
            return []
        stmt = (
            select(
                SignalScoreSnapshot.ts_code,
                SignalScoreSnapshot.trade_date,
                SignalScoreSnapshot.factor_orthogonal,
                SignalScoreSnapshot.score_breakdown,
            )
            .where(
                SignalScoreSnapshot.ts_code.in_(holding_codes),
                SignalScoreSnapshot.trade_date <= trade_date,
            )
            .order_by(
                SignalScoreSnapshot.ts_code.asc(),
                SignalScoreSnapshot.trade_date.desc(),
            )
        )
        return list((await self._session.execute(stmt)).all())

    # ─── 持仓/账户基础查询（Phase 6 AccountService 底层依赖） ──────────────────

    async def get_positions_by_account(self, account_id: int) -> list[Position]:
        """返回指定账户的全部持仓。"""
        result = await self._session.execute(
            select(Position).where(Position.account_id == account_id)
        )
        return list(result.scalars().all())

    async def get_all_holding_codes(self) -> set[str]:
        """返回**全体账户**当前持仓（``shares > 0``）的 ts_code 并集。

        供每日管线做候选池的持仓保护用。管线自 V1.5-G 起是账户无关的，
        因此这里取并集而不是某个账户——只要有任何账户持有，该股就必须进入
        当日评分与退出评估的范围。

        **不设数量上限**：``pool_capacity`` 限制的是"评分最高那部分"的规模，
        持仓是语义完全不同的另一部分，额外并入。若将来为了控制池子大小把持仓
        也纳入截断，就会原样复现 2026-08-28 修复的那个缺陷——
        **持仓必须全部可评估**（见 `docs/reviews/algo_framework_audit_2026-08-28.md` §1）。
        """
        result = await self._session.execute(
            select(Position.ts_code).where(Position.shares > 0).distinct()
        )
        return {row[0] for row in result.all()}

    async def get_account_by_id(self, account_id: int) -> Account | None:
        """按 ID 查询账户。"""
        result = await self._session.execute(
            select(Account).where(Account.id == account_id)
        )
        return result.scalar_one_or_none()

    async def get_default_account(self) -> Account | None:
        """返回 id 最小的账户（单账户场景）。"""
        result = await self._session.execute(
            select(Account).order_by(Account.id).limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # V1.5-C C0：日级 IC 产出闭环用（原为 scripts/backfill_daily_ic.py 内私有
    # SQL，下沉为 repo 公共方法，供脚本与生产 Job 共用同一实现）
    # ------------------------------------------------------------------

    async def get_max_daily_quote_date(self) -> date | None:
        """``daily_quote`` 中最大的 trade_date（判定前向窗口是否已有数据）。"""
        result = await self._session.execute(select(func.max(DailyQuote.trade_date)))
        return result.scalar()

    async def get_excluded_codes_for_ic(
        self,
        ts_codes: list[str],
        base_date: date,
        end_date: date,
    ) -> set[str]:
        """base 或 end 日涨跌停 / 停牌的 ts_code（SDD §7.4：剔除异常收益样本）。

        这些标的在窗口两端无法真实成交，其前向收益不代表可实现收益，纳入会污染 IC。
        """
        if not ts_codes:
            return set()
        stmt = (
            select(DailyQuote.ts_code)
            .where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date.in_([base_date, end_date]),
                or_(
                    DailyQuote.limit_up.is_(True),
                    DailyQuote.limit_down.is_(True),
                    DailyQuote.is_suspended.is_(True),
                ),
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return {r[0] for r in result.all()}
