"""core/rate_limit.py 单元测试（V1.5-G G-2b §4.3）。

key_func 取真实客户端 IP 的优先链：
CF-Connecting-IP（Cloudflare Tunnel 注入，不可伪造）
> X-Forwarded-For 首项（nginx 注入）
> request.client.host（直连兜底）。

生产拓扑为 Cloudflare Tunnel + nginx 反代，若直接用 client.host 所有用户
会坍缩进同一个限频桶（全站共享 5 次注册/小时）。
"""
from __future__ import annotations

from starlette.requests import Request

from quantpilot.core.rate_limit import get_real_client_ip


def _make_request(
    headers: dict[str, str] | None = None,
    client_host: str = "172.18.0.5",
) -> Request:
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": raw_headers,
        "query_string": b"",
        "client": (client_host, 54321),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


class TestGetRealClientIp:
    def test_cf_connecting_ip_preferred(self):
        request = _make_request(
            headers={
                "CF-Connecting-IP": "203.0.113.7",
                "X-Forwarded-For": "198.51.100.1, 172.18.0.1",
            }
        )
        assert get_real_client_ip(request) == "203.0.113.7"

    def test_xff_first_entry_when_no_cf_header(self):
        request = _make_request(
            headers={"X-Forwarded-For": "198.51.100.1, 172.18.0.1"}
        )
        assert get_real_client_ip(request) == "198.51.100.1"

    def test_xff_single_entry_stripped(self):
        request = _make_request(headers={"X-Forwarded-For": "  198.51.100.9  "})
        assert get_real_client_ip(request) == "198.51.100.9"

    def test_fallback_to_client_host(self):
        request = _make_request(client_host="192.0.2.33")
        assert get_real_client_ip(request) == "192.0.2.33"

    def test_empty_xff_falls_back_to_client_host(self):
        request = _make_request(
            headers={"X-Forwarded-For": ""}, client_host="192.0.2.34"
        )
        assert get_real_client_ip(request) == "192.0.2.34"

    def test_no_client_returns_placeholder(self):
        request = _make_request()
        request.scope["client"] = None
        assert get_real_client_ip(request) == "127.0.0.1"
