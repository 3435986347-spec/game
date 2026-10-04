import sys
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.engine import EngineConfig


def make_client(tmp_path, *, engine: bool = True) -> TestClient:
    engine_config = None
    if engine:
        engine_config = EngineConfig(
            path=Path(sys.executable), args=(str(FAKE_ENGINE),), hint_movetime_ms=50
        )
    config = AppConfig(
        static_dir=None, engine=engine_config, library_db=tmp_path / "lib.db", review_movetime_ms=30
    )
    return TestClient(create_app(config))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


def play(client, gid: str, *moves: str) -> None:
    for move in moves:
        resp = client.post(f"/api/games/{gid}/moves", json={"move": move})
        assert resp.status_code == 200, resp.text


def test_analyse_each_move_and_cache_positions(client):
    gid = client.post("/api/games", json={"mode": "free"}).json()["id"]
    play(client, gid, "h2e2", "h9g7")
    first = client.post(f"/api/games/{gid}/analysis", json={"ply": 1}).json()
    assert first["ply"] == 1 and first["cn"] == "炮二平五" and first["side"] == "red"
    # 假引擎总是推荐字母序第一的着法（车九进一），对走棋方总是 0.59
    assert first["best_cn"] == "车九进一" and first["grade"] == "失误"
    assert first["win_before"] == 0.59 and first["win_after"] == pytest.approx(0.41)
    assert first["red_win"] == pytest.approx(0.41) and first["explanation"] is None
    second = client.post(f"/api/games/{gid}/analysis", json={"ply": 2}).json()
    assert second["cn"] == "马8进7" and second["side"] == "black"
    game = client.app.state.games[gid]
    assert set(game.position_evals) == {(), ("h2e2",), ("h2e2", "h9g7")}  # 走之后的局面复用

    explained = client.post(f"/api/games/{gid}/analysis/explain", json={"ply": 2}).json()
    assert explained["source"] == "template" and explained["headline"].startswith("马8进7")
    again = client.post(f"/api/games/{gid}/analysis", json={"ply": 2}).json()
    assert again["explanation"] == explained  # 讲解过的步，分析结果里带上讲解


def test_analysis_follows_undo(client):
    gid = client.post("/api/games", json={"mode": "free"}).json()["id"]
    play(client, gid, "h2e2")
    client.post(f"/api/games/{gid}/analysis", json={"ply": 1})
    client.post(f"/api/games/{gid}/undo")
    resp = client.post(f"/api/games/{gid}/analysis", json={"ply": 1})
    assert resp.status_code == 400 and "不存在" in resp.json()["detail"]
    play(client, gid, "b2e2")  # 悔棋后走了别的着法：按新着法分析
    assert client.post(f"/api/games/{gid}/analysis", json={"ply": 1}).json()["cn"] == "炮八平五"


def test_analysis_of_checkmate(client):
    fen = "4k4/R8/9/9/9/9/9/9/1R7/3K5 w - - 0 1"
    gid = client.post("/api/games", json={"mode": "free", "fen": fen}).json()["id"]
    play(client, gid, "b1b9")
    result = client.post(f"/api/games/{gid}/analysis", json={"ply": 1}).json()
    assert result["terminal"] == "红方胜（将死）" and result["red_win"] == 1.0
    assert result["win_after"] == 1.0 and result["drop"] == 0.0


def test_analysis_errors(tmp_path):
    with make_client(tmp_path, engine=False) as c:
        gid = c.post("/api/games", json={"mode": "free"}).json()["id"]
        play(c, gid, "h2e2")
        resp = c.post(f"/api/games/{gid}/analysis", json={"ply": 1})
        assert resp.status_code == 503 and "引擎" in resp.json()["detail"]
        assert c.post("/api/games/nope/analysis", json={"ply": 1}).status_code == 404
        assert c.post(f"/api/games/{gid}/analysis", json={"ply": 0}).status_code == 422
