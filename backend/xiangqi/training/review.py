"""整盘复盘（docs 6.2、6.3 节）：引擎逐个局面分析 → 每步评级 → 准确率、分阶段表现、关键时刻。

评级用「走这步之前的期望得分 − 走这步之后的期望得分」（走棋方视角）衡量。
阈值是初始值，用一段时间后再调整。

两次独立搜索的评估有随机波动（多线程搜索不确定；Pikafish 的 WDL 很陡，±100 分大约就是 80% / 20%），
直接拿「走之前」和「下一个局面」比较，误差会被当成失误。所以：
- 走的着法在走之前那次搜索的候选里（MultiPV 前 3）：直接比较同一次搜索里最佳着法和这步的评估；
- 不在候选里：它不会比最后一个候选好，取「下一个局面的评估」和「最后一个候选」中较低的那个；
- 走了引擎的最佳着法：不算损失。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass

from ..core import RED, Position, RuleConfig, game_result, parse_iccs
from ..core.features import game_phase
from ..engine import AnalysisResult, Limit, UciEngine
from ..llm import EngineLine, ExplainContext, build_context

GRADES = ("妙着", "好棋", "可以", "缓着", "失误", "漏着")
BAD_GRADES = ("缓着", "失误", "漏着")
PHASES = ("开局", "中局", "残局")
KEY_MOMENTS = 3
MULTIPV = 3
# 妙着：走出了「唯一好棋」——最佳着法比第二名高出这么多。docs 6.2 节的初始值是 15%，
# 但 Pikafish 的 WDL 很陡，激烈的局面里 15% 的差距很常见（实测一盘 61 步的棋评出 13 个），
# 所以提高到 25%，并且要求走完后至少还有 40% 的期望得分：输棋局面里找到唯一的防守着法不算妙着
_UNIQUE_GAP = 0.25
_UNIQUE_MIN_SCORE = 0.40
_FORCED_MOVES = 3  # 合法着法不超过这么多时（如被将军只能这样应），找到最佳着法不算妙着


def grade_move(
    drop: float,
    *,
    is_best: bool,
    gap: float | None,
    best_score: float | None = None,
    obvious: bool = False,
) -> str:
    """按期望得分的下降评级。gap 为最佳着法比第二名高出多少（只有一个候选时为 None），
    best_score 为最佳着法的期望得分；obvious 为「显而易见」的着法（吃回对方刚吃掉的子、
    几乎没得选），这种着法再好也不算妙着。"""
    if (
        is_best
        and not obvious
        and gap is not None
        and gap >= _UNIQUE_GAP
        and (best_score is None or best_score >= _UNIQUE_MIN_SCORE)
    ):
        return "妙着"
    if drop <= 0.02:
        return "好棋"
    if drop <= 0.05:
        return "可以"
    if drop <= 0.10:
        return "缓着"
    if drop <= 0.20:
        return "失误"
    return "漏着"


def move_accuracy(drop: float) -> float:
    """单步准确率 0–100：按期望得分下降的百分点换算（Lichess 的经验公式）。"""
    value = 103.1668 * math.exp(-0.04354 * drop * 100) - 3.1669
    return max(0.0, min(100.0, round(value, 2)))


@dataclass
class PositionEval:
    """一个局面的引擎评估。lines 为走棋方视角的候选着法（终局时为空）。"""

    ply: int
    turn: int
    red_win: float
    lines: list[EngineLine]
    terminal: str | None = None  # 终局说明（如「红方胜（将死）」）

    def score_for(self, side: int) -> float:
        return self.red_win if side == RED else 1.0 - self.red_win


def lines_from(result: AnalysisResult) -> list[EngineLine]:
    return [
        EngineLine(line.move, tuple(line.pv), round(line.expected_score(), 4), line.mate)
        for line in result.lines
        if line.pv
    ]


async def analyse_positions(
    engine: UciEngine,
    initial_fen: str,
    moves: list[str],
    *,
    limit: Limit,
    rules: RuleConfig,
    multipv: int = MULTIPV,
    progress: Callable[[int, int], None] | None = None,
) -> list[PositionEval]:
    """逐个局面（开局到终局，共 len(moves) + 1 个）请引擎分析。

    无子可走的局面、以及按规则已经结束的最后一个局面直接按结果计分，不调用引擎。
    中间局面即使按本项目的简化规则判了重复，对局实际还在继续，所以照常分析。
    """
    pos = Position.from_fen(initial_fen, validate=False)
    total = len(moves) + 1
    evals: list[PositionEval] = []
    for ply in range(total):
        if ply > 0:
            pos.push(parse_iccs(moves[ply - 1]))
        final = ply == total - 1
        evals.append(
            await _evaluate(engine, initial_fen, moves[:ply], pos, limit, rules, multipv, final)
        )
        if progress is not None:
            progress(ply + 1, total)
    return evals


async def evaluate_position(
    engine: UciEngine,
    initial_fen: str,
    moves: list[str],
    *,
    limit: Limit,
    rules: RuleConfig,
    multipv: int = MULTIPV,
) -> PositionEval:
    """进行中的对局里的一个局面（初始局面 + moves）：按规则已经结束就按结果计分，否则请引擎分析。"""
    pos = Position.from_fen(initial_fen, validate=False)
    for text in moves:
        pos.push(parse_iccs(text))
    return await _evaluate(engine, initial_fen, moves, pos, limit, rules, multipv, True)


async def _evaluate(
    engine: UciEngine,
    initial_fen: str,
    moves: list[str],
    pos: Position,
    limit: Limit,
    rules: RuleConfig,
    multipv: int,
    final: bool,
) -> PositionEval:
    """final：对局停在这个局面（按规则已经结束的话就是终局）。"""
    ply = len(moves)
    result = game_result(pos, rules) if final or not pos.legal_moves() else None
    if result is not None:
        red = 0.5 if result.winner is None else (1.0 if result.winner == RED else 0.0)
        return PositionEval(ply, pos.turn, red, [], result.text)
    analysis = await engine.analyse(initial_fen, moves, limit=limit, multipv=multipv)
    lines = lines_from(analysis)
    score = lines[0].score if lines else 0.5
    red = score if pos.turn == RED else 1.0 - score
    return PositionEval(ply, pos.turn, round(red, 4), lines)


@dataclass
class MoveGrade:
    ply: int  # 走完这步后的步数（从 1 开始），即这步棋在着法列表中的序号
    move: str
    side: int
    grade: str
    win_before: float  # 走棋方视角：走这步之前（按引擎最佳着法）
    win_after: float  # 走棋方视角：走完这步之后
    is_best: bool  # 是否就是引擎的最佳着法
    phase: str

    @property
    def drop(self) -> float:
        # 走了引擎的最佳着法时，下一个局面评估的波动只是搜索误差，不算损失
        return 0.0 if self.is_best else max(0.0, self.win_before - self.win_after)


def grade_moves(initial_fen: str, moves: list[str], evals: list[PositionEval]) -> list[MoveGrade]:
    pos = Position.from_fen(initial_fen, validate=False)
    out: list[MoveGrade] = []
    last_capture: int | None = None  # 上一步吃子的落点
    for i, text in enumerate(moves):
        out.append(_grade(pos, i + 1, text, evals[i], evals[i + 1], last_capture))
        move = parse_iccs(text)
        last_capture = move[1] if pos.board[move[1]] else None
        pos.push(move)
    return out


def grade_last_move(
    initial_fen: str, moves: list[str], before: PositionEval, after: PositionEval
) -> MoveGrade:
    """只评 moves 的最后一步（边下边分析用）。before / after 为走这步之前 / 之后局面的评估。"""
    pos = Position.from_fen(initial_fen, validate=False)
    last_capture: int | None = None
    for text in moves[:-1]:
        move = parse_iccs(text)
        last_capture = move[1] if pos.board[move[1]] else None
        pos.push(move)
    return _grade(pos, len(moves), moves[-1], before, after, last_capture)


def _grade(
    pos: Position,
    ply: int,
    text: str,
    before: PositionEval,
    after: PositionEval,
    last_capture: int | None,
) -> MoveGrade:
    """pos 为走这步之前的局面；last_capture 为上一步吃子的落点（没吃子为 None）。"""
    mover = pos.turn
    lines = before.lines
    is_best = bool(lines) and lines[0].move == text
    win_before = lines[0].score if lines else before.score_for(mover)
    played = next((line for line in lines if line.move == text), None)
    if after.terminal is not None or not lines:
        win_after = after.score_for(mover)  # 走完就分出胜负：结果是确定的
    elif played is not None:
        win_after = played.score  # 同一次搜索里的评估，和 win_before 可比
    else:
        win_after = min(after.score_for(mover), lines[-1].score)
    gap = lines[0].score - lines[1].score if len(lines) >= 2 else None
    move = parse_iccs(text)
    could_be_unique = is_best and gap is not None and gap >= _UNIQUE_GAP
    # 生成全部合法着法较慢，只在可能评为妙着时才检查是不是「几乎没得选」
    obvious = could_be_unique and (
        move[1] == last_capture or len(pos.legal_moves()) <= _FORCED_MOVES
    )
    grade = MoveGrade(
        ply=ply,
        move=text,
        side=mover,
        grade="",
        win_before=round(win_before, 4),
        win_after=round(win_after, 4),
        is_best=is_best,
        phase=game_phase(pos),
    )
    grade.grade = grade_move(
        grade.drop, is_best=is_best, gap=gap, best_score=win_before, obvious=obvious
    )
    return grade


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def summarize(grades: list[MoveGrade], focus: list[int]) -> dict:
    """准确率、分阶段准确率、各评级数量（按红黑分开），以及关键时刻。

    关键时刻：关注的一方（自己的对局只看自己）评为缓着及以下的步中，损失最大的 3 步。
    """
    stats = {}
    for side, name in ((RED, "red"), (-RED, "black")):
        mine = [g for g in grades if g.side == side]
        stats[name] = {
            "moves": len(mine),
            "accuracy": _mean([move_accuracy(g.drop) for g in mine]),
            "phases": {
                phase: _mean([move_accuracy(g.drop) for g in mine if g.phase == phase])
                for phase in PHASES
            },
            "grades": {grade: sum(1 for g in mine if g.grade == grade) for grade in GRADES},
        }
    candidates = [g for g in grades if g.side in focus and g.grade in BAD_GRADES]
    candidates.sort(key=lambda g: g.drop, reverse=True)
    key_moments = sorted(g.ply for g in candidates[:KEY_MOMENTS])
    return {
        "focus": ["red" if s == RED else "black" for s in focus],
        "stats": stats,
        "key_moments": key_moments,
    }


def focus_sides(record: dict) -> list[int]:
    """关注的一方：自己的对局（标签里写着「我」的一方）只看自己，其余看双方。"""
    red_me, black_me = record.get("red") == "我", record.get("black") == "我"
    if record.get("kind") == "my_game" and red_me != black_me:
        return [RED] if red_me else [-RED]
    return [RED, -RED]


def move_context(
    record: dict,
    ply: int,
    before: list[EngineLine],
    after: list[EngineLine],
    *,
    grade: str | None,
    win_before: float | None,
    win_after: float | None,
    level: str,
    extra_facts: list[str] | None = None,
    extra_allowed: list[tuple[list[int], tuple[int, int]]] | None = None,
) -> ExplainContext:
    """record（initial_fen、moves）中第 ply 步（从 1 开始）的讲解上下文。"""
    pos = position_after(record["initial_fen"], record["moves"], ply - 1)
    return build_context(
        pos,
        parse_iccs(record["moves"][ply - 1]),
        before=before,
        after=after,
        win_before=win_before,
        win_after=win_after,
        grade=grade,
        level=level,
        extra_facts=extra_facts,
        extra_allowed=extra_allowed,
    )


def position_after(initial_fen: str, moves: list[str], ply: int) -> Position:
    """走了前 ply 步之后的局面。"""
    pos = Position.from_fen(initial_fen, validate=False)
    for text in moves[:ply]:
        pos.push(parse_iccs(text))
    return pos


async def review_game(
    engine: UciEngine,
    record: dict,
    *,
    limit: Limit,
    rules: RuleConfig,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[PositionEval], list[MoveGrade], dict]:
    """整盘分析 + 每步评级 + 汇总（不含讲解）。"""
    fen, moves = record["initial_fen"], record["moves"]
    evals = await analyse_positions(engine, fen, moves, limit=limit, rules=rules, progress=progress)
    grades = grade_moves(fen, moves, evals)
    return evals, grades, summarize(grades, focus_sides(record))


def review_rows(
    evals: list[PositionEval], grades: list[MoveGrade], explanations: dict[int, dict]
) -> list[dict]:
    """move_analysis 表的各行：每个局面的评估，加上走到这个局面的那步棋的评级和讲解。"""
    rows = [
        {
            "ply": ev.ply,
            "red_win": ev.red_win,
            "lines": json.dumps([line.to_dict() for line in ev.lines]),
            "terminal": ev.terminal,
        }
        for ev in evals
    ]
    for g in grades:
        rows[g.ply].update(
            move=g.move,
            grade=g.grade,
            win_before=g.win_before,
            win_after=g.win_after,
            is_best=int(g.is_best),
            phase=g.phase,
        )
        if g.ply in explanations:
            rows[g.ply]["explanation"] = json.dumps(explanations[g.ply], ensure_ascii=False)
    return rows
