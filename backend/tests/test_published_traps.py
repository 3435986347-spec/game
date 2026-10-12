"""逐条验收正式飞刀库的回放、公开接口与人机练习，不启动真实引擎。"""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import fake_engine_command
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.core import RED, Position, game_result, move_to_iccs, parse_iccs
from xiangqi.engine import EngineConfig, UciEngine
from xiangqi.openings import TRAP_LINES


@pytest.fixture(params=tuple(TRAP_LINES), ids=tuple(TRAP_LINES))
def line(request):
    return TRAP_LINES[request.param]


@pytest.fixture
def client():
    command = fake_engine_command()
    config = AppConfig(
        static_dir=None,
        engine=EngineConfig(path=Path(command[0]), args=tuple(command[1:]), hint_movetime_ms=50),
    )
    with TestClient(create_app(config)) as connection:
        yield connection


@pytest.fixture
def searches(monkeypatch):
    calls = []
    original = UciEngine.analyse

    async def analyse(self, *args, **kwargs):
        calls.append((args, kwargs))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(UciEngine, "analyse", analyse)
    return calls


def create_practice(client, line, ai_plays):
    response = client.post("/api/games", json={
        "mode": "vs_ai", "trap_id": line.trap.id, "ai_plays": ai_plays, "ai_level": 1,
    })
    assert response.status_code == 200, response.text
    game = response.json()
    trap_side = "red" if line.trap.side == RED else "black"
    assert game["user_side"] == (
        ("black" if trap_side == "red" else "red") if ai_plays == "trap" else trap_side
    )
    assert game["moves"] == [] and game["book_name"] == line.trap.name
    return game


def scripted_step(client, game, line):
    ply = len(game["moves"])
    if game["ai_to_move"]:
        response = client.post(f"/api/games/{game['id']}/ai-move")
    else:
        response = client.post(f"/api/games/{game['id']}/moves", json={"move": line.moves[ply]})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["moves"][-1]["iccs"] == line.moves[ply]
    assert updated["moves"][-1]["by_ai"] == game["ai_to_move"]
    assert updated["position"]["fen"] == line.fens[ply + 1]
    return updated


def nonterminal_alternative(game, expected):
    for move in game.position.legal_moves():
        text = move_to_iccs(move)
        if text == expected:
            continue
        after = game.position.copy()
        after.push(move)
        if game_result(after, game.rules) is None:
            return text
    raise AssertionError("教学局面应有剧本外、仍可继续对弈的合法着法")


def test_published_main_refute_trap_point_and_public_source(line):
    source = urlsplit(line.trap.source)
    assert source.scheme in {"http", "https"} and source.netloc
    position = Position.start()
    for step, text in zip(line.steps, line.moves, strict=True):
        assert position.is_legal(parse_iccs(text)), (line.trap.id, step.ply, text)
        position.push(parse_iccs(text))
        assert position.fen() == step.fen == line.fens[step.ply]
    before, after = Position.from_fen(line.start_fen), Position.from_fen(line.trap_fen)
    assert before.turn == line.trap.side and after.turn == -line.trap.side
    before.push(parse_iccs(line.moves[line.trap.trap_ply - 1]))
    assert before.fen() == after.fen()
    assert line.refute[0] != line.moves[line.trap.trap_ply]
    for step, text in zip(line.refute_steps, line.refute, strict=True):
        assert after.is_legal(parse_iccs(text)), (line.trap.id, step.ply, text)
        after.push(parse_iccs(text))
        assert after.fen() == step.fen
    assert line.refute_steps[0].ply == line.trap.trap_ply + 1
    result = game_result(after)
    assert result is None or result.winner != line.trap.side


def test_published_list_detail_and_free_preset_support_refute(client, line):
    response = client.get("/api/traps")
    assert response.status_code == 200
    summaries = {item["id"]: item for item in response.json()}
    assert set(summaries) == set(TRAP_LINES)
    assert summaries[line.trap.id]["ply_count"] == len(line.moves)
    assert summaries[line.trap.id]["trap_ply"] == line.trap.trap_ply
    response = client.get(f"/api/traps/{line.trap.id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["source"] == line.trap.source
    assert detail["start_fen"] == line.start_fen and detail["trap_fen"] == line.trap_fen
    assert [step["iccs"] for step in detail["moves"]] == list(line.moves)
    assert [step["iccs"] for step in detail["refute"]] == list(line.refute)
    # 指定飞刀后，调用者的 fen/moves 不可覆盖教学预摆或丢失其历史。
    response = client.post("/api/games", json={
        "trap_id": line.trap.id, "fen": Position.start().fen(), "moves": ["a0a1"],
    })
    assert response.status_code == 200
    game = response.json()
    assert game["position"]["fen"] == line.start_fen
    assert [step["iccs"] for step in game["moves"]] == list(
        line.moves[:line.trap.trap_ply - 1]
    )
    assert game["book_name"] == line.trap.name and game["user_side"] is None
    for text, expected_fen in (
        (line.moves[line.trap.trap_ply - 1], line.trap_fen),
        (line.refute[0], line.refute_steps[0].fen),
    ):
        response = client.post(f"/api/games/{game['id']}/moves", json={"move": text})
        assert response.status_code == 200, response.text
        assert response.json()["position"]["fen"] == expected_fen


@pytest.mark.parametrize("ai_plays", ["trap", "victim"])
def test_each_published_script_plays_in_both_roles_then_ends_or_switches_to_engine(
    client, line, searches, ai_plays
):
    game = create_practice(client, line, ai_plays)
    while len(game["moves"]) < len(line.moves):
        game = scripted_step(client, game, line)
    assert not searches  # 完整教学主变的 AI 着法全部来自实际剧本。
    if game["position"]["result"] is not None:
        response = client.post(f"/api/games/{game['id']}/ai-move")
        assert response.status_code == 400  # 终局无需也不能继续引擎搜索。
        return
    internal = client.app.state.games[game["id"]]
    if not game["ai_to_move"]:
        continuation = nonterminal_alternative(internal, None)
        response = client.post(f"/api/games/{game['id']}/moves", json={"move": continuation})
        assert response.status_code == 200, response.text
    response = client.post(f"/api/games/{game['id']}/ai-move")
    assert response.status_code == 200, response.text
    assert len(searches) == 1 and response.json()["moves"][-1]["by_ai"]


@pytest.mark.parametrize("ai_plays", ["trap", "victim"])
def test_each_published_role_switches_to_engine_after_deviation_at_trap(
    client, line, searches, ai_plays
):
    game = create_practice(client, line, ai_plays)
    while len(game["moves"]) < line.trap.trap_ply or game["ai_to_move"]:
        game = scripted_step(client, game, line)
    assert not searches
    internal = client.app.state.games[game["id"]]
    if ai_plays == "trap":
        deviation = line.refute[0]  # 玩家主动破解诱饵，AI 应放弃受害者主变。
    else:
        deviation = nonterminal_alternative(internal, line.moves[len(game["moves"])])
    response = client.post(f"/api/games/{game['id']}/moves", json={"move": deviation})
    assert response.status_code == 200, response.text
    assert not internal.book and internal._selected_trap_id == line.trap.id
    before = tuple(internal.move_list)
    response = client.post(f"/api/games/{game['id']}/ai-move")
    assert response.status_code == 200, response.text
    assert len(searches) == 1 and tuple(searches[0][0][1]) == before
    assert response.json()["moves"][-1]["by_ai"]
    assert response.json()["book_name"] == line.trap.name
