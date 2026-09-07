"""F-SI：配置项是否**真的被消费**——不是「存进去了」，是「改了结果会变」。

## 为什么单独一个文件

`test_engine_config_injection.py` 那套测的是**存储**：

    t = TrendStrategy(config=TrendStrategyConfig(ma_short=5))
    assert t._cfg.ma_short == 5      # ← 字段从未被读取，这条照样绿

2026-08-27 专项排查（`docs/reviews/silent_ignore_audit_2026-08-27.md`）发现
`config_defaults` 有 12 个字段**零引用**，被模块级同值常量 / 写死字面量架空，
而三个配置类**都对用户可编辑**——用户改了会存库、界面显示已保存、代码永不读取。
「旋钮拧了没反应且不报错」是本项目最贵的缺陷族（CLAUDE.md §4.11）。

## 本文件的判据

每个字段一条「**改参数 → 结果必须变**」（§4.4 对 pandas_ta `bbands(std=)` 那条的判据）。
断言「不抛异常」或「值存下来了」都测不出静默失效。

⚠️ 2026-09-07 补：审计漏了 `TrendStrategyConfig.ma_short` / `ma_long`，
漏的原因**与它自己记下的盲点完全相同**——`MarketStateConfig` 有同名字段且被消费，
纯 grep 判为「已引用」。故零引用实为 **14 项**。这两项的接线方式需要设计决策
（因子用的是 5/10/20/60 四档 MA 阶梯，两个配置字段表达不了），本批不猜，留待拍板。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from quantpilot.core.config_defaults import (
    DEFAULT_FACTOR_MONITOR,
    FactorMonitorConfig,
    ScoringPipelineConfig,
    TrendStrategyConfig,
)
from quantpilot.engine.strategies.trend import TrendStrategy


def _price_snapshot(
    n: int = 120, *, period: float = 21.0, drift: float = 0.0,
) -> tuple[pd.Index, dict]:
    """构造价格序列。

    ⚠️ `period` 默认 21 不是随手取的：`macd_signal` 只影响 DEA，而因子被离散成
    {0, 0.5, 1.0} 三档 —— 多数序列上改 signal 落在同一档，测试会**假绿**。
    2026-09-07 扫了 84 组 (amp, period, drift)，只有 `period=21` 那几组能让
    `signal 9 → 2` 跨档（0.5 → 1.0）。这正是 §4.11「测试输入比现实更配合」的
    反面用法：要证明参数被消费，就得**主动找**那个能区分的输入，
    而不是拿手边的序列试一次没变就以为参数没接线（或反过来以为接线了）。
    """
    universe = pd.Index(["000001.SZ"], name="ts_code")
    cols: list[date] = []
    d = date(2025, 1, 1)
    while len(cols) < n:
        if d.weekday() < 5:
            cols.append(d)
        d += timedelta(days=1)
    t = np.arange(n, dtype=float)
    close = 100 + 15 * np.sin(t / period) + t * drift
    adj = pd.DataFrame([close], columns=cols, index=universe)
    return universe, {"trade_date": cols[-1], "adj_prices": adj}


class TestTrendMacdParamsConsumed:
    """`TrendStrategyConfig.macd_fast/slow/signal` 目前被 `trend.py` 写死的
    `ta.macd(close, fast=12, slow=26, signal=9)` 架空。"""

    @staticmethod
    def _macd(cfg: TrendStrategyConfig | None, **kw) -> float:
        universe, snap = _price_snapshot(**kw)
        df = TrendStrategy(config=cfg).compute_raw_factors(universe, snap)
        return float(df.loc["000001.SZ", "macd_signal"])

    def test_macd_fast_slow_change_result(self) -> None:
        base = self._macd(None, period=9.0, drift=0.05)
        fast = self._macd(
            TrendStrategyConfig(macd_fast=3, macd_slow=7, macd_signal=3),
            period=9.0, drift=0.05,
        )
        assert not (np.isnan(base) and np.isnan(fast)), "构造的价格序列没算出 MACD，测试无效"
        assert base != fast, "改 macd_fast/slow 结果没变 —— 参数被写死字面量架空"

    def test_macd_signal_alone_changes_result(self) -> None:
        """单独钉 `macd_signal`：它是审计的**假阴性**项（撞了同名因子权重键）。

        只钉 fast/slow 的话，signal 继续写死也能过。
        """
        base = self._macd(None)
        sig = self._macd(TrendStrategyConfig(macd_signal=2))
        assert base != sig, "改 macd_signal 结果没变 —— 该参数仍被写死"


class TestFactorMonitorConfigConsumed:
    """`FactorMonitorConfig` 9 项被 `factor_monitor_service` 顶部同值模块级常量架空。

    判据不能是「服务把 config 存下来了」，必须是**行为随配置变化**。
    这里取 `state_min_samples`：它决定 `rolling_icir_state` 在样本不足时返回 None，
    是这 9 项里唯一能在无 DB 的单测里直接观测到分支切换的。
    """

    @staticmethod
    def _svc(cfg: FactorMonitorConfig | None):
        from unittest.mock import MagicMock

        from quantpilot.services.factor_monitor_service import FactorMonitorService

        return FactorMonitorService(MagicMock(), MagicMock(), config=cfg)

    async def test_service_accepts_config(self) -> None:
        svc = self._svc(FactorMonitorConfig(state_min_samples=99))
        assert (await svc._factor_config()).state_min_samples == 99

    async def test_default_config_is_the_dataclass_not_a_parallel_constant(self) -> None:
        """默认必须来自 `DEFAULT_FACTOR_MONITOR`，不是另一份平行常量。

        平行副本是这族缺陷的载体：两边今天数值相同，任一侧被改就静默分叉。
        """
        assert await self._svc(None)._factor_config() is DEFAULT_FACTOR_MONITOR

    async def test_config_service_provider_is_actually_consumed(self) -> None:
        """传 `config_service` 时必须**真的去读它**，而不是回落默认值。

        ⚠️ 这条是惰性设计的关键判据：工厂只传 provider、不读值，所以
        「工厂传了 provider」并不证明配置生效——服务端不去 await 它，
        用户配置照样无效，缺陷只是从工厂挪到了服务。
        """
        from unittest.mock import AsyncMock, MagicMock

        from quantpilot.services.factor_monitor_service import FactorMonitorService

        provider = MagicMock()
        provider.get_factor_monitor_params = AsyncMock(
            return_value=FactorMonitorConfig(state_min_samples=7)
        )
        provider.get_scoring_pipeline_params = AsyncMock(
            return_value=ScoringPipelineConfig(hysteresis_enabled=False)
        )
        svc = FactorMonitorService(MagicMock(), MagicMock(), config_service=provider)
        assert (await svc._factor_config()).state_min_samples == 7
        assert (await svc._scoring_config()).hysteresis_enabled is False

    async def test_provider_is_not_hit_at_construction(self) -> None:
        """构造时**不得**触达 provider —— 构造即 IO 会让 FastAPI 依赖在鉴权前打 DB。

        实证：首版工厂在构造时 await 配置读取，`/factor-quality` 的 401 用例
        直接变成 ConnectionRefused，假 session 的单测全炸。
        """
        from unittest.mock import AsyncMock, MagicMock

        from quantpilot.services.factor_monitor_service import FactorMonitorService

        provider = MagicMock()
        provider.get_factor_monitor_params = AsyncMock(return_value=DEFAULT_FACTOR_MONITOR)
        FactorMonitorService(MagicMock(), MagicMock(), config_service=provider)
        assert provider.get_factor_monitor_params.await_count == 0

    def test_module_level_parallel_constants_are_gone(self) -> None:
        """平行常量必须删除，不能「加了 config 但代码还读常量」。

        ⚠️ 这条是本组真正的判据：只加构造参数、内部照旧读 `_STATE_MIN_SAMPLES`，
        上面两条一样绿（§4.11「调用点是否真传参」——自证式测试测不出缺陷）。
        """
        import quantpilot.services.factor_monitor_service as mod

        for name in ("_ICIR_WINDOW_DAYS", "_ICIR_LAG_DAYS", "_ICIR_WARMUP_DAYS",
                     "_STATE_MIN_SAMPLES", "_BOOTSTRAP_ITERS"):
            assert not hasattr(mod, name), (
                f"{name} 仍是模块级常量 —— 配置与它并存即为平行副本"
            )

    def test_no_hardcoded_window_literals_left(self) -> None:
        """源码里不得再出现这 5 个窗口的字面量（252/272/60/1000/20 的赋值形态）。"""
        import ast
        import inspect

        import quantpilot.services.factor_monitor_service as mod

        src = inspect.getsource(mod)
        tree = ast.parse(src)
        # 只看模块级赋值，避免误伤函数内合法的数值
        bad = [
            n.targets[0].id
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
            and n.value.value in (252, 272, 1000)
        ]
        assert not bad, f"模块级仍有窗口字面量常量：{bad}"


class TestHysteresisSwitchConsumed:
    """`ScoringPipelineConfig.hysteresis_enabled` 无人读取 → Hysteresis 恒开。

    关掉它应当让本月顺序**立即生效**（不进 pending_switch）。
    """

    def test_service_accepts_scoring_config(self) -> None:
        from unittest.mock import MagicMock

        from quantpilot.services.factor_monitor_service import FactorMonitorService

        svc = FactorMonitorService(
            MagicMock(), MagicMock(),
            scoring_config=ScoringPipelineConfig(hysteresis_enabled=False),
        )
        assert svc._scoring_cfg.hysteresis_enabled is False

    def test_disabled_hysteresis_adopts_this_month_order_immediately(self) -> None:
        """关开关 → 直接采用本月顺序、status=stable；开开关 → 走状态机。

        ⚠️ 用真实的 `HysteresisStateMachine` 造一个「本月顺序变了」的场景：
        开着时它必然返回 `pending_switch` 且沿用上月顺序，关掉时必须不是那样。
        两者相同 = 开关没接线。
        """
        from quantpilot.engine.hysteresis import HysteresisStateMachine

        prev = ["value", "trend", "momentum", "mean_reversion"]
        this = ["momentum", "value", "trend", "mean_reversion"]
        on_order, on_status = HysteresisStateMachine().evaluate(
            prev_month_order=prev, this_month_order=this, last_status="stable",
        )
        assert (on_order, on_status) != (this, "stable"), (
            "构造的场景没触发迟滞，本测试无效"
        )

        from quantpilot.services.factor_monitor_service import resolve_effective_order

        off_order, off_status = resolve_effective_order(
            prev_month_order=prev, this_month_order=this, last_status="stable",
            hysteresis_enabled=False,
        )
        assert (off_order, off_status) == (this, "stable"), "关掉迟滞后仍未立即生效"

        on2 = resolve_effective_order(
            prev_month_order=prev, this_month_order=this, last_status="stable",
            hysteresis_enabled=True,
        )
        assert on2 == (on_order, on_status), "开启时必须与状态机结果一致"


class TestFactorMonitorConstructedThroughFactory:
    """⚠️ 本组是整个 F-SI 里**最容易漏、也最贵**的一层。

    给 `FactorMonitorService.__init__` 加了 `config` 参数并不等于用户配置生效——
    只要有任何一个构造点不传，那条路径上的配置就依旧无效。CLAUDE.md §4.11 表第 4 例
    正是这个形状：`compute_pool` 的持仓保护机制完全正确，只因链上三层默认
    `frozenset()`、终点从未被传入非空值，`candidate_pool.is_holding` 五年 0 行，
    持仓跌出候选池后硬止损不可达。

    判据只能在**调用点**上验（§4.11：任何「构造 spy 再调用它」的测试都是自证式的，
    缺陷仍在时照样绿）。这里用 AST 断言一条更硬的不变量：
    **除工厂函数外，src 下不得出现 `FactorMonitorService(...)` 直接构造。**
    比逐点检查 `config=` 更难绕过——新增调用点忘了走工厂就会红。
    """

    _ALLOWED = {"backend/src/quantpilot/services/scoring_factory.py"}

    @staticmethod
    def _direct_constructions() -> list[str]:
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "quantpilot"
        hits: list[str] = []
        for f in root.rglob("*.py"):
            if f.name == "factor_monitor_service.py":
                continue  # 定义处
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "FactorMonitorService"
                ):
                    hits.append(f"{f.name}:{n.lineno}")
        return hits

    def test_only_the_factory_constructs_it(self) -> None:
        hits = [h for h in self._direct_constructions() if not h.startswith("scoring_factory.py")]
        assert not hits, (
            "以下调用点绕过 build_factor_monitor_service 直接构造，"
            f"用户配置在这些路径上不生效：{hits}"
        )

    def test_factory_actually_passes_config_service(self) -> None:
        """工厂必须把 `ConfigService` 传进去，而不是又回落 DEFAULT_*。

        没有这条，把工厂写成 `FactorMonitorService(session, engine)` 也能让上一条全绿。
        （工厂**不读值**是有意的——读值即构造时 IO，见工厂 docstring。）
        """
        import ast
        import inspect

        from quantpilot.services.scoring_factory import build_factor_monitor_service

        tree = ast.parse(inspect.getsource(build_factor_monitor_service).lstrip())
        kwargs = {
            kw.arg
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "FactorMonitorService"
            for kw in n.keywords
        }
        assert "config_service" in kwargs, (
            f"工厂未传 config_service —— 用户配置无从进入服务：{kwargs}"
        )
        ctor = {
            getattr(n.func, "id", None)
            for n in ast.walk(tree) if isinstance(n, ast.Call)
        }
        assert "ConfigService" in ctor, "工厂没构造 ConfigService，配置来源仍是 DEFAULT_*"


class TestEveryEditableConfigKeyHasAConsumer:
    """用户可编辑的每个 `config_key`，生产侧都必须真的去读它。

    2026-09-07 实测：`api/v1/settings.py::_VALID_CONFIG_KEYS` 的 12 个 key 里，
    **6 个的 ConfigService getter 在 src/ 与 scripts/ 下零调用**——用户改了
    会存库、界面显示已保存、生产永不读取。这比 2026-08-27 审计的口径更严重一档：
    审计数的是「类内字段无人引用」（12 个字段），这里是**整个 key 从未被读取**。

    ⚠️ 白名单不是豁免，是**账**：它记录当前欠着的 6 项（已登记 roadmap §6 V1.5-F，
    属「依赖外部决策」——策略参数该不该给用户调、`strategy_weights` 与 ICIR
    运行期权重孰先，都要产品拍板，CLAUDE.md §5.4 四类充分理由之一）。
    新增第 7 项、或加了新 key 却没接线，本测试立刻红。
    """

    # 已知未接线（V1.5-F）。**只许缩短，不许加长**。
    _KNOWN_UNWIRED = {
        "market_state_params",
        "strategy_weights",
        "strategy_params_trend",
        "strategy_params_momentum",
        "strategy_params_mean_reversion",
        "strategy_params_value",
    }

    @staticmethod
    def _getter_call_sites() -> dict[str, int]:
        import ast
        import pathlib

        from quantpilot.api.v1.settings import _VALID_CONFIG_KEYS

        root = pathlib.Path(__file__).resolve().parents[2]
        defn = root / "src" / "quantpilot" / "services" / "config_service.py"
        counts = {k: 0 for k in _VALID_CONFIG_KEYS}
        # key → getter 名（ConfigService 的命名约定）
        getters = {k: f"get_{k}" for k in counts}
        for base in ("src", "scripts"):
            for f in (root / base).rglob("*.py"):
                if f == defn:
                    continue
                try:
                    tree = ast.parse(f.read_text(encoding="utf-8"))
                except SyntaxError:  # pragma: no cover
                    continue
                for n in ast.walk(tree):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                        for k, g in getters.items():
                            if n.func.attr == g:
                                counts[k] += 1
        return counts

    def test_no_new_unwired_config_keys(self) -> None:
        counts = self._getter_call_sites()
        unwired = {k for k, c in counts.items() if c == 0}
        new = unwired - self._KNOWN_UNWIRED
        assert not new, (
            f"新增了无人读取的用户可编辑配置：{sorted(new)} —— "
            "旋钮拧了没反应且不报错（CLAUDE.md §4.11）"
        )

    def test_allowlist_shrinks_when_a_key_gets_wired(self) -> None:
        """白名单里的 key 一旦接上线，必须从白名单删掉。

        没有这条，白名单会变成永久豁免——「记了账」和「还了账」看起来一样。
        """
        counts = self._getter_call_sites()
        wired_but_listed = {k for k in self._KNOWN_UNWIRED if counts.get(k, 0) > 0}
        assert not wired_but_listed, (
            f"这些 key 已有消费者，请从 _KNOWN_UNWIRED 删除：{sorted(wired_but_listed)}"
        )
