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

    # 日志（Phase 10 §8.4 / SDD §15.5）
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_json: bool = True


settings = Settings()
