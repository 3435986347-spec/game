"""强棋多变与提示提速实测：固定预算、新进程对照，不调用外部服务或写棋谱库。"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from xiangqi.api import create_app  # noqa: E402
from xiangqi.api.games import (  # noqa: E402
    Game,
    _confirmed_hint,
    _hint_analysis,
    _hint_reply,
    ai_move,
)
from xiangqi.api.schemas import NewGameRequest  # noqa: E402
from xiangqi.config import load_config  # noqa: E402
from xiangqi.core import START_FEN, Position, game_result, move_to_iccs, parse_iccs  # noqa: E402
from xiangqi.engine import (  # noqa: E402
    AnalysisCache,
    EngineService,
    Limit,
    choose_move,
    get_level,
    search_multipv,
)
from xiangqi.openings import TRAP_LINES  # noqa: E402


async def compare_styles(config, name, moves):
    position = Position.start()
    for move in moves:
        position.push(parse_iccs(move))
    legal = {move_to_iccs(move) for move in position.legal_moves()}
    report = {"name": name, "initial_fen": START_FEN, "moves": list(moves), "styles": {}}
    for style in ("standard", "varied"):
        service = EngineService(config.engine)
        try:
            engine = await service.player()
            level = get_level(10)
            started = time.perf_counter()
            result = await engine.analyse(
                START_FEN, moves, limit=level.limit(), multipv=search_multipv(level, style),
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            counts = Counter(
                choose_move(result, level, random.Random(seed), style=style, legal_moves=legal)
                for seed in range(256)
            )
            assert all(move in legal for move in counts)
            report["styles"][style] = {
                "elapsed_ms": round(elapsed_ms, 2), "bestmove": result.bestmove,
                "selection_counts_from_one_result": dict(counts),
                "lines": [{
                    "move": line.move, "depth": line.depth, "score_cp": line.score_cp,
                    "mate": line.mate, "wdl": line.wdl, "bound": line.bound,
                    "time_ms": line.time_ms, "nodes": line.nodes,
                } for line in result.lines],
            }
        finally:
            await service.close()
    return report


class RecordedEngine:
    def __init__(self, engine):
        self.engine, self.calls = engine, 0

    async def analyse(self, *args, **kwargs):
        self.calls += 1
        return await self.engine.analyse(*args, **kwargs)


async def compare_hints(config, cases):
    results = []
    for name, moves in cases:
        service = EngineService(config.engine)
        try:
            engine = RecordedEngine(await service.player())

            async def player(engine=engine):
                return engine

            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                engines=SimpleNamespace(player=player, config=config.engine),
                analysis_cache=AnalysisCache(),
            )))
            game = Game.create(NewGameRequest(moves=list(moves)), config.rules)
            started = time.perf_counter()
            main = await _hint_analysis(request, game)
            main_ms = (time.perf_counter() - started) * 1000
            main_calls = engine.calls
            paired = {"name": name, "main_ms": round(main_ms, 2),
                      "main_searches": main_calls, "confirmed": _confirmed_hint(main),
                      "move": main.bestmove, "paths": {}}
            # 同一份真实主搜索作配对，避免两次独立主搜索的收敛差异掩盖应着复用效果。
            # 不确认评分时两条路径都不能展示应着，保留该样本而不重试挑结果。
            for optimized in (False, True):
                before_calls = engine.calls
                started = time.perf_counter()
                reply = None
                if _confirmed_hint(main):
                    if optimized:
                        reply = await _hint_reply(request, game, main.bestmove)
                    else:
                        after = game.position.copy()
                        after.push(parse_iccs(main.bestmove))
                        if game_result(after, config.rules) is None:
                            reply = await engine.analyse(
                                game.initial_fen, [*game.move_list, main.bestmove],
                                limit=Limit(movetime_ms=max(
                                    300, min(config.engine.hint_movetime_ms // 2, 600)
                                )), multipv=1,
                            )
                extra_ms = (time.perf_counter() - started) * 1000
                paired["paths"]["pv_reuse" if optimized else "independent_reply"] = {
                    "total_ms": round(main_ms + extra_ms, 2),
                    "reply_ms": round(extra_ms, 2),
                    "searches": main_calls + engine.calls - before_calls,
                    "has_reply": bool(reply and reply.lines),
                    "reply_confirmed": bool(reply and _confirmed_hint(reply)),
                }
            results.append(paired)
        finally:
            await service.close()
    return results


async def measure_api_shortcuts(config):
    results = []
    for name, options in [
        ("唯一合法应着", {"fen": "4k4/R8/9/9/9/9/9/9/9/3K5 b - - 0 1",
                         "user_side": "red"}),
        ("弃马十三着一步杀", {"moves": list(TRAP_LINES["qima-shisan"].moves[:-1]),
                             "user_side": "black"}),
    ]:
        app = create_app(replace(config, library_db=None, static_dir=None))
        async with app.router.lifespan_context(app):
            game = Game.create(NewGameRequest(
                mode="vs_ai", ai_level=10, ai_style="varied", **options,
            ), config.rules)
            app.state.games[game.id] = game
            started = time.perf_counter()
            view = await ai_move(game.id, SimpleNamespace(app=app))
            results.append({
                "name": name, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "move": view.moves[-1].iccs,
                "engine_started": app.state.engines.running("player") is not None,
                "result": view.position.result.model_dump() if view.position.result else None,
            })
    return results


async def run(args):
    config = load_config(args.config)
    if config.engine is None:
        raise ValueError("先配置本机引擎")
    report = {
        "threads": config.engine.threads, "hash_mb": config.engine.hash_mb,
        "ai_movetime_ms": get_level(10).movetime_ms,
        "notice": (
            "棋风对照各使用新进程；提示配对共用一次主搜索；"
            "选着分布是在一次真实分析上使用256个固定随机种子，"
            "不是256盘对局，不推断Elo；MultiPV3将相同总预算分给多个候选"
        ),
        "positions": [],
    }
    cases = [
        ("标准开局", ()),
        ("隐蔽弃马陷阱点", TRAP_LINES["hidden-shunpao-shandian"].moves[:17]),
        ("弃马十三着最后一步杀", TRAP_LINES["qima-shisan"].moves[:-1]),
    ]
    if args.section in ("all", "styles"):
        for name, moves in cases:
            report["positions"].append(await compare_styles(config, name, moves))
            print(f"完成：{name}", flush=True)
    if args.section in ("all", "hints"):
        report["hints_paired"] = await compare_hints(config, cases)
    if args.section in ("all", "shortcuts"):
        report["api_shortcuts"] = await measure_api_shortcuts(config)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(REPO / "config.toml"))
    parser.add_argument("--section", choices=("all", "styles", "hints", "shortcuts"), default="all")
    parser.add_argument("--output", default=str(REPO / "data/analysis-performance/strength.json"))
    asyncio.run(run(parser.parse_args()))
