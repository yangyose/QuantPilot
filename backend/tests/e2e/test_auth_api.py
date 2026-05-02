"""
AUTH / HEALTH / FMT: E2E 端点测试（RED 阶段）
api/v1/auth.py 与 main.py 尚未实现，路由返回 404。
"""
from httpx import AsyncClient

from quantpilot.core.config import settings
from quantpilot.core.security import create_token
from tests.conftest import TEST_PASSWORD

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

async def test_login_success(client: AsyncClient):
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


async def test_login_wrong_password(client: AsyncClient):
    """AUTH-02: 错误密码 → 401"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 401


async def test_login_wrong_username(client: AsyncClient):
    """AUTH-03: 错误用户名 → 401"""
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
    token = create_token("access")
    resp = await client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AUTH-05~07 : 受保护路由（conftest 中注入的 /test/protected）
# ---------------------------------------------------------------------------

async def test_protected_with_valid_token(client: AsyncClient):
    """AUTH-05: 有效 access_token → 200"""
    token = create_token("access")
    resp = await client.get(
        "/test/protected", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"] == settings.admin_username


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
    refresh_token = create_token("refresh")
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "access_token" in body["data"]


async def test_refresh_with_access_token(client: AsyncClient):
    """AUTH-09: 传入 access_token（类型错误）→ 401"""
    access_token = create_token("access")
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
# FMT-01~03 : 统一响应格式
# ---------------------------------------------------------------------------

async def test_fmt_success_response(client: AsyncClient):
    """FMT-01: 成功响应 body 格式 → {code: 0, data: ..., msg: 'ok'}"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": TEST_PASSWORD},
    )
    body = resp.json()
    assert body["code"] == 0
    assert body["msg"] == "ok"
    assert "data" in body


async def test_fmt_error_response(client: AsyncClient):
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
