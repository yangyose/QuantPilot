"""持仓私有信号的评估与推送（单一实现源）。V1.5-G G-4d-3/4 + V1.5-K 后续修正。

## 三个调用方共用本函数

| 调用方 | 时机 | 用的是哪天的价 |
|---|---|---|
| `_stop_loss_warn_job` | 15:05 | 前一交易日收盘（**前瞻预警**，「快到了」）|
| `DailyPipeline._step7_private_signals` | 管线末尾（盯市后）| **当日收盘**（已经破了）|
| `_private_signal_recheck_job` | 18:30 | 当日收盘（**兜底**，防管线自身挂掉）|

三处各写一份就会漂移；且「止损/加仓逻辑单一实现源」是 `evaluate_private_signals`
的既定约定，本模块把推送这一半也收进来。

## ⚠️ `sent` 只计**真正落库**的那些

2026-09-04 生产首跑实测：`sent=3` 而 `in_app_notification` 只落 1 行。
成因是原实现在 `notify(...)` 不抛异常时就计数，而 `NotificationService.notify`
**去重命中时返回 `None` 且不抛**。去重本身是对的，但计数器与其名字不符——
排障时会让人以为发了 3 条。故此处只在返回非 None 时计数。

## 为什么管线末尾也要触发（而不只靠定时 Job）

复评需要的是「盯市已完成」，不是「到 18:30 了」。把因果依赖编码成时间偏移有实证代价：
**2026-09-03 管线跑到 18:11 才被 OOM 杀死**——若只有 18:30 那个 Job，它会看到
`status=RUNNING` → 守卫跳过 → 那天根本没做止损检查，而那天正是持仓跌到 −14.59% 的日子。
守卫防住了「用错数据」，防不住「根本没查」。

故管线末尾直接触发（主，零空等）+ 保留 18:30（兜底，防管线自身挂掉）。
两者叠加不会重复通知：`payload` 含日期，NotificationService 按 24h 窗口去重。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["notify_private_signals"]


async def notify_private_signals(
    signal_service: Any,
    notifier: Any,
    account: Any,
    positions: list,
    today: Any,
) -> int:
    """评估某账户持仓的私有信号并推送，返回**真正落库**的条数。

    `evaluate_private_signals` 复用 SignalGenerator（止损/加仓逻辑单一实现源），
    返回持仓派生的私有 SELL（`hard_stop_loss` / 短中期因子翻转 → `notify_risk_warn`）
    + 加仓 BUY（SDD §10.1 can_add，用户 2026-07-03 拍板同路走通知 → `notify("SIGNAL_BUY")`）；
    共享 `pct_above_sell` 已排除（管线已产）。

    失败隔离：评估失败返回 0 并记 warning，**不抛**——调用方是管线与定时 Job，
    不该被这一步带崩。单条推送失败也不影响其余条。
    """
    if not positions:
        return 0
    try:
        private = await signal_service.evaluate_private_signals(today, positions)
    except Exception:
        logger.warning(
            "private_signal_eval_failed: account=%s", getattr(account, "id", "?"),
            exc_info=True,
        )
        return 0

    sent = 0
    for ps in private:
        try:
            if ps.signal_type == "BUY":
                created = await notifier.notify(
                    "SIGNAL_BUY",
                    f"加仓提示：{ps.ts_code}",
                    ps.reason or "持仓达买入条件且满足加仓规则",
                    payload={
                        "ts_code": ps.ts_code,
                        "date": str(today),
                        "kind": "add_position",
                    },
                    account_id=account.id,
                )
            else:
                created = await notifier.notify_risk_warn(
                    event_type=ps.trigger_reason or "private_sell",
                    message=ps.reason,
                    payload={"ts_code": ps.ts_code, "date": str(today)},
                    account_id=account.id,
                )
            # ⚠️ 只计真正落库的：去重命中时返回 None 且不抛，计进去会让
            # 日志里的 sent 与 in_app_notification 行数对不上（2026-09-04 实证）。
            if created is not None:
                sent += 1
        except Exception:
            logger.warning(
                "private_signal_notify_failed: account=%s ts_code=%s",
                getattr(account, "id", "?"), ps.ts_code, exc_info=True,
            )
    return sent
