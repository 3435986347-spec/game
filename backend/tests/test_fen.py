import pytest

from xiangqi.core import BLACK, RED, START_FEN, FenError, Position, parse_fen, validate_board


def test_start_roundtrip():
    assert Position.start().fen() == START_FEN
    assert Position.from_fen(START_FEN).fen() == START_FEN


def test_letter_aliases_and_red_side():
    # 有的软件用 E/H 表示相/马，用 r 表示红方走
    fen = "rheakaehr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RHEAKAEHR r"
    assert Position.from_fen(fen).fen() == START_FEN


def test_missing_counters_default():
    board, turn, halfmove, fullmove = parse_fen(START_FEN.split()[0] + " b")
    assert (turn, halfmove, fullmove) == (BLACK, 0, 1)


@pytest.mark.parametrize(
    "fen",
    [
        "",
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/RNBAKABNR w",  # 只有 9 行
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNRR w",  # 10 列
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNX w",  # 未知字母
        "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR x",  # 未知走棋方
    ],
)
def test_malformed_fen(fen):
    with pytest.raises(FenError):
        Position.from_fen(fen)


@pytest.mark.parametrize(
    ("fen", "message"),
    [
        ("3k5/9/9/9/9/9/9/9/9/3KK4 w", "应有且只有一个"),
        ("3k5/9/9/9/9/9/9/9/9/K8 w", "九宫"),
        ("3k5/9/9/9/2B6/9/9/9/9/4K4 w", "相/象"),  # 红相过河
        ("3k5/9/9/9/9/9/9/9/4P4/4K4 w", "兵/卒"),  # 红兵在己方底部
        ("3k5/9/9/9/9/9/1P7/9/9/4K4 w", "兵/卒"),  # 红兵未过河却在 b 列
        ("4k4/9/9/9/9/9/9/9/4R4/3K5 w", "直接吃掉"),  # 红方走棋，但黑将已被红车将军
    ],
)
def test_validate_board(fen, message):
    board, turn, _, _ = parse_fen(fen)
    errors = validate_board(board, turn)
    assert any(message in e for e in errors), errors


def test_validate_rejects_facing_kings_for_side_to_move():
    board, _, _, _ = parse_fen("4k4/9/9/9/9/9/9/9/9/4K4 w")
    assert validate_board(board, RED)  # 两将照面，红方走棋时可以「吃将」


def test_from_fen_can_skip_validation():
    pos = Position.from_fen("3k5/9/9/9/9/9/9/9/9/3KK4 w", validate=False)
    assert pos.board.count(1) == 2
