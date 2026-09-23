"""C5 §7.4 插件存储与审计两表的**结构契约**（2026-09-23）。

## 为什么先钉结构而不是等集成测试

C3 那次的教训（设计 §5.3 / v0.17 修订行）：`low_volatility_score` 登记、映射、迁移、契约
测试全对，唯独 `Scorer.aggregate` 没把值填进去 → 两张表新列恒 NULL，而 1184 条测试全绿。
所以「表建了」与「表的约束真是设计要的那套」是两件事——本文件在 ORM 元数据上逐条核对
设计 §7.4 声明的东西：UNIQUE(user_id, name, version)、`(plugin_id, created_at DESC)` 索引、
`action` 取值域、FK 与级联、`error_excerpt` 可空。

集成层（真建表 + alembic 升降级 + 真插数据）归 `tests/integration/test_int_plugin_tables.py`，
本文件只看元数据，秒级、无 DB。
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Date, Integer, String, Text

from quantpilot.models.plugin import (
    PLUGIN_ACTIONS,
    PLUGIN_STATUSES,
    StrategyPlugin,
    StrategyPluginAudit,
)


def _cols(model) -> dict:
    return {c.name: c for c in model.__table__.columns}


class TestStrategyPluginTable:
    def test_table_and_columns(self) -> None:
        assert StrategyPlugin.__tablename__ == "strategy_plugin"
        cols = _cols(StrategyPlugin)
        assert set(cols) == {
            "id", "user_id", "name", "version", "source_code", "status",
            "created_at", "updated_at",
        }
        assert isinstance(cols["id"].type, BigInteger)
        assert isinstance(cols["source_code"].type, Text), "源码必须是 TEXT（§7.4）"
        assert isinstance(cols["name"].type, String)

    def test_unique_user_name_version(self) -> None:
        """§7.4：`UNIQUE(user_id, name, version)` —— 同一用户同名同版本只能有一份。"""
        uniques = [
            tuple(c.name for c in con.columns)
            for con in StrategyPlugin.__table__.constraints
            if con.__class__.__name__ == "UniqueConstraint"
        ]
        assert ("user_id", "name", "version") in uniques, uniques

    def test_user_fk_cascades(self) -> None:
        """用户注销 → 其插件随之删除（不留孤儿行）。"""
        fks = list(StrategyPlugin.__table__.foreign_keys)
        assert len(fks) == 1
        fk = fks[0]
        assert fk.column.table.name == "user"
        assert fk.ondelete == "CASCADE"

    def test_status_domain_is_constrained_in_db(self) -> None:
        """状态值域必须由 DB CHECK 约束兜住——只在应用层校验挡不住脚本/手工 SQL 写脏。"""
        checks = [
            con for con in StrategyPlugin.__table__.constraints
            if con.__class__.__name__ == "CheckConstraint"
        ]
        text_all = " ".join(str(c.sqltext) for c in checks)
        assert PLUGIN_STATUSES, "状态枚举不能为空"
        for status in PLUGIN_STATUSES:
            assert f"'{status}'" in text_all, f"{status} 不在 CHECK 约束里：{text_all}"

    def test_soft_delete_is_a_status_not_a_row_delete(self) -> None:
        """§7.5 DELETE 端点是**软删**（审计要留痕）→ 状态枚举里必须有 deleted。"""
        assert "deleted" in PLUGIN_STATUSES


class TestStrategyPluginAuditTable:
    def test_table_and_columns(self) -> None:
        assert StrategyPluginAudit.__tablename__ == "strategy_plugin_audit"
        cols = _cols(StrategyPluginAudit)
        assert set(cols) == {
            "id", "plugin_id", "user_id", "action", "trade_date",
            "duration_ms", "peak_memory_kb", "exit_status", "error_excerpt", "created_at",
        }
        assert isinstance(cols["duration_ms"].type, Integer)
        assert isinstance(cols["peak_memory_kb"].type, BigInteger)
        assert isinstance(cols["trade_date"].type, Date)
        assert isinstance(cols["error_excerpt"].type, Text)

    def test_runtime_metrics_are_nullable(self) -> None:
        """upload/delete 这类动作没有耗时/内存/退出状态——这些列必须可空（C-4：不填假值）。"""
        cols = _cols(StrategyPluginAudit)
        for name in ("trade_date", "duration_ms", "peak_memory_kb", "exit_status",
                     "error_excerpt"):
            assert cols[name].nullable, f"{name} 应可空"
        for name in ("plugin_id", "user_id", "action"):
            assert not cols[name].nullable, f"{name} 不应可空"

    def test_action_domain(self) -> None:
        """§7.4：`action ∈ {upload, update, delete, load, execute}`，且由 DB CHECK 兜住。"""
        assert PLUGIN_ACTIONS == ("upload", "update", "delete", "load", "execute")
        checks = " ".join(
            str(c.sqltext) for c in StrategyPluginAudit.__table__.constraints
            if c.__class__.__name__ == "CheckConstraint"
        )
        for action in PLUGIN_ACTIONS:
            assert f"'{action}'" in checks, f"{action} 不在 CHECK 约束里"

    def test_index_on_plugin_and_created_at_desc(self) -> None:
        """§7.4：`INDEX(plugin_id, created_at DESC)` —— 详情页要取「最近审计」。

        降序必须写进索引定义（`sa.text("created_at DESC")`，CLAUDE.md §4.8）；
        只建 `(plugin_id, created_at)` 会让「最近 N 条」走反向扫描。
        """
        idx = {i.name: i for i in StrategyPluginAudit.__table__.indexes}
        target = next((i for i in idx.values() if "plugin" in i.name and "created" in i.name), None)
        assert target is not None, f"缺 (plugin_id, created_at DESC) 索引，现有 {list(idx)}"
        expr = " ".join(str(e) for e in target.expressions).lower()
        assert "plugin_id" in expr
        assert "desc" in expr, f"created_at 必须降序，实得 {expr}"

    def test_audit_survives_plugin_deletion(self) -> None:
        """插件行被硬删时审计**不能**跟着消失——审计的意义就是留痕。

        故 `plugin_id` 的 FK 用 `ON DELETE SET NULL`？不行，它 NOT NULL。
        取 `RESTRICT`：想删插件行必须先处理审计，实际路径是**软删**（status=deleted），
        审计永远保留（§7.5 DELETE 端点是软删）。
        """
        fk = next(fk for fk in StrategyPluginAudit.__table__.foreign_keys
                  if fk.column.table.name == "strategy_plugin")
        assert fk.ondelete == "RESTRICT", (
            "插件行硬删会带走审计 → 审计失去意义；软删才是设计路径"
        )
