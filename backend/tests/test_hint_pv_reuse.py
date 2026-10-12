"""三级提示复用同次完整主变；缺少可靠应着时仍按原预算补搜。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.api.games import Game, _hint_reply, _hint_view
from xiangqi.api.schemas import NewGameRequest
from xiangqi.config import AppConfig
from xiangqi.core import START_FEN, RuleConfig
from xiangqi.engine import AnalysisCache, AnalysisResult, InfoLine


def source(pv=("h2e2", "h9g7", "h0g2"), **scores):
    return AnalysisResult(
        [InfoLine(1, 25, list(pv), **(scores or {"score_cp": 60}), time_ms=1500, nodes=50000)],
        pv[0],
    )


def setup(result=None, *, moves=(), rules=None, fen=None):
    game = Game.create(NewGameRequest(moves=list(moves), fen=fen), rules or RuleConfig())
    game._hint_cache = (tuple(game.move_list), result) if result is not None else None
    cache = AnalysisCache()
    calls = []
    reply_move = "h0g2" if moves else "h9g7"
    independent = AnalysisResult([InfoLine(1, 18, [reply_move], score_cp=-20)], reply_move)

    async def analyse(fen, history, *, limit, multipv=1):
        calls.append((fen, tuple(history), limit.movetime_ms, multipv))
        return independent

    async def player():
        return SimpleNamespace(analyse=analyse)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        analysis_cache=cache,
        engines=SimpleNamespace(config=SimpleNamespace(hint_movetime_ms=1500), player=player),
    )))
    return game, cache, request, calls


@pytest.mark.parametrize("black", [False, True])
@pytest.mark.parametrize("scores", [{"score_cp": 60}, {"score_cp": 60, "wdl": (600, 300, 100)}])
def test_reply_flips_player_perspective_without_new_search_or_independent_cache(black, scores):
    pv = ("h9g7", "h0g2", "b9c7") if black else ("h2e2", "h9g7", "h0g2")
    original = source(pv, **scores)
    game, cache, request, calls = setup(original, moves=("h2e2",) if black else ())

    reply = asyncio.run(_hint_reply(request, game, pv[0]))

    assert calls == []
    assert reply.lines[0].pv == list(pv[1:])
    assert reply.lines[0].expected_score() == pytest.approx(1 - original.lines[0].expected_score())
    assert reply.lines[0].score_cp == -60
    assert reply.lines[0].wdl == ((100, 300, 600) if "wdl" in scores else None)
    assert reply.lines[0].time_ms is None and reply.lines[0].nodes is None
    assert cache.get(game.initial_fen, [*game.move_list, pv[0]], 1, 600) is None
    assert game._hint_reply_cache == {}
    view = _hint_view(game.position, original.lines[0], 3, reply=reply)
    expected = original.lines[0].expected_score()
    assert view.red_win == pytest.approx(round(1 - expected if black else expected, 4))


@pytest.mark.parametrize("mate", [1, 5, -1, -5])
def test_mate_result_flips_outcome_without_copying_distance(mate):
    game, _, request, calls = setup(source(mate=mate))
    reply = asyncio.run(_hint_reply(request, game, "h2e2"))
    assert calls == [] and reply.lines[0].mate is None
    assert reply.lines[0].wdl == ((0, 0, 1000) if mate > 0 else (1000, 0, 0))


@pytest.mark.parametrize("case", [
    "missing", "short", "illegal", "malformed", "bound", "history", "move", "unfinished",
])
def test_unusable_source_preserves_original_reply_search_budget(case):
    original = None if case == "missing" else source()
    if case in ("short", "illegal", "malformed", "move"):
        pv = {
            "short": ("h2e2",), "illegal": ("h2e2", "a9a0"),
            "malformed": ("h2e2", "invalid"), "move": ("h0g2", "h9g7"),
        }[case]
        original = source(pv)
    if case == "bound":
        original.lines[0] = replace(original.lines[0], bound="lower")
    if case == "unfinished":
        original.bestmove = None
    game, _, request, calls = setup(original)
    if case == "history":
        game._hint_cache = (("h0g2", "h9g7", "g2h0", "g7h9"), original)

    reply = asyncio.run(_hint_reply(request, game, "h2e2"))

    assert reply.lines[0].depth == 18
    assert calls == [(game.initial_fen, ("h2e2",), 600, 1)]


@pytest.mark.parametrize("bad_tail", ["a0a9", "invalid"])
def test_illegal_later_pv_requires_independent_reply_search(bad_tail):
    game, _, request, calls = setup(source(("h2e2", "h9g7", bad_tail, "b9c7")))
    reply = asyncio.run(_hint_reply(request, game, "h2e2"))
    assert reply.lines[0].pv == ["h9g7"] and reply.lines[0].depth == 18
    assert calls == [(game.initial_fen, ("h2e2",), 600, 1)]


@pytest.mark.parametrize("bad_tail", ["e0e9", "invalid"])
def test_invalid_pv_tail_cannot_turn_failed_cannon_capture_into_sacrifice(bad_tail):
    original = source(("e2e6", "g7e6", bad_tail), wdl=(100, 900, 0))
    game, _, request, calls = setup(original, moves=("h2e2", "h9g7"))

    async def analyse(fen, history, *, limit, multipv):
        calls.append((fen, tuple(history), limit.movetime_ms, multipv))
        # 独立复核发现吃卒丢炮让黑方大优，不能用非法源PV的55%包装为飞刀。
        return AnalysisResult([InfoLine(1, 22, ["g7e6"], score_cp=400)], "g7e6")

    async def player():
        return SimpleNamespace(analyse=analyse)

    request.app.state.engines.player = player
    reply = asyncio.run(_hint_reply(request, game, "e2e6"))
    assert calls == [(game.initial_fen, ("h2e2", "h9g7", "e2e6"), 600, 1)]
    view = _hint_view(game.position, original.lines[0], 3, reply=reply, trap_index={})
    assert view.trap is None
    assert "弃子" not in view.purpose


def test_rule_terminal_precedes_source_and_independent_reply_cache():
    game, _, request, calls = setup(
        source(), rules=RuleConfig(move_limit=1), fen=START_FEN.replace("0 1", "1 1"),
    )
    game._hint_reply_cache[((), "h2e2")] = source(("h9g7",))
    assert asyncio.run(_hint_reply(request, game, "h2e2")) is None
    assert calls == []


def test_rule_terminal_inside_pv_ends_the_returned_variation():
    game, _, request, calls = setup(source(), rules=RuleConfig(move_limit=1))
    reply = asyncio.run(_hint_reply(request, game, "h2e2"))
    assert reply.lines[0].pv == ["h9g7"] and calls == []


def test_source_update_rederives_reply_instead_of_reusing_old_tail():
    game, cache, request, calls = setup(source())
    old = asyncio.run(_hint_reply(request, game, "h2e2"))
    game._hint_reply_cache[((), "h2e2")] = old
    updated = source(("h2e2", "b9c7", "h0g2"), score_cp=100)
    game._hint_cache = ((), updated)

    reply = asyncio.run(_hint_reply(request, game, "h2e2"))

    assert reply.lines[0].pv == ["b9c7", "h0g2"]
    assert reply.lines[0].score_cp == -100 and calls == []
    assert cache.get(game.initial_fen, ["h2e2"], 1, 600) is None


def test_level3_hint_with_complete_pv_searches_once_at_full_configured_budget():
    result = source()
    calls = []

    async def analyse(fen, moves, *, limit, multipv):
        calls.append((tuple(moves), limit.movetime_ms, multipv))
        return result

    async def player():
        return SimpleNamespace(analyse=analyse)

    app = create_app(AppConfig(static_dir=None))
    app.state.engines.player = player
    app.state.engines.config = SimpleNamespace(hint_movetime_ms=1500)
    with TestClient(app) as client:
        gid = client.post("/api/games", json={}).json()["id"]
        assert client.post(f"/api/games/{gid}/hint", json={"level": 2}).status_code == 200
        for _ in range(2):
            response = client.post(f"/api/games/{gid}/hint", json={"level": 3})
            assert response.status_code == 200
            assert "对方最好应以" in response.json()["text"]
        assert calls == [((), 1500, 1)]
