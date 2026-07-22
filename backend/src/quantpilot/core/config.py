from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 数据库
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT 认证
    # admin_username / admin_password_hash（V1.5-G 起）仅供 alembic 0018 迁移种子
    # 首用户消费，应用运行时不再读取；0018 跑过后可从 env 移除（留空则 0018 跳过种子）。
    admin_username: str = ""
    admin_password_hash: str = ""
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # 认证接口按 IP 限频（V1.5-G G-2b §4.3）。字符串遵循 limits 库语法（如 "10/minute"）。
    # storage_uri 留空 = memory://（生产为单 uvicorn 进程，进程内计数够用）；
    # 未来多 worker 部署时配 redis URI 共享计数。
    rate_limit_enabled: bool = True
    rate_limit_login: str = "10/minute"
    rate_limit_register: str = "5/hour"
    rate_limit_storage_uri: str = ""

    # 数据源
    tushare_token: str = ""

    # 通知（Phase 10：WxPusher）
    wxpusher_app_token: str = ""
    wxpusher_uid: str = ""

    # 应用
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:80"]

    # 回测整体开关（2026-06-29）：False = 在本服务器彻底禁用 POST /backtest/run（→ 503）。
    # 缘起：2GB 生产机即便单个短区间回测（实测 6 日）也吃 ~1.5GB → /health 超时 11 分钟
    # （window_days 护栏只挡长区间，挡不住"短区间也 OOM"）。生产 .env.prod 置 false，
    # 回测统一走本地算力中心 scripts/run_backtest_local.py，跑完经 /backtest/import 回灌。
    # 本地/大内存机默认 True（放开）。
    backtest_enabled: bool = True

    # 回测护栏（2026-06-15）：限制单次回测的日历跨度（天）。0 = 不限制。
    # 生产 2GB 机内存有限，长区间回测（daily_quotes 全量 pivot）会 OOM 拖垮整机；
    # 服务器 .env.prod 设保守值（如 100），超限直接拒绝并提示本地运行；本地大内存机
    # 不设此值（默认 0 = 放开），用 scripts/run_backtest_local.py 跑长区间。
    # 注：backtest_enabled=False 时本项无意义（请求在更前置被 503 拦截）。
    backtest_max_window_days: int = 0

    # 日志（Phase 10 §8.4 / SDD §15.5）
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_json: bool = True


settings = Settings()
