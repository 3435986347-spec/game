"""高等级只跳过已经由完整规则证实的立即获胜局面。"""

import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.api.games import _immediate_winning_moves
from xiangqi.config import AppConfig
from xiangqi.core import (
    BLACK,
    RED,
    START_FEN,
    Position,
    RuleConfig,
    game_result,
    move_to_iccs,
    parse_iccs,
)
from xiangqi.engine import AnalysisResult, InfoLine
from xiangqi.openings import TRAP_LINES

MATE_FEN = "4k4/R8/9/9/9/9/9/9/1R7/3K5 w - - 0 1"


def legal(position):
    return {move_to_iccs(move) for move in position.legal_moves()}


def mate_position(side):
    position = Position.from_fen(MATE_FEN)
    if side == BLACK:
        return Position([-piece for piece in reversed(position.board)], BLACK)
    return position


@pytest.mark.parametrize("side", [RED, BLACK])
@pytest.mark.parametrize("style", ["standard", "varied"])
@pytest.mark.parametrize("level", [8, 9, 10])
def test_immediate_win_uses_rules_without_starting_engine(side, style, level):
    position = mate_position(side)
    wins = _immediate_winning_moves(position, RuleConfig(), legal(position))
    assert wins and len(legal(position)) > 1
    with TestClient(create_app(AppConfig(static_dir=None))) as client:
        game = client.post("/api/games", json={
            "mode": "vs_ai", "ai_level": level, "ai_style": style,
            "user_side": "black" if side == RED else "red", "fen": position.fen(),
        }).json()
        response = client.post(f"/api/games/{game['id']}/ai-move")
        assert response.status_code == 200, response.text
        played = response.json()
        assert played["moves"][0]["iccs"] in wins
        assert played["position"]["result"]["winner"] == ("red" if side == RED else "black")
        assert client.app.state.engines.running("player") is None


@pytest.mark.parametrize("style", ["standard", "varied"])
def test_standard_is_stable_and_varied_uses_multiple_proven_wins(style):
    position = mate_position(RED)
    wins = _immediate_winning_moves(position, RuleConfig(), legal(position))
    assert len(wins) > 1
    chosen = set()
    with TestClient(create_app(AppConfig(static_dir=None))) as client:
        for seed in range(20):
            game = client.post("/api/games", json={
                "mode": "vs_ai", "ai_level": 10, "ai_style": style,
                "user_side": "black", "fen": position.fen(),
            }).json()
            client.app.state.games[game["id"]].rng.seed(seed)
            response = client.post(f"/api/games/{game['id']}/ai-move")
            assert response.status_code == 200, response.text
            chosen.add(response.json()["moves"][0]["iccs"])
    assert chosen == ({wins[0]} if style == "standard" else set(wins))


@pytest.mark.parametrize("draw", [False, True])
def test_no_immediate_win_keeps_normal_search_and_draw_is_not_a_win(draw):
    rules = RuleConfig(move_limit=1) if draw else RuleConfig()
    fen = START_FEN.replace("0 1", "1 1") if draw else START_FEN
    position = Position.from_fen(fen)
    assert _immediate_winning_moves(position, rules, legal(position)) == []
    calls = []

    class Engine:
        async def analyse(self, fen, moves, *, limit, multipv):
            calls.append((limit, multipv))
            return AnalysisResult([InfoLine(1, 20, ["h2e2"], score_cp=20)], "h2e2")

    async def player():
        return Engine()

    app = create_app(AppConfig(static_dir=None, rules=rules))
    app.state.engines.player = player
    with TestClient(app) as client:
        game = client.post("/api/games", json={
            "mode": "vs_ai", "ai_level": 10, "ai_style": "varied",
            "user_side": "black", "fen": fen,
        }).json()
        response = client.post(f"/api/games/{game['id']}/ai-move")
        assert response.status_code == 200, response.text
        assert len(calls) == 1 and calls[0][0].movetime_ms == 4000 and calls[0][1] == 3
        if draw:
            assert response.json()["position"]["result"] == {
                "winner": None, "reason": "move_limit", "text": "和棋（自然限着）",
            }


def test_detection_preserves_original_position_and_complete_history():
    position = Position.start()
    for text in TRAP_LINES["qima-shisan"].moves[:-1]:
        position.push(parse_iccs(text))

    def snapshot():
        return (
            position.fen(), position.board[:], position.moves, position.keys, position.key,
            [position.gave_check(ply) for ply in range(position.ply)],
        )

    before = snapshot()
    assert _immediate_winning_moves(position, RuleConfig(), legal(position)) == ["b7e7"]
    assert snapshot() == before
    assert game_result(position) is None


@pytest.mark.parametrize("level", range(1, 8))
def test_teaching_levels_still_use_engine_with_mate_available(level):
    calls = []

    class Engine:
        async def analyse(self, fen, moves, *, limit, multipv):
            calls.append((limit, multipv))
            return AnalysisResult([InfoLine(1, 10, ["b1b2"], score_cp=20)], "b1b2")

    async def player():
        return Engine()

    app = create_app(AppConfig(static_dir=None))
    app.state.engines.player = player
    with TestClient(app) as client:
        game = client.post("/api/games", json={
            "mode": "vs_ai", "ai_level": level, "ai_style": "varied",
            "user_side": "black", "fen": MATE_FEN,
        }).json()
        response = client.post(f"/api/games/{game['id']}/ai-move")
        assert response.status_code == 200, response.text
        assert response.json()["moves"][0]["iccs"] == "b1b2"
        assert response.json()["position"]["result"] is None
        assert len(calls) == 1
