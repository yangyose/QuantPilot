"""诊断脚本：查看最近的回测任务状态。
用法：在 backend/ 目录运行 uv run python scripts/check_backtest_tasks.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select, text


async def main() -> None:
    from quantpilot.core.database import AsyncSessionLocal
    from quantpilot.models.system import BacktestTask

    async with AsyncSessionLocal() as session:
        tasks = (await session.execute(
            select(BacktestTask).order_by(BacktestTask.created_at.desc()).limit(10)
        )).scalars().all()

        if not tasks:
            print("数据库中没有回测任务")
            return

        print(f"最近 {len(tasks)} 条回测任务：")
        print("-" * 80)
        for t in tasks:
            print(f"task_id   : {t.task_id}")
            print(f"status    : {t.status}")
            print(f"created_at: {t.created_at}")
            print(f"started_at: {t.started_at}")
            print(f"finished_at: {t.finished_at}")
            print(f"error_msg : {t.error_msg!r}")
            print("-" * 80)


if __name__ == "__main__":
    asyncio.run(main())
