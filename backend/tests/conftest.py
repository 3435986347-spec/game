import sys
from pathlib import Path

from xiangqi.core import Position, parse_iccs
from xiangqi.core.fen import parse_fen


def board_of(fen: str) -> tuple[list[int], int]:
    board, turn, _, _ = parse_fen(fen)
    return board, turn


def play(pos: Position, *moves: str) -> Position:
    for m in moves:
        pos.push(parse_iccs(m))
    return pos


FAKE_ENGINE = Path(__file__).with_name("fake_uci_engine.py")


def fake_engine_command(*flags: str) -> list[str]:
    """启动测试用假引擎的命令（用当前 Python 解释器运行）。"""
    return [sys.executable, str(FAKE_ENGINE), *flags]
