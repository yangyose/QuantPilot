from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AuthError(Exception):
    """JWT 认证失败（token 无效、过期、类型不匹配）"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class SignalNotFoundError(ValueError):
    """信号 ID 不存在（HTTP 404）；与非法状态转换（ValueError/HTTP 400）区分。"""


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "data": None, "msg": str(exc.detail)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 覆盖 FastAPI 默认的 {"detail": [...]} 格式，统一为 {"code", "data", "msg"}
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "data": None,
                "msg": "请求参数校验失败",
                "errors": [
                    {"field": ".".join(str(loc_part) for loc_part in e["loc"]), "reason": e["msg"]}
                    for e in exc.errors()
                ],
            },
        )
