"""C5 `PluginStrategy`：把用户插件包成标准 `BaseStrategy`（设计 §7.3）。

## 为什么是适配器而不是新一套管线

插件只实现 `compute_raw_factors(universe, data)` 这**一个**函数（`BaseStrategy` 抽象方法的
子集），其余全部继承——于是它天然获得五步管线、`apply_constraints`、因子血缘、ICIR 监控，
兑现 SDD §15.2「新增策略只需实现标准接口，无需修改核心引擎」。

## 两条硬边界

1. **插件不许覆写 `score()` / `apply_constraints`**：适配器只从插件命名空间取
   `compute_raw_factors` 这一个名字（沙箱侧也只调它），插件里同名的 `apply_constraints`
   永远不会被接进来——否则插件可以绕过硬约束（C1-1 那条：约束的唯一落点在基类）。
2. **插件看不到 `MarketSnapshot` 原件**：只喂白名单键、且值必须是 pandas 结构或标量
   （`_plugin_payload`）。原样丢整个快照进去，插件就能顺着里面的对象往外爬；SDD §15.2
   「只能通过系统提供的标准数据接口获取数据」正是这个意思。

## 失败语义（C-4）

插件跑挂 / 被拒 / 超时 → 返回**全 NaN** 的因子矩阵（列名取自插件上次成功的列，或退化成
单列），`Scorer.aggregate` 会记 `scorer_strategy_skipped_all_nan` 并跳过该策略。
⚠️ **不置 0**：Z-score 后 0 是横截面均值 = 一张中性分，等于让坏插件参与选股（§4.4）。
每次运行的结果（状态 / 耗时 / 峰值内存 / 能力集 / 错误摘要）留在 `last_run`，供 §7.4 审计落库。
"""
from __future__ import annotations

import logging

import pandas as pd

from quantpilot.engine.sandbox.plugin_runner import PluginRunResult, run_plugin
from quantpilot.engine.strategies.base import (
    DEFAULT_REQUIRED_HISTORY_DAYS,
    BaseStrategy,
    MarketSnapshot,
)

logger = logging.getLogger(__name__)

# 允许透传给插件的快照键。**加键前先问**：这个值是 pandas 结构或标量吗？
# 不是就别加（`_plugin_payload` 会把非白名单类型的值直接丢掉，不是报错——
# 报错会让一个无关的新键把所有插件打挂）。
PAYLOAD_KEYS: tuple[str, ...] = (
    "trade_date", "adj_prices", "daily_quotes", "financials",
    "index_adj_prices", "market_cap", "f_score", "money_flow", "stock_info",
)
_PAYLOAD_TYPES = (pd.DataFrame, pd.Series, int, float, str, type(None))


def _plugin_payload(market_data: MarketSnapshot) -> dict[str, object]:
    """从快照里挑出可交给插件的部分（白名单键 × 白名单类型）。"""
    out: dict[str, object] = {}
    for key in PAYLOAD_KEYS:
        if key not in market_data:
            continue
        value = market_data[key]  # type: ignore[literal-required]
        if isinstance(value, _PAYLOAD_TYPES):
            out[key] = value
    # date 类不在 _PAYLOAD_TYPES 里（它不是标量数值），单独转成 ISO 字符串传
    td = market_data.get("trade_date")
    if td is not None and not isinstance(td, str):
        out["trade_date"] = str(td)
    return out


class PluginStrategy(BaseStrategy):
    """用户插件的标准策略外衣。`name` / `display_name` 由调用方（Service）给定。"""

    def __init__(
        self,
        *,
        name: str,
        display_name: str,
        source: str,
        timeout_s: float = 300.0,
        memory_mb: int = 100,
        required_history_days: int = DEFAULT_REQUIRED_HISTORY_DAYS,
        allow_without_memory_limit: bool = False,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.weights = {}
        self._source = source
        self._timeout_s = timeout_s
        self._memory_mb = memory_mb
        self._required_history_days = required_history_days
        self._allow_without_memory_limit = allow_without_memory_limit
        self._last_run: PluginRunResult | None = None
        self._last_columns: list[str] = []

    @property
    def required_history_days(self) -> int:
        return self._required_history_days

    @property
    def last_run(self) -> PluginRunResult | None:
        """上一次执行的沙箱结果（供 §7.4 `strategy_plugin_audit` 落库）。"""
        return self._last_run

    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        result = run_plugin(
            self._source, universe, _plugin_payload(market_data),
            timeout_s=self._timeout_s, memory_mb=self._memory_mb,
            allow_without_memory_limit=self._allow_without_memory_limit,
        )
        self._last_run = result
        if not result.ok or result.factors is None:
            logger.warning(
                "plugin_strategy_failed name=%s status=%s duration_ms=%s error=%s",
                self.name, result.exit_status, result.duration_ms, result.error,
            )
            cols = self._last_columns or ["plugin_factor"]
            return pd.DataFrame(float("nan"), index=universe, columns=cols)

        factors = result.factors
        self._last_columns = [str(c) for c in factors.columns]
        # fallthrough：下面填 weights 后返回
        # `weights` 只是登记（生产评分因子等权，见 BaseStrategy.weights 那段）：
        # 按等权填，让 lineage / 旧 `score()` 路径有个自洽的值。
        n = len(factors.columns)
        self.weights = {str(c): 1.0 / n for c in factors.columns} if n else {}
        return factors

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        """插件的 L1 理由文本：只罗列因子值，**不替插件编解释**（C-4：不造内容）。

        插件作者无法提供中文话术，系统也不该猜它的因子含义——故如实列出前三个有值因子的
        名字与数值。该文本只走旧 `score()` 路径（生产五步管线用 `Scorer` 的
        `build_top_drivers`），此处存在是为了满足基类契约并让冷启动路径可读。
        """
        shown = [
            f"{name}={float(value):.4g}"
            for name, value in raw_row.items()
            if pd.notna(value)
        ][:3]
        detail = "、".join(shown) if shown else "无有效因子"
        return f"插件「{self.display_name}」评分 {final_score:.1f}（{detail}）"
