"""Phase 10 §8.4：生产日志配置（SDD §15.5）。

- RotatingFileHandler：单文件 50 MB，保留 7 个归档
- JSONFormatter：结构化 JSON 日志，便于 ELK/Grafana 采集
- 控制台 + 文件双通道；DEBUG=true 时控制台增加 DEBUG 级别
- Phase 13 P13-C：SecretFilter 过滤敏感字段（TUSHARE_TOKEN/bcrypt/Bearer/WxPusher token/REDIS_URL）
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
from pathlib import Path

_SECRET_PATTERNS = [
    re.compile(
        r"(TUSHARE_TOKEN|ADMIN_PASSWORD_HASH|JWT_SECRET_KEY|WXPUSHER_APP_TOKEN|REDIS_URL)"
        r"\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\$2[abxy]\$[0-9]{2}\$[./A-Za-z0-9]{53}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"AT_[A-Za-z0-9]{16,}"),
    re.compile(r"UID_[A-Za-z0-9]{16,}"),
]


# 标准 LogRecord 属性集合——SecretFilter 扫描 record.__dict__ 时跳过这些，
# 仅对 structured logging 经 extra={...} 注入的自定义字符串字段脱敏
# （V1.5-A R13-P3-2）。以一个空 LogRecord 的 __dict__ 键为准 + msg/args（已单独处理）。
_STD_LOGRECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class SecretFilter(logging.Filter):
    """Phase 13 S5-GAP-03：过滤日志中潜在敏感字段。

    匹配后整段替换为 ***REDACTED***。扫描 record.msg + record.args 的字符串表示，
    并（V1.5-A R13-P3-2）遍历 record.__dict__ 中 structured logging `extra={...}`
    注入的**非标准**字符串字段，防止密钥经 extra 字段泄漏。不修改原 dict/对象引用，
    非字符串 extra 值原样保留。匹配后清空 record.args，避免格式化时重新插入。
    """

    @staticmethod
    def _scrub(text: str) -> str:
        for pat in _SECRET_PATTERNS:
            text = pat.sub("***REDACTED***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg = self._scrub(msg)
        record.args = ()
        # R13-P3-2：脱敏 extra 注入的自定义字符串属性（跳过标准 LogRecord 属性 + 非 str 值）
        for key, val in record.__dict__.items():
            if key in _STD_LOGRECORD_ATTRS:
                continue
            if isinstance(val, str):
                record.__dict__[key] = self._scrub(val)
        return True


class JSONFormatter(logging.Formatter):
    """结构化 JSON 日志格式化器。

    输出字段：timestamp / level / logger / message / module / function / line。
    异常时额外输出 exc_info 字符串。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    log_dir: str | None = None,
    level: str = "INFO",
    enable_json: bool = True,
) -> None:
    """配置根 logger。

    Args:
        log_dir: 日志目录；None 则读 LOG_DIR 环境变量，再回退 logs/
        level: 根日志级别
        enable_json: 文件输出是否使用 JSON（True 为生产默认；开发可关）
    """
    log_dir = log_dir or os.getenv("LOG_DIR", "logs")
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(Path(log_dir) / "quantpilot.log"),
        maxBytes=50 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        JSONFormatter() if enable_json
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    secret_filter = SecretFilter()
    for handler in (console, file_handler):
        handler.addFilter(secret_filter)

    # 第三方库噪声压制
    for noisy in ("apscheduler", "httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel("WARNING")


__all__ = ["JSONFormatter", "SecretFilter", "setup_logging"]
