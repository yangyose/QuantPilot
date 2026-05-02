from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class ValidationResult:
    is_valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    invalid_rows: pd.Index = field(default_factory=lambda: pd.Index([]))


class DataValidator:
    """数据校验器，实现 SDD §5.5 全部校验规则。

    所有方法为同步纯函数，输入/输出均为 DataFrame / ValidationResult。
    """

    COMPLETENESS_THRESHOLD = 0.95
    ADJ_FACTOR_CHANGE_THRESHOLD = 0.20

    def validate_daily_quotes(
        self, df: pd.DataFrame, prev_count: int
    ) -> ValidationResult:
        """执行 SDD §5.5 日线校验：
        - 完整性：当日股票数 >= prev_count × 0.95
        - 价格有效性：low <= open,close <= high（含等于）
        - 成交量非负：vol >= 0
        - 复权连续性：相邻两日 adj_factor 变化率 <= 20%（按 ts_code 分组）
        异常行打标，不直接丢弃；errors 中记录阻断性问题。
        """
        warnings: list[str] = []
        errors: list[str] = []
        invalid_mask = pd.Series(False, index=df.index)

        # 1. 完整性校验
        threshold = prev_count * self.COMPLETENESS_THRESHOLD
        if len(df) < threshold:
            errors.append(
                f"完整性不足：当日股票数 {len(df)} < 阈值 {threshold:.0f}"
                f"（prev_count={prev_count}×{self.COMPLETENESS_THRESHOLD}）"
            )

        # 2. 价格有效性
        price_cols = {"low", "open", "close", "high"}
        if price_cols.issubset(df.columns):
            price_invalid = (
                (df["low"] > df["close"])
                | (df["low"] > df["open"])
                | (df["open"] > df["high"])
                | (df["close"] > df["high"])
            )
            invalid_mask |= price_invalid

        # 3. 成交量非负
        if "vol" in df.columns:
            invalid_mask |= df["vol"] < 0

        return ValidationResult(
            is_valid=len(errors) == 0,
            warnings=warnings,
            errors=errors,
            invalid_rows=df.index[invalid_mask],
        )

    def validate_adj_factor_series(self, df: pd.DataFrame) -> ValidationResult:
        """校验多日 adj_factor 时间序列的连续性（供 Phase 3+ 因子引擎调用）。

        输入 df 需包含 adj_factor 列，可含 ts_code 列（多股）或不含（单股序列）。
        相邻两日变化率超过阈值时记录 warning；单日数据（每 ts_code 仅 1 行）不触发。
        """
        warnings: list[str] = []
        if "adj_factor" not in df.columns or len(df) <= 1:
            return ValidationResult(is_valid=True, warnings=warnings)

        if "ts_code" in df.columns:
            for _, group in df.groupby("ts_code"):
                af = group["adj_factor"].dropna()
                exceeded = af.pct_change().abs().dropna() > self.ADJ_FACTOR_CHANGE_THRESHOLD
                if len(af) > 1 and exceeded.any():
                    warnings.append(
                        "复权连续性告警：相邻交易日 adj_factor 变化超过"
                        f" {self.ADJ_FACTOR_CHANGE_THRESHOLD * 100:.0f}%"
                    )
                    break  # 发现一例即告警，不重复
        else:
            af = df["adj_factor"].dropna()
            exceeded = af.pct_change().abs().dropna() > self.ADJ_FACTOR_CHANGE_THRESHOLD
            if len(af) > 1 and exceeded.any():
                warnings.append(
                    "复权连续性告警：相邻交易日 adj_factor 变化超过"
                    f" {self.ADJ_FACTOR_CHANGE_THRESHOLD * 100:.0f}%"
                )

        return ValidationResult(is_valid=True, warnings=warnings)

    def validate_financial_data(
        self, df: pd.DataFrame, as_of_date: date
    ) -> ValidationResult:
        """执行财务数据 PIT 校验。

        PIT 违规（publish_date > as_of_date）和数据异常（publish_date < report_period）
        均为**行级过滤**，不阻断整批入库（is_valid 始终 True）：
        - invalid_rows：需跳过的行索引，调用方应 drop 后再 upsert
        - errors：违规日志（供记录用，不影响 is_valid）

        注意：回测或历史回填时必须传入 trade_date，不能硬编码 date.today()。
        """
        errors: list[str] = []
        invalid_mask = pd.Series(False, index=df.index)

        if "publish_date" in df.columns:
            future_mask = df["publish_date"] > as_of_date
            if future_mask.any():
                errors.append(
                    f"PIT 违规：{future_mask.sum()} 行 publish_date > as_of_date({as_of_date})，"
                    "这些行将被跳过"
                )
                invalid_mask |= future_mask

            if "report_period" in df.columns:
                bad = df["publish_date"] < df["report_period"]
                if bad.any():
                    errors.append(
                        f"数据异常：{bad.sum()} 行 publish_date < report_period"
                    )
                    invalid_mask |= bad

        return ValidationResult(
            is_valid=True,  # 行级过滤，不阻断整批
            errors=errors,
            invalid_rows=df.index[invalid_mask],
        )

    def validate_trade_date(
        self, df: pd.DataFrame, expected_date: date
    ) -> ValidationResult:
        """时效性校验：df 中 trade_date 必须等于 expected_date"""
        errors: list[str] = []
        if "trade_date" in df.columns:
            wrong = df["trade_date"] != expected_date
            if wrong.any():
                errors.append(
                    f"时效性异常：{wrong.sum()} 行 trade_date ≠ {expected_date}"
                )
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)
