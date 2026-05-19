"""SignalService：信号 CRUD + 过期扫描（Phase 5，Phase 10 §7.1 完整化 generate_for_date）。"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from quantpilot.core.exceptions import SignalNotFoundError
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.risk import RiskWarning
from quantpilot.engine.signal import TradeSignal
from quantpilot.models.business import Signal as SignalModel
from quantpilot.models.business import SignalScoreSnapshot

if TYPE_CHECKING:
    from quantpilot.services.account_service import AccountService
    from quantpilot.services.config_service import ConfigService
    from quantpilot.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# 合法状态转换表（SDD §9.4）
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"VIEWED", "ACTED"},
    "VIEWED": {"ACTED"},
    "ACTED": set(),
    "EXPIRED": set(),
    "SUPERSEDED": set(),
}


class SignalService:
    """信号 CRUD + 过期扫描（Engine 层负责具体计算）。

    Phase 10 §7.1：`generate_for_date` 完整化需要 `account_service` + `config_service` 注入；
    未提供时仅支持 CRUD 方法（save/get_*/update_status/expire_old_signals），
    调用 `generate_for_date` 会抛 RuntimeError（去除 V1.0 降级）。
    """

    def __init__(
        self,
        repo: MarketDataRepository,
        *,
        account_service: AccountService | None = None,
        config_service: ConfigService | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._repo = repo
        self._account_svc = account_service
        self._cfg = config_service
        # Phase 10 §5.4：注入后 generate_for_date 将把风险告警推送给通知服务
        self._notifier = notification_service

    async def save(
        self,
        signals: list[TradeSignal],
        trade_date: date,
        composite_df: pd.DataFrame | None = None,
        risk_warnings: list[RiskWarning] | None = None,
    ) -> int:
        """批量 upsert 信号（ON CONFLICT ts_code, trade_date, signal_type）。

        处理流程：
        1. BLOCK 级风险告警：从 signals 中移除对应 BUY 信号，不持久化。
        2. WARN 级风险告警：将 message 追加到对应信号的 reason 字段。
        3. 若提供 composite_df，则为每个未被阻断的信号写入 SignalScoreSnapshot。

        返回实际 upsert 行数（BLOCK 信号已移除后的数量）。
        """
        if not signals:
            return 0

        warnings = risk_warnings or []

        # 收集 BLOCK 级告警对应的 ts_code（移除 BUY 信号）
        blocked_codes: set[str] = {
            w.ts_code for w in warnings if w.severity == "BLOCK"
        }
        # 收集 WARN 级告警的 message（追加到 reason）
        warn_messages: dict[str, list[str]] = {}
        for w in warnings:
            if w.severity == "WARN" and w.ts_code != "ACCOUNT":
                warn_messages.setdefault(w.ts_code, []).append(w.message)
        # 账户级 WARN 告警追加到所有 BUY 信号
        account_warns = [
            w.message for w in warnings
            if w.ts_code == "ACCOUNT" and w.severity == "WARN"
        ]

        # 过滤掉 BLOCK 的 BUY 信号
        filtered: list[TradeSignal] = []
        for sig in signals:
            if sig.signal_type == "BUY" and sig.ts_code in blocked_codes:
                logger.info(
                    "signal_blocked: ts_code=%s trade_date=%s",
                    sig.ts_code, trade_date,
                )
                continue
            filtered.append(sig)

        if not filtered:
            return 0

        # 构建 DB 行
        rows: list[dict] = []
        for sig in filtered:
            reason_parts = [sig.reason] if sig.reason else []
            if sig.ts_code in warn_messages:
                reason_parts.extend(warn_messages[sig.ts_code])
            if sig.signal_type == "BUY" and account_warns:
                reason_parts.extend(account_warns)
            rows.append({
                "ts_code": sig.ts_code,
                "signal_type": sig.signal_type,
                "trade_date": trade_date,
                "score": sig.score,
                "suggested_pct": sig.suggested_pct,
                "suggested_price_low": sig.suggested_price_low,
                "suggested_price_high": sig.suggested_price_high,
                "stop_loss_price": sig.stop_loss_price,
                "signal_strength": sig.signal_strength,
                "liquidity_note": sig.liquidity_note,
                "t1_warning": sig.t1_warning if sig.signal_type == "BUY" else None,
                "reason": " | ".join(reason_parts) if reason_parts else None,
                "status": "NEW",
                # Phase 11 §5 新列：TradeSignal 透传到 Signal ORM
                "composite_z": sig.composite_z,
                "composite_pct_in_market": sig.composite_pct_in_market,
                "trigger_reason": sig.trigger_reason,
            })

        returned_signals = await self._repo.upsert_signals(rows)
        count = len(returned_signals)

        # 写入 SignalScoreSnapshot（数据血缘，C-02 修复）
        if composite_df is not None and not composite_df.empty:
            id_map = {(r["ts_code"], r["signal_type"]): r["id"] for r in returned_signals}
            snapshot_rows = self._build_snapshot_rows(filtered, trade_date, composite_df, id_map)
            if snapshot_rows:
                await self._repo.upsert_signal_snapshots(snapshot_rows)

        return count

    def _build_snapshot_rows(
        self,
        signals: list[TradeSignal],
        trade_date: date,
        composite_df: pd.DataFrame,
        id_map: dict[tuple, int],
    ) -> list[dict]:
        """从 composite_df 和 id_map 构建 SignalScoreSnapshot 行（C-02 修复）。

        id_map: {(ts_code, signal_type): signal_id}，由 upsert_signals RETURNING 子句提供。
        composite_df: index=ts_code，可选列 composite_score/trend_score/reversion_score/
                      momentum_score/value_score/market_state（均可缺失，缺失时为 None）。
        信号的 score_breakdown/raw_factors 来自 TradeSignal 本身（由 SignalGenerator 填充）。
        """
        def _safe_float(val: object) -> float | None:
            if val is None:
                return None
            try:
                v = float(val)  # type: ignore[arg-type]
                return None if pd.isna(v) else v
            except (TypeError, ValueError):
                return None

        rows: list[dict] = []
        for sig in signals:
            signal_id = id_map.get((sig.ts_code, sig.signal_type))
            if signal_id is None:
                continue

            if sig.ts_code in composite_df.index:
                row_data = composite_df.loc[sig.ts_code]
                composite_score = _safe_float(row_data.get("composite_score", sig.score))
                trend_score = _safe_float(row_data.get("trend_score"))
                reversion_score = _safe_float(row_data.get("reversion_score"))
                momentum_score = _safe_float(row_data.get("momentum_score"))
                value_score = _safe_float(row_data.get("value_score"))
                market_state = row_data.get("market_state")
            else:
                composite_score = sig.score
                trend_score = reversion_score = momentum_score = value_score = None
                market_state = None

            rows.append({
                "signal_id": signal_id,
                "trade_date": trade_date,
                "ts_code": sig.ts_code,
                "composite_score": composite_score,
                "trend_score": trend_score,
                "reversion_score": reversion_score,
                "momentum_score": momentum_score,
                "value_score": value_score,
                "market_state": market_state,
                "score_breakdown": sig.score_breakdown,
                "raw_factors": sig.raw_factors,
            })
        return rows

    async def get_today_signals(
        self,
        trade_date: date,
        signal_type: str | None = None,
        status: str | None = None,
    ) -> list[SignalModel]:
        """查询指定日期的信号列表，支持按 signal_type / status 过滤。"""
        return await self._repo.get_signals_by_date(trade_date, signal_type, status)

    async def get_signal_history(
        self,
        ts_code: str | None = None,
        signal_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SignalModel]:
        """查询历史信号（分页），支持多条件过滤。"""
        return await self._repo.get_signal_history(ts_code, signal_type, status, limit, offset)

    async def update_status(
        self,
        signal_id: int,
        new_status: str,
    ) -> SignalModel:
        """更新信号状态（仅允许合法转换）。非法转换抛出 ValueError。"""
        signal = await self._repo.get_signal_by_id(signal_id)
        if signal is None:
            raise SignalNotFoundError(f"Signal {signal_id} not found")

        allowed = _VALID_TRANSITIONS.get(signal.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"非法状态转换：{signal.status} → {new_status}。"
                f"当前状态允许的转换：{allowed or '无'}"
            )

        updated = await self._repo.update_signal_status(signal_id, new_status)
        if updated is None:
            raise SignalNotFoundError(f"Signal {signal_id} not found after update")
        return updated

    async def get_lineage(
        self,
        signal_id: int,
    ) -> tuple[SignalModel, SignalScoreSnapshot | None]:
        """返回信号及其评分快照（若存在）。信号不存在时抛出 ValueError。"""
        signal = await self._repo.get_signal_by_id(signal_id)
        if signal is None:
            raise SignalNotFoundError(f"Signal {signal_id} not found")
        snapshot = await self._repo.get_signal_snapshot(signal_id)
        return signal, snapshot

    async def get_last_buy_signal(
        self,
        ts_code: str,
        as_of_date: date | None = None,
    ) -> SignalModel | None:
        """Phase 10 §5.5：返回某股票最近一条 BUY 信号（供止损预警 Job 读取 stop_loss_price）。

        as_of_date：可选上界（包含），未指定则取全历史。
        """
        return await self._repo.get_last_buy_signal(ts_code, as_of_date)

    async def _compute_holding_signal_states(
        self,
        holdings: list,
        trade_date: date,
    ) -> dict[str, dict]:
        """Phase 11 §5.2：为每个持仓预计算短期 z 降幅 + 中期 ICIR 翻转标志。

        - 短期：从 signal_score_snapshot.factor_orthogonal JSONB 取昨日 vs 今日
          核心贡献策略（取 score_breakdown_raw.contribution 降序首位）的
          z_orthogonal_normalized 差值；昨日缺失 → 不计算
        - 中期：查 factor_ic_window_state 近 ~30 天最近 2 次聚合行，本月 ICIR < 0
          且上月 ICIR ≥ 0 → 标记翻转；任一缺失 → False

        本方法返回 ``{ts_code: {"short_term_z_drop_value": float|None,
        "mid_term_icir_flipped": bool}}``。SignalGenerator 内查表，缺键不触发。

        【V1.0 简化】Phase 11 §5.2 完整实现需访问 signal_score_snapshot +
        factor_ic_window_state 两张表；为保持 SignalService 路径单一职责，本方法
        在数据缺失时静默返回空字典，不抛异常（自然降级）。
        """
        if not holdings:
            return {}

        from datetime import timedelta as _td

        from sqlalchemy import select

        from quantpilot.models.business import (
            FactorICWindowState,
            SignalScoreSnapshot,
        )

        # 仅对真正持仓的 ts_code 查询，避免无意义 IO
        holding_codes = [p.ts_code for p in holdings]
        out: dict[str, dict] = {}

        # ─── 短期 z 降幅：今日 vs 昨日 signal_score_snapshot.factor_orthogonal ───
        # 取每只持仓股最近 2 条 signal_score_snapshot（按 trade_date desc）
        try:
            stmt = (
                select(
                    SignalScoreSnapshot.ts_code,
                    SignalScoreSnapshot.trade_date,
                    SignalScoreSnapshot.factor_orthogonal,
                    SignalScoreSnapshot.score_breakdown,
                )
                .where(
                    SignalScoreSnapshot.ts_code.in_(holding_codes),
                    SignalScoreSnapshot.trade_date <= trade_date,
                )
                .order_by(
                    SignalScoreSnapshot.ts_code.asc(),
                    SignalScoreSnapshot.trade_date.desc(),
                )
            )
            rows = (await self._repo._session.execute(stmt)).all()
        except Exception:
            logger.exception("compute_holding_states_snapshot_query_failed")
            rows = []

        per_code_snapshots: dict[str, list] = {}
        for r in rows:
            per_code_snapshots.setdefault(r.ts_code, []).append(r)

        for code in holding_codes:
            snaps = per_code_snapshots.get(code, [])
            if len(snaps) < 2:
                continue
            today_snap, prev_snap = snaps[0], snaps[1]
            today_orth = today_snap.factor_orthogonal or {}
            prev_orth = prev_snap.factor_orthogonal or {}
            breakdown = today_snap.score_breakdown or {}
            if not today_orth or not prev_orth or not breakdown:
                continue
            # 找核心贡献策略 = score_breakdown.contribution 降序首位
            try:
                top_strategy = max(
                    breakdown.items(),
                    key=lambda kv: float(kv[1].get("contribution", 0.0)),
                )[0]
            except (ValueError, TypeError, KeyError):
                continue
            today_v = today_orth.get(top_strategy, {}).get("z_orthogonal_normalized")
            prev_v = prev_orth.get(top_strategy, {}).get("z_orthogonal_normalized")
            if today_v is None or prev_v is None:
                continue
            out.setdefault(code, {})
            out[code]["short_term_z_drop_value"] = float(prev_v) - float(today_v)

        # ─── 中期 ICIR 翻转：factor_ic_window_state 近 ~30 天最近 2 行聚合 ───
        # V1.0 简化：strategy=factor 名；查 4 个策略中"核心策略"（用 top_strategy 同源）
        # 为简化实现，仅检查 holding 的 top_strategy（来自 signal_score_snapshot）；
        # 无 top_strategy 时跳过（不计入翻转）。
        for code in holding_codes:
            state_info = out.get(code, {})
            # 用 short_term 部分已经查到的 top_strategy；若没有 short_term 数据但需要
            # 中期判定，可再查一次 score_breakdown。
            today_breakdown = None
            snaps = per_code_snapshots.get(code, [])
            if snaps:
                today_breakdown = snaps[0].score_breakdown
            if not today_breakdown:
                continue
            try:
                top_strategy = max(
                    today_breakdown.items(),
                    key=lambda kv: float(kv[1].get("contribution", 0.0)),
                )[0]
            except (ValueError, TypeError, KeyError):
                continue
            try:
                stmt2 = (
                    select(FactorICWindowState)
                    .where(
                        FactorICWindowState.strategy == top_strategy,
                        FactorICWindowState.factor == top_strategy,  # V1.0 简化
                        FactorICWindowState.trade_date <= trade_date,
                        FactorICWindowState.trade_date >= trade_date - _td(days=70),
                        FactorICWindowState.icir.isnot(None),
                    )
                    .order_by(FactorICWindowState.trade_date.desc())
                    .limit(2)
                )
                ic_rows = (await self._repo._session.execute(stmt2)).scalars().all()
            except Exception:
                logger.exception("compute_holding_states_icir_query_failed")
                continue
            if len(ic_rows) < 2:
                continue
            this_icir = float(ic_rows[0].icir) if ic_rows[0].icir is not None else None
            prev_icir = float(ic_rows[1].icir) if ic_rows[1].icir is not None else None
            if this_icir is None or prev_icir is None:
                continue
            flipped = (this_icir < 0) and (prev_icir >= 0)
            state_info["mid_term_icir_flipped"] = flipped
            out[code] = state_info

        return out

    async def expire_old_signals(
        self,
        as_of_date: date,
        ttl_days: int = 3,
    ) -> int:
        """将 (NEW/VIEWED) 状态且 trade_date < as_of_date - ttl_days 的信号改为 EXPIRED。
        由 DailyPipeline 在每日数据入库完成后调用（Phase 7 集成）。
        返回过期信号数量。
        """
        cutoff = as_of_date - timedelta(days=ttl_days)
        count = await self._repo.expire_signals_before(cutoff)
        logger.info(
            "expire_old_signals: as_of=%s cutoff=%s expired=%d",
            as_of_date, cutoff, count,
        )
        return count

    async def generate_for_date(self, trade_date: date) -> list[SignalModel]:
        """从 candidate_pool 快照生成当日信号（Pipeline CP3 调用路径，Phase 10 §7.1 完整化）。

        数据流：
        1. 读取 candidate_pool（按 composite_score DESC），为空直接返回 []
        2. 加载 AccountService 持仓 / 默认账户总资产现金
        3. 加载 market_state_history 最近一行（缺失 → OSCILLATION）
        4. 加载 snapshot_quotes（close/amount/limit_up/is_suspended/sw_industry_l1）
        5. 加载 ConfigService signal_params / universe_params / risk_limits
        6. 运行 SignalGenerator → PositionSizer → RiskChecker 全链路
        7. 调用 self.save() 持久化（BLOCK 告警移除、WARN 附加到 reason）
        8. 返回本次写入的 Signal ORM 列表

        Phase 10：移除 V1.0 降级，`account_service` 和 `config_service` 必须注入；
        否则抛 RuntimeError。
        """
        if self._account_svc is None:
            raise RuntimeError(
                "generate_for_date 需要注入 account_service（Phase 10 §7.1 去除降级）"
            )
        if self._cfg is None:
            raise RuntimeError(
                "generate_for_date 需要注入 config_service（Phase 10 §7.1 去除降级）"
            )

        from quantpilot.engine.market_state import MarketStateEnum
        from quantpilot.engine.position import PositionConfig, PositionSizer
        from quantpilot.engine.risk import RiskChecker
        from quantpilot.engine.signal import SignalGenerator

        pool_entries = await self._repo.get_pool(trade_date=trade_date)
        if not pool_entries:
            logger.info(
                "generate_for_date_skip: no in_pool entries for %s", trade_date
            )
            return []

        ts_codes = [e.ts_code for e in pool_entries]

        # ── 构建 composite_df（SignalGenerator 输入 + SignalScoreSnapshot 血缘）────
        # Phase 11 §5：携带新 6 列（composite_z / composite_pct_in_market /
        # score_breakdown_raw / weights_source / hysteresis_status），由
        # SignalGenerator 走分位阈值主路径。旧 candidate_pool 行新列 NULL → 自然回 V1.0-r5 旧路径。
        composite_df = pd.DataFrame(
            [
                {
                    "ts_code": e.ts_code,
                    "composite_score": (
                        float(e.composite_score) if e.composite_score is not None else None
                    ),
                    "trend_score": (
                        float(e.trend_score) if e.trend_score is not None else None
                    ),
                    "reversion_score": (
                        float(e.reversion_score) if e.reversion_score is not None else None
                    ),
                    "momentum_score": (
                        float(e.momentum_score) if e.momentum_score is not None else None
                    ),
                    "value_score": (
                        float(e.value_score) if e.value_score is not None else None
                    ),
                    "market_state": e.market_state,
                    # Phase 11 新列
                    "composite_z": (
                        float(e.composite_z)
                        if getattr(e, "composite_z", None) is not None
                        else None
                    ),
                    "composite_pct_in_market": (
                        float(e.composite_pct_in_market)
                        if getattr(e, "composite_pct_in_market", None) is not None
                        else None
                    ),
                    "weights_source": getattr(e, "weights_source", None),
                    "score_breakdown": getattr(e, "score_breakdown_raw", None),
                }
                for e in pool_entries
            ]
        ).set_index("ts_code")

        # ── 依赖加载（持仓 / 账户 / 市场状态 / 行情 / 配置）─────────────────────
        positions = await self._account_svc.get_all_positions()
        account = await self._account_svc.get_default_account()
        total_assets = (
            float(account.total_assets) if account and account.total_assets else 0.0
        )
        cash = float(account.cash) if account and account.cash else 0.0

        ms_row = await self._repo.get_latest_market_state(
            before_date=trade_date + timedelta(days=1)
        )
        try:
            market_state = (
                MarketStateEnum(ms_row.market_state) if ms_row else MarketStateEnum.OSCILLATION
            )
        except ValueError:
            market_state = MarketStateEnum.OSCILLATION

        snapshot = await self._repo.get_snapshot_quotes(ts_codes, trade_date)

        signal_cfg = await self._cfg.get_signal_params()
        universe_cfg = await self._cfg.get_universe_params()
        risk_limits = await self._cfg.get_risk_limits()

        # ── Phase 11 §5.2：持仓信号状态（短期 z 降幅 / 中期 ICIR 翻转）预计算 ────
        # 旧 candidate_pool 无 factor_orthogonal / factor_ic_window_state 数据时，
        # 本函数返回空 dict —— SignalGenerator 自然降级，不影响 hard_stop_loss 等其它条件。
        holding_signal_states = await self._compute_holding_signal_states(
            holdings=positions,
            trade_date=trade_date,
        )

        # ── Engine 层链路 ──────────────────────────────────────────────────────
        generator = SignalGenerator(signal_cfg=signal_cfg, universe_cfg=universe_cfg)
        trade_signals = generator.generate(
            composite_scores=composite_df,
            current_positions=positions,
            market_state=market_state,
            snapshot_quotes=snapshot,
            trade_date=trade_date,
            holding_signal_states=holding_signal_states,
        )

        # PositionConfig.min_cash_pct 保留默认（RiskLimitsConfig 未含该字段）
        position_cfg = PositionConfig(
            single_pct=risk_limits.single_trade_pct,
            max_single_stock_pct=risk_limits.max_single_stock_pct,
            max_total_pct=risk_limits.max_total_position_pct,
        )
        sizer = PositionSizer()
        sized_signals = sizer.suggest(
            signals=trade_signals,
            account_total_assets=total_assets,
            account_cash=cash,
            current_positions=positions,
            market_state=market_state,
            config=position_cfg,
        )

        checker = RiskChecker(risk_limits=risk_limits)
        # stock_industry：从 snapshot_quotes 读取 sw_industry_l1 列
        if "sw_industry_l1" in snapshot.columns:
            industry_df = snapshot[["sw_industry_l1"]].copy()
        else:
            industry_df = pd.DataFrame()

        # V1.0 整改 Batch 2 — B2-1：传入账户当前最大回撤 + 阈值，触发 SDD §10.2 WARN 级告警
        # （此前漏传 → RiskChecker 内部跳过 drawdown 检查，回撤告警从未触发）
        current_drawdown: float | None = None
        if account is not None:
            current_drawdown = await self._account_svc.get_current_drawdown(account.id)

        warnings = checker.check(
            signals=sized_signals,
            current_positions=positions,
            account_total_assets=total_assets,
            stock_industry=industry_df,
            account_max_drawdown_pct=current_drawdown,
            max_drawdown_pct=risk_limits.max_drawdown_pct,
        )

        generated_codes = {s.ts_code for s in sized_signals}
        await self.save(sized_signals, trade_date, composite_df, warnings)

        # Phase 10 §5.4：风险告警推送（best-effort，逐条异常隔离）
        if self._notifier is not None and warnings:
            for w in warnings:
                try:
                    await self._notifier.notify_risk_warn(
                        event_type=w.warning_type,
                        message=w.message,
                        payload={
                            "ts_code": w.ts_code,
                            "severity": w.severity,
                            "trade_date": str(trade_date),
                        },
                    )
                except Exception:
                    logger.warning(
                        "notify_risk_warn_failed: ts_code=%s type=%s",
                        w.ts_code, w.warning_type, exc_info=True,
                    )

        all_today = await self._repo.get_signals_by_date(
            trade_date, signal_type=None, status=None
        )
        saved = [s for s in all_today if s.ts_code in generated_codes]
        logger.info(
            "generate_for_date_done: trade_date=%s raw=%d saved=%d warnings=%d",
            trade_date, len(trade_signals), len(saved), len(warnings),
        )
        return saved
