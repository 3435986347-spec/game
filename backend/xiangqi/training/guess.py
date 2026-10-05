"""猜着练习（docs 5.4 节）：跟着大师对局一步步猜下一着，引擎打分。

| 情况 | 得分 |
|---|---|
| 和大师着法相同 | 3 |
| 不同，但引擎认为不差于大师着法 | 3（「你找到了同样好的着法」） |
| 比大师着法差 ≤ 3% 期望得分 | 2 |
| 差 3–10% | 1 |
| 差 > 10% | 0，附讲解 |

大师着法比引擎最佳差 10% 以上时标注「此处大师着法也非最佳」；
你走出更好的着法不扣分（已含在上表里）。
一盘结束后，失分最多的 3 步（得分不超过 1）加入错题本。
"""

from __future__ import annotations

from ..core import Position

MASTER_NOT_BEST = 0.10
WORST_TO_CARDS = 3


def guess_points(user_score: float, master_score: float, *, same: bool) -> int:
    if same:
        return 3
    loss = master_score - user_score
    if loss <= 0:
        return 3
    if loss <= 0.03:
        return 2
    if loss <= 0.10:
        return 1
    return 0


def side_to_move_at(initial_fen: str, ply: int) -> str:
    """走了 ply 步之后轮到哪一方（red / black）。"""
    first_red = Position.from_fen(initial_fen, validate=False).turn == 1
    red = first_red if ply % 2 == 0 else not first_red
    return "red" if red else "black"


def first_turn(initial_fen: str, moves: list[str], side: str, start: int) -> int | None:
    """从第 start 步（走了 start 步之后的局面）起，第一个轮到 side 走、并且还有下一步可猜的局面。"""
    for ply in range(max(start, 0), len(moves)):
        if side_to_move_at(initial_fen, ply) == side:
            return ply
    return None


def worst_answers(answers: list[dict]) -> list[dict]:
    """失分最多的几步（得分不超过 1；放弃的按最差算）。"""
    bad = [a for a in answers if a["points"] <= 1]
    bad.sort(key=lambda a: (a["points"], -(a["loss"] if a["loss"] is not None else 1.0)))
    return bad[:WORST_TO_CARDS]
