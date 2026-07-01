"""V1.5-G G-4b 集成测试：通知账户隔离（混合方案 §6.4，需真实 PostgreSQL）。

- INT-NOTIF-01：账户私有通知（止损/风险）仅归属账户可见；系统级(NULL)通知全员可见。
- INT-NOTIF-02：去重按账户分账户判定——两账户同 ts_code 止损各自触发。
- INT-NOTIF-03：mark_read 跨账户 → None（不能标记他人私有通知）。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.security import hash_password
from quantpilot.models.account import Account
from quantpilot.models.user import User
from quantpilot.services.config_service import ConfigService
from quantpilot.services.notification_service import NotificationService
from tests.integration._helpers import seeded_user_id


async def _account_a(session: AsyncSession) -> Account:
    acc = Account(
        user_id=await seeded_user_id(session),
        name="账户A", account_type="REAL", cash=100000.0, total_assets=100000.0,
    )
    session.add(acc)
    await session.flush()
    return acc


async def _account_b(session: AsyncSession) -> Account:
    user_b = User(
        username="notif_user_b", email="notif_user_b@test.local",
        password_hash=hash_password("Str0ngPass!"), level="L1",
    )
    session.add(user_b)
    await session.flush()
    acc = Account(
        user_id=user_b.id,
        name="账户B", account_type="REAL", cash=100000.0, total_assets=100000.0,
    )
    session.add(acc)
    await session.flush()
    return acc


def _svc(session: AsyncSession) -> NotificationService:
    return NotificationService(session, ConfigService(session, None))


async def test_int_notif_01_private_scoped_shared_visible_all(
    db_session: AsyncSession,
) -> None:
    """A 的止损私有只 A 可见；系统级(NULL)信号通知 A/B 均可见。"""
    acc_a = await _account_a(db_session)
    acc_b = await _account_b(db_session)
    svc = _svc(db_session)

    # A 私有止损
    await svc.notify_stop_loss_warn(
        ts_code="AAA.SZ", name=None, current_price=10.0,
        stop_loss_price=9.9, distance_pct=0.01, account_id=acc_a.id,
    )
    # 系统级信号（account_id 默认 None）
    await svc.notify("SIGNAL_BUY", "买入信号", "body", {"ts_code": "SYS.SZ"})

    a_items, a_total = await svc.list_notifications(account_id=acc_a.id)
    a_types = {n.notify_type for n in a_items}
    assert a_types == {"STOP_LOSS_WARN", "SIGNAL_BUY"}
    assert a_total == 2

    b_items, b_total = await svc.list_notifications(account_id=acc_b.id)
    b_types = {n.notify_type for n in b_items}
    assert b_types == {"SIGNAL_BUY"}  # 仅系统级可见，看不到 A 的止损
    assert b_total == 1

    # 未读数亦按账户隔离
    assert await svc.count_unread(account_id=acc_a.id) == 2
    assert await svc.count_unread(account_id=acc_b.id) == 1


async def test_int_notif_02_dedup_per_account(db_session: AsyncSession) -> None:
    """两账户同 ts_code 止损各自触发（去重按账户分账户判定）。"""
    acc_a = await _account_a(db_session)
    acc_b = await _account_b(db_session)
    svc = _svc(db_session)

    n_a = await svc.notify_stop_loss_warn(
        ts_code="DUP.SZ", name=None, current_price=10.0,
        stop_loss_price=9.9, distance_pct=0.01, account_id=acc_a.id,
    )
    n_b = await svc.notify_stop_loss_warn(
        ts_code="DUP.SZ", name=None, current_price=10.0,
        stop_loss_price=9.9, distance_pct=0.01, account_id=acc_b.id,
    )
    # 两账户各写入一条（未被跨账户去重）
    assert n_a is not None and n_b is not None

    # 同账户同 ts_code 二次 → 被去重
    n_a2 = await svc.notify_stop_loss_warn(
        ts_code="DUP.SZ", name=None, current_price=10.0,
        stop_loss_price=9.9, distance_pct=0.01, account_id=acc_a.id,
    )
    assert n_a2 is None


async def test_int_notif_03_mark_read_cross_account_none(
    db_session: AsyncSession,
) -> None:
    """B 按 A 私有通知 id 标记已读 → None（不越权，路由转 404）；A 自己可标记。"""
    acc_a = await _account_a(db_session)
    acc_b = await _account_b(db_session)
    svc = _svc(db_session)

    notif = await svc.notify_stop_loss_warn(
        ts_code="MRK.SZ", name=None, current_price=10.0,
        stop_loss_price=9.9, distance_pct=0.01, account_id=acc_a.id,
    )
    assert notif is not None

    # B 越权标记 → None
    assert await svc.mark_read(notif.id, account_id=acc_b.id) is None
    # A 自己标记成功
    marked = await svc.mark_read(notif.id, account_id=acc_a.id)
    assert marked is not None and marked.read_at is not None


async def test_int_notif_03_mark_all_read_scoped(db_session: AsyncSession) -> None:
    """mark_all_read 仅标记本账户可见（私有 + 系统级），不动他人私有。"""
    acc_a = await _account_a(db_session)
    acc_b = await _account_b(db_session)
    svc = _svc(db_session)

    await svc.notify_stop_loss_warn(
        ts_code="X.SZ", name=None, current_price=10.0,
        stop_loss_price=9.9, distance_pct=0.01, account_id=acc_a.id,
    )
    await svc.notify_stop_loss_warn(
        ts_code="Y.SZ", name=None, current_price=10.0,
        stop_loss_price=9.9, distance_pct=0.01, account_id=acc_b.id,
    )
    await svc.notify("SIGNAL_BUY", "买入信号", "body", {"ts_code": "SYS.SZ"})

    # A 全标已读 → 标 2 条（A 私有 1 + 系统级 1），不动 B 的私有
    updated = await svc.mark_all_read(account_id=acc_a.id)
    assert updated == 2
    assert await svc.count_unread(account_id=acc_b.id) == 1  # B 私有仍未读
