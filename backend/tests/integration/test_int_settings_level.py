"""V1.5-G G-4a 集成测试：/settings 按 user.level 过滤（§6.3，需真实 PostgreSQL）。

INT-LVL-01：L1 用户仅见 L1 配置项（如 notification_prefs），看不到 L2/L3 高级参数；
L3 用户见全部。过滤依据 CONFIG_KEY_LEVEL 代码真源，非 DB user_level 列。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.system import UserConfig
from quantpilot.services.settings_service import SettingsService


async def _seed_configs(session: AsyncSession) -> None:
    """种三档配置：L1(notification_prefs) / L2(signal_params) / L3(strategy_weights)。"""
    session.add_all([
        UserConfig(config_key="notification_prefs", config_value={"wx_enabled": True}),
        UserConfig(config_key="signal_params", config_value={"buy_threshold": 80}),
        UserConfig(config_key="strategy_weights", config_value={"trend": 0.5}),
    ])
    await session.flush()


async def test_int_lvl_01_l1_sees_only_l1_configs(db_session: AsyncSession) -> None:
    """L1 用户只见 notification_prefs（L1）。"""
    await _seed_configs(db_session)
    svc = SettingsService(db_session)

    keys = {c.config_key for c in await svc.get_settings(max_level="L1")}
    assert keys == {"notification_prefs"}


async def test_int_lvl_01_l2_sees_l1_l2(db_session: AsyncSession) -> None:
    """L2 用户见 L1 + L2，不见 L3（strategy_weights）。"""
    await _seed_configs(db_session)
    svc = SettingsService(db_session)

    keys = {c.config_key for c in await svc.get_settings(max_level="L2")}
    assert keys == {"notification_prefs", "signal_params"}


async def test_int_lvl_01_l3_sees_all(db_session: AsyncSession) -> None:
    """L3 用户见全部三项。"""
    await _seed_configs(db_session)
    svc = SettingsService(db_session)

    keys = {c.config_key for c in await svc.get_settings(max_level="L3")}
    assert keys == {"notification_prefs", "signal_params", "strategy_weights"}


async def test_int_lvl_01_no_max_level_returns_all(db_session: AsyncSession) -> None:
    """max_level=None（如 export 备份）不过滤，返回全部。"""
    await _seed_configs(db_session)
    svc = SettingsService(db_session)

    keys = {c.config_key for c in await svc.get_settings()}
    assert keys == {"notification_prefs", "signal_params", "strategy_weights"}


async def test_int_lvl_01_upsert_stamps_correct_level(db_session: AsyncSession) -> None:
    """upsert_setting 按 CONFIG_KEY_LEVEL 写正确 user_level（不再硬编码 L2）。"""
    svc = SettingsService(db_session)

    cfg = await svc.upsert_setting("strategy_weights", {"trend": 0.6})
    assert cfg.user_level == "L3"

    cfg2 = await svc.upsert_setting("notification_prefs", {"wx_enabled": False})
    assert cfg2.user_level == "L1"
