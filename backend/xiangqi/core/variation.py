"""着法序列（如引擎主要变化）转中文记谱。"""

from __future__ import annotations

from .notation import NotationError, move_to_chinese, parse_iccs
from .position import Position


def pv_to_chinese(pos: Position, pv: list[str], limit: int = 8) -> list[str]:
    """把 ICCS 着法序列转成中文记谱（从 pos 开始走），遇到不合法的着法就停止。"""
    p = pos.copy()
    out: list[str] = []
    for text in pv[:limit]:
        try:
            move = parse_iccs(text)
        except NotationError:
            break
        if not p.is_legal(move):
            break
        out.append(move_to_chinese(p.board, move))
        p.push(move)
    return out
