"""生成讲解评测集 xiangqi/llm/eval_set.json（docs 4.6 节）。

开局漏着、中局战术、残局技巧各 10 个局面。

局面来自 Pikafish 低级别自对弈，不用第三方棋谱（见 docs 5.7 节）：
从标准开局、以及几个自己构造的残局局面出发，用 1–4 级难度自己和自己下，再用更长的时间分析每一步，
从中挑出：开局阶段的失误 / 漏着；中局里和吃子、将军有关的失误 / 漏着；残局里的失误或妙着。
每个局面冻结引擎分析（候选着法、对方应着、胜率），评测时只重新计算规则引擎的事实。

需要先在 config.toml 中配置好引擎。用法：
  cd backend && uv run python scripts/make_llm_eval_set.py [--games 10] [--seed 1] [--movetime 300]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

from xiangqi.config import load_config
from xiangqi.core import START_FEN, FenError, Position, game_result, parse_iccs
from xiangqi.core.features import gives_check
from xiangqi.engine import EngineService, Limit, choose_move, get_level
from xiangqi.training import analyse_positions, grade_moves

OUT = Path(__file__).resolve().parents[1] / "xiangqi" / "llm" / "eval_set.json"
PER_CATEGORY = 10
MAX_PER_GAME = 2
MAX_PLIES = 160

# 自己构造的残局起始局面（红先）
ENDGAMES = [
    "2bakab2/9/9/9/9/9/2P6/9/7R1/3K5 w - - 0 1",  # 车兵对士象全
    "4k4/4a4/9/6n2/9/9/2N6/2C6/9/3K5 w - - 0 1",  # 马炮对马士
    "3akab2/9/4b4/9/5n3/9/9/9/9/3RK4 w - - 0 1",  # 车对马士象
    "4k4/4a4/3a5/9/2P6/9/9/9/2C6/3K5 w - - 0 1",  # 炮兵对双士
    "2bakab2/9/9/9/4r4/9/9/9/R7R/4K4 w - - 0 1",  # 双车对车士象全
    "3k1a3/4a4/9/9/3N5/9/2P6/9/9/4K4 w - - 0 1",  # 马兵对双士
]


async def self_play(engine, fen: str, levels: tuple[int, int], rng: random.Random) -> list[str]:
    pos = Position.from_fen(fen)
    moves: list[str] = []
    while len(moves) < MAX_PLIES and game_result(pos) is None:
        level = get_level(levels[0] if pos.turn == 1 else levels[1])
        result = await engine.analyse(fen, moves, limit=level.limit(), multipv=level.multipv)
        move = choose_move(result, level, rng)
        if move is None:
            break
        moves.append(move)
        pos.push(parse_iccs(move))
    return moves


def candidates(game_no: int, fen: str, moves: list[str], evals) -> list[dict]:
    out = []
    pos = Position.from_fen(fen)
    for g in grade_moves(fen, moves, evals):
        move = parse_iccs(g.move)
        before, after = evals[g.ply - 1], evals[g.ply]
        tactical = pos.board[move[1]] != 0 or gives_check(pos.board, move)
        if before.lines:
            best = parse_iccs(before.lines[0].move)
            tactical = tactical or pos.board[best[1]] != 0 or gives_check(pos.board, best)
        category = None
        if g.phase == "开局" and g.grade in ("失误", "漏着"):
            category = "开局漏着"
        elif g.phase == "中局" and g.grade in ("失误", "漏着") and tactical:
            category = "中局战术"
        elif g.phase == "残局" and g.grade in ("妙着", "缓着", "失误", "漏着"):
            category = "残局技巧"
        if category and before.lines and after.lines:
            out.append(
                {
                    "category": category,
                    "game": game_no,
                    "fen": pos.fen(),
                    "move": g.move,
                    "grade": g.grade,
                    "win_before": g.win_before,
                    "win_after": g.win_after,
                    "before": [line.to_dict() for line in before.lines],
                    "after": [line.to_dict() for line in after.lines],
                }
            )
        pos.push(move)
    return out


def pick(pool: list[dict], rng: random.Random) -> list[dict]:
    """每类挑 10 个，同一盘棋最多 2 个；残局技巧里妙着和失误各占一半（不够时互相补）。"""
    chosen: list[dict] = []
    for category in ("开局漏着", "中局战术", "残局技巧"):
        items = [c for c in pool if c["category"] == category]
        rng.shuffle(items)
        if category == "残局技巧":  # 妙着优先排进前一半
            good = [c for c in items if c["grade"] == "妙着"][: PER_CATEGORY // 2]
            items = good + [c for c in items if c not in good]
        per_game: dict[int, int] = {}
        taken = []
        for c in items:
            if per_game.get(c["game"], 0) >= MAX_PER_GAME:
                continue
            per_game[c["game"]] = per_game.get(c["game"], 0) + 1
            taken.append(c)
            if len(taken) == PER_CATEGORY:
                break
        print(f"{category}：候选 {len(items)} 个，选了 {len(taken)} 个", file=sys.stderr)
        for i, c in enumerate(taken, 1):
            c["id"] = f"{category}-{i:02d}"
        chosen += taken
    return chosen


async def main_async(args) -> None:
    config = load_config()
    if config.engine is None:
        sys.exit("config.toml 中没有配置 [engine]")
    engines = EngineService(config.engine)
    rng = random.Random(args.seed)
    pool: list[dict] = []
    starts = [(START_FEN, (1, 3)), (START_FEN, (2, 2)), (START_FEN, (3, 1))] * args.games
    starts = starts[: args.games]
    for fen in ENDGAMES:
        try:
            Position.from_fen(fen)
        except FenError as e:
            print(f"跳过不合法的残局局面 {fen}：{e}", file=sys.stderr)
            continue
        starts += [(fen, (2, 3)), (fen, (4, 2)), (fen, (3, 3))]
    try:
        player = await engines.player()
        reviewer = await engines.reviewer()
        for n, (fen, levels) in enumerate(starts, 1):
            moves = await self_play(player, fen, levels, rng)
            evals = await analyse_positions(
                reviewer, fen, moves, limit=Limit(movetime_ms=args.movetime),
                rules=config.rules, multipv=3,
            )  # fmt: skip
            found = candidates(n, fen, moves, evals)
            pool += found
            print(
                f"第 {n}/{len(starts)} 盘：{len(moves)} 步，候选 {len(found)} 个", file=sys.stderr
            )
    finally:
        await engines.close()
    chosen = pick(pool, rng)
    for c in chosen:
        del c["game"]
    data = {
        "description": "讲解评测集：Pikafish 低级别自对弈中的局面，引擎分析已冻结。"
        "生成脚本：backend/scripts/make_llm_eval_set.py",
        "engine": player.name,
        "movetime_ms": args.movetime,
        "positions": chosen,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已写入 {OUT}（{len(chosen)} 个局面）", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--games", type=int, default=10, help="从标准开局自对弈多少盘")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--movetime", type=int, default=300, help="分析每个局面的时间（毫秒）")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
