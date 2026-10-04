"""Position：棋盘 + 走棋方 + 历史，支持走子和悔棋。"""

from __future__ import annotations

from dataclasses import dataclass

from .board import BLACK, RED, Move
from .fen import START_FEN, FenError, format_fen, parse_fen, validate_board
from .movegen import in_check, is_legal_move, legal_moves
from .zobrist import PIECE_KEYS, SIDE_KEY, compute_key


class IllegalMoveError(ValueError):
    """着法不合法。"""


@dataclass(slots=True, frozen=True)
class _Undo:
    move: Move
    captured: int
    halfmove_clock: int
    gave_check: bool  # 这步棋是否将军（长将判定用）


class Position:
    """一个可以走子、悔棋的局面。

    board[sq] 为棋子编码（红正黑负，0 为空）；turn 为 1（红走）或 -1（黑走）。
    key 为当前局面的 Zobrist 哈希；keys 记录了从初始局面开始每一步之后的哈希。
    """

    __slots__ = ("board", "turn", "halfmove_clock", "fullmove", "key", "_keys", "_undo")

    def __init__(
        self, board: list[int], turn: int = RED, halfmove_clock: int = 0, fullmove: int = 1
    ) -> None:
        self.board = list(board)
        self.turn = turn
        self.halfmove_clock = halfmove_clock
        self.fullmove = fullmove
        self.key = compute_key(self.board, turn)
        self._keys = [self.key]
        self._undo: list[_Undo] = []

    @classmethod
    def start(cls) -> Position:
        return cls.from_fen(START_FEN)

    @classmethod
    def from_fen(cls, fen: str, *, validate: bool = True) -> Position:
        board, turn, halfmove, fullmove = parse_fen(fen)
        if validate:
            errors = validate_board(board, turn)
            if errors:
                raise FenError("；".join(errors))
        return cls(board, turn, halfmove, fullmove)

    def fen(self) -> str:
        return format_fen(self.board, self.turn, self.halfmove_clock, self.fullmove)

    def copy(self) -> Position:
        other = Position.__new__(Position)
        other.board = list(self.board)
        other.turn = self.turn
        other.halfmove_clock = self.halfmove_clock
        other.fullmove = self.fullmove
        other.key = self.key
        other._keys = list(self._keys)
        other._undo = list(self._undo)
        return other

    # ---- 查询 ----

    @property
    def ply(self) -> int:
        """从创建以来走了多少步（半回合）。"""
        return len(self._undo)

    @property
    def moves(self) -> list[Move]:
        return [u.move for u in self._undo]

    @property
    def keys(self) -> list[int]:
        """keys[i] 为走了 i 步之后的局面哈希，keys[0] 为初始局面。"""
        return list(self._keys)

    def gave_check(self, ply_index: int) -> bool:
        """第 ply_index 步（从 0 开始）是否将军。"""
        return self._undo[ply_index].gave_check

    def legal_moves(self) -> list[Move]:
        return legal_moves(self.board, self.turn)

    def is_legal(self, move: Move) -> bool:
        return is_legal_move(self.board, self.turn, move)

    def in_check(self) -> bool:
        return in_check(self.board, self.turn)

    # ---- 走子 / 悔棋 ----

    def push(self, move: Move) -> None:
        if not self.is_legal(move):
            raise IllegalMoveError(f"不合法的着法：{move}")
        self._push_unchecked(move)

    def _push_unchecked(self, move: Move) -> None:
        frm, to = move
        b = self.board
        piece, captured = b[frm], b[to]
        key = self.key ^ PIECE_KEYS[piece + 7][frm] ^ PIECE_KEYS[piece + 7][to] ^ SIDE_KEY
        if captured:
            key ^= PIECE_KEYS[captured + 7][to]
        b[to] = piece
        b[frm] = 0
        gave_check = in_check(b, -self.turn)
        self._undo.append(_Undo(move, captured, self.halfmove_clock, gave_check))
        self.halfmove_clock = 0 if captured else self.halfmove_clock + 1
        if self.turn == BLACK:
            self.fullmove += 1
        self.turn = -self.turn
        self.key = key
        self._keys.append(key)

    def pop(self) -> Move:
        if not self._undo:
            raise IndexError("没有可以撤销的着法")
        u = self._undo.pop()
        self._keys.pop()
        frm, to = u.move
        self.board[frm] = self.board[to]
        self.board[to] = u.captured
        self.turn = -self.turn
        if self.turn == BLACK:
            self.fullmove -= 1
        self.halfmove_clock = u.halfmove_clock
        self.key = self._keys[-1]
        return u.move
