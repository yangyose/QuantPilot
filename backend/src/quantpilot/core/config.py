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
    admin_username: str
    admin_password_hash: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # 数据源
    tushare_token: str = ""

    # 通知（Phase 10：WxPusher）
    wxpusher_app_token: str = ""
    wxpusher_uid: str = ""

    # 应用
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:80"]

    # 回测护栏（2026-06-15）：限制单次回测的日历跨度（天）。0 = 不限制。
    # 生产 2GB 机内存有限，长区间回测（daily_quotes 全量 pivot）会 OOM 拖垮整机；
    # 服务器 .env.prod 设保守值（如 100），超限直接拒绝并提示本地运行；本地大内存机
    # 不设此值（默认 0 = 放开），用 scripts/run_backtest_local.py 跑长区间。
    backtest_max_window_days: int = 0

    # 日志（Phase 10 §8.4 / SDD §15.5）
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_json: bool = True


settings = Settings()
