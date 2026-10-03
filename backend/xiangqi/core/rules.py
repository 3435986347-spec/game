"""胜负判定：将死、困毙、长将、重复局面、自然限着。

长捉判定（连续捉对方无根子）规则复杂，暂未实现：除长将外的重复局面一律判和。
"""

from __future__ import annotations

from dataclasses import dataclass

from .board import BLACK, RED
from .position import Position

_SIDE_NAME = {RED: "红方", BLACK: "黑方"}
_REASON_TEXT = {
    "checkmate": "将死",
    "stalemate": "困毙",
    "perpetual_check": "长将",
    "repetition": "重复局面",
    "move_limit": "自然限着",
}


@dataclass(frozen=True)
class RuleConfig:
    move_limit: int = 60  # 连续多少回合（双方各走一步为一回合）无吃子判和；0 表示不限
    repetition_count: int = 3  # 同一局面出现几次进入重复判定


@dataclass(frozen=True)
class GameResult:
    winner: int | None  # RED / BLACK；None 为和棋
    reason: str  # checkmate | stalemate | perpetual_check | repetition | move_limit

    @property
    def text(self) -> str:
        reason = _REASON_TEXT[self.reason]
        if self.winner is None:
            return f"和棋（{reason}）"
        if self.reason == "perpetual_check":
            return f"{_SIDE_NAME[self.winner]}胜（{_SIDE_NAME[-self.winner]}长将）"
        return f"{_SIDE_NAME[self.winner]}胜（{reason}）"


def game_result(pos: Position, cfg: RuleConfig | None = None) -> GameResult | None:
    """当前局面的胜负；棋局未结束时返回 None。"""
    cfg = cfg or RuleConfig()
    if not pos.legal_moves():
        # 无子可走：被将军为将死，否则为困毙。两种情况都是走棋方负。
        return GameResult(-pos.turn, "checkmate" if pos.in_check() else "stalemate")

    repetition = _repetition_result(pos, cfg)
    if repetition is not None:
        return repetition

    if cfg.move_limit and pos.halfmove_clock >= cfg.move_limit * 2:
        return GameResult(None, "move_limit")
    return None


def _repetition_result(pos: Position, cfg: RuleConfig) -> GameResult | None:
    keys = pos.keys
    n = len(keys) - 1
    current = keys[n]
    # 吃子之后的局面不可能和吃子之前的重复，只需往回看 halfmove_clock 步；
    # 走棋方相同的局面才可能相同，所以步长为 2。
    earliest = max(n - pos.halfmove_clock, 0)
    previous = [i for i in range(n - 2, earliest - 1, -2) if keys[i] == current]
    if len(previous) + 1 < cfg.repetition_count:
        return None

    # 看最近一次循环（上一次出现到现在）里，每一方是否每一步都在将军
    start = previous[0]
    all_checks = {RED: True, BLACK: True}
    for i in range(start, n):
        mover = pos.turn if (n - i) % 2 == 0 else -pos.turn
        if not pos.gave_check(i):
            all_checks[mover] = False
    if all_checks[RED] and not all_checks[BLACK]:
        return GameResult(BLACK, "perpetual_check")
    if all_checks[BLACK] and not all_checks[RED]:
        return GameResult(RED, "perpetual_check")
    return GameResult(None, "repetition")
