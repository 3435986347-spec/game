"""并发复用只省重复搜索，不改变预算、候选或取消语义。"""

import asyncio
from dataclasses import replace

import pytest

from xiangqi.core import START_FEN
from xiangqi.engine import AnalysisCache, AnalysisResult, EngineError, InfoLine, Limit


def result(multipv=1):
    moves = ("h2e2", "b2e2", "h0g2")
    return AnalysisResult(
        [InfoLine(rank, 20, [move], score_cp=30 - rank)
         for rank, move in enumerate(moves[:multipv], start=1)],
        moves[0],
    )


class ControlledEngine:
    def __init__(self, *, cleanup_blocked=False):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        if not cleanup_blocked:
            self.cleanup_release.set()
        self.cancelled = 0
        self.failure = None

    async def analyse(self, fen, moves=(), *, limit, multipv=1, searchmoves=()):
        self.calls.append((fen, tuple(moves), limit, multipv, tuple(searchmoves)))
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            raise
        if self.failure is not None:
            raise self.failure
        return result(multipv)


def test_three_identical_requests_execute_one_search_and_return_independent_snapshots():
    async def run():
        engine = ControlledEngine()
        cache = AnalysisCache()
        calls = [asyncio.create_task(cache.get_or_analyse(
            engine, START_FEN, [], limit=Limit(movetime_ms=1500), multipv=3
        )) for _ in range(3)]
        await engine.started.wait()
        assert len(engine.calls) == 1
        assert len(cache._inflight) == 1
        engine.release.set()
        first, second, third = await asyncio.gather(*calls)
        assert first == second == third == result(3)
        first.lines[0].pv.clear()
        first.engine_messages.append("caller mutation")
        assert second == third == result(3)
        assert not cache._inflight
        assert await cache.get_or_analyse(
            engine, START_FEN, [], limit=Limit(movetime_ms=500), multipv=1
        ) == result(1)
        assert len(engine.calls) == 1

    asyncio.run(run())


def test_cancelling_one_waiter_keeps_shared_search_alive_for_the_other():
    async def run():
        engine = ControlledEngine()
        cache = AnalysisCache()
        first = asyncio.create_task(cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500)
        ))
        second = asyncio.create_task(cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500)
        ))
        await engine.started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert engine.cancelled == 0
        assert not second.done()
        engine.release.set()
        assert await second == result()
        assert len(engine.calls) == 1 and not cache._inflight

    asyncio.run(run())


def test_last_waiter_cancellation_waits_for_engine_cleanup_even_when_cancelled_twice():
    async def run():
        engine = ControlledEngine(cleanup_blocked=True)
        cache = AnalysisCache()
        waiter = asyncio.create_task(cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500)
        ))
        await engine.started.wait()
        waiter.cancel()
        await engine.cleanup_started.wait()
        assert engine.cancelled == 1 and not waiter.done()
        assert not cache._inflight
        waiter.cancel()  # 重复取消不能中断被保护的 stop/bestmove 排空。
        await asyncio.sleep(0)
        assert not waiter.done()
        engine.cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert cache.get(START_FEN, [], 1, 1500) is None
        engine.release.set()
        assert await cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500)
        ) == result()
        assert len(engine.calls) == 2 and not cache._inflight

    asyncio.run(run())


def test_shared_exception_reaches_all_waiters_and_next_request_retries():
    async def run():
        engine = ControlledEngine()
        engine.failure = EngineError("test failure")
        cache = AnalysisCache()
        calls = [asyncio.create_task(cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500)
        )) for _ in range(2)]
        await engine.started.wait()
        engine.release.set()
        failures = await asyncio.gather(*calls, return_exceptions=True)
        assert all(isinstance(failure, EngineError) for failure in failures)
        assert len(engine.calls) == 1 and not cache._inflight
        engine.failure = None
        assert await cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500)
        ) == result()
        assert len(engine.calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("different", ["fen", "history", "multipv", "budget", "engine"])
def test_only_exact_inflight_requests_on_same_engine_are_joined(different):
    async def run():
        engine = ControlledEngine()
        other_engine = ControlledEngine() if different == "engine" else engine
        fen = START_FEN.replace("0 1", "0 2") if different == "fen" else START_FEN
        history = ["h2e2"] if different == "history" else []
        multipv = 3 if different == "multipv" else 1
        budget = 500 if different == "budget" else 1500
        cache = AnalysisCache()
        first = asyncio.create_task(cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500)
        ))
        second = asyncio.create_task(cache.get_or_analyse(
            other_engine, fen, history, limit=Limit(movetime_ms=budget), multipv=multipv
        ))
        await engine.started.wait()
        await other_engine.started.wait()
        assert len(cache._inflight) == 2
        engine.release.set()
        other_engine.release.set()
        await asyncio.gather(first, second)
        other_calls = len(other_engine.calls) if other_engine is not engine else 0
        assert len(engine.calls) + other_calls == 2
        assert not cache._inflight

    asyncio.run(run())


@pytest.mark.parametrize("limit, searchmoves", [
    (Limit(depth=30), ()),
    (Limit(nodes=50000), ()),
    (Limit(depth=30, movetime_ms=1500), ()),
    (Limit(nodes=50000, movetime_ms=1500), ()),
    (Limit(movetime_ms=1500), ("h2e2",)),
])
def test_non_time_and_restricted_searches_never_use_or_update_shared_time_cache(
    limit, searchmoves
):
    async def run():
        cache = AnalysisCache()
        cached = result()
        cached.lines[0] = replace(cached.lines[0], depth=40)
        cache.put(START_FEN, [], 1, 1500, cached)
        engine = ControlledEngine()
        engine.release.set()
        first, second = await asyncio.gather(*(
            cache.get_or_analyse(
                engine, START_FEN, limit=limit, searchmoves=searchmoves
            ) for _ in range(2)
        ))
        assert first == second == result()
        assert len(engine.calls) == 2 and not cache._inflight
        assert cache.get(START_FEN, [], 1, 1500) == cached

    asyncio.run(run())


def test_mutable_history_is_frozen_before_search_task_starts():
    async def run():
        engine = ControlledEngine()
        cache = AnalysisCache()
        history = ["h0g2"]
        call = asyncio.create_task(cache.get_or_analyse(
            engine, START_FEN, history, limit=Limit(movetime_ms=1500)
        ))
        await engine.started.wait()
        history.append("h9g7")
        engine.release.set()
        await call
        assert engine.calls[0][1] == ("h0g2",)
        assert cache.get(START_FEN, ["h0g2"], 1, 1500) == result()
        assert cache.get(START_FEN, history, 1, 1500) is None

    asyncio.run(run())


def test_insufficient_candidate_result_is_shared_once_but_does_not_satisfy_later_request():
    class PartialEngine:
        calls = 0

        async def analyse(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            return result()

    async def run():
        engine = PartialEngine()
        cache = AnalysisCache()
        first, second = await asyncio.gather(*(
            cache.get_or_analyse(
                engine, START_FEN, limit=Limit(movetime_ms=1500), multipv=3
            ) for _ in range(2)
        ))
        assert first == second == result() and engine.calls == 1
        assert cache.get(START_FEN, [], 3, 1500) is None
        await cache.get_or_analyse(
            engine, START_FEN, limit=Limit(movetime_ms=1500), multipv=3
        )
        assert engine.calls == 2 and not cache._inflight

    asyncio.run(run())
