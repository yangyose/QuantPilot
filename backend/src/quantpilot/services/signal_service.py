"""SignalService：信号 CRUD + 过期扫描（Phase 5，Phase 10 §7.1 完整化 generate_for_date）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

from quantpilot.core.exceptions import SignalNotFoundError
from quantpilot.data.factor_ic_repository import FactorICRepository
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.risk import RiskWarning
from quantpilot.engine.signal import TradeSignal
from quantpilot.models.business import Signal as SignalModel
from quantpilot.models.business import SignalScoreSnapshot

if TYPE_CHECKING:
    from quantpilot.services.account_service import AccountService
    from quantpilot.services.config_service import ConfigService
    from quantpilot.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# V1.5-G G-4d-3：持仓派生的**私有 SELL** trigger（账户成本价 / 因子衰减依赖持仓上下文）。
# 共享 pct_above_sell（客观市场分位，管线已产）与 加仓 BUY 不在此集合。
_PRIVATE_SELL_TRIGGERS: frozenset[str] = frozenset(
    {"hard_stop_loss", "short_term_z_drop", "mid_term_icir_flip"}
)


@dataclass
class _GenInputs:
    """generate_for_date / evaluate_private_signals 共享的账户无关输入。"""

    composite_df: pd.DataFrame
    ts_codes: list[str]
    market_state: MarketStateEnum
    snapshot: pd.DataFrame
    signal_cfg: Any
    universe_cfg: Any

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

    V1.5-G G-4d-1（§2 管线与账户解耦）：`generate_for_date` **不再读账户**，只需
    `config_service` 注入即可产出账户无关的共享信号（缺失抛 RuntimeError）。
    `account_service` / `notification_service` 仅供历史兼容保留（可选、当前路径不使用）；
    账户维度叠加（is_holding / 仓位建议 / 私有 SELL）移至 API 请求期的 SignalViewService
    （G-4d-2），回撤主动推送并入 G-4c 每日 Job（G-4d-3）。
    """

    def __init__(
        self,
        repo: MarketDataRepository,
        *,
        account_service: AccountService | None = None,
        config_service: ConfigService | None = None,
        notification_service: NotificationService | None = None,
        factor_ic_repo: FactorICRepository | None = None,
    ) -> None:
        self._repo = repo
        self._account_svc = account_service
        self._cfg = config_service
        # Phase 10 §5.4：注入后 generate_for_date 将把风险告警推送给通知服务
        self._notifier = notification_service
        # Phase 11 §5.2：双重失效止损中"中期 ICIR 翻转"判定走 factor_ic_window_state；
        # FactorICRepository 为无状态 repo，默认 instantiate 一个即可，单测可注入 mock。
        self._factor_ic_repo = factor_ic_repo or FactorICRepository()

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

            factor_winsorized = None
            factor_neutralized = None
            factor_orthogonal = None
            if sig.ts_code in composite_df.index:
                row_data = composite_df.loc[sig.ts_code]
                composite_score = _safe_float(row_data.get("composite_score", sig.score))
                trend_score = _safe_float(row_data.get("trend_score"))
                reversion_score = _safe_float(row_data.get("reversion_score"))
                momentum_score = _safe_float(row_data.get("momentum_score"))
                value_score = _safe_float(row_data.get("value_score"))
                market_state = row_data.get("market_state")
                # Phase 12 P12 评审 P1-4：5 步管线产物落 signal_score_snapshot
                factor_winsorized = row_data.get("factor_winsorized")
                factor_neutralized = row_data.get("factor_neutralized")
                factor_orthogonal = row_data.get("factor_orthogonal")
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
                # Phase 12 §3.1.2：3 个新 JSONB 列写入（修 P12 评审 P1-4）
                "factor_winsorized": factor_winsorized,
                "factor_neutralized": factor_neutralized,
                "factor_orthogonal": factor_orthogonal,
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

    async def get_latest_signals(
        self,
        signal_type: str | None = None,
        status: str | None = None,
    ) -> tuple[list[SignalModel], date | None]:
        """返回"最新有信号的交易日"的信号列表 + 该交易日。

        信号是收盘后每日一次产出，首页/信号页缺省查字面今天在盘中（17:30 批之前）、
        周末、节假日必然为空——这是无意义的空态。缺省回退到最近一个有信号的交易日
        （与原始规格 spec_v0.1「首页展示最新信号列表」一致）。

        库中无任何信号时返回 ([], None)。调用方据此把响应的 trade_date 设为 None。
        """
        latest = await self._repo.get_latest_signal_date()
        if latest is None:
            return [], None
        signals = await self._repo.get_signals_by_date(latest, signal_type, status)
        return signals, latest

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
        market_state: MarketStateEnum,
    ) -> dict[str, dict]:
        """Phase 11 §5.2：为每个持仓预计算短期 z 降幅 + 中期 ICIR 翻转标志。

        - 短期：从 signal_score_snapshot.factor_orthogonal JSONB 取昨日 vs 今日
          核心贡献策略（取 score_breakdown_raw.contribution 降序首位）的
          z_orthogonal_normalized 差值；昨日缺失 → 不计算
        - 中期：查 factor_ic_window_state 当日 ``state=market_state`` 维度最近 2 行
          聚合行，本月 ICIR < 0 且上月 ICIR ≥ 0 → 标记翻转；任一缺失 → False

        本方法返回 ``{ts_code: {"short_term_z_drop_value": float|None,
        "mid_term_icir_flipped": bool}}``。SignalGenerator 内查表，缺键不触发。

        【V1.0 简化】Phase 11 §5.2 完整实现需访问 signal_score_snapshot +
        factor_ic_window_state 两张表；为保持 SignalService 路径单一职责，本方法
        在数据缺失时静默返回空字典，不抛异常（自然降级）。

        【P1-1 修订（2026-05-19 实施评审）】两段 raw SQL 改为 Repository 方法：
        - 短期路径：``self._repo.get_recent_score_snapshots_for_holdings``
        - 中期路径：``self._factor_ic_repo.get_recent_aggregates``（已存在的
          通用方法，按 state 维度过滤）
        消除 Phase 7 C-02 违反（Service 层禁止 raw SQL 绕 Repository）。
        """
        if not holdings:
            return {}

        # 仅对真正持仓的 ts_code 查询，避免无意义 IO
        holding_codes = [p.ts_code for p in holdings]
        out: dict[str, dict] = {}

        # ─── 短期 z 降幅：今日 vs 昨日 signal_score_snapshot.factor_orthogonal ───
        try:
            rows = await self._repo.get_recent_score_snapshots_for_holdings(
                holding_codes, trade_date,
            )
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

        # ─── 中期 ICIR 翻转：factor_ic_window_state state=market_state 维度近 2 行 ───
        # V1.0 简化：strategy=factor 名；查 4 个策略中"核心策略"（用 top_strategy 同源）。
        market_state_str = market_state.value
        for code in holding_codes:
            state_info = out.get(code, {})
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
                ic_rows = await self._factor_ic_repo.get_recent_aggregates(
                    self._repo.session,
                    strategy=top_strategy,
                    factor=top_strategy,  # V1.0 简化：strategy=factor 名
                    state=market_state_str,
                    as_of=trade_date,
                    limit=2,
                )
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
        """从 candidate_pool 快照生成当日**共享**信号（Pipeline CP3 调用路径）。

        V1.5-G G-4d-1（§2 管线与账户解耦）：本函数**不再读账户**，产出账户无关的
        共享信号（BUY 候选 + 客观 pct_above_sell SELL）。仓位建议 / 集中度 BLOCK /
        持仓私有 SELL / 回撤 RISK_WARN 移 API 请求期按用户账户叠加（G-4d-2）+
        每日 Job（G-4d-3）。

        数据流：
        1. 读取 candidate_pool（按 composite_score DESC），为空直接返回 []
        2. 加载 market_state_history 最近一行（缺失 → OSCILLATION）
        3. 加载 snapshot_quotes（close/amount/limit_up/is_suspended/sw_industry_l1）
        4. 加载 ConfigService signal_params / universe_params
        5. 运行 SignalGenerator（current_positions=[]）→ 共享 TradeSignal
        6. 调用 self.save() 持久化
        7. 返回本次写入的 Signal ORM 列表

        `config_service` 必须注入，否则抛 RuntimeError。
        """
        # V1.5-G G-4d-1（§2 管线与账户解耦）：generate_for_date 不再读账户，只需 config_service。
        if self._cfg is None:
            raise RuntimeError(
                "generate_for_date 需要注入 config_service（Phase 10 §7.1 去除降级）"
            )

        from quantpilot.engine.signal import SignalGenerator

        inputs = await self._load_generation_inputs(trade_date)
        if inputs is None:
            logger.info(
                "generate_for_date_skip: no in_pool entries for %s", trade_date
            )
            return []

        # ── Engine 层链路（账户无关）──────────────────────────────────────────────
        # current_positions=[] + holding_signal_states={}：无持仓上下文，SignalGenerator
        # 只产共享信号（BUY 候选 + 客观 pct_above_sell SELL）。持仓派生的私有信号
        # （hard_stop_loss / 加仓 / 短中期翻转）与仓位建议移 API 请求期按用户账户叠加
        # （G-4d-2 SignalViewService）+ 每日 Job 按账户评估通知（G-4d-3）。
        generator = SignalGenerator(
            signal_cfg=inputs.signal_cfg, universe_cfg=inputs.universe_cfg
        )
        trade_signals = generator.generate(
            composite_scores=inputs.composite_df,
            current_positions=[],
            market_state=inputs.market_state,
            snapshot_quotes=inputs.snapshot,
            trade_date=trade_date,
            holding_signal_states={},
        )

        generated_codes = {s.ts_code for s in trade_signals}
        await self.save(trade_signals, trade_date, inputs.composite_df)

        all_today = await self._repo.get_signals_by_date(
            trade_date, signal_type=None, status=None
        )
        saved = [s for s in all_today if s.ts_code in generated_codes]
        logger.info(
            "generate_for_date_done: trade_date=%s raw=%d saved=%d",
            trade_date, len(trade_signals), len(saved),
        )

        # Phase 13 §3.1.2 埋点：按 signal_type 聚合计数
        from quantpilot.core.metrics import SIGNALS_GENERATED
        type_counts: dict[str, int] = {}
        for s in saved:
            type_counts[s.signal_type] = type_counts.get(s.signal_type, 0) + 1
        for stype, cnt in type_counts.items():
            SIGNALS_GENERATED.labels(type=stype).inc(cnt)

        return saved

    async def _load_generation_inputs(self, trade_date: date) -> _GenInputs | None:
        """加载 SignalGenerator 的**账户无关**输入（候选池评分 / 市场状态 / 行情 / 配置）。

        generate_for_date（共享信号）与 evaluate_private_signals（按账户私有 SELL）复用，
        保证两条路径吃完全相同的评分上下文。候选池为空 → 返回 None。
        `self._cfg` 须已注入（调用方保证）。
        """
        pool_entries = await self._repo.get_pool(trade_date=trade_date)
        if not pool_entries:
            return None

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
                    # Phase 12 §3.1.2：5 步管线产物快照透传给 _build_snapshot_rows，
                    # 让 signal_score_snapshot 3 列真写入（修 P12 评审 P1-4）
                    "factor_winsorized": getattr(e, "factor_winsorized", None),
                    "factor_neutralized": getattr(e, "factor_neutralized", None),
                    "factor_orthogonal": getattr(e, "factor_orthogonal", None),
                }
                for e in pool_entries
            ]
        ).set_index("ts_code")

        ms_row = await self._repo.get_latest_market_state(
            before_date=trade_date + timedelta(days=1)
        )
        try:
            market_state = (
                MarketStateEnum(ms_row.market_state)
                if ms_row
                else MarketStateEnum.OSCILLATION
            )
        except ValueError:
            market_state = MarketStateEnum.OSCILLATION

        snapshot = await self._repo.get_snapshot_quotes(ts_codes, trade_date)
        signal_cfg = await self._cfg.get_signal_params()
        universe_cfg = await self._cfg.get_universe_params()

        return _GenInputs(
            composite_df=composite_df,
            ts_codes=ts_codes,
            market_state=market_state,
            snapshot=snapshot,
            signal_cfg=signal_cfg,
            universe_cfg=universe_cfg,
        )

    async def evaluate_private_signals(
        self, trade_date: date, positions: list
    ) -> list[TradeSignal]:
        """V1.5-G G-4d-3：为某账户持仓评估**私有 SELL**（不落库，供每日 Job 通知）。

        管线只产账户无关的共享信号（G-4d-1）；每日 Job 按账户重跑 SignalGenerator
        评估持仓派生的私有 SELL（hard_stop_loss / short_term_z_drop / mid_term_icir_flip）。
        复用 `_load_generation_inputs` + SignalGenerator——止损逻辑**单一实现源**，
        杜绝在 Job 里另写一份 hard_stop_loss 判定造成语义漂移。

        过滤规则：仅保留 ts_code 在持仓集合内、且 trigger_reason ∈ 私有 SELL 三类的信号。
        共享 pct_above_sell（管线已产）与 加仓 BUY 被排除。空持仓 / 空候选池 → []。
        """
        if not positions:
            return []
        if self._cfg is None:
            raise RuntimeError("evaluate_private_signals 需要注入 config_service")

        from quantpilot.engine.signal import SignalGenerator

        inputs = await self._load_generation_inputs(trade_date)
        if inputs is None:
            return []

        holding_signal_states = await self._compute_holding_signal_states(
            holdings=positions,
            trade_date=trade_date,
            market_state=inputs.market_state,
        )
        generator = SignalGenerator(
            signal_cfg=inputs.signal_cfg, universe_cfg=inputs.universe_cfg
        )
        signals = generator.generate(
            composite_scores=inputs.composite_df,
            current_positions=positions,
            market_state=inputs.market_state,
            snapshot_quotes=inputs.snapshot,
            trade_date=trade_date,
            holding_signal_states=holding_signal_states,
        )
        holding_codes = {p.ts_code for p in positions}
        return [
            s
            for s in signals
            if s.ts_code in holding_codes and s.trigger_reason in _PRIVATE_SELL_TRIGGERS
        ]
