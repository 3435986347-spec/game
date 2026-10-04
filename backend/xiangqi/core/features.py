"""战术特征：攻击与保护、处境危险的棋子、子力、局面阶段、一步杀。

讲解上下文（docs 3.5 节）和 L1 方向提示（6.1 节）用到的事实都在这里计算，不调用象棋引擎。
「攻击」指能合法地吃到：走完后己方不被将军，所以被牵制的子不算在攻击它的子里。
"""

from __future__ import annotations

from dataclasses import dataclass

from .board import (
    ADVISOR,
    BISHOP,
    BLACK,
    CANNON,
    FILES,
    KING,
    KNIGHT,
    PAWN,
    RED,
    ROOK,
    Move,
    file_of,
    own_half,
    rank_of,
)
from .movegen import _piece_moves, in_check, legal_moves
from .notation import BLACK_NAME, RED_NAME
from .position import Position

SIDE_NAME = {RED: "红方", BLACK: "黑方"}

# 子力价值（常用的经验值）。帅不计入子力；作为攻击者时按极大值算，保证「帅捉子」不被当成以小捉大。
PIECE_VALUES = {ADVISOR: 2.0, BISHOP: 2.0, KNIGHT: 4.0, ROOK: 9.0, CANNON: 4.5, PAWN: 1.0}
_KING_AS_ATTACKER = 1000.0
_MAJORS = (ROOK, KNIGHT, CANNON)
_COUNT_ORDER = (ROOK, KNIGHT, CANNON, ADVISOR, BISHOP, PAWN)


def side_of_piece(piece: int) -> int:
    return RED if piece > 0 else BLACK


def piece_value(piece: int, square: int) -> float:
    """子力价值；过河兵按 2 分算。帅返回 0。"""
    t = abs(piece)
    if t == KING:
        return 0.0
    if t == PAWN and not own_half(rank_of(square), side_of_piece(piece)):
        return 2.0
    return PIECE_VALUES[t]


def _attacker_value(piece: int, square: int) -> float:
    return _KING_AS_ATTACKER if abs(piece) == KING else piece_value(piece, square)


def piece_name(piece: int) -> str:
    return (RED_NAME if piece > 0 else BLACK_NAME)[abs(piece)]


def square_name(square: int) -> str:
    return f"{FILES[file_of(square)]}{rank_of(square)}"


def describe(board: list[int], square: int) -> str:
    """如「马(g7)」：棋子名 + ICCS 坐标。"""
    return f"{piece_name(board[square])}({square_name(square)})"


# ---------------------------------------------------------------------------
# 攻击与保护
# ---------------------------------------------------------------------------


def attackers(board: list[int], target: int, side: int) -> list[int]:
    """side 方能合法吃到 target 格的棋子所在的格。

    target 上是 side 方自己的子时，求的是「谁在保护它」：假设对方吃掉了它，side 方能不能吃回来。
    空格同理（假设对方的子走到这里）。
    """
    original = board[target]
    if original * side > 0 and abs(original) == KING:
        return []
    if original * side >= 0:
        board[target] = -side * PAWN  # 放一个对方的子占位：只影响「能不能吃」，不影响走法
    out: list[int] = []
    try:
        for s, p in enumerate(board):
            if p * side <= 0 or s == target:
                continue
            moves: list[Move] = []
            _piece_moves(board, s, side, moves)
            if (s, target) not in moves:
                continue
            captured = board[target]
            board[target], board[s] = p, 0
            legal = not in_check(board, side)
            board[s], board[target] = p, captured
            if legal:
                out.append(s)
    finally:
        board[target] = original
    return out


def defenders(board: list[int], square: int) -> list[int]:
    """保护 square 上棋子的己方棋子。"""
    return attackers(board, square, side_of_piece(board[square]))


@dataclass(frozen=True)
class Threat:
    """一个处境危险的棋子。"""

    square: int
    piece: int
    attackers: tuple[int, ...]
    defenders: tuple[int, ...]

    @property
    def hanging(self) -> bool:
        """无根：被攻击且没有保护。"""
        return not self.defenders


def threatened(board: list[int], side: int) -> list[Threat]:
    """side 方处境危险的棋子（不含帅）：被攻击且无根，或被价值低得多的子攻击（以小捉大）。

    价值高的排在前面。
    """
    out: list[Threat] = []
    for s, p in enumerate(board):
        if p * side <= 0 or abs(p) == KING:
            continue
        atk = attackers(board, s, -side)
        if not atk:
            continue
        dfn = attackers(board, s, side)
        cheapest = min(_attacker_value(board[a], a) for a in atk)
        if not dfn or piece_value(p, s) - cheapest >= 1.0:
            out.append(Threat(s, p, tuple(atk), tuple(dfn)))
    out.sort(key=lambda t: -piece_value(t.piece, t.square))
    return out


def threat_text(board: list[int], threat: Threat) -> str:
    """如「黑方 车(i9) 被红方 马(h7) 攻击，没有保护」。"""
    side = side_of_piece(threat.piece)
    who = "、".join(describe(board, a) for a in threat.attackers)
    text = f"{SIDE_NAME[side]} {describe(board, threat.square)} 被{SIDE_NAME[-side]} {who} 攻击"
    if threat.hanging:
        return text + "，没有保护"
    return text + "（攻击它的子价值更低，被吃掉会亏子）"


# ---------------------------------------------------------------------------
# 将军与一步杀
# ---------------------------------------------------------------------------


def gives_check(board: list[int], move: Move) -> bool:
    frm, to = move
    piece, captured = board[frm], board[to]
    board[to], board[frm] = piece, 0
    try:
        return in_check(board, -side_of_piece(piece))
    finally:
        board[frm], board[to] = piece, captured


def checking_moves(board: list[int], side: int) -> list[Move]:
    """side 方可以将军的合法着法。"""
    return [m for m in legal_moves(board, side) if gives_check(board, m)]


def mating_moves(board: list[int], side: int) -> list[Move]:
    """side 方一步就能将死对方的着法。"""
    out: list[Move] = []
    for frm, to in checking_moves(board, side):
        piece, captured = board[frm], board[to]
        board[to], board[frm] = piece, 0
        try:
            if not legal_moves(board, -side):
                out.append((frm, to))
        finally:
            board[frm], board[to] = piece, captured
    return out


# ---------------------------------------------------------------------------
# 子力与局面阶段
# ---------------------------------------------------------------------------


def material(board: list[int], side: int) -> float:
    return sum(piece_value(p, s) for s, p in enumerate(board) if p * side > 0)


def material_text(board: list[int], side: int) -> str:
    """如「车2 马2 炮1 仕2 相2 兵5」。"""
    names = RED_NAME if side == RED else BLACK_NAME
    parts = []
    for t in _COUNT_ORDER:
        n = sum(1 for p in board if p == t * side)
        if n:
            parts.append(f"{names[t]}{n}")
    return " ".join(parts) or "只剩" + names[KING]


def game_ply(pos: Position) -> int:
    """从对局开始算的半回合数（按 FEN 的回合数推算）。"""
    return (pos.fullmove - 1) * 2 + (1 if pos.turn == BLACK else 0)


def game_phase(pos: Position) -> str:
    """开局 / 中局 / 残局。车马炮合计不超过 6 个为残局；前 20 步且车马炮几乎都在为开局。"""
    majors = sum(1 for p in pos.board if abs(p) in _MAJORS)
    if majors <= 6:
        return "残局"
    if game_ply(pos) < 20 and majors >= 10:
        return "开局"
    return "中局"
