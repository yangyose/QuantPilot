"""INT-SET-01~05: SettingsService 集成测试（需真实 PostgreSQL）。"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.services.settings_service import SettingsService


# ---------------------------------------------------------------------------
# INT-SET-01: upsert（首次创建）→ old_value=None，history 写入
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_set_01_upsert_creates_config(db_session: AsyncSession) -> None:
    """首次 upsert → UserConfig 创建，history.old_value=None。"""
    svc = SettingsService(db_session)
    config = await svc.upsert_setting("buy_threshold", {"value": 80})

    assert config.config_key == "buy_threshold"
    assert config.config_value == {"value": 80}

    histories, total = await svc.get_config_history("buy_threshold")
    assert total == 1
    assert histories[0].old_value is None
    assert histories[0].new_value == {"value": 80}


# ---------------------------------------------------------------------------
# INT-SET-02: upsert（更新）→ old_value 记录前值，config 更新
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_set_02_upsert_updates_config(db_session: AsyncSession) -> None:
    """两次 upsert → 第二次 history.old_value = 第一次的 config_value。"""
    svc = SettingsService(db_session)
    await svc.upsert_setting("sell_threshold", {"value": 60})
    config = await svc.upsert_setting("sell_threshold", {"value": 65}, change_note="调高阈值")

    assert config.config_value == {"value": 65}

    histories, total = await svc.get_config_history("sell_threshold")
    assert total == 2
    # 最新 history 在前（order by changed_at DESC）
    latest = histories[0]
    assert latest.old_value == {"value": 60}
    assert latest.new_value == {"value": 65}
    assert latest.change_note == "调高阈值"


# ---------------------------------------------------------------------------
# INT-SET-03: get_settings 返回所有配置
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_set_03_get_settings_all(db_session: AsyncSession) -> None:
    """get_settings() 返回全部 UserConfig，按 config_key 排序。"""
    svc = SettingsService(db_session)
    await svc.upsert_setting("aaa_key", {"v": 1})
    await svc.upsert_setting("zzz_key", {"v": 2})

    configs = await svc.get_settings()
    keys = [c.config_key for c in configs]
    # 测试用独立事务，本测试内写入的两个 key 应都存在
    assert "aaa_key" in keys
    assert "zzz_key" in keys
    # 验证按字母排序
    idx_a = keys.index("aaa_key")
    idx_z = keys.index("zzz_key")
    assert idx_a < idx_z


# ---------------------------------------------------------------------------
# INT-SET-04: revert_config → 恢复为 old_value
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_set_04_revert_config(db_session: AsyncSession) -> None:
    """revert_config(history_id) → 将 config_value 恢复为 history.old_value。"""
    svc = SettingsService(db_session)
    await svc.upsert_setting("max_position", {"value": 10})
    await svc.upsert_setting("max_position", {"value": 15})

    # 最新 history（第二次写入）的 old_value = {"value": 10}
    histories, _ = await svc.get_config_history("max_position")
    second_history = histories[0]  # 最新在前

    reverted = await svc.revert_config(second_history.id)
    assert reverted.config_value == {"value": 10}


# ---------------------------------------------------------------------------
# INT-SET-05: revert_config 首次创建（old_value=None）→ ValueError
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_set_05_revert_no_old_value_raises(db_session: AsyncSession) -> None:
    """首次创建的 history（old_value=None）调用 revert_config → ValueError。"""
    svc = SettingsService(db_session)
    await svc.upsert_setting("init_key", {"value": 1})

    histories, _ = await svc.get_config_history("init_key")
    first_history = histories[0]  # 只有一条，old_value=None

    with pytest.raises(ValueError, match="old_value=None"):
        await svc.revert_config(first_history.id)
