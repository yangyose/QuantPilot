"""E2E 测试：账户管理 /account（ASGI，Mock AccountService）。"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

from quantpilot.api.deps import get_account_service, get_signal_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.account import Account, FundFlow, TradeRecord


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_account(account_id: int = 1, cash: float = 100000.0) -> Account:
    a = MagicMock(spec=Account)
    a.id = account_id
    a.name = "主账户"
    a.account_type = "REAL"
    a.broker = None
    a.total_assets = cash
    a.cash = cash
    a.synced_at = None
    return a


def _mock_trade(trade_id: int = 1) -> TradeRecord:
    t = MagicMock(spec=TradeRecord)
    t.id = trade_id
    t.account_id = 1
    t.ts_code = "000001.SZ"
    t.trade_type = "BUY"
    t.trade_date = date(2026, 4, 10)
    t.price = 10.0
    t.shares = 1000
    t.amount = 10000.0
    t.commission = 25.0
    t.stamp_tax = 0.0
    t.signal_id = None
    t.note = None
    t.created_at = None
    return t


def _mock_flow(flow_id: int = 1, flow_type: str = "DEPOSIT") -> FundFlow:
    f = MagicMock(spec=FundFlow)
    f.id = flow_id
    f.account_id = 1
    f.flow_type = flow_type
    f.amount = 10000.0
    f.trade_date = date(2026, 4, 10)
    f.ts_code = None
    f.related_trade_id = None
    f.note = None
    f.created_at = None
    return f


# ---------------------------------------------------------------------------
# GET /account
# ---------------------------------------------------------------------------

async def test_aapi_01_get_account_no_auth(client: AsyncClient) -> None:
    """GET /account 无鉴权 → 401。"""
    resp = await client.get("/api/v1/account")
    assert resp.status_code == 401


async def test_aapi_02_get_account_ok(client: AsyncClient) -> None:
    """GET /account 有鉴权 → 200，AccountSummary 结构。"""
    mock = AsyncMock()
    mock.get_default_account = AsyncMock(return_value=_mock_account())
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/account", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert "id" in data
        assert "cash" in data
        assert "total_assets" in data
    finally:
        app.dependency_overrides.pop(get_account_service, None)


async def test_aapi_03_get_account_not_found(client: AsyncClient) -> None:
    """GET /account 无账户 → 404。"""
    mock = AsyncMock()
    mock.get_default_account = AsyncMock(return_value=None)
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/account", headers=_auth())
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_account_service, None)


# ---------------------------------------------------------------------------
# POST /account/sync
# ---------------------------------------------------------------------------

async def test_aapi_04_sync_no_auth(client: AsyncClient) -> None:
    """POST /account/sync 无鉴权 → 401。"""
    resp = await client.post("/api/v1/account/sync", params={"account_id": 1})
    assert resp.status_code == 401


async def test_aapi_05_sync_ok(client: AsyncClient) -> None:
    """POST /account/sync 有鉴权 → 200，返回 AccountSummary。"""
    mock = AsyncMock()
    mock.sync_account = AsyncMock(return_value=_mock_account())
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/account/sync", params={"account_id": 1}, headers=_auth()
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
    finally:
        app.dependency_overrides.pop(get_account_service, None)


# ---------------------------------------------------------------------------
# POST /account/trades
# ---------------------------------------------------------------------------

async def test_aapi_06_trades_no_auth(client: AsyncClient) -> None:
    """POST /account/trades 无鉴权 → 401。"""
    resp = await client.post("/api/v1/account/trades", json={
        "account_id": 1, "ts_code": "000001.SZ", "trade_type": "BUY",
        "trade_date": "2026-04-10", "price": 10.0, "shares": 1000,
    })
    assert resp.status_code == 401


async def test_aapi_07_trades_missing_field(client: AsyncClient) -> None:
    """POST /account/trades 缺少必填字段 → 422。"""
    resp = await client.post(
        "/api/v1/account/trades",
        json={"account_id": 1},  # 缺少 ts_code / trade_type / trade_date / price / shares
        headers=_auth(),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "errors" in body


async def test_aapi_08_trades_buy_ok(client: AsyncClient) -> None:
    """POST /account/trades BUY → 200，返回 TradeRecordItem。"""
    mock_svc = AsyncMock()
    mock_svc.record_trade = AsyncMock(return_value=_mock_trade())
    mock_sig = AsyncMock()
    app.dependency_overrides[get_account_service] = lambda: mock_svc
    app.dependency_overrides[get_signal_service] = lambda: mock_sig
    try:
        resp = await client.post(
            "/api/v1/account/trades",
            json={
                "account_id": 1, "ts_code": "000001.SZ", "trade_type": "BUY",
                "trade_date": "2026-04-10", "price": 10.0, "shares": 1000,
            },
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["trade_type"] == "BUY"
        assert data["ts_code"] == "000001.SZ"
    finally:
        app.dependency_overrides.pop(get_account_service, None)
        app.dependency_overrides.pop(get_signal_service, None)


async def test_aapi_09_trades_oversell(client: AsyncClient) -> None:
    """POST /account/trades SELL 超卖 → 400。"""
    mock_svc = AsyncMock()
    mock_svc.record_trade = AsyncMock(
        side_effect=ValueError("超卖：当前持仓 500 股，尝试卖出 1000 股")
    )
    mock_sig = AsyncMock()
    app.dependency_overrides[get_account_service] = lambda: mock_svc
    app.dependency_overrides[get_signal_service] = lambda: mock_sig
    try:
        resp = await client.post(
            "/api/v1/account/trades",
            json={
                "account_id": 1, "ts_code": "000001.SZ", "trade_type": "SELL",
                "trade_date": "2026-04-10", "price": 10.0, "shares": 1000,
            },
            headers=_auth(),
        )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_account_service, None)
        app.dependency_overrides.pop(get_signal_service, None)


# ---------------------------------------------------------------------------
# POST /account/deposit
# ---------------------------------------------------------------------------

async def test_aapi_10_deposit_no_auth(client: AsyncClient) -> None:
    """POST /account/deposit 无鉴权 → 401。"""
    resp = await client.post("/api/v1/account/deposit", json={
        "account_id": 1, "amount": 10000.0, "trade_date": "2026-04-10",
    })
    assert resp.status_code == 401


async def test_aapi_11_deposit_ok(client: AsyncClient) -> None:
    """POST /account/deposit DEPOSIT（无 ts_code） → 200。"""
    mock = AsyncMock()
    mock.deposit = AsyncMock(return_value=_mock_flow(flow_type="DEPOSIT"))
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/account/deposit",
            json={"account_id": 1, "amount": 10000.0, "trade_date": "2026-04-10"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["flow_type"] == "DEPOSIT"
    finally:
        app.dependency_overrides.pop(get_account_service, None)


async def test_aapi_12_deposit_dividend(client: AsyncClient) -> None:
    """POST /account/deposit DIVIDEND（含 ts_code） → 200。"""
    mock = AsyncMock()
    div_flow = _mock_flow(flow_type="DIVIDEND")
    div_flow.ts_code = "000001.SZ"
    mock.record_dividend = AsyncMock(return_value=div_flow)
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/account/deposit",
            json={
                "account_id": 1, "amount": 500.0,
                "trade_date": "2026-04-10", "ts_code": "000001.SZ",
            },
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["flow_type"] == "DIVIDEND"
    finally:
        app.dependency_overrides.pop(get_account_service, None)


# ---------------------------------------------------------------------------
# POST /account/withdraw
# ---------------------------------------------------------------------------

async def test_aapi_13_withdraw_no_auth(client: AsyncClient) -> None:
    """POST /account/withdraw 无鉴权 → 401。"""
    resp = await client.post("/api/v1/account/withdraw", json={
        "account_id": 1, "amount": 5000.0, "trade_date": "2026-04-10",
    })
    assert resp.status_code == 401


async def test_aapi_14_withdraw_ok(client: AsyncClient) -> None:
    """POST /account/withdraw → 200。"""
    mock = AsyncMock()
    mock.withdraw = AsyncMock(return_value=_mock_flow(flow_type="WITHDRAW"))
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/account/withdraw",
            json={"account_id": 1, "amount": 5000.0, "trade_date": "2026-04-10"},
            headers=_auth(),
        )
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_account_service, None)


async def test_aapi_15_withdraw_insufficient_cash(client: AsyncClient) -> None:
    """POST /account/withdraw 现金不足 → 400。"""
    mock = AsyncMock()
    mock.withdraw = AsyncMock(side_effect=ValueError("现金不足：可用 100.00 元，出金 5000.00 元"))
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/account/withdraw",
            json={"account_id": 1, "amount": 5000.0, "trade_date": "2026-04-10"},
            headers=_auth(),
        )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_account_service, None)


# ---------------------------------------------------------------------------
# GET /account/cashflow
# ---------------------------------------------------------------------------

async def test_aapi_16_cashflow_no_auth(client: AsyncClient) -> None:
    """GET /account/cashflow 无鉴权 → 401。"""
    resp = await client.get("/api/v1/account/cashflow", params={"account_id": 1})
    assert resp.status_code == 401


async def test_aapi_17_cashflow_ok(client: AsyncClient) -> None:
    """GET /account/cashflow 有鉴权 → 200，含 items/total 分页结构。"""
    mock = AsyncMock()
    mock.get_cashflow = AsyncMock(return_value=([], 0))
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.get(
            "/api/v1/account/cashflow", params={"account_id": 1}, headers=_auth()
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
    finally:
        app.dependency_overrides.pop(get_account_service, None)


async def test_aapi_18_cashflow_invalid_date(client: AsyncClient) -> None:
    """GET /account/cashflow start_date 非法格式 → 422（FastAPI date 类型自动校验）。"""
    resp = await client.get(
        "/api/v1/account/cashflow",
        params={"account_id": 1, "start_date": "not-a-date"},
        headers=_auth(),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("code") == 422
    assert "errors" in body
