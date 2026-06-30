from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from quantpilot.core.config import settings
from quantpilot.core.exceptions import AuthError

TokenType = Literal["access", "refresh"]


# 常见弱口令黑名单（V1.5-G §4.3 注册防滥用基线）
_WEAK_PASSWORDS = frozenset(
    {"password", "12345678", "123456789", "qwerty", "abc123", "11111111", "iloveyou"}
)


def validate_password_strength(plain: str) -> None:
    """校验密码强度（注册 + 改密）。不达标抛 ValueError（路由转 422）。

    基线：最小长度 8 + 不接受纯数字 + 不接受常见弱口令。
    """
    if len(plain) < 8:
        raise ValueError("密码长度至少 8 位")
    if plain.isdigit():
        raise ValueError("密码不能为纯数字")
    if plain.lower() in _WEAK_PASSWORDS:
        raise ValueError("密码过于常见，请更换")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(token_type: TokenType, subject: str) -> str:
    """签发 JWT。subject = 稳定身份（V1.5-G：str(user_id)，不用 username——
    username 未来可改）。"""
    if token_type == "access":
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.jwt_refresh_token_expire_days
        )
    payload = {"sub": subject, "type": token_type, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> str:
    """返回 subject（V1.5-G：user_id 字符串），验证失败抛出 AuthError"""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        if payload.get("type") != expected_type:
            raise AuthError("token 类型不匹配")
        return payload["sub"]
    except InvalidTokenError as e:
        raise AuthError(str(e))
