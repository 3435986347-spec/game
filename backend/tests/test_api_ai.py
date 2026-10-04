import sys
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.engine import EngineConfig


def make_client(*flags: str, engine: bool = True, flavor: str = "pikafish") -> TestClient:
    config = AppConfig(static_dir=None)
    if engine:
        engine_config = EngineConfig(
            path=Path(sys.executable),
            args=(str(FAKE_ENGINE), *flags),
            flavor=flavor,
            hint_movetime_ms=50,
        )
        config = AppConfig(static_dir=None, engine=engine_config)
    return TestClient(create_app(config))


@pytest.fixture
def client():
    with make_client() as c:  # with：触发 lifespan，测试结束时关闭引擎进程
        yield c


def new_game(client, **body):
    resp = client.post("/api/games", json={"mode": "vs_ai", **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


def post(client, path, **body):
    return client.post(path, json=body or None)


def test_engine_status(client):
    status = client.get("/api/engine").json()
    assert status["ok"] is True and status["name"] == "FakeEngine 1.0"
    assert [lv["level"] for lv in status["levels"]] == list(range(1, 11))


def test_engine_status_not_configured():
    with make_client(engine=False) as c:
        status = c.get("/api/engine").json()
    assert status["configured"] is False and status["ok"] is False and "未配置" in status["error"]


def test_user_red_flow(client):
    game = new_game(client, user_side="red", ai_level=5)
    gid = game["id"]
    assert game["user_side"] == "red" and game["ai_level"] == 5 and not game["ai_to_move"]

    game = post(client, f"/api/games/{gid}/moves", move="h2e2").json()
    assert game["ai_to_move"] is True
    resp = post(client, f"/api/games/{gid}/moves", move="h9g7")  # 轮到 AI 时不能替它走
    assert resp.status_code == 400 and "AI" in resp.json()["detail"]

    game = post(client, f"/api/games/{gid}/ai-move").json()
    assert len(game["moves"]) == 2 and game["moves"][1]["by_ai"] is True
    assert game["ai_to_move"] is False and game["position"]["turn"] == "red"
    assert post(client, f"/api/games/{gid}/ai-move").status_code == 400  # 不是 AI 的回合

    game = post(client, f"/api/games/{gid}/undo").json()  # 一次撤回 AI 和你各一步
    assert game["moves"] == [] and game["position"]["turn"] == "red"


def test_user_black_ai_moves_first(client):
    game = new_game(client, user_side="black")
    gid = game["id"]
    assert game["ai_to_move"] is True
    assert post(client, f"/api/games/{gid}/hint", level=3).status_code == 400
    game = post(client, f"/api/games/{gid}/ai-move").json()
    assert game["moves"][0]["by_ai"] and game["position"]["turn"] == "black"
    resp = post(client, f"/api/games/{gid}/undo")  # 只有 AI 的第一步，不能悔
    assert resp.status_code == 400


def test_undo_when_ai_has_not_replied(client):
    gid = new_game(client)["id"]
    post(client, f"/api/games/{gid}/moves", move="h2e2")
    game = post(client, f"/api/games/{gid}/undo").json()
    assert game["moves"] == []


def test_hints(client):
    gid = new_game(client)["id"]
    hint2 = post(client, f"/api/games/{gid}/hint", level=2).json()
    assert hint2["level"] == 2 and hint2["move"] is None and len(hint2["from_square"]) == 2
    hint3 = post(client, f"/api/games/{gid}/hint", level=3).json()
    assert hint3["move"].startswith(hint2["from_square"])  # 同一局面用缓存，结论一致
    assert hint3["cn"] == hint3["pv_cn"][0] and 0 <= hint3["red_win"] <= 1
    assert "引擎推荐" in hint3["text"]
    assert client.get(f"/api/games/{gid}").json()["hints_used"] == 2


def test_hint_in_free_mode(client):
    gid = client.post("/api/games", json={}).json()["id"]
    assert post(client, f"/api/games/{gid}/hint", level=3).status_code == 200
    assert post(client, f"/api/games/{gid}/ai-move").status_code == 400  # 自由对弈没有 AI


def test_ai_unavailable_returns_503():
    with make_client(engine=False) as c:
        gid = c.post("/api/games", json={"mode": "vs_ai", "user_side": "black"}).json()["id"]
        resp = c.post(f"/api/games/{gid}/ai-move")
    assert resp.status_code == 503 and "未配置" in resp.json()["detail"]


def test_engine_crash_returns_503_with_reason():
    with make_client("--die-on-go") as c:
        gid = c.post("/api/games", json={"mode": "vs_ai", "user_side": "black"}).json()["id"]
        resp = c.post(f"/api/games/{gid}/ai-move")
    assert resp.status_code == 503 and "pikafish.nnue" in resp.json()["detail"]


def test_invalid_level_rejected(client):
    resp = client.post("/api/games", json={"mode": "vs_ai", "ai_level": 11})
    assert resp.status_code == 422


def test_analysis_websocket(client):
    gid = new_game(client)["id"]
    with client.websocket_connect("/ws/analysis") as ws:
        ws.send_json({"type": "start", "game_id": gid, "multipv": 2})
        info = ws.receive_json()
        assert info["type"] == "info" and info["game_id"] == gid and info["ply"] == 0
        assert info["fen"] == client.get(f"/api/games/{gid}").json()["position"]["fen"]
        line = info["lines"][0]
        assert {"move", "cn", "pv_cn", "red_win", "score_cp", "mate"} <= line.keys()
        ws.send_json({"type": "stop"})
        while (msg := ws.receive_json())["type"] == "info":
            pass  # 停止前可能还有在途的消息
        assert msg["type"] == "stopped"

        ws.send_json({"type": "start", "game_id": "missing"})
        assert ws.receive_json() == {"type": "error", "message": "对局不存在"}


def test_analysis_websocket_without_engine():
    with make_client(engine=False) as c:
        gid = c.post("/api/games", json={}).json()["id"]
        with c.websocket_connect("/ws/analysis") as ws:
            ws.send_json({"type": "start", "game_id": gid})
            msg = ws.receive_json()
    assert msg["type"] == "error" and "未配置" in msg["message"]


# ---- 评审中发现的问题的回归测试 ----


def test_wrong_flavor_gives_503_not_500():
    with make_client(flavor="fairy-stockfish") as c:  # 假引擎实际用 0–9 行号
        gid = c.post("/api/games", json={"mode": "vs_ai", "user_side": "black"}).json()["id"]
        ai = c.post(f"/api/games/{gid}/ai-move")
        free = c.post("/api/games", json={}).json()["id"]
        hint = c.post(f"/api/games/{free}/hint", json={"level": 3})
    assert ai.status_code == 503 and "flavor" in ai.json()["detail"]
    assert hint.status_code == 503 and "flavor" in hint.json()["detail"]


def test_hint_cache_is_keyed_by_history(client):
    gid = client.post("/api/games", json={}).json()["id"]
    post(client, f"/api/games/{gid}/hint", level=3)
    for move in ("h0g2", "h9g7", "g2h0", "g7h9"):  # 回到初始局面，但历史不同
        post(client, f"/api/games/{gid}/moves", move=move)
    post(client, f"/api/games/{gid}/hint", level=3)
    game = client.app.state.games[gid]
    assert game._hint_cache[0] == ("h0g2", "h9g7", "g2h0", "g7h9")


def test_second_page_takes_over_analysis_and_first_is_told(client):
    gid = new_game(client)["id"]
    with client.websocket_connect("/ws/analysis") as first:
        first.send_json({"type": "start", "game_id": gid})
        assert first.receive_json()["type"] == "info"
        with client.websocket_connect("/ws/analysis") as second:
            second.send_json({"type": "start", "game_id": gid})
            while (msg := first.receive_json())["type"] == "info":
                pass
            assert msg["type"] == "error" and "另一个页面" in msg["message"]
            assert second.receive_json()["type"] == "info"


def test_hint_and_analysis_use_red_perspective(client):
    """假引擎总是给走棋方 cp +40、WDL 340/500/160（期望得分 0.59）。"""
    gid = client.post("/api/games", json={}).json()["id"]
    red_to_move = post(client, f"/api/games/{gid}/hint", level=3).json()
    assert red_to_move["red_win"] == pytest.approx(0.59)
    post(client, f"/api/games/{gid}/moves", move="h2e2")  # 轮到黑方
    black_to_move = post(client, f"/api/games/{gid}/hint", level=3).json()
    assert black_to_move["red_win"] == pytest.approx(0.41)
    assert "黑方期望得分约 59%" in black_to_move["text"]
    with client.websocket_connect("/ws/analysis") as ws:
        ws.send_json({"type": "start", "game_id": gid, "multipv": 1})
        line = ws.receive_json()["lines"][0]
    assert line["score_cp"] == -40 and line["red_win"] == pytest.approx(0.41)


def test_level2_then_level3_hint_searches_once(client, monkeypatch):
    from xiangqi.engine import UciEngine

    calls = []
    original = UciEngine.analyse

    async def counting(self, *args, **kwargs):
        calls.append(args)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(UciEngine, "analyse", counting)
    gid = client.post("/api/games", json={}).json()["id"]
    post(client, f"/api/games/{gid}/hint", level=2)
    post(client, f"/api/games/{gid}/hint", level=3)
    assert len(calls) == 1
    post(client, f"/api/games/{gid}/moves", move="h2e2")
    post(client, f"/api/games/{gid}/hint", level=2)
    assert len(calls) == 2  # 局面变了，重新搜索
