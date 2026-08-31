"""部署版本戳（`backend/VERSION` + `GET /health`）。

背景：生产服务器不是 git 仓库，此前无法从运行中的系统回答「现在跑的是哪个版本」——
`/health` 返回的是 Phase 10 写死的 `"1.0.0"`，从未随部署变过。2026-08-31 部署 C1+P0
时，确认基线只能靠逐文件 md5 比对（还因服务器 .py 是 CRLF 而全部对不上，险些误判为
「服务器不在任何已知 commit 上」）。

这组用例守的是**版本戳不能退化回常量**。按 CLAUDE.md §4.11 的判据选形态：
- 「是否真被消费」→ 写「改输入 → 结果必须变」（VER-02），而不是只断言「返回了个字符串」
- 「声称与现实脱节」→ VER-01 钉死仓库里的 VERSION 恒为 unknown，防止有人把某个 sha
  提交进去，从此每次部署都报同一个永久过期的假版本
"""
from __future__ import annotations

import pathlib

from quantpilot.main import VERSION_FILE, _read_deployed_version

_SENTINEL = "unknown"


# ======================================================================
# VER-01：仓库里的 VERSION 必须恒为 unknown
#         真实 sha 只在服务器上由 deploy_prod.sh 写入，绝不进 git
# ======================================================================
def test_ver_01_repo_version_file_is_sentinel() -> None:
    assert VERSION_FILE.exists(), (
        f"{VERSION_FILE} 缺失。Dockerfile 有 `COPY VERSION .`，缺文件会让镜像构建失败"
    )
    content = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert content == _SENTINEL, (
        f"仓库中的 VERSION 必须是 '{_SENTINEL}'，实际是 '{content}'。\n"
        "真实 commit sha 由 scripts/deploy_prod.sh 在服务器上 build 前写入，"
        "**不提交进 git**——一旦提交，此后每次部署都会报同一个永久过期的版本号，"
        "而它看起来完全合理，比 'unknown' 更容易骗过人。"
    )


# ======================================================================
# VER-02：真的在读文件——改内容，返回值必须跟着变
#         只断言「返回了某个字符串」的测试，对硬编码常量同样绿
# ======================================================================
def test_ver_02_actually_reads_the_file(tmp_path: pathlib.Path) -> None:
    first = tmp_path / "VERSION"
    first.write_text("abc1234 2026-08-31T14:21:00+08:00 main\n", encoding="utf-8")
    assert _read_deployed_version(first) == "abc1234 2026-08-31T14:21:00+08:00 main"

    # 换一个值，结果必须不同——这才证明它消费了输入
    second = tmp_path / "VERSION2"
    second.write_text("deadbee 2026-09-01T09:00:00+08:00 main\n", encoding="utf-8")
    assert _read_deployed_version(second) == "deadbee 2026-09-01T09:00:00+08:00 main"

    assert _read_deployed_version(first) != _read_deployed_version(second)


# ======================================================================
# VER-03/04：降级路径——缺文件 / 空文件都回 unknown，且不抛异常
#            绝不回落到某个像样的假版本号
# ======================================================================
def test_ver_03_missing_file_returns_unknown(tmp_path: pathlib.Path) -> None:
    assert _read_deployed_version(tmp_path / "does_not_exist") == _SENTINEL


def test_ver_04_blank_file_returns_unknown(tmp_path: pathlib.Path) -> None:
    blank = tmp_path / "VERSION"
    blank.write_text("   \n\t\n", encoding="utf-8")
    assert _read_deployed_version(blank) == _SENTINEL


# ======================================================================
# VER-05：/health 端点吐的是版本戳，不是又一个常量
#         用 AST 检查端点函数体——它必须引用 _DEPLOYED_VERSION，
#         而不是内联一个字面量（形态同 EXIT-05：只能在源码上验证）
# ======================================================================
def test_ver_05_health_endpoint_uses_the_stamp() -> None:
    import ast

    src = pathlib.Path("src/quantpilot/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == "health"
        ),
        None,
    )
    assert fn is not None, "main.py 中未找到 health 端点函数"

    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "_DEPLOYED_VERSION" in names, (
        "/health 必须返回版本戳 _DEPLOYED_VERSION。此前它返回硬编码的 '1.0.0'，"
        "从 Phase 10 起从未随部署变过——问它等于没问。"
    )

    literals = {
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert not any(ch.isdigit() for lit in literals for ch in lit), (
        f"/health 函数体内不应出现含数字的字符串字面量（疑似写死版本号）：{literals}"
    )
