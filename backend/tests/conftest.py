from xiangqi.core import Position, parse_iccs
from xiangqi.core.fen import parse_fen


def board_of(fen: str) -> tuple[list[int], int]:
    board, turn, _, _ = parse_fen(fen)
    return board, turn


def play(pos: Position, *moves: str) -> Position:
    for m in moves:
        pos.push(parse_iccs(m))
    return pos
