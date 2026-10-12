import asyncio
from dataclasses import replace

import pytest

from xiangqi.core import START_FEN, Position, RuleConfig, move_to_iccs
from xiangqi.engine import AnalysisCache, AnalysisResult, InfoLine, Limit
from xiangqi.training import evaluate_position


def analysis(depth=12, multipv=1, *, completed=True, nodes=1000):
    moves = ("h2e2", "b2e2", "h0g2")
    lines = [
        InfoLine(i, depth, [move], score_cp=40 - i, nodes=nodes)
        for i, move in enumerate(moves[:multipv], start=1)
    ]
    return AnalysisResult(lines, lines[0].move if completed else None)


def test_shared_cache_requires_same_initial_position_and_complete_history():
    cache = AnalysisCache()
    result = analysis()
    history = ["h0g2", "h9g7", "g2h0", "g7h9"]
    cache.put(START_FEN, history, 1, 1500, result)
    assert cache.get(START_FEN, tuple(history), 1, 1500) == result
    assert cache.get(START_FEN, [], 1, 1500) is None  # 同棋盘、不同重复历史
    assert cache.get(START_FEN.replace("0 1", "0 2"), history, 1, 1500) is None
    history.append("h2e2")
    assert cache.get(START_FEN, history, 1, 1500) is None
    assert cache.get(START_FEN, history[:-1], 1, 1500) == result


def test_reuse_requires_sufficient_time_and_candidate_search():
    cache = AnalysisCache()
    result = analysis(multipv=3)
    cache.put(START_FEN, [], 3, 1500, result)
    assert cache.get(START_FEN, [], 3, 1500) == result
    assert cache.get(START_FEN, [], 3, 1501) is None
    assert cache.get(START_FEN, [], 4, 500) is None
    single = cache.get(START_FEN, [], 1, 500)
    assert single.lines == result.lines[:1]
    assert single.bestmove == result.bestmove
    cache.put(START_FEN, ["h2e2"], 1, 1500, analysis())
    assert cache.get(START_FEN, ["h2e2"], 3, 500) is None


def test_infinite_snapshots_seed_but_do_not_claim_finite_search_quality():
    cache = AnalysisCache()
    snapshot = analysis(depth=20, multipv=3, completed=False)
    cache.put(START_FEN, [], 3, None, snapshot)
    assert cache.get(START_FEN, [], 3, None) == snapshot
    assert cache.get(START_FEN, [], 1, 10) is None
    finite = analysis(depth=12, multipv=3)
    cache.put(START_FEN, [], 3, 1500, finite)
    assert cache.get(START_FEN, [], 3, None) == snapshot
    assert cache.get(START_FEN, [], 3, 1500) == finite


def test_finite_result_can_seed_a_revisited_realtime_position():
    cache = AnalysisCache()
    result = analysis(depth=18, multipv=3)
    cache.put(START_FEN, [], 3, 1500, result)
    assert cache.get(START_FEN, [], 3, None) == result


def test_shallow_restart_does_not_replace_deep_snapshot():
    cache = AnalysisCache()
    deep = analysis(depth=20, multipv=3, completed=False)
    cache.put(START_FEN, [], 3, None, deep)
    cache.put(START_FEN, [], 3, None, analysis(depth=5, multipv=3, completed=False))
    assert cache.get(START_FEN, [], 3, None) == deep
    deeper = analysis(depth=21, multipv=3, completed=False)
    cache.put(START_FEN, [], 3, None, deeper)
    assert cache.get(START_FEN, [], 3, None) == deeper


def test_same_depth_updates_refresh_score_and_remaining_candidates():
    cache = AnalysisCache()
    partial = analysis(depth=20, completed=False)
    cache.put(START_FEN, [], 3, None, partial)
    complete = analysis(depth=20, multipv=3, completed=False)
    cache.put(START_FEN, [], 3, None, complete)
    assert cache.get(START_FEN, [], 3, None) == complete
    complete.lines[0] = replace(complete.lines[0], score_cp=60, nodes=2000)
    cache.put(START_FEN, [], 3, None, complete)
    assert cache.get(START_FEN, [], 3, None) == complete


def test_cache_snapshots_are_isolated_from_callers():
    cache = AnalysisCache()
    result = analysis()
    cache.put(START_FEN, [], 1, 1500, result)
    result.lines[0].pv.append("h9g7")
    result.engine_messages.append("caller changed original")
    first = cache.get(START_FEN, [], 1, 1500)
    assert first.lines[0].pv == ["h2e2"] and first.engine_messages == []
    first.lines[0].pv.clear()
    first.lines.clear()
    first.engine_messages.append("caller changed snapshot")
    again = cache.get(START_FEN, [], 1, 1500)
    assert again.lines[0].pv == ["h2e2"] and again.engine_messages == []


def test_capacity_evicts_least_recently_used_position():
    cache = AnalysisCache(max_entries=2)
    result = analysis()
    cache.put(START_FEN, [], 1, 1500, result)
    cache.put(START_FEN, ["h2e2"], 1, 1500, result)
    assert cache.get(START_FEN, [], 1, 1500) is not None
    cache.put(START_FEN, ["b2e2"], 1, 1500, result)
    assert cache.get(START_FEN, ["h2e2"], 1, 1500) is None
    assert cache.get(START_FEN, [], 1, 1500) == result
    assert cache.get(START_FEN, ["b2e2"], 1, 1500) == result


def test_capacity_limits_search_variants_of_one_position():
    cache = AnalysisCache(max_entries=2)
    result = analysis()
    for movetime in (1500, 1000, 500):
        cache.put(START_FEN, [], 1, movetime, result)
    assert cache.get(START_FEN, [], 1, 1500) is None
    assert cache.get(START_FEN, [], 1, 1000) == result
    assert cache.get(START_FEN, [], 1, 500) == result


def test_empty_incomplete_and_bound_results_are_not_reused():
    cache = AnalysisCache()
    cache.put(START_FEN, [], 1, 1500, AnalysisResult([], None))
    cache.put(START_FEN, [], 1, 1500, analysis(completed=False))
    result = analysis()
    result.lines[0] = replace(result.lines[0], bound="lower")
    cache.put(START_FEN, [], 1, 1500, result)
    assert cache.get(START_FEN, [], 1, 1500) is None


def test_completed_bestmove_must_match_the_scored_first_candidate():
    cache = AnalysisCache()
    result = analysis()
    result.bestmove = "b2e2"
    cache.put(START_FEN, [], 1, 1500, result)
    assert cache.get(START_FEN, [], 1, 1500) is None
    assert cache.get(START_FEN, [], 1, None) is None


@pytest.mark.parametrize("problem", ["missing_score", "negative_wdl", "wrong_wdl_total"])
def test_missing_or_malformed_scores_cannot_enter_the_shared_cache(problem):
    cache = AnalysisCache()
    result = analysis()
    if problem == "missing_score":
        result.lines[0] = replace(result.lines[0], score_cp=None)
    elif problem == "negative_wdl":
        result.lines[0] = replace(result.lines[0], wdl=(-1, 1000, 1))
    else:
        result.lines[0] = replace(result.lines[0], wdl=(500, 500, 500))
    cache.put(START_FEN, [], 1, 1500, result)
    cache.put(START_FEN, [], 1, None, result)
    assert cache.get(START_FEN, [], 1, 1500) is None
    assert cache.get(START_FEN, [], 1, None) is None


def test_unscored_second_candidate_does_not_claim_full_coverage():
    cache = AnalysisCache()
    result = analysis(multipv=3)
    result.lines[1] = replace(result.lines[1], score_cp=None)
    cache.put(START_FEN, [], 3, 1500, result)
    assert cache.get(START_FEN, [], 3, 1500) is None
    assert cache.get(START_FEN, [], 1, 1500).lines == result.lines[:1]


def test_zero_capacity_disables_cache_and_negative_capacity_is_rejected():
    cache = AnalysisCache(max_entries=0)
    cache.put(START_FEN, [], 1, 1500, analysis())
    assert cache.get(START_FEN, [], 1, 1500) is None
    with pytest.raises(ValueError, match="max_entries"):
        AnalysisCache(max_entries=-1)


def test_partial_candidates_do_not_claim_requested_multipv_coverage():
    cache = AnalysisCache()
    partial = analysis(depth=30)
    cache.put(START_FEN, [], 3, 1500, partial)
    assert cache.get(START_FEN, [], 3, 1500) is None
    assert cache.get(START_FEN, [], 3, None) is None
    assert cache.get(START_FEN, [], 1, 1500) == partial


def test_deep_partial_update_does_not_replace_complete_candidate_search():
    cache = AnalysisCache()
    complete = analysis(depth=20, multipv=3, completed=False)
    cache.put(START_FEN, [], 3, None, complete)
    partial = analysis(depth=30, completed=False)
    cache.put(START_FEN, [], 3, None, partial)
    assert cache.get(START_FEN, [], 3, None) == complete
    assert cache.get(START_FEN, [], 1, None) == partial


@pytest.mark.parametrize("problem", ["missing_rank", "duplicate", "bound", "empty_pv"])
def test_candidate_coverage_stops_at_incomplete_or_unreliable_rank(problem):
    cache = AnalysisCache()
    result = analysis(multipv=3)
    if problem == "missing_rank":
        result.lines.pop(1)
    elif problem == "duplicate":
        result.lines[1] = replace(result.lines[1], pv=list(result.lines[0].pv))
    elif problem == "bound":
        result.lines[1] = replace(result.lines[1], bound="lower")
    else:
        result.lines[1] = replace(result.lines[1], pv=[])
    cache.put(START_FEN, [], 3, 1500, result)
    assert cache.get(START_FEN, [], 2, 1500) is None
    assert cache.get(START_FEN, [], 3, 1500) is None
    first = cache.get(START_FEN, [], 1, 1500)
    assert first is not None and first.lines == result.lines[:1]


def test_all_legal_moves_satisfy_a_larger_multipv_request():
    fen = "3k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1"
    legal = [move_to_iccs(move) for move in Position.from_fen(fen).legal_moves()]
    assert len(legal) == 2
    result = AnalysisResult(
        [InfoLine(rank, 20, [move], score_cp=0) for rank, move in enumerate(legal, start=1)],
        legal[0],
    )
    cache = AnalysisCache()
    cache.put(fen, [], 3, 1500, result)
    assert cache.get(fen, [], 3, 1500) == result
    # 实际缺少唯一另一应着时，仍不能把一条当作两条完整候选。
    incomplete = AnalysisCache()
    incomplete.put(fen, [], 3, 1500, AnalysisResult(result.lines[:1], legal[0]))
    assert incomplete.get(fen, [], 3, 1500) is None


@pytest.mark.parametrize(
    "limit",
    [
        Limit(depth=30),
        Limit(nodes=50000),
        Limit(movetime_ms=1500, depth=30),
        Limit(movetime_ms=1500, nodes=50000),
    ],
)
def test_depth_node_and_mixed_searches_do_not_reuse_time_or_realtime_cache(limit):
    cache = AnalysisCache()
    cache.put(START_FEN, [], 3, None, analysis(depth=5, multipv=3, completed=False))
    cache.put(START_FEN, [], 3, 1500, analysis(depth=8, multipv=3))

    class Engine:
        calls = []

        async def analyse(self, fen, moves, *, limit, multipv):
            self.calls.append(limit)
            return analysis(depth=30, multipv=multipv)

    engine = Engine()
    evaluated = asyncio.run(
        evaluate_position(engine, START_FEN, [], limit=limit, rules=RuleConfig(), cache=cache)
    )
    assert engine.calls == [limit]
    assert evaluated.lines[0].move == "h2e2"
    # 非时间搜索不能覆盖用于实时预填的原始缓存。
    assert cache.get(START_FEN, [], 3, None).lines[0].depth == 8
