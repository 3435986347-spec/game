"""xiangqi-puzzles：从棋谱库自动出题（docs 5.6 节）。

题目来自复盘：每盘复盘过的棋都会自动出题（打谱页点「开始复盘」也一样）。这个命令批量做这件事：
从棋谱库里随机挑还没复盘过的对局，逐盘复盘（结果存库，打谱时直接显示），再从中出题。
每盘大约 1 分钟（按 config.toml 中 [review] movetime_ms），可以随时按 Ctrl+C 停止，已完成的保留。
不调用大模型（关键时刻的讲解在打谱时按需生成）。

用法：cd backend && uv run xiangqi-puzzles [--games 10] [--opening 中炮] [--player 许银川]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from ..engine import EngineService, Limit
from ..library import Library, moves_hash
from .collect import collect_from_review
from .review import review_game, review_rows
from .store import TrainingStore


def pick_games(library: Library, count: int, opening: str | None, player: str | None) -> list[int]:
    where = ["kind = 'library'", "id NOT IN (SELECT game_id FROM reviews)", "ply_count >= 20"]
    params: list[object] = []
    if opening:
        where.append("opening = ?")
        params.append(opening)
    if player:
        where.append("(red LIKE ? OR black LIKE ?)")
        params += [f"%{player}%", f"%{player}%"]
    rows = library.connect().execute(
        f"SELECT id FROM games WHERE {' AND '.join(where)} ORDER BY RANDOM() LIMIT ?",
        [*params, count],
    )
    return [row[0] for row in rows]


async def run(args: argparse.Namespace) -> None:
    from ..config import load_config  # 避免循环导入

    config = load_config(args.config)
    if config.engine is None:
        sys.exit("config.toml 中没有配置 [engine]，出题需要象棋引擎。")
    library = Library(config.library_db, index_plies=config.library_index_plies)
    store = TrainingStore(library)
    engines = EngineService(config.engine)
    limit = Limit(movetime_ms=config.review_movetime_ms)
    game_ids = pick_games(library, args.games, args.opening, args.player)
    if not game_ids:
        sys.exit("没有找到符合条件、还没复盘过的对局。")
    movetime = config.review_movetime_ms
    print(f"复盘并出题：{len(game_ids)} 盘，每个局面 {movetime} 毫秒", file=sys.stderr)
    total_puzzles = 0
    try:
        engine = await engines.reviewer()
        for n, game_id in enumerate(game_ids, 1):
            record = library.game_moves(game_id)
            if record is None:
                continue
            started = time.monotonic()
            evals, grades, summary = await review_game(
                engine, record, limit=limit, rules=config.rules
            )
            summary["tags"] = []
            library.save_review(
                game_id,
                moves_hash=moves_hash(record["initial_fen"], record["moves"]),
                engine=engine.name,
                movetime_ms=config.review_movetime_ms,
                summary=summary,
                rows=review_rows(evals, grades, {}),
            )
            try:
                added, _ = collect_from_review(store, record, game_id, evals, grades, {})
            except Exception as e:  # 复盘已经存好，出题失败不影响后面的对局
                print(f"[{n}/{len(game_ids)}] 第 {game_id} 盘出题失败：{e}", file=sys.stderr)
                continue
            total_puzzles += added
            title = f"{record.get('red') or '红方'} vs {record.get('black') or '黑方'}"
            print(
                f"[{n}/{len(game_ids)}] {title}：{len(record['moves'])} 步，"
                f"新题 {added} 道，{time.monotonic() - started:.0f} 秒",
                file=sys.stderr,
            )
    finally:
        await engines.close()
        stats = store.puzzle_stats()
        print(f"\n本次新增 {total_puzzles} 道题，题库共 {stats['total']} 道。", file=sys.stderr)
        library.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xiangqi-puzzles", description="从棋谱库自动出题")
    parser.add_argument("--games", type=int, default=10, help="复盘多少盘（默认 10）")
    parser.add_argument("--opening", help="只挑这种开局的对局，如 中炮对屏风马")
    parser.add_argument("--player", help="只挑这位棋手的对局")
    parser.add_argument("--config", help="配置文件路径")
    try:
        asyncio.run(run(parser.parse_args(argv)))
    except KeyboardInterrupt:
        print("\n已停止，完成的对局和题目已保存。", file=sys.stderr)


if __name__ == "__main__":
    main()
