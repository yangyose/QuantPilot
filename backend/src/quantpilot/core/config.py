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
    #
    # ⚠️ 2026-09-14 复查 + **实测**（生产已 2C2G → 2C4G，见
    # `docs/reviews/memory_premise_after_4gb_2026-09-14.md` §2②）：
    # 本地算力中心跑同样的 6 交易日回测，峰值工作集 **3530 MB（3.5 GB）**
    # ——不是上面写的 ~1.5GB。而生产空闲 available 仅约 2523M（总 3723M），
    # **即 503 不但要维持，且这一条是最没资格放宽的**：升配后需求反而涨得更快
    # （机器 2GB→3.7GB 涨 1.7 倍，本作业 1.5→3.5GB 涨 2.3 倍）。
    #
    # 这正是运维红线①依据 (b) 的实证：峰值随 universe 与 5 年窗口逐年长、
    # **地板没被抬高**（SQL 下推尚未实施）。
    # ⇒ **放宽的前置条件是算法（`get_pe_pb_history_bulk` 的 SQL 下推），不是内存**；
    # 在那之前任何扩容都只是推迟撞墙时间。
    # ⚠️ 复测一律在本地算力中心，**不能在生产上试**——那正是红线①禁的那类作业。
    #
    # ✅ 2026-09-16 下推已做（同一 6 日窗口同一测法）：**3530 MB → 1056 MB**；30 日窗口
    # 1159 MB（约 +4 MB/交易日，100 日 ≈ 1.45 GB）。三步：财务切片流式分块、daily_quotes
    # 由 ORM 对象改列裁剪流式、NUMERIC 在 SQL 内 cast float8（Decimal 碎片是大头）；分位
    # 改走生产同款 `get_pe_pb_percentile_bulk`（顺带把回测分位窗口从 ~400 天对齐到 5 年）。
    # ⇒ 上面那条「算法前置」已满足。**503 仍维持**：剩下的门槛不再是内存，而是运维红线①
    # （回测就是一次全 universe 评分作业）——放开与否由用户按
    # `docs/reviews/memory_premise_after_4gb_2026-09-14.md` §3 拍板，不在代码里自行放开。
    backtest_enabled: bool = True

    # 回测护栏（2026-06-15）：限制单次回测的日历跨度（天）。0 = 不限制。
    # 生产 2GB 机内存有限，长区间回测（daily_quotes 全量 pivot）会 OOM 拖垮整机；
    # 服务器 .env.prod 设保守值（如 100），超限直接拒绝并提示本地运行；本地大内存机
    # 不设此值（默认 0 = 放开），用 scripts/run_backtest_local.py 跑长区间。
    # 注：backtest_enabled=False 时本项无意义（请求在更前置被 503 拦截）。
    backtest_max_window_days: int = 0

    # 回测禁提交时段（2026-09-16，用户拍板「有条件放开回测」选项 B）：Asia/Shanghai 的
    # `HH:MM-HH:MM` 逗号分隔，左闭右开；空 = 不限制。生产设 `17:15-18:30,19:15-20:15`，
    # 避开 17:30 每日管线与 19:30 日级 IC Job——回测本身就是一次全 universe 评分，
    # 与它们**叠加**才是运维红线①真正禁的形态（4GB 实测：管线增量约 0.5 GB、
    # 6~30 日回测 1.0~1.2 GB，各自都有余量，叠加就没有）。
    backtest_blackout_windows: str = ""

    # 日志（Phase 10 §8.4 / SDD §15.5）
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_json: bool = True


settings = Settings()
