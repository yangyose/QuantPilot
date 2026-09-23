"""C5 策略插件沙箱执行器（SDD §15.2 / 设计 §7.1-§7.2）。

## 先说清楚挡什么、不挡什么（设计 §7.1 的诚实声明，代码里再说一遍）

挡得住：插件死循环/超时（硬超时 + 两段 kill）、崩溃波及主进程（spawn 子进程）、
误用文件/网络/系统模块（**deny-by-default** 的导入白名单 + socket 桩）、
输出形状非法（index/列数/dtype/inf 全校验）、内存超预算（**仅 POSIX**，见下）。

**挡不住**：蓄意逃逸（`ctypes` / C 扩展 / `/proc` / 取 `__class__` 爬对象图）。后端容器非
root、无 `CAP_SYS_ADMIN`，容器内用不了 seccomp / unshare / 嵌套容器。故产品级决策是
**生产禁用插件执行**（`plugin_execution_enabled=false` → 端点 503），执行只在本地算力中心。

## 两个与设计文档不同的实现决定（2026-09-23，实测后定）

1. **内存限额是「基线 + 预算」的增量，不是绝对 100MB。** SDD §15.2 写「内存 ≤100MB」，
   直译成 `RLIMIT_AS = 100MB` 会**连 happy path 一起打死**——子进程用 spawn 起，pandas +
   numpy 一进来虚拟地址空间就远超 100MB（numpy 的 arena 动辄几百 MB）。所以先量本进程
   已占的 VmSize（`/proc/self/statm`），再 `setrlimit(RLIMIT_AS, baseline + memory_mb)`，
   语义 = 「插件自己最多再申请 100MB」。这才是 SDD 那条限制想表达的东西。
2. **限额不可用时 fail-closed。** `resource` 是 POSIX-only，而「本地算力中心」这台机是
   Windows（设计 §7.1 恰恰把执行放在那）。两种选择：假装沙箱完整、或诚实拒绝。按 C-4 取
   后者：`sandbox_capabilities()` 自报能力集，`run_plugin` 默认拒绝（`sandbox_unavailable`），
   要在无限额平台上跑必须显式传 `allow_without_memory_limit=True`，且结果里带着能力集
   供审计落库。**不要把默认值改成 True** ——那等于把「沙箱装了」变成一句空话。

判据：`tests/unit/test_plugin_sandbox.py`（每条机制一条用例，平台相关的两条按
`resource` 可用性各跑一半，不会两边都静默跳过）。
"""
from __future__ import annotations

import ast
import logging
import multiprocessing as mp
import re
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# 插件可 import 的顶层模块（**deny-by-default**：不在这里的一律拒）
ALLOWED_MODULES: frozenset[str] = frozenset({"math", "statistics", "pandas", "numpy"})
# 输出上限：列数（因子数）与单元格总数，防「返回一个巨表把父进程也拖垮」
MAX_FACTOR_COLS = 64
# 传给插件的 builtins 白名单。⚠️ 故意不含 open/eval/exec/compile/input/globals/locals/
# vars/dir/getattr/setattr/__import__（后者由我们自己的实现替换）——见文件头「挡不住」。
_SAFE_BUILTIN_NAMES = (
    "abs all any bool dict divmod enumerate filter float format frozenset int isinstance "
    "issubclass len list map max min next print range repr reversed round set slice sorted "
    "str sum tuple type zip True False None Exception ValueError TypeError KeyError "
    "IndexError ZeroDivisionError ArithmeticError RuntimeError StopIteration"
).split()

_EXIT_OK = "ok"
_EXIT_REJECTED_IMPORT = "rejected_import"
_EXIT_SOCKET = "socket_blocked"
_EXIT_TIMEOUT = "timeout"
_EXIT_MEMORY = "memory"
_EXIT_INVALID_OUTPUT = "invalid_output"
_EXIT_ERROR = "error"
_EXIT_UNAVAILABLE = "sandbox_unavailable"
_EXIT_BOOTSTRAP = "sandbox_bootstrap_failed"
_HELLO = "__sandbox_ready__"

# 错误文本落审计前的最小脱敏（与 Phase 13 `SecretFilter` 同精神：按**形状**匹配 URL 凭证，
# 不按键名——键名匹配只挡得住恰好用了那个键名的调用点，见 CLAUDE.md §4.11 第 7 例）。
_CRED_URL = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s:/@]+:[^\s:/@]+@")


def _redact(text: str, limit: int = 2000) -> str:
    return _CRED_URL.sub(r"\1***:***@", text)[:limit]


@dataclass(frozen=True)
class SandboxCapabilities:
    """本平台上沙箱实际具备的能力——**自报，不假装**（C-4）。"""

    subprocess_isolation: bool
    import_whitelist: bool
    socket_block: bool
    memory_limit: bool


@dataclass(frozen=True)
class PluginRunResult:
    ok: bool
    exit_status: str
    factors: pd.DataFrame | None
    error: str | None
    duration_ms: int
    peak_memory_kb: int | None
    capabilities: SandboxCapabilities


def _resource_module() -> Any | None:
    try:
        import resource  # noqa: PLC0415  (POSIX-only，故意延迟导入)
    except ImportError:
        return None
    return resource


def sandbox_capabilities() -> SandboxCapabilities:
    """当前平台的沙箱能力集。`memory_limit` 取决于 POSIX `resource` 是否可用。"""
    return SandboxCapabilities(
        subprocess_isolation="spawn" in mp.get_all_start_methods(),
        import_whitelist=True,
        socket_block=True,
        memory_limit=_resource_module() is not None,
    )


# ------------------------------------------------------------------ 静态预检

def static_check_imports(source: str) -> str | None:
    """AST 预检：任何 import 的顶层模块不在白名单即拒；`__import__` 调用一律拒。

    为什么静态 + 运行时两层都要：静态这层能在 **exec 之前**就拦住模块级 import 并**指名
    那个模块**（错误信息可用）；运行时那层挡住动态构造的导入（`importlib` 之类）。
    只做运行时会让「模块级 import os」在 exec 那一刻才炸、错误里看不出是哪个模块；
    只做静态则挡不住动态导入。返回被拒模块名（供错误信息），通过则 None。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in ALLOWED_MODULES:
                    return top
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top not in ALLOWED_MODULES:
                return top or "(relative import)"
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "__import__":
                return "__import__"
            if isinstance(fn, ast.Attribute) and fn.attr in ("import_module", "__import__"):
                return fn.attr
    return None


# ------------------------------------------------------------------ 子进程侧

def _baseline_vms_bytes() -> int:
    """本进程已占的虚拟地址空间（Linux 走 /proc/self/statm；拿不到回落 0）。

    用它把 `RLIMIT_AS` 设成「基线 + 预算」——见文件头实现决定 1。
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            pages = int(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0
    import os  # noqa: PLC0415

    return pages * os.sysconf("SC_PAGE_SIZE")


def _apply_limits(memory_mb: int) -> bool:
    """施加资源限制；返回内存限额是否真的加上了。"""
    resource = _resource_module()
    if resource is None:
        return False
    budget = _baseline_vms_bytes() + memory_mb * 1024 * 1024
    ok = True
    try:
        resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
    except (ValueError, OSError):
        ok = False
    for name, value in (("RLIMIT_NPROC", 0), ("RLIMIT_NOFILE", 64), ("RLIMIT_CORE", 0)):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            soft, hard = resource.getrlimit(limit)
            resource.setrlimit(limit, (min(value, hard) if hard > 0 else value, hard))
        except (ValueError, OSError):
            pass  # 收紧失败不影响主机制（内存/超时/导入），且已在 capabilities 里自报
    return ok


class _ImportRejected(Exception):
    """插件请求了白名单外的模块。"""


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    top = name.split(".")[0]
    if top not in ALLOWED_MODULES:
        raise _ImportRejected(name)
    import builtins  # noqa: PLC0415

    return builtins.__import__(name, globals, locals, fromlist, level)


def _block_socket() -> None:
    """把 socket 构造替换成抛异常的桩（子进程是一次性的，改全局无副作用）。"""
    try:
        import socket  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return

    def _blocked(*_a: object, **_k: object) -> None:
        raise PermissionError("插件沙箱禁止网络访问（socket 已屏蔽）")

    socket.socket = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]


def _validate_output(obj: object, universe: pd.Index) -> str | None:
    """输出校验；通过返回 None，否则返回拒收原因。"""
    if not isinstance(obj, pd.DataFrame):
        return f"返回值必须是 pandas.DataFrame，实得 {type(obj).__name__}"
    if obj.shape[1] == 0:
        return "返回值没有任何因子列"
    if obj.shape[1] > MAX_FACTOR_COLS:
        return f"因子列数 {obj.shape[1]} 超过上限 {MAX_FACTOR_COLS}"
    if list(obj.index) != list(universe):
        return "返回值的 index 必须与入参 universe 逐值相同（顺序一致）"
    for col in obj.columns:
        if not isinstance(col, str) or not col or len(col) > 64:
            return f"非法因子列名：{col!r}"
        series = obj[col]
        if not pd.api.types.is_numeric_dtype(series):
            return f"因子列 {col!r} 不是数值列（实得 {series.dtype}）"
        arr = series.to_numpy(dtype=float, copy=False)
        if bool((arr == float("inf")).any() or (arr == float("-inf")).any()):
            return f"因子列 {col!r} 含 inf（NaN 可以，inf 会毁掉 Winsorize 整列）"
    return None


def _child_main(source: str, universe: pd.Index, data: object, memory_mb: int, conn: Any) -> None:
    """子进程入口：限额 → 屏蔽 → exec → 调用 → 校验 → 回传。**不抛异常出去**。"""
    peak_kb: int | None = None
    try:
        # 第一件事：报到。父进程据此区分「沙箱没起来」与「插件跑挂」——
        # spawn 要求父进程的 `__main__` 可被子进程重新导入（REPL / stdin 脚本里不成立），
        # 那种失败在父侧只看到 exitcode=1，与插件自己崩溃**长得一模一样**。
        # 把这两件事报成同一个状态，等于让沙箱的问题伪装成用户插件的问题（C-4）。
        conn.send((_HELLO,))
        # 先把白名单模块导进来（在装限制性 __import__ 之前），这样插件的
        # `import pandas` 只是从 sys.modules 取已建好的模块对象，不会触发 pandas
        # 自己那一大堆内部 import（那些会撞上白名单）
        import math  # noqa: F401, PLC0415
        import statistics  # noqa: F401, PLC0415

        import numpy  # noqa: F401, PLC0415

        _block_socket()
        limited = _apply_limits(memory_mb)

        rejected = static_check_imports(source)
        if rejected is not None:
            conn.send((_EXIT_REJECTED_IMPORT, None, f"禁止导入模块：{rejected}", peak_kb, limited))
            return

        import builtins  # noqa: PLC0415

        safe_builtins = {
            n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES if hasattr(builtins, n)
        }
        safe_builtins["__import__"] = _restricted_import
        namespace: dict[str, Any] = {"__builtins__": safe_builtins, "__name__": "qp_plugin"}

        exec(compile(source, "<plugin>", "exec"), namespace)  # noqa: S102
        fn = namespace.get("compute_raw_factors")
        if not callable(fn):
            conn.send((
                _EXIT_INVALID_OUTPUT, None, "插件必须定义 compute_raw_factors(universe, data)",
                peak_kb, limited,
            ))
            return

        out = fn(universe, data)
        reason = _validate_output(out, universe)
        peak_kb = _peak_rss_kb()
        if reason is not None:
            conn.send((_EXIT_INVALID_OUTPUT, None, reason, peak_kb, limited))
            return
        conn.send((_EXIT_OK, out.astype(float), None, peak_kb, limited))
    except _ImportRejected as exc:
        conn.send((_EXIT_REJECTED_IMPORT, None, f"禁止导入模块：{exc}", peak_kb, True))
    except PermissionError as exc:
        conn.send((_EXIT_SOCKET, None, _redact(str(exc)), peak_kb, True))
    except MemoryError:
        conn.send((_EXIT_MEMORY, None, "插件超出内存预算（RLIMIT_AS）", peak_kb, True))
    except SyntaxError as exc:
        conn.send((_EXIT_ERROR, None, f"SyntaxError: {_redact(str(exc))}", peak_kb, True))
    except BaseException as exc:  # noqa: BLE001  (子进程边界：任何异常都要变成结果)
        conn.send((
            _EXIT_ERROR, None, f"{type(exc).__name__}: {_redact(str(exc))}", peak_kb, True,
        ))
    finally:
        try:
            conn.close()
        except OSError:  # pragma: no cover
            pass


def _peak_rss_kb() -> int | None:
    resource = _resource_module()
    if resource is None:
        return None
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ValueError, OSError):  # pragma: no cover
        return None


# ------------------------------------------------------------------ 父进程侧

def run_plugin(
    source: str,
    universe: pd.Index,
    data: object,
    *,
    timeout_s: float,
    memory_mb: int = 100,
    allow_without_memory_limit: bool = False,
) -> PluginRunResult:
    """在受限子进程里跑插件的 `compute_raw_factors`，永不抛异常（结果里说原因）。

    Args:
        source: 插件源码（单文件）。
        universe / data: 已构造好的 pandas 结构——**不传 session / repo / adapter**
            （SDD §15.2「只能通过系统提供的标准数据接口获取数据」的落地方式）。
        timeout_s: 硬超时；到点 `terminate()` → `kill()` 两段。
        memory_mb: 插件可**额外**申请的内存预算（见文件头实现决定 1）。
        allow_without_memory_limit: 无 `resource` 的平台上显式降级运行。默认 False =
            fail-closed，返回 `sandbox_unavailable`。

    Returns:
        `PluginRunResult`；`exit_status ∈ {ok, rejected_import, socket_blocked, timeout,
        memory, invalid_output, error, sandbox_unavailable, sandbox_bootstrap_failed}`。

    ⚠️ **调用方必须能被子进程 import**：`spawn` 会在子进程里重新导入调用方的 `__main__`，
    从 REPL / `python - <<EOF`（`__main__` 是 `<stdin>`）调用时子进程起不来。这种失败在
    父侧只看到 `exitcode=1`、与「插件自己崩溃」长得一模一样，故单列
    `sandbox_bootstrap_failed`（子进程起来的第一件事就是报到，父进程据此区分）——
    把沙箱的问题报成用户插件的问题是 C-4 禁的那种静默掩盖。
    """
    caps = sandbox_capabilities()
    if not caps.memory_limit and not allow_without_memory_limit:
        return PluginRunResult(
            ok=False, exit_status=_EXIT_UNAVAILABLE, factors=None,
            error=(
                "本平台无 POSIX resource 模块，memory limit 无法施加 → 拒绝执行插件。"
                "要在此平台跑请显式传 allow_without_memory_limit=True（审计会记下）。"
            ),
            duration_ms=0, peak_memory_kb=None, capabilities=caps,
        )

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child_main, args=(source, universe, data, memory_mb, child_conn), daemon=True,
    )
    started = time.monotonic()
    proc.start()
    child_conn.close()  # 父侧必须关掉自己那一端，否则 poll 永远等不到 EOF

    payload = None
    bootstrapped = False
    deadline = started + timeout_s
    while time.monotonic() < deadline:
        # 先收再 join：payload 大于管道缓冲时，子进程会阻塞在 send 上，
        # 先 join 就是死锁（multiprocessing 文档点名的那个坑）
        if parent_conn.poll(0.05):
            try:
                msg = parent_conn.recv()
            except EOFError:
                break
            if isinstance(msg, tuple) and len(msg) == 1 and msg[0] == _HELLO:
                bootstrapped = True
                continue
            payload = msg
            break
        if not proc.is_alive():
            while parent_conn.poll(0):
                try:
                    msg = parent_conn.recv()
                except EOFError:
                    break
                if isinstance(msg, tuple) and len(msg) == 1 and msg[0] == _HELLO:
                    bootstrapped = True
                    continue
                payload = msg
            break

    if payload is None and proc.is_alive():
        proc.terminate()
        proc.join(2.0)
        if proc.is_alive():  # pragma: no cover  (terminate 一般够)
            proc.kill()
            proc.join(2.0)
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.warning("plugin_sandbox_timeout timeout_s=%s duration_ms=%s", timeout_s, duration_ms)
        return PluginRunResult(
            ok=False, exit_status=_EXIT_TIMEOUT, factors=None,
            error=f"插件执行超时（预算 {timeout_s:.1f}s）", duration_ms=duration_ms,
            peak_memory_kb=None, capabilities=caps,
        )

    proc.join(2.0)
    duration_ms = int((time.monotonic() - started) * 1000)
    exitcode = proc.exitcode

    if payload is None:
        if not bootstrapped:
            # 沙箱子进程压根没起来（spawn 需要父进程 `__main__` 可导入——REPL /
            # `python - <<EOF` 这种入口不满足）。**不是插件的问题**，如实分开报。
            logger.error("plugin_sandbox_bootstrap_failed exitcode=%s", exitcode)
            return PluginRunResult(
                ok=False, exit_status=_EXIT_BOOTSTRAP, factors=None,
                error=(
                    f"沙箱子进程未能启动（exitcode={exitcode}）——与插件无关。"
                    "spawn 要求调用方的 __main__ 可被子进程导入：从 REPL / stdin 脚本"
                    "调用会这样失败，请从模块或脚本文件里调。"
                ),
                duration_ms=duration_ms, peak_memory_kb=None, capabilities=caps,
            )
        # 起来了但没回传就死了：被信号杀掉（OOM killer / RLIMIT 触发的 SIGKILL）与
        # 「异常退出」在这里区分不开，故按信号号判断，其余归 error（C-4：不猜）
        status = _EXIT_MEMORY if exitcode in (-9, 137) else _EXIT_ERROR
        return PluginRunResult(
            ok=False, exit_status=status, factors=None,
            error=f"插件子进程异常退出（exitcode={exitcode}）", duration_ms=duration_ms,
            peak_memory_kb=None, capabilities=caps,
        )

    status, factors, error, peak_kb, limited = payload
    if not limited and caps.memory_limit:  # pragma: no cover  (仅 POSIX 上限额加不上时)
        logger.warning("plugin_sandbox_memory_limit_not_applied")
    return PluginRunResult(
        ok=status == _EXIT_OK, exit_status=status, factors=factors,
        error=error, duration_ms=duration_ms, peak_memory_kb=peak_kb,
        capabilities=caps,
    )
