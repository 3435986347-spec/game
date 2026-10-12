"""弃子飞刀教学：主变/破解、局面匹配和对弈提示。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..core.position import Position
from .index import TRAP_LINES, TrapLine, TrapMatch, build_trap_index, match_move, match_position
from .traps import TRAPS, Trap


@dataclass(frozen=True)
class TrapAdvice:
    kind: Literal["suggest", "warn"]
    id: str
    name: str
    advice: str


def move_note(match: TrapMatch) -> str:
    if 1 <= match.ply <= len(match.line.steps):
        step = match.line.steps[match.ply - 1]
        if step.note:
            return step.note
        return f"{match.trap.name}主变：{step.cn}。{match.trap.principle}"
    return match.trap.principle


def trap_advice(position: Position, match: TrapMatch | None) -> TrapAdvice | None:
    if match is None or match.ply >= len(match.line.moves):
        return None
    trap, line = match.trap, match.line
    if position.turn == trap.side:
        next_move = line.steps[match.ply]
        advice = f"这里可研究「{trap.name}」的{next_move.cn}。"
        advice += next_move.note or trap.principle
        return TrapAdvice("suggest", trap.id, trap.name, advice)
    if match.ply == trap.trap_ply:
        first = line.refute_steps[0]
        advice = f"这是「{trap.name}」的诱饵，先考虑{first.cn}。"
        advice += first.note or trap.principle
    else:
        advice = f"当前局面已进入「{trap.name}」的攻杀主变，先检查将门安全。{trap.principle}"
    return TrapAdvice("warn", trap.id, trap.name, advice)


__all__ = [
    "TRAPS",
    "TRAP_LINES",
    "Trap",
    "TrapAdvice",
    "TrapLine",
    "TrapMatch",
    "build_trap_index",
    "match_move",
    "match_position",
    "move_note",
    "trap_advice",
]
