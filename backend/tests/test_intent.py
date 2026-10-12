import pytest
from conftest import play

from xiangqi.core import Position, move_to_iccs, parse_iccs, purpose_of_move
from xiangqi.training import Purpose


def test_capture_and_failed_attack_are_not_called_sacrifice():
    pos = play(Position.start(), "h2e2", "h9g7")
    before = pos.fen(), pos.board[:], pos.keys
    purpose = purpose_of_move(pos, "e2e6", pv=["g7e6"], score_before=0.54, score_after=0.16)
    assert isinstance(purpose, Purpose)
    assert "吃掉" in purpose.text and "卒" in purpose.text
    assert not purpose.sacrifice
    assert (pos.fen(), pos.board, pos.keys) == before


def test_sacrifice_requires_score_evidence_and_net_material_loss():
    pos = play(Position.start(), "h2e2", "h9g7")
    # 同一合法吃回变化：单看棋谱不能断言主动弃子，需要引擎仍认可这步。
    assert not purpose_of_move(pos, "e2e6", pv=["g7e6"]).sacrifice
    assert purpose_of_move(pos, "e2e6", pv=["g7e6"], score_before=0.55, score_after=0.55).sacrifice
    assert not purpose_of_move(
        pos, "e2e6", pv=["g7e6"], score_before=0.1, score_after=0.1
    ).sacrifice
    assert not purpose_of_move(pos, "e2e6", pv=[], score_before=0.55, score_after=0.55).sacrifice


def exchange_case():
    return (
        Position.from_fen("r3k4/9/9/9/pRr6/R8/P8/9/C8/3K5 w - - 0 1"),
        ["a4a5", "a9a5", "b5a5", "c5a5", "a1a5"],
    )


@pytest.mark.parametrize("black", [False, True])
def test_complete_exchange_gain_is_not_sacrifice_from_either_side(black):
    pos, pv = exchange_case()
    if black:
        pos = Position([-p for p in reversed(pos.board)], turn=-pos.turn)
        pv = [move_to_iccs(tuple(89 - s for s in parse_iccs(text))) for text in pv]
    purpose = purpose_of_move(pos, pv[0], pv=pv[1:], score_before=0.6, score_after=0.6)
    assert not purpose.sacrifice
    assert "弃子" not in purpose.text


def test_later_sacrifice_does_not_describe_current_quiet_development():
    purpose = purpose_of_move(
        Position.start(), "h2e2", pv=["h9g7", "e2e6", "g7e6"],
        score_before=0.6, score_after=0.6,
    )
    assert not purpose.sacrifice
    assert "弃子" not in purpose.text


def test_current_move_can_offer_another_piece_by_uncovering_attack():
    pos = Position.from_fen("4k1r2/9/9/9/6P2/9/9/6N2/9/3K5 w - - 0 30")
    purpose = purpose_of_move(
        pos, "g5f5", pv=["g9g2"], score_before=0.6, score_after=0.6,
    )
    assert purpose.sacrifice


def test_unrelated_preexisting_hanging_piece_is_not_current_move_sacrifice():
    pos = Position.from_fen("4k1r2/9/9/9/9/9/2P6/6N2/9/3K5 w - - 0 30")
    purpose = purpose_of_move(
        pos, "c3c4", pv=["g9g2"], score_before=0.6, score_after=0.6,
    )
    assert not purpose.sacrifice


@pytest.mark.parametrize("score", [float("inf"), float("nan"), -0.1, 1.1])
def test_invalid_scores_do_not_support_sacrifice(score):
    pos = play(Position.start(), "h2e2", "h9g7")
    assert not purpose_of_move(
        pos, "e2e6", pv=["g7e6"], score_before=0.55, score_after=score,
    ).sacrifice


@pytest.mark.parametrize(
    "pv",
    [
        ["not-a-move", "g7e6"],
        ["g7e6", "not-a-move"],
        ["g7e6", "e0e9"],
    ],
)
def test_invalid_pv_does_not_support_sacrifice(pv):
    pos = play(Position.start(), "h2e2", "h9g7")
    purpose = purpose_of_move(
        pos,
        "e2e6",
        pv=pv,
        score_before=0.55,
        score_after=0.55,
    )
    assert not purpose.sacrifice


def test_check_and_evasion_have_concrete_explanations():
    pos = Position.from_fen("4k4/9/9/9/9/9/9/9/R8/3K5 w - - 0 30")
    assert "将军" in purpose_of_move(pos, "a1e1").text
    pos.push(parse_iccs("a1e1"))
    assert "解除将军" in purpose_of_move(pos, "e9f9").text


def test_deep_analysis_detects_mate_threat():
    pos = Position.from_fen("4k4/9/R8/9/9/9/9/9/1R7/3K5 w - - 0 30")
    assert "一步杀威胁" in purpose_of_move(pos, "a7a8", deep=True).text


def test_illegal_move_is_rejected_without_changing_position():
    pos = Position.start()
    before = pos.fen()
    with pytest.raises(ValueError):
        purpose_of_move(pos, "a0a9")
    assert pos.fen() == before
