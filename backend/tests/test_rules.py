from conftest import play

from xiangqi.core import BLACK, RED, Position, RuleConfig, game_result

PERPETUAL_START = "4k4/R8/9/9/9/9/9/9/9/3K5 w"  # 红车在 a8，黑将 e9，红帅 d0
SHUFFLE_START = "r4k3/9/9/9/9/9/9/9/9/3K4R w"  # 双方各一车来回走，互不将军
PERPETUAL_CYCLE = ("a8a9", "e9e8", "a9a8", "e8e9")  # 红车每步都将军
SHUFFLE_CYCLE = ("i0i1", "a9a8", "i1i0", "a8a9")


def test_start_is_not_over():
    assert game_result(Position.start()) is None


def test_checkmate():
    # 双车错：a9 车将军，a8 车封住 e8；d9 与红帅照面，f9 被 a9 车控制
    result = game_result(Position.from_fen("R3k4/R8/9/9/9/9/9/9/9/3K5 b"))
    assert (result.winner, result.reason, result.text) == (RED, "checkmate", "红方胜（将死）")


def test_stalemate_loses():
    # 黑将没有被将军，但无子可走：困毙判负（和国际象棋的逼和不同）
    result = game_result(Position.from_fen("4k4/9/4P4/9/9/9/9/9/5R3/3K5 b"))
    assert (result.winner, result.reason) == (RED, "stalemate")


def test_perpetual_check_loses():
    pos = play(Position.from_fen(PERPETUAL_START), *PERPETUAL_CYCLE)
    assert game_result(pos) is None  # 同一局面第 2 次出现，还不判
    play(pos, *PERPETUAL_CYCLE)
    result = game_result(pos)
    assert (result.winner, result.reason) == (BLACK, "perpetual_check")
    assert result.text == "黑方胜（红方长将）"


def test_repetition_without_checks_is_draw():
    pos = play(Position.from_fen(SHUFFLE_START), *SHUFFLE_CYCLE, *SHUFFLE_CYCLE)
    result = game_result(pos)
    assert (result.winner, result.reason, result.text) == (None, "repetition", "和棋（重复局面）")


def test_repetition_count_is_configurable():
    pos = play(Position.from_fen(SHUFFLE_START), *SHUFFLE_CYCLE)
    assert game_result(pos, RuleConfig(repetition_count=2)).reason == "repetition"


def test_move_limit():
    pos = play(Position.from_fen(SHUFFLE_START), *SHUFFLE_CYCLE)
    assert game_result(pos, RuleConfig(move_limit=2)).reason == "move_limit"
    assert game_result(pos, RuleConfig(move_limit=0)) is None
