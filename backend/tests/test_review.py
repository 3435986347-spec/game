import asyncio
import sys
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE, play

from xiangqi.core import RED, START_FEN, Position, RuleConfig, parse_iccs
from xiangqi.engine import Limit, UciEngine
from xiangqi.llm import EngineLine
from xiangqi.training import (
    PositionEval,
    analyse_positions,
    direction_hint,
    grade_last_move,
    grade_move,
    grade_moves,
    move_accuracy,
    summarize,
)


def test_grade_thresholds():
    assert grade_move(0.0, is_best=True, gap=0.3, best_score=0.6) == "妙着"
    assert grade_move(0.0, is_best=True, gap=0.2, best_score=0.6) == "好棋"  # 差距不够大
    assert grade_move(0.0, is_best=True, gap=0.3, best_score=0.2) == "好棋"  # 输棋里的唯一防守
    assert grade_move(0.0, is_best=True, gap=0.3, best_score=0.6, obvious=True) == "好棋"  # 吃回
    assert grade_move(0.02, is_best=False, gap=None) == "好棋"
    assert grade_move(0.04, is_best=False, gap=None) == "可以"
    assert grade_move(0.08, is_best=False, gap=None) == "缓着"
    assert grade_move(0.15, is_best=False, gap=None) == "失误"
    assert grade_move(0.35, is_best=False, gap=None) == "漏着"


@pytest.mark.parametrize(
    "boundary,expected,higher",
    [
        (0.02, "好棋", "可以"),
        (0.05, "可以", "缓着"),
        (0.10, "缓着", "失误"),
        (0.20, "失误", "漏着"),
    ],
)
def test_float_subtraction_respects_exact_boundaries_without_coarse_rounding(
    boundary, expected, higher
):
    subtraction = 0.8 - (0.8 - boundary)
    assert grade_move(subtraction, is_best=False, gap=None) == expected
    # 与边界只差 1e-8 也是真实超出，不能像四位舍入那样被吞掉。
    assert grade_move(boundary + 1e-8, is_best=False, gap=None) == higher


@pytest.mark.parametrize("side", [RED, -RED])
@pytest.mark.parametrize("above", [False, True])
@pytest.mark.parametrize(
    "boundary,expected,higher",
    [
        (0.02, "好棋", "可以"),
        (0.05, "可以", "缓着"),
        (0.10, "缓着", "失误"),
        (0.20, "失误", "漏着"),
    ],
)
def test_grade_last_move_boundaries_in_both_mover_perspectives(
    side, above, boundary, expected, higher
):
    position = Position.start()
    if side != RED:
        position.push(parse_iccs("h2e2"))
    fen = position.fen()
    best, played, reply = ("a0a1", "h2e2", "a9a8") if side == RED else ("a9a8", "h7e7", "a0a1")
    drop = boundary + (0.0001 if above else 0)
    score = round(0.8 - drop, 4)
    before = ev(0, side, 0.8 if side == RED else 0.2, (best, 0.8), (played, score))
    after = ev(1, -side, 0.25 if side == RED else 0.75, (reply, 0.75))
    grade = grade_last_move(fen, [played], before, after)
    assert grade.side == side
    assert grade.win_before == 0.8 and grade.win_after == score
    assert grade.grade == (higher if above else expected)


def test_move_accuracy_range():
    assert move_accuracy(0.0) == 100.0
    assert 0 < move_accuracy(0.1) < move_accuracy(0.02) < 100
    assert move_accuracy(1.0) == 0.0


def ev(ply: int, turn: int, red: float, *lines: tuple[str, float]) -> PositionEval:
    return PositionEval(ply, turn, red, [EngineLine(m, (m,), s) for m, s in lines])


def test_grade_moves_and_summary():
    moves = ["h2e2", "h9g7", "e2e6"]
    evals = [
        ev(0, RED, 0.55, ("h2e2", 0.55), ("b2e2", 0.55)),  # 红走了最佳
        ev(1, -RED, 0.55, ("h9g7", 0.45), ("b9c7", 0.44)),  # 黑走了最佳
        ev(2, RED, 0.54, ("h0g2", 0.54), ("b0c2", 0.53)),  # 红走 e2e6，不是最佳
        ev(3, -RED, 0.16, ("g7e6", 0.84)),
    ]
    grades = grade_moves(START_FEN, moves, evals)
    assert [g.grade for g in grades] == ["好棋", "好棋", "漏着"]
    blunder = grades[2]
    assert (blunder.win_before, blunder.win_after) == (0.54, 0.16)
    assert round(blunder.drop, 2) == 0.38 and blunder.phase == "开局"

    summary = summarize(grades, [RED])
    assert summary["key_moments"] == [3]
    assert summary["stats"]["red"]["moves"] == 2
    assert summary["stats"]["red"]["grades"]["漏着"] == 1
    assert summary["stats"]["black"]["accuracy"] == 100.0
    assert summary["stats"]["red"]["phases"]["中局"] is None
    assert summarize(grades, [-RED])["key_moments"] == []  # 只关注黑方：黑方没有失误


def test_best_move_counts_as_no_loss_even_if_next_eval_drops():
    moves = ["h2e2"]
    evals = [ev(0, RED, 0.6, ("h2e2", 0.6)), ev(1, -RED, 0.5, ("h9g7", 0.5))]
    (g,) = grade_moves(START_FEN, moves, evals)
    assert g.is_best and g.drop == 0.0 and g.grade == "好棋"


@pytest.mark.parametrize("side", [RED, -RED])
@pytest.mark.parametrize("result_score", [0.0, 0.5, 1.0])
def test_terminal_result_overrides_best_immunity_in_grades_accuracy_and_key_moments(
    side, result_score
):
    position = Position.start()
    if side != RED:
        position.push(parse_iccs("h2e2"))
    move, other = ("a0a1", "h2e2") if side == RED else ("a9a8", "h7e7")
    before = ev(0, side, 0.9 if side == RED else 0.1, (move, 0.9), (other, 0.3))
    after = PositionEval(
        1, -side, result_score if side == RED else 1.0 - result_score,
        [], "规则确定的终局",
    )
    (g,) = grade_moves(position.fen(), [move], [before, after])
    last = grade_last_move(position.fen(), [move], before, after)
    assert g == last and g.is_best and g.terminal
    assert g.win_after == result_score
    assert g.drop == pytest.approx(max(0.0, 0.9 - result_score))
    assert g.grade == ("好棋" if result_score == 1.0 else "漏着")
    summary = summarize([g], [side])
    stats = summary["stats"]["red" if side == RED else "black"]
    assert stats["accuracy"] == round(move_accuracy(g.drop), 1)
    assert summary["key_moments"] == ([] if result_score == 1.0 else [1])


def test_analyse_positions_with_fake_engine():
    engine = UciEngine([sys.executable, str(FAKE_ENGINE)], cwd=Path(FAKE_ENGINE).parent)

    async def go():
        await engine.start()
        try:
            seen = []
            evals = await analyse_positions(
                engine, START_FEN, ["a0a1", "a9a8"], limit=Limit(movetime_ms=50),
                rules=RuleConfig(), progress=lambda done, total: seen.append((done, total)),
            )  # fmt: skip
            return evals, seen
        finally:
            await engine.close()

    evals, seen = asyncio.run(go())
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert [e.ply for e in evals] == [0, 1, 2]
    assert all(len(e.lines) == 3 for e in evals)  # 默认 MultiPV 3
    # 假引擎对走棋方总是 cp +40、WDL 340/500/160 → 期望得分 0.59
    assert evals[0].lines[0].score == 0.59 and evals[0].red_win == 0.59
    assert evals[1].red_win == 0.41  # 黑方走棋：红方视角 1 − 0.59


def test_final_checkmate_is_scored_from_the_result():
    # 双车错：红车沉底将死。最后一个局面直接按结果计分，不交给引擎
    engine = UciEngine([sys.executable, str(FAKE_ENGINE)], cwd=Path(FAKE_ENGINE).parent)
    fen = "4k4/R8/9/9/9/9/9/9/1R7/3K5 w - - 0 1"

    async def go():
        await engine.start()
        try:
            return await analyse_positions(
                engine, fen, ["b1b9"], limit=Limit(movetime_ms=50), rules=RuleConfig()
            )
        finally:
            await engine.close()

    evals = asyncio.run(go())
    assert evals[0].terminal is None and evals[0].lines
    assert evals[1].terminal == "红方胜（将死）" and evals[1].red_win == 1.0
    assert evals[1].lines == []
    (g,) = grade_moves(fen, ["b1b9"], evals)
    assert g.win_after == 1.0 and g.drop == 0.0


def test_direction_hint_levels():
    pos = Position.from_fen("4k4/R8/9/9/9/9/9/9/1R7/3K5 w - - 0 30")
    assert direction_hint(pos) == "你有一步杀棋的机会，仔细找一找！"
    checked = Position.from_fen("4k4/4R4/9/4P4/4C4/9/9/9/9/3K5 b - - 0 30")
    assert direction_hint(checked).startswith("你正被将军")
    hanging = play(Position.start(), "h2e2", "h9g7", "e2e6")  # 黑方走：红炮 e6 无根
    assert "炮(e6)没有保护" in direction_hint(hanging, "g7e6")
    assert "炮(e6)" not in direction_hint(hanging, "a9a8")  # 引擎最佳着法不吃子：不提示吃炮
    assert direction_hint(Position.start()).startswith("局面平稳")
