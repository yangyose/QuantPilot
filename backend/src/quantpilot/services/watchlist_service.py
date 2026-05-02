"""WatchlistService：黑白名单 CRUD（Phase 4）。"""
from __future__ import annotations

from typing import Literal

from quantpilot.data.repository import MarketDataRepository
from quantpilot.schemas.scoring import WatchlistItem


class WatchlistService:
    def __init__(self, repo: MarketDataRepository) -> None:
        self._repo = repo

    async def get_list(
        self,
        list_type: Literal["BLACKLIST", "WHITELIST"] | None = None,
    ) -> list[WatchlistItem]:
        rows = await self._repo.get_watchlist(list_type=list_type)
        return [
            WatchlistItem(
                ts_code=r.ts_code,
                list_type=r.list_type,
                note=r.reason or "",
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def add(
        self,
        ts_code: str,
        list_type: Literal["BLACKLIST", "WHITELIST"],
        note: str = "",
    ) -> WatchlistItem:
        """ts_code + list_type 唯一约束，重复添加返回已有记录（幂等）。"""
        row = await self._repo.add_watchlist(ts_code=ts_code, list_type=list_type, note=note)
        return WatchlistItem(
            ts_code=row.ts_code,
            list_type=row.list_type,
            note=row.reason or "",
            created_at=row.created_at,
        )

    async def remove(
        self,
        ts_code: str,
        list_type: Literal["BLACKLIST", "WHITELIST"],
    ) -> None:
        """不存在时静默成功（幂等）。"""
        await self._repo.remove_watchlist(ts_code=ts_code, list_type=list_type)
