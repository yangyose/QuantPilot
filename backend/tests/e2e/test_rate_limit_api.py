"""RL-01~05: /auth/login + /auth/register 按 IP 限频 e2e（V1.5-G G-2b §4.3）。

全套件默认 limiter.enabled=False（conftest autouse），本文件用局部 fixture 打开
并 reset 计数（memory:// 存储，reset 安全）。不同 IP 桶经 X-Forwarded-For 模拟
（ASGITransport 的 client.host 固定，key_func 优先取代理头）。
"""
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_auth_service
from quantpilot.core.rate_limit import limiter
from quantpilot.main import app
from quantpilot.models.user import User
from quantpilot.services.auth_service import AuthService

_LOGIN_LIMIT = 10   # settings.rate_limit_login = "10/minute"
_REGISTER_LIMIT = 5  # settings.rate_limit_register = "5/hour"


@pytest.fixture
def rate_limited() -> AsyncGenerator[None, None]:
    """本测试内启用限频；前后 reset 防跨测试串桶。"""
    limiter.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()


@pytest.fixture
def mock_auth() -> AsyncGenerator[AsyncMock, None]:
    """mock AuthService：login 查无此人（→401），register 正常返回新用户。"""
    new_user = MagicMock(spec=User)
    new_user.username = "alice"
    new_user.email = "alice@example.com"
    new_user.level = "L1"
    mock = AsyncMock(spec=AuthService)
    mock.get_user_by_username.return_value = None
    mock.register.return_value = new_user
    app.dependency_overrides[get_auth_service] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_auth_service, None)


# ---------------------------------------------------------------------------
# RL-01~02 : login 10/minute
# ---------------------------------------------------------------------------

async def test_login_over_limit_returns_429(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-01: 同一 IP 第 11 次 login → 429（前 10 次正常进入端点 → 401）。"""
    headers = {"X-Forwarded-For": "203.0.113.10"}
    body = {"username": "nobody", "password": "whatever-123"}
    for _ in range(_LOGIN_LIMIT):
        resp = await client.post("/api/v1/auth/login", json=body, headers=headers)
        assert resp.status_code == 401
    resp = await client.post("/api/v1/auth/login", json=body, headers=headers)
    assert resp.status_code == 429


async def test_login_429_response_format(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-02: 429 响应遵守项目统一格式 {code: 429, data: null, msg: ...}。"""
    headers = {"X-Forwarded-For": "203.0.113.11"}
    body = {"username": "nobody", "password": "whatever-123"}
    for _ in range(_LOGIN_LIMIT + 1):
        resp = await client.post("/api/v1/auth/login", json=body, headers=headers)
    assert resp.status_code == 429
    payload = resp.json()
    assert payload["code"] == 429
    assert payload["data"] is None
    assert isinstance(payload["msg"], str) and payload["msg"]


# ---------------------------------------------------------------------------
# RL-03 : register 5/hour
# ---------------------------------------------------------------------------

async def test_register_over_limit_returns_429(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-03: 同一 IP 第 6 次 register → 429（前 5 次正常 → 200）。"""
    headers = {"X-Forwarded-For": "203.0.113.12"}
    for i in range(_REGISTER_LIMIT):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "username": f"alice{i}",
                "email": f"alice{i}@example.com",
                "password": "Str0ngPass",
            },
            headers=headers,
        )
        assert resp.status_code == 200
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice-over",
            "email": "alice-over@example.com",
            "password": "Str0ngPass",
        },
        headers=headers,
    )
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# RL-04 : 不同 IP 独立计数
# ---------------------------------------------------------------------------

async def test_different_ip_has_separate_bucket(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-04: IP-A 打满 login 限额后，IP-B 仍可正常请求（进入端点 → 401）。"""
    body = {"username": "nobody", "password": "whatever-123"}
    headers_a = {"X-Forwarded-For": "203.0.113.13"}
    for _ in range(_LOGIN_LIMIT + 1):
        resp = await client.post("/api/v1/auth/login", json=body, headers=headers_a)
    assert resp.status_code == 429

    headers_b = {"X-Forwarded-For": "203.0.113.14"}
    resp = await client.post("/api/v1/auth/login", json=body, headers=headers_b)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# RL-05 : 默认（未启用 fixture）不限频——保证全套 e2e 不被限频破坏
# ---------------------------------------------------------------------------

async def test_limiter_disabled_by_default(client: AsyncClient, mock_auth: AsyncMock):
    """RL-05: conftest autouse 关闭限频后，连续超限次请求全部 401（不出现 429）。"""
    body = {"username": "nobody", "password": "whatever-123"}
    headers = {"X-Forwarded-For": "203.0.113.15"}
    for _ in range(_LOGIN_LIMIT + 2):
        resp = await client.post("/api/v1/auth/login", json=body, headers=headers)
        assert resp.status_code == 401
