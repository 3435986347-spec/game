"""更深的已完成研究应升级提示，但实时快照、不同历史和浅结果不能覆盖它。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.api.games import Game, _hint_analysis, _hint_reply
from xiangqi.api.schemas import NewGameRequest
from xiangqi.config import AppConfig
from xiangqi.core import RuleConfig
from xiangqi.engine import AnalysisCache, AnalysisResult, EngineError, InfoLine


def analysis(move="h2e2", *, depth=12, nodes=1000, score=20, bound=None):
    return AnalysisResult(
        [InfoLine(1, depth, [move, "h9g7"], score_cp=score, nodes=nodes, bound=bound)], move
    )


def setup(local):
    game = Game.create(NewGameRequest(), RuleConfig())
    game._hint_cache = ((), local)
    cache = AnalysisCache()

    async def player():
        pytest.fail("有足够的已完成缓存时不应再调用引擎")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        analysis_cache=cache,
        engines=SimpleNamespace(config=SimpleNamespace(hint_movetime_ms=1500), player=player),
    )))
    return game, cache, request


def test_deeper_completed_research_replaces_old_hint_without_search():
    game, cache, request = setup(analysis())
    deeper = analysis("h0g2", depth=30)
    cache.put(game.initial_fen, [], 1, 30000, deeper)

    result = asyncio.run(_hint_analysis(request, game))

    assert result == deeper
    assert game._hint_cache == ((), deeper)


def test_updated_hint_invalidates_same_history_reply_even_when_best_move_is_unchanged():
    game, cache, request = setup(analysis())
    stale_reply = analysis("h9g7")
    other_history = (("h2e2",), "h9g7")
    game._hint_reply_cache = {
        ((), "h2e2"): stale_reply,
        ((), "h0g2"): stale_reply,
        other_history: stale_reply,
    }
    deeper = analysis(depth=30, score=80)
    cache.put(game.initial_fen, [], 1, 30000, deeper)

    asyncio.run(_hint_analysis(request, game))

    assert game._hint_reply_cache == {other_history: stale_reply}


@pytest.mark.parametrize("case", ["missing", "shallower", "live", "short_budget", "history"])
def test_local_deep_hint_survives_unusable_or_weaker_global_result(case):
    local = analysis(depth=25)
    game, cache, request = setup(local)
    reply = analysis("h9g7")
    game._hint_reply_cache[((), "h2e2")] = reply
    if case != "missing":
        cache.put(
            game.initial_fen,
            ["h0g2", "h9g7", "g2h0", "g7h9"] if case == "history" else [],
            1,
            None if case == "live" else (1000 if case == "short_budget" else 30000),
            analysis("h0g2", depth=10 if case == "shallower" else 32),
        )

    assert asyncio.run(_hint_analysis(request, game)) == local
    assert game._hint_reply_cache[((), "h2e2")] is reply


def test_equal_depth_uses_shared_cache_node_quality_tiebreak():
    game, cache, request = setup(analysis(depth=25, nodes=1000))
    deeper = analysis("h0g2", depth=25, nodes=3000)
    cache.put(game.initial_fen, [], 1, 30000, deeper)
    assert asyncio.run(_hint_analysis(request, game)) == deeper


def test_equal_quality_keeps_existing_hint_and_reply_stable():
    local = analysis(depth=25)
    game, cache, request = setup(local)
    reply = analysis("h9g7")
    game._hint_reply_cache[((), "h2e2")] = reply
    cache.put(game.initial_fen, [], 1, 30000, analysis("h0g2", depth=25))

    assert asyncio.run(_hint_analysis(request, game)) == local
    assert game._hint_reply_cache[((), "h2e2")] is reply


@pytest.mark.parametrize("bound", ["lower", "upper"])
def test_unconfirmed_local_hint_cannot_override_confirmed_global_result(bound):
    local = analysis(depth=40, bound=bound)
    game, cache, request = setup(local)
    exact = analysis("h0g2", depth=25)
    cache.put(game.initial_fen, [], 1, 1500, exact)
    assert asyncio.run(_hint_analysis(request, game)) == exact


@pytest.mark.parametrize("bound", ["lower", "upper"])
@pytest.mark.parametrize("movetime", [50, 1500])
def test_hint_recovers_once_with_same_history_and_at_least_original_budget(bound, movetime):
    game, cache, request = setup(None)
    game.play("h2e2")
    unconfirmed = analysis("h9g7", depth=40, bound=bound)
    exact = analysis("h9g7", depth=30)
    calls = []

    async def analyse(fen, moves, *, limit, multipv):
        calls.append((fen, tuple(moves), limit.movetime_ms, multipv))
        if len(calls) == 1:
            return unconfirmed
        assert cache.get(fen, moves, 1, movetime) is None
        return exact

    async def player():
        return SimpleNamespace(analyse=analyse)

    request.app.state.engines.player = player
    request.app.state.engines.config.hint_movetime_ms = movetime
    assert asyncio.run(_hint_analysis(request, game)) == exact
    assert calls == [
        (game.initial_fen, ("h2e2",), movetime, 1),
        (game.initial_fen, ("h2e2",), max(300, movetime), 1),
    ]
    assert game._hint_cache == (("h2e2",), exact)
    assert cache.get(game.initial_fen, ["h2e2"], 1, movetime) == exact


@pytest.mark.parametrize("bound", ["lower", "upper"])
def test_hint_returns_final_legal_move_without_caching_unconfirmed_score(bound):
    game, cache, request = setup(None)
    unconfirmed = analysis(depth=40, bound=bound)
    calls = []

    async def analyse(*args, **kwargs):
        calls.append(kwargs["limit"].movetime_ms)
        return unconfirmed

    async def player():
        return SimpleNamespace(analyse=analyse)

    request.app.state.engines.player = player
    result = asyncio.run(_hint_analysis(request, game))
    assert result.lines[0].move == result.bestmove == "h2e2"
    assert result.lines[0].bound == bound
    assert calls == [1500, 1500]
    assert game._hint_cache is None
    assert cache.get(game.initial_fen, [], 1, 1500) is None


@pytest.mark.parametrize("bestmove", [None, "a0a9"])
def test_unconfirmed_hint_without_legal_final_bestmove_is_rejected(bestmove):
    game, cache, request = setup(None)
    unconfirmed = analysis(bound="lower")
    unconfirmed.bestmove = bestmove

    async def analyse(*args, **kwargs):
        return unconfirmed

    async def player():
        return SimpleNamespace(analyse=analyse)

    request.app.state.engines.player = player
    with pytest.raises(EngineError, match="合法的着法"):
        asyncio.run(_hint_analysis(request, game))
    assert game._hint_cache is None
    assert cache.get(game.initial_fen, [], 1, 1500) is None


@pytest.mark.parametrize("matching_line", [False, True])
def test_pending_hint_http_uses_final_bestmove_without_score_reply_or_cache(matching_line):
    unconfirmed = analysis(bound="lower")
    unconfirmed.bestmove = "h0g2"  # 最终着法不同于旧 PV 第一着。
    if matching_line:
        unconfirmed.lines.append(InfoLine(2, 28, ["h0g2"], score_cp=900, bound="upper"))
    calls = []

    async def analyse(fen, moves, *, limit, multipv):
        calls.append((tuple(moves), limit.movetime_ms))
        return unconfirmed

    async def player():
        return SimpleNamespace(analyse=analyse)

    app = create_app(AppConfig(static_dir=None))
    app.state.engines.player = player
    app.state.engines.config = SimpleNamespace(hint_movetime_ms=50)
    with TestClient(app) as client:
        game = client.post("/api/games", json={}).json()
        response = client.post(f"/api/games/{game['id']}/hint", json={"level": 3})
        assert response.status_code == 200
        view = response.json()
        assert view["move"] == "h0g2" and view["from_square"] == "h0"
        assert view["red_win"] is None and view["trap"] is None and view["purpose"] is None
        assert "本次评分尚未确认，可稍后再看评价" in view["text"]
        assert "%" not in view["text"]
        assert view["pv_cn"] == [view["cn"]]
        assert app.state.games[game["id"]]._hint_cache is None
        assert app.state.analysis_cache.get(game["initial_fen"], [], 1, 50) is None
        level2 = client.post(f"/api/games/{game['id']}/hint", json={"level": 2})
        assert level2.status_code == 200 and level2.json()["from_square"] == "h0"
        assert calls == [((), 50), ((), 300), ((), 50), ((), 300)]  # 无额外应着搜索。


@pytest.mark.parametrize("bound", ["lower", "upper"])
def test_unconfirmed_reply_is_discarded_and_not_used_for_explanation(bound):
    game, cache, request = setup(analysis())
    unconfirmed = analysis("h9g7", depth=40, bound=bound)
    game._hint_reply_cache[((), "h2e2")] = unconfirmed
    calls = []

    async def analyse(*args, **kwargs):
        calls.append(kwargs["limit"].movetime_ms)
        return unconfirmed

    async def player():
        return SimpleNamespace(analyse=analyse)

    request.app.state.engines.player = player
    assert asyncio.run(_hint_reply(request, game, "h2e2")) is None
    assert calls == [600]
    assert not game._hint_reply_cache
    assert cache.get(game.initial_fen, ["h2e2"], 1, 600) is None
