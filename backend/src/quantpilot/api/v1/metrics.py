"""Phase 13 §4.1 + §4.2.1：Prometheus exposition 端点。

`GET /metrics` 无 JWT 鉴权（生产 nginx 配置内网 IP 白名单限制访问）。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from quantpilot.core.metrics import REGISTRY

router = APIRouter()


@router.get("", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition format（text/plain; version=0.0.4）。"""
    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


__all__ = ["router"]
