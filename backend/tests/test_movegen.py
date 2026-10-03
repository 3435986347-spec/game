import pytest
from conftest import board_of

from xiangqi.core import START_FEN, in_check, legal_moves, move_to_iccs, parse_iccs, perft
from xiangqi.core.board import ADVISOR_MOVES, BISHOP_MOVES, KING_MOVES, KNIGHT_MOVES, PAWN_MOVES


def destinations(fen: str, square: str) -> set[str]:
    """fen 局面中，square 上的棋子的全部合法落点（ICCS 坐标）。"""
    board, turn = board_of(fen)
    frm = parse_iccs(square + square)[0]
    return {move_to_iccs(m)[2:] for m in legal_moves(board, turn) if m[0] == frm}


@pytest.mark.parametrize(("depth", "nodes"), [(1, 44), (2, 1920), (3, 79666)])
def test_perft_start(depth, nodes):
    board, turn = board_of(START_FEN)
    assert perft(board, turn, depth) == nodes


@pytest.mark.slow
def test_perft_start_depth4():
    board, turn = board_of(START_FEN)
    assert perft(board, turn, 4) == 3290240


def test_tables_stay_on_board():
    # 回归测试：曾因九宫判断漏了 rank >= 0，产生负下标，Python 列表会静默地从末尾取值
    for row in ADVISOR_MOVES + KING_MOVES + PAWN_MOVES[1] + PAWN_MOVES[-1]:
        assert all(0 <= x < 90 for x in row)
    for row in KNIGHT_MOVES + BISHOP_MOVES:
        assert all(0 <= x < 90 and 0 <= y < 90 for x, y in row)


def test_knight_leg_blocks():
    # 初始局面 b0 马：去 d1 的马腿 c0 有相，被蹩住
    assert destinations(START_FEN, "b0") == {"a2", "c2"}


def test_bishop_eye_blocks():
    assert destinations("3k5/9/9/9/9/9/9/9/3N5/2B1K4 w - - 0 1", "c0") == {"a2"}


def test_bishop_cannot_cross_river():
    assert destinations("3k5/9/9/9/9/2B6/9/9/9/4K4 w - - 0 1", "c4") == {"a2", "e2"}


def test_cannon_needs_screen_to_capture():
    dest = destinations(START_FEN, "b2")
    assert "b9" in dest  # 隔着 b7 黑炮吃 b9 黑马
    assert "b7" not in dest and "b8" not in dest


def test_pawn_moves_before_and_after_river():
    assert destinations(START_FEN, "e3") == {"e4"}
    assert destinations("3k5/9/9/9/4P4/9/9/9/9/4K4 w - - 0 1", "e5") == {"e6", "d5", "f5"}


def test_flying_general_pins_blocker():
    # e5 马是两将之间唯一的子，走开就会照面，所以一步也不能走
    assert destinations("4k4/9/9/9/4N4/9/9/9/9/4K4 w - - 0 1", "e5") == set()


@pytest.mark.parametrize(
    ("fen", "side", "expected"),
    [
        ("4k4/9/9/9/4p4/9/9/4C4/9/3K5 b - - 0 1", -1, True),  # 炮隔一子将军
        ("3k5/9/4N4/9/9/9/9/9/9/5K3 b - - 0 1", -1, True),  # 马将军
        # 马腿 e8 被堵住，不算将军。帅旁边的 d8 是空的，误用它当马腿就会判错
        ("3k5/4p4/4N4/9/9/9/9/9/9/5K3 b - - 0 1", -1, False),
        ("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1", 1, True),  # 将帅照面
        ("3k5/9/9/9/9/9/9/9/3pK4/9 w - - 0 1", 1, True),  # 过河卒横向将军
        (START_FEN, 1, False),
    ],
)
def test_in_check(fen, side, expected):
    board, _ = board_of(fen)
    assert in_check(board, side) is expected
