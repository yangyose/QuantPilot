from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from quantpilot.api.deps import get_current_user, get_market_state_service, get_repo
from quantpilot.data.repository import MarketDataRepository
from quantpilot.schemas.market import (
    MarketStateHistoryResponse,
    MarketStateItem,
    MarketStateResponse,
)
from quantpilot.schemas.scoring import (
    PoolResponse,
    PoolStockItem,
    StockScoreItem,
    StockScoreResponse,
)
from quantpilot.services.market_state_service import MarketStateService

router = APIRouter()


def _record_to_item(record: object) -> MarketStateItem:
    return MarketStateItem(
        trade_date=record.trade_date,
        market_state=str(record.market_state),
        trend_strength=record.trend_strength,
        adx_value=record.adx_value,
        ma20=record.ma20,
        ma60=record.ma60,
        state_changed=record.state_changed,
        description=record.description,
    )


@router.get("/state")
async def get_current_market_state(
    _: str = Depends(get_current_user),
    svc: MarketStateService = Depends(get_market_state_service),
) -> JSONResponse:
    """GET /api/v1/market/state — 查询当前市场状态"""
    record = await svc.get_current_state()
    current = _record_to_item(record) if record is not None else None
    data = MarketStateResponse(current=current)
    return JSONResponse({"code": 0, "data": data.model_dump(mode="json"), "msg": "ok"})


@router.get("/state/history")
async def get_market_state_history(
    start: date,
    end: date,
    _: str = Depends(get_current_user),
    svc: MarketStateService = Depends(get_market_state_service),
) -> JSONResponse:
    """GET /api/v1/market/state/history — 查询历史市场状态"""
    records = await svc.get_state_history(start, end)
    items = [_record_to_item(r) for r in records]
    data = MarketStateHistoryResponse(items=items, total=len(items))
    return JSONResponse({"code": 0, "data": data.model_dump(mode="json"), "msg": "ok"})


_ALLOWED_SORT_FIELDS = frozenset({
    "composite_score", "trend_score", "momentum_score", "reversion_score", "value_score",
    # Phase 11 §9.1：支持按分位排序（rank ascending 等价于按 z desc）
    "composite_z", "composite_pct_in_market",
})


@router.get("/pool")
async def get_candidate_pool(
    trade_date: date | None = Query(default=None),
    in_pool_only: bool = Query(default=True),
    sort_by: str = Query(default="composite_score"),
    _: str = Depends(get_current_user),
    repo: MarketDataRepository = Depends(get_repo),
) -> JSONResponse:
    """GET /api/v1/market/pool — 候选股池（最新交易日或指定日期）"""
    # 若未指定日期，取最新有数据的日期
    if trade_date is None:
        trade_date = await repo.get_latest_quote_date()
        if trade_date is None:
            data = PoolResponse(trade_date=date.today(), market_state=None, pool=[], total=0)
            return JSONResponse({"code": 0, "data": data.model_dump(mode="json"), "msg": "ok"})

    pool_records = await repo.get_pool(trade_date=trade_date, in_pool_only=in_pool_only)
    whitelist_codes = await repo.get_whitelist_codes()

    # 尝试获取股票名称
    ts_codes = [r.ts_code for r in pool_records]
    try:
        name_df = await repo.get_stock_info_bulk(ts_codes=ts_codes)
        has_name = not name_df.empty and "name" in name_df.columns
        names: dict[str, str] = name_df["name"].to_dict() if has_name else {}
    except Exception:
        names = {}

    # 按指定字段降序排列（非法字段退回 composite_score；None 排最后）
    sort_field = sort_by if sort_by in _ALLOWED_SORT_FIELDS else "composite_score"
    sorted_records = sorted(
        pool_records,
        key=lambda r: (getattr(r, sort_field) is None, -(getattr(r, sort_field) or 0)),
    )

    pool_items = [
        PoolStockItem(
            rank=i + 1,
            ts_code=r.ts_code,
            name=names.get(r.ts_code),
            composite_score=float(r.composite_score) if r.composite_score is not None else None,
            trend_score=float(r.trend_score) if r.trend_score is not None else None,
            momentum_score=float(r.momentum_score) if r.momentum_score is not None else None,
            reversion_score=float(r.reversion_score) if r.reversion_score is not None else None,
            value_score=float(r.value_score) if r.value_score is not None else None,
            is_holding=r.is_holding,
            is_watchlist=(r.ts_code in whitelist_codes),
            # Phase 11 §9.1：分位主路径三层输出 + 审计字段
            composite_z=(
                float(r.composite_z)
                if getattr(r, "composite_z", None) is not None
                else None
            ),
            composite_pct_in_market=(
                float(r.composite_pct_in_market)
                if getattr(r, "composite_pct_in_market", None) is not None
                else None
            ),
            weights_source=getattr(r, "weights_source", None),
            hysteresis_status=getattr(r, "hysteresis_status", None),
            score_breakdown_raw=getattr(r, "score_breakdown_raw", None),
        )
        for i, r in enumerate(sorted_records)
    ]

    # 市场状态从第一条记录中取（如有）
    market_state_str = sorted_records[0].market_state if sorted_records else None

    data = PoolResponse(
        trade_date=trade_date,
        market_state=market_state_str,
        pool=pool_items,
        total=len(pool_items),
    )
    return JSONResponse({"code": 0, "data": data.model_dump(mode="json"), "msg": "ok"})


@router.get("/stock/{ts_code}/kline")
async def get_stock_kline(
    ts_code: str,
    days: int = Query(default=60, ge=5, le=365),
    _: str = Depends(get_current_user),
    repo: MarketDataRepository = Depends(get_repo),
) -> JSONResponse:
    """GET /api/v1/market/stock/{ts_code}/kline — 单股 K 线数据（OHLCV）"""
    bars = await repo.get_kline_bars(ts_code=ts_code, limit=days)
    data = [
        {
            "date": b.trade_date.isoformat(),
            "open": float(b.open) if b.open is not None else None,
            "high": float(b.high) if b.high is not None else None,
            "low": float(b.low) if b.low is not None else None,
            "close": float(b.close) if b.close is not None else None,
            "vol": b.vol,
        }
        for b in bars
    ]
    return JSONResponse({"code": 0, "data": {"ts_code": ts_code, "bars": data}, "msg": "ok"})


@router.get("/stock/{ts_code}/score")
async def get_stock_score_history(
    ts_code: str,
    days: int = Query(default=30, ge=1, le=365),
    _: str = Depends(get_current_user),
    repo: MarketDataRepository = Depends(get_repo),
) -> JSONResponse:
    """GET /api/v1/market/stock/{ts_code}/score — 单股历史评分走势"""
    records = await repo.get_stock_scores(ts_code=ts_code, limit=days)

    history = [
        StockScoreItem(
            trade_date=r.trade_date,
            composite_score=float(r.composite_score) if r.composite_score is not None else None,
            trend_score=float(r.trend_score) if r.trend_score is not None else None,
            momentum_score=float(r.momentum_score) if r.momentum_score is not None else None,
            reversion_score=float(r.reversion_score) if r.reversion_score is not None else None,
            value_score=float(r.value_score) if r.value_score is not None else None,
            market_state=r.market_state,
        )
        for r in records
    ]

    data = StockScoreResponse(ts_code=ts_code, history=history)
    return JSONResponse({"code": 0, "data": data.model_dump(mode="json"), "msg": "ok"})
