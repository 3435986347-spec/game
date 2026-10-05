"""自动出题（docs 5.6、8.1 节）与做题等级分。

出题条件：某个局面里引擎的最佳着法比第二名高出 20% 以上的期望得分（「唯一好棋」），
并且走了之后至少还有 40%——输棋局面里唯一的防守不出成题。棋谱里大师走出了它可以出题，
大师错过了也可以出题，答案都是引擎的着法。吃回对方刚吃掉的子、几乎没得选的局面不出题。
docs 5.6 节的初始值是 15%：Pikafish 的 WDL 很陡，15% 的差距在激烈局面里很常见，题目会太多太杂。

标签由规则引擎判断（杀法、将军、吃子、弃子、捉双、杀棋威胁，以及局面阶段），
难度分按「着法是否安静、是否弃子、几步杀」估一个初始值；做题等级分用 Elo 公式更新。
"""

from __future__ import annotations

from ..core import Position, parse_iccs
from ..core.features import (
    game_phase,
    gives_check,
    mating_moves,
    piece_value,
    threatened,
)
from ..core.movegen import in_check
from ..llm import EngineLine
from .review import PositionEval

UNIQUE_GAP = 0.20
MIN_SCORE = 0.40
_FORCED_MOVES = 3
BASE_RATING = 1200
USER_RATING_KEY = "puzzle_rating"


def puzzle_tags(pos: Position, line: EngineLine) -> list[str]:
    """正解的主题标签。pos 为题目局面，line 为引擎的最佳着法。"""
    b, mover = pos.board, pos.turn
    move = parse_iccs(line.move)
    to = move[1]
    captured = b[to]
    tags: list[str] = []
    if line.mate is not None and line.mate > 0:
        tags += ["杀法", f"{line.mate}步杀"]
    if gives_check(b, move):
        tags.append("将军")
    if captured:
        tags.append("吃子")
    after = pos.copy()
    after.push(move)
    a = after.board
    gained = piece_value(captured, to) if captured else 0.0
    if any(t.square == to for t in threatened(a, mover)) and piece_value(a[to], to) > gained + 0.5:
        tags.append("弃子")  # 走过去的子会被吃掉，吃到的却不值它
    before_targets = {t.square for t in threatened(b, -mover)}
    if len([t for t in threatened(a, -mover) if t.square not in before_targets]) >= 2:
        tags.append("捉双")
    if "杀法" not in tags and not in_check(a, -mover) and mating_moves(a, mover):
        tags.append("杀棋威胁")
    if not tags:
        tags.append("要着")  # 安静的一步：不吃子、不将军
    tags.append(game_phase(pos))
    return tags


def puzzle_rating(tags: list[str], line: EngineLine) -> int:
    """初始难度分：安静着法、弃子、长杀更难；吃子更容易被想到。"""
    rating = BASE_RATING
    if "吃子" not in tags and "将军" not in tags:
        rating += 250
    if "弃子" in tags:
        rating += 250
    if line.mate is not None and line.mate >= 3:
        rating += 150
    if "捉双" in tags:
        rating += 100
    if "吃子" in tags and "弃子" not in tags:
        rating -= 150
    return max(600, min(2400, rating))


def extract_puzzles(
    initial_fen: str, moves: list[str], evals: list[PositionEval], *, game_id: int | None
) -> list[dict]:
    """从一盘棋的复盘结果里找出可以出题的局面。"""
    pos = Position.from_fen(initial_fen, validate=False)
    last_capture: int | None = None
    out: list[dict] = []
    for ply, ev in enumerate(evals):
        if ply > 0:
            move = parse_iccs(moves[ply - 1])
            last_capture = move[1] if pos.board[move[1]] else None
            pos.push(move)
        lines = ev.lines
        if ev.terminal is not None or len(lines) < 2:
            continue
        best, second = lines[0], lines[1]
        if best.score - second.score < UNIQUE_GAP or best.score < MIN_SCORE:
            continue
        if parse_iccs(best.move)[1] == last_capture or len(pos.legal_moves()) <= _FORCED_MOVES:
            continue
        tags = puzzle_tags(pos, best)
        out.append(
            {
                "fen": pos.fen(),
                "solution": best.move,
                "pv": " ".join(best.pv[1:9]),
                "tags": tags,
                "rating": puzzle_rating(tags, best),
                "source_game_id": game_id,
                "source_ply": ply,
                "master_found": int(ply < len(moves) and moves[ply] == best.move),
            }
        )
    return out


def update_rating(user: float, puzzle: float, solved: bool, k: float = 32) -> float:
    """Elo：做对比预期难的题涨得多，做错简单的题掉得多。"""
    expected = 1 / (1 + 10 ** ((puzzle - user) / 400))
    return round(user + k * ((1.0 if solved else 0.0) - expected), 1)
