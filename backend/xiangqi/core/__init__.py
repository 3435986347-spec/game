"""象棋规则引擎：棋盘表示、着法生成、胜负判定、FEN 与中文记谱。"""

from .board import BLACK, RED, Move
from .fen import START_FEN, FenError, format_fen, parse_fen, validate_board
from .movegen import in_check, is_legal_move, legal_moves, perft, pseudo_legal_moves
from .notation import (
    NotationError,
    chinese_variants,
    move_to_chinese,
    move_to_iccs,
    parse_chinese,
    parse_iccs,
    parse_move,
)
from .position import IllegalMoveError, Position
from .rules import GameResult, RuleConfig, game_result

__all__ = [
    "BLACK",
    "RED",
    "START_FEN",
    "FenError",
    "GameResult",
    "IllegalMoveError",
    "Move",
    "NotationError",
    "Position",
    "RuleConfig",
    "chinese_variants",
    "format_fen",
    "game_result",
    "in_check",
    "is_legal_move",
    "legal_moves",
    "move_to_chinese",
    "move_to_iccs",
    "parse_chinese",
    "parse_fen",
    "parse_iccs",
    "parse_move",
    "perft",
    "pseudo_legal_moves",
    "validate_board",
]
