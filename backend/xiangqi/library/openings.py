"""开局识别（自动，仅供参考）。

只识别从标准开局开始的对局，按红方第一步和黑方前几步的应法归类。规则刻意保守，只覆盖最常见、
定义明确的开局；更细的分类（如 ECCO 编码）以后再做。
"""

from __future__ import annotations

from ..core import START_FEN

# 红方第一步 → 开局名称（左右对称的走法归为同一种）
_FIRST_MOVES = {
    "h2e2": "中炮", "b2e2": "中炮",  # 炮二平五 / 炮八平五
    "g3g4": "仙人指路", "c3c4": "仙人指路",  # 兵三进一 / 兵七进一
    "g0e2": "飞相局", "c0e2": "飞相局",  # 相三进五 / 相七进五
    "h0g2": "起马局", "b0c2": "起马局",  # 马二进三 / 马八进七
    "h2d2": "过宫炮", "b2f2": "过宫炮",  # 炮二平六 / 炮八平四
    "h2f2": "仕角炮", "b2d2": "仕角炮",  # 炮二平四 / 炮八平六
}  # fmt: skip

_BLACK_HORSES = {"h9g7", "b9c7"}  # 马8进7、马2进3
_BLACK_CORNER_CANNONS = {"h7f7", "b7d7"}  # 炮8平6、炮2平4（炮到士角）


def classify(initial_fen: str, moves: list[str]) -> str | None:
    """对局（ICCS 着法）的开局名称；不是从标准开局开始或无法归类时返回 None。"""
    if not moves or _board_part(initial_fen) != _board_part(START_FEN):
        return None
    name = _FIRST_MOVES.get(moves[0], "其他开局")
    if name != "中炮" or len(moves) < 2:
        return name

    # 中炮：看黑方的应法
    red_cannon_file = moves[0][0]  # h（炮二平五）或 b（炮八平五）
    black_first = moves[1]
    if black_first in ("h7e7", "b7e7"):  # 黑方第一步也架中炮
        return "顺炮" if black_first[0] == red_cannon_file else "列炮"
    black_moves = set(moves[1:7:2])  # 黑方前三步
    if _BLACK_HORSES <= black_moves:
        return "中炮对反宫马" if black_moves & _BLACK_CORNER_CANNONS else "中炮对屏风马"
    return "中炮"


def _board_part(fen: str) -> str:
    return " ".join(fen.split()[:2])
