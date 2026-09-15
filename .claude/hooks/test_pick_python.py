"""pick_python.sh 夹具 —— 钩子解释器选择的正反两面。

为什么值得单独钉（2026-09-14）：
四个钩子都靠 `python -c "import sys"` 探测解释器。会话重启后 PATH 上的 python / py /
python3（Windows Store「Python Install Manager」别名桩）**启动即挂死**——不是报错，是不
返回：探测本身挂 1948s，`timeout 15` 杀不掉。于是每个 Bash/Edit/Write 调用都在 PreToolUse
（guard）挂到 600s 超时、再在 PostToolUse（auto_test）挂 600s：**每次工具调用延迟 20 分钟，
守卫因超时 fail-open 整段失效**。而四个既有夹具全部**在这之前**跑过并全绿——它们不覆盖
「PATH 上的 python 挂死」这个现实分支（§4.11「测试输入比现实更配合」）。

本夹具用一个**假的、会挂死的 `python`** 放在 PATH 最前面来重现那个现实：
  - 正面：venv 存在时必须在几秒内选中 venv、**根本不碰** PATH 上的那个
  - 反面：venv 不存在时仍回落到 PATH（fail-open 语义不变，别把兜底删了）
  - QP_PYBIN 显式指定优先于一切

判据：`backend/.venv/Scripts/python.exe .claude/hooks/test_pick_python.py` → 全过。
⚠️ 用 venv 的 python 跑本夹具，别用裸 `python`——裸的那个正是会挂死的桩。
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HOOKS = pathlib.Path(__file__).resolve().parent
PICKER = HOOKS / "pick_python.sh"
ROOT = HOOKS.parents[1]
VENV_PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = ROOT / "backend" / ".venv" / "bin" / "python"

# 与其余夹具一致：不能用裸 `bash`（Windows 上可能解析到 WSL 的 System32\bash.exe）。
BASH = None
for cand in (
    "C:/Program Files/Git/bin/bash.exe",
    "C:/Program Files (x86)/Git/bin/bash.exe",
    (os.environ.get("PROGRAMFILES", "") or "") + "/Git/bin/bash.exe",
    "/bin/bash",
    "/usr/bin/bash",
):
    if os.path.isfile(cand):
        BASH = cand
        break

# 假 python 的挂死时长必须**远大于**下面的 subprocess 超时：探到它就等于失败。
HANG_SECONDS = 25
CALL_TIMEOUT = 12


def _fake_python(dirpath: pathlib.Path, body: str) -> None:
    """往 dirpath 放一个名为 python 的可执行脚本（Git Bash 按 PATH 找得到）。"""
    for name in ("python", "py", "python3"):
        p = dirpath / name
        p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
        p.chmod(0o755)


def _source(picker: pathlib.Path, extra_path: pathlib.Path, env_extra: dict | None = None):
    env = dict(os.environ)
    env["PATH"] = extra_path.as_posix() + os.pathsep + env.get("PATH", "")
    env.pop("QP_PYBIN", None)
    if env_extra:
        env.update(env_extra)
    t0 = time.monotonic()
    try:
        p = subprocess.run(
            [BASH, "-c", '. "$1"; printf "%s" "$PYBIN"', "_", picker.as_posix()],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=CALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - t0
    return p.stdout.decode("utf-8", errors="replace"), time.monotonic() - t0


def main() -> int:
    if BASH is None:
        print("FATAL: 找不到 Git Bash")
        return 1
    if not PICKER.exists():
        print("FATAL: 缺 " + str(PICKER))
        return 1
    if not VENV_PY.exists():
        print("FATAL: 缺项目 venv（先 uv sync）: " + str(VENV_PY))
        return 1
    print("bash = " + BASH)

    passed = failed = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print("PASS  " + name.ljust(34) + detail)
        else:
            failed += 1
            print("FAIL  " + name.ljust(34) + detail)

    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)

        # ---- 1. PATH 上的 python 挂死、venv 存在 → 必须秒选 venv，绝不去探 PATH ----
        hang = tdp / "hang"
        hang.mkdir()
        _fake_python(hang, "sleep " + str(HANG_SECONDS))
        out, dt = _source(PICKER, hang)
        # picker 用 bash 的 pwd 拼路径，Git Bash 下是 /d/... 形式；归一到 d:/... 再比。
        chosen = re.sub(r"^/([a-z])/", r"\1:/", (out or "").replace("\\", "/").lower())
        ok = out is not None and chosen == VENV_PY.as_posix().lower() and dt < CALL_TIMEOUT
        report("venv_first_even_if_path_hangs", ok,
               "got=" + ("<timeout>" if out is None else out) + " in " + f"{dt:.1f}s")

        # ---- 2. QP_PYBIN 显式指定优先于 venv ----
        out, dt = _source(PICKER, hang, {"QP_PYBIN": str(VENV_PY)})
        report("qp_pybin_override_honored", (out or "") == str(VENV_PY),
               "got=" + ("<timeout>" if out is None else out))

        # ---- 3. venv 不存在 → 仍回落到 PATH（兜底不能丢）----
        # 把 picker 复制到一个没有 ../../backend/.venv 的目录树里。
        fake_root = tdp / "repo" / ".claude" / "hooks"
        fake_root.mkdir(parents=True)
        shutil.copy(PICKER, fake_root / "pick_python.sh")
        fast = tdp / "fast"
        fast.mkdir()
        _fake_python(fast, "exit 0")
        out, dt = _source(fake_root / "pick_python.sh", fast)
        report("fallback_to_path_when_no_venv", out == "python",
               "got=" + ("<timeout>" if out is None else repr(out)))

        # ---- 4. 全部落空 → PYBIN 为空（调用方 exit 0 放行，fail-open 语义不变）----
        dead = tdp / "dead"
        dead.mkdir()
        _fake_python(dead, "exit 1")
        out, dt = _source(fake_root / "pick_python.sh", dead)
        report("empty_when_nothing_works", out == "",
               "got=" + ("<timeout>" if out is None else repr(out)))

    print(f"\n{passed}/{passed + failed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
