"""FEN 读写与局面合法性检查。

FEN 从黑方底线（rank 9）写到红方底线（rank 0）；大写红方、小写黑方：
K 帅 A 仕 B 相 N 马 R 车 C 炮 P 兵。例如初始局面：
rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1
"""

from .board import (
    ADVISOR,
    ADVISOR_SQUARES,
    BISHOP,
    BISHOP_SQUARES,
    BLACK,
    KING,
    MAX_COUNT,
    PAWN,
    PIECE_LETTERS,
    RED,
    file_of,
    in_palace,
    rank_of,
    sq,
)
from .movegen import in_check

START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"

# 有的软件用 E（Elephant）表示相、H（Horse）表示马
_LETTER_ALIASES = {"E": "B", "H": "N"}


class FenError(ValueError):
    """FEN 格式错误，或摆出的局面不可能出现。"""


def piece_from_letter(ch: str) -> int:
    upper = _LETTER_ALIASES.get(ch.upper(), ch.upper())
    t = PIECE_LETTERS.find(upper)
    if t <= 0:
        raise FenError(f"无法识别的棋子字母：{ch!r}")
    return t if ch.isupper() else -t


def piece_to_letter(p: int) -> str:
    letter = PIECE_LETTERS[abs(p)]
    return letter if p > 0 else letter.lower()


def parse_fen(fen: str) -> tuple[list[int], int, int, int]:
    """解析 FEN，返回 (棋盘, 走棋方, 无吃子半回合数, 回合数)。只检查格式，不检查局面是否合理。"""
    parts = fen.split()
    if not parts:
        raise FenError("FEN 为空")
    rows = parts[0].split("/")
    if len(rows) != 10:
        raise FenError(f"FEN 应有 10 行，实际为 {len(rows)} 行")
    board = [0] * 90
    for i, row in enumerate(rows):
        rank, file = 9 - i, 0
        for ch in row:
            if ch.isdigit():
                file += int(ch)
                continue
            if file >= 9:
                raise FenError(f"第 {i + 1} 段「{row}」超过 9 列")
            board[sq(file, rank)] = piece_from_letter(ch)
            file += 1
        if file != 9:
            raise FenError(f"第 {i + 1} 段「{row}」应为 9 列，实际为 {file} 列")

    side_field = parts[1].lower() if len(parts) > 1 else "w"
    if side_field in ("w", "r"):
        turn = RED
    elif side_field == "b":
        turn = BLACK
    else:
        raise FenError(f"无法识别的走棋方：{parts[1]!r}（应为 w 或 b）")

    try:
        halfmove = int(parts[4]) if len(parts) > 4 else 0
        fullmove = int(parts[5]) if len(parts) > 5 else 1
    except ValueError as e:
        raise FenError("FEN 中的回合数不是整数") from e
    return board, turn, max(halfmove, 0), max(fullmove, 1)


def board_to_placement(board: list[int]) -> str:
    rows = []
    for rank in range(9, -1, -1):
        row, empty = "", 0
        for file in range(9):
            p = board[sq(file, rank)]
            if p == 0:
                empty += 1
                continue
            if empty:
                row += str(empty)
                empty = 0
            row += piece_to_letter(p)
        if empty:
            row += str(empty)
        rows.append(row)
    return "/".join(rows)


def format_fen(board: list[int], turn: int, halfmove: int = 0, fullmove: int = 1) -> str:
    side = "w" if turn == RED else "b"
    return f"{board_to_placement(board)} {side} - - {halfmove} {fullmove}"


def validate_board(board: list[int], turn: int) -> list[str]:
    """检查局面是否可能在实战中出现，返回问题列表（空列表表示没有问题）。"""
    errors: list[str] = []
    for side, name in ((RED, "红方"), (BLACK, "黑方")):
        counts = {t: 0 for t in MAX_COUNT}
        for s, p in enumerate(board):
            if p * side <= 0:
                continue
            t = abs(p)
            counts[t] += 1
            f, r = file_of(s), rank_of(s)
            if t == KING and not in_palace(f, r, side):
                errors.append(f"{name}的帅/将不在九宫内")
            elif t == ADVISOR and s not in ADVISOR_SQUARES[side]:
                errors.append(f"{name}的仕/士位置不合法")
            elif t == BISHOP and s not in BISHOP_SQUARES[side]:
                errors.append(f"{name}的相/象位置不合法")
            elif t == PAWN:
                home = r if side == RED else 9 - r  # 换算成「己方视角」的行号
                if home < 3 or (home <= 4 and f % 2 == 1):
                    errors.append(f"{name}的兵/卒位置不合法")
        if counts[KING] != 1:
            errors.append(f"{name}应有且只有一个帅/将")
        for t, limit in MAX_COUNT.items():
            if t != KING and counts[t] > limit:
                errors.append(f"{name}的{'仕相马车炮兵'[t - 2]}超过 {limit} 个")
    if not errors and in_check(board, -turn):
        errors.append("轮到走棋的一方可以直接吃掉对方的帅/将（或将帅照面）")
    return errors
