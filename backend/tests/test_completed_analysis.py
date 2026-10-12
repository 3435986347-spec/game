"""只有完成 stop/bestmove 握手的持续搜索，才能复用成有时间预算的搜索。"""

import asyncio
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from conftest import fake_engine_command

from xiangqi.api.analysis import _cache_completed, _Target
from xiangqi.core import START_FEN, Position
from xiangqi.engine import AnalysisCache, AnalysisResult, InfoLine, Limit, UciEngine
from xiangqi.engine.uci import CompletedSearch, _merge


def test_infinite_snapshot_becomes_completed_only_after_bestmove():
    async def scenario():
        engine = UciEngine(fake_engine_command())
        await engine.start()
        try:
            async with aclosing(engine.analyse_infinite(START_FEN, multipv=2)) as generator:
                async for lines in generator:
                    assert engine.completed_search is None
                    if len(lines) == 2:
                        break
            completed = engine.completed_search
            assert completed is not None
            assert (completed.initial_fen, completed.moves, completed.multipv) == (START_FEN, (), 2)
            assert completed.result.bestmove == completed.result.lines[0].move
            assert all(line.time_ms is not None for line in completed.result.lines)
            await engine.analyse(START_FEN, limit=Limit(nodes=10))
            assert engine.completed_search is None  # 下一搜索不能沿用上一局面的完成标记。
        finally:
            await engine.close()

    asyncio.run(scenario())


def completed_engine(*, history=(), times=(1600, 1400), bestmove="a0a1", bound=None):
    lines = [
        InfoLine(1, 32, ["a0a1"], score_cp=20, time_ms=times[0], bound=bound),
        InfoLine(2, 31, ["h2e2"], score_cp=10, time_ms=times[1]),
    ]
    return SimpleNamespace(completed_search=CompletedSearch(
        START_FEN, history, 2, AnalysisResult(lines, bestmove)
    ))


def test_completed_cache_uses_real_minimum_candidate_time_and_preserves_depth():
    cache = AnalysisCache()
    target = _Target(None, START_FEN, [], Position.start())
    _cache_completed(cache, target, 2, completed_engine())
    assert cache.get(START_FEN, [], 2, 1500) is None
    result = cache.get(START_FEN, [], 2, 1400)
    assert result.lines[0].depth == 32 and result.bestmove == "a0a1"


@pytest.mark.parametrize("changes", [
    {"history": ("h0g2",)},
    {"times": (None, 1400)},
    {"times": (0, 1400)},
    {"bestmove": "h2e2"},
    {"bound": "lower"},
])
def test_unfinished_unknown_time_or_mismatched_search_cannot_supply_finite_cache(changes):
    cache = AnalysisCache()
    target = _Target(None, START_FEN, [], Position.start())
    _cache_completed(cache, target, 2, completed_engine(**changes))
    assert cache.get(START_FEN, [], 1, 1) is None


def test_live_snapshot_never_supplies_finite_budget():
    cache = AnalysisCache()
    engine = completed_engine()
    result = engine.completed_search.result
    cache.put(START_FEN, [], 2, None, result)
    assert cache.get(START_FEN, [], 2, None) is not None
    assert cache.get(START_FEN, [], 1, 1000) is None
    engine.completed_search = None
    _cache_completed(cache, _Target(None, START_FEN, [], Position.start()), 2, engine)
    assert cache.get(START_FEN, [], 1, 1000) is None


@pytest.mark.parametrize("rank", [1, 2])
@pytest.mark.parametrize("bound", ["lower", "upper"])
def test_final_bound_cannot_promote_old_exact_score_to_larger_budget(rank, bound):
    engine = UciEngine(["unused"])
    lines = {
        1: InfoLine(1, 20, ["a0a1"], score_cp=20, time_ms=6000),
        2: InfoLine(2, 20, ["h2e2"], score_cp=10, time_ms=6000),
    }
    move = lines[rank].move
    lines[rank] = InfoLine(rank, 8, [move], score_cp=10, time_ms=100)
    latest = dict(lines)
    info = InfoLine(rank, 22, [move], score_cp=400, bound=bound, time_ms=6000)
    _merge(lines, info)
    latest[rank] = info
    # 实时面板仍展示已确认的评分与本次搜索累计进度。
    assert lines[rank].bound is None and lines[rank].time_ms == 6000
    assert lines[rank].depth == 8

    result = engine._analysis_result(lines, latest, "a0a1")
    assert result.lines[rank - 1].bound == bound
    engine.completed_search = CompletedSearch(START_FEN, (), 2, result)
    cache = AnalysisCache()
    _cache_completed(cache, _Target(None, START_FEN, [], Position.start()), 2, engine)
    assert cache.get(START_FEN, [], rank, 6000) is None
    if rank == 2:
        # 第一名确实已完成 6 秒的精确搜索，仍可单独复用。
        assert cache.get(START_FEN, [], 1, 6000) is not None


def test_second_cancel_during_infinite_stop_kills_unsynchronized_engine(monkeypatch):
    async def scenario():
        engine = UciEngine(["unused"])
        engine._proc = SimpleNamespace(poll=lambda: None)
        reading = asyncio.Event()
        draining = asyncio.Event()
        killed = []

        async def prepare(*args):
            pass

        async def read(timeout):
            reading.set()
            await asyncio.Event().wait()

        async def collect(*args):
            draining.set()
            await asyncio.Event().wait()

        async def kill():
            killed.append(True)
            engine._proc = None
            engine.completed_search = None

        monkeypatch.setattr(engine, "_prepare", prepare)
        monkeypatch.setattr(engine, "_send", lambda message: None)
        monkeypatch.setattr(engine, "_readline", read)
        monkeypatch.setattr(engine, "_collect_until_bestmove", collect)
        monkeypatch.setattr(engine, "_kill", kill)

        async def consume():
            async with aclosing(engine.analyse_infinite(START_FEN)) as generator:
                async for _ in generator:
                    pass

        task = asyncio.create_task(consume())
        await reading.wait()
        task.cancel()
        await draining.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert killed == [True] and engine.completed_search is None

    asyncio.run(scenario())
