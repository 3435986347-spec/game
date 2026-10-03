import random

import pytest
from conftest import play

from xiangqi.core import START_FEN, IllegalMoveError, Position, parse_iccs
from xiangqi.core.zobrist import compute_key


def test_push_pop_restores_everything():
    pos = Position.start()
    play(pos, "h2e2", "h9g7", "e2e6", "g7e6")  # 炮五进四吃卒，马7进5吃炮
    assert pos.halfmove_clock == 0 and pos.fullmove == 3
    for _ in range(4):
        pos.pop()
    assert pos.fen() == START_FEN
    assert pos.key == compute_key(pos.board, pos.turn)
    assert pos.ply == 0


def test_halfmove_clock_and_fullmove():
    pos = play(Position.start(), "h2e2", "h9g7", "h0g2")
    assert pos.fen().endswith(" b - - 3 2")


def test_incremental_zobrist_matches_full_recompute():
    rng = random.Random(7)
    pos = Position.start()
    for _ in range(300):
        moves = pos.legal_moves()
        if not moves:
            break
        pos.push(rng.choice(moves))
        assert pos.key == compute_key(pos.board, pos.turn)


def test_illegal_move_raises():
    pos = Position.start()
    with pytest.raises(IllegalMoveError):
        pos.push(parse_iccs("h2h7"))  # 炮不隔子不能吃
    assert pos.fen() == START_FEN


def test_copy_is_independent():
    pos = Position.start()
    other = pos.copy()
    other.push(parse_iccs("h2e2"))
    assert pos.fen() == START_FEN and other.ply == 1


def test_pop_on_empty_raises():
    with pytest.raises(IndexError):
        Position.start().pop()
