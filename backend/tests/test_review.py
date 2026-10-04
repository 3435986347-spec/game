import asyncio
import sys
from pathlib import Path

from conftest import FAKE_ENGINE, play

from xiangqi.core import RED, START_FEN, Position, RuleConfig
from xiangqi.engine import Limit, UciEngine
from xiangqi.llm import EngineLine
from xiangqi.training import (
    PositionEval,
    analyse_positions,
    direction_hint,
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
