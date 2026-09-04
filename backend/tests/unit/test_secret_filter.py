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


# ── V1.5-K：凭证 URL 按「形状」脱敏，而不是按键名 ──────────────────────────────
#
# 2026-09-03 生产实证：`SecretFilter` 的 docstring 宣称覆盖 REDIS_URL，实际四个月
# 从未拦住过——生产日志里 Redis 密码一直是明文。
#
# 根因不在过滤器，在**测试输入**：`_SECRET_PATTERNS` 匹配的是字面量键名
# `REDIS_URL=...`，而 `main.py` 真正打的是 `redis_connected url=redis://:<pw>@...`
# ——键名是 `url`。UT-P13-C-01 喂的 `"config REDIS_URL=redis://:secret@..."`
# 是**自己造的、恰好能匹配**的串，不是应用真正输出的那一行，于是全绿而缺陷仍在。
#
# 这是 CLAUDE.md §4.11「接了但没生效」一族的又一实例，载体是**测试输入**：
# 替身/输入比现实更配合 → 现实中的那条分支在测试世界里不可表达 → 无人写它。
#
# 判据因此改为：**用 main.py 逐字打出的那行真实格式**，且断言密码子串不出现，
# 而不是断言出现了 ***REDACTED***（后者在「整行没匹配上」时同样可以为真）。


class TestCredentialUrlRedaction:
    def test_real_main_py_log_line_is_redacted(self) -> None:
        """`main.py:72` 逐字格式：键名是 url，不含 REDIS_URL 字样。

        这条是本次缺陷的回归守卫——把形状匹配去掉，它立刻红。
        """
        f = SecretFilter()
        rec = _make_record("redis_connected url=redis://:s3cr3t%23pw@redis:6379/0")
        assert f.filter(rec) is True
        out = rec.getMessage()
        assert "s3cr3t%23pw" not in out, f"密码泄漏在日志里：{out}"

    def test_failure_branch_also_redacted(self) -> None:
        """`main.py:74` 的失败分支同样带 url，且还拼了 reason。"""
        f = SecretFilter()
        rec = _make_record(
            "redis_connect_failed url=redis://:s3cr3t@redis:6379/0 reason=timeout"
        )
        f.filter(rec)
        assert "s3cr3t" not in rec.getMessage()

    def test_database_dsn_redacted(self) -> None:
        """DSN 同形状——今天没人打它，但打了就该拦住。"""
        f = SecretFilter()
        rec = _make_record(
            "db connect postgresql+asyncpg://qp:pgpassword@db:5432/quantpilot"
        )
        f.filter(rec)
        assert "pgpassword" not in rec.getMessage()

    def test_password_hidden_even_when_username_present(self) -> None:
        f = SecretFilter()
        rec = _make_record("conn amqp://alice:hunter2@mq:5672/")
        f.filter(rec)
        out = rec.getMessage()
        assert "hunter2" not in out

    def test_host_survives_so_the_line_stays_useful(self) -> None:
        """脱敏不能把整行变成 ***REDACTED***——运维还要靠 host/port 排障。

        整段替换会让「连的是哪个 redis」这个信息一起消失，日志就没用了。
        """
        f = SecretFilter()
        rec = _make_record("redis_connected url=redis://:pw@redis:6379/0")
        f.filter(rec)
        out = rec.getMessage()
        assert "redis:6379" in out, f"host:port 不该被抹掉：{out}"
        assert "redis_connected" in out

    def test_url_without_credentials_untouched(self) -> None:
        """无凭证的 URL 原样保留，避免误杀。"""
        f = SecretFilter()
        rec = _make_record("fetch https://api.tushare.pro/data?x=1")
        f.filter(rec)
        assert rec.getMessage() == "fetch https://api.tushare.pro/data?x=1"

    def test_scheme_relative_or_malformed_does_not_raise(self) -> None:
        f = SecretFilter()
        for bad in ("://@", "redis://@host", "not a url at all", "a://b:@c"):
            rec = _make_record(bad)
            assert f.filter(rec) is True
