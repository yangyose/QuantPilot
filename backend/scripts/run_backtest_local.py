"""本地算力中心：长区间回测 CLI（2026-06-15）。

生产 2GB 机对长区间回测设了护栏（BACKTEST_MAX_WINDOW_DAYS，超限 422 拒绝），因为
daily_quotes 全量 pivot 会 OOM 拖垮整机。长区间回测改在本地大内存机用本脚本跑，跑完
经 `POST /backtest/import` 把结果回流生产 DB，使生产 Web 也能查看。

数据来源 = 本地「算力库」（独立卷 + 端口，从最新远端备份恢复，见
scripts/sync_local_backtest_db.sh）。回测复用与生产**完全一致**的构造路径
（`build_engine_from_snapshot` + `ConfigService.get_all_for_snapshot`），只是不受护栏限制。

每条结果盖「数据基线」戳（config_snapshot.data_baseline = 本地库 daily_quote 的
max trade_date），回流后生产 Web 可标注「本结果基于截至 X 日的数据」。

用法（在 backend/ 目录）：
  # 本地跑 3.5 年回测（连本地算力库 5434），仅本地查看：
  DATABASE_URL=postgresql+asyncpg://quantpilot:PWD@localhost:5434/quantpilot \\
      uv run python scripts/run_backtest_local.py --start 2023-01-01 --end 2026-06-12

  # 跑完回流生产（需服务器登录凭据）：
  QP_SERVER_URL=https://quant.portableagi.com QP_SERVER_USER=admin \\
  QP_SERVER_PASSWORD='***' \\
      uv run python scripts/run_backtest_local.py --start 2023-01-01 --end 2026-06-12 --push

环境变量：
  DATABASE_URL       本地算力库（必须指向本地，勿用生产/测试库）
  QP_SERVER_URL      回流目标（默认 https://quant.portableagi.com）
  QP_SERVER_USER     服务器用户名（默认 admin）
  QP_SERVER_PASSWORD 服务器密码（--push 时必填；不落盘，从 env/交互读）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Windows 控制台默认 ANSI 代码页（cp936/cp932）无法输出中文 → 重配 UTF-8。
# 必须在 basicConfig 之前（StreamHandler 在 basicConfig 时捕获 sys.stderr）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_backtest_local")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="本地长区间回测（结果可回流生产）")
    p.add_argument("--start", required=True, help="回测开始日 YYYY-MM-DD")
    p.add_argument("--end", required=True, help="回测结束日 YYYY-MM-DD")
    p.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金（默认 100 万）")
    p.add_argument("--commission", type=float, default=None, help="佣金率（缺省走配置默认）")
    p.add_argument("--stamp", type=float, default=None, help="印花税率（缺省走配置默认）")
    p.add_argument("--slippage", type=float, default=None, help="滑点率（缺省走配置默认）")
    p.add_argument("--push", action="store_true", help="跑完把结果回流生产 DB")
    return p.parse_args()


def _guard_local_db() -> None:
    """红线：拒绝把本地回测指向生产/测试库（生产 5432 写、测试 5433 会被回归清库）。"""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit("❌ 未设置 DATABASE_URL（应指向本地算力库，如 localhost:5434）")
    if ":5433/" in url:
        sys.exit("❌ DATABASE_URL 指向 5433 测试库（集成测试会 DROP 所有表）；请用本地算力库")


def _warn_if_stale() -> None:
    """提醒：本地算力库是否落后于最新拉回的生产备份。

    SessionStart 钩子每日把最新生产备份拉到 backups/remote/；sync_local_backtest_db.sh
    恢复后写 marker `.last_restore`（已恢复的备份名）。若 backups/remote/ 里有比 marker
    更新的备份，说明本地算力库数据已过期 → 警告（不阻断，用户可有意跑旧基线）。
    """
    repo_root = Path(__file__).resolve().parents[2]
    remote_dir = repo_root / "backups" / "remote"
    if not remote_dir.is_dir():
        return
    backups = sorted(remote_dir.glob("qp_*.sql.gz"), key=lambda p: p.name)
    if not backups:
        logger.warning("⚠️ 未发现拉回的生产备份（backups/remote/）；本地库可能很旧。")
        return
    latest = backups[-1].name
    marker = remote_dir / ".last_restore"
    restored = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if restored != latest:
        logger.warning(
            "⚠️ 本地算力库数据可能过期：已恢复=%s，最新备份=%s。"
            "建议先同步最新生产数据：bash scripts/sync_local_backtest_db.sh",
            restored or "(无记录)", latest,
        )
    else:
        logger.info("本地算力库已是最新备份 %s（与生产同步）。", latest)


async def _run(args: argparse.Namespace) -> None:
    from sqlalchemy import func, select

    from quantpilot.core.database import AsyncSessionLocal
    from quantpilot.data.calendar import TradingCalendar
    from quantpilot.data.repository import MarketDataRepository
    from quantpilot.engine.backtest.engine import BacktestConfig
    from quantpilot.models.market import DailyQuote
    from quantpilot.services.backtest_service import BacktestService, build_engine_from_snapshot
    from quantpilot.services.config_service import ConfigService

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        sys.exit("❌ end_date 早于 start_date")

    # ① 读基线日 + 交易日历 + 配置快照（一个只读 session）
    async with AsyncSessionLocal() as session:
        baseline = (
            await session.execute(select(func.max(DailyQuote.trade_date)))
        ).scalar_one_or_none()
        repo = MarketDataRepository(session)
        # 日历覆盖 [start-200d, end+5d]：前置窗口够各策略 lookback（130d）+ 余量
        calendar = await TradingCalendar.from_repo(
            repo, start - timedelta(days=200), end + timedelta(days=5),
        )
        cfg_svc = ConfigService(session, None)
        defaults = await cfg_svc.get_backtest_defaults()
        snapshot = dict(await cfg_svc.get_all_for_snapshot())

    trade_dates = calendar.get_trade_dates(start, end)
    if not trade_dates:
        sys.exit(
            f"❌ 本地库在 {start}~{end} 无交易日数据；"
            "先 sync_local_backtest_db.sh 恢复最新备份"
        )
    logger.info(
        "本地回测：%s~%s（%d 交易日），数据基线=%s，初始资金=%.0f",
        start, end, len(trade_dates), baseline, args.capital,
    )

    commission = args.commission if args.commission is not None else defaults.commission_rate
    stamp = args.stamp if args.stamp is not None else defaults.stamp_tax_rate
    slippage = args.slippage if args.slippage is not None else defaults.slippage_rate
    config = BacktestConfig(
        start_date=start, end_date=end, initial_capital=args.capital,
        strategy_config={}, account_config={},
        commission_rate=commission, stamp_tax_rate=stamp, slippage_rate=slippage,
    )
    # 数据基线戳：回流后生产 Web 标注「本结果基于截至 X 日的数据」
    snapshot["data_baseline"] = baseline.isoformat() if baseline else None
    engine = build_engine_from_snapshot(snapshot, calendar)

    # ② 建任务（PENDING）
    async with AsyncSessionLocal() as session:
        task_id = await BacktestService(session, engine).create_task(
            config, engine_snapshot=snapshot,
        )
    logger.info("task_id=%s 已建，开始计算……", task_id)

    # ③ 跑回测（节流打印进度）
    _last = {"pct": -10}

    def progress_cb(trade_date_str: str, pct: int, nav: float) -> None:
        if pct >= _last["pct"] + 10 or pct >= 100:
            _last["pct"] = pct
            logger.info("  进度 %3d%%  %s  NAV=%.4f", pct, trade_date_str, nav)

    async with AsyncSessionLocal() as session:
        await BacktestService(session, engine).run_task(task_id, config, progress_cb)

    # ④ 读结果
    async with AsyncSessionLocal() as session:
        svc = BacktestService(session, engine=None)
        task = await svc.get_task(task_id)
        result = await svc.get_result(task_id)

    if task is None or task.status != "SUCCESS" or result is None:
        _st = getattr(task, "status", None)
        _err = getattr(task, "error_msg", None)
        sys.exit(f"❌ 回测失败：status={_st} err={_err}")

    perf = result.performance_json
    logger.info("✅ 回测完成 task_id=%s", task_id)
    for k in ("total_return", "annual_return", "sharpe", "max_drawdown", "win_rate"):
        if k in perf:
            logger.info("    %-14s = %s", k, perf[k])

    if args.push:
        await _push_to_server(task, result)
    else:
        logger.info("（未 --push；结果仅在本地库。加 --push 回流生产 Web）")


async def _push_to_server(task, result) -> None:
    """登录服务器 → POST /backtest/import 回流 task+result 两行（幂等）。"""
    import httpx

    base = os.environ.get("QP_SERVER_URL", "https://quant.portableagi.com").rstrip("/")
    user = os.environ.get("QP_SERVER_USER", "admin")
    pwd = os.environ.get("QP_SERVER_PASSWORD")
    if not pwd:
        sys.exit("❌ --push 需设 QP_SERVER_PASSWORD 环境变量（不落盘）")

    payload = {
        "task_id": task.task_id,
        "config_json": task.config_json,
        "config_snapshot": task.config_snapshot,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "performance": result.performance_json,
        "daily_nav": result.daily_nav_json,
        "disclaimer": result.disclaimer,
    }
    async with httpx.AsyncClient(base_url=base, timeout=60) as cli:
        r = await cli.post("/api/v1/auth/login", json={"username": user, "password": pwd})
        if r.status_code != 200 or r.json().get("code") != 0:
            sys.exit(f"❌ 登录失败 status={r.status_code} body={r.text[:200]}")
        token = r.json()["data"]["access_token"]
        r = await cli.post(
            "/api/v1/backtest/import", json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            sys.exit(f"❌ 回流失败 status={r.status_code} body={r.text[:300]}")
        data = r.json()["data"]
        if data.get("imported"):
            logger.info("✅ 已回流生产 DB：task_id=%s（生产 Web 可查看）", task.task_id)
        else:
            logger.info("ℹ️  生产已存在该 task_id，跳过（幂等）：%s", task.task_id)


def main() -> None:
    args = _parse_args()
    _guard_local_db()
    _warn_if_stale()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
