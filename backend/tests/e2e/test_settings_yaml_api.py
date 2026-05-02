"""E2E 测试：Phase 10 §6.9 YAML 导出/导入 /settings/export|/settings/import。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import yaml
from httpx import AsyncClient

from quantpilot.api.deps import get_settings_service
from quantpilot.core.security import create_token
from quantpilot.main import app


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_config(config_key: str, config_value: dict, config_id: int = 1) -> object:
    from types import SimpleNamespace
    return SimpleNamespace(
        id=config_id,
        config_key=config_key,
        config_value=config_value,
        user_level="L2",
        description=None,
        updated_at=None,
    )


# ──────────────────────────── export ────────────────────────────


async def test_cfg_exp_01_export_no_auth(client: AsyncClient) -> None:
    """CFG-EXP-01：GET /settings/export 无鉴权 → 401。"""
    resp = await client.get("/api/v1/settings/export")
    assert resp.status_code == 401


async def test_cfg_exp_02_export_returns_yaml(client: AsyncClient) -> None:
    """CFG-EXP-02：GET /settings/export 有鉴权 → 200，text/yaml，YAML 含自定义 key。"""
    mock_svc = AsyncMock()
    mock_svc.get_settings = AsyncMock(return_value=[
        _mock_config("signal_params", {"buy_threshold": 85.0, "sell_threshold": 40.0}, 1),
        _mock_config("risk_limits", {"max_single_stock_pct": 0.2}, 2),
    ])
    app.dependency_overrides[get_settings_service] = lambda: mock_svc
    try:
        resp = await client.get("/api/v1/settings/export", headers=_auth())
        assert resp.status_code == 200
        assert "yaml" in resp.headers["content-type"].lower()
        body = resp.text
        # 含生成时间注释（以 "#" 开头）
        assert body.startswith("#")
        # YAML 可解析
        parsed = yaml.safe_load(body)
        assert isinstance(parsed, dict)
        assert parsed["signal_params"]["buy_threshold"] == 85.0
        assert parsed["risk_limits"]["max_single_stock_pct"] == 0.2
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


# ──────────────────────────── import ────────────────────────────


async def test_cfg_imp_01_import_no_auth(client: AsyncClient) -> None:
    """CFG-IMP-01：POST /settings/import 无鉴权 → 401。"""
    resp = await client.post(
        "/api/v1/settings/import",
        json={"yaml_content": "signal_params:\n  buy_threshold: 85\n"},
    )
    assert resp.status_code == 401


async def test_cfg_imp_02_import_valid_applies(client: AsyncClient) -> None:
    """CFG-IMP-02：合法 YAML → 200；每个 key 触发 upsert_setting；返回 changes 列表。"""
    yaml_body = (
        "signal_params:\n"
        "  buy_threshold: 85\n"
        "  sell_threshold: 40\n"
        "risk_limits:\n"
        "  max_single_stock_pct: 0.25\n"
    )

    mock_svc = AsyncMock()
    # 当前 DB 中的旧值
    mock_svc.get_settings = AsyncMock(return_value=[
        _mock_config("signal_params", {"buy_threshold": 80.0, "sell_threshold": 40.0}, 1),
    ])
    mock_svc.upsert_setting = AsyncMock(
        side_effect=lambda config_key, config_value, change_note=None: _mock_config(
            config_key, config_value, 99
        )
    )
    app.dependency_overrides[get_settings_service] = lambda: mock_svc
    try:
        resp = await client.post(
            "/api/v1/settings/import",
            json={"yaml_content": yaml_body, "dry_run": False},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["applied"] is True
        assert data["total_in_yaml"] == 2
        keys = {c["config_key"] for c in data["changes"]}
        assert keys == {"signal_params", "risk_limits"}

        # 动作分类：signal_params 已存在 → update；risk_limits 首次创建 → create
        actions = {c["config_key"]: c["action"] for c in data["changes"]}
        assert actions["signal_params"] == "update"
        assert actions["risk_limits"] == "create"

        # upsert 被调用 2 次（每个 key 一次）
        assert mock_svc.upsert_setting.await_count == 2
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


async def test_cfg_imp_03_dry_run_does_not_apply(client: AsyncClient) -> None:
    """CFG-IMP-03：dry_run=true → 返回 changes，但 upsert_setting 不被调用。"""
    yaml_body = "signal_params:\n  buy_threshold: 90\n"

    mock_svc = AsyncMock()
    mock_svc.get_settings = AsyncMock(return_value=[])
    mock_svc.upsert_setting = AsyncMock()
    app.dependency_overrides[get_settings_service] = lambda: mock_svc
    try:
        resp = await client.post(
            "/api/v1/settings/import",
            json={"yaml_content": yaml_body, "dry_run": True},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["applied"] is False
        assert len(data["changes"]) == 1
        mock_svc.upsert_setting.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


async def test_cfg_imp_04_invalid_yaml_returns_422(client: AsyncClient) -> None:
    """CFG-IMP-04：非法 YAML → 422。"""
    resp = await client.post(
        "/api/v1/settings/import",
        json={"yaml_content": ":::\ninvalid: [unclosed"},
        headers=_auth(),
    )
    assert resp.status_code == 422


async def test_cfg_imp_05_unknown_keys_skipped(client: AsyncClient) -> None:
    """CFG-IMP-05：未知 config_key → 计入 skipped_keys；已知 key 仍被应用。"""
    yaml_body = (
        "signal_params:\n"
        "  buy_threshold: 85\n"
        "unknown_key_xx:\n"
        "  foo: bar\n"
    )

    mock_svc = AsyncMock()
    mock_svc.get_settings = AsyncMock(return_value=[])
    mock_svc.upsert_setting = AsyncMock(
        side_effect=lambda config_key, config_value, change_note=None: _mock_config(
            config_key, config_value, 99
        )
    )
    app.dependency_overrides[get_settings_service] = lambda: mock_svc
    try:
        resp = await client.post(
            "/api/v1/settings/import",
            json={"yaml_content": yaml_body, "dry_run": False},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "unknown_key_xx" in data["skipped_keys"]
        assert len(data["changes"]) == 1
        assert data["changes"][0]["config_key"] == "signal_params"
        # 仅 signal_params upsert
        assert mock_svc.upsert_setting.await_count == 1
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


async def test_cfg_imp_06_non_dict_value_returns_422(client: AsyncClient) -> None:
    """CFG-IMP-06：顶层 YAML 非 dict（如字符串/列表）→ 422。"""
    resp = await client.post(
        "/api/v1/settings/import",
        json={"yaml_content": "- foo\n- bar\n"},
        headers=_auth(),
    )
    assert resp.status_code == 422
