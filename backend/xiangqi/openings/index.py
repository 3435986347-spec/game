"""飞刀剧本的合法回放与局面索引：忽略 FEN 计数器，支持换序转置。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..core.board import BLACK, RED
from ..core.notation import move_to_chinese, move_to_iccs, parse_iccs, parse_move
from ..core.position import Position
from .traps import TRAPS, Trap


@dataclass(frozen=True)
class TrapMove:
    ply: int
    cn: str
    iccs: str
    fen: str
    note: str | None = None


@dataclass(frozen=True)
class TrapLine:
    trap: Trap
    moves: tuple[str, ...]
    fens: tuple[str, ...]
    steps: tuple[TrapMove, ...]
    refute: tuple[str, ...]
    refute_steps: tuple[TrapMove, ...]

    @property
    def trap_fen(self) -> str:
        return self.fens[self.trap.trap_ply]

    @property
    def start_fen(self) -> str:
        return self.fens[self.trap.trap_ply - 1]


@dataclass(frozen=True)
class TrapMatch:
    line: TrapLine
    ply: int

    @property
    def trap(self) -> Trap:
        return self.line.trap


TrapIndex = Mapping[str, tuple[TrapMatch, ...]]


def position_key(position: Position | str) -> str:
    """只按棋子位置和走棋方匹配，半回合/回合计数不会妨碍换序匹配。"""
    fen = position.fen() if isinstance(position, Position) else position
    return " ".join(fen.split()[:2])


def _replay(
    position: Position,
    texts: tuple[str, ...],
    start_ply: int,
    notes: tuple[tuple[int, str], ...],
    label: str,
) -> tuple[TrapMove, ...]:
    out = []
    annotations = dict(notes)
    for ply, text in enumerate(texts, start=start_ply + 1):
        try:
            move = parse_move(position.board, position.turn, text)
            cn = move_to_chinese(position.board, move)
            position.push(move)
        except ValueError as e:
            raise ValueError(f"{label} 第 {ply} 步 {text!r} 不合法：{e}") from e
        out.append(TrapMove(ply, cn, move_to_iccs(move), position.fen(), annotations.get(ply)))
    return tuple(out)


def replay_trap(trap: Trap) -> TrapLine:
    """非法教学数据在加载时即报错；破解变从走完弃子那步的局面开始。"""
    if trap.side not in (RED, BLACK):
        raise ValueError(f"飞刀 {trap.id} 执刀方无效")
    if not 1 <= trap.trap_ply < len(trap.moves):
        raise ValueError(f"飞刀 {trap.id} 的陷阱步必须在主变范围内并有后续应着")
    mover = RED if trap.trap_ply % 2 else BLACK
    if mover != trap.side:
        raise ValueError(f"飞刀 {trap.id} 的陷阱步不是执刀方的回合")
    if not trap.refute:
        raise ValueError(f"飞刀 {trap.id} 缺少破解变")
    position = Position.start()
    steps = _replay(position, trap.moves, 0, trap.notes, f"飞刀 {trap.id} 主变")
    fens = (Position.start().fen(), *(step.fen for step in steps))
    refute = _replay(
        Position.from_fen(fens[trap.trap_ply]),
        trap.refute,
        trap.trap_ply,
        trap.refute_notes,
        f"飞刀 {trap.id} 破解变",
    )
    if refute[0].iccs == steps[trap.trap_ply].iccs:
        raise ValueError(f"飞刀 {trap.id} 的破解变与陷阱应着相同")
    return TrapLine(
        trap,
        tuple(step.iccs for step in steps),
        tuple(fens),
        steps,
        tuple(step.iccs for step in refute),
        refute,
    )


def load_lines(traps: Iterable[Trap]) -> dict[str, TrapLine]:
    lines = {}
    for trap in traps:
        if trap.id in lines:
            raise ValueError(f"重复的飞刀 id：{trap.id}")
        lines[trap.id] = replay_trap(trap)
    return lines


TRAP_LINES = load_lines(TRAPS)


def build_trap_index(
    lines: Mapping[str, TrapLine] | None = None,
) -> dict[str, tuple[TrapMatch, ...]]:
    """只标记临近弃子和后续攻杀的局面，普通开局共有着法不会被误报为飞刀。"""
    out: dict[str, list[TrapMatch]] = {}
    for line in (TRAP_LINES if lines is None else lines).values():
        for ply in range(line.trap.trap_ply - 1, len(line.fens)):
            out.setdefault(position_key(line.fens[ply]), []).append(TrapMatch(line, ply))
    return {key: tuple(matches) for key, matches in out.items()}


def _select_match(
    matches: Iterable[TrapMatch], position: Position, preferred_trap_id: str | None
) -> TrapMatch | None:
    candidates = list(matches)
    preferred = [match for match in candidates if match.trap.id == preferred_trap_id]
    if preferred:
        candidates = preferred
    if len({match.trap.id for match in candidates}) != 1:
        # 同局面属于多个变例时，不能把某一条的破解建议当作通用应法。
        return None
    if len(candidates) == 1:
        return candidates[0]

    # 同一主变也可能重复到达同一个局面。历史能明确定位时，保留真实的发生次数；
    # 不依赖 FEN 的回合计数，因为预摆局面和换序转置的计数可能不同。
    history = tuple(move_to_iccs(move) for move in position.moves)
    exact = [
        match
        for match in candidates
        if match.ply == len(history) and match.line.moves[: match.ply] == history
    ]
    if len(exact) == 1:
        return exact[0]
    if history:

        def suffix_length(match: TrapMatch) -> int:
            count = 0
            for actual, recorded in zip(
                reversed(history), reversed(match.line.moves[: match.ply]), strict=False
            ):
                if actual != recorded:
                    break
                count += 1
            return count

        lengths = [(suffix_length(match), match) for match in candidates]
        longest = max(length for length, _ in lengths)
        supported = [match for length, match in lengths if length == longest]
        if longest and len(supported) == 1:
            return supported[0]

    # 只有裸 FEN 时，关键弃子选择优先于稍后回到该局面的普通主变续着。
    decisions = [
        match for match in candidates if match.ply in (match.trap.trap_ply - 1, match.trap.trap_ply)
    ]
    return decisions[0] if len(decisions) == 1 else None


def match_position(
    index: TrapIndex, position: Position, preferred_trap_id: str | None = None
) -> TrapMatch | None:
    return _select_match(index.get(position_key(position), ()), position, preferred_trap_id)


def match_move(
    index: TrapIndex, position: Position, text: str, preferred_trap_id: str | None = None
) -> TrapMatch | None:
    try:
        move = parse_iccs(text)
    except ValueError:
        return None
    if not position.is_legal(move):
        return None
    after = position.copy()
    after.push(move)
    matches = index.get(position_key(after), ())
    # 第 trap_ply - 1 个局面只是供下一步建议使用，不把进入该局面的普通着法标为诱饵。
    before_key, iccs = position_key(position), move_to_iccs(move)
    return _select_match(
        (
            match
            for match in matches
            if match.ply >= match.trap.trap_ply
            and match.line.moves[match.ply - 1] == iccs
            and position_key(match.line.fens[match.ply - 1]) == before_key
        ),
        after,
        preferred_trap_id,
    )
