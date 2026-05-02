"""CandidatePoolManager 单元测试 POOL-01~05（Phase 4 T-12）。"""
from __future__ import annotations

import pytest

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.pool import CandidatePoolManager, PoolEntry
from quantpilot.engine.scorer import CompositeScore


def _make_composite(ts_code: str, score: float) -> CompositeScore:
    """辅助函数：构造最简 CompositeScore。"""
    return CompositeScore(
        ts_code=ts_code,
        composite_score=score,
        trend_score=score,
        momentum_score=score,
        reversion_score=score,
        value_score=score,
        market_state=MarketStateEnum.UPTREND,
        score_breakdown={},
        explanation="",
    )


@pytest.fixture
def manager() -> CandidatePoolManager:
    return CandidatePoolManager(pool_capacity=3)


@pytest.fixture
def ten_stocks() -> list[CompositeScore]:
    """10 只股票，分数从 100 到 10（步长 -10）。"""
    return [_make_composite(f"S{i:02d}", (10 - i) * 10.0) for i in range(10)]
    # S00=100, S01=90, ..., S09=10


# ---------------------------------------------------------------------------
# POOL-01：composite_score 最高的 N 只入池（N = pool_capacity）
# ---------------------------------------------------------------------------
class TestPOOL01:
    def test_top_n_in_pool(self, manager: CandidatePoolManager, ten_stocks):
        result = manager.compute_pool(ten_stocks, frozenset(), frozenset())
        in_pool = [e for e in result if e.in_pool]
        assert len(in_pool) == 3
        pool_codes = {e.ts_code for e in in_pool}
        assert pool_codes == {"S00", "S01", "S02"}

    def test_in_pool_false_for_rest(self, manager: CandidatePoolManager, ten_stocks):
        result = manager.compute_pool(ten_stocks, frozenset(), frozenset())
        # 只有入池的 3 只被返回（设计：compute_pool 仅返回 in_pool=True 的条目）
        # 或者也可能返回全部，只是 in_pool 字段不同
        pool_codes = {e.ts_code for e in result if e.in_pool}
        assert pool_codes == {"S00", "S01", "S02"}
        assert "S03" not in pool_codes  # S03(70) 不入池

    def test_scores_preserved(self, manager: CandidatePoolManager, ten_stocks):
        """入池条目的 composite_score 与输入一致。"""
        result = manager.compute_pool(ten_stocks, frozenset(), frozenset())
        by_code = {e.ts_code: e for e in result}
        assert abs(by_code["S00"].composite_score - 100.0) < 0.01
        assert abs(by_code["S01"].composite_score - 90.0) < 0.01


# ---------------------------------------------------------------------------
# POOL-02：持仓保护（holding_codes 强制入池，is_holding=True）
# ---------------------------------------------------------------------------
class TestPOOL02:
    def test_holding_forced_in_pool(self, manager: CandidatePoolManager, ten_stocks):
        # S09 分数最低（10），但因持仓保护强制入池
        result = manager.compute_pool(ten_stocks, frozenset({"S09"}), frozenset())
        by_code = {e.ts_code: e for e in result}
        assert "S09" in by_code
        assert by_code["S09"].in_pool is True

    def test_holding_is_holding_true(self, manager: CandidatePoolManager, ten_stocks):
        result = manager.compute_pool(ten_stocks, frozenset({"S09"}), frozenset())
        by_code = {e.ts_code: e for e in result}
        assert by_code["S09"].is_holding is True

    def test_non_holding_is_holding_false(self, manager: CandidatePoolManager, ten_stocks):
        result = manager.compute_pool(ten_stocks, frozenset({"S09"}), frozenset())
        by_code = {e.ts_code: e for e in result}
        for code in ["S00", "S01", "S02"]:
            assert by_code[code].is_holding is False


# ---------------------------------------------------------------------------
# POOL-03：白名单标的额外入池（WHITELIST）
# ---------------------------------------------------------------------------
class TestPOOL03:
    def test_whitelist_forced_in_pool(self, manager: CandidatePoolManager, ten_stocks):
        # S08 分数次低（20），但因白名单强制入池
        result = manager.compute_pool(ten_stocks, frozenset(), frozenset({"S08"}))
        by_code = {e.ts_code: e for e in result}
        assert "S08" in by_code
        assert by_code["S08"].in_pool is True

    def test_whitelist_is_holding_false(self, manager: CandidatePoolManager, ten_stocks):
        """白名单标的不是持仓，is_holding=False。"""
        result = manager.compute_pool(ten_stocks, frozenset(), frozenset({"S08"}))
        by_code = {e.ts_code: e for e in result}
        assert by_code["S08"].is_holding is False


# ---------------------------------------------------------------------------
# POOL-04：总数可超 N（持仓 + 白名单使容量超出 pool_capacity）
# ---------------------------------------------------------------------------
class TestPOOL04:
    def test_pool_exceeds_capacity_with_holding_and_whitelist(
        self, manager: CandidatePoolManager, ten_stocks
    ):
        # pool_capacity=3；持仓保护 S07, S08, S09 + top3 S00/S01/S02 → 最多 6 只
        holding = frozenset({"S07", "S08", "S09"})
        result = manager.compute_pool(ten_stocks, holding, frozenset())
        in_pool = [e for e in result if e.in_pool]
        pool_codes = {e.ts_code for e in in_pool}
        # top 3 + 3 holding = 最多 6（可能重叠，但 S07/S08/S09 都不在 top3）
        assert len(pool_codes) == 6

    def test_overlap_holding_and_top_n(self, manager: CandidatePoolManager, ten_stocks):
        """持仓已在 top N 时不重复计数。"""
        holding = frozenset({"S00"})   # S00 已是 top1，不应重复
        result = manager.compute_pool(ten_stocks, holding, frozenset())
        in_pool = [e for e in result if e.in_pool]
        pool_codes = {e.ts_code for e in in_pool}
        assert len(pool_codes) == 3   # top3，S00 在其中，is_holding=True


# ---------------------------------------------------------------------------
# POOL-05：淡出标的标记（由 ScoringService 差集逻辑处理，compute_pool 返回入池集合）
# ---------------------------------------------------------------------------
class TestPOOL05:
    def test_compute_pool_returns_list_of_pool_entry(
        self, manager: CandidatePoolManager, ten_stocks
    ):
        result = manager.compute_pool(ten_stocks, frozenset(), frozenset())
        assert isinstance(result, list)
        assert all(isinstance(e, PoolEntry) for e in result)

    def test_compute_pool_no_db_io(self, manager: CandidatePoolManager, ten_stocks):
        """compute_pool() 是纯函数：无 IO，仅依赖输入参数。
        两次调用相同输入应得到相同输出（幂等性）。
        """
        r1 = manager.compute_pool(ten_stocks, frozenset(), frozenset())
        r2 = manager.compute_pool(ten_stocks, frozenset(), frozenset())
        codes1 = {e.ts_code for e in r1 if e.in_pool}
        codes2 = {e.ts_code for e in r2 if e.in_pool}
        assert codes1 == codes2

    def test_out_of_pool_codes_can_be_detected_by_diff(
        self, manager: CandidatePoolManager, ten_stocks
    ):
        """验证 ScoringService 的淡出逻辑可以从 compute_pool 结果推导出来：
        prev_pool_codes - current_pool_codes 就是需要标记 in_pool=False 的标的。
        """
        # 第一天：S00/S01/S02 入池
        prev_codes = {"S00", "S01", "S02"}
        # 第二天：S01 跌出 top3，S03 入 top3（通过修改分数模拟）
        new_stocks = [
            _make_composite("S00", 100),
            _make_composite("S01", 10),   # 大幅下跌
            _make_composite("S02", 90),
            _make_composite("S03", 80),
        ]
        result = manager.compute_pool(new_stocks, frozenset(), frozenset())
        current_codes = {e.ts_code for e in result if e.in_pool}
        fade_out = prev_codes - current_codes
        assert "S01" in fade_out    # S01 不在新池中，应被淡出
        assert "S00" not in fade_out
        assert "S02" not in fade_out
        assert "S03" not in fade_out
