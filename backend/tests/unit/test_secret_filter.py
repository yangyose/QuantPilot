"""UT-P13-C-01~05: Phase 13 SecretFilter 单元测试。

依据 docs/design/phases/phase13_production_observability.md §3.3 + §6.1：
- UT-P13-C-01: TUSHARE_TOKEN/REDIS_URL 形如 KEY=VALUE 被替换 ***REDACTED***
- UT-P13-C-02: bcrypt hash 字符串被替换
- UT-P13-C-03: Bearer JWT token 被替换
- UT-P13-C-04: 普通业务日志不被误杀（ts_code / trade_date / count 等）
- UT-P13-C-05: record.args 被清空（避免格式化时重新插入）
"""
from __future__ import annotations

import logging

from quantpilot.core.logging_config import SecretFilter


def _make_record(msg: str, args: tuple = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name="quantpilot.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_ut_p13_c_01_tushare_token_replaced() -> None:
    """UT-P13-C-01: TUSHARE_TOKEN=xxx / REDIS_URL=redis://... 被遮蔽。"""
    f = SecretFilter()

    rec = _make_record("启动时载入 TUSHARE_TOKEN=abc123def456 完成")
    assert f.filter(rec) is True
    assert "abc123def456" not in rec.getMessage()
    assert "***REDACTED***" in rec.getMessage()

    rec2 = _make_record("config REDIS_URL=redis://:secret@redis:6379/0 加载")
    assert f.filter(rec2) is True
    assert "secret@redis" not in rec2.getMessage()
    assert "***REDACTED***" in rec2.getMessage()

    rec3 = _make_record("env JWT_SECRET_KEY=very_long_secret_key_here_12345")
    f.filter(rec3)
    assert "very_long_secret_key_here_12345" not in rec3.getMessage()
    assert "***REDACTED***" in rec3.getMessage()


def test_ut_p13_c_02_bcrypt_hash_replaced() -> None:
    """UT-P13-C-02: bcrypt hash 字符串被替换。"""
    f = SecretFilter()
    rec = _make_record(
        "admin password hash: $2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW done"
    )
    assert f.filter(rec) is True
    msg = rec.getMessage()
    assert "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW" not in msg
    assert "***REDACTED***" in msg


def test_ut_p13_c_03_bearer_jwt_replaced() -> None:
    """UT-P13-C-03: Bearer JWT 被替换。"""
    f = SecretFilter()
    rec = _make_record(
        "incoming Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.signature_part"
    )
    assert f.filter(rec) is True
    msg = rec.getMessage()
    assert "eyJhbGciOiJIUzI1NiJ9" not in msg
    assert "***REDACTED***" in msg

    rec2 = _make_record("WxPusher app_token: AT_abcDEF12345678ghijKL token loaded")
    f.filter(rec2)
    assert "AT_abcDEF12345678ghijKL" not in rec2.getMessage()
    assert "***REDACTED***" in rec2.getMessage()

    rec3 = _make_record("WxPusher uid: UID_abcDEF12345678ghijKL")
    f.filter(rec3)
    assert "UID_abcDEF12345678ghijKL" not in rec3.getMessage()
    assert "***REDACTED***" in rec3.getMessage()


def test_ut_p13_c_04_business_logs_preserved() -> None:
    """UT-P13-C-04: 普通业务日志保留（ts_code / trade_date / count）。"""
    f = SecretFilter()
    cases = [
        "ingest_daily start: trade_date=2026-05-22 ts_codes_count=5840",
        "signal generated: ts_code=000001.SZ score=0.85 type=BUY",
        "ICIR window state: strategy=trend factor=macd_hist icir=0.123",
        "pipeline_run_id=42 status=SUCCESS elapsed=12.3s",
        "INFO: 候选池 50 只，BUY 信号 23 条",
    ]
    for msg in cases:
        rec = _make_record(msg)
        assert f.filter(rec) is True
        assert "***REDACTED***" not in rec.getMessage(), (
            f"业务日志被误杀: {msg}"
        )


def test_ut_p13_c_05_args_cleared_after_filter() -> None:
    """UT-P13-C-05: record.args 被清空，避免格式化时重新插入。"""
    f = SecretFilter()
    rec = _make_record("token loaded: TUSHARE_TOKEN=%s", ("secret_token_xyz",))
    assert f.filter(rec) is True
    assert rec.args == ()
    assert "secret_token_xyz" not in rec.getMessage()


# ── V1.5-A A4（R13-P3-2）：SecretFilter 扫描 record.__dict__ 覆盖 structured logging extra ──


def test_a4_secret_filter_scrubs_extra_dict_fields() -> None:
    """A4-R13P3-2: logger.info(..., extra={...}) 的敏感字段落在 record.__dict__，
    SecretFilter 须遍历非标准属性脱敏，防止 extra 字段泄漏密钥。
    """
    f = SecretFilter()
    rec = _make_record("data ingest done")
    # 模拟 structured logging extra 注入的自定义属性
    rec.tushare_cfg = "TUSHARE_TOKEN=abc123def456secret"   # KEY=VALUE 型
    rec.auth_header = "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig_here"  # Bearer 型
    rec.wxpusher = "AT_abcDEF12345678ghijKL"                # AT_ 型
    rec.plain = "ts_code=000001.SZ score=0.9"              # 业务字段不误杀

    assert f.filter(rec) is True
    assert "abc123def456secret" not in rec.tushare_cfg
    assert "***REDACTED***" in rec.tushare_cfg
    assert "eyJhbGciOiJIUzI1NiJ9" not in rec.auth_header
    assert "AT_abcDEF12345678ghijKL" not in rec.wxpusher
    # 业务字段保留
    assert rec.plain == "ts_code=000001.SZ score=0.9"


def test_a4_secret_filter_extra_ignores_non_str_and_standard_attrs() -> None:
    """A4-R13P3-2: 非字符串 extra 值 + 标准 LogRecord 属性不被改写/不报错。"""
    f = SecretFilter()
    rec = _make_record("x")
    rec.count = 42            # 非 str
    rec.ratio = 0.85          # 非 str
    assert f.filter(rec) is True
    assert rec.count == 42
    assert rec.ratio == 0.85
    # 标准属性（levelname/name 等）保持不变
    assert rec.levelname == "INFO"
    assert rec.name == "quantpilot.test"
