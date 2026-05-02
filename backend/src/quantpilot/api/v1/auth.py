from fastapi import APIRouter
from fastapi.responses import JSONResponse

from quantpilot.core.config import settings
from quantpilot.core.exceptions import AuthError
from quantpilot.core.security import create_token, decode_token, verify_password
from quantpilot.schemas.auth import AccessTokenResponse, LoginRequest, RefreshRequest, TokenResponse

router = APIRouter()


@router.post("/login")
async def login(body: LoginRequest):
    # 先执行 bcrypt（约 100ms），再比对用户名，避免短路求值暴露用户名枚举侧信道
    password_ok = verify_password(body.password, settings.admin_password_hash)
    username_ok = body.username == settings.admin_username
    if not (username_ok and password_ok):
        return JSONResponse(
            status_code=401,
            content={"code": 401, "data": None, "msg": "用户名或密码错误"},
        )
    access_token = create_token("access")
    refresh_token = create_token("refresh")
    return {
        "code": 0,
        "data": TokenResponse(
            access_token=access_token, refresh_token=refresh_token
        ).model_dump(),
        "msg": "ok",
    }


@router.post("/refresh")
async def refresh(body: RefreshRequest):
    try:
        decode_token(body.refresh_token, expected_type="refresh")
    except AuthError as e:
        return JSONResponse(
            status_code=401,
            content={"code": 401, "data": None, "msg": str(e)},
        )
    access_token = create_token("access")
    return {
        "code": 0,
        "data": AccessTokenResponse(access_token=access_token).model_dump(),
        "msg": "ok",
    }
