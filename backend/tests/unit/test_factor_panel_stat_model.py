"""K-6：`factor_panel_stat` ORM 与权威 DDL 的一致性（V1.5-K §2.1 / system_design §4.2）。

⚠️ 本表是**研究数据落点**：K-2~K-6 全部统计量整批写入、可整批丢弃重来。
运行时表 `factor_ic_window_state` 一个字节都不动（它喂 ICIR 决定实盘权重）。

判据取向：**不测「表能建出来」，测「约束真的成立」**。
CLAUDE.md §4.8 要求 ORM `__table_args__` 与迁移文件保持一致——本文件钉 ORM 这一半，
迁移那一半由 `test_migration_0026_*` 钉。
"""
from __future__ import annotations

from quantpilot.models.business import FactorPanelStat


class TestSchemaMatchesDesign:
    def test_tablename(self) -> None:
        assert FactorPanelStat.__tablename__ == "factor_panel_stat"

    def test_all_dimension_columns_are_not_null(self) -> None:
        """九个维度列 + sample_size 必须 NOT NULL。

        ⚠️ 尤其 `bucket`：**PostgreSQL 的 UNIQUE 视 NULL 互不相等**，
        可空 bucket 会让九列唯一键形同虚设、静默写入重复行。
        与 C0-7 唯一约束撞车（alembic 0025）是同一族缺陷。
        `value` 是唯一允许为空的业务列（指标本身可能算不出）。
        """
        t = FactorPanelStat.__table__
        required = [
            "panel_run", "trade_date", "strategy", "factor",
            "stage", "state", "horizon", "metric", "bucket", "sample_size",
        ]
        nullable = [c for c in required if t.c[c].nullable]
        assert nullable == [], f"这些列不该可空：{nullable}"
        assert t.c["value"].nullable is True, "value 应可空——指标可能算不出"

    def test_bucket_defaults_to_minus_one(self) -> None:
        """`bucket` 默认 -1 表示「不适用」，而不是 NULL。"""
        d = FactorPanelStat.__table__.c["bucket"].server_default
        assert d is not None, "bucket 必须有 server_default，否则旧行插入会 NOT NULL 冲突"
        assert "-1" in str(d.arg), f"bucket 默认应为 -1，实得 {d.arg}"

    def test_unique_constraint_covers_all_nine_dimensions(self) -> None:
        """唯一键必须**恰好**是设计定的九列——多一列或少一列都会改变去重语义。

        少一列 → 合法的不同行被判为冲突、互相覆盖；
        多一列 → 本该冲突的重复行并存。两种都是静默的。
        """
        from sqlalchemy import UniqueConstraint

        ucs = [
            c for c in FactorPanelStat.__table__.constraints
            if isinstance(c, UniqueConstraint)
        ]
        assert len(ucs) == 1, f"应恰有 1 个唯一约束，实得 {len(ucs)}"
        uc = ucs[0]
        assert uc.name == "uq_factor_panel_stat"
        assert [c.name for c in uc.columns] == [
            "panel_run", "trade_date", "strategy", "factor",
            "stage", "state", "horizon", "metric", "bucket",
        ]

    def test_value_precision_matches_design(self) -> None:
        """NUMERIC(12,6)：IC 在 ±1 内，但十分位前向收益/成本可能更大，故 12 位。"""
        v = FactorPanelStat.__table__.c["value"].type
        assert (v.precision, v.scale) == (12, 6)

    def test_string_lengths_match_design(self) -> None:
        expected = {
            "panel_run": 64, "strategy": 32, "factor": 64,
            "stage": 16, "state": 16, "metric": 32,
        }
        got = {k: FactorPanelStat.__table__.c[k].type.length for k in expected}
        assert got == expected


class TestRuntimeTableUntouched:
    def test_factor_ic_window_state_unique_constraint_unchanged(self) -> None:
        """K-6 走新表的全部理由：运行时表**一个字节都不改**。

        它有三个读取方（`get_ic_daily_window` 喂 ICIR / `get_monthly_quality_ic_series`
        / `get_existing_daily_ic_dates` 断点续传）。其中断点续传**不按 factor 过滤**，
        往现表塞因子行一旦 row_type 用错，就会让续传误判「该日已回填」而静默跳过。
        本用例是那个决策的回归守卫——现表约束若被动过，这里立刻红。
        """
        from sqlalchemy import UniqueConstraint

        from quantpilot.models.business import FactorICWindowState

        ucs = [
            c for c in FactorICWindowState.__table__.constraints
            if isinstance(c, UniqueConstraint)
        ]
        assert len(ucs) == 1
        assert ucs[0].name == "uq_factor_ic_window_state_skftr"
        assert [c.name for c in ucs[0].columns] == [
            "strategy", "factor", "state", "trade_date", "row_type",
        ]
