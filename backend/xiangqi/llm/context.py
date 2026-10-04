"""讲解上下文（docs 3.5 节）：交给大模型的结构化事实。

原则：大模型只「讲」，不「算」。所有事实——合法着法、最佳着法、胜率、对方的应着、哪个子没保护——
都由引擎和规则引擎算好；大模型只负责挑重点、讲清楚、总结原则。
上下文由 5 部分组成：文字棋盘、棋子清单、引擎分析、规则引擎提取的战术事实、学生水平。
allowed_moves_cn 列出讲解中允许出现的全部着法，用于事后校验（validate.py）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import RED, Move, Position, move_to_chinese, move_to_iccs, parse_iccs
from ..core.board import ADVISOR, BISHOP, CANNON, KING, KNIGHT, PAWN, ROOK
from ..core.features import (
    SIDE_NAME,
    describe,
    game_phase,
    gives_check,
    material_text,
    mating_moves,
    piece_name,
    piece_value,
    threat_text,
    threatened,
)
from ..core.movegen import in_check
from ..core.notation import chinese_variants
from .validate import move_key

_RED_CHARS = " 帅仕相马车炮兵"
_BLACK_CHARS = " 将士象馬車砲卒"  # 黑方用繁体区分，帮助模型分清红黑
_EMPTY = "．"
_PIECE_ORDER = (KING, ROOK, KNIGHT, CANNON, ADVISOR, BISHOP, PAWN)


@dataclass(frozen=True)
class EngineLine:
    """引擎的一条候选着法。score 为走棋方的期望得分 0..1（胜 1、和 0.5、负 0）。"""

    move: str  # ICCS
    pv: tuple[str, ...]
    score: float
    mate: int | None = None  # 正数：走棋方 N 步杀；负数：被杀

    def to_dict(self) -> dict:
        return {"move": self.move, "pv": list(self.pv), "score": self.score, "mate": self.mate}

    @classmethod
    def from_dict(cls, data: dict) -> EngineLine:
        return cls(data["move"], tuple(data["pv"]), float(data["score"]), data.get("mate"))


@dataclass
class ExplainContext:
    data: dict  # 交给大模型的 JSON
    allowed: list[str]  # allowed_moves_cn（标准写法）
    allowed_keys: set[str]  # 标准写法和别种写法的比较键，校验用
    # 以下给模板讲解和缓存键使用
    position_key: str  # 局面（棋子摆放 + 走棋方）
    move: str  # ICCS
    move_cn: str
    side: int
    grade: str | None
    win_before: float | None
    win_after: float | None
    best: list[tuple[str, float, list[str]]]  # (着法, 走棋方期望得分, 主要变化)
    reply: tuple[str, str] | None  # (对方最佳应着, 效果)
    facts: list[str]
    key_facts: list[str]  # 最能说明问题的事实（模板讲解的「原因」）
    tags: list[str]
    phase: str
    level: str


def board_text(board: list[int]) -> list[str]:
    """中文字符画的棋盘，黑方在上。"""
    lines = ["  1 2 3 4 5 6 7 8 9   （黑方）"]
    for rank in range(9, -1, -1):
        row = []
        for f in range(9):
            p = board[rank * 9 + f]
            row.append(_EMPTY if p == 0 else (_RED_CHARS if p > 0 else _BLACK_CHARS)[abs(p)])
        lines.append(f"{rank} {''.join(row)}")
    lines.append("  九八七六五四三二一   （红方）")
    lines.append("图例：红方 帅仕相马车炮兵；黑方 将士象馬車砲卒")
    return lines


def piece_list(board: list[int], side: int) -> list[str]:
    """如 ["帅(e0)", "车(a0)", ...]：每个子的位置，免得模型自己数格子。"""
    out = []
    for t in _PIECE_ORDER:
        for s in range(90):
            if board[s] == t * side:
                out.append(describe(board, s))
    return out


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


class _Moves:
    """收集讲解中允许出现的着法（标准写法 + 别种写法的比较键）。"""

    def __init__(self) -> None:
        self.allowed: list[str] = []
        self.keys: set[str] = set()

    def add(self, board: list[int], move: Move) -> str:
        cn = move_to_chinese(board, move)
        if cn not in self.allowed:
            self.allowed.append(cn)
        for variant in chinese_variants(board, move):
            self.keys.add(move_key(variant))
        return cn

    def add_line(self, pos: Position, pv: tuple[str, ...] | list[str], limit: int) -> list[str]:
        p = pos.copy()
        out = []
        for text in list(pv)[:limit]:
            move = parse_iccs(text)
            if not p.is_legal(move):
                break
            out.append(self.add(p.board, move))
            p.push(move)
        return out


def _capture_effect(board: list[int], move: Move) -> str:
    """这步棋的效果：吃了什么、是否将军。"""
    frm, to = move
    parts = []
    if board[to]:
        parts.append(f"吃掉{SIDE_NAME[-1 if board[frm] > 0 else 1]}的{describe(board, to)}")
    if gives_check(board, move):
        parts.append("将军")
    return "，".join(parts)


def _material_swing(pos: Position, moves: list[str]) -> tuple[list[str], list[str], float] | None:
    """按着法序列走下去，双方各吃掉了什么，以及走棋方的子力净得失。没有吃子时返回 None。"""
    mover = pos.turn
    p = pos.copy()
    gained: list[str] = []  # 走棋方吃掉的
    lost: list[str] = []  # 走棋方被吃掉的
    net = 0.0
    for text in moves:
        move = parse_iccs(text)
        if not p.is_legal(move):
            break
        captured = p.board[move[1]]
        if captured:
            value = piece_value(captured, move[1])
            if captured * mover < 0:
                gained.append(piece_name(captured))
                net += value
            else:
                lost.append(piece_name(captured))
                net -= value
        p.push(move)
    if not gained and not lost:
        return None
    return gained, lost, net


def build_context(
    pos: Position,
    move: Move,
    *,
    before: list[EngineLine],
    after: list[EngineLine],
    win_before: float | None,
    win_after: float | None,
    grade: str | None,
    level: str,
) -> ExplainContext:
    """pos：走这步之前的局面；before：这个局面的引擎候选（走棋方视角，最好的在前）；
    after：走完之后局面的引擎候选（对方视角，第一条即对方的最佳应着）；
    win_before / win_after：走棋方在走这步之前（按最佳着法）/ 之后的期望得分。"""
    b = pos.board
    mover = pos.turn
    me, opp = SIDE_NAME[mover], SIDE_NAME[-mover]
    move_iccs = move_to_iccs(move)
    moves = _Moves()
    move_cn = moves.add(b, move)

    after_pos = pos.copy()
    after_pos.push(move)
    a = after_pos.board
    to = move[1]

    facts: list[str] = []
    key_facts: list[str] = []
    tags: list[str] = []

    def tag(name: str) -> None:
        if name not in tags:
            tags.append(name)

    # ---- 这步棋本身 ----
    if pos.in_check():
        facts.append(f"走这步之前{me}正被将军，必须应将")
    if b[to]:
        facts.append(f"{move_cn}吃掉了{opp}的{describe(b, to)}")
    if in_check(a, -mover):
        facts.append(f"{move_cn}将军")
        tag("将军")

    # ---- 走完之后：自己的子是否安全 ----
    before_mine = {t.square for t in threatened(b, mover)}
    for t in threatened(a, mover):
        text = threat_text(a, t)
        if t.square == to:
            fact = f"走完后{text}"
            if t.hanging:
                fact += "：这是一枚无根子"
                tag("无根子")
            if b[to] and piece_value(a[to], to) > piece_value(b[to], to):
                tag("贪吃")
            facts.append(fact)
            key_facts.append(fact)
        elif t.square in before_mine:
            fact = f"走这步之前{text}，这步没有解除威胁"
            facts.append(fact)
            key_facts.append(fact)
            tag("没有解除威胁")
        else:
            fact = f"走完后{text}"
            facts.append(fact)
            key_facts.append(fact)
            tag("漏看对方攻击")

    # ---- 走完之后：给对方制造的威胁 ----
    before_theirs = {t.square for t in threatened(b, -mover)}
    new_targets = [t for t in threatened(a, -mover) if t.square not in before_theirs]
    for t in new_targets:
        facts.append(f"这步棋之后{threat_text(a, t)}")
    if len(new_targets) >= 2:
        tag("捉双")
    elif new_targets:
        tag("捉子")

    # 空着法：假设对方停一步不走，走棋方下一步能不能直接将死（对方正被将军时不适用）
    if not in_check(a, -mover):
        for m in mating_moves(a, mover)[:2]:
            # 着法要在「对方停一步」的局面里写：棋盘相同，只是走棋方不变
            cn = moves.add(a, m)
            facts.append(f"如果{opp}不应对，{me}下一步{cn}就能将死对方")
            tag("杀棋威胁")
    for m in mating_moves(a, -mover)[:2]:
        cn = moves.add(a, m)
        fact = f"走完后{opp}可以{cn}一步将死{me}"
        facts.append(fact)
        key_facts.insert(0, fact)
        tag("漏看杀棋")

    # ---- 引擎分析 ----
    best = []
    engine_best = []
    for line in before[:3]:
        pv_cn = moves.add_line(pos, line.pv, 5)
        if not pv_cn:
            continue
        best.append((pv_cn[0], line.score, pv_cn))
        entry: dict = {"cn": pv_cn[0], "iccs": line.move, "win_prob": round(line.score, 2)}
        if line.mate is not None:
            entry["mate"] = f"{me}{line.mate}步杀" if line.mate > 0 else f"{opp}{-line.mate}步杀"
        if len(pv_cn) > 1:
            entry["pv_cn"] = pv_cn
        engine_best.append(entry)
    if best and before and before[0].move != move_iccs:
        fact = f"引擎认为更好的是{best[0][0]}（走完后{me}期望得分约{_pct(best[0][1])}）"
        if win_after is not None:
            fact += f"，实际这步走完后约{_pct(win_after)}"
        facts.append(fact)

    reply = None
    opponent_reply = None
    if after:
        pv_cn = moves.add_line(after_pos, after[0].pv, 5)
        if pv_cn:
            reply_move = parse_iccs(after[0].move)
            effect = _capture_effect(a, reply_move)
            reply = (pv_cn[0], effect)
            opponent_reply = {"cn": pv_cn[0], "iccs": after[0].move}
            if effect:
                opponent_reply["effect"] = effect
            if len(pv_cn) > 1:
                opponent_reply["pv_cn"] = pv_cn
            fact = f"{opp}的最佳应着是{pv_cn[0]}" + (f"，{effect}" if effect else "")
            facts.append(fact)
            if effect:
                key_facts.append(fact)

        swing = _material_swing(pos, [move_iccs, *after[0].pv[:3]])
        if swing is not None:
            gained, lost, net = swing
            parts = []
            if gained:
                parts.append(f"{me}吃掉{'、'.join(gained)}")
            if lost:
                parts.append(f"{opp}吃掉{'、'.join(lost)}")
            verdict = (
                f"{me}子力净亏约{-net:g}分"
                if net < 0
                else f"{me}子力净赚约{net:g}分"
                if net > 0
                else "子力大致相当"
            )
            fact = f"按这步加引擎主要变化走下去：{'，'.join(parts)}，{verdict}"
            facts.append(fact)
            if net <= -1:
                key_facts.append(fact)
                tag("亏子")

    phase = game_phase(pos)
    tag(phase)

    move_played: dict = {"cn": move_cn, "iccs": move_iccs}
    if win_before is not None:
        move_played["win_prob_before"] = round(win_before, 2)
    if win_after is not None:
        move_played["win_prob_after"] = round(win_after, 2)
    if grade:
        move_played["grade"] = grade

    data: dict = {
        "player": {"side": me, "level": level},
        "phase": phase,
        "fen": pos.fen(),
        "board_text": board_text(b),
        "pieces": {SIDE_NAME[RED]: piece_list(b, RED), SIDE_NAME[-RED]: piece_list(b, -RED)},
        "material": {
            SIDE_NAME[RED]: material_text(b, RED),
            SIDE_NAME[-RED]: material_text(b, -RED),
        },
        "move_played": move_played,
        "engine_best": engine_best,
    }
    if opponent_reply:
        data["opponent_reply"] = opponent_reply
    data["facts"] = facts
    data["allowed_moves_cn"] = moves.allowed

    return ExplainContext(
        data=data,
        allowed=moves.allowed,
        allowed_keys=moves.keys,
        position_key=" ".join(pos.fen().split()[:2]),
        move=move_iccs,
        move_cn=move_cn,
        side=mover,
        grade=grade,
        win_before=win_before,
        win_after=win_after,
        best=best,
        reply=reply,
        facts=facts,
        key_facts=key_facts,
        tags=tags,
        phase=phase,
        level=level,
    )
