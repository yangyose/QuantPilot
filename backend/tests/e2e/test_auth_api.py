"""AUTH / HEALTH / FMT: E2E 端点测试（V1.5-G G-2 多用户改造）。

login/register/me 改为 DB 背书（AuthService）；e2e 无 DB → 用 dependency_overrides
注入 mock AuthService / mock 当前用户。真 DB 路径见 tests/integration（INT-REG/AUTH/ISO）。
"""
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_auth_service, get_current_user
from quantpilot.core.config import settings
from quantpilot.core.security import create_token, hash_password
from quantpilot.main import app
from quantpilot.models.user import User
from quantpilot.services.auth_service import AuthService, DuplicateUserError
from tests.conftest import TEST_PASSWORD


def _admin_user() -> User:
    u = MagicMock(spec=User)
    u.id = 1
    u.username = settings.admin_username
    u.email = f"{settings.admin_username}@local"
    u.level = "L3"
    u.is_active = True
    u.password_hash = hash_password(TEST_PASSWORD)
    return u


@pytest.fixture
async def login_auth() -> AsyncGenerator[AsyncMock, None]:
    """注入 mock AuthService：get_user_by_username 返回 admin（匹配时）/ None。"""
    admin = _admin_user()

    async def _get_user(username: str) -> User | None:
        return admin if username == settings.admin_username else None

    mock = AsyncMock(spec=AuthService)
    mock.get_user_by_username.side_effect = _get_user
    app.dependency_overrides[get_auth_service] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_auth_service, None)


# ---------------------------------------------------------------------------
# HEALTH-01
# ---------------------------------------------------------------------------

async def test_health(client: AsyncClient):
    """HEALTH-01: GET /health 无需鉴权 → 200"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# AUTH-01~03 : 登录
# ---------------------------------------------------------------------------

async def test_login_success(client: AsyncClient, login_auth: AsyncMock):
    """AUTH-01: 正确凭证 → 200 + access_token + refresh_token"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "access_token" in body["data"]
    assert "refresh_token" in body["data"]


async def test_login_wrong_password(client: AsyncClient, login_auth: AsyncMock):
    """AUTH-02: 错误密码 → 401"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 401


async def test_login_wrong_username(client: AsyncClient, login_auth: AsyncMock):
    """AUTH-03: 不存在的用户名 → 401"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "nonexistent", "password": "whatever"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AUTH-04 : health 端点（无需鉴权，验证 client 正常）
# ---------------------------------------------------------------------------

async def test_health_with_token(client: AsyncClient):
    """AUTH-04: 携带有效 access_token 访问 /health → 200（health 无需鉴权）"""
    token = create_token("access", "1")
    resp = await client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AUTH-05~07 : 受保护路由（conftest 中注入的 /test/protected）
# ---------------------------------------------------------------------------

async def test_protected_with_valid_token(client: AsyncClient):
    """AUTH-05: 有效 access_token → 200，返回 user_id"""
    token = create_token("access", "1")
    resp = await client.get(
        "/test/protected", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"] == 1


async def test_protected_without_token(client: AsyncClient):
    """AUTH-06: 无 token → 401"""
    resp = await client.get("/test/protected")
    assert resp.status_code == 401


async def test_protected_with_tampered_token(client: AsyncClient):
    """AUTH-07: 篡改 token → 401"""
    resp = await client.get(
        "/test/protected", headers={"Authorization": "Bearer tampered.token.xxx"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AUTH-08~10 : refresh
# ---------------------------------------------------------------------------

async def test_refresh_success(client: AsyncClient):
    """AUTH-08: 有效 refresh_token → 新 access_token"""
    refresh_token = create_token("refresh", "1")
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "access_token" in body["data"]


async def test_refresh_with_access_token(client: AsyncClient):
    """AUTH-09: 传入 access_token（类型错误）→ 401"""
    access_token = create_token("access", "1")
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": access_token}
    )
    assert resp.status_code == 401


async def test_refresh_with_invalid_token(client: AsyncClient):
    """AUTH-10: 无效 token → 401"""
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "invalid.token.here"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AUTH-REG-01~04 : 注册（mock AuthService）
# ---------------------------------------------------------------------------

def _override_register(mock: AsyncMock) -> None:
    app.dependency_overrides[get_auth_service] = lambda: mock


async def test_register_success(client: AsyncClient):
    """AUTH-REG-01: 合法注册 → 200，返回 {username,email,level=L1}，不含 token（§4.4）。"""
    new_user = MagicMock(spec=User)
    new_user.username = "alice"
    new_user.email = "alice@example.com"
    new_user.level = "L1"
    mock = AsyncMock(spec=AuthService)
    mock.register.return_value = new_user
    _override_register(mock)
    try:
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "email": "alice@example.com",
                "password": "Str0ngPass",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == {
            "username": "alice", "email": "alice@example.com", "level": "L1"
        }
        assert "access_token" not in body["data"]
    finally:
        app.dependency_overrides.pop(get_auth_service, None)


async def test_register_duplicate_returns_409(client: AsyncClient):
    """AUTH-REG-02: username/email 已注册 → 409。"""
    mock = AsyncMock(spec=AuthService)
    mock.register.side_effect = DuplicateUserError("用户名已被注册")
    _override_register(mock)
    try:
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "admin", "email": "x@example.com", "password": "Str0ngPass"
            },
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == 409
    finally:
        app.dependency_overrides.pop(get_auth_service, None)


async def test_register_weak_password_returns_422(client: AsyncClient):
    """AUTH-REG-03: 弱密码 → 422（schema min_length=8 在路由前拦截）。"""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "email": "bob@example.com", "password": "short"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


async def test_register_missing_field_returns_422(client: AsyncClient):
    """AUTH-REG-04: 缺字段 → 422。"""
    resp = await client.post("/api/v1/auth/register", json={"username": "bob"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# AUTH-ME-01~02 : /auth/me
# ---------------------------------------------------------------------------

async def test_me_success(client: AsyncClient):
    """AUTH-ME-01: 有效 token → 200，返回当前用户 {username,email,level}。"""
    user = _admin_user()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        token = create_token("access", "1")
        resp = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["username"] == settings.admin_username
        assert body["data"]["level"] == "L3"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_me_without_token_returns_401(client: AsyncClient):
    """AUTH-ME-02: 无 token → 401。"""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# FMT-01~03 : 统一响应格式
# ---------------------------------------------------------------------------

async def test_fmt_success_response(client: AsyncClient, login_auth: AsyncMock):
    """FMT-01: 成功响应 body 格式 → {code: 0, data: ..., msg: 'ok'}"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": TEST_PASSWORD},
    )
    body = resp.json()
    assert body["code"] == 0
    assert body["msg"] == "ok"
    assert "data" in body


async def test_fmt_error_response(client: AsyncClient, login_auth: AsyncMock):
    """FMT-02: 4xx 错误 body 格式 → {code: 401, data: null, msg: '...'}"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": "wrong"},
    )
    body = resp.json()
    assert body["code"] == 401
    assert body["data"] is None
    assert isinstance(body["msg"], str)


async def test_fmt_validation_error_response(client: AsyncClient):
    """FMT-03: Pydantic 422 → {code: 422, data: null, msg: '...'} 而非原始 {detail: [...]}"""
    resp = await client.post("/api/v1/auth/login", content=b"")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert body["data"] is None
    assert "detail" not in body
