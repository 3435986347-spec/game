"""用规则事实概括着法目的；主要变化和引擎分数用于区分弃子与失误。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .board import CANNON, KING, KNIGHT, PAWN, ROOK, Move, rank_of
from .features import (
    game_phase,
    gives_check,
    mating_moves,
    piece_name,
    piece_value,
    threatened,
)
from .movegen import is_legal_move
from .notation import parse_iccs
from .position import Position


@dataclass(frozen=True)
class Purpose:
    text: str
    sacrifice: bool = False


def _accepted_sacrifice(
    pos: Position, move: Move, after: Position, pv: list[str], newly_offered: set[int]
) -> bool:
    """当前一步的献子须被第一应着接受，并在完整合法变化里仍有净损失。

    通用标注取保守口径：未来另一步送子不算当前步的目的；已经吃回的
    普通兑换也不能因为更远处另一次亏子而重新变成弃子。正式飞刀由棋谱
    匹配单独识别，可以包括临时献子后夺车的战术。
    """
    if not pv:
        return False
    board = list(after.board)
    turn = after.turn
    captured = pos.board[move[1]]
    balance = piece_value(captured, move[1]) if captured else 0.0
    for index, text in enumerate(pv):
        try:
            reply = parse_iccs(text)
        except ValueError:
            return False
        if not is_legal_move(board, turn, reply):
            return False
        frm, to = reply
        target = board[to]
        if index == 0 and (
            target * pos.turn <= 0 or (to != move[1] and to not in newly_offered)
        ):
            return False
        if abs(target) == KING:
            # 吃将结束棋局，尾随着法不能作为有效变化的证据。
            return index == len(pv) - 1 and balance <= -1.0
        if target:
            value = piece_value(target, to)
            balance += value if target * pos.turn < 0 else -value
        if balance > -1.0:
            return False
        # 只需要合法回放及子力事实，不需要重复检查合法性或维护棋谱哈希。
        board[to], board[frm] = board[frm], 0
        turn = -turn
    return balance <= -1.0


def purpose_of_move(
    pos: Position,
    move: str | Move,
    *,
    pv: list[str] | None = None,
    score_before: float | None = None,
    score_after: float | None = None,
    deep: bool = False,
) -> Purpose:
    """pv 从走完 move 后的对方应着开始；分数都从走棋方视角表示。

    不更改传入的局面。只陈述可由规则验证的吃子、将军和威胁；只有引擎
    分数支持、当前献子被接受的完整合法变化才称作「弃子抢攻」。deep 开启
    一步杀威胁检查。pv 应传入引擎完整变化，不截断尚未结束的兑换。
    """
    parsed = parse_iccs(move) if isinstance(move, str) else move
    if not pos.is_legal(parsed):
        raise ValueError("不能解释不合法的着法")
    frm, to = parsed
    piece, captured = pos.board[frm], pos.board[to]
    if abs(captured) == KING:
        return Purpose(f"吃掉对方的{piece_name(captured)}，结束对局。")

    checking = gives_check(pos.board, parsed)
    was_checked = pos.in_check()
    mine_before = threatened(pos.board, pos.turn)
    theirs_before = {t.square for t in threatened(pos.board, -pos.turn)}
    after = pos.copy()
    after.push(parsed)
    mine_after = {t.square for t in threatened(after.board, pos.turn)}
    targets = [t for t in threatened(after.board, -pos.turn) if t.square not in theirs_before]

    actions: list[str] = []
    if captured:
        actions.append(f"吃掉对方的{piece_name(captured)}")
    if checking and not after.legal_moves():
        actions.append("将死对方")
    elif checking:
        actions.append("将军，迫使对方应将")
    elif deep and mating_moves(after.board, pos.turn):
        actions.append("制造一步杀威胁，迫使对方防守")
    if len(targets) >= 2:
        actions.append("同时攻击两个目标，形成捉双")
    elif targets:
        actions.append(f"攻击对方的{piece_name(targets[0].piece)}")
    if was_checked:
        actions.append("解除将军")
    elif any((to if t.square == frm else t.square) not in mine_after for t in mine_before):
        actions.append("解除己方棋子受到的威胁")

    # 稳定的引擎评价才支持「主动弃子」的判断。局面已明显劣势时，即使
    # 分数不再下降也不能把损失包装成成功的攻势。
    sacrifice = (
        score_before is not None
        and score_after is not None
        and isfinite(score_before)
        and isfinite(score_after)
        and 0.0 <= score_before <= 1.0
        and 0.0 <= score_after <= 1.0
        and score_after >= max(0.45, score_before - 0.03)
        and _accepted_sacrifice(
            pos,
            parsed,
            after,
            pv or [],
            mine_after - {to if t.square == frm else t.square for t in mine_before},
        )
    )
    if sacrifice:
        actions.insert(0, "弃子争取攻势，引擎评价仍支持这步棋")
    if not actions:
        name = piece_name(piece)
        if abs(piece) == PAWN:
            actions.append("推进兵卒，争取活动空间")
        elif abs(piece) in (KNIGHT, ROOK, CANNON):
            if game_phase(pos) == "开局" and rank_of(frm) in (0, 9) and rank_of(to) != rank_of(frm):
                actions.append("出动大子，抢占要点")
            else:
                actions.append(f"调整{name}的位置，为后续走法做准备")
        else:
            actions.append(f"调整{name}的位置，巩固阵形")
    return Purpose("；".join(actions[:3]) + "。", sacrifice=sacrifice)
