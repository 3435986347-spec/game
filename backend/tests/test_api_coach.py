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
    # 「走之前」局面（MultiPV 3，评级用）和「走之后」局面（MultiPV 1，应着用）分开缓存
    assert set(game.position_evals) == {(), ("h2e2",)}
    assert set(game.reply_evals) == {("h2e2",), ("h2e2", "h9g7")}

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


def test_hint_then_analysis_stays_consistent(client, monkeypatch):
    """提示推荐的着法，走了之后评级应一致：走之前复用提示的搜索（更深），
    不会被更浅的评级搜索误判成失误——同一局面评级不再重复调用引擎。"""
    from xiangqi.engine import UciEngine

    calls = []
    original = UciEngine.analyse

    async def counting(self, *args, **kwargs):
        calls.append(args)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(UciEngine, "analyse", counting)
    gid = client.post("/api/games", json={"mode": "free", "trap_id": "qima-shisan"}).json()["id"]
    # 陷阱点前一步（红方走）：3 级提示 → 局面搜索 + 应着搜索
    hint = client.post(f"/api/games/{gid}/hint", json={"level": 3}).json()
    assert len(calls) == 2
    play(client, gid, hint["move"])
    result = client.post(f"/api/games/{gid}/analysis", json={"ply": 11}).json()
    # 走之前用提示缓存（不加引擎调用），只补「走之后」一次搜索
    assert len(calls) == 3
    assert result["grade"] == "好棋" and result["drop"] == 0.0


def test_trap_move_explanation_mentions_trap(client):
    """走了飞刀剧本里的着法，「讲解这步」会说明它和飞刀的关系。"""
    gid = client.post("/api/games", json={"mode": "free", "trap_id": "qima-shisan"}).json()["id"]
    play(client, gid, "a0a1")  # 车九进一：弃马十三着的诱饵着法
    explanation = client.post(f"/api/games/{gid}/analysis/explain", json={"ply": 11}).json()
    assert "弃马十三着" in explanation["why"] or "弃马十三着" in explanation["headline"]


def test_analysis_reuses_reply_search_when_playing_best(client, monkeypatch):
    """走了上一轮「走之后」评估给出的最佳应着：直接复用那一次搜索，评好棋，不重复搜索。"""
    from xiangqi.engine import UciEngine

    calls = []
    original = UciEngine.analyse

    async def counting(self, *args, **kwargs):
        calls.append(args)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(UciEngine, "analyse", counting)
    gid = client.post("/api/games", json={"mode": "free"}).json()["id"]
    play(client, gid, "h2e2")
    client.post(f"/api/games/{gid}/analysis", json={"ply": 1})
    assert len(calls) == 2  # 走之前（MultiPV 3）+ 走之后（MultiPV 1）
    # 走之后局面的最佳应着（假引擎按字母序取第一）
    game = client.app.state.games[gid]
    best_reply = game.reply_evals[("h2e2",)].lines[0].move
    play(client, gid, best_reply)
    second = client.post(f"/api/games/{gid}/analysis", json={"ply": 2}).json()
    assert len(calls) == 3  # 走之前复用上一轮搜索，只补走之后一次
    assert second["grade"] == "好棋" and second["drop"] == 0.0
