import random
from dataclasses import replace

import pytest

from xiangqi.engine import (
    AnalysisResult,
    InfoLine,
    choose_move,
    get_level,
    search_multipv,
)


def line(rank, cp=100, score=0.60, **kwargs):
    return InfoLine(
        rank, 24, [f"a0a{rank}"], score_cp=cp,
        wdl=(round(score * 1000), 0, 1000 - round(score * 1000)), **kwargs,
    )


def result(*lines):
    return AnalysisResult(list(lines), lines[0].move if lines else None)


def picks(analysis, level=10, count=500, **kwargs):
    return [
        choose_move(analysis, get_level(level), random.Random(seed), style="varied", **kwargs)
        for seed in range(count)
    ]


@pytest.mark.parametrize("level", range(1, 11))
def test_varied_search_changes_only_high_level_multipv(level):
    difficulty = get_level(level)
    before_limit = difficulty.limit()
    assert search_multipv(difficulty) == difficulty.multipv
    assert search_multipv(difficulty, "varied") == (
        max(3, difficulty.multipv) if level >= 8 else difficulty.multipv
    )
    assert difficulty.limit() == before_limit
    if level == 10:
        assert before_limit.movetime_ms == 4000 and before_limit.nodes is None


@pytest.mark.parametrize("level", [8, 9, 10])
def test_near_equal_candidates_vary_with_preference_for_engine_best(level):
    choices = picks(result(line(1), line(2, 95, 0.597), line(3, 90, 0.595)), level)
    assert set(choices) == {"a0a1", "a0a2", "a0a3"}
    assert choices.count("a0a1") > choices.count("a0a2") > choices.count("a0a3")


@pytest.mark.parametrize("level", [8, 9, 10])
@pytest.mark.parametrize("score", [0.0, 0.6, 1.0])
def test_both_thresholds_are_required_including_saturated_wdl(level, score):
    choices = picks(result(
        line(1, 100, score),
        line(2, 85, max(score - 0.01, 0)),  # 阈值边界可选。
        line(3, 84, score),  # 超过 cp 限制，即使 WDL 相同也不能选。
        line(4, 100, score - 0.011) if score else line(4, 0, 0),
    ), level)
    assert set(choices) == {"a0a1", "a0a2"}


def test_cp_conversion_is_available_when_engine_has_no_wdl():
    choices = picks(result(*[
        replace(line(rank, cp), wdl=None) for rank, cp in [(1, 0), (2, -5), (3, -15)]
    ]))
    assert set(choices) == {"a0a1", "a0a2"}  # -15cp 的近似期望分已超过 0.01 差距。


def test_native_wdl_and_cp_approximation_are_not_mixed():
    analysis = result(line(1, 0, 0.5), replace(line(2, 0, 0.5), wdl=None))
    assert set(picks(analysis)) == {"a0a1"}
    analysis.lines.reverse()
    analysis.bestmove = "a0a2"
    assert set(picks(analysis)) == {"a0a2"}


@pytest.mark.parametrize("replacement", [
    {"bound": "upper"},
    {"bound": "lower"},
    {"score_cp": None},
    {"score_cp": float("nan")},
    {"score_cp": float("inf")},
    {"wdl": (1000, 1000, 0)},
    {"wdl": (1001, 0, -1)},
    {"depth": 23},
    {"depth": 25},
    {"pv": []},
])
def test_unreliable_alternatives_never_enter_the_draw(replacement):
    analysis = result(line(1), replace(line(2), **replacement))
    assert set(picks(analysis)) == {"a0a1"}


@pytest.mark.parametrize("replacement", [
    {"bound": "upper"},
    {"score_cp": None},
    {"wdl": (1, 2, 3)},
    {"pv": []},
])
def test_missing_best_evidence_returns_final_bestmove(replacement):
    analysis = AnalysisResult([replace(line(1), **replacement), line(2)], "a0a1")
    assert set(picks(analysis)) == {"a0a1"}
    analysis.bestmove = "b0c2"
    assert set(picks(analysis)) == {"b0c2"}


def test_repeated_candidate_does_not_gain_extra_random_weight():
    original = result(line(1), line(2))
    duplicate = result(line(1), line(2), replace(line(2), multipv=3))
    assert picks(original) == picks(duplicate)


def test_illegal_candidate_cannot_be_selected_and_illegal_best_is_reported():
    analysis = result(line(1), line(2), line(3))
    assert set(picks(analysis, legal_moves={"a0a1", "a0a3"})) == {"a0a1", "a0a3"}
    assert set(picks(analysis, legal_moves={"a0a2", "a0a3"})) == {None}
    assert set(picks(analysis, legal_moves=set())) == {None}


@pytest.mark.parametrize("level", [8, 9, 10])
def test_forced_wins_only_vary_between_equally_fast_mates(level):
    analysis = result(
        replace(line(1), score_cp=None, mate=100),
        replace(line(2), score_cp=None, mate=101),
        replace(line(3), score_cp=None, mate=100),
        line(4, 1000, 1),
    )
    assert set(picks(analysis, level)) == {"a0a1", "a0a3"}


def test_forced_losses_only_vary_between_longest_resistance():
    analysis = result(
        replace(line(1), score_cp=None, mate=-101),
        replace(line(2), score_cp=None, mate=-100),
        replace(line(3), score_cp=None, mate=-101),
        replace(line(4), score_cp=None, mate=0),
    )
    assert set(picks(analysis)) == {"a0a1", "a0a3"}
    analysis.lines.append(line(5, -1000, 0))
    assert set(picks(analysis)) == {"a0a5"}


@pytest.mark.parametrize("level", range(1, 8))
def test_teaching_levels_keep_the_same_selection_and_rng_sequence(level):
    analysis = result(line(1), line(2, 50, 0.55), line(3, 0, 0.5))
    for seed in range(30):
        assert choose_move(analysis, get_level(level), random.Random(seed)) == choose_move(
            analysis, get_level(level), random.Random(seed), style="varied",
        )


def test_standard_full_strength_and_legality_filter_remain_available():
    analysis = result(line(1), line(2))
    assert {
        choose_move(analysis, get_level(10), random.Random(seed)) for seed in range(50)
    } == {"a0a1"}
    assert choose_move(
        analysis, get_level(10), random.Random(0), legal_moves={"a0a2"},
    ) is None
    assert {
        choose_move(analysis, get_level(1), random.Random(seed), legal_moves={"a0a1"})
        for seed in range(50)
    } == {"a0a1"}


def test_empty_result_and_unknown_style():
    assert choose_move(result(), get_level(10), random.Random(0), style="varied") is None
    with pytest.raises(ValueError, match="风格"):
        search_multipv(get_level(10), "unknown")
    with pytest.raises(ValueError, match="风格"):
        choose_move(result(), get_level(10), random.Random(0), style="unknown")
