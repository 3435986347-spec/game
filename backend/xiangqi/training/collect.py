"""复盘之后自动收集训练材料（docs 5.6、8.2 节）：
- 每盘复盘过的棋（棋谱库或自己的）都从中自动出题；
- 自己的对局里，自己这一方评为失误、漏着的局面加入错题本（正确着法 = 引擎推荐）。
"""

from __future__ import annotations

from .puzzles import extract_puzzles
from .review import MoveGrade, PositionEval, focus_sides, position_after
from .store import TrainingStore

MISTAKE_GRADES = ("失误", "漏着")


def collect_from_review(
    store: TrainingStore,
    record: dict,
    game_id: int,
    evals: list[PositionEval],
    grades: list[MoveGrade],
    explanations: dict[int, dict],
) -> tuple[int, int]:
    """返回（新增的题数，新增的错题数）。会访问数据库，在线程中调用。"""
    fen, moves = record["initial_fen"], record["moves"]
    added_puzzles = store.add_puzzles(extract_puzzles(fen, moves, evals, game_id=game_id))
    added_cards = 0
    if record.get("kind") == "my_game":
        focus = focus_sides(record)
        title = f"{record.get('red') or '红方'} vs {record.get('black') or '黑方'}"
        for g in grades:
            before = evals[g.ply - 1]
            if g.side not in focus or g.grade not in MISTAKE_GRADES or not before.lines:
                continue
            best = before.lines[0]
            if best.move == g.move:
                continue
            pos = position_after(fen, moves, g.ply - 1)
            card = store.add_card(
                "mistake",
                pos.fen(),
                best.move,
                pv=best.pv[1:9],
                played=g.move,
                explanation=explanations.get(g.ply),
                source=f"复盘：{title} 第 {g.ply} 步（{g.grade}）",
                source_game_id=game_id,
                source_ply=g.ply,
            )
            added_cards += card is not None
    return added_puzzles, added_cards
