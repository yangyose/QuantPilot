from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.database import get_db
from quantpilot.core.exceptions import AuthError
from quantpilot.core.security import decode_token
from quantpilot.data.attribution_repository import AttributionRepository
from quantpilot.data.repository import MarketDataRepository
from quantpilot.data.validators import DataValidator
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.market_state import MarketStateEngine
from quantpilot.models.account import Account
from quantpilot.models.user import User
from quantpilot.services.account_service import AccountService
from quantpilot.services.attribution_service import AttributionService
from quantpilot.services.auth_service import AuthService
from quantpilot.services.backtest_service import BacktestService
from quantpilot.services.config_service import ConfigService
from quantpilot.services.data_service import DataService
from quantpilot.services.factor_monitor_service import FactorMonitorService
from quantpilot.services.lineage_service import LineageService
from quantpilot.services.market_state_service import MarketStateService
from quantpilot.services.notification_service import NotificationService
from quantpilot.services.performance_service import PerformanceService
from quantpilot.services.report_service import ReportService
from quantpilot.services.settings_service import SettingsService
from quantpilot.services.setup_service import SetupService
from quantpilot.services.signal_service import SignalService
from quantpilot.services.strategy_service import ScoringService
from quantpilot.services.watchlist_service import WatchlistService

security = HTTPBearer()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    """轻量鉴权守卫：仅解 token 取 user_id（str(user_id)→int），不查 DB。

    停用用户的未过期 access token 仍可访问共享/守卫路由（access 短时效 60min）；
    新 token 签发（login/refresh）会校验 is_active，故停用即断后续。需 is_active
    实时校验的路由用 get_current_user（带 DB）。
    """
    try:
        sub = decode_token(credentials.credentials, expected_type="access")
        return int(sub)
    except (AuthError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


async def get_current_user(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> User:
    """载入当前用户并校验 is_active（不存在/停用→401）。供需要 level/邮箱的路由。"""
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用"
        )
    return user


async def get_current_account_id(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> int:
    """解析当前用户的账户 id。所有账户层路由统一依赖此函数取 account_id（G-3）。"""
    result = await session.execute(
        select(Account.id).where(Account.user_id == user_id)
    )
    account_id = result.scalar_one_or_none()
    if account_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="当前用户无账户"
        )
    return account_id


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    """按请求构造 AuthService（注册/用户管理）。"""
    return AuthService(session)


def get_data_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> DataService:
    """每请求构造 DataService：adapter/calendar 来自 app.state（长期），
    session/repo 每次请求新建（避免连接池泄漏和事务污染）。
    Phase 13 §3.6：注入 AKShareAdapter 作 fallback + NotificationService 用于
    "data_source_unavailable" 告警；fallback_adapter 在 app.state 缺失时降级为 None
    （仍可用主 Tushare 路径，只是没有自动降级）。
    """
    adapter = getattr(request.app.state, "adapter", None)
    calendar = getattr(request.app.state, "calendar", None)
    if adapter is None or calendar is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据服务未初始化（TUSHARE_TOKEN 未配置）",
        )
    repo = MarketDataRepository(session)
    fallback_adapter = getattr(request.app.state, "fallback_adapter", None)
    config_service = ConfigService(session)
    notifier = NotificationService(session, config_service=config_service)
    return DataService(
        adapter, DataValidator(), repo, calendar,
        fallback_adapter=fallback_adapter, notifier=notifier,
    )


def get_market_state_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> MarketStateService:
    """按请求构造 MarketStateService：engine 来自 app.state 单例，session 每次新建。
    若 market_state_engine 未初始化（startup 失败），直接抛 AttributeError（fail-fast）。
    """
    engine: MarketStateEngine = request.app.state.market_state_engine
    repo = MarketDataRepository(session)
    return MarketStateService(engine=engine, repo=repo)


def get_repo(session: AsyncSession = Depends(get_db)) -> MarketDataRepository:
    """提供 MarketDataRepository 依赖（供测试 override）。"""
    return MarketDataRepository(session)


def get_watchlist_service(
    session: AsyncSession = Depends(get_db),
) -> WatchlistService:
    """按请求构造 WatchlistService。"""
    repo = MarketDataRepository(session)
    return WatchlistService(repo=repo)


def get_signal_service(repo: MarketDataRepository = Depends(get_repo)) -> SignalService:
    """按请求构造 SignalService。"""
    return SignalService(repo)


def get_account_service(session: AsyncSession = Depends(get_db)) -> AccountService:
    """按请求构造 AccountService（直接操作 Session，见 phase6_account.md §4）。"""
    return AccountService(session)


def get_settings_service(session: AsyncSession = Depends(get_db)) -> SettingsService:
    """按请求构造 SettingsService。"""
    return SettingsService(session)


def get_setup_service(session: AsyncSession = Depends(get_db)) -> SetupService:
    """按请求构造 SetupService（首次启动向导状态）。"""
    return SetupService(session)


def get_config_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ConfigService:
    """按请求构造 ConfigService；Redis 从 app.state 取（未配置时降级为无缓存）。"""
    redis = getattr(request.app.state, "redis", None)
    return ConfigService(session, redis)


def get_notification_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
    config_service: ConfigService = Depends(get_config_service),
) -> NotificationService:
    """按请求构造 NotificationService；WxPusherAdapter 从 app.state 取（Step 9 注入）。

    对 REST 查询端点（列表/已读/未读数/wx-status），wx 为 None 不影响调用。
    """
    wx = getattr(request.app.state, "wxpusher", None)
    return NotificationService(session, config_service, wx)


def get_factor_monitor_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> FactorMonitorService:
    """按请求构造 FactorMonitorService。

    Phase 14 §14-5：注入 app.state.calendar 让 rolling_icir_state 走严格交易日
    窗口（SDD §7.4：252 + 20 交易日）。lifespan 启动失败导致 calendar 缺失时
    （Tushare token 未配置 + fallback_calendar 也失败）传 None → service 回退到
    日历日近似 + WARNING 日志。
    """
    calendar = getattr(request.app.state, "calendar", None)
    return FactorMonitorService(
        session, FactorMonitorEngine(), calendar=calendar,
    )


def get_report_service(session: AsyncSession = Depends(get_db)) -> ReportService:
    """按请求构造 ReportService。"""
    return ReportService(session)


def get_lineage_service(session: AsyncSession = Depends(get_db)) -> LineageService:
    """按请求构造 LineageService。"""
    return LineageService(session)


def get_attribution_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AttributionService:
    """按请求构造 AttributionService（Phase 12 §3.2.2）。

    Phase 13 P1-4 修订：注入 app.state.calendar 让 run_monthly 走严格交易日
    lookback（calendar 未初始化时 AttributionService 内 fallback 到日历天近似）。
    """
    calendar = getattr(request.app.state, "calendar", None)
    return AttributionService(session, AttributionRepository(), calendar=calendar)


def get_performance_service(session: AsyncSession = Depends(get_db)) -> PerformanceService:
    """按请求构造 PerformanceService。"""
    return PerformanceService(session)


def get_backtest_service(
    session: AsyncSession = Depends(get_db),
) -> BacktestService:
    """按请求构造 BacktestService（REST 查询端点用）。

    Phase 10 §4.4 评审 C-02/C-03：BacktestEngine 不再为单例。
    `_run_backtest_bg` 后台任务读取 `task.config_snapshot` 即时构造 engine，
    本依赖只为 `create_task / get_task / get_result` 等查询端点服务，engine=None。
    """
    return BacktestService(session, engine=None)


def get_scoring_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ScoringService:
    """按请求构造 ScoringService（calendar 来自 app.state 单例）。

    若 calendar 未初始化（无 TUSHARE_TOKEN），抛 AttributeError（fail-fast）。
    """
    from quantpilot.data.calendar import TradingCalendar
    from quantpilot.engine.pool import CandidatePoolManager
    from quantpilot.engine.scorer import Scorer
    from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
    from quantpilot.engine.strategies.momentum import MomentumStrategy
    from quantpilot.engine.strategies.trend import TrendStrategy
    from quantpilot.engine.strategies.value import ValueStrategy
    from quantpilot.engine.universe import UniverseFilter

    calendar: TradingCalendar = request.app.state.calendar
    repo = MarketDataRepository(session)
    return ScoringService(
        repo=repo,
        universe_filter=UniverseFilter(),
        strategies=[TrendStrategy(), MomentumStrategy(), MeanReversionStrategy(), ValueStrategy()],
        scorer=Scorer(),
        pool_manager=CandidatePoolManager(),  # 用 DEFAULT_UNIVERSE.pool_capacity（V1.0 → 50）
        calendar=calendar,
    )
