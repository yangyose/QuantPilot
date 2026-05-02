"""
SEC: JWT 纯函数单元测试（RED 阶段）
core/security.py 尚未实现，此文件写测试先让它们全部 FAIL。
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from quantpilot.core.config import settings
from quantpilot.core.exceptions import AuthError
from quantpilot.core.security import create_token, decode_token, hash_password, verify_password
from tests.conftest import TEST_PASSWORD

# ---------------------------------------------------------------------------
# SEC-01 / SEC-02 : 密码哈希
# ---------------------------------------------------------------------------

def test_verify_password_correct():
    """SEC-01: 正确明文密码 → True"""
    plain = TEST_PASSWORD
    assert verify_password(plain, hash_password(plain)) is True


def test_verify_password_wrong():
    """SEC-02: 错误明文密码 → False"""
    hashed = hash_password(TEST_PASSWORD)
    assert verify_password("wrong-password", hashed) is False


# ---------------------------------------------------------------------------
# SEC-03 / SEC-04 : 正常 token 签发与解码
# ---------------------------------------------------------------------------

def test_decode_access_token_returns_username():
    """SEC-03: access token 解码返回用户名"""
    token = create_token("access")
    username = decode_token(token, expected_type="access")
    assert username == settings.admin_username


def test_decode_refresh_token_returns_username():
    """SEC-04: refresh token 解码返回用户名"""
    token = create_token("refresh")
    username = decode_token(token, expected_type="refresh")
    assert username == settings.admin_username


# ---------------------------------------------------------------------------
# SEC-05 : token 类型不匹配
# ---------------------------------------------------------------------------

def test_decode_token_type_mismatch():
    """SEC-05: access token 当作 refresh 解码 → AuthError"""
    token = create_token("access")
    with pytest.raises(AuthError):
        decode_token(token, expected_type="refresh")


# ---------------------------------------------------------------------------
# SEC-06 : 篡改 token
# ---------------------------------------------------------------------------

def test_decode_tampered_token():
    """SEC-06: 无效/篡改 token → AuthError"""
    with pytest.raises(AuthError):
        decode_token("tampered.token.xxx", expected_type="access")


# ---------------------------------------------------------------------------
# SEC-07 : 过期 token（直接构造过期 payload）
# ---------------------------------------------------------------------------

def test_decode_expired_token():
    """SEC-07: 过期 token → AuthError"""
    expired_payload = {
        "sub": settings.admin_username,
        "type": "access",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    expired_token = jwt.encode(
        expired_payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(AuthError):
        decode_token(expired_token, expected_type="access")
