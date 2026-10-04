"""L1 方向提示（docs 6.1 节）：只指出方向，不泄露着法。

数据来自规则引擎（被攻击的子、无根子、杀棋威胁）。有引擎最佳着法时，只用它判断「主题」
（这步能不能得子、要不要将军），并过滤掉和最佳着法方向不符的「可以吃子」提示。
"""

from __future__ import annotations

from ..core import Position, parse_iccs
from ..core.features import describe, game_phase, gives_check, mating_moves, threatened

_CALM = {
    "开局": "局面平稳。开局阶段优先出动还没动的车马炮，让它们尽快参加战斗。",
    "中局": "局面平稳。找找对方的弱点：没有保护的子、空虚的九宫，再定下一步的计划。",
    "残局": "残局阶段，想想怎样让兵卒和帅（将）也参加战斗。",
}


def direction_hint(pos: Position, best: str | None = None) -> str:
    """当前走棋方的方向提示（最多两句）。best 为引擎最佳着法（ICCS），没有引擎时为 None。"""
    b, me = pos.board, pos.turn
    if pos.in_check():
        return "你正被将军，先想办法应将：躲开、垫子，或者吃掉将军的子。"

    best_move = parse_iccs(best) if best else None
    best_captures = best_move is not None and b[best_move[1]] != 0
    tips: list[str] = []

    if mating_moves(b, -me):  # 假如这步不管，对方下一步就能将死你
        tips.append("小心：对方有一步杀的威胁，先补强九宫的防守！")
    mine = threatened(b, me)
    if mine:
        t = mine[0]
        who = "、".join(describe(b, a) for a in t.attackers)
        detail = "，而且没有保护" if t.hanging else "（对方用价值更低的子在捉它）"
        tips.append(f"注意：你的{describe(b, t.square)}正被对方{who}攻击{detail}。")

    if mating_moves(b, me):
        tips.append("你有一步杀棋的机会，仔细找一找！")
    elif best_move is None or best_captures:
        targets = threatened(b, -me)
        if targets:
            t = targets[0]
            detail = "没有保护" if t.hanging else "可以以小吃大"
            tips.append(f"对方的{describe(b, t.square)}{detail}，看看能不能吃掉它。")

    if not tips and best_move is not None:
        if best_captures:
            tips.append("这一步有机会得子，看看哪里能吃。")
        elif gives_check(b, best_move):
            tips.append("可以考虑主动将军，逼对方应付。")
    if not tips:
        tips.append(_CALM[game_phase(pos)])
    return " ".join(tips[:2])
