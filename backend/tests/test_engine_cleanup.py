"""有限搜索取消/超时后不能把残留的 bestmove 交给下一局面。"""

import asyncio
import sys
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE, fake_engine_command

from xiangqi.core import START_FEN, Position, parse_iccs
from xiangqi.engine import EngineConfig, EngineService, Limit, UciEngine


def shorten_waits(engine, monkeypatch):
    collect = engine._collect_until_bestmove

    async def bounded(lines, timeout, latest=None):
        return await collect(lines, min(timeout, 0.15), latest)

    monkeypatch.setattr(engine, "_collect_until_bestmove", bounded)


@pytest.mark.parametrize("ignore_stop", [False, True])
def test_cancelled_finite_search_drains_or_kills_before_releasing_lock(monkeypatch, ignore_stop):
    async def scenario():
        flags = ("--slow-first-go", "--ignore-stop") if ignore_stop else ("--slow-first-go",)
        engine = UciEngine(fake_engine_command(*flags))
        await engine.start()
        if ignore_stop:
            shorten_waits(engine, monkeypatch)
        emitted = asyncio.Event()
        handle = engine._handle_line

        def observe(raw):
            info = handle(raw)
            if info is not None:
                emitted.set()
            return info

        monkeypatch.setattr(engine, "_handle_line", observe)
        task = asyncio.create_task(engine.analyse(START_FEN, limit=Limit(movetime_ms=5000)))
        try:
            await asyncio.wait_for(emitted.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert not engine._lock.locked()
            if ignore_stop:
                assert not engine.alive
            else:
                assert engine.alive
                # 下一次是黑方局面。若旧红方 bestmove 残留，这里会返回非法的红方着法。
                next_result = await asyncio.wait_for(
                    engine.analyse(START_FEN, ["h2e2"], limit=Limit(nodes=1000)), 3
                )
                position = Position.start()
                position.push(parse_iccs("h2e2"))
                assert position.is_legal(parse_iccs(next_result.bestmove))
        finally:
            if not task.done():
                task.cancel()
            await engine.close()

    asyncio.run(scenario())


def test_timeout_receives_stopped_result_and_engine_can_search_again(monkeypatch):
    async def scenario():
        engine = UciEngine(fake_engine_command("--slow-first-go"))
        await engine.start()
        shorten_waits(engine, monkeypatch)
        try:
            result = await engine.analyse(START_FEN, limit=Limit(nodes=1000))
            assert result.bestmove is not None and engine.alive
            followup = await engine.analyse(START_FEN, ["h2e2"], limit=Limit(nodes=1000))
            position = Position.start()
            position.push(parse_iccs("h2e2"))
            assert position.is_legal(parse_iccs(followup.bestmove))
        finally:
            await engine.close()

    asyncio.run(scenario())


def test_stop_timeout_kills_engine_and_service_restarts_it(monkeypatch):
    async def scenario():
        service = EngineService(
            EngineConfig(
                path=Path(sys.executable),
                args=(str(FAKE_ENGINE), "--slow-first-go", "--ignore-stop"),
            )
        )
        first = await service.player()
        shorten_waits(first, monkeypatch)
        try:
            with pytest.raises(TimeoutError):
                await first.analyse(START_FEN, limit=Limit(nodes=1000))
            assert not first.alive and not first._lock.locked()
            restarted = await service.player()
            assert restarted is not first and restarted.alive
        finally:
            await service.close()

    asyncio.run(scenario())
