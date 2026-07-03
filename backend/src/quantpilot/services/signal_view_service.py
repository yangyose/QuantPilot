"""SignalViewService：GET /signals 响应组装期的账户维度叠加（V1.5-G G-4d-2）。

§2 派生语义：管线产**账户无关的共享信号**（G-4d-1 解耦，signals 表无 account_id），
本服务在 API 请求期按当前用户账户实时叠加账户维度视图——**已持仓标记 is_holding**
+ **仓位建议 suggested_pct**（设计文档 §2 line 194）。

设计约束（关键）：
- **绝不写 ORM 列**：signals 是共享数据，若在 session 内改 ORM 属性会随 get_db 自动
  commit 把 per-account 的 suggested_pct 写回共享表。故本服务只改**响应 dict**
  （SignalResponse.model_dump() 产物），不碰 ORM 对象。
- **降级保护**（【降级说明】）：当前账户数据加载失败时不 500 整个信号页——保留共享
  信号可见（is_holding 缺省 False、suggested_pct 不叠加），记 logger.exception。
  恢复条件：账户 / 市场状态 / 配置数据恢复可读。理由：C-1 元目标——保护用户查看核心
  信号的能力优先于叠加信息的完整性。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.position import PositionConfig, PositionSizer
from quantpilot.engine.signal import TradeSignal

if TYPE_CHECKING:
    from quantpilot.services.account_service import AccountService
    from quantpilot.services.config_service import ConfigService

logger = logging.getLogger(__name__)


class SignalViewService:
    """API 请求期按账户叠加 is_holding + 仓位建议（不落库，纯视图变换）。"""

    def __init__(
        self,
        repo: MarketDataRepository,
        *,
        account_service: AccountService,
        config_service: ConfigService,
    ) -> None:
        self._repo = repo
        self._account_svc = account_service
        self._cfg = config_service

    async def apply_account_overlay(
        self, signal_dicts: list[dict], account_id: int
    ) -> None:
        """就地为响应 dict 列表叠加 is_holding + suggested_pct（BUY）。

        signal_dicts：SignalResponse.model_dump() 产物列表（含 ts_code / signal_type /
        suggested_pct / is_holding / trade_date 键）。**原地修改**，不返回。
        """
        if not signal_dicts:
            return

        try:
            positions = await self._account_svc.get_positions(account_id)
            account = await self._account_svc.get_account(account_id)

            holding_codes = {p.ts_code for p in positions}
            for d in signal_dicts:
                d["is_holding"] = d["ts_code"] in holding_codes

            # ── 仓位建议：仅 BUY 信号，PositionSizer 按账户总资产/现金/持仓计算 ──
            buy_dicts = [d for d in signal_dicts if d.get("signal_type") == "BUY"]
            if not buy_dicts:
                return

            trade_date = self._resolve_trade_date(signal_dicts)
            market_state = await self._load_market_state(trade_date)
            risk_limits = await self._cfg.get_risk_limits()
            position_cfg = PositionConfig(
                single_pct=risk_limits.single_trade_pct,
                max_single_stock_pct=risk_limits.max_single_stock_pct,
                max_total_pct=risk_limits.max_total_position_pct,
            )

            total_assets = (
                float(account.total_assets)
                if account is not None and account.total_assets is not None
                else 0.0
            )
            cash = (
                float(account.cash)
                if account is not None and account.cash is not None
                else 0.0
            )

            # 从 BUY dict 重建最小 TradeSignal（PositionSizer 只读 signal_type + ts_code）
            buy_signals = [
                TradeSignal(
                    ts_code=d["ts_code"],
                    signal_type="BUY",
                    trade_date=trade_date,
                    score=float(d.get("score") or 0.0),
                )
                for d in buy_dicts
            ]
            sized = PositionSizer().suggest(
                signals=buy_signals,
                account_total_assets=total_assets,
                account_cash=cash,
                current_positions=positions,
                market_state=market_state,
                config=position_cfg,
            )
            pct_map = {s.ts_code: s.suggested_pct for s in sized}
            for d in buy_dicts:
                d["suggested_pct"] = pct_map.get(d["ts_code"])
        except Exception:
            # 【降级说明】账户/市场/配置数据加载失败 → 保留共享信号可见（is_holding
            # 缺省、suggested_pct 不叠加），不 500 整个信号页。恢复条件：数据恢复可读。
            logger.exception(
                "apply_account_overlay_failed: account_id=%s n=%d",
                account_id, len(signal_dicts),
            )

    @staticmethod
    def _resolve_trade_date(signal_dicts: list[dict]) -> date:
        """取信号交易日（用于市场状态查询）；dict 中可能是 date 或 isoformat 字符串。"""
        raw = signal_dicts[0].get("trade_date")
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            return date.fromisoformat(raw)
        return date.today()

    async def _load_market_state(self, trade_date: date) -> MarketStateEnum:
        ms_row = await self._repo.get_latest_market_state(
            before_date=trade_date + timedelta(days=1)
        )
        if ms_row is None:
            return MarketStateEnum.OSCILLATION
        try:
            return MarketStateEnum(ms_row.market_state)
        except ValueError:
            return MarketStateEnum.OSCILLATION
