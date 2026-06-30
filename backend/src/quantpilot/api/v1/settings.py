"""REST API：用户配置管理 /settings（Phase 6 + Phase 10 §6.9 YAML 导出/导入）。"""
from __future__ import annotations

from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse

from quantpilot.api.deps import get_config_service, get_current_user_id, get_settings_service
from quantpilot.schemas.settings import (
    ConfigHistoryResponse,
    ImportChange,
    ImportRequest,
    ImportResponse,
    UserConfigHistoryItem,
    UserConfigItem,
    UserConfigUpdate,
)
from quantpilot.services.config_service import ConfigService
from quantpilot.services.settings_service import SettingsService

router = APIRouter()

# Phase 10 §6.9：12 个合法 config_key 白名单（与 ConfigService.get_all_for_snapshot 对齐）
_VALID_CONFIG_KEYS: frozenset[str] = frozenset({
    "signal_params",
    "risk_limits",
    "market_state_params",
    "universe_params",
    "strategy_weights",
    "strategy_params_trend",
    "strategy_params_momentum",
    "strategy_params_mean_reversion",
    "strategy_params_value",
    "backtest_defaults",
    "notification_prefs",
    "factor_monitor_params",
})


@router.get("")
async def get_settings(
    service: SettingsService = Depends(get_settings_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """GET /settings → 获取全部用户配置（V1.0 不过滤 user_level）。"""
    configs = await service.get_settings()
    return {"code": 0, "data": [UserConfigItem.model_validate(c) for c in configs], "msg": "ok"}


@router.put("")
async def update_setting(
    body: UserConfigUpdate,
    service: SettingsService = Depends(get_settings_service),
    cfg: ConfigService = Depends(get_config_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """PUT /settings → 更新单项配置，自动写入变更历史；主动失效 ConfigService 缓存。

    Phase 10 §6.9：仅接受 12 个合法 config_key；未知 key → 400（避免僵尸配置入库）。
    """
    if body.config_key not in _VALID_CONFIG_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"未知 config_key={body.config_key}；合法集合见 GET /settings/export"
            ),
        )
    config = await service.upsert_setting(
        config_key=body.config_key,
        config_value=body.config_value,
        change_note=body.change_note,
    )
    await cfg.invalidate(body.config_key)
    return {"code": 0, "data": UserConfigItem.model_validate(config), "msg": "ok"}


@router.get("/config-history")
async def get_config_history(
    config_key: str | None = None,
    limit: int = 50,
    offset: int = 0,
    service: SettingsService = Depends(get_settings_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """GET /settings/config-history → 配置变更历史（分页 + 按 key 过滤）。"""
    items, total = await service.get_config_history(
        config_key=config_key,
        limit=limit,
        offset=offset,
    )
    return {
        "code": 0,
        "data": ConfigHistoryResponse(
            items=[UserConfigHistoryItem.model_validate(h) for h in items],
            total=total,
        ).model_dump(),
        "msg": "ok",
    }


@router.get("/export", response_class=PlainTextResponse)
async def export_settings(
    service: SettingsService = Depends(get_settings_service),
    _: int = Depends(get_current_user_id),
) -> PlainTextResponse:
    """GET /settings/export → 以 YAML 格式导出所有已设置的 user_config（Phase 10 §6.9）。

    返回 Content-Type `text/yaml`；首行为生成时间注释。未设置的 config_key 不出现在输出中
    （按需再加载默认值时从 ConfigService 侧处理）。
    """
    configs = await service.get_settings()
    payload: dict[str, dict] = {
        c.config_key: c.config_value for c in configs if c.config_key in _VALID_CONFIG_KEYS
    }
    # allow_unicode=True 支持中文；sort_keys=False 保留字典插入顺序
    body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    content = f"# QuantPilot 配置导出  生成时间: {timestamp}\n{body}"
    return PlainTextResponse(content=content, media_type="text/yaml; charset=utf-8")


@router.post("/import")
async def import_settings(
    body: ImportRequest,
    service: SettingsService = Depends(get_settings_service),
    cfg: ConfigService = Depends(get_config_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """POST /settings/import → 上传 YAML 批量 upsert（Phase 10 §6.9）。

    `dry_run=true` 仅返回差异预览，不改库。未知 config_key 计入 `skipped_keys`。
    顶层 YAML 必须为 dict；每个 value 必须为 dict；否则 422。
    """
    try:
        parsed = yaml.safe_load(body.yaml_content)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"YAML 解析失败：{exc}",
        ) from exc

    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="YAML 顶层必须是映射（key: value 结构）",
        )

    # 当前 DB 状态 → key → current_value
    current_configs = await service.get_settings()
    current_map: dict[str, dict] = {c.config_key: c.config_value for c in current_configs}

    changes: list[ImportChange] = []
    skipped: list[str] = []

    for key, new_value in parsed.items():
        if key not in _VALID_CONFIG_KEYS:
            skipped.append(key)
            continue
        if not isinstance(new_value, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"config_key={key} 的值必须为字典，实际为 {type(new_value).__name__}",
            )
        old_value = current_map.get(key)
        if old_value is None:
            action = "create"
        elif old_value == new_value:
            action = "noop"
        else:
            action = "update"
        changes.append(
            ImportChange(
                config_key=key, action=action, old_value=old_value, new_value=new_value,
            )
        )

    # 非 dry_run → 应用非 noop 的变更（经过 upsert_setting 自动写 history）
    if not body.dry_run:
        for chg in changes:
            if chg.action == "noop":
                continue
            await service.upsert_setting(
                config_key=chg.config_key,
                config_value=chg.new_value,
                change_note="YAML 导入",
            )
            await cfg.invalidate(chg.config_key)

    response = ImportResponse(
        applied=not body.dry_run,
        total_in_yaml=len(parsed),
        changes=changes,
        skipped_keys=skipped,
    )
    return {"code": 0, "data": response.model_dump(), "msg": "ok"}


@router.post("/config-history/{history_id}/revert")
async def revert_config(
    history_id: int,
    service: SettingsService = Depends(get_settings_service),
    cfg: ConfigService = Depends(get_config_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """POST /settings/config-history/{id}/revert → 恢复到指定历史的 old_value（变更前状态）。

    old_value 为 None（首次创建记录）→ 400。
    """
    try:
        config = await service.revert_config(history_id)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    await cfg.invalidate(config.config_key)
    return {"code": 0, "data": UserConfigItem.model_validate(config), "msg": "ok"}
