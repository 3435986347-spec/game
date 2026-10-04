"""把解析出的棋谱变成经过校验的对局：起始局面、ICCS 着法、结果。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core import (
    FenError,
    NotationError,
    Position,
    in_check,
    parse_chinese,
    parse_iccs,
    pseudo_legal_moves,
)
from ..core.board import ADVISOR, BISHOP, CANNON, KING, KNIGHT, PAWN, ROOK
from ..core.notation import BLACK_NAME, RED_NAME, RED_NUM, move_to_iccs
from .parse import RawGame

_RESULTS = {
    "1-0": "1-0", "0-1": "0-1", "1/2-1/2": "1/2-1/2", "½-½": "1/2-1/2", "*": "*",
    "红胜": "1-0", "红先胜": "1-0", "黑胜": "0-1", "红先负": "0-1", "和": "1/2-1/2",
    "和棋": "1/2-1/2", "红先和": "1/2-1/2",
}  # fmt: skip

_ICCS = re.compile(r"^[A-Ia-i][0-9]-?[A-Ia-i][0-9]$")
_WXF = re.compile(r"^(?:([+-])([A-Za-z])|([A-Za-z])([1-9+-]))([+\-.=])([1-9])$")
_WXF_PIECES = {
    "K": KING, "A": ADVISOR, "E": BISHOP, "B": BISHOP, "H": KNIGHT, "N": KNIGHT,
    "R": ROOK, "C": CANNON, "P": PAWN,
}  # fmt: skip
_WXF_ACTIONS = {"+": "进", "-": "退", ".": "平", "=": "平"}


class GameFormatError(ValueError):
    """棋谱中有不合法或无法识别的着法、起始局面无效等。message 直接给用户看。"""


@dataclass
class ParsedGame:
    headers: dict[str, str]
    initial_fen: str
    moves: list[str]  # ICCS，每一步都已校验合法
    result: str  # "1-0" | "0-1" | "1/2-1/2" | "*"
    # 每一步之后的局面哈希（keys[0] 为起始局面），建立局面索引用
    keys: list[int] = field(default_factory=list, repr=False, compare=False)


def resolve_game(raw: RawGame) -> ParsedGame:
    """逐步校验着法并转成 ICCS。任何一步不合法或无法识别都抛 GameFormatError。"""
    fen = raw.headers.get("FEN", "").strip()
    try:
        pos = Position.from_fen(fen) if fen else Position.start()
    except FenError as e:
        raise GameFormatError(f"起始局面（FEN）无效：{e}") from e
    initial_fen = pos.fen()
    moves: list[str] = []
    keys = [pos.key]
    for ply, text in enumerate(raw.moves, start=1):
        move = _resolve_move(pos, text, ply)
        pos._push_unchecked(move)  # 已在 _resolve_move 中校验
        moves.append(move_to_iccs(move))
        keys.append(pos.key)
    result = normalize_result(raw.headers.get("Result")) or raw.result or "*"
    return ParsedGame(raw.headers, initial_fen, moves, result, keys)


def normalize_result(value: str | None) -> str | None:
    if value is None:
        return None
    return _RESULTS.get(value.strip())


def _resolve_move(pos: Position, text: str, ply: int) -> tuple[int, int]:
    if _ICCS.match(text):
        move = parse_iccs(text)
        if not pos.is_legal(move):
            raise GameFormatError(f"第 {ply} 步 {text} 不合法：{_illegal_reason(pos, move)}")
        return move
    chinese = text
    if _WXF.match(text):
        try:
            chinese = wxf_to_chinese(text, red=pos.turn > 0)
        except GameFormatError as e:
            raise GameFormatError(f"第 {ply} 步 {text} 无法识别：{e}") from e
    try:
        return parse_chinese(pos.board, pos.turn, chinese)
    except NotationError as e:
        side = "红方" if pos.turn > 0 else "黑方"
        if not any(c in text for c in "进進退平+-.="):
            raise GameFormatError(f"第 {ply} 步 {text} 无法识别（不是着法）") from e
        raise GameFormatError(f"第 {ply} 步 {text} 不合法或无法识别（轮到{side}走）") from e


def _illegal_reason(pos: Position, move: tuple[int, int]) -> str:
    frm, to = move
    piece = pos.board[frm]
    if piece == 0:
        return "起点没有棋子"
    if piece * pos.turn < 0:
        return "走的是对方的棋子"
    if move not in pseudo_legal_moves(pos.board, pos.turn):
        return "不符合棋子的走法"
    if in_check(pos.board, pos.turn):
        return "被将军时没有应将"
    return "走完后己方被将军（或将帅照面）"


def wxf_to_chinese(text: str, *, red: bool) -> str:
    """WXF 记法 → 中文纵线记谱，如 C2.5 → 炮二平五（红方）、H8+7 → 马8进7（黑方）。
    同一纵线上两个同种子用 C+.5 / +C.5（前）、C-.5 / -C.5（后）表示。"""
    m = _WXF.match(text)
    if not m:
        raise GameFormatError(f"无法识别的 WXF 着法：{text}")
    tandem, letter = (m.group(1), m.group(2)) if m.group(1) else (None, m.group(3))
    second, action, target = m.group(4), m.group(5), m.group(6)
    if second in ("+", "-"):
        tandem = second
    piece = _WXF_PIECES.get(letter.upper())
    if piece is None:
        raise GameFormatError(f"无法识别的 WXF 棋子：{text}")
    name = (RED_NAME if red else BLACK_NAME)[piece]

    def num(d: str) -> str:
        return RED_NUM[int(d)] if red else d

    head = ("前" if tandem == "+" else "后") + name if tandem else name + num(second)
    return head + _WXF_ACTIONS[action] + num(target)
