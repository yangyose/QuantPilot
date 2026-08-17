"""financial_data 两个覆盖索引：消除评分路径两条全表扫描的排序与回堆

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-17

起因（2026-08-17 生产实测）：`MarketDataRepository.get_latest_n_financials` 是每日
管线评分与任何 `score_universe_for_date` 调用的必经查询，生产实测**单次 9 分钟**。
`financial_data` 有 658 万行（5824 股 × 22 报告期 × 每交易日一行快照——日频行携带
当日 pe/pb，是 ValueStrategy 历史分位的数据源，**不可删**，见 repository.py 该方法
docstring）。原计划走 `uq_financial_code_period_publish`(ts_code, report_period,
publish_date) 做 Index Scan + Incremental Sort，需把 621 万行全部读堆并排序。

本索引把列序改为 (ts_code, report_period, publish_date DESC) 并 INCLUDE 该方法读取
的全部 5 个业务列，使之：
- 顺序恰为窗口 `PARTITION BY (ts_code, report_period) ORDER BY publish_date DESC`
  所需 → 排序节点完全消失
- 变成 Index Only Scan（Heap Fetches=0）→ 不再回堆

本地算力中心（5434，生产 2026-08-17 备份全量副本）A/B 实测，同一条含 5840 只
ts_code IN 列表的真实查询：

    无本索引：31,340 ms，buffers 6,266,426（含 1,471,275 次磁盘读）
    有本索引： 2,323 ms，buffers   414,740（磁盘读 0）

生产为磁盘瓶颈（2GB 机、shared_buffers 远小于本地），磁盘读归零的收益预期高于本地
的 13.5 倍。索引体积实测 477 MB。

第二个索引针对同一评分路径的 `get_latest_financial` 日频段（`DISTINCT ON (ts_code)
... ORDER BY ts_code, publish_date DESC`）。既有 `idx_financial_code_publish` 是
(ts_code, publish_date) **升序**，与所需的 publish_date DESC 方向相反 → 规划器只能
Index Scan 后外部排序。同上实测：

    无本索引：69,673 ms，buffers 6,249,210（含 1,440,330 次磁盘读），
              Sort Method: external merge **落盘 260,992 kB**
    有本索引：25,878 ms，buffers   378,743（磁盘读 45,805），**排序节点消失**

2GB 生产机上那 261 MB 外部排序落盘是评分路径的主要代价来源，本索引直接消除它。
索引体积实测 358 MB。剩余耗时为 DISTINCT ON 仍需扫全部匹配索引项（Postgres 15 无
skip scan），彻底消除需把查询改写为 loose index scan —— 属代码改动，本迁移不涉及。

两者均为纯新增索引，不改表结构、不动数据；downgrade 直接 drop。用 CONCURRENTLY 建
避免持 SHARE 锁阻塞 17:30 管线的写入，故需 autocommit_block（CONCURRENTLY 不能在事
务内）。生产建索引耗时预期数分钟级（本地 477MB 约 28s 非并发 / 358MB 约 3.5min 并发）。
"""
from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_PERIOD_INDEX = "idx_financial_code_period_publish_covering"
_DAILY_INDEX = "idx_financial_code_publish_desc_covering"


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY 不能运行在事务块内 → 显式 autocommit。
    with op.get_context().autocommit_block():
        # get_latest_n_financials：两级去重的第一级窗口
        op.create_index(
            _PERIOD_INDEX,
            "financial_data",
            ["ts_code", "report_period", "publish_date"],
            unique=False,
            postgresql_concurrently=True,
            postgresql_ops={"publish_date": "DESC"},
            postgresql_include=[
                "roe",
                "net_profit_yoy",
                "revenue_yoy",
                "debt_to_asset",
                "total_equity",
            ],
        )
        # get_latest_financial 日频段：DISTINCT ON (ts_code) ORDER BY publish_date DESC
        op.create_index(
            _DAILY_INDEX,
            "financial_data",
            ["ts_code", "publish_date"],
            unique=False,
            postgresql_concurrently=True,
            postgresql_ops={"publish_date": "DESC"},
            postgresql_include=["pe_ttm", "pb", "dividend_yield"],
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _DAILY_INDEX,
            table_name="financial_data",
            postgresql_concurrently=True,
        )
        op.drop_index(
            _PERIOD_INDEX,
            table_name="financial_data",
            postgresql_concurrently=True,
        )
