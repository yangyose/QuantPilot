"""C5 插件执行开关：默认值取失效方向 + 仓库 compose 双写（运维红线②，2026-09-23）。

## 为什么要专门钉这个

红线②的实例摆在那里：`BACKTEST_ENABLED`（防 4 次 OOM 宕机的那个开关）自 2026-06-29 起
**只存在于服务器的 compose**，仓库那一半漏了，直到 2026-08-27 才发现——期间任何「按 git
重建一套生产」都会静默丢掉它、把回测重新打开。而 `docker exec printenv` 在服务器上照样
通过，所以现场核验发现不了。

`PLUGIN_EXECUTION_ENABLED` 的失效后果比回测那次更重：回测打开只是内存风险，插件执行打开
等于**在生产上跑用户提交的任意 Python**，而沙箱按设计 §7.1 挡不住蓄意逃逸。所以这里把
三件事钉成测试：默认值必须是 False、仓库 compose 白名单里必须有它、且 compose 的默认值
必须是失效方向（`:-false`）。
"""
from __future__ import annotations

import re
from pathlib import Path

from quantpilot.core.config import Settings

_REPO = Path(__file__).resolve().parents[3]
_COMPOSE = _REPO / "docker-compose.prod.yml"
_ENV_EXAMPLE = _REPO / ".env.prod.example"
_KEY = "PLUGIN_EXECUTION_ENABLED"


def test_default_is_disabled() -> None:
    """代码默认值 = 失效方向。漏配环境变量时保持关闭，而不是打开。"""
    assert Settings().plugin_execution_enabled is False


def test_memory_limit_bypass_defaults_to_disabled() -> None:
    """无 RLIMIT 平台的降级开关同样默认关——开着就等于「沙箱装了」变成一句空话。"""
    assert Settings().plugin_allow_without_memory_limit is False


def test_repo_compose_whitelists_the_key() -> None:
    """红线②：仓库 compose 的 `environment:` 是白名单（非全量透传），漏写即永久失效。"""
    text = _COMPOSE.read_text(encoding="utf-8")
    assert f"{_KEY}:" in text, (
        f"{_KEY} 不在仓库 docker-compose.prod.yml 的 environment 白名单里 → "
        "按 git 重建生产时该开关静默消失（红线②那次 BACKTEST_ENABLED 就是这样丢的）"
    )


def test_compose_default_is_the_disabled_direction() -> None:
    m = re.search(rf"^\s*{_KEY}:\s*\$\{{{_KEY}:-(?P<default>[^}}]*)\}}", _COMPOSE.read_text(
        encoding="utf-8"), re.M)
    assert m is not None, f"{_KEY} 必须写成 ${{{_KEY}:-<默认值>}} 形式"
    assert m.group("default").strip().lower() == "false", (
        "compose 默认值必须是 false（失效方向）——默认 true 时漏配 .env.prod "
        "就会在生产上放开「跑用户提交的任意 Python」"
    )


def test_env_example_documents_it_as_false() -> None:
    """`.env.prod.example` 是重建生产的抄写源，必须显式写明 false。"""
    lines = [
        ln.strip() for ln in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if ln.strip().startswith(f"{_KEY}=")
    ]
    assert lines == [f"{_KEY}=false"], f"实得 {lines}"
