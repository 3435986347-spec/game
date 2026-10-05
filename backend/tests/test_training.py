import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import play

from xiangqi.core import RED, START_FEN, Position
from xiangqi.engine import Limit
from xiangqi.library import Library
from xiangqi.llm import EngineLine
from xiangqi.training import PositionEval, TrainingStore, judge_moves
from xiangqi.training.guess import first_turn, guess_points, side_to_move_at, worst_answers
from xiangqi.training.puzzles import extract_puzzles, puzzle_rating, puzzle_tags, update_rating
from xiangqi.training.srs import schedule


def ev(ply: int, turn: int, *lines: tuple[str, float], mate: int | None = None) -> PositionEval:
    out = [
        EngineLine(m, (m, "a9a8"), s, mate if i == 0 else None) for i, (m, s) in enumerate(lines)
    ]
    score = lines[0][1] if lines else 0.5
    return PositionEval(ply, turn, score if turn == RED else 1 - score, out)


def test_guess_points_table():
    assert guess_points(0.5, 0.5, same=True) == 3
    assert guess_points(0.6, 0.5, same=False) == 3  # 比大师着法还好
    assert guess_points(0.48, 0.5, same=False) == 2
    assert guess_points(0.43, 0.5, same=False) == 1
    assert guess_points(0.30, 0.5, same=False) == 0


def test_guess_turns():
    assert side_to_move_at(START_FEN, 0) == "red" and side_to_move_at(START_FEN, 3) == "black"
    moves = ["h2e2", "h9g7", "h0g2", "i9h9"]
    assert first_turn(START_FEN, moves, "black", 0) == 1
    assert first_turn(START_FEN, moves, "red", 1) == 2
    assert first_turn(START_FEN, moves, "red", 4) is None


def test_worst_answers_prefers_zero_points_and_big_losses():
    answers = [
        {"ply": 1, "points": 3, "loss": 0.0},
        {"ply": 3, "points": 0, "loss": 0.3},
        {"ply": 5, "points": 1, "loss": 0.08},
        {"ply": 7, "points": 0, "loss": None},  # 放弃
        {"ply": 9, "points": 0, "loss": 0.5},
    ]
    assert [a["ply"] for a in worst_answers(answers)] == [7, 9, 3]


def test_srs_schedule():
    now = datetime(2026, 10, 5, 9, 0, 0)
    card = {"reps": 0, "lapses": 0, "interval_days": 0.0, "ease": 2.5}
    first = schedule(card, True, now)
    assert first["interval_days"] == 1.0 and first["due_at"] == "2026-10-06 09:00:00"
    second = schedule({**card, **first}, True, now)
    assert second["interval_days"] == 3.0
    third = schedule({**card, **second}, True, now)
    assert third["interval_days"] == round(3.0 * second["ease"], 1)
    wrong = schedule({**card, **third}, False, now)
    assert wrong["reps"] == 0 and wrong["lapses"] == 1 and wrong["interval_days"] == 0.0
    assert wrong["due_at"] == (now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    assert wrong["ease"] < third["ease"]


def test_recapture_is_not_a_puzzle():
    # 1. 炮二平五 马8进7 2. 炮五进四：马7进5 是唯一好棋，但只是吃回刚吃了卒的炮，太明显，不出题
    moves = ["h2e2", "h9g7", "e2e6"]
    evals = [
        ev(0, RED, ("h2e2", 0.55), ("b2e2", 0.55)),
        ev(1, -RED, ("h9g7", 0.45), ("b9c7", 0.44)),
        ev(2, RED, ("h0g2", 0.54), ("b0c2", 0.53)),
        ev(3, -RED, ("g7e6", 0.84), ("a9a8", 0.40)),
    ]
    assert extract_puzzles(START_FEN, moves, evals, game_id=7) == []


def test_puzzle_extraction_and_tags():
    # 红车平平常常走到 a5，被黑马捉住：黑方马2退1（吃车）是唯一好棋
    fen = "3k5/9/1n7/9/9/9/9/9/9/R3K4 w - - 0 1"
    evals = [
        ev(0, RED, ("a0a1", 0.30), ("a0a2", 0.29)),
        ev(1, -RED, ("b7a5", 0.95), ("d9e9", 0.40)),
    ]
    puzzles = extract_puzzles(fen, ["a0a5"], evals, game_id=7)
    assert len(puzzles) == 1
    p = puzzles[0]
    assert p["solution"] == "b7a5" and p["source_ply"] == 1 and p["source_game_id"] == 7
    assert p["master_found"] == 0  # 棋谱到此为止，没走出正解
    assert "吃子" in p["tags"] and "残局" in p["tags"]
    assert p["rating"] < 1200  # 吃无根子比较容易
    assert p["fen"] == play(Position.from_fen(fen), "a0a5").fen()


def test_puzzle_tags_mate_and_rating():
    pos = Position.from_fen("4k4/R8/9/9/9/9/9/9/1R7/3K5 w - - 0 1")
    line = EngineLine("b1b9", ("b1b9",), 1.0, 1)
    tags = puzzle_tags(pos, line)
    assert tags[:3] == ["杀法", "1步杀", "将军"] and "残局" in tags
    quiet = puzzle_rating(["要着", "中局"], EngineLine("a0a1", ("a0a1",), 0.6))
    assert quiet > 1200


def test_update_rating():
    assert update_rating(1200, 1200, True) == 1216.0
    assert update_rating(1200, 1200, False) == 1184.0
    assert update_rating(1200, 1600, True) > 1216  # 做对难题涨得多


def test_store_cards_and_puzzles(tmp_path):
    library = Library(tmp_path / "lib.db")
    store = TrainingStore(library)
    fen = Position.start().fen()
    card_id = store.add_card("mistake", fen, "h2e2", played="a0a1", pv=["h9g7"], source="测试")
    assert card_id is not None
    assert store.add_card("mistake", fen, "h2e2") is None  # 同一局面同一正解只收一次
    assert store.due_count() == 1 and store.next_due()["id"] == card_id
    store.update_card(card_id, {"due_at": "2999-01-01 00:00:00"})
    assert store.due_count() == 0 and store.next_due() is None
    assert store.card(card_id)["pv"] == "h9g7"

    added = store.add_puzzles(
        [
            {"fen": fen, "solution": "h2e2", "pv": "", "tags": ["要着", "开局"], "rating": 1400,
             "source_game_id": None, "source_ply": 0, "master_found": 1},
            {"fen": fen, "solution": "b2e2", "pv": "", "tags": ["杀法"], "rating": 900,
             "source_game_id": None, "source_ply": 0, "master_found": 0},
        ]
    )  # fmt: skip
    assert added == 2 and store.add_puzzles([{**store.puzzle(1), "tags": ["x"]}]) == 0
    assert store.pick_puzzle(1000)["solution"] == "b2e2"  # 难度最接近
    assert store.pick_puzzle(1000, theme="要着")["solution"] == "h2e2"
    store.record_attempt(1, True)
    stats = store.puzzle_stats()
    assert stats["total"] == 2 and stats["attempted"] == 1 and stats["solved"] == 1
    assert {"tag": "开局", "count": 1} in stats["tags"]
    library.close()


class StubEngine:
    """按固定分数排序的假引擎；只搜部分着法（searchmoves）时分数整体低 0.05，模拟两次搜索的波动。"""

    SCORES = {"h2e2": 0.7, "b2e2": 0.6, "h0g2": 0.55, "a3a4": 0.5, "i0i1": 0.3}

    def __init__(self) -> None:
        self.searched: list[tuple[str, ...]] = []

    async def analyse(self, fen, moves, *, limit, multipv=1, searchmoves=()):
        self.searched.append(tuple(searchmoves))
        shift = 0.05 if searchmoves else 0.0
        picked = [m for m in self.SCORES if not searchmoves or m in searchmoves][:multipv]
        lines = [
            SimpleNamespace(
                move=m, pv=[m], mate=None, expected_score=lambda s=self.SCORES[m] - shift: s
            )
            for m in picked
        ]
        return SimpleNamespace(lines=lines)


def test_judge_moves_compares_best_in_the_same_search():
    engine = StubEngine()
    limit = Limit(movetime_ms=1)
    # 两步都在前 3 名里：一次搜索就够
    judged = asyncio.run(judge_moves(engine, START_FEN, [], ["b2e2", "h0g2"], limit=limit))
    assert engine.searched == [()]
    assert judged.best.move == "h2e2" and judged.best_score == pytest.approx(0.7)
    # 有一步不在前 3 名里：只搜这几步，连同引擎的最佳着法，评估可以直接比较
    judged = asyncio.run(judge_moves(engine, START_FEN, [], ["i0i1", "a3a4"], limit=limit))
    assert set(engine.searched[-1]) == {"i0i1", "a3a4", "h2e2"}
    assert judged.scores == {
        "i0i1": pytest.approx(0.25),
        "a3a4": pytest.approx(0.45),
        "h2e2": pytest.approx(0.65),
    }
    assert judged.best.score == pytest.approx(0.7) and judged.best_score == pytest.approx(0.65)
