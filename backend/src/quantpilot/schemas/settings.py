"""Pydantic schemas for settings/user-config API（Phase 6）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserConfigItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    config_key: str
    config_value: dict
    user_level: str
    description: str | None
    updated_at: datetime | None


class UserConfigUpdate(BaseModel):
    config_key: str
    config_value: dict
    change_note: str | None = None


class UserConfigHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    config_key: str
    old_value: dict | None
    new_value: dict
    changed_at: datetime | None
    change_note: str | None


class ConfigHistoryResponse(BaseModel):
    items: list[UserConfigHistoryItem]
    total: int


# ─────────────────── Phase 10 §6.9 YAML 导入导出 ───────────────────


class ImportRequest(BaseModel):
    """POST /settings/import 请求体。"""

    yaml_content: str
    dry_run: bool = False


class ImportChange(BaseModel):
    """单条导入变更条目。"""

    config_key: str
    action: str  # "create" | "update" | "noop"
    old_value: dict | None
    new_value: dict


class ImportResponse(BaseModel):
    """POST /settings/import 响应数据。"""

    applied: bool
    total_in_yaml: int
    changes: list[ImportChange]
    skipped_keys: list[str]
