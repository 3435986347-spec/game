import sys
import time
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.api import guess as guess_api
from xiangqi.api import review as review_api
from xiangqi.config import AppConfig
from xiangqi.core import START_FEN
from xiangqi.engine import EngineConfig
from xiangqi.llm import EngineLine
from xiangqi.training import Judgement

PGN = """[Event "测试赛"]
[Red "张三"]
[Black "李四"]
[Result "1-0"]
1. H2-E2 H9-G7 2. E2-E6 G7-E6 3. H0-G2 I9-H9 1-0
""".encode()


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


def import_game(client) -> int:
    resp = client.post(
        "/api/library/import", params={"filename": "t.pgn"}, content=PGN,
        headers={"Content-Type": "application/octet-stream"},
    )  # fmt: skip
    job_id = resp.json()["job_id"]
    for _ in range(100):
        if client.get(f"/api/library/import/{job_id}").json()["done"]:
            break
        time.sleep(0.05)
    return client.get("/api/library/games").json()["items"][0]["id"]


def test_guess_flow(client):
    gid = import_game(client)
    session = client.post("/api/guess", json={"game_id": gid, "side": "red"}).json()
    sid = session["id"]
    assert session["current_ply"] == 0 and session["total_plies"] == 6 and not session["finished"]
    assert session["fen"].startswith("rnbakabnr") and session["moves"] == []

    # 第 1 步猜对
    result = client.post(f"/api/guess/{sid}/answer", json={"move": "炮二平五"}).json()
    answer = result["answer"]
    assert answer["ply"] == 1 and answer["same"] and answer["points"] == 3
    assert answer["master_cn"] == "炮二平五" and answer["loss"] == 0.0
    s = result["session"]
    assert s["current_ply"] == 2 and s["score"] == 3 and s["max_score"] == 3
    assert [m["cn"] for m in s["moves"]] == ["炮二平五", "马8进7"]  # 大师这步和对方的应着

    # 第 3 步猜了别的着法：假引擎按字母序打分，a0a1 排在 e2e6 前面 → 不差于大师
    answer = client.post(f"/api/guess/{sid}/answer", json={"move": "a0a1"}).json()["answer"]
    assert answer["ply"] == 3 and not answer["same"] and answer["as_good"] and answer["points"] == 3
    assert answer["master_cn"] == "炮五进四" and answer["user_cn"] == "车九进一"

    # 第 5 步不会：0 分，猜完；放弃的那步加入错题本
    result = client.post(f"/api/guess/{sid}/answer", json={"move": None}).json()
    assert result["answer"]["points"] == 0 and result["answer"]["loss"] is None
    s = result["session"]
    assert s["finished"] and s["current_ply"] == 6 and s["score"] == 6 and s["max_score"] == 9
    summary = s["summary"]
    assert summary["answered"] == 3 and summary["matched"] == 1 and summary["worst"] == [5]
    assert summary["cards_added"] == 1
    assert client.post(f"/api/guess/{sid}/answer", json={"move": "h0g2"}).status_code == 400

    card = client.get("/api/train/next").json()["card"]
    assert card["kind"] == "mistake" and card["played"] is None and "猜着" in card["source"]
    items = client.get("/api/guess").json()
    assert items[0]["id"] == sid and items[0]["finished"] and items[0]["red"] == "张三"


def test_guess_zero_points_gets_explanation(client, monkeypatch):
    gid = import_game(client)
    sid = client.post("/api/guess", json={"game_id": gid, "side": "red"}).json()["id"]

    async def fake_judge(engine, fen, moves, candidates, *, limit):
        user, master = candidates
        lines = [EngineLine(master, (master,), 0.6), EngineLine("b2e2", ("b2e2",), 0.58)]
        return Judgement(lines, {user: 0.2, master: 0.6})

    monkeypatch.setattr(guess_api, "judge_moves", fake_judge)
    answer = client.post(f"/api/guess/{sid}/answer", json={"move": "i0i1"}).json()["answer"]
    assert answer["points"] == 0 and answer["loss"] == pytest.approx(0.4)
    assert answer["explanation"]["source"] == "template"
    assert "大师走的是炮二平五" in answer["explanation"]["why"] + answer["explanation"]["headline"]


def test_guess_errors(client, tmp_path):
    gid = import_game(client)
    assert client.post("/api/guess", json={"game_id": 999, "side": "red"}).status_code == 404
    resp = client.post("/api/guess", json={"game_id": gid, "side": "red", "skip_plies": 6})
    assert resp.status_code == 400
    sid = client.post("/api/guess", json={"game_id": gid, "side": "black"}).json()["id"]
    assert client.get(f"/api/guess/{sid}").json()["current_ply"] == 1  # 黑方：先看红方走一步
    resp = client.post(f"/api/guess/{sid}/answer", json={"move": "h2e2"})  # 这时是黑方走
    assert resp.status_code == 400
    assert client.delete(f"/api/guess/{sid}").json() == {"deleted": True}
    assert client.get(f"/api/guess/{sid}").status_code == 404


def test_review_cards_from_own_game_and_card_answer(client):
    gid = client.post("/api/games", json={"mode": "free"}).json()["id"]
    for move in ("h2e2", "h9g7"):
        client.post(f"/api/games/{gid}/moves", json={"move": move})
    library_id = client.post(f"/api/games/{gid}/review").json()["library_id"]
    for _ in range(200):
        if client.get(f"/api/library/games/{library_id}/review").json()["status"] == "done":
            break
        time.sleep(0.05)
    # 假引擎下两步都评为失误（不是最佳着法），自由对弈双方都是自己：两步都进错题本
    summary = client.get("/api/train/summary").json()
    assert summary["due"] == 2 and summary["cards"] == 2 and summary["rating"] == 1200
    card = client.get("/api/train/next").json()["card"]
    assert card["played"] == "h2e2" and card["played_cn"] == "炮二平五" and card["turn"] == "red"
    assert "复盘" in card["source"]

    result = client.post(f"/api/train/cards/{card['id']}/answer", json={"move": "车九进一"}).json()
    assert result["correct"] and result["solution"] == "a0a1" and result["interval_days"] == 1.0
    assert result["pv_cn"][0] == "车九进一" and result["due"] == 1

    second = client.get("/api/train/next").json()["card"]
    wrong = client.post(f"/api/train/cards/{second['id']}/answer", json={"move": None}).json()
    assert not wrong["correct"] and wrong["interval_days"] == 0.0
    explained = client.post(f"/api/train/cards/{second['id']}/explain").json()
    assert explained["source"] == "template" and explained["headline"]
    assert client.get("/api/train/next").json() == {"card": None, "due": 0}  # 10 分钟后才再出


def test_manual_cards_and_puzzles_without_engine(tmp_path):
    with make_client(tmp_path, engine=False) as c:
        body = {"fen": START_FEN, "solution": "h2e2", "played": "a3a4", "source": "打谱"}
        first = c.post("/api/train/cards", json=body).json()
        assert first["created"] and first["card_id"]
        assert c.post("/api/train/cards", json=body).json()["created"] is False
        other_spelling = {**body, "solution": "H2-E2", "played": "A3-A4"}
        assert c.post("/api/train/cards", json=other_spelling).json()["created"] is False
        assert c.get("/api/train/due").json() == {"due": 1}
        bad = c.post("/api/train/cards", json={**body, "solution": "h2h8"})
        assert bad.status_code == 400
        items = c.get("/api/train/cards").json()
        assert items[0]["solution_cn"] == "炮二平五" and items[0]["played_cn"] == "兵九进一"

        store = c.app.state.training
        store.add_puzzles(
            [{"fen": START_FEN, "solution": "h2e2", "pv": "h9g7", "tags": ["要着", "开局"],
              "rating": 1300, "source_game_id": None, "source_ply": 0, "master_found": 1}]
        )  # fmt: skip
        nxt = c.get("/api/train/puzzle").json()
        assert nxt["rating"] == 1200 and nxt["puzzle"]["turn"] == "red"
        pid = nxt["puzzle"]["id"]
        # 没有引擎时只认正解：走别的就是做错，加入错题本
        wrong = c.post(f"/api/train/puzzles/{pid}/attempt", json={"move": "b2e2"}).json()
        assert not wrong["correct"] and wrong["card_added"] and wrong["rating_after"] < 1200
        assert wrong["pv_cn"] == ["炮二平五", "马8进7"] and wrong["tags"] == ["要着", "开局"]
        right = c.post(f"/api/train/puzzles/{pid}/attempt", json={"move": "h2e2"}).json()
        assert right["correct"] and right["rating_after"] > right["rating_before"]
        summary = c.get("/api/train/summary").json()
        assert summary["puzzles"] == 1 and summary["due_puzzles"] == 1
        assert c.post(f"/api/train/puzzles/{pid}/explain").status_code == 503  # 讲解要用引擎
        assert c.delete(f"/api/train/cards/{first['card_id']}").json() == {"deleted": True}


def test_review_survives_puzzle_collection_failure(client, monkeypatch):
    def broken(*args):
        raise RuntimeError("出题出错")

    monkeypatch.setattr(review_api, "collect_from_review", broken)
    gid = import_game(client)
    client.post(f"/api/library/games/{gid}/review")
    for _ in range(200):
        view = client.get(f"/api/library/games/{gid}/review").json()
        if view["status"] != "running":
            break
        time.sleep(0.05)
    assert view["status"] == "done", view  # 复盘结果已经存好，出题失败只记日志


def test_explain_mate_in_one_uses_result(client):
    fen = "4k4/R8/9/9/9/9/9/9/1R7/3K5 w - - 0 1"  # 车一进八（b1b9）一步杀
    client.app.state.training.add_puzzles(
        [{"fen": fen, "solution": "b1b9", "pv": "", "tags": ["杀法"], "rating": 1000,
          "source_game_id": None, "source_ply": None, "master_found": None}]
    )  # fmt: skip
    pid = client.get("/api/train/puzzle").json()["puzzle"]["id"]
    explanation = client.post(f"/api/train/puzzles/{pid}/explain").json()
    assert "走完后约100%" in explanation["why"]  # 走完就将死：按结果计分
