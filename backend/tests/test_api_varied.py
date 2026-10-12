"""棋风接入、同预算搜索和强制应着快速路径。"""

import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.engine import AnalysisResult, InfoLine


class RecordingEngine:
    def __init__(self, bestmove="h2e2"):
        self.bestmove = bestmove
        self.calls = []

    async def analyse(self, fen, moves=(), *, limit, multipv):
        self.calls.append((fen, tuple(moves), limit, multipv))
        return AnalysisResult([
            InfoLine(1, 24, ["h2e2"], score_cp=20, wdl=(100, 900, 0)),
            InfoLine(2, 24, ["b2e2"], score_cp=18, wdl=(98, 902, 0)),
            InfoLine(3, 24, ["a0a9"], score_cp=19, wdl=(99, 901, 0)),  # 非法不能被变招选中
        ], self.bestmove)


def test_old_clients_default_to_standard_and_invalid_style_is_rejected():
    with TestClient(create_app(AppConfig(static_dir=None))) as client:
        assert client.post("/api/games", json={}).json()["ai_style"] == "standard"
        assert client.post("/api/games", json={"ai_style": "reckless"}).status_code == 422


@pytest.mark.parametrize("style", ["standard", "varied"])
def test_only_legal_move_does_not_start_engine(style):
    # 黑将只能将5平6；不存在引擎配置也能完成强制应着。
    with TestClient(create_app(AppConfig(static_dir=None))) as client:
        game = client.post("/api/games", json={
            "mode": "vs_ai", "ai_level": 10, "ai_style": style, "user_side": "red",
            "fen": "4k4/R8/9/9/9/9/9/9/9/3K5 b - - 0 1",
        }).json()
        assert game["position"]["legal_moves"] == ["e9f9"]
        response = client.post(f"/api/games/{game['id']}/ai-move")
        assert response.status_code == 200
        assert response.json()["moves"][0]["iccs"] == "e9f9"
        assert client.app.state.engines.running("player") is None


@pytest.mark.parametrize(("style", "multipv"), [("standard", 1), ("varied", 3)])
def test_full_strength_style_uses_same_budget_and_only_legal_moves(style, multipv):
    app = create_app(AppConfig(static_dir=None))
    engine = RecordingEngine()

    async def player():
        return engine

    app.state.engines.player = player
    with TestClient(app) as client:
        game = client.post("/api/games", json={
            "mode": "vs_ai", "ai_level": 10, "ai_style": style, "user_side": "black",
        }).json()
        app.state.games[game["id"]].rng.seed(2)
        response = client.post(f"/api/games/{game['id']}/ai-move")
        assert response.status_code == 200, response.text
        assert response.json()["ai_style"] == style
        assert response.json()["moves"][0]["iccs"] in {"h2e2", "b2e2"}
        assert len(engine.calls) == 1
        assert engine.calls[0][2].movetime_ms == 4000
        assert engine.calls[0][3] == multipv
        if style == "standard":
            assert response.json()["moves"][0]["iccs"] == "h2e2"


def test_illegal_final_bestmove_does_not_change_game():
    app = create_app(AppConfig(static_dir=None))

    async def player():
        return RecordingEngine(bestmove="a0a9")

    app.state.engines.player = player
    with TestClient(app) as client:
        game = client.post("/api/games", json={
            "mode": "vs_ai", "ai_level": 10, "ai_style": "varied", "user_side": "black",
        }).json()
        response = client.post(f"/api/games/{game['id']}/ai-move")
        assert response.status_code == 503
        assert client.get(f"/api/games/{game['id']}").json()["moves"] == []


@pytest.mark.parametrize("role", ["trap", "victim"])
def test_trap_roles_keep_style_after_leaving_script(role):
    app = create_app(AppConfig(static_dir=None))
    with TestClient(app) as client:
        game = client.post("/api/games", json={
            "mode": "vs_ai", "ai_level": 10, "ai_style": "varied",
            "trap_id": "hidden-shunpao-shandian", "ai_plays": role,
        }).json()
        assert game["ai_style"] == "varied"
        stored = app.state.games[game["id"]]
        stored.book = ()
        assert stored.view().ai_style == "varied"
