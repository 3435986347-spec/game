import sys
import time
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.core import START_FEN
from xiangqi.engine import EngineConfig

PGN = """[Event "测试赛"]
[Red "张三"]
[Black "李四"]
[Result "1-0"]
1. H2-E2 H9-G7 2. H0-G2 I9-H9 1-0

[Red "坏棋谱"]
1. H2-E2 H2-E3 *
""".encode("gbk")  # 也顺便检查 GBK 编码的文件

MATE_START = "4k4/R8/9/9/9/9/9/9/9/3K5 w"  # 红车在 a8，可以来回将军


@pytest.fixture
def client(tmp_path):
    engine = EngineConfig(path=Path(sys.executable), args=(str(FAKE_ENGINE),), hint_movetime_ms=50)
    config = AppConfig(static_dir=None, engine=engine, library_db=tmp_path / "lib.db")
    with TestClient(create_app(config)) as c:
        yield c


def import_file(client, data: bytes, filename: str = "测试.pgn") -> dict:
    resp = client.post(
        "/api/library/import",
        params={"filename": filename},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/library/import/{job_id}").json()
        if job["done"]:
            return job
        time.sleep(0.05)
    raise AssertionError("导入没有完成")


def test_import_job_and_search(client):
    job = import_file(client, PGN)
    assert job["filename"] == "测试.pgn" and job["error"] is None
    assert (job["games_seen"], job["imported"], job["duplicates"], job["failed"]) == (2, 1, 0, 1)
    assert job["errors"][0]["index"] == 2 and "第 2 步" in job["errors"][0]["reason"]

    assert client.get("/api/library/stats").json() == {
        "games": 1,
        "my_games": 0,
        "indexed_plies": 40,
    }
    found = client.get("/api/library/games", params={"q": "张三"}).json()
    assert found["total"] == 1 and found["items"][0]["opening"] == "中炮"
    assert found["items"][0]["source"] == "测试.pgn"
    assert client.get("/api/library/openings").json() == [{"name": "中炮", "games": 1}]


def test_empty_upload_rejected(client):
    resp = client.post("/api/library/import", content=b"")
    assert resp.status_code == 400


def test_game_record_and_explorer(client):
    import_file(client, PGN)
    gid = client.get("/api/library/games").json()["items"][0]["id"]
    record = client.get(f"/api/library/games/{gid}").json()
    assert [m["cn"] for m in record["moves"]] == ["炮二平五", "马8进7", "马二进三", "车9平8"]
    assert len(record["fens"]) == 5 and record["fens"][0] == START_FEN
    explorer = client.get("/api/library/explorer", params={"fen": START_FEN}).json()
    assert explorer["games"] == 1
    assert explorer["moves"] == [
        {"move": "h2e2", "cn": "炮二平五", "games": 1, "red_wins": 1, "draws": 0, "black_wins": 0}
    ]
    assert client.get("/api/library/explorer", params={"fen": "bad"}).status_code == 400
    assert client.get("/api/library/games/9999").status_code == 404
    assert client.delete(f"/api/library/games/{gid}").json() == {"deleted": True}
    assert client.delete(f"/api/library/games/{gid}").status_code == 404


def test_new_game_from_library_position(client):
    resp = client.post("/api/games", json={"mode": "free", "moves": ["h2e2", "h9g7"]})
    game = resp.json()
    assert [m["cn"] for m in game["moves"]] == ["炮二平五", "马8进7"]
    assert game["position"]["turn"] == "red" and game["initial_fen"] == START_FEN
    bad = client.post("/api/games", json={"moves": ["h2e2", "h2e3"]})
    assert bad.status_code == 400 and "第 2 步" in bad.json()["detail"]


def test_vs_ai_from_position_ai_moves_when_its_turn(client):
    game = client.post(
        "/api/games", json={"mode": "vs_ai", "user_side": "red", "moves": ["h2e2"]}
    ).json()
    assert game["ai_to_move"] is True  # 走了一步后轮到黑方（AI）


def test_finished_game_is_saved_automatically(client):
    game = client.post("/api/games", json={"fen": MATE_START}).json()
    gid = game["id"]
    assert game["library_id"] is None
    game = client.post(f"/api/games/{gid}/moves", json={"move": "a8a9"}).json()  # 还没结束
    assert game["library_id"] is None
    for move in ("e9e8", "a9a8", "e8e9"):  # 来回将军，局面重复
        game = client.post(f"/api/games/{gid}/moves", json={"move": move}).json()
    assert game["position"]["result"] is None
    saved = client.post(f"/api/games/{gid}/save").json()
    assert saved["library_id"] == client.get(f"/api/games/{gid}").json()["library_id"]
    record = client.get(f"/api/library/games/{saved['library_id']}").json()
    assert record["kind"] == "my_game" and record["result"] == "*" and record["red"] == "我"


def test_checkmate_saves_with_result(client):
    gid = client.post("/api/games", json={"fen": "4k4/R8/1R7/9/9/9/9/9/9/3K5 w"}).json()["id"]
    game = client.post(f"/api/games/{gid}/moves", json={"move": "b7b9"}).json()  # 双车错杀
    assert game["position"]["result"]["reason"] == "checkmate"
    record = client.get(f"/api/library/games/{game['library_id']}").json()
    assert record["result"] == "1-0" and record["kind"] == "my_game"
    mine = client.get("/api/library/games", params={"kind": "my_game"}).json()
    assert mine["total"] == 1


def test_analysis_socket_by_fen_and_moves(client):
    with client.websocket_connect("/ws/analysis") as ws:
        ws.send_json({"type": "start", "fen": START_FEN, "moves": ["h2e2"], "multipv": 1})
        info = ws.receive_json()
        assert info["type"] == "info" and info["game_id"] is None and info["ply"] == 1
        assert info["fen"].startswith(
            "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C2C4/9/RNBAKABNR b"
        )
        ws.send_json({"type": "start", "fen": START_FEN, "moves": ["h2h9x"]})
        while (msg := ws.receive_json())["type"] == "info":
            pass
        assert msg["type"] == "error"


def test_analysis_of_final_position_reports_no_moves(client):
    with client.websocket_connect("/ws/analysis") as ws:
        ws.send_json(
            {"type": "start", "fen": "R3k4/R8/9/9/9/9/9/9/9/3K5 b - - 0 1"}
        )  # 黑方已被将死
        info = ws.receive_json()
    assert info["type"] == "info" and info["lines"] == [] and info["depth"] == 0
