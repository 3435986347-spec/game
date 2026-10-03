"""着法生成与将军检测。

函数直接操作长度为 90 的棋盘列表，不依赖 Position，便于 perft 测试和批量回放。
"""

from .board import (
    ADVISOR,
    ADVISOR_MOVES,
    BISHOP,
    BISHOP_MOVES,
    CANNON,
    KING,
    KING_MOVES,
    KNIGHT,
    KNIGHT_ATTACKERS,
    KNIGHT_MOVES,
    PAWN,
    PAWN_MOVES,
    RAYS,
    ROOK,
    Move,
    file_of,
    on_board,
    rank_of,
    sq,
)


def pseudo_legal_moves(b: list[int], side: int) -> list[Move]:
    """按各棋子走法生成着法，不检查走完后己方是否被将军。"""
    out: list[Move] = []
    for s, p in enumerate(b):
        if p * side <= 0:
            continue
        t = p if side > 0 else -p
        if t == ROOK:
            for ray in RAYS[s]:
                for x in ray:
                    q = b[x]
                    if q == 0:
                        out.append((s, x))
                        continue
                    if q * side < 0:
                        out.append((s, x))
                    break
        elif t == CANNON:
            for ray in RAYS[s]:
                screen = False
                for x in ray:
                    q = b[x]
                    if not screen:
                        if q == 0:
                            out.append((s, x))  # 不吃子时像车一样走
                        else:
                            screen = True  # 找到炮架
                    elif q != 0:
                        if q * side < 0:
                            out.append((s, x))  # 隔一子吃对方
                        break
        elif t == KNIGHT:
            for to, leg in KNIGHT_MOVES[s]:
                if b[leg] == 0 and b[to] * side <= 0:  # 蹩马腿
                    out.append((s, to))
        elif t == BISHOP:
            for to, eye in BISHOP_MOVES[s]:
                if b[eye] == 0 and b[to] * side <= 0:  # 塞象眼
                    out.append((s, to))
        elif t == ADVISOR:
            for to in ADVISOR_MOVES[s]:
                if b[to] * side <= 0:
                    out.append((s, to))
        elif t == KING:
            for to in KING_MOVES[s]:
                if b[to] * side <= 0:
                    out.append((s, to))
        elif t == PAWN:
            for to in PAWN_MOVES[side][s]:
                if b[to] * side <= 0:
                    out.append((s, to))
    return out


def in_check(b: list[int], side: int) -> bool:
    """side 方（1 红 / -1 黑）的帅/将是否正被将军（含将帅照面）。

    从帅的位置反向查找，比生成对方全部着法快得多。仕、相不能离开己方半场，
    永远将不到对方，所以只需要查车、炮、马、兵和将帅照面。
    """
    k = b.index(KING * side)
    enemy = -side
    for ray in RAYS[k]:  # 车、炮，以及将帅照面
        screen = False
        for t in ray:
            p = b[t]
            if p == 0:
                continue
            if not screen:
                if p == enemy * ROOK or p == enemy * KING:  # 中间无子直接碰到对方将帅 = 照面
                    return True
                screen = True  # 第一个子当作炮架
            else:
                if p == enemy * CANNON:
                    return True
                break
    for n, leg in KNIGHT_ATTACKERS[k]:  # 马：马腿在「马」旁边，不在帅旁边！
        if b[n] == enemy * KNIGHT and b[leg] == 0:
            return True
    f, r = file_of(k), rank_of(k)
    if on_board(f, r + side) and b[sq(f, r + side)] == enemy * PAWN:  # 对方兵从正前方攻来
        return True
    for df in (-1, 1):  # 能走到我方九宫旁的兵一定已过河，可横向攻击
        if on_board(f + df, r) and b[sq(f + df, r)] == enemy * PAWN:
            return True
    return False


def legal_moves(b: list[int], side: int) -> list[Move]:
    """合法着法 = 伪合法着法中，走完后己方不被将军（含将帅照面）的那些。"""
    out: list[Move] = []
    for frm, to in pseudo_legal_moves(b, side):
        cap = b[to]
        b[to] = b[frm]
        b[frm] = 0
        if not in_check(b, side):
            out.append((frm, to))
        b[frm] = b[to]
        b[to] = cap
    return out


def perft(b: list[int], side: int, depth: int) -> int:
    """从当前局面走 depth 步的所有合法着法序列数，用于检验着法生成是否正确。"""
    if depth == 0:
        return 1
    moves = legal_moves(b, side)
    if depth == 1:
        return len(moves)
    total = 0
    for frm, to in moves:
        cap = b[to]
        b[to] = b[frm]
        b[frm] = 0
        total += perft(b, -side, depth - 1)
        b[frm] = b[to]
        b[to] = cap
    return total
