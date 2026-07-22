from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from quantpilot.api.deps import get_auth_service, get_current_user
from quantpilot.core.config import settings
from quantpilot.core.exceptions import AuthError
from quantpilot.core.rate_limit import limiter
from quantpilot.core.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from quantpilot.models.user import User
from quantpilot.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UpdateMeRequest,
    UserMeResponse,
)
from quantpilot.services.auth_service import AuthService, DuplicateUserError

router = APIRouter()

# dummy bcrypt 哈希（真实 salt，永不匹配）：用户不存在时仍跑一次 verify_password
# 保持登录耗时恒定，防「用户名是否存在」经响应时间泄露（CLAUDE.md §7 计时侧信道）。
_DUMMY_HASH = hash_password("dummy-password-never-matches-any-input")


@router.post("/login")
@limiter.limit(settings.rate_limit_login)
async def login(
    request: Request,  # slowapi 装饰器要求端点显式接收 Request（取 IP 分桶）
    body: LoginRequest,
    auth: AuthService = Depends(get_auth_service),
):
    user = await auth.get_user_by_username(body.username.strip())
    # 先执行 bcrypt（约 100ms），再比对结果，避免短路求值暴露用户名枚举侧信道
    password_ok = verify_password(
        body.password, user.password_hash if user is not None else _DUMMY_HASH
    )
    if user is None or not password_ok or not user.is_active:
        return JSONResponse(
            status_code=401,
            content={"code": 401, "data": None, "msg": "用户名或密码错误"},
        )
    subject = str(user.id)
    return {
        "code": 0,
        "data": TokenResponse(
            access_token=create_token("access", subject),
            refresh_token=create_token("refresh", subject),
        ).model_dump(),
        "msg": "ok",
    }


@router.post("/refresh")
async def refresh(body: RefreshRequest):
    try:
        subject = decode_token(body.refresh_token, expected_type="refresh")
    except AuthError as e:
        return JSONResponse(
            status_code=401,
            content={"code": 401, "data": None, "msg": str(e)},
        )
    return {
        "code": 0,
        "data": AccessTokenResponse(
            access_token=create_token("access", subject)
        ).model_dump(),
        "msg": "ok",
    }


@router.post("/register")
@limiter.limit(settings.rate_limit_register)
async def register(
    request: Request,  # slowapi 装饰器要求端点显式接收 Request（取 IP 分桶）
    body: RegisterRequest,
    auth: AuthService = Depends(get_auth_service),
):
    """开放自助注册：建 user(level=L1) + 自动建空账户。

    注册成功不签发 token（§4.4 锁定：前端跳登录页手动登录，为 V1.5-F 邮箱验证留位）。
    """
    try:
        user = await auth.register(body.username, body.email, body.password)
    except ValueError as e:  # 密码强度
        return JSONResponse(
            status_code=422, content={"code": 422, "data": None, "msg": str(e)}
        )
    except DuplicateUserError as e:  # username/email 已注册
        return JSONResponse(
            status_code=409, content={"code": 409, "data": None, "msg": str(e)}
        )
    return {
        "code": 0,
        "data": UserMeResponse(
            username=user.username, email=user.email, level=user.level
        ).model_dump(),
        "msg": "ok",
    }


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "code": 0,
        "data": UserMeResponse(
            username=user.username, email=user.email, level=user.level
        ).model_dump(),
        "msg": "ok",
    }


@router.patch("/me")
async def update_me(
    body: UpdateMeRequest,
    user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
):
    try:
        updated = await auth.update_me(
            user, level=body.level, email=body.email, password=body.password
        )
    except ValueError as e:  # 非法 level / 密码强度
        return JSONResponse(
            status_code=422, content={"code": 422, "data": None, "msg": str(e)}
        )
    except DuplicateUserError as e:
        return JSONResponse(
            status_code=409, content={"code": 409, "data": None, "msg": str(e)}
        )
    return {
        "code": 0,
        "data": UserMeResponse(
            username=updated.username, email=updated.email, level=updated.level
        ).model_dump(),
        "msg": "ok",
    }
