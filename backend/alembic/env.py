import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from quantpilot.core.config import settings
from quantpilot.models import Base  # 聚合所有 ORM 模型的 metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection):
    """同步回调，在异步连接的 run_sync 中执行"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # 检测列类型变更
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    connectable = create_async_engine(settings.database_url, echo=settings.debug)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    raise NotImplementedError("Offline mode not supported with asyncpg driver")
else:
    asyncio.run(run_migrations_online())
