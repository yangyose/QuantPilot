"""RL-01~05: /auth/login + /auth/register 按 IP 限频 e2e（V1.5-G G-2b §4.3）。

全套件默认 limiter.enabled=False（conftest autouse），本文件用局部 fixture 打开
并 reset 计数（memory:// 存储，reset 安全）。不同 IP 桶经 X-Forwarded-For 模拟
（ASGITransport 的 client.host 固定，key_func 优先取代理头）。

超限断言一律经 `_burst()` 发起，理由见该函数 docstring：limits 的 fixed-window
把窗口锚定在**首次命中**，整批请求耗时若越过窗口长度，计数会被清掉，
「第 N+1 次必 429」会静默变成正常响应。
"""
from collections.abc import AsyncGenerator, Awaitable, Callable
from time import monotonic
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, Response

from quantpilot.api.deps import get_auth_service
from quantpilot.core.rate_limit import limiter
from quantpilot.main import app
from quantpilot.models.user import User
from quantpilot.services.auth_service import AuthService

_LOGIN_LIMIT = 10   # settings.rate_limit_login = "10/minute"
_REGISTER_LIMIT = 5  # settings.rate_limit_register = "5/hour"

_LOGIN_WINDOW_SECONDS = 60      # 与 "10/minute" 的窗口长度一致
_REGISTER_WINDOW_SECONDS = 3600  # 与 "5/hour" 的窗口长度一致
_MAX_BURST_ATTEMPTS = 3


@pytest.fixture
def rate_limited() -> AsyncGenerator[None, None]:
    """本测试内启用限频；前后 reset 防跨测试串桶。"""
    limiter.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()


@pytest.fixture
def mock_auth() -> AsyncGenerator[AsyncMock, None]:
    """mock AuthService：login 查无此人（→401），register 正常返回新用户。"""
    new_user = MagicMock(spec=User)
    new_user.username = "alice"
    new_user.email = "alice@example.com"
    new_user.level = "L1"
    mock = AsyncMock(spec=AuthService)
    mock.get_user_by_username.return_value = None
    mock.register.return_value = new_user
    app.dependency_overrides[get_auth_service] = lambda: mock
    yield mock
    app.dependency_overrides.pop(get_auth_service, None)


async def _burst(
    send: Callable[[int], Awaitable[Response]],
    count: int,
    *,
    window: float,
) -> list[Response]:
    """连发 count 次请求，保证整批落在**同一个限频窗口**内，返回响应列表。

    调用方拿到列表后再断言——本函数内部不断言，否则跨窗口时会先炸在断言上，
    失去重试机会。

    **为什么需要它**：limits 的 fixed-window 把窗口锚定在该 key 的首次命中
    （`MemoryStorage.incr` 在计数 0→1 时写 `expirations[key] = time.time() + expiry`），
    而非对齐整分钟。因此整批请求耗时一旦超过窗口长度，计数被清零，
    「第 N+1 次必 429」就静默退化成正常响应。

    2026-08-19 实测到：合跑 `tests/unit/ tests/e2e/` 时机器负载高，该轮耗时 527s
    （正常 141s），三个 `10/minute` 的 login 用例同时失败，而 `5/hour` 的 register
    用例不受影响——正是这个耦合的指纹。单独跑本文件必过，故极易被误判为偶发。

    这里**不放宽断言**（超限仍要求精确 429），只解除「断言正确性依赖机器快慢」
    这一耦合：整批跨了窗口就 reset 重来；连续 _MAX_BURST_ATTEMPTS 次都跨不过去
    才判失败——那时慢的是被测系统本身，属于应当暴露的真问题。
    """
    for _ in range(_MAX_BURST_ATTEMPTS):
        limiter.reset()
        started = monotonic()
        responses = [await send(i) for i in range(count)]
        if monotonic() - started < window:
            return responses
    raise AssertionError(
        f"连续 {_MAX_BURST_ATTEMPTS} 次都无法在 {window}s 窗口内发完 {count} 个请求，"
        "限频断言无法成立。这不是测试问题——请查被测端点为何如此慢。"
    )


# ---------------------------------------------------------------------------
# RL-01~02 : login 10/minute
# ---------------------------------------------------------------------------

async def test_login_over_limit_returns_429(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-01: 同一 IP 第 11 次 login → 429（前 10 次正常进入端点 → 401）。"""
    headers = {"X-Forwarded-For": "203.0.113.10"}
    body = {"username": "nobody", "password": "whatever-123"}
    responses = await _burst(
        lambda _: client.post("/api/v1/auth/login", json=body, headers=headers),
        _LOGIN_LIMIT + 1,
        window=_LOGIN_WINDOW_SECONDS,
    )
    assert [r.status_code for r in responses[:_LOGIN_LIMIT]] == [401] * _LOGIN_LIMIT
    assert responses[-1].status_code == 429


async def test_login_429_response_format(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-02: 429 响应遵守项目统一格式 {code: 429, data: null, msg: ...}。"""
    headers = {"X-Forwarded-For": "203.0.113.11"}
    body = {"username": "nobody", "password": "whatever-123"}
    responses = await _burst(
        lambda _: client.post("/api/v1/auth/login", json=body, headers=headers),
        _LOGIN_LIMIT + 1,
        window=_LOGIN_WINDOW_SECONDS,
    )
    resp = responses[-1]
    assert resp.status_code == 429
    payload = resp.json()
    assert payload["code"] == 429
    assert payload["data"] is None
    assert isinstance(payload["msg"], str) and payload["msg"]


# ---------------------------------------------------------------------------
# RL-03 : register 5/hour
# ---------------------------------------------------------------------------

async def test_register_over_limit_returns_429(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-03: 同一 IP 第 6 次 register → 429（前 5 次正常 → 200）。"""
    headers = {"X-Forwarded-For": "203.0.113.12"}
    responses = await _burst(
        lambda i: client.post(
            "/api/v1/auth/register",
            json={
                "username": f"alice{i}",
                "email": f"alice{i}@example.com",
                "password": "Str0ngPass",
            },
            headers=headers,
        ),
        _REGISTER_LIMIT + 1,
        window=_REGISTER_WINDOW_SECONDS,
    )
    assert [r.status_code for r in responses[:_REGISTER_LIMIT]] == [200] * _REGISTER_LIMIT
    assert responses[-1].status_code == 429


# ---------------------------------------------------------------------------
# RL-04 : 不同 IP 独立计数
# ---------------------------------------------------------------------------

async def test_different_ip_has_separate_bucket(
    client: AsyncClient, rate_limited: None, mock_auth: AsyncMock
):
    """RL-04: IP-A 打满 login 限额后，IP-B 仍可正常请求（进入端点 → 401）。

    IP-B 的请求与 IP-A 的整批共处同一次 `_burst`，确保断言成立时 A 确实仍处于
    超限状态——否则窗口若在两者之间失效，本用例会「通过」但什么都没证明。
    """
    body = {"username": "nobody", "password": "whatever-123"}
    headers_a = {"X-Forwarded-For": "203.0.113.13"}
    headers_b = {"X-Forwarded-For": "203.0.113.14"}
    responses = await _burst(
        lambda i: client.post(
            "/api/v1/auth/login",
            json=body,
            headers=headers_a if i <= _LOGIN_LIMIT else headers_b,
        ),
        _LOGIN_LIMIT + 2,
        window=_LOGIN_WINDOW_SECONDS,
    )
    assert responses[_LOGIN_LIMIT].status_code == 429   # IP-A 第 11 次
    assert responses[-1].status_code == 401             # IP-B 首次，独立桶


# ---------------------------------------------------------------------------
# RL-05 : 默认（未启用 fixture）不限频——保证全套 e2e 不被限频破坏
# ---------------------------------------------------------------------------

async def test_limiter_disabled_by_default(client: AsyncClient, mock_auth: AsyncMock):
    """RL-05: conftest autouse 关闭限频后，连续超限次请求全部 401（不出现 429）。

    限频关闭时不计数，与窗口无关，故无需 `_burst`。
    """
    body = {"username": "nobody", "password": "whatever-123"}
    headers = {"X-Forwarded-For": "203.0.113.15"}
    for _ in range(_LOGIN_LIMIT + 2):
        resp = await client.post("/api/v1/auth/login", json=body, headers=headers)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# RL-06 : _burst 窗口守卫自检
# ---------------------------------------------------------------------------

async def test_burst_rejects_window_overrun():
    """RL-06: 整批必然跨窗口时，_burst 须重试满次数后判失败。

    上面四个用例在快机器上无论有没有守卫都会通过，故守卫本身必须被单独钉死，
    否则它退化成装饰性代码，2026-08-19 那个 flake 会悄悄回来。
    """
    calls = 0

    async def _send(_i: int) -> Response:
        nonlocal calls
        calls += 1
        return MagicMock(spec=Response)

    with pytest.raises(AssertionError, match="限频断言无法成立"):
        await _burst(_send, 2, window=0.0)   # window=0 → 必然判定为跨窗口
    assert calls == 2 * _MAX_BURST_ATTEMPTS  # 确实重试了，而非一次就放弃


async def test_burst_retries_then_returns_clean_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RL-07: 首批跨窗口 → reset 重来 → 次批落在窗口内 → 返回的是**次批**响应。

    这是真正防 flake 的那条路径（RL-06 只覆盖了放弃路径）。用可控时钟精确驱动，
    不靠真实等待：_burst 每轮取两次 monotonic（起点 + 终点）。
    """
    ticks = iter([
        0.0, 999.0,  # 第 1 批：耗时 999s → 判定跨窗口，丢弃并重试
        0.0, 1.0,    # 第 2 批：耗时 1s → 窗口内，采纳
    ])
    monkeypatch.setattr(f"{__name__}.monotonic", lambda: next(ticks))

    calls = 0

    async def _send(_i: int) -> Response:
        nonlocal calls
        calls += 1
        resp = MagicMock(spec=Response)
        resp.status_code = 500 if calls <= 2 else 200  # 前 2 次属被丢弃的第 1 批
        return resp

    responses = await _burst(_send, 2, window=60.0)
    assert calls == 4, "应恰好重试一次（第 1 批 2 次 + 第 2 批 2 次）"
    assert [r.status_code for r in responses] == [200, 200], "返回的必须是次批响应"
