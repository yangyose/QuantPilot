from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd
import tushare as ts

from quantpilot.data.adapters.base import DataSourceAdapter


def _last_quarter_end(d: date) -> date:
    """d 之前（不含 d）最近的季度末日：3/31, 6/30, 9/30, 12/31"""
    quarter_ends = [
        date(d.year - 1, 12, 31),
        date(d.year, 3, 31),
        date(d.year, 6, 30),
        date(d.year, 9, 30),
        date(d.year, 12, 31),
    ]
    result = quarter_ends[0]
    for qe in quarter_ends:
        if qe < d:
            result = qe
        else:
            break
    return result

logger = logging.getLogger(__name__)

# Tushare 限流异常特征词（中文接口文案 + 英文兜底）——用于 TUSHARE_CALLS 埋点区分
# status=rate_limit vs error（V1.5-A A4 / R13-P3-4）。
_RATE_LIMIT_MARKERS = ("每分钟", "每天", "最多访问", "访问频率", "rate limit", "too many")


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m.lower() in msg for m in _RATE_LIMIT_MARKERS)


class TushareAdapter(DataSourceAdapter):
    """Tushare Pro 适配器。

    - 所有 Tushare SDK 调用通过 asyncio.to_thread() 异步化
    - 速率限制：内置 asyncio.Semaphore 控制并发，默认 max_concurrent=3
    - 字段映射：见 phase2_data_pipeline_v1.0.md §5
    """

    def __init__(self, token: str, max_concurrent: int = 3) -> None:
        self._pro = ts.pro_api(token)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # RM-17 性能优化（2026-05-12 真机验收）：fina_indicator 按 (period_str,
        # ts_codes_key) 缓存。ingest_history 60 天窗口里 last_quarter_end 通常
        # 只有 1-2 个值（如 2025-12-31 / 2026-03-31），每天调一次重复 110 批
        # ≈ 5 小时；缓存后整次 ingest_history 只调 1-2 × 110 批 ≈ 5-10 分钟。
        self._fina_cache: dict[str, pd.DataFrame] = {}

    async def _call(self, func: Any, **kwargs: Any) -> pd.DataFrame:
        """受限并发的异步包装器。

        V1.5-A A4（R13-P3-4）：所有 13 个 Tushare 接口的统一入口——在此一处
        埋 ``TUSHARE_CALLS{interface, status}``（success / rate_limit / error），
        覆盖全接口。interface 取被调方法名（``func.__name__``）。
        """
        from quantpilot.core.metrics import TUSHARE_CALLS

        interface = getattr(func, "__name__", "unknown")
        async with self._semaphore:
            try:
                result = await asyncio.to_thread(func, **kwargs)
            except Exception as exc:
                status = "rate_limit" if _is_rate_limit_error(exc) else "error"
                TUSHARE_CALLS.labels(interface=interface, status=status).inc()
                raise
            TUSHARE_CALLS.labels(interface=interface, status="success").inc()
            return result

    @staticmethod
    def _fmt(d: date) -> str:
        """date → Tushare YYYYMMDD 字符串"""
        return d.strftime("%Y%m%d")

    @staticmethod
    def _to_date(s: Any) -> date | None:
        """YYYYMMDD 字符串 → date，空值返回 None"""
        if not s or pd.isna(s):
            return None
        try:
            return pd.to_datetime(str(s), format="%Y%m%d").date()
        except Exception:
            return None

    # ── 股票列表 ──────────────────────────────────────────────────────────────

    async def fetch_stock_list(self) -> pd.DataFrame:
        """合并上市 + 退市股，映射为标准格式"""
        active = await self._call(
            self._pro.stock_basic,
            list_status="L",
            fields="ts_code,name,industry,market,list_date,list_status",
        )
        delisted = await self._call(
            self._pro.stock_basic,
            list_status="D",
            fields="ts_code,name,industry,market,list_date,delist_date,list_status",
        )
        df = pd.concat([active, delisted], ignore_index=True).drop_duplicates("ts_code")

        result = pd.DataFrame(
            {
                "ts_code": df["ts_code"],
                "name": df["name"],
                "market": df.get("market", pd.Series(dtype=str, index=df.index)),
                # Phase 2 占位：使用 Tushare 自有行业分类，非申万 L1/L2。
                # Phase 4 行业中性化前须替换为申万分类（index_classify + stock_industry API）。
                "sw_industry_l1": df["industry"],
                "sw_industry_l2": None,
                "list_date": df["list_date"].apply(self._to_date),
                "delist_date": (
                    df["delist_date"].apply(self._to_date)
                    if "delist_date" in df.columns
                    else None
                ),
                "is_active": df["list_status"] == "L",
            }
        )
        return result.reset_index(drop=True)

    # ── 日线行情 ───────────────────────────────────────────────────────────────

    async def fetch_daily_quotes(
        self,
        trade_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """合并 daily / daily_basic / adj_factor / suspend_d / limit_list_d"""
        date_str = self._fmt(trade_date)

        # 5 个调用无依赖关系，并发执行（受 _semaphore 限制，仍安全）
        daily, basic, adj, suspend, limit = await asyncio.gather(
            self._call(self._pro.daily, trade_date=date_str),
            self._call(self._pro.daily_basic, trade_date=date_str),
            self._call(self._pro.adj_factor, trade_date=date_str),
            self._call(self._pro.suspend_d, suspend_date=date_str),
            self._call(self._pro.limit_list_d, trade_date=date_str),
        )

        # 合并
        df = daily.merge(basic[["ts_code", "turnover_rate", "circ_mv"]], on="ts_code", how="left")
        df = df.merge(adj[["ts_code", "adj_factor"]], on="ts_code", how="left")

        # 停牌标记
        suspended_codes: set[str] = set(suspend["ts_code"].tolist()) if len(suspend) else set()
        df["is_suspended"] = df["ts_code"].isin(suspended_codes)

        # 涨跌停标记
        # Tushare limit_list_d 实际返回列名为 "limit"（值: 'U'=涨停, 'D'=跌停, 'Z'=炸板）
        limit_up_codes: set[str] = set()
        limit_down_codes: set[str] = set()
        if len(limit) and "limit" in limit.columns:
            limit_up_codes = set(limit.loc[limit["limit"] == "U", "ts_code"].tolist())
            limit_down_codes = set(limit.loc[limit["limit"] == "D", "ts_code"].tolist())
        df["limit_up"] = df["ts_code"].isin(limit_up_codes)
        df["limit_down"] = df["ts_code"].isin(limit_down_codes)

        # is_st 默认 False；历史回填时由 DataService.ingest_history 注入 namechange 缓存覆盖
        df["is_st"] = False

        # 单位换算
        df["pct_chg"] = df["pct_chg"] / 100          # % → 小数
        df["vol"] = df["vol"] * 100                   # 手 → 股
        df["amount"] = df["amount"] * 1000            # 千元 → 元
        df["turnover_rate"] = df["turnover_rate"] / 100  # % → 小数
        df["float_mkt_cap"] = df["circ_mv"] * 10_000    # 万元 → 元

        # trade_date → date 类型
        df["trade_date"] = df["trade_date"].apply(self._to_date)

        # 标准列顺序
        cols = [
            "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
            "pct_chg", "vol", "amount", "turnover_rate", "float_mkt_cap",
            "adj_factor", "is_suspended", "is_st", "limit_up", "limit_down",
        ]
        df = df[[c for c in cols if c in df.columns]].reset_index(drop=True)

        # ts_codes 过滤
        if ts_codes is not None:
            df = df[df["ts_code"].isin(ts_codes)].reset_index(drop=True)

        return df

    # ── 财务数据 ───────────────────────────────────────────────────────────────

    async def fetch_financial_data(
        self,
        as_of_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """全市场财务数据快照：daily_basic 提供 pe_ttm/pb（每日全量），
        fina_indicator 提供最近季报的 roe/growth。
        publish_date = as_of_date（入库时点），report_period = 最近季度末。
        """
        date_str = self._fmt(as_of_date)
        period_str = self._fmt(_last_quarter_end(as_of_date))

        # 1. 每日基本面（全市场）：pe_ttm/pb/dv_ttm 来自 daily_basic
        basic = await self._call(
            self._pro.daily_basic,
            trade_date=date_str,
            fields="ts_code,pe_ttm,pb,dv_ttm",
        )

        # 2. 最近季报财务数据：fina_indicator(period=最近季度末, ts_code=批量)
        # RM-17 修复（2026-05-12）：原 period-only 调用 Tushare 不支持，全部走
        # except 分支让 roe/yoy/financial 字段全 NULL，导致 ValueStrategy 价值陷阱
        # 过滤跳过 → 评分退化 → 真机 top 20 全 ST。改按 ts_code 分批传 50 只 + period
        # 组合查询（fina_indicator 支持此组合），50 只/批 × 0.3s sleep ≈ 单日 +33s。
        codes_for_fina: list[str] = (
            ts_codes if ts_codes is not None else basic["ts_code"].dropna().tolist()
        )
        # 按 period_str 缓存——ingest_history 60 天窗口里 period 通常只有 1-2 个值
        # （last_quarter_end），重复 110 批 × 60 天 = 6600 调用降到 110~220。
        # codes 集合按 as_of_date 略有变化（少量上市/退市），下游 merge by ts_code 自动
        # 丢弃多余行；首次构建用全市场 codes，后续日期复用。
        cache_key = period_str
        if cache_key in self._fina_cache:
            fina = self._fina_cache[cache_key]
            logger.info(
                "fina_indicator_cache_hit",
                extra={"period": period_str, "rows": len(fina)},
            )
        else:
            fina_frames: list[pd.DataFrame] = []
            for i in range(0, len(codes_for_fina), 50):
                batch = codes_for_fina[i : i + 50]
                try:
                    df_batch = await self._call(
                        self._pro.fina_indicator,
                        period=period_str,
                        ts_code=",".join(batch),
                        fields="ts_code,end_date,roe,netprofit_yoy,tr_yoy,debt_to_assets",
                    )
                    if df_batch is not None and not df_batch.empty:
                        fina_frames.append(df_batch)
                except Exception:
                    logger.exception(
                        "fina_indicator_batch_failed",
                        extra={"period": period_str, "batch_start": i, "batch_size": len(batch)},
                    )
                if i + 50 < len(codes_for_fina):
                    await asyncio.sleep(0.3)
            if fina_frames:
                fina = pd.concat(fina_frames, ignore_index=True).drop_duplicates(
                    subset=["ts_code", "end_date"], keep="last"
                )
            else:
                fina = pd.DataFrame(
                    columns=["ts_code", "end_date", "roe", "netprofit_yoy",
                             "tr_yoy", "debt_to_assets"]
                )
            self._fina_cache[cache_key] = fina
        # 注：total_equity（总股东权益）来自 balancesheet API（total_hldr_eqy_exc_min_int），
        # 不在 fina_indicator 中；V1.5 接入 fetch_balance_sheet 补充，V1.0 暂存 NaN。

        # basic 为主表（全市场），LEFT JOIN fina（季报可能缺失部分股票）
        fina_cols = ["ts_code", "end_date", "roe", "netprofit_yoy", "tr_yoy", "debt_to_assets"]
        df = basic.merge(fina[fina_cols], on="ts_code", how="left")

        # report_period 缺失时填充最近季度末（保证 NOT NULL 约束）
        df["end_date"] = df["end_date"].fillna(period_str)

        # publish_date = as_of_date（入库时点，非 fina 的 ann_date）
        df["publish_date"] = as_of_date

        # 字段映射 + 单位换算
        df = df.rename(
            columns={
                "end_date": "report_period",
                "netprofit_yoy": "net_profit_yoy",
                "tr_yoy": "revenue_yoy",
                "debt_to_assets": "debt_to_asset",
                "dv_ttm": "dividend_yield",
            }
        )
        df["report_period"] = df["report_period"].apply(self._to_date)
        df["roe"] = df["roe"] / 100
        df["net_profit_yoy"] = df["net_profit_yoy"] / 100
        df["revenue_yoy"] = df["revenue_yoy"] / 100
        df["dividend_yield"] = df["dividend_yield"] / 100
        df["debt_to_asset"] = df["debt_to_asset"] / 100
        # total_equity 需要 balancesheet API（total_hldr_eqy_exc_min_int），
        # fina_indicator 不提供该字段，暂填 NaN，Phase 4 前补充。
        df["total_equity"] = float("nan")

        if ts_codes is not None:
            df = df[df["ts_code"].isin(ts_codes)]

        return df.reset_index(drop=True)

    # ── 指数历史 ───────────────────────────────────────────────────────────────

    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """调用 index_daily，含完整 OHLCV（Phase 3 ADX 计算需要 high/low）"""
        df = await self._call(
            self._pro.index_daily,
            ts_code=index_code,
            start_date=self._fmt(start_date),
            end_date=self._fmt(end_date),
        )
        df = df.rename(columns={"ts_code": "index_code"})
        df["trade_date"] = df["trade_date"].apply(self._to_date)
        df["pct_chg"] = df["pct_chg"] / 100
        if "vol" in df.columns:
            df["vol"] = df["vol"] * 100  # 手 → 股
        cols = ["index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"]
        df = df[[c for c in cols if c in df.columns]]
        return df.sort_values("trade_date").reset_index(drop=True)

    # ── 交易日历 ───────────────────────────────────────────────────────────────

    async def fetch_trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """调用 trade_cal，过滤 is_open==1，返回升序 date 列表"""
        df = await self._call(
            self._pro.trade_cal,
            exchange="SSE",
            start_date=self._fmt(start_date),
            end_date=self._fmt(end_date),
        )
        open_days = df.loc[df["is_open"] == 1, "cal_date"]
        dates = [self._to_date(d) for d in open_days]
        return sorted(d for d in dates if d is not None)

    # ── 指数成分股 ─────────────────────────────────────────────────────────────

    async def fetch_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        """调用 index_weight 返回 PIT 成分股 ts_code 列表（升序）。

        Bug 7b 修复：Tushare index_weight 是月度稀疏接口（仅 rebalance 日有数据），
        直接传任意 trade_date 大概率返回空。本方法用 [trade_date - 60 天, trade_date]
        range query 拿到窗口内所有 snapshot，再选 ≤ trade_date 的最近一次（PIT 正确）。
        """
        start = trade_date - timedelta(days=60)
        df = await self._call(
            self._pro.index_weight,
            index_code=index_code,
            start_date=self._fmt(start),
            end_date=self._fmt(trade_date),
        )
        if df is None or df.empty:
            logger.warning(
                "fetch_index_components_empty",
                extra={"index_code": index_code, "trade_date": str(trade_date)},
            )
            return []
        latest_date = df["trade_date"].max()
        snapshot = df[df["trade_date"] == latest_date]
        return sorted(snapshot["con_code"].dropna().tolist())

    async def fetch_index_components_range(
        self, index_code: str, start_date: date, end_date: date
    ) -> dict[date, list[str]]:
        """一次 API 调用拿到 [start_date, end_date] 内所有成分股 snapshot。

        Bug 7a 修复：供 ingest_history 批量加载用，避免按每个 trade_date 循环（4×N 次调用
        变 4 次）。返回 {snapshot_date: sorted_components} 字典。
        """
        df = await self._call(
            self._pro.index_weight,
            index_code=index_code,
            start_date=self._fmt(start_date),
            end_date=self._fmt(end_date),
        )
        if df is None or df.empty:
            logger.warning(
                "fetch_index_components_range_empty",
                extra={
                    "index_code": index_code,
                    "start": str(start_date),
                    "end": str(end_date),
                },
            )
            return {}
        result: dict[date, list[str]] = {}
        for snap_date_str, group in df.groupby("trade_date"):
            snap_date = self._to_date(snap_date_str)
            if snap_date is None:
                continue
            result[snap_date] = sorted(group["con_code"].dropna().tolist())
        return result

    # ── 历史改名（is_st PIT 还原）─────────────────────────────────────────────

    # ── Phase 4 TD 修复 ───────────────────────────────────────────────────────

    async def fetch_stock_industry(self) -> pd.DataFrame:
        """TD-3：通过 index_classify + index_member 获取申万一级行业分类。

        实现策略：
          1. index_classify(level='L1', src='SW2021') → 31 个 L1 行业
          2. 每个 L1 的 index_code → index_member(is_new='Y') → 当前成分股
          3. 合并构建 ts_code → sw_industry_l1 映射

        注意：Tushare `stock_industry` API 不存在（error 40101），
        `index_classify` + `index_member` 是官方推荐替代路径。
        sw_industry_l2 当前不填充（Phase 4 策略仅使用 L1）。

        输出列：ts_code, sw_industry_l1, sw_industry_l2（全为 None）
        """
        empty = pd.DataFrame(columns=["ts_code", "sw_industry_l1", "sw_industry_l2"])

        l1_df = await self._call(self._pro.index_classify, level="L1", src="SW2021")
        if l1_df is None or l1_df.empty:
            logger.warning("fetch_stock_industry: index_classify(L1) returned empty")
            return empty

        records: list[dict] = []
        for _, row in l1_df.iterrows():
            index_code: str = row["index_code"]
            industry_name: str = row["industry_name"]
            members_df = await self._call(
                self._pro.index_member,
                index_code=index_code,
                is_new="Y",
            )
            if members_df is not None and not members_df.empty:
                for ts_code in members_df["con_code"].dropna():
                    records.append({
                        "ts_code": ts_code,
                        "sw_industry_l1": industry_name,
                        "sw_industry_l2": None,
                    })

        if not records:
            logger.warning("fetch_stock_industry: no members returned from index_member")
            return empty

        df = pd.DataFrame(records)
        return df.drop_duplicates("ts_code").reset_index(drop=True)

    async def fetch_financial_by_stock(
        self,
        ts_codes: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """TD-1：逐股查询 fina_indicator，每批 50 只，批次间 sleep(0.3s)。

        输出列：ts_code, publish_date（公告日 PIT）, report_period（报告期）,
                roe, net_profit_yoy, revenue_yoy, debt_to_asset
        单位换算：Tushare 原始值为 %，除以 100 转换为小数。
        """
        if not ts_codes:
            return pd.DataFrame(columns=[
                "ts_code", "publish_date", "report_period",
                "roe", "net_profit_yoy", "revenue_yoy", "debt_to_asset",
            ])
        batch_size = 50
        frames: list[pd.DataFrame] = []
        for i in range(0, len(ts_codes), batch_size):
            batch = ts_codes[i : i + batch_size]
            df = await self._call(
                self._pro.fina_indicator,
                ts_code=",".join(batch),
                start_date=self._fmt(start_date),
                end_date=self._fmt(end_date),
                fields="ts_code,ann_date,end_date,roe,netprofit_yoy,tr_yoy,debt_to_assets",
            )
            if not df.empty:
                frames.append(df)
            if i + batch_size < len(ts_codes):
                await asyncio.sleep(0.3)

        if not frames:
            return pd.DataFrame(columns=[
                "ts_code", "publish_date", "report_period",
                "roe", "net_profit_yoy", "revenue_yoy", "debt_to_asset",
            ])
        result = pd.concat(frames, ignore_index=True)
        result = result.rename(columns={
            "ann_date": "publish_date",
            "end_date": "report_period",
            "netprofit_yoy": "net_profit_yoy",
            "tr_yoy": "revenue_yoy",
            "debt_to_assets": "debt_to_asset",
        })
        result["publish_date"] = result["publish_date"].apply(self._to_date)
        result["report_period"] = result["report_period"].apply(self._to_date)
        for col in ["roe", "net_profit_yoy", "revenue_yoy", "debt_to_asset"]:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors="coerce") / 100.0
        return result.reset_index(drop=True)

    async def fetch_balance_sheet(
        self,
        ts_codes: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """TD-2：逐股查询 balancesheet，获取 total_equity（总股东权益），每批 50 只。

        输出列：ts_code, publish_date（公告日）, report_period（报告期）, total_equity（元）
        单位换算：Tushare 原始值为万元，乘以 10000 转换为元。
        """
        if not ts_codes:
            return pd.DataFrame(columns=[
                "ts_code", "publish_date", "report_period", "total_equity",
            ])
        batch_size = 50
        frames: list[pd.DataFrame] = []
        for i in range(0, len(ts_codes), batch_size):
            batch = ts_codes[i : i + batch_size]
            df = await self._call(
                self._pro.balancesheet,
                ts_code=",".join(batch),
                start_date=self._fmt(start_date),
                end_date=self._fmt(end_date),
                fields="ts_code,ann_date,end_date,total_hldr_eqy_exc_min_int",
            )
            if not df.empty:
                frames.append(df)
            if i + batch_size < len(ts_codes):
                await asyncio.sleep(0.3)

        if not frames:
            return pd.DataFrame(columns=[
                "ts_code", "publish_date", "report_period", "total_equity",
            ])
        result = pd.concat(frames, ignore_index=True)
        result = result.rename(columns={
            "ann_date": "publish_date",
            "end_date": "report_period",
            "total_hldr_eqy_exc_min_int": "total_equity",
        })
        result["publish_date"] = result["publish_date"].apply(self._to_date)
        result["report_period"] = result["report_period"].apply(self._to_date)
        result["total_equity"] = (
            pd.to_numeric(result["total_equity"], errors="coerce") * 10_000.0  # 万元 → 元
        )
        return result.reset_index(drop=True)

    # ── 历史改名（is_st PIT 还原）─────────────────────────────────────────────

    async def fetch_dividend_data(self, trade_date: date) -> pd.DataFrame:
        """获取指定除权日（ex_date）的分红数据（Phase 7 D-07）。

        仅返回 ex_date == trade_date 的记录（精确日期匹配）。
        输出列：ts_code, ex_date, cash_div（每股现金分红，元）。
        数据源：Tushare `dividend` API（积分需 ≥ 2000）。

        【降级说明】Tushare `dividend` 同时返回 `cash_div`（税后每股）和
        `cash_div_tax`（税前每股）。本实现选用 `cash_div_tax` 作为每股现金分红，
        理由：(a) A 股个税差别化（< 1 月 20% / 1-12 月 10% / ≥ 12 月 0%）取决于
        持仓时长，V1.0 单账户场景无统一公式；(b) `cash_div` 字段是 Tushare 按
        "持有 ≥ 1 年免税" 默认计算的结果，对短线持仓者会高估到账金额。
        恢复条件：V1.5 接入 record_dividend 时按持仓 lot 分批计算 holding_days 实际税率。

        Bug 15 修复（2026-05-12）：旧实现错调 `fina_dividend`（不存在的接口名）
        导致 Tushare 返回 "请指定正确的接口名"；正确接口为 `dividend`。
        """
        ex_date_str = self._fmt(trade_date)
        df = await self._call(
            self._pro.dividend,
            ex_date=ex_date_str,
            fields="ts_code,ex_date,cash_div_tax",
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["ts_code", "ex_date", "cash_div"])

        df = df.rename(columns={"cash_div_tax": "cash_div"})
        df["ex_date"] = df["ex_date"].apply(self._to_date)
        df = df[df["ex_date"] == trade_date].copy()
        df["cash_div"] = pd.to_numeric(df["cash_div"], errors="coerce")
        df = df.dropna(subset=["cash_div"])
        df = df[df["cash_div"] > 0]
        return df[["ts_code", "ex_date", "cash_div"]].reset_index(drop=True)

    async def fetch_namechange(
        self, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """获取 start_date~end_date 范围内全市场历史改名记录。

        用于 ingest_history 回填前构建 ST 状态缓存。
        输出列：ts_code, name, start_date, end_date（end_date=None 表示至今有效）
        """
        df = await self._call(
            self._pro.namechange,
            start_date=self._fmt(start_date),
            end_date=self._fmt(end_date),
            fields="ts_code,name,start_date,end_date",
        )
        if df.empty:
            return df
        df["start_date"] = df["start_date"].apply(self._to_date)
        df["end_date"] = df["end_date"].apply(self._to_date)
        return df
