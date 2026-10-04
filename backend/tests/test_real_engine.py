"""用真实引擎跑完整对局。默认跳过；设置环境变量后运行：

XIANGQI_TEST_ENGINE=/path/to/pikafish uv run pytest tests/test_real_engine.py
（Fairy-Stockfish 另加 XIANGQI_TEST_ENGINE_FLAVOR=fairy-stockfish）
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.core import Position, legal_moves, parse_iccs
from xiangqi.engine import EngineConfig

ENGINE = os.environ.get("XIANGQI_TEST_ENGINE")
FLAVOR = os.environ.get("XIANGQI_TEST_ENGINE_FLAVOR", "pikafish")

pytestmark = pytest.mark.skipif(not ENGINE, reason="未设置 XIANGQI_TEST_ENGINE")


@pytest.fixture
def client():
    engine = EngineConfig(
        path=Path(ENGINE or ""), flavor=FLAVOR, threads=1, hash_mb=16, hint_movetime_ms=50
    )
    with TestClient(create_app(AppConfig(static_dir=None, engine=engine))) as c:
        yield c


def test_full_game_user_follows_hints_against_level_1(client):
    """用户每步都走 3 级提示的推荐着法，对手是 1 级 AI。

    棋局应正常结束（或走满 200 步），并且每一步都合法。
    """
    gid = client.post(
        "/api/games", json={"mode": "vs_ai", "user_side": "red", "ai_level": 1}
    ).json()["id"]
    for _ in range(200):
        game = client.get(f"/api/games/{gid}").json()
        if game["position"]["result"]:
            break
        if game["ai_to_move"]:
            resp = client.post(f"/api/games/{gid}/ai-move")
        else:
            hint = client.post(f"/api/games/{gid}/hint", json={"level": 3})
            assert hint.status_code == 200, hint.text
            resp = client.post(f"/api/games/{gid}/moves", json={"move": hint.json()["move"]})
        assert resp.status_code == 200, resp.text

    game = client.get(f"/api/games/{gid}").json()
    pos = Position.from_fen(game["initial_fen"])
    for record in game["moves"]:
        move = parse_iccs(record["iccs"])
        assert move in legal_moves(pos.board, pos.turn)
        pos.push(move)
    print(f"\n{len(game['moves'])} 步，结果：{game['position']['result']}")


@pytest.mark.parametrize("level", [1, 5, 10])
def test_each_level_produces_legal_move(client, level):
    gid = client.post(
        "/api/games", json={"mode": "vs_ai", "user_side": "black", "ai_level": level}
    ).json()["id"]
    game = client.post(f"/api/games/{gid}/ai-move").json()
    assert game["moves"][0]["by_ai"] is True
