import random

import pytest
from conftest import board_of

from xiangqi.core import (
    START_FEN,
    NotationError,
    Position,
    legal_moves,
    move_to_chinese,
    move_to_iccs,
    parse_chinese,
    parse_iccs,
    parse_move,
)


@pytest.mark.parametrize(
    ("fen", "iccs", "cn"),
    [
        (START_FEN, "h2e2", "炮二平五"),
        (START_FEN, "b0c2", "马八进七"),
        (START_FEN, "i0i1", "车一进一"),
        (START_FEN, "h0g2", "马二进三"),
        (START_FEN, "g3g4", "兵三进一"),
        (START_FEN, "c0e2", "相七进五"),
        (START_FEN, "f0e1", "仕四进五"),
        (START_FEN, "e0e1", "帅五进一"),
        (START_FEN.replace(" w ", " b "), "h9g7", "马8进7"),
        (START_FEN.replace(" w ", " b "), "i9i8", "车9进1"),
        (START_FEN.replace(" w ", " b "), "b7e7", "炮2平5"),
        # 同一纵线两个车 / 马：前后
        ("4k4/9/4n4/4n4/8R/9/9/9/9/3K4R w", "i5i7", "前车进二"),
        ("4k4/9/4n4/4n4/8R/9/9/9/9/3K4R w", "i0h0", "后车平二"),
        ("4k4/9/4n4/4n4/8R/9/9/9/9/3K4R b", "e6d4", "前马进4"),
        ("4k4/9/4n4/4n4/8R/9/9/9/9/3K4R b", "e7c8", "后马退3"),
        # 仕相在同一纵线时不用前后，靠进退区分
        ("3k5/9/9/9/9/9/9/3A5/9/3AK4 w", "d0e1", "仕六进五"),
        ("3k5/9/9/9/9/9/9/3A5/9/3AK4 w", "d2e1", "仕六退五"),
        # 同一纵线三个兵：前中后
        ("3k5/9/4P4/4P4/4P4/9/9/9/9/4K4 w", "e7e8", "前兵进一"),
        ("3k5/9/4P4/4P4/4P4/9/9/9/9/4K4 w", "e6d6", "中兵平六"),
        ("3k5/9/4P4/4P4/4P4/9/9/9/9/4K4 w", "e5f5", "后兵平四"),
        # 两条纵线各有两个兵：位置词 + 纵线号
        ("3k5/9/9/2P1P4/2P1P4/9/9/9/9/4K4 w", "e6e7", "前五进一"),
        ("3k5/9/9/2P1P4/2P1P4/9/9/9/9/4K4 w", "c5b5", "后七平八"),
    ],
)
def test_move_to_chinese(fen, iccs, cn):
    board, turn = board_of(fen)
    move = parse_iccs(iccs)
    assert move in legal_moves(board, turn)
    assert move_to_chinese(board, move) == cn
    assert parse_chinese(board, turn, cn) == move


@pytest.mark.parametrize("text", ["炮二平五", "砲二平五", "炮2平5", "炮 二 平 五", "h2e2", "H2-E2"])
def test_parse_variants_red(text):
    board, turn = board_of(START_FEN)
    assert move_to_iccs(parse_move(board, turn, text)) == "h2e2"


@pytest.mark.parametrize("text", ["马8进7", "馬８進７", "马八进七", "傌8进7"])
def test_parse_variants_black(text):
    board, turn = board_of(START_FEN.replace(" w ", " b "))
    assert move_to_iccs(parse_move(board, turn, text)) == "h9g7"


def test_parse_alias_with_piece_name_for_multiple_pawn_files():
    board, turn = board_of("3k5/9/9/2P1P4/2P1P4/9/9/9/9/4K4 w")
    assert move_to_iccs(parse_chinese(board, turn, "前兵五进一")) == "e6e7"
    with pytest.raises(NotationError, match="歧义"):
        parse_chinese(board, turn, "前兵进一")  # 五路和七路都有前兵


@pytest.mark.parametrize("text", ["车一平二", "车一进十", "abc", "h2h7", "h2h8", ""])
def test_parse_rejects_bad_moves(text):
    board, turn = board_of(START_FEN)
    with pytest.raises(NotationError):
        parse_move(board, turn, text)


def test_iccs_roundtrip():
    for text in ("a0a1", "i9h9", "e3e4"):
        assert move_to_iccs(parse_iccs(text)) == text


def test_random_games_notation_is_unique_and_roundtrips():
    """随机对局里每一步：标准记谱在所有合法着法中唯一，并能解析回同一着法。"""
    rng = random.Random(20261003)
    for _ in range(30):
        pos = Position.start()
        for _ in range(200):
            moves = pos.legal_moves()
            if not moves:
                break
            names = [move_to_chinese(pos.board, m) for m in moves]
            assert len(set(names)) == len(names), (pos.fen(), names)
            move = rng.choice(moves)
            assert parse_chinese(pos.board, pos.turn, move_to_chinese(pos.board, move)) == move
            pos.push(move)
