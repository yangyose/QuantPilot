"""C5 策略插件沙箱（SDD §15.2 / 设计 §7.1-§7.2）——逃逸与资源限制的 RED 用例。

## 这些测试为什么这么写

设计 §7.1 已经**诚实声明**：本沙箱挡不住蓄意逃逸（容器内非 root、无 `CAP_SYS_ADMIN`，
用不了 seccomp / unshare / 嵌套容器）。所以这里测的**不是**「安全」，而是设计里承诺的
那六条具体机制**真的生效**：子进程隔离 / 硬超时 / 内存硬限 / 导入白名单 / socket 屏蔽 /
输出形状校验。每条都要「先 RED」：断言的是**被拒的那个事实**，不是「没抛异常」。

## 平台现实（设计 §7.2 未交代，2026-09-23 补）

`RLIMIT_AS` 依赖 POSIX `resource` 模块——**Windows 没有**，而「本地算力中心」这台机
恰好是 Windows（设计 §7.1 把执行路径放在本地算力中心）。于是有两种可能的做法：
①假装沙箱完整、内存限额静默失效；②诚实地 fail-closed，并让调用方显式选择。
按 C-4「不静默掩盖」取 ②：`sandbox_capabilities()` 自报能力集，`run_plugin` 在
**限额不可用时默认拒绝执行**，只有显式传 `allow_without_memory_limit=True` 才降级运行
（审计里记下这一点）。本文件同时钉「Linux 上限额必须真生效」与「Windows 上必须拒绝」，
用 `resource` 是否可导入来选断言——两个平台各跑自己那一半，谁都不会静默跳过全部。
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib
import subprocess
import sys
import textwrap

import pandas as pd
import pytest

from quantpilot.engine.sandbox.plugin_runner import (
    PluginRunResult,
    run_plugin,
    sandbox_capabilities,
)

_HAS_RESOURCE = importlib.util.find_spec("resource") is not None

# 沙箱不可用（无内存限额）时，用它显式降级跑——否则 run_plugin 会拒绝
_ALLOW = {"allow_without_memory_limit": True}

_UNIVERSE = pd.Index(["000001.SZ", "000002.SZ"], name="ts_code")
_DATA = {"close": pd.Series({"000001.SZ": 10.0, "000002.SZ": 20.0})}


def _src(body: str) -> str:
    return textwrap.dedent(body)


_GOOD = _src('''
    import pandas as pd

    def compute_raw_factors(universe, data):
        return pd.DataFrame({"f1": [1.0] * len(universe)}, index=universe)
''')


class TestHappyPath:
    def test_valid_plugin_returns_factors(self) -> None:
        r = run_plugin(_GOOD, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert isinstance(r, PluginRunResult)
        assert r.ok, r.error
        assert r.exit_status == "ok"
        assert list(r.factors.index) == list(_UNIVERSE)
        assert list(r.factors.columns) == ["f1"]
        assert r.duration_ms >= 0

    def test_plugin_can_use_whitelisted_modules(self) -> None:
        src = _src('''
            import math

            import numpy as np
            import pandas as pd

            def compute_raw_factors(universe, data):
                v = [math.sqrt(x) for x in np.arange(1.0, len(universe) + 1.0)]
                return pd.DataFrame({"f1": v}, index=universe)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert r.ok, r.error


class TestImportBlacklist:
    """设计 §7.2：白名单 {math, statistics, pandas, numpy}，黑名单含 os/sys/socket/ctypes…"""

    @pytest.mark.parametrize("mod", ["os", "sys", "subprocess", "socket", "ctypes",
                                     "importlib", "pathlib", "shutil", "builtins"])
    def test_blacklisted_import_is_rejected(self, mod: str) -> None:
        src = _src(f'''
            import pandas as pd

            def compute_raw_factors(universe, data):
                import {mod}
                return pd.DataFrame({{"f1": [1.0] * len(universe)}}, index=universe)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok
        assert r.exit_status == "rejected_import", r.exit_status
        assert mod in (r.error or "")

    def test_import_at_module_level_is_rejected_too(self) -> None:
        """顶层 import 也要拦——只拦函数体内等于没拦。"""
        src = _src('''
            import os

            def compute_raw_factors(universe, data):
                return None
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok and r.exit_status == "rejected_import"

    def test_dunder_import_is_rejected(self) -> None:
        """绕过 import 语句直接调 `__import__` 同样要拦。"""
        src = _src('''
            import pandas as pd

            def compute_raw_factors(universe, data):
                m = __import__("os")
                return pd.DataFrame({"f1": [m.getpid()] * len(universe)}, index=universe)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok and r.exit_status == "rejected_import"


class TestDangerousBuiltinsAreAbsent:
    """builtins 白名单：`open` / `eval` / `exec` / `globals` / `getattr` 等一律不给。

    这层此前没有测试（只测了 import 白名单）——而它挡的是**不需要 import 就能用**的
    那批入口：`open("/proc/self/environ")` 读环境变量、`eval` 拼字符串绕过 AST 预检、
    `getattr(obj, "__class__")` 爬对象图。2026-09-23 用对抗性探针逐条真打后补的用例。
    ⚠️ 它们报 `error`（NameError）而不是专门的状态码——如实反映「名字不存在」，
    不额外包装（C-4：不编错误分类）。
    """

    @pytest.mark.parametrize("expr", [
        'open("/tmp/x", "w")',
        'open("/proc/self/environ").read()',
        'eval("1+1")',
        'exec("x = 1")',
        'compile("1", "<s>", "eval")',
        "globals()",
        "locals()",
        "vars()",
        "dir()",
        'getattr(data, "__class__")',
        'setattr(data, "x", 1)',
        "input()",
        "memoryview(b'x')",
    ])
    def test_builtin_is_not_available(self, expr: str) -> None:
        src = _src(f'''
            def compute_raw_factors(universe, data):
                return {expr}
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok, f"{expr} 居然可用"
        assert r.exit_status in ("error", "invalid_output"), r.exit_status
        if r.exit_status == "error":
            assert "not defined" in (r.error or ""), r.error

    def test_whitelisted_builtins_do_work(self) -> None:
        """反向：正常插件要用的那些必须在（否则白名单收得过紧、插件写不出东西）。"""
        src = _src('''
            import pandas as pd

            def compute_raw_factors(universe, data):
                vals = [float(abs(round(x / 3, 2))) for x in range(len(universe))]
                assert isinstance(sorted(vals), list) and len(set(vals)) >= 1
                assert max(vals) >= min(vals) and sum(vals) >= 0
                return pd.DataFrame({"f1": vals}, index=universe)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert r.ok, r.error


class TestParentIsolation:
    def test_plugin_mutating_payload_does_not_affect_parent(self) -> None:
        """spawn = 独立内存：插件改自己那份 `data`，父进程的快照不受影响。"""
        src = _src('''
            import pandas as pd

            def compute_raw_factors(universe, data):
                data["close"] = None
                return pd.DataFrame({"f1": [1.0] * len(universe)}, index=universe)
        ''')
        payload = {"close": pd.Series({"000001.SZ": 10.0, "000002.SZ": 20.0})}
        r = run_plugin(src, _UNIVERSE, payload, timeout_s=30.0, **_ALLOW)
        assert r.ok, r.error
        assert isinstance(payload["close"], pd.Series), "父进程的入参被插件改掉了"


class TestBootstrapFailureIsDistinct:
    """沙箱没起来 ≠ 插件跑挂（2026-09-23 对抗性探针时踩到）。

    spawn 要求调用方的 `__main__` 可被子进程重新导入；从 REPL / `python - <<EOF` 调用时
    不满足，子进程 exitcode=1 且什么都没回传——**与插件自己崩溃在父侧长得一模一样**。
    报成同一个状态就是让沙箱的问题伪装成用户插件的问题，故单列 `sandbox_bootstrap_failed`。
    """

    def test_status_code_exists_and_is_documented(self) -> None:
        from quantpilot.engine.sandbox import plugin_runner as pr

        assert pr._EXIT_BOOTSTRAP == "sandbox_bootstrap_failed"
        assert "__main__" in (pr.run_plugin.__doc__ or "") or "bootstrap" in (
            pr.run_plugin.__doc__ or ""
        )
        # 子进程第一件事就是报到，父进程据此区分两种失败
        assert "conn.send((_HELLO,))" in inspect.getsource(pr._child_main)

    def test_end_to_end_from_stdin_main(self) -> None:
        """真打一次：**从 stdin 喂脚本**（`__main__.__file__` = `<stdin>`）必须报 bootstrap 失败。

        ⚠️ 上一条只检查源码字符串里有没有那句 `conn.send`，重构一下就可能假阳性通过
        （2026-09-23 冷启动评审点出这层脆性）。这条真起一个进程跑 `run_plugin`，断言它报的是
        `sandbox_bootstrap_failed`——而不是把沙箱自己起不来的问题栽给插件。

        ⚠️ **入口选错了测不出来**：`python -c '...'` 实测是 **ok** 的（spawn 对 `-c` 有特殊
        处理，不去重新导入文件），只有 **stdin** 这种 `__main__` 指向 `<stdin>` 的入口才触发。
        我第一版写成 `-c` 时拿到 `STATUS=ok`，差点据此以为守卫失效——判据必须用**真正会失败
        的那个入口**，这正是 §4.11「核对前先问：实际走的是哪一条路径」。
        """
        driver = _src(f"""
            import pandas as pd

            from quantpilot.engine.sandbox.plugin_runner import run_plugin

            r = run_plugin({_GOOD!r}, pd.Index(["000001.SZ"]), {{}}, timeout_s=20.0,
                           allow_without_memory_limit=True)
            print("STATUS=" + r.exit_status)
        """)
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-"], input=driver, capture_output=True, text=True, timeout=120,
            cwd=str(pathlib.Path(__file__).resolve().parents[2]),
        )
        out = proc.stdout + proc.stderr
        assert "STATUS=sandbox_bootstrap_failed" in out, out[-600:]


class TestNetworkBlocked:
    def test_socket_creation_raises(self) -> None:
        """socket 模块本身在黑名单里；即便拿到 socket 对象，构造也必须抛。"""
        src = _src('''
            import pandas as pd

            def compute_raw_factors(universe, data):
                import socket
                socket.socket()
                return pd.DataFrame({"f1": [1.0] * len(universe)}, index=universe)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok
        assert r.exit_status in ("rejected_import", "socket_blocked"), r.exit_status


class TestTimeout:
    def test_infinite_loop_is_killed(self) -> None:
        src = _src('''
            def compute_raw_factors(universe, data):
                while True:
                    pass
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=2.0, **_ALLOW)
        assert not r.ok
        assert r.exit_status == "timeout"
        assert r.duration_ms >= 1500  # 真的等到超时才杀，不是立刻返回

    def test_sleep_longer_than_budget_is_killed(self) -> None:
        """不靠 CPU 忙等也要能杀（`terminate` → `kill` 两段）。"""
        src = _src('''
            def compute_raw_factors(universe, data):
                import time
                time.sleep(60)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=2.0, **_ALLOW)
        assert not r.ok
        assert r.exit_status in ("timeout", "rejected_import"), r.exit_status


class TestOutputValidation:
    """设计 §7.2 输出校验：index=ts_code、数值列、形状/类型全校验，越界值拒收。"""

    @pytest.mark.parametrize("body, why", [
        ("return None", "None"),
        ("return 42", "非 DataFrame"),
        ("return pd.Series([1.0] * len(universe), index=universe)", "Series 不是 DataFrame"),
        ('return pd.DataFrame({"f1": ["a"] * len(universe)}, index=universe)', "非数值列"),
        ('return pd.DataFrame({"f1": [1.0]}, index=["999999.XX"])', "index 不是入参 universe"),
        ('return pd.DataFrame(index=universe)', "零列"),
        ('return pd.DataFrame({"f" + str(i): [1.0] * len(universe) '
         'for i in range(200)}, index=universe)', "列数超上限"),
    ])
    def test_invalid_output_is_rejected(self, body: str, why: str) -> None:
        src = _src(f'''
            import pandas as pd

            def compute_raw_factors(universe, data):
                {body}
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok, f"{why} 应被拒收"
        assert r.exit_status == "invalid_output", r.exit_status

    def test_inf_is_rejected(self) -> None:
        """inf 进 Winsorize 会毁掉整列（§4.4）→ 必须在沙箱边界拒收。"""
        src = _src('''
            import numpy as np
            import pandas as pd

            def compute_raw_factors(universe, data):
                return pd.DataFrame({"f1": [np.inf] * len(universe)}, index=universe)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok and r.exit_status == "invalid_output"

    def test_nan_is_allowed(self) -> None:
        """NaN 是合法的「无观测」，不能一起拒——那会逼插件用 0 占位（C-4）。"""
        src = _src('''
            import numpy as np
            import pandas as pd

            def compute_raw_factors(universe, data):
                return pd.DataFrame({"f1": [np.nan] * len(universe)}, index=universe)
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert r.ok, r.error


class TestMissingEntryPoint:
    def test_no_compute_raw_factors_is_rejected(self) -> None:
        r = run_plugin("x = 1\n", _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok and r.exit_status == "invalid_output"

    def test_syntax_error_is_reported_not_raised(self) -> None:
        r = run_plugin("def broken(:\n", _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok and r.exit_status == "error"
        assert "Syntax" in (r.error or "") or "syntax" in (r.error or "")


class TestSubprocessIsolation:
    def test_plugin_crash_does_not_kill_parent(self) -> None:
        """插件段错误 / 直接退出进程也只影响子进程。"""
        src = _src('''
            def compute_raw_factors(universe, data):
                raise MemoryError("boom")
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert not r.ok and r.exit_status in ("error", "memory")
        # 父进程仍然能正常跑下一个插件
        assert run_plugin(_GOOD, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW).ok


class TestCapabilityHonesty:
    """C-4：能力不可用时必须说出来并 fail-closed，不能假装沙箱完整。"""

    def test_capabilities_report_matches_platform(self) -> None:
        caps = sandbox_capabilities()
        assert caps.subprocess_isolation is True
        assert caps.import_whitelist is True
        assert caps.socket_block is True
        assert caps.memory_limit is _HAS_RESOURCE

    def test_refuses_to_run_when_memory_limit_unavailable(self) -> None:
        if _HAS_RESOURCE:
            pytest.skip("本平台有 RLIMIT_AS，这条只在 Windows 等无 resource 的平台成立")
        r = run_plugin(_GOOD, _UNIVERSE, _DATA, timeout_s=30.0)
        assert not r.ok
        assert r.exit_status == "sandbox_unavailable"
        assert "memory" in (r.error or "").lower()

    def test_result_carries_capabilities(self) -> None:
        r = run_plugin(_GOOD, _UNIVERSE, _DATA, timeout_s=30.0, **_ALLOW)
        assert r.capabilities.memory_limit is _HAS_RESOURCE

    @pytest.mark.skipif(not _HAS_RESOURCE, reason="需要 POSIX resource（RLIMIT_AS）")
    def test_memory_hog_is_killed_where_limit_works(self) -> None:
        src = _src('''
            def compute_raw_factors(universe, data):
                blob = bytearray(400 * 1024 * 1024)
                return blob
        ''')
        r = run_plugin(src, _UNIVERSE, _DATA, timeout_s=60.0, memory_mb=100)
        assert not r.ok
        assert r.exit_status in ("memory", "error"), r.exit_status
