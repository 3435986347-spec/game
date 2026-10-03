import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig


@pytest.fixture
def client():
    return TestClient(create_app(AppConfig(static_dir=None)))


def new_game(client, fen=None):
    resp = client.post("/api/games", json={"fen": fen})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_new_game(client):
    game = new_game(client)
    pos = game["position"]
    assert pos["turn"] == "red"
    assert len(pos["board"]) == 90 and pos["board"][4] == "K" and pos["board"][85] == "k"
    assert len(pos["legal_moves"]) == 44
    assert game["moves"] == [] and pos["result"] is None


def test_play_iccs_and_chinese_then_undo(client):
    gid = new_game(client)["id"]
    game = client.post(f"/api/games/{gid}/moves", json={"move": "h2e2"}).json()
    game = client.post(f"/api/games/{gid}/moves", json={"move": "马8进7"}).json()
    assert [m["cn"] for m in game["moves"]] == ["炮二平五", "马8进7"]
    assert game["position"]["last_move"] == "h9g7"
    assert game["position"]["turn"] == "red"

    game = client.post(f"/api/games/{gid}/undo").json()
    assert [m["iccs"] for m in game["moves"]] == ["h2e2"]
    assert client.get(f"/api/games/{gid}").json() == game


def test_illegal_move_rejected(client):
    gid = new_game(client)["id"]
    resp = client.post(f"/api/games/{gid}/moves", json={"move": "h2h7"})
    assert resp.status_code == 400
    assert client.get(f"/api/games/{gid}").json()["moves"] == []


def test_undo_at_start_rejected(client):
    gid = new_game(client)["id"]
    assert client.post(f"/api/games/{gid}/undo").status_code == 400


def test_bad_fen_rejected(client):
    resp = client.post("/api/games", json={"fen": "3k5/9/9/9/9/9/9/9/9/K8 w"})
    assert resp.status_code == 400 and "九宫" in resp.json()["detail"]


def test_finished_game(client):
    game = new_game(client, "R3k4/R8/9/9/9/9/9/9/9/3K5 b")
    pos = game["position"]
    assert pos["result"] == {"winner": "red", "reason": "checkmate", "text": "红方胜（将死）"}
    assert pos["legal_moves"] == [] and pos["in_check"] is True
    resp = client.post(f"/api/games/{game['id']}/moves", json={"move": "e9e8"})
    assert resp.status_code == 400


def test_unknown_game(client):
    assert client.get("/api/games/nope").status_code == 404


def test_root_explains_how_to_build_frontend(client):
    resp = client.get("/")
    assert resp.status_code == 200 and "npm run build" in resp.text
