"""unit/test_notification_service.py: Phase 10 NotificationService 真实实现单测。

INV-NTF-01：偏好开关过滤（notify_signal_buy=False → 不写库不推送）
INV-NTF-02：推送时段过滤（当前小时不在 [start, end) → 仅写库不推送）
INV-NTF-03：去重（24 小时内同类型同 payload 仅写一次）
INV-NTF-04：兜底写库始终发生 + WxPusher 失败标 wx_error
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from quantpilot.core.config_defaults import DEFAULT_NOTIFICATION, NotificationConfig
from quantpilot.models.business import InAppNotification
from quantpilot.notification.base import NotificationChannel
from quantpilot.services.notification_service import NotificationService

# 在 autouse fixture 替换前捕获原始静态方法，留给 TestPushWindow 直接调用
_ORIGINAL_IN_PUSH_WINDOW = NotificationService._in_push_window


# ───────────────────── Fakes ─────────────────────
class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """最小可用的 AsyncSession 替身。"""

    def __init__(self) -> None:
        self.added: list[InAppNotification] = []
        self.flush_count = 0
        # 预设 execute 返回值队列（None = 无重复行）
        self._execute_results: list[Any] = []
        self.executed_stmts: list[Any] = []

    def queue_execute(self, value: Any) -> None:
        self._execute_results.append(value)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed_stmts.append(stmt)
        if self._execute_results:
            return _FakeResult(self._execute_results.pop(0))
        return _FakeResult(None)


class _FakeConfigService:
    def __init__(self, prefs: NotificationConfig) -> None:
        self._prefs = prefs

    async def get_notification_prefs(self) -> NotificationConfig:
        return self._prefs


class _FakeWx:
    """WxPusherAdapter 的测试替身。

    ⚠️ `configured` 不是可选装饰——真实 adapter 有这个状态，替身缺了它就等于
    在测试世界里删掉了「未配置」这个现实存在的分支（2026-09-02 生产缺陷成因）。
    契约由 TestDoubleFidelity 钉死。
    """

    def __init__(
        self, ok: bool = True, uid: str = "UID_test", configured: bool = True
    ) -> None:
        self.ok = ok
        self.uid = uid
        self.target = uid   # ABC 契约成员（日志用），真实 adapter 亦返回 uid
        self.configured = configured
        self.calls: list[tuple[str, str]] = []

    async def send(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        # 与真实 adapter 同构：未配置直接 False，不产生任何"尝试"
        if not self.configured:
            return False
        return self.ok


# ───────────────────── 助手 ─────────────────────
def _build(
    prefs: NotificationConfig | None = None,
    wx_ok: bool = True,
    with_wx: bool = True,
    wx_configured: bool = True,
) -> tuple[NotificationService, _FakeSession, _FakeWx | None]:
    session = _FakeSession()
    wx: _FakeWx | None = (
        _FakeWx(ok=wx_ok, configured=wx_configured) if with_wx else None
    )
    svc = NotificationService(
        session=session,  # type: ignore[arg-type]
        config_service=_FakeConfigService(prefs or DEFAULT_NOTIFICATION),  # type: ignore[arg-type]
        wxpusher=wx,
    )
    return svc, session, wx


@pytest.fixture(autouse=True)
def force_push_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让 _in_push_window 返回 True，避免本地时间影响断言。
    需要测试时段过滤的用例自行调用 monkeypatch 覆盖。"""
    monkeypatch.setattr(
        NotificationService,
        "_in_push_window",
        staticmethod(lambda prefs, now=None: True),
    )


# ───────────────────── INV-NTF-01：开关过滤 ─────────────────────
class TestSwitchFilter:
    async def test_signal_buy_disabled_skips_everything(self) -> None:
        prefs = replace(DEFAULT_NOTIFICATION, notify_signal_buy=False)
        svc, session, wx = _build(prefs=prefs)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is None
        assert session.added == []
        assert session.flush_count == 0
        assert wx is not None and wx.calls == []

    async def test_unknown_type_passes_default_true(self) -> None:
        """未在 _TYPE_PREF_MAP 登记的类型默认放行（如 PIPELINE_FAILURE）。"""
        svc, session, _ = _build()
        result = await svc.notify("PIPELINE_FAILURE", "t", "b", {"run_id": 1})
        assert result is not None
        assert len(session.added) == 1


# ───────────────────── INV-NTF-02：时段过滤 ─────────────────────
class TestPushWindow:
    async def test_outside_window_writes_db_but_skips_wx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 强制把 _in_push_window 改回 False（覆盖 autouse fixture）
        monkeypatch.setattr(
            NotificationService,
            "_in_push_window",
            staticmethod(lambda prefs, now=None: False),
        )
        svc, session, wx = _build()
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert len(session.added) == 1
        assert wx is not None and wx.calls == []
        assert result.wx_pushed is False

    def test_window_normal_range(self) -> None:
        prefs = replace(DEFAULT_NOTIFICATION, push_start_hour=15, push_end_hour=22)
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 15)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 21)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 22)) is False
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 14)) is False

    def test_window_overnight_range(self) -> None:
        # 跨日：22 → 06
        prefs = replace(DEFAULT_NOTIFICATION, push_start_hour=22, push_end_hour=6)
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 23)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 5)) is True
        assert _ORIGINAL_IN_PUSH_WINDOW(prefs, datetime(2026, 4, 21, 10)) is False


# ───────────────────── INV-NTF-03：去重 ─────────────────────
class TestDedup:
    async def test_dedup_skips_when_recent_exists(self) -> None:
        svc, session, wx = _build()
        # 模拟 _is_duplicate 查库返回已有 id
        session.queue_execute(123)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is None
        assert session.added == []
        assert wx is not None and wx.calls == []

    async def test_no_dedup_when_payload_is_none(self) -> None:
        svc, session, _ = _build()
        result = await svc.notify("SIGNAL_BUY", "t", "b", payload=None)
        assert result is not None
        # payload=None 时跳过去重查询
        assert session.executed_stmts == []

    async def test_no_dedup_for_different_payload(self) -> None:
        svc, session, _ = _build()
        # 第一次：dedup 查询返回 None；第二次：返回 None
        session.queue_execute(None)
        session.queue_execute(None)
        await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "600519.SH"})
        assert len(session.added) == 2


# ───────────────────── INV-NTF-04：兜底写库 + WxPusher 失败标记 ─────────────────────
class TestFallback:
    async def test_wx_failure_marks_in_app(self) -> None:
        svc, session, wx = _build(wx_ok=False)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert result.wx_pushed is False
        assert result.wx_error is not None
        assert "重试 3 次均失败" in result.wx_error
        assert wx is not None and len(wx.calls) == 1

    async def test_wx_success_marks_pushed(self) -> None:
        svc, session, wx = _build(wx_ok=True)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert result.wx_pushed is True
        assert result.wx_error is None

    async def test_no_wx_adapter_only_writes_in_app(self) -> None:
        svc, session, wx = _build(with_wx=False)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert result.wx_pushed is False
        assert wx is None
        assert len(session.added) == 1

    async def test_wx_disabled_in_prefs_skips_push(self) -> None:
        prefs = replace(DEFAULT_NOTIFICATION, wx_enabled=False)
        svc, session, wx = _build(prefs=prefs)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert wx is not None and wx.calls == []
        assert result.wx_pushed is False


# ───────────────────── 模板渲染（轻量验证） ─────────────────────
class TestTemplates:
    async def test_market_state_change_template(self) -> None:
        svc, session, _ = _build()
        result = await svc.notify_market_state_change("UPTREND", "DOWNTREND", "2026-04-21")
        assert result is not None
        assert "UPTREND" in result.title and "DOWNTREND" in result.title
        assert "2026-04-21" in result.body

    async def test_stop_loss_warn_template(self) -> None:
        svc, _, _ = _build()
        result = await svc.notify_stop_loss_warn(
            ts_code="000001.SZ",
            name="平安银行",
            current_price=10.50,
            stop_loss_price=10.40,
            distance_pct=0.0095,
        )
        assert result is not None
        assert "平安银行" in result.title
        assert "10.50" in result.body and "10.40" in result.body
        assert "0.95%" in result.body

    async def test_factor_alert_template_with_ic(self) -> None:
        svc, _, _ = _build()
        result = await svc.notify_factor_alert("IC_NEGATIVE", "trend", "ma_alignment", -0.0123)
        assert result is not None
        assert "trend.ma_alignment" in result.title
        assert "-0.0123" in result.body

    async def test_factor_alert_legacy_signature_compat(self) -> None:
        """Phase 7 调用方仍用 (alert_type, strategy, factor) 三参签名。"""
        svc, session, _ = _build()
        result = await svc.notify_factor_alert("IC_NEGATIVE", "trend", "ma_alignment")
        assert result is not None
        assert len(session.added) == 1


# ───────────────────── 异常路径：flush 失败 ─────────────────────
class TestFlushFailure:
    async def test_flush_exception_re_raises(self) -> None:
        svc, session, _ = _build()

        async def _raising_flush() -> None:
            raise RuntimeError("DB down")

        session.flush = _raising_flush  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="DB down"):
            await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})


# ───────────────────── 信号模板 ─────────────────────
class TestSignalTemplate:
    async def test_buy_signal_renders_full_template(self) -> None:
        from quantpilot.models.business import Signal
        svc, _, _ = _build()
        sig = Signal(
            id=1,
            ts_code="000001.SZ",
            signal_type="BUY",
            trade_date=datetime(2026, 4, 21).date(),
            score=85.0,
            suggested_pct=0.10,
            suggested_price_low=10.0,
            suggested_price_high=10.20,
            stop_loss_price=9.20,
            signal_strength="STRONG",
            reason="均线多头排列+MACD金叉",
            status="NEW",
        )
        result = await svc.notify_signal(sig, name="平安银行", amount=10000)
        assert result is not None
        assert "平安银行" in result.title
        assert "85.00/100" in result.body
        assert "STRONG" in result.body
        assert "10.00-10.20" in result.body
        assert "10.0%" in result.body
        assert "10000" in result.body
        # stop_loss_pct = (1 - 9.20/10.00) * 100 = 8.0
        assert "8.0%" in result.body
        assert "T+1" in result.body


# ───────── INV-NTF-05：未配置渠道 ≠ 发送失败（2026-09-02 生产缺陷回归） ─────────
#
# 生产实证：`.env.prod` 的 WXPUSHER_APP_TOKEN/UID 为空 → adapter `_configured=False`
# → `send()` 立即 return False（未发 HTTP、未重试）。但 Service 把这个 False 当成
# 「发送失败」处理：每条通知记一条 ERROR，并把 "重试 3 次均失败" 写进 wx_error。
# 结果是 3196 行通知带着一句**假话**，外加每天 52 行伪 ERROR 淹没真错误。
#
# 缺陷藏了 3.5 个月的原因就在本文件里：`_FakeWx` 没有 `configured` 属性，
# **测试替身比真实对象更能干**，那条分支在测试中根本无法被触发。
# 故本组用例同时钉住替身与真实 adapter 的接口一致性（TestDoubleFidelity）。
class TestUnconfiguredChannel:
    async def test_unconfigured_wx_is_not_called(self) -> None:
        """未配置 → 根本不该去调 send（它必然失败，调了只是浪费与噪声）。"""
        svc, _session, wx = _build(wx_configured=False)
        await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert wx is not None and wx.calls == []

    async def test_unconfigured_wx_writes_no_false_error(self) -> None:
        """未配置 → wx_error 必须为 None。

        语义锚点：`wx_error IS NOT NULL` 恒等于「真的尝试过发送且失败了」。
        运维靠这个区分「没配」和「配了但发不出去」——写假话会让这个区分失效。
        """
        svc, _session, _wx = _build(wx_configured=False)
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None
        assert result.wx_pushed is False
        assert result.wx_error is None

    async def test_unconfigured_wx_logs_no_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """未配置是启动时已 WARN 过一次的既知状态，不该每条通知再 ERROR 一次。"""
        svc, _session, _wx = _build(wx_configured=False)
        with caplog.at_level("ERROR", logger="quantpilot.services.notification_service"):
            await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert [r for r in caplog.records if r.levelname == "ERROR"] == []

    async def test_configured_failure_still_errors(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """反向守卫：真失败仍必须 ERROR + 写 wx_error。

        没有这条，把上面三条「修绿」的最省事做法就是把 ERROR 整个删掉——
        那会连真正的发送失败一起静音，比原缺陷更糟。
        """
        svc, _session, wx = _build(wx_ok=False, wx_configured=True)
        with caplog.at_level("ERROR", logger="quantpilot.services.notification_service"):
            result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None and result.wx_error is not None
        assert "重试 3 次均失败" in result.wx_error
        assert wx is not None and len(wx.calls) == 1
        assert any(
            "notification_degraded" in r.message for r in caplog.records
            if r.levelname == "ERROR"
        )


class TestDoubleFidelity:
    """替身接口必须是真实 adapter 的子集——否则测试会比现实宽松。

    本缺陷的成因不是逻辑写错，而是 `_FakeWx` 缺了 `configured`，
    使「未配置」这个真实存在的状态在测试世界里不可表达（§4.11「接了但没生效」族）。
    """

    def test_fake_wx_exposes_same_contract_as_real_adapter(self) -> None:
        from quantpilot.notification.wxpusher import WxPusherAdapter

        # 在**实例**上查：uid / configured 在真实 adapter 上是 property、
        # 在替身上是实例属性，类层面 hasattr 对后者恒 False。
        real = WxPusherAdapter(app_token="", uid="")
        fake = _FakeWx()
        for attr in ("send", "configured", "target"):
            assert hasattr(real, attr), f"真实 adapter 缺 {attr}"
            assert hasattr(fake, attr), f"替身缺 {attr}，测试会比现实宽松"

    def test_real_adapter_reports_unconfigured_on_empty_credentials(self) -> None:
        """钉死生产实际形态：空 token/uid → configured 为 False。

        这是「未配置」这条分支在真实对象上的入口；它若变了，
        上面 TestUnconfiguredChannel 全组就都在测一个不存在的状态。
        """
        from quantpilot.notification.wxpusher import WxPusherAdapter

        assert WxPusherAdapter(app_token="", uid="").configured is False
        assert WxPusherAdapter(app_token="AT_x", uid="").configured is False
        assert WxPusherAdapter(app_token="", uid="UID_x").configured is False
        assert WxPusherAdapter(app_token="AT_x", uid="UID_x").configured is True


# ───────── INV-NTF-06：NotificationService 只依赖 ABC 契约（2026-09-02） ─────────
#
# 缺陷形态：`NotificationChannel` ABC 只声明 `send()`，但 NotificationService 还直接
# 访问 `self._wx.configured` / `.uid` —— 两者都是 WxPusherAdapter 私有的。
# 于是「只实现 ABC 的新渠道」塞进来会当场 AttributeError，而 ABC 的存在让人
# 以为可以直接扩展（`base.py` 注释甚至写着「V1.5 可扩 ServerChan/Email/Slack」）。
#
# 这条边界一直靠「唯一实现恰好是 WxPusherAdapter」掩盖着，ABC 从未真正生效。
# ⚠️ 判据必须用**只实现 ABC 的最小渠道**，不能用 _FakeWx —— 后者有 configured/uid，
# 缺陷仍在时照样绿（自证式测试，见 CLAUDE.md §4.11「调用点是否真传参」那条的教训）。
class _MinimalChannel(NotificationChannel):
    """严格只实现 ABC 声明的成员——未来 WeComAdapter 的形状。"""

    def __init__(self, ok: bool = True, configured: bool = True) -> None:
        self._ok = ok
        self._configured = configured
        self.calls: list[tuple[str, str]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def send(self, title: str, body: str) -> bool:
        self.calls.append((title, body))
        return self._ok


class TestDependsOnlyOnABC:
    async def test_minimal_channel_send_succeeds(self) -> None:
        ch = _MinimalChannel(ok=True)
        svc = NotificationService(
            session=_FakeSession(),  # type: ignore[arg-type]
            config_service=_FakeConfigService(DEFAULT_NOTIFICATION),  # type: ignore[arg-type]
            wxpusher=ch,  # type: ignore[arg-type]
        )
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None and result.wx_pushed is True
        assert len(ch.calls) == 1

    async def test_minimal_channel_failure_path_does_not_crash(self) -> None:
        """失败分支要读「收件目标」写进 ERROR 日志——这里原先直接取 .uid 会炸。"""
        ch = _MinimalChannel(ok=False)
        svc = NotificationService(
            session=_FakeSession(),  # type: ignore[arg-type]
            config_service=_FakeConfigService(DEFAULT_NOTIFICATION),  # type: ignore[arg-type]
            wxpusher=ch,  # type: ignore[arg-type]
        )
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None and result.wx_error is not None

    async def test_minimal_channel_unconfigured_is_skipped(self) -> None:
        ch = _MinimalChannel(configured=False)
        svc = NotificationService(
            session=_FakeSession(),  # type: ignore[arg-type]
            config_service=_FakeConfigService(DEFAULT_NOTIFICATION),  # type: ignore[arg-type]
            wxpusher=ch,  # type: ignore[arg-type]
        )
        result = await svc.notify("SIGNAL_BUY", "t", "b", {"ts_code": "000001.SZ"})
        assert result is not None and result.wx_error is None
        assert ch.calls == []

    def test_abc_declares_what_the_service_uses(self) -> None:
        """AST 检查：NotificationService 对渠道对象的属性访问必须都在 ABC 契约内。

        这条比上面三条更根本——它在**访问点**上验证，
        新加一个 `self._wx.<私有属性>` 会立刻变红，而不必等到有人真去写新渠道。
        """
        import ast
        import inspect

        from quantpilot.services import notification_service as ns

        allowed = {n for n in dir(NotificationChannel) if not n.startswith("_")}
        tree = ast.parse(inspect.getsource(ns))
        used = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_wx"
        }
        assert used, "未扫到任何 self._wx.<attr> 访问，检查 AST 匹配逻辑是否失效"
        assert used <= allowed, (
            f"越过 ABC 契约访问了 {sorted(used - allowed)}；"
            "应加进 NotificationChannel 或改用契约内成员"
        )
