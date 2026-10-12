"""共享局面与重复局面不能让提示、讲解或实时分析认错飞刀。"""

import asyncio
import random
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FAKE_ENGINE
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.api import games as games_api
from xiangqi.api.analysis import _info_message, _line_extras, _target
from xiangqi.api.games import Game, _hint_view
from xiangqi.api.schemas import NewGameRequest
from xiangqi.config import AppConfig
from xiangqi.core import RED, Position, RuleConfig, parse_iccs
from xiangqi.engine import EngineConfig, InfoLine, UciEngine
from xiangqi.openings import TRAP_LINES, build_trap_index, trap_advice
from xiangqi.openings.index import match_move, match_position, position_key, replay_trap


def twins():
    """数据只存在于测试中，不把未验证的重复飞刀写进正式库。"""
    base = TRAP_LINES["qima-shisan"]
    return {
        trap_id: replace(base, trap=replace(base.trap, id=trap_id, name=name))
        for trap_id, name in (("first", "第一个测试飞刀"), ("selected", "选中的测试飞刀"))
    }


def test_ambiguous_position_and_move_require_explicit_selection():
    lines = twins()
    index = build_trap_index(lines)
    line = lines["selected"]
    before = Position.from_fen(line.start_fen)
    assert match_position(index, before) is None
    assert trap_advice(before, match_position(index, before)) is None
    assert match_move(index, before, line.moves[line.trap.trap_ply - 1]) is None
    assert match_position(index, before, "selected").trap.id == "selected"
    assert match_move(index, before, "A0-A1", "selected").trap.id == "selected"
    assert match_position(index, before, "missing") is None
    assert match_move(index, before, "a0a1", "missing") is None
    # 单一变例仍能自动识别，旧的二/三参数调用保持有效。
    single = build_trap_index({"first": lines["first"]})
    assert match_position(single, before).trap.id == "first"
    assert match_move(single, before, "a0a1", "missing").trap.id == "first"


def test_actual_move_and_preceding_position_filter_before_preference():
    base = TRAP_LINES["qima-shisan"].trap
    # 换序后双方棋子位置相同，但进入共享局面的最后着法和前局面不同。
    first = replay_trap(
        replace(
            base,
            id="rook",
            trap_ply=5,
            moves=("h2e2", "h9g7", "b0c2", "b9c7", "a0a1", "a9a8"),
            refute=("i9i8",),
        )
    )
    second = replay_trap(
        replace(
            base,
            id="cannon",
            trap_ply=5,
            moves=("b0c2", "h9g7", "a0a1", "b9c7", "h2e2", "a9a8"),
            refute=("i9i8",),
        )
    )
    assert position_key(first.trap_fen) == position_key(second.trap_fen)
    index = build_trap_index({"cannon": second, "rook": first})
    before = Position.from_fen(first.start_fen)
    assert match_move(index, before, "a0a1").trap.id == "rook"
    # 选择只是打破剩余歧义，不能盖过实际着法证明属于另一变例的事实。
    assert match_move(index, before, "a0a1", "cannon").trap.id == "rook"
    after = Position.from_fen(first.trap_fen)
    assert match_position(index, after) is None
    assert match_position(index, after, "cannon").trap.id == "cannon"
    assert before.fen() == first.start_fen  # 匹配没有修改调用者局面
    assert match_move(index, before, "not-a-move") is None
    assert match_move(index, before, "a0a9") is None


def test_repeated_fen_uses_current_decision_and_real_move_history():
    base = TRAP_LINES["qima-shisan"]
    line = replay_trap(
        replace(
            base.trap,
            id="repeated",
            moves=(*base.moves[:11], "f9e8", "a1a0", "e8f9", "a0a1", *base.moves[11:]),
            refute=("h3h4",),
        )
    )
    assert position_key(line.fens[11]) == position_key(line.fens[15])
    assert position_key(line.fens[10]) == position_key(line.fens[14])
    # 倒转索引顺序也不改变选择：裸 FEN 要显示当前的弃子决策，而非后面的普通续着。
    index = {
        key: tuple(reversed(matches))
        for key, matches in build_trap_index({line.trap.id: line}).items()
    }
    naked = Position.from_fen(line.trap_fen)
    assert match_position(index, naked).ply == 11
    assert "诱饵" in trap_advice(naked, match_position(index, naked)).advice
    pos = Position.start()
    for text in line.moves[:14]:
        pos.push(parse_iccs(text))
    assert match_position(index, pos).ply == 14
    assert match_move(index, pos, "a0a1").ply == 15
    pos.push(parse_iccs("a0a1"))
    assert match_position(index, pos).ply == 15
    # 实际炮吃马只能是第 16 半回合，不能被重复局面早先的补士着法误匹配。
    assert match_move(index, pos, "b7b0").ply == 16


@pytest.mark.parametrize("mode,ai_plays", [("free", None), ("vs_ai", "trap"), ("vs_ai", "victim")])
def test_selected_id_is_resolved_for_random_and_retained_after_deviation(
    monkeypatch, mode, ai_plays
):
    lines = twins()
    monkeypatch.setattr(games_api, "TRAP_LINES", lines)
    monkeypatch.setattr(random.Random, "choice", lambda self, values: lines["selected"])
    game = Game.create(NewGameRequest(mode=mode, trap_id="random", ai_plays=ai_plays), RuleConfig())
    assert game._selected_trap_id == "selected"
    assert game.book_name == lines["selected"].trap.name
    assert "_selected_trap_id" not in game.view().model_dump()
    if mode == "free":
        assert game.position.fen() == lines["selected"].start_fen
    elif ai_plays == "trap":
        assert game.ai_side == RED
        game.play_ai("h2e2")
        game.play("h9g7")
        assert not game.book
    else:
        assert game.ai_side == -RED
        game.play("a0a1")
        assert not game.book
    assert game._selected_trap_id == "selected"


def test_hint_and_stream_extras_share_preferred_selection():
    lines = twins()
    line, index = lines["selected"], build_trap_index(lines)
    pos = Position.from_fen(line.start_fen)
    best = InfoLine(1, 20, ["a0a1"], score_cp=0)
    unselected = _hint_view(pos, best, 3, trap_index=index)
    assert unselected.alert is None
    assert unselected.trap is None or unselected.trap.id == "sacrifice"
    selected = _hint_view(pos, best, 3, trap_index=index, preferred_trap_id="selected")
    assert selected.trap.id == selected.alert.id == "selected"
    extras, alert = _line_extras(pos, [best], index, "selected")
    assert extras[0][1]["id"] == alert["id"] == "selected"
    message = asyncio.run(_info_message(None, 10, pos, [best], index, preferred_trap_id="selected"))
    assert message["lines"][0]["trap"]["id"] == message["trap_alert"]["id"] == "selected"


def test_websocket_target_uses_private_selection_only_for_games(monkeypatch):
    lines = twins()
    monkeypatch.setattr(games_api, "TRAP_LINES", lines)
    game = Game.create(NewGameRequest(trap_id="selected"), RuleConfig())
    app = SimpleNamespace(state=SimpleNamespace(games={game.id: game}))
    target = _target(app, {"game_id": game.id})
    assert target.preferred_trap_id == "selected"
    arbitrary = _target(app, {"fen": game.position.fen(), "preferred_trap_id": "selected"})
    assert arbitrary.preferred_trap_id is None


def test_selected_game_hint_coach_and_websocket_ignore_first_shared_variant(monkeypatch, tmp_path):
    lines = twins()
    monkeypatch.setattr(games_api, "TRAP_LINES", lines)
    config = AppConfig(
        static_dir=None,
        library_db=tmp_path / "library.db",
        review_movetime_ms=30,
        engine=EngineConfig(
            path=Path(sys.executable), args=(str(FAKE_ENGINE),), hint_movetime_ms=30
        ),
    )
    with TestClient(create_app(config)) as client:
        client.app.state.trap_index = build_trap_index(lines)
        game = client.post("/api/games", json={"trap_id": "selected"}).json()
        gid = game["id"]
        hint = client.post(f"/api/games/{gid}/hint", json={"level": 3})
        assert hint.status_code == 200, hint.text
        assert hint.json()["trap"]["id"] == hint.json()["alert"]["id"] == "selected"
        with client.websocket_connect("/ws/analysis") as ws:
            ws.send_json({"type": "start", "game_id": gid})
            info = ws.receive_json()
            assert info["type"] == "info", info
            assert info["trap_alert"]["id"] == "selected"
            assert info["lines"][0]["trap"]["id"] == "selected"
        assert client.post(f"/api/games/{gid}/moves", json={"move": "a0a1"}).status_code == 200
        explanation = client.post(f"/api/games/{gid}/analysis/explain", json={"ply": 11})
        assert explanation.status_code == 200, explanation.text
        assert "选中的测试飞刀" in explanation.json()["why"]
        assert "第一个测试飞刀" not in explanation.json()["why"]


@pytest.mark.parametrize("ai_plays", ["trap", "victim"])
def test_both_ai_roles_switch_to_engine_after_user_deviates(monkeypatch, tmp_path, ai_plays):
    lines = twins()
    monkeypatch.setattr(games_api, "TRAP_LINES", lines)
    searches = []
    original = UciEngine.analyse

    async def analyse(self, *args, **kwargs):
        searches.append(args)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(UciEngine, "analyse", analyse)
    config = AppConfig(
        static_dir=None,
        library_db=tmp_path / "library.db",
        engine=EngineConfig(path=Path(sys.executable), args=(str(FAKE_ENGINE),)),
    )
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/games", json={"mode": "vs_ai", "trap_id": "selected", "ai_plays": ai_plays}
        )
        assert created.status_code == 200, created.text
        gid = created.json()["id"]
        if ai_plays == "trap":
            opening = client.post(f"/api/games/{gid}/ai-move")
            assert opening.status_code == 200, opening.text
            assert opening.json()["moves"][0]["iccs"] == "h2e2"
            assert not searches  # AI 的首着仍是剧本，不提前调用引擎。
            deviation = "h9g7"
        else:
            deviation = "a0a1"
        played = client.post(f"/api/games/{gid}/moves", json={"move": deviation})
        assert played.status_code == 200, played.text
        game = client.app.state.games[gid]
        assert not game.book and game._selected_trap_id == "selected"
        reply = client.post(f"/api/games/{gid}/ai-move")
        assert reply.status_code == 200, reply.text
        assert reply.json()["moves"][-1]["by_ai"]
        assert len(searches) == 1
        assert game._selected_trap_id == "selected"
        assert reply.json()["book_name"] == lines["selected"].trap.name
