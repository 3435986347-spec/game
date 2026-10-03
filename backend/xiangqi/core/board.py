"""棋盘坐标、棋子编码与预计算表。

坐标约定（与 docs/xiangqi-plan.md 3.1 节一致）：
- 列 file 0–8 对应 a–i（红方视角从左到右），行 rank 0–9（红方底线为 0）。
- 一维下标 sq = rank * 9 + file，范围 0–89。
- 棋子用整数表示：红方为正、黑方为负、0 为空；绝对值为棋子类型。
"""

from typing import Final

RED: Final = 1
BLACK: Final = -1

EMPTY: Final = 0
KING, ADVISOR, BISHOP, KNIGHT, ROOK, CANNON, PAWN = range(1, 8)  # 帅仕相马车炮兵

PIECE_LETTERS: Final = " KABNRCP"  # 下标 = 棋子类型；FEN 中大写为红方、小写为黑方
FILES: Final = "abcdefghi"

Move = tuple[int, int]  # (起点, 终点)


def sq(file: int, rank: int) -> int:
    return rank * 9 + file


def file_of(s: int) -> int:
    return s % 9


def rank_of(s: int) -> int:
    return s // 9


def on_board(f: int, r: int) -> bool:
    return 0 <= f < 9 and 0 <= r < 10


def in_palace(f: int, r: int, side: int) -> bool:
    """九宫：d–f 列，红方 0–2 行，黑方 7–9 行。"""
    return 3 <= f <= 5 and (0 <= r <= 2 if side == RED else 7 <= r <= 9)


def own_half(r: int, side: int) -> bool:
    """是否在 side 方自己的半场（红方 0–4 行，黑方 5–9 行）。"""
    return 0 <= r <= 4 if side == RED else 5 <= r <= 9


def side_of(s: int) -> int:
    """格子所在半场属于哪一方。"""
    return RED if rank_of(s) <= 4 else BLACK


# ---------------------------------------------------------------------------
# 预计算表：启动时算一次，生成着法时直接查表，省掉大量边界判断。
# ---------------------------------------------------------------------------

# 马：[落点 df, dr, 马腿 lf, lr]。马腿永远在「马」旁边、沿长边方向的那一格。
_KNIGHT_STEPS = [
    (1, 2, 0, 1),
    (-1, 2, 0, 1),
    (1, -2, 0, -1),
    (-1, -2, 0, -1),
    (2, 1, 1, 0),
    (2, -1, 1, 0),
    (-2, 1, -1, 0),
    (-2, -1, -1, 0),
]

KNIGHT_MOVES: list[list[tuple[int, int]]] = [[] for _ in range(90)]  # [(落点, 马腿)]
for _s in range(90):
    _f, _r = file_of(_s), rank_of(_s)
    for _df, _dr, _lf, _lr in _KNIGHT_STEPS:
        if on_board(_f + _df, _r + _dr):
            KNIGHT_MOVES[_s].append((sq(_f + _df, _r + _dr), sq(_f + _lf, _r + _lr)))

# 反查表：哪些格子上的马能跳到 s，以及那匹马的马腿（用于将军检测）
KNIGHT_ATTACKERS: list[list[tuple[int, int]]] = [[] for _ in range(90)]
for _n in range(90):
    for _to, _leg in KNIGHT_MOVES[_n]:
        KNIGHT_ATTACKERS[_to].append((_n, _leg))

# 四个方向上由近到远的格子（车、炮、将帅照面用）
RAYS: list[list[list[int]]] = [[] for _ in range(90)]
for _s in range(90):
    for _df, _dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        _ray, _f, _r = [], file_of(_s) + _df, rank_of(_s) + _dr
        while on_board(_f, _r):
            _ray.append(sq(_f, _r))
            _f, _r = _f + _df, _r + _dr
        RAYS[_s].append(_ray)

# 相/象：田字，不能过河；[(落点, 象眼)]
BISHOP_MOVES: list[list[tuple[int, int]]] = [[] for _ in range(90)]
for _s in range(90):
    _f, _r = file_of(_s), rank_of(_s)
    for _df, _dr in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
        _tf, _tr = _f + _df, _r + _dr
        if on_board(_tf, _tr) and own_half(_tr, side_of(_s)):
            BISHOP_MOVES[_s].append((sq(_tf, _tr), sq(_f + _df // 2, _r + _dr // 2)))

# 仕/士：九宫内斜走一格；帅/将：九宫内直走一格
ADVISOR_MOVES: list[list[int]] = [[] for _ in range(90)]
KING_MOVES: list[list[int]] = [[] for _ in range(90)]
for _s in range(90):
    _f, _r, _side = file_of(_s), rank_of(_s), side_of(_s)
    if not in_palace(_f, _r, _side):
        continue
    for _df, _dr in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        if in_palace(_f + _df, _r + _dr, _side):
            ADVISOR_MOVES[_s].append(sq(_f + _df, _r + _dr))
    for _df, _dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if in_palace(_f + _df, _r + _dr, _side):
            KING_MOVES[_s].append(sq(_f + _df, _r + _dr))

# 兵/卒：过河前只能前进，过河后可左右
PAWN_MOVES: dict[int, list[list[int]]] = {
    RED: [[] for _ in range(90)],
    BLACK: [[] for _ in range(90)],
}
for _side in (RED, BLACK):
    for _s in range(90):
        _f, _r = file_of(_s), rank_of(_s)
        if on_board(_f, _r + _side):
            PAWN_MOVES[_side][_s].append(sq(_f, _r + _side))
        if not own_half(_r, _side):
            for _df in (-1, 1):
                if on_board(_f + _df, _r):
                    PAWN_MOVES[_side][_s].append(sq(_f + _df, _r))

# 各棋子的合法摆放位置（用于检查摆出来的局面是否可能出现）
ADVISOR_SQUARES = {
    RED: {sq(3, 0), sq(5, 0), sq(4, 1), sq(3, 2), sq(5, 2)},
    BLACK: {sq(3, 9), sq(5, 9), sq(4, 8), sq(3, 7), sq(5, 7)},
}
_BISHOP_RED = [(2, 0), (6, 0), (0, 2), (4, 2), (8, 2), (2, 4), (6, 4)]
BISHOP_SQUARES = {
    RED: {sq(f, r) for f, r in _BISHOP_RED},
    BLACK: {sq(f, 9 - r) for f, r in _BISHOP_RED},
}
MAX_COUNT = {KING: 1, ADVISOR: 2, BISHOP: 2, KNIGHT: 2, ROOK: 2, CANNON: 2, PAWN: 5}
