from conftest import board_of, play

from xiangqi.core import Position, parse_iccs
from xiangqi.core.features import (
    attackers,
    checking_moves,
    defenders,
    describe,
    game_phase,
    material,
    material_text,
    mating_moves,
    piece_value,
    threatened,
)


def sq(name: str) -> int:
    return parse_iccs(name + name)[0]


def test_hanging_cannon_after_greedy_capture():
    # 1. 炮二平五 马8进7 2. 炮五进四：炮吃中卒后被黑马攻击，且没有子保护
    pos = play(Position.start(), "h2e2", "h9g7", "e2e6")
    b = pos.board
    assert attackers(b, sq("e6"), -1) == [sq("g7")]
    assert defenders(b, sq("e6")) == []
    threats = threatened(b, 1)
    assert [t.square for t in threats] == [sq("e6")] and threats[0].hanging
    assert describe(b, sq("e6")) == "炮(e6)"


def test_protected_piece_is_not_hanging_but_cheaper_attacker_counts():
    # 黑车在 e5，红兵在 e4 正面攻击（兵吃车）；黑马（d7）保护着车：不是无根子，但以小捉大仍算危险
    b, _ = board_of("4k4/9/3n5/9/4r4/4P4/9/9/9/4K4 w")
    t = {t.square: t for t in threatened(b, -1)}
    assert sq("e5") in t
    assert t[sq("e5")].defenders == (sq("d7"),) and not t[sq("e5")].hanging
    assert t[sq("e5")].attackers == (sq("e4"),)


def test_cannon_without_screen_does_not_protect():
    # 黑炮在 c5，和 e5 的黑车之间没有炮架：炮不能吃回，车是无根子
    b, _ = board_of("4k4/9/9/9/2c1r4/4P4/9/9/9/4K4 w")
    t = {t.square: t for t in threatened(b, -1)}
    assert t[sq("e5")].hanging


def test_pinned_piece_does_not_attack():
    # 黑马在 e8 被红车（e4）牵制在将前：它不能离开 e 线，所以不算攻击 d6 上的红兵
    b, _ = board_of("4k4/4n4/9/3P5/9/4R4/9/9/9/3K5 b")
    assert attackers(b, sq("d6"), -1) == []
    b[sq("e4")] = 0  # 拿掉红车，马就能吃兵了
    assert attackers(b, sq("d6"), -1) == [sq("e8")]


def test_king_cannot_capture_protected_piece():
    # 红车在 e8 将军，有红炮（e5，隔着 e6 上的兵）保护：黑将不能吃车
    b, _ = board_of("4k4/4R4/9/4P4/4C4/9/9/9/9/3K5 b")
    assert attackers(b, sq("e8"), -1) == []


def test_mating_moves_and_checks():
    # 双车错：a8 的车封住第 8 行，b1 的车沉底将军即杀；车走 e1 只是将军
    b, _ = board_of("4k4/R8/9/9/9/9/9/9/1R7/3K5 w")
    assert mating_moves(b, 1) == [parse_iccs("b1b9")]
    checks = checking_moves(b, 1)
    assert parse_iccs("b1b9") in checks and parse_iccs("b1e1") in checks


def test_material_and_phase():
    pos = Position.start()
    assert material(pos.board, 1) == material(pos.board, -1)
    assert material_text(pos.board, 1) == "车2 马2 炮2 仕2 相2 兵5"
    assert game_phase(pos) == "开局"
    endgame = Position.from_fen("3k5/9/9/9/9/9/9/9/4R4/4K4 w - - 0 50")
    assert game_phase(endgame) == "残局"


def test_crossed_pawn_value():
    assert piece_value(7, sq("e3")) == 1.0  # 红兵没过河
    assert piece_value(7, sq("e5")) == 2.0  # 过河兵
    assert piece_value(-7, sq("e4")) == 2.0  # 黑卒过河
