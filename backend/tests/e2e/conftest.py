"""e2e 目录专属：**任何触达真实 DB 的路径立刻失败**，与机器上有没有 DB 无关。

## 为什么（2026-09-17，CI 连红三次才发现）

`test_bt_09c` 首版在「出窗那一跳」没 mock 日历，请求继续走到 `ConfigService` → 真实 DB。
本机 5432 恰好开着 → 绿；CI 没有 DB → 全局 engine 的 QueuePool 跨 loop，报的是
`Future attached to a different loop`（CLAUDE.md §4.11 那一族），**不是**连接拒绝——
看堆栈根本想不到是「测试碰了 DB」。三个 commit 的 CI 红了才追到这里。

e2e 的契约本来就是「无 DB」（`client` fixture 已 stub 掉 account/level 等查库依赖），
但那是逐个记得的清单，漏一个就回到靠环境碰运气。这里改成**结构性**的：给全局 engine
挂 `do_connect` 监听器，任何路径（`get_db` / 直接 `AsyncSessionLocal()` / 脚本内自建
session）只要真要拿连接，都在同一处以同一句话失败——错误信息直接指出「哪个 e2e 用例
碰了 DB」，不会再伪装成跨 loop 或连接拒绝。
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import event

from quantpilot.core.database import engine


class E2ETouchedDatabase(RuntimeError):
    """e2e 用例触达了真实 DB——该 mock 的依赖没 mock（不是环境问题）。"""


def _refuse_connect(*_args: object, **_kwargs: object) -> None:
    raise E2ETouchedDatabase(
        "e2e 用例触达了真实 DB：某个查库依赖（get_db / ConfigService / calendar …）没有被 "
        "mock 或 override。请 mock 它，而不是给测试机起一个 DB——起了 DB 只会让本机绿、CI 红。"
    )


@pytest.fixture(autouse=True, scope="session")
def _e2e_forbid_database() -> Generator[None, None, None]:
    sync_engine = engine.sync_engine
    event.listen(sync_engine, "do_connect", _refuse_connect)
    try:
        yield
    finally:
        event.remove(sync_engine, "do_connect", _refuse_connect)
