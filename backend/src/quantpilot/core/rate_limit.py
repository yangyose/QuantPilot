"""按 IP 限频（V1.5-G G-2b §4.3）：/auth/login + /auth/register 防暴力破解/批量注册。

- 存储默认 memory://（进程内计数）：生产为单 uvicorn 进程，无需共享后端；
  未来多 worker 时经 RATE_LIMIT_STORAGE_URI 切 redis（limits 库同一 URI 语法）。
- key_func 取真实客户端 IP 的优先链：CF-Connecting-IP（Cloudflare Tunnel 注入，
  不可伪造）> X-Forwarded-For 首项（nginx 注入）> request.client.host（直连兜底）。
  生产拓扑为 Cloudflare Tunnel + nginx 反代，直接用 client.host 会把所有用户
  坍缩进同一个桶（全站共享 5 次注册/小时）。
- 测试：conftest autouse fixture 置 limiter.enabled=False，限频专项 e2e 局部打开。
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse

from quantpilot.core.config import settings


def get_real_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        return cf_ip
    xff = request.headers.get("X-Forwarded-For", "")
    first = xff.split(",")[0].strip()
    if first:
        return first
    if request.client is not None:
        return request.client.host
    return "127.0.0.1"


limiter = Limiter(
    key_func=get_real_client_ip,
    storage_uri=settings.rate_limit_storage_uri or "memory://",
    enabled=settings.rate_limit_enabled,
    # 存储后端故障（如未来切 redis 后 redis 挂掉）时放行请求而非 500——
    # 限频是防滥用护栏，不能反过来把正常登录打挂。
    swallow_errors=True,
)


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """429 响应统一项目格式 {code, data, msg}（默认 handler 返回 {"error": ...}）。"""
    return JSONResponse(
        status_code=429,
        content={"code": 429, "data": None, "msg": "请求过于频繁，请稍后再试"},
    )
