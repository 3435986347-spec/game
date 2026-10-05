"""名局 AI 解读（docs 5.5 节）：在整盘复盘的基础上，推断关键着法的意图，再做分阶段总结。

1. 转折点：期望得分变化最大的几步（至少 10%），加上妙着，最多 6 步。
2. 每个转折点交给大模型（或模板）的事实：引擎主变化（复盘时已有）、走前走后的特征对比（规则引擎），
   以及空着法威胁——假设对方「停一步不走」，让引擎看走棋方下一步最想走什么，这就是这步棋制造的威胁
   （实现方法是把局面的走棋方翻转后交给引擎；对方正被将军时局面不合法，跳过这一项）。
3. 分阶段总结：开局名称、双方各阶段的准确率、转折点和它们的意图。
"""

from __future__ import annotations

from ..core import Position, format_fen, move_to_chinese, parse_iccs
from ..core.features import SIDE_NAME, describe, gives_check
from ..core.movegen import in_check
from ..engine import Limit, UciEngine
from ..llm import EngineLine, SummaryContext
from ..llm.validate import find_moves, move_key
from .guess import side_to_move_at
from .review import PHASES, lines_from

MAX_POINTS = 6
MIN_SWING = 0.10
MAX_BRILLIANT = 2
_RESULT_TEXT = {"1-0": "红方胜", "0-1": "黑方胜", "1/2-1/2": "和棋", "*": "棋局没有下完"}


def turning_points(curve: list[float], grades: dict[int, str]) -> list[int]:
    """转折点（着法序号，从小到大）：红方期望得分变化最大的几步，加上妙着。"""
    swings = sorted(((abs(curve[p] - curve[p - 1]), p) for p in range(1, len(curve))), reverse=True)
    big = [p for swing, p in swings if swing >= MIN_SWING]
    brilliant = [p for p in sorted(grades) if grades[p] == "妙着"][:MAX_BRILLIANT]
    chosen = [p for p in big if p not in brilliant][: MAX_POINTS - len(brilliant)]
    return sorted(chosen + brilliant)


async def null_move_threat(
    engine: UciEngine, after: Position, mover: int, *, limit: Limit
) -> EngineLine | None:
    """空着法：after 是 mover 走完之后的局面，假设对方停一步，mover 再走一步时引擎最想走什么。"""
    if in_check(after.board, -mover):
        return None  # 对方正被将军，不能「停一步」
    fen = format_fen(after.board, mover, after.halfmove_clock, after.fullmove)
    lines = lines_from(await engine.analyse(fen, [], limit=limit, multipv=1))
    return lines[0] if lines else None


def threat_fact(after: Position, mover: int, threat: EngineLine) -> tuple[str, tuple[int, int]]:
    """空着法威胁的说明，以及要加进白名单的着法。"""
    move = parse_iccs(threat.move)
    b = after.board
    cn = move_to_chinese(b, move)
    text = f"如果{SIDE_NAME[-mover]}停一步不走，{SIDE_NAME[mover]}最想走{cn}"
    if threat.mate is not None and threat.mate > 0:
        text += f"，{threat.mate}步之内就能将死对方"
    elif b[move[1]]:
        text += f"，吃掉{SIDE_NAME[-mover]}的{describe(b, move[1])}"
    elif gives_check(b, move):
        text += "，将军"
    return text, move


def build_summary_context(
    record: dict,
    stats: dict,
    points: list[dict],
    *,
    level: str,
) -> SummaryContext:
    """points：[{ply, side, cn, grade, phase, red_before, red_after, intent}]；
    stats：复盘汇总里的 red / black 各阶段准确率。"""
    side_name = {"red": "红方", "black": "黑方"}
    accuracy_data = {}
    for side in ("red", "black"):
        s = stats[side]
        accuracy_data[side_name[side]] = {
            "总体": s["accuracy"],
            **{p: s["phases"].get(p) for p in PHASES if s["phases"].get(p) is not None},
        }
    accuracy_text = {}
    for phase in PHASES:
        red, black = stats["red"]["phases"].get(phase), stats["black"]["phases"].get(phase)
        if red is not None or black is not None:
            accuracy_text[phase] = (
                f"红 {round(red) if red is not None else '—'} · "
                f"黑 {round(black) if black is not None else '—'}"
            )
    phase_points: dict[str, list[str]] = {}
    allowed: list[str] = []
    keys: set[str] = set()
    for p in points:
        desc = (
            f"第 {p['ply']} 步{side_name[p['side']]}{p['cn']}（{p['grade']}，"
            f"红方期望得分 {round(p['red_before'] * 100)}% → {round(p['red_after'] * 100)}%）"
        )
        phase_points.setdefault(p["phase"], []).append(desc)
        for move in [p["cn"], *find_moves(p.get("intent") or "")]:
            if move_key(move) not in keys:
                keys.add(move_key(move))
                allowed.append(move)
    result_text = _RESULT_TEXT.get(record.get("result") or "*", "结果未知")
    data = {
        "player": {"level": level},
        "game": {
            "red": record.get("red") or "红方",
            "black": record.get("black") or "黑方",
            "event": record.get("event"),
            "result": result_text,
            "opening": record.get("opening"),
            "plies": len(record["moves"]),
        },
        "accuracy": accuracy_data,
        "turning_points": [
            {
                "ply": p["ply"],
                "side": side_name[p["side"]],
                "move": p["cn"],
                "grade": p["grade"],
                "phase": p["phase"],
                "red_win_before": round(p["red_before"], 2),
                "red_win_after": round(p["red_after"], 2),
                "intent": p.get("intent"),
            }
            for p in points
        ],
        "allowed_moves_cn": allowed,
    }
    return SummaryContext(
        data=data,
        allowed_keys=keys,
        level=level,
        opening=record.get("opening"),
        result_text=result_text,
        phase_points=phase_points,
        accuracy=accuracy_text,
    )


def side_of_ply(initial_fen: str, ply: int) -> str:
    """第 ply 步（从 1 开始）是哪一方走的。"""
    return side_to_move_at(initial_fen, ply - 1)
