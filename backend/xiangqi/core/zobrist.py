"""Zobrist 局面哈希：每个「棋子 × 格子」和「黑方走棋」各对应一个固定的随机数，局面哈希是它们的异或。

随机数由哈希函数确定性地生成，跨平台、跨 Python 版本都不变，因此可以存进数据库长期使用
（局面检索、错题本去重、讲解缓存）。取 63 位，保证能放进 SQLite 的有符号 64 位整数。
"""

import hashlib

_MASK63 = (1 << 63) - 1


def _rand63(label: str) -> int:
    digest = hashlib.blake2b(f"xiangqi-zobrist:{label}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") & _MASK63


# PIECE_KEYS[p + 7][s]：p 为 -7..7 的棋子编码
PIECE_KEYS: list[list[int]] = [
    [_rand63(f"{p}:{s}") if p != 0 else 0 for s in range(90)] for p in range(-7, 8)
]
SIDE_KEY: int = _rand63("black-to-move")


def compute_key(board: list[int], turn: int) -> int:
    key = SIDE_KEY if turn < 0 else 0
    for s, p in enumerate(board):
        if p:
            key ^= PIECE_KEYS[p + 7][s]
    return key
