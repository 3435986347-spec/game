"""着法记法：ICCS 坐标（h2e2）与中文纵线记谱（炮二平五）的双向转换。

中文记谱规则：
- 格式为「棋子 + 起点纵线 + 动作 + 数字」。红方用中文数字，黑方用阿拉伯数字；
  纵线都从走棋方自己的右手边数起。
- 同一行移动为「平」，向对方走为「进」，向己方走为「退」。
- 「平」后面是目标纵线；直走子（车炮兵帅）进退时后面是步数；斜走子（马相仕）进退时后面是目标纵线。
- 同一纵线上有两个同种同色子时用「前/后」代替纵线号（仕相除外，进退方向已能区分）；
  兵卒同一纵线有三个时用「前/中/后」，更多时用「一二三四五」；
  若有两条以上纵线各有多个兵卒，则用「前五」这种写法（位置词 + 纵线号）。

中文转回坐标时不单独写解析器：在当前局面生成全部合法着法，逐个生成记谱（含常见的别种写法），
和输入比较，匹配上的就是它。
"""

import re

from .board import ADVISOR, BISHOP, FILES, KNIGHT, PAWN, Move, file_of, rank_of, sq
from .movegen import legal_moves

RED_NUM = "零一二三四五六七八九"
RED_NAME = " 帅仕相马车炮兵"  # 下标 = 棋子类型
BLACK_NAME = " 将士象马车炮卒"

_POSITION_WORDS = {2: "前后", 3: "前中后", 4: "一二三四", 5: "一二三四五"}


class NotationError(ValueError):
    """无法识别或有歧义的着法写法。"""


# ---------------------------------------------------------------------------
# ICCS
# ---------------------------------------------------------------------------

_ICCS_RE = re.compile(r"^\s*([a-iA-I])([0-9])\s*-?\s*([a-iA-I])([0-9])\s*$")


def move_to_iccs(move: Move) -> str:
    frm, to = move
    return f"{FILES[file_of(frm)]}{rank_of(frm)}{FILES[file_of(to)]}{rank_of(to)}"


def parse_iccs(text: str) -> Move:
    """解析 ICCS 坐标着法，接受 h2e2、H2-E2 等写法。不检查是否合法。"""
    m = _ICCS_RE.match(text)
    if not m:
        raise NotationError(f"不是 ICCS 坐标着法：{text!r}")
    f1, r1, f2, r2 = m.groups()
    return sq(FILES.index(f1.lower()), int(r1)), sq(FILES.index(f2.lower()), int(r2))


# ---------------------------------------------------------------------------
# 中文记谱
# ---------------------------------------------------------------------------


def file_no(f: int, red: bool) -> int:
    """纵线编号：红方 a..i 为 9..1，黑方 a..i 为 1..9。"""
    return 9 - f if red else f + 1


def _num(n: int, red: bool) -> str:
    return RED_NUM[n] if red else str(n)


def _tail(b: list[int], move: Move) -> str:
    """动作 + 数字，如「平五」「进7」。"""
    frm, to = move
    p = b[frm]
    red, t = p > 0, abs(p)
    fr, tr, tf = rank_of(frm), rank_of(to), file_of(to)
    if fr == tr:
        return "平" + _num(file_no(tf, red), red)
    action = "进" if (tr > fr) == red else "退"
    if t in (ADVISOR, BISHOP, KNIGHT):
        return action + _num(file_no(tf, red), red)
    return action + _num(abs(tr - fr), red)


def _heads(b: list[int], frm: int) -> list[str]:
    """着法前半部分（棋子 + 纵线，或前后 + 棋子）。第一个为标准写法，其余为别种写法。"""
    p = b[frm]
    red, t, f = p > 0, abs(p), file_of(frm)
    name = (RED_NAME if red else BLACK_NAME)[t]
    file_label = _num(file_no(f, red), red)
    plain = name + file_label

    same = [s for s in range(f, 90, 9) if b[s] == p]  # 同一纵线上的同种同色子，按行号从小到大
    if len(same) < 2:
        return [plain]
    order = same[::-1] if red else same  # 从前（靠近对方）到后
    n, idx = len(order), order.index(frm)
    word = _POSITION_WORDS[n][idx]

    if t in (ADVISOR, BISHOP):  # 仕相不用前后，但也接受「前仕」这种写法
        return [plain, word + name]
    if t == PAWN and _files_with_multiple(b, p) > 1:
        return [word + file_label, word + name + file_label, word + name]
    heads = [word + name]
    if n == 3:
        heads.append("一二三"[idx] + name)
    return heads


def _files_with_multiple(b: list[int], p: int) -> int:
    """棋子 p 有多少条纵线上不止一个。"""
    return sum(1 for f in range(9) if sum(1 for s in range(f, 90, 9) if b[s] == p) > 1)


def move_to_chinese(b: list[int], move: Move) -> str:
    """把着法转成中文纵线记谱。b 是走这步之前的棋盘。"""
    return _heads(b, move[0])[0] + _tail(b, move)


def chinese_variants(b: list[int], move: Move) -> list[str]:
    """标准写法和常见的别种写法。"""
    tail = _tail(b, move)
    return [head + tail for head in _heads(b, move[0])]


# 繁体、全角、红黑异体字等统一映射成同一个比较用的键
_KEY_TABLE = str.maketrans(
    {
        **dict.fromkeys("车車俥伡", "R"),
        **dict.fromkeys("马馬傌", "N"),
        **dict.fromkeys("炮砲包", "C"),
        **dict.fromkeys("相象", "B"),
        **dict.fromkeys("仕士", "A"),
        **dict.fromkeys("帅帥将將", "K"),
        **dict.fromkeys("兵卒", "P"),
        **dict.fromkeys("进進", "+"),
        "退": "-",
        "平": "=",
        "前": "f",
        "中": "m",
        **dict.fromkeys("后後", "b"),
        **{c: str(i) for i, c in enumerate("零一二三四五六七八九")},
        **{c: str(i) for i, c in enumerate("０１２３４５６７８９")},
    }
)
_WHITESPACE_RE = re.compile(r"\s+")


def _match_key(text: str) -> str:
    return _WHITESPACE_RE.sub("", text).translate(_KEY_TABLE)


def parse_chinese(b: list[int], side: int, text: str) -> Move:
    """在 side 方走棋的局面 b 中，把中文记谱解析成着法。"""
    key = _match_key(text)
    matches = {
        m for m in legal_moves(b, side) if any(_match_key(v) == key for v in chinese_variants(b, m))
    }
    if not matches:
        raise NotationError(f"当前局面没有这步棋：{text!r}")
    if len(matches) > 1:
        raise NotationError(f"着法有歧义：{text!r}")
    return matches.pop()


def parse_move(b: list[int], side: int, text: str) -> Move:
    """解析 ICCS 或中文记谱，并检查是否为合法着法。"""
    try:
        move = parse_iccs(text)
    except NotationError:
        return parse_chinese(b, side, text)
    if move not in legal_moves(b, side):
        raise NotationError(f"不合法的着法：{text!r}")
    return move
