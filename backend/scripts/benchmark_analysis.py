"""本机分析效率实测；不调用大模型、不改棋谱库，结果保存在 data/analysis-performance/。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import aclosing
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from xiangqi.api.analysis import _cache_completed, _Target  # noqa: E402
from xiangqi.api.train import _explain  # noqa: E402
from xiangqi.config import load_config  # noqa: E402
from xiangqi.core import Position, game_result, parse_iccs  # noqa: E402
from xiangqi.engine import AnalysisCache, EngineService, Limit  # noqa: E402
from xiangqi.openings import TRAP_LINES  # noqa: E402


class RecordedEngine:
    def __init__(self, engine):
        self.engine = engine
        self.calls = []

    async def analyse(self, *args, **kwargs):
        started = time.perf_counter()
        result = await self.engine.analyse(*args, **kwargs)
        self.calls.append({
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "depth": result.lines[0].depth if result.lines else None,
            "bestmove": result.bestmove,
        })
        return result


class LocalExplanation:
    level = "入门"

    async def explain(self, context):
        return SimpleNamespace(to_dict=lambda: {
            "grade": context.grade, "win_before": context.win_before,
            "win_after": context.win_after,
        })


async def compare_training(config, fen):
    measurements = {}
    move = None
    for optimized in (False, True):
        service = EngineService(config.engine)
        try:
            engine = RecordedEngine(await service.reviewer())
            started = time.perf_counter()
            if not optimized:
                before = await engine.analyse(
                    fen, limit=Limit(movetime_ms=config.review_movetime_ms), multipv=3,
                )
                move = before.lines[0].move
                after = Position.from_fen(fen)
                after.push(parse_iccs(move))
                if game_result(after, config.rules) is None:
                    await engine.analyse(
                        fen, [move], limit=Limit(movetime_ms=config.review_movetime_ms), multipv=1,
                    )
            else:
                async def reviewer(engine=engine):
                    return engine

                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                    config=config, engines=SimpleNamespace(reviewer=reviewer),
                    analysis_cache=AnalysisCache(), explainer=LocalExplanation(),
                )))
                await _explain(request, fen, move, judge_grade=True)
            measurements["optimized" if optimized else "previous_two_searches"] = {
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "searches": len(engine.calls), "calls": engine.calls,
            }
        finally:
            await service.close()
    return {"fen": fen, "move": move, **measurements}


async def compare_duplicates(config):
    results = {}
    for shared in (False, True):
        service = EngineService(config.engine)
        try:
            engine = RecordedEngine(await service.reviewer())
            cache = AnalysisCache(max_entries=0)
            limit = Limit(movetime_ms=config.review_movetime_ms)
            started = time.perf_counter()
            await asyncio.gather(*[
                cache.get_or_analyse(engine, Position.start().fen(), limit=limit, multipv=3)
                if shared else engine.analyse(Position.start().fen(), limit=limit, multipv=3)
                for _ in range(3)
            ])
            results["shared" if shared else "independent"] = {
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "searches": len(engine.calls),
            }
        finally:
            await service.close()
    return results


async def compare_completed(config, duration_ms):
    service = EngineService(config.engine)
    fen = Position.start().fen()
    try:
        engine = await service.reviewer()
        started = time.perf_counter()
        cold = await engine.analyse(
            fen, limit=Limit(movetime_ms=config.review_movetime_ms), multipv=3,
        )
        cold_ms = (time.perf_counter() - started) * 1000
        research_started = time.perf_counter()
        async with aclosing(engine.analyse_infinite(fen, multipv=3)) as generator:
            async with asyncio.timeout(duration_ms / 1000 + 20):
                async for lines in generator:
                    if len(lines) == 3 and all(
                        line.time_ms is not None and line.time_ms >= duration_ms for line in lines
                    ):
                        break
        research_elapsed_ms = (time.perf_counter() - research_started) * 1000
        cache = AnalysisCache()
        _cache_completed(cache, _Target(None, fen, [], Position.start()), 3, engine)
        completed_cached = cache.get(fen, [], 3, config.review_movetime_ms) is not None
        recorded = RecordedEngine(engine)
        started = time.perf_counter()
        reused = await cache.get_or_analyse(
            recorded, fen, limit=Limit(movetime_ms=config.review_movetime_ms), multipv=3,
        )
        return {
            "cold_ms": round(cold_ms, 2), "cold_depth": cold.lines[0].depth,
            "research_target_ms": duration_ms,
            "research_elapsed_ms": round(research_elapsed_ms, 2),
            "completed_cached": completed_cached,
            "reuse_extra_searches": len(recorded.calls),
            "reuse_ms": round((time.perf_counter() - started) * 1000, 2),
            "reuse_depth": reused.lines[0].depth,
        }
    finally:
        await service.close()


async def run(args):
    config = load_config(args.config)
    if config.engine is None:
        raise ValueError("先配置引擎")
    report = {
        "threads": config.engine.threads, "hash_mb": config.engine.hash_mb,
        "movetime_ms": config.review_movetime_ms,
        "notice": (
            "单机耗时实测；训练和并发对照各用新进程排除跨对照哈希复用；"
            "完成研究复用有前置搜索成本，不推断Elo变化"
        ),
        "training": [],
    }
    for fen in (Position.start().fen(), TRAP_LINES["hidden-shunpao-shandian"].trap_fen):
        report["training"].append(await compare_training(config, fen))
        print("完成一个训练局面的前后对照", flush=True)
    report["duplicates"] = await compare_duplicates(config)
    print("完成并发请求对照", flush=True)
    report["completed_research"] = await compare_completed(config, args.research_ms)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(REPO / "config.toml"))
    parser.add_argument("--research-ms", type=int, default=6000)
    parser.add_argument("--output", default=str(REPO / "data/analysis-performance/2026-10-12.json"))
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
