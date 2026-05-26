"""E2E-P14-7-04: Phase 14 §14-7 R13-P2-4 验证 API_REQUEST_DURATION
endpoint 标签使用 route template（如 /api/v1/signals/{signal_id}/lineage）
而非 raw URL（如 /api/v1/signals/123/lineage），防止 Prometheus series 基数爆炸。
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from quantpilot.core import metrics
from quantpilot.core.config import settings
from quantpilot.main import app


@pytest.fixture(autouse=True)
def _no_tushare_lifespan(monkeypatch):
    """禁用 lifespan 中的 Tushare/Scheduler 启动，避免 APScheduler 后台 job
    泄漏到后续 e2e 测试。"""
    monkeypatch.setattr(settings, "tushare_token", "")
    yield


def _collect_endpoint_labels(prefix: str) -> set[str]:
    """从 API_REQUEST_DURATION 收集所有以 prefix 开头的 endpoint 标签值。"""
    endpoints: set[str] = set()
    for sample in metrics.API_REQUEST_DURATION.collect():
        for s in sample.samples:
            ep = s.labels.get("endpoint", "")
            if ep.startswith(prefix):
                endpoints.add(ep)
    return endpoints


def test_e2e_p14_7_04_path_param_uses_template_not_raw_url() -> None:
    """Phase 14 §14-7 R13-P2-4：用 path param 路径触发 middleware → endpoint
    标签必须是 route.path 模板（含 `{signal_id}`），不是 raw URL（含具体 id）。
    无鉴权 → 401，但 middleware 仍会在 finally 段记录耗时。
    """
    with TestClient(app) as tc:
        # 用两个不同 signal_id 命中同一模板
        tc.get("/api/v1/signals/11111/lineage")
        tc.get("/api/v1/signals/22222/lineage")

    eps = _collect_endpoint_labels("/api/v1/signals/")
    # 必须有 template 形式的 endpoint
    template = "/api/v1/signals/{signal_id}/lineage"
    assert template in eps, (
        f"期望 endpoint 含 route 模板 {template!r}，实际见到 {eps!r}"
    )
    # 不应该有 raw URL（含具体数字 id）
    for ep in eps:
        assert "11111" not in ep, f"endpoint 不应含 raw signal_id：{ep!r}"
        assert "22222" not in ep, f"endpoint 不应含 raw signal_id：{ep!r}"


def test_e2e_p14_7_04b_unknown_route_falls_back_to_raw_path() -> None:
    """404 路由（route 未匹配）→ fallback 用 raw path 保留可观测性。"""
    with TestClient(app) as tc:
        tc.get("/api/v1/nonexistent-route-xxx-fb04")

    eps = _collect_endpoint_labels("/api/v1/nonexistent-route-xxx-fb04")
    # 404 时 route.path 为 None → fallback 使用 raw path
    assert "/api/v1/nonexistent-route-xxx-fb04" in eps, (
        f"404 路径应通过 fallback 记录 raw path，实际见到 {eps!r}"
    )
