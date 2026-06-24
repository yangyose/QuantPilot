"""AccountService：账户/持仓/成交/资金流水管理（Phase 6）。

AccountService 直接持有 AsyncSession（无独立 AccountRepository 层）：
- 账户数据域与市场数据域（MarketDataRepository）完全独立
- record_trade() 需要原子性写入 trade_record / position / fund_flow / account.cash（4 张表）
- 参考：system_design §3 文件结构注释
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.account import Account, DailyPortfolioValue, FundFlow, Position, TradeRecord

logger = logging.getLogger(__name__)


def compute_wac(
    old_shares: int,
    old_cost: float,
    new_shares: int,
    new_price: float,
    commission: float = 0.0,
) -> float:
    """加权平均成本（WAC）计算，commission 摊入成本。

    Args:
        old_shares: 买入前已有股数（首次建仓传 0）
        old_cost:   买入前平均成本（首次建仓传 0.0）
        new_shares: 本次买入股数
        new_price:  本次买入价格
        commission: 本次交易佣金（摊入成本）

    Returns:
        新的加权平均成本（每股）
    """
    total_shares = old_shares + new_shares
    return (old_shares * old_cost + new_shares * new_price + commission) / total_shares


class OversellError(ValueError):
    """replay 过程中卖出超过持仓（撤销某买入后导致后续卖出无券可卖）。"""


@dataclass(frozen=True)
class ReplayEvent:
    """持仓 replay 的单个事件（成交 BUY/SELL 或分红 DIVIDEND）。"""

    kind: str  # "BUY" | "SELL" | "DIVIDEND"
    trade_date: date
    seq: int  # 同日内的稳定排序键（一般用主键 id）
    shares: int = 0  # BUY/SELL 股数；DIVIDEND 传 0
    price: float = 0.0  # BUY/SELL 价格
    commission: float = 0.0  # BUY 佣金（摊入成本）
    amount: float = 0.0  # DIVIDEND 现金总额（用于摊低成本）


@dataclass(frozen=True)
class ReplayResult:
    """replay 后的持仓状态。shares == 0 表示已平仓（不应有持仓行）。"""

    shares: int
    cost_price: float
    open_date: date | None
    phase: str | None  # BUILD/HOLD/REDUCE；平仓时 None


# 同日内事件处理次序：BUY 先于 DIVIDEND（分红依赖当时持股），SELL 最后。
_KIND_ORDER = {"BUY": 0, "DIVIDEND": 1, "SELL": 2}


def replay_position(events: list[ReplayEvent]) -> ReplayResult:
    """从成交/分红事件序列重建持仓（纯函数，无 IO）。

    将持仓视为成交流水的派生视图：按时间重放 BUY（WAC 累积）、SELL（减仓，成本不变）、
    DIVIDEND（摊低成本）。任何一步持仓为负 → 抛 OversellError（撤销会破坏后续卖出）。
    平仓（shares 归零）后 cost/open_date 复位，使后续重新建仓从干净状态起算。
    """
    ordered = sorted(events, key=lambda e: (e.trade_date, _KIND_ORDER.get(e.kind, 9), e.seq))
    shares = 0
    cost = 0.0
    open_date: date | None = None
    phase: str | None = None

    for ev in ordered:
        if ev.kind == "BUY":
            cost = compute_wac(shares, cost, ev.shares, ev.price, ev.commission)
            shares += ev.shares
            if open_date is None:
                open_date = ev.trade_date
            phase = "BUILD"
        elif ev.kind == "SELL":
            shares -= ev.shares
            if shares < 0:
                raise OversellError(
                    f"撤销后 {ev.trade_date} 卖出 {ev.shares} 股超过当时持仓"
                )
            if shares == 0:
                cost = 0.0
                open_date = None
                phase = None
            else:
                phase = "REDUCE"
        elif ev.kind == "DIVIDEND":
            if shares > 0 and ev.amount:
                cost -= ev.amount / shares
        else:  # pragma: no cover - 防御性
            raise ValueError(f"未知 replay 事件类型：{ev.kind}")

    return ReplayResult(shares=shares, cost_price=cost, open_date=open_date, phase=phase)


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ 账户

    async def get_account(self, account_id: int) -> Account | None:
        result = await self._session.execute(
            select(Account).where(Account.id == account_id)
        )
        return result.scalar_one_or_none()

    async def get_default_account(self) -> Account | None:
        """获取第一个账户（V1.0 单账户场景）。"""
        result = await self._session.execute(
            select(Account).order_by(Account.id).limit(1)
        )
        return result.scalar_one_or_none()

    async def sync_account(self, account_id: int) -> Account:
        """从 daily_quote 更新持仓当前价/市值/盈亏，重算 total_assets。

        使用 DISTINCT ON (ts_code) 获取各股最新收盘价，正确处理不同股票停牌日期不同的情况。

        【降级说明】设计文档 §2.2/§3.3 指定从 daily_basic 查询收盘价，
        实际使用 DailyQuote（daily_quote 表）——该表语义更明确（OHLCV），
        且 daily_quote.close 与 daily_basic.close 内容等价。
        恢复条件：若后续需要 daily_basic 专属字段（如复权因子），再统一切换。
        """
        account = await self.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        positions = await self.get_positions(account_id)
        if not positions:
            account.total_assets = float(account.cash or 0)
            account.synced_at = dt.datetime.now(tz=dt.timezone.utc)
            return account

        ts_codes = [p.ts_code for p in positions]

        # 延迟导入避免循环依赖
        from quantpilot.models.market import DailyQuote

        stmt = (
            select(DailyQuote.ts_code, DailyQuote.close)
            .distinct(DailyQuote.ts_code)
            .where(DailyQuote.ts_code.in_(ts_codes))
            .order_by(DailyQuote.ts_code, DailyQuote.trade_date.desc())
        )
        result = await self._session.execute(stmt)
        price_map: dict[str, float] = {
            row.ts_code: float(row.close) for row in result if row.close is not None
        }

        total_market_value = 0.0
        for pos in positions:
            current_price = price_map.get(pos.ts_code)
            if current_price is not None:
                pos.current_price = current_price
                pos.market_value = current_price * pos.shares
                if pos.cost_price:
                    pos.pnl_pct = (current_price - float(pos.cost_price)) / float(pos.cost_price)
            total_market_value += float(pos.market_value or 0)

        account.total_assets = float(account.cash or 0) + total_market_value
        account.synced_at = dt.datetime.now(tz=dt.timezone.utc)
        return account

    # ------------------------------------------------------------------ 持仓

    async def get_positions(self, account_id: int) -> list[Position]:
        result = await self._session.execute(
            select(Position).where(Position.account_id == account_id)
        )
        return list(result.scalars().all())

    async def get_all_positions(self) -> list[Position]:
        """供 Phase 7 DailyPipeline 获取全部活跃持仓（跨账户）。"""
        result = await self._session.execute(select(Position))
        return list(result.scalars().all())

    async def update_position(
        self,
        position_id: int,
        current_price: float | None = None,
        phase: str | None = None,
    ) -> Position:
        result = await self._session.execute(
            select(Position).where(Position.id == position_id)
        )
        position = result.scalar_one_or_none()
        if position is None:
            raise ValueError(f"Position {position_id} not found")

        if current_price is not None:
            position.current_price = current_price
            position.market_value = current_price * position.shares
            if position.cost_price:
                position.pnl_pct = (
                    (current_price - float(position.cost_price)) / float(position.cost_price)
                )
        if phase is not None:
            position.phase = phase

        await self._session.flush()
        await self._session.refresh(position)
        return position

    # ------------------------------------------------------------------ 成交录入

    async def record_trade(
        self,
        account_id: int,
        ts_code: str,
        trade_type: str,
        trade_date: date,
        price: float,
        shares: int,
        commission: float = 0.0,
        stamp_tax: float = 0.0,
        signal_id: int | None = None,
        note: str | None = None,
    ) -> TradeRecord:
        """写入 trade_record + 更新 position + 写入 fund_flow + 更新 account.cash（原子）。

        BUY：WAC 成本价，phase = BUILD。
        SELL 超卖（卖出 > 持仓）→ 抛 ValueError。
        SELL 清仓（shares_after == 0）→ 删除 position 行。
        """
        if trade_type not in ("BUY", "SELL"):
            raise ValueError(f"非法 trade_type：{trade_type}")

        account = await self.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        amount = price * shares
        trade = TradeRecord(
            account_id=account_id,
            ts_code=ts_code,
            trade_type=trade_type,
            trade_date=trade_date,
            price=price,
            shares=shares,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            signal_id=signal_id,
            note=note,
        )
        self._session.add(trade)
        await self._session.flush()
        trade_id = trade.id

        pos_result = await self._session.execute(
            select(Position).where(
                Position.account_id == account_id,
                Position.ts_code == ts_code,
            )
        )
        position = pos_result.scalar_one_or_none()

        if trade_type == "BUY":
            if position is None:
                new_pos = Position(
                    account_id=account_id,
                    ts_code=ts_code,
                    shares=shares,
                    cost_price=compute_wac(0, 0.0, shares, price, commission),
                    open_date=trade_date,
                    phase="BUILD",
                )
                self._session.add(new_pos)
            else:
                position.cost_price = compute_wac(
                    position.shares, float(position.cost_price or 0),
                    shares, price, commission,
                )
                position.shares += shares
                position.phase = "BUILD"

            account.cash = float(account.cash or 0) - (amount + commission)
            self._session.add(FundFlow(
                account_id=account_id,
                flow_type="BUY_FEE",
                amount=-(amount + commission),
                trade_date=trade_date,
                ts_code=ts_code,
                related_trade_id=trade_id,
            ))

        elif trade_type == "SELL":
            if position is None:
                raise ValueError(f"无持仓可卖：{ts_code}")

            shares_after = position.shares - shares
            if shares_after < 0:
                raise ValueError(
                    f"超卖：当前持仓 {position.shares} 股，尝试卖出 {shares} 股"
                )

            proceeds = amount - commission - stamp_tax
            account.cash = float(account.cash or 0) + proceeds

            if shares_after == 0:
                await self._session.delete(position)
            else:
                position.shares = shares_after
                position.phase = "REDUCE"

            self._session.add(FundFlow(
                account_id=account_id,
                flow_type="SELL_PROCEEDS",
                amount=proceeds,
                trade_date=trade_date,
                ts_code=ts_code,
                related_trade_id=trade_id,
            ))

        await self._session.flush()
        await self._session.refresh(trade)
        return trade

    async def list_trades(
        self,
        account_id: int,
        include_voided: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TradeRecord], int]:
        """成交记录列表（分页）。include_voided=False 时过滤已作废行。"""
        stmt = select(TradeRecord).where(TradeRecord.account_id == account_id)
        if not include_voided:
            stmt = stmt.where(TradeRecord.is_voided.is_(False))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(
            TradeRecord.trade_date.desc(), TradeRecord.id.desc()
        ).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    # ------------------------------------------------------------------ 作废订正

    async def _gather_replay_events(
        self, account_id: int, ts_code: str, exclude_trade_id: int | None = None,
    ) -> list[ReplayEvent]:
        """收集某 ts_code 的非作废成交 + 非作废分红，转为 replay 事件序列。

        持仓视为成交流水的派生视图：撤销 = 排除某行后重放。exclude_trade_id 用于
        「先校验后落库」（在标记作废前先 dry-run replay 检测超卖）。
        """
        trade_stmt = select(TradeRecord).where(
            TradeRecord.account_id == account_id,
            TradeRecord.ts_code == ts_code,
            TradeRecord.is_voided.is_(False),
        )
        if exclude_trade_id is not None:
            trade_stmt = trade_stmt.where(TradeRecord.id != exclude_trade_id)
        trades = (await self._session.execute(trade_stmt)).scalars().all()

        div_stmt = select(FundFlow).where(
            FundFlow.account_id == account_id,
            FundFlow.ts_code == ts_code,
            FundFlow.flow_type == "DIVIDEND",
            FundFlow.is_voided.is_(False),
        )
        dividends = (await self._session.execute(div_stmt)).scalars().all()

        events: list[ReplayEvent] = []
        for t in trades:
            events.append(ReplayEvent(
                kind=t.trade_type,
                trade_date=t.trade_date,
                seq=t.id,
                shares=int(t.shares or 0),
                price=float(t.price or 0),
                commission=float(t.commission or 0),
            ))
        for f in dividends:
            events.append(ReplayEvent(
                kind="DIVIDEND",
                trade_date=f.trade_date,
                seq=f.id,
                amount=float(f.amount or 0),
            ))
        return events

    async def _apply_position_result(
        self, account_id: int, ts_code: str, result: ReplayResult,
    ) -> None:
        """把 replay 结果落到 position 表：平仓删除、否则 upsert（价格字段待下次盯市刷新）。"""
        pos_result = await self._session.execute(
            select(Position).where(
                Position.account_id == account_id,
                Position.ts_code == ts_code,
            )
        )
        position = pos_result.scalar_one_or_none()

        if result.shares == 0:
            if position is not None:
                await self._session.delete(position)
            return

        if position is None:
            position = Position(account_id=account_id, ts_code=ts_code, shares=result.shares)
            self._session.add(position)

        position.shares = result.shares
        position.cost_price = result.cost_price
        position.open_date = result.open_date
        position.phase = result.phase
        # 价格相关字段失效 → 置空，待下次 sync_account / mark_to_market 刷新
        position.current_price = None
        position.market_value = None
        position.pnl_pct = None

    async def void_trade(
        self, trade_id: int, void_note: str | None = None,
    ) -> TradeRecord:
        """作废一笔成交（软删除）：联动作废费用流水 + 逆仕訳现金 + 重建持仓。

        先 dry-run replay（排除本笔）检测超卖——若撤销会导致后续卖出无券可卖则抛
        OversellError，不改动任何数据。校验通过后落库。
        """
        trade = (await self._session.execute(
            select(TradeRecord).where(TradeRecord.id == trade_id)
        )).scalar_one_or_none()
        if trade is None:
            raise ValueError(f"成交 {trade_id} not found")
        if trade.is_voided:
            raise ValueError("该成交已作废，不可重复作废")

        # 先校验：排除本笔后 replay（可能抛 OversellError，此时未改动数据）
        events = await self._gather_replay_events(
            trade.account_id, trade.ts_code, exclude_trade_id=trade_id,
        )
        result = replay_position(events)

        now = dt.datetime.now(tz=dt.timezone.utc)
        trade.is_voided = True
        trade.voided_at = now
        trade.void_note = void_note

        # 联动作废本笔产生的资金流水（BUY_FEE / SELL_PROCEEDS）并逆仕訳现金
        account = await self.get_account(trade.account_id)
        if account is None:  # pragma: no cover - FK 保证存在
            raise ValueError(f"Account {trade.account_id} not found")
        flows = (await self._session.execute(
            select(FundFlow).where(
                FundFlow.related_trade_id == trade_id,
                FundFlow.is_voided.is_(False),
            )
        )).scalars().all()
        for f in flows:
            account.cash = float(account.cash or 0) - float(f.amount or 0)
            f.is_voided = True
            f.voided_at = now
            f.void_note = void_note or "成交作废联动"

        await self._apply_position_result(trade.account_id, trade.ts_code, result)
        await self._session.flush()
        await self._session.refresh(trade)
        logger.info(
            "trade_voided id=%d account=%d ts_code=%s flows_voided=%d",
            trade_id, trade.account_id, trade.ts_code, len(flows),
        )
        return trade

    async def void_fund_flow(
        self, flow_id: int, void_note: str | None = None,
    ) -> FundFlow:
        """作废一笔资金流水（DEPOSIT/WITHDRAW/DIVIDEND）：逆仕訳现金；分红则重建持仓。

        BUY_FEE / SELL_PROCEEDS 不可单独作废（须经对应成交的 void_trade 联动），
        否则会与成交状态脱节。
        """
        flow = (await self._session.execute(
            select(FundFlow).where(FundFlow.id == flow_id)
        )).scalar_one_or_none()
        if flow is None:
            raise ValueError(f"资金流水 {flow_id} not found")
        if flow.is_voided:
            raise ValueError("该资金流水已作废，不可重复作废")
        if flow.flow_type in ("BUY_FEE", "SELL_PROCEEDS"):
            raise ValueError("交易费用流水不可单独作废，请作废对应成交记录")

        account = await self.get_account(flow.account_id)
        if account is None:  # pragma: no cover - FK 保证存在
            raise ValueError(f"Account {flow.account_id} not found")

        now = dt.datetime.now(tz=dt.timezone.utc)
        account.cash = float(account.cash or 0) - float(flow.amount or 0)
        flow.is_voided = True
        flow.voided_at = now
        flow.void_note = void_note

        # 分红作废 → 撤销其对成本的摊低，重建持仓（标记作废后 gather 自动排除本笔）
        if flow.flow_type == "DIVIDEND" and flow.ts_code:
            events = await self._gather_replay_events(flow.account_id, flow.ts_code)
            result = replay_position(events)
            await self._apply_position_result(flow.account_id, flow.ts_code, result)

        await self._session.flush()
        await self._session.refresh(flow)
        logger.info(
            "fund_flow_voided id=%d account=%d type=%s amount=%s",
            flow_id, flow.account_id, flow.flow_type, flow.amount,
        )
        return flow

    # ------------------------------------------------------------------ 资金流水

    async def find_fund_flow_by_idempotency(
        self, account_id: int, idempotency_key: str,
    ) -> FundFlow | None:
        """Phase 14 §14-1：按 (account_id, idempotency_key) 查找已存在的 fund_flow。

        命中 → 返回原 FundFlow；未命中 → None。用于 deposit/record_dividend
        幂等保护（先查后写 + IntegrityError 兜底重查的两层防御）。
        """
        result = await self._session.execute(
            select(FundFlow).where(
                FundFlow.account_id == account_id,
                FundFlow.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def deposit(
        self,
        account_id: int,
        amount: float,
        trade_date: date,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> FundFlow:
        """入金。idempotency_key 非空时启用幂等保护（先查后写 + 竞态兜底重查）。

        Phase 14 §14-1（RM-13）：客户端网络抖动 / 双击重试时，同 key 第二次调用
        直接返回首次的 FundFlow，account.cash 不二次累加；NULL key 走旧路径（兼容）。
        """
        if idempotency_key is not None:
            existing = await self.find_fund_flow_by_idempotency(
                account_id, idempotency_key,
            )
            if existing is not None:
                logger.info(
                    "deposit_idempotent_hit account=%d key=%s flow_id=%d",
                    account_id, idempotency_key, existing.id,
                )
                return existing

        account = await self.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        account.cash = float(account.cash or 0) + amount
        flow = FundFlow(
            account_id=account_id,
            flow_type="DEPOSIT",
            amount=amount,
            trade_date=trade_date,
            note=note,
            idempotency_key=idempotency_key,
        )
        self._session.add(flow)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if idempotency_key is not None and (
                "uq_fund_flow_account_idempotency" in str(exc)
            ):
                # 并发竞态：先查未命中 → 另一请求已 INSERT → 本请求撞 partial unique
                await self._session.rollback()
                existing = await self.find_fund_flow_by_idempotency(
                    account_id, idempotency_key,
                )
                if existing is not None:
                    logger.info(
                        "deposit_idempotent_race_resolved account=%d key=%s",
                        account_id, idempotency_key,
                    )
                    return existing
            raise
        await self._session.refresh(flow)
        return flow

    async def withdraw(
        self,
        account_id: int,
        amount: float,
        trade_date: date,
        note: str | None = None,
    ) -> FundFlow:
        """amount 为正数，内部写入负值 fund_flow。cash 不足 → 抛 ValueError。"""
        account = await self.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        cash = float(account.cash or 0)
        if cash < amount:
            raise ValueError(f"现金不足：可用 {cash:.2f} 元，出金 {amount:.2f} 元")

        account.cash = cash - amount
        flow = FundFlow(
            account_id=account_id,
            flow_type="WITHDRAW",
            amount=-amount,
            trade_date=trade_date,
            note=note,
        )
        self._session.add(flow)
        await self._session.flush()
        await self._session.refresh(flow)
        return flow

    async def record_dividend(
        self,
        account_id: int,
        ts_code: str,
        amount: float,
        trade_date: date,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> FundFlow:
        """手动录入分红：写入 DIVIDEND fund_flow + cash += amount + 调整 cost_price。

        若对应持仓存在，cost_price -= amount / shares（降低每股成本）。
        若已平仓（持仓不存在），仅写 fund_flow，不更新 cost_price。

        Phase 14 §14-1（RM-13）：idempotency_key 非空时启用幂等保护，重复提交
        直接返回首次的 FundFlow，cost_price 不二次调整 + cash 不二次累加。

        V1.0 整改 Batch 2 — B2-2 排查结论：cost_price 在 V1.0 仅用于：
        - AccountService.pnl_pct = (current_price - cost_price) / cost_price
          （前后均为非复权 daily_quote.close）
        - SignalGenerator 加仓判定（cost_deviation 比较，前后均为非复权 close）
        **不参与 BacktestEngine（独立 BacktestPosition + adj_close）**
        **不参与 PerformanceService（基于 DailyPortfolioValue 快照）**
        因此当前 cost_price -= amount / shares 与后复权 adj_factor 不存在双重计算。
        未来若引入 cost_price 参与绩效或回测，必须先评估前/后复权双轨记录
        （详见 docs/reviews/v1_overall_review_2026-04-27.md §5.3 FIN-HIGH-08）。
        """
        if idempotency_key is not None:
            existing = await self.find_fund_flow_by_idempotency(
                account_id, idempotency_key,
            )
            if existing is not None:
                logger.info(
                    "dividend_idempotent_hit account=%d key=%s flow_id=%d",
                    account_id, idempotency_key, existing.id,
                )
                return existing

        account = await self.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        account.cash = float(account.cash or 0) + amount

        pos_result = await self._session.execute(
            select(Position).where(
                Position.account_id == account_id,
                Position.ts_code == ts_code,
            )
        )
        position = pos_result.scalar_one_or_none()
        if position is not None and position.shares > 0:
            # V1.0 整改 Batch 2 — B2-2：cost_price 仅用于账户层成本展示，不参与回测/绩效计算
            position.cost_price = float(position.cost_price or 0) - amount / position.shares

        flow = FundFlow(
            account_id=account_id,
            flow_type="DIVIDEND",
            amount=amount,
            trade_date=trade_date,
            ts_code=ts_code,
            note=note,
            idempotency_key=idempotency_key,
        )
        self._session.add(flow)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if idempotency_key is not None and (
                "uq_fund_flow_account_idempotency" in str(exc)
            ):
                await self._session.rollback()
                existing = await self.find_fund_flow_by_idempotency(
                    account_id, idempotency_key,
                )
                if existing is not None:
                    logger.info(
                        "dividend_idempotent_race_resolved account=%d key=%s",
                        account_id, idempotency_key,
                    )
                    return existing
            raise
        await self._session.refresh(flow)
        return flow

    async def get_current_drawdown(self, account_id: int) -> float | None:
        """V1.0 整改 Batch 2 — B2-1：基于 daily_portfolio_value 计算账户当前最大回撤。

        返回 None 表示无足够数据（< 2 个净值点）；返回 0.0~1.0 表示历史峰值至今的最大回撤幅度。
        SignalService.generate_for_date 在 CP3 调 RiskChecker 时传入此值，
        触发 SDD §10.2 账户回撤 WARN 级告警（与 risk_limits.max_drawdown_pct 比较）。
        """
        from quantpilot.models.account import DailyPortfolioValue

        rows = (await self._session.execute(
            select(DailyPortfolioValue.total_value)
            .where(DailyPortfolioValue.account_id == account_id)
            .order_by(DailyPortfolioValue.trade_date)
        )).scalars().all()

        if len(rows) < 2:
            return None

        running_max = float(rows[0])
        max_dd = 0.0
        for v in rows[1:]:
            fv = float(v)
            if fv > running_max:
                running_max = fv
            if running_max > 0:
                dd = (running_max - fv) / running_max
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    async def get_cashflow(
        self,
        account_id: int,
        flow_type: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
        include_voided: bool = False,
    ) -> tuple[list[FundFlow], int]:
        """返回 (流水列表, total_count)，支持分页与过滤。

        include_voided=False（默认）过滤已作废流水——已作废行的现金影响已在
        account.cash 逆仕訳，列表默认不展示以免误导余额对账。
        """
        stmt = select(FundFlow).where(FundFlow.account_id == account_id)
        if not include_voided:
            stmt = stmt.where(FundFlow.is_voided.is_(False))
        if flow_type:
            stmt = stmt.where(FundFlow.flow_type == flow_type)
        if start_date:
            stmt = stmt.where(FundFlow.trade_date >= start_date)
        if end_date:
            stmt = stmt.where(FundFlow.trade_date <= end_date)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(FundFlow.trade_date.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    # ------------------------------------------------------------------ 盯市

    async def mark_to_market(self, trade_date: date) -> list[Account]:
        """按 trade_date 当日 daily_quote.close 更新所有账户持仓价格，写 daily_portfolio_value。

        与 sync_account() 区别：
        - sync_account()：用 DISTINCT ON 取各股最新价（任意日期），单账户
        - mark_to_market()：精确匹配 trade_date，跨所有账户，写 daily_portfolio_value

        返回已更新的 Account 列表（可能为空，若当日无持仓）。
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from quantpilot.models.market import DailyQuote

        positions = await self.get_all_positions()
        if not positions:
            logger.info("mark_to_market_skip: no positions on %s", trade_date)
            return []

        ts_codes = list({p.ts_code for p in positions})

        # 精确匹配当日价格（不用 DISTINCT ON）
        price_rows = await self._session.execute(
            select(DailyQuote.ts_code, DailyQuote.close).where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date == trade_date,
            )
        )
        price_map: dict[str, float] = {
            row.ts_code: float(row.close)
            for row in price_rows
            if row.close is not None
        }

        if not price_map:
            logger.info("mark_to_market_skip: no daily_quote data for %s", trade_date)
            return []

        # 按 account_id 分组更新持仓
        account_ids = list({p.account_id for p in positions})
        accounts: list[Account] = []

        for account_id in account_ids:
            account_result = await self._session.execute(
                select(Account).where(Account.id == account_id)
            )
            account = account_result.scalar_one_or_none()
            if account is None:
                continue

            acct_positions = [p for p in positions if p.account_id == account_id]
            total_market_value = 0.0

            for pos in acct_positions:
                current_price = price_map.get(pos.ts_code)
                if current_price is not None:
                    pos.current_price = current_price
                    pos.market_value = current_price * pos.shares
                    if pos.cost_price:
                        pos.pnl_pct = (
                            (current_price - float(pos.cost_price)) / float(pos.cost_price)
                        )
                total_market_value += float(pos.market_value or 0)

            cash = float(account.cash or 0)
            total_value = cash + total_market_value
            account.total_assets = total_value

            # 写 daily_portfolio_value（ON CONFLICT DO UPDATE）
            stmt = (
                pg_insert(DailyPortfolioValue)
                .values(
                    account_id=account_id,
                    trade_date=trade_date,
                    total_value=total_value,
                    cash=cash,
                    position_value=total_market_value,
                )
                .on_conflict_do_update(
                    constraint="uq_dpv_account_date",
                    set_={
                        "total_value": total_value,
                        "cash": cash,
                        "position_value": total_market_value,
                    },
                )
            )
            await self._session.execute(stmt)
            accounts.append(account)

        await self._session.flush()
        logger.info(
            "mark_to_market_done: trade_date=%s accounts=%d",
            trade_date, len(accounts),
        )
        return accounts
