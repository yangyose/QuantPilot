"""SettingsService：用户配置 CRUD + 变更历史回溯（Phase 6）。"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.config_defaults import CONFIG_KEY_LEVEL, config_visible_at_level
from quantpilot.models.system import UserConfig, UserConfigHistory

logger = logging.getLogger(__name__)


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_settings(self, max_level: str | None = None) -> list[UserConfig]:
        """返回 user_config 记录；max_level 非空时按 level 过滤（V1.5-G G-4a §6.3）。

        max_level = 当前用户 level（L1/L2/L3）→ 仅返回该用户可见的配置项
        （config 所需 level <= 用户 level）。None → 不过滤（如 export 全量备份）。
        过滤依据 CONFIG_KEY_LEVEL 代码内真源，非 DB user_level 列（后者不可靠）。
        """
        result = await self._session.execute(
            select(UserConfig).order_by(UserConfig.config_key)
        )
        configs = list(result.scalars().all())
        if max_level is not None:
            configs = [
                c for c in configs if config_visible_at_level(c.config_key, max_level)
            ]
        return configs

    async def upsert_setting(
        self,
        config_key: str,
        config_value: dict,
        change_note: str | None = None,
    ) -> UserConfig:
        """写 user_config + 自动写 user_config_history（old_value = 当前值，可为 None）。"""
        existing_result = await self._session.execute(
            select(UserConfig).where(UserConfig.config_key == config_key)
        )
        existing = existing_result.scalar_one_or_none()
        old_value = existing.config_value if existing else None

        history = UserConfigHistory(
            config_key=config_key,
            old_value=old_value,
            new_value=config_value,
            change_note=change_note,
        )
        self._session.add(history)

        stmt = (
            insert(UserConfig)
            .values(
                config_key=config_key,
                # G-4a §6.3：按 config_key 的所需 level 写库（不再硬编码 L2），
                # 让 DB user_level 列与 CONFIG_KEY_LEVEL 真源一致；未登记 key 回落 L2。
                config_value=config_value,
                user_level=CONFIG_KEY_LEVEL.get(config_key, "L2"),
                updated_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=["config_key"],
                set_={"config_value": config_value, "updated_at": func.now()},
            )
            .returning(UserConfig)
        )
        result = await self._session.execute(stmt)
        config = result.scalar_one()
        await self._session.flush()
        # RETURNING may yield a stale identity-map object after ON CONFLICT DO UPDATE;
        # refresh forces re-read of the actually committed values.
        await self._session.refresh(config)
        return config

    async def get_config_history(
        self,
        config_key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserConfigHistory], int]:
        stmt = select(UserConfigHistory)
        if config_key:
            stmt = stmt.where(UserConfigHistory.config_key == config_key)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(UserConfigHistory.changed_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def revert_config(self, history_id: int) -> UserConfig:
        """回退：读取 history.old_value（变更前状态）→ 等价调用 upsert_setting。

        不存在 → 抛 ValueError。
        old_value 为 None（首次创建记录，无前值）→ 抛 ValueError。
        """
        result = await self._session.execute(
            select(UserConfigHistory).where(UserConfigHistory.id == history_id)
        )
        history = result.scalar_one_or_none()
        if history is None:
            raise ValueError(f"Config history {history_id} not found")
        if history.old_value is None:
            raise ValueError(
                f"无法回退：历史记录 {history_id} 为首次创建，old_value=None，无前值可恢复"
            )
        return await self.upsert_setting(
            config_key=history.config_key,
            config_value=history.old_value,
            change_note=f"Reverted to state before history #{history_id}",
        )
