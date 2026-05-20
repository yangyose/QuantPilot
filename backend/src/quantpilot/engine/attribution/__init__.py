"""Phase 12 多因子归因引擎（Engine 层，严格无 IO）。"""
from quantpilot.engine.attribution.regression import AttributionResult, run_ols

__all__ = ["AttributionResult", "run_ols"]
