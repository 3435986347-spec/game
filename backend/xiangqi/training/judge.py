"""判断一步棋好不好：猜着、复习错题、做题共用。

先不限着法搜索（MultiPV 3）拿到引擎的前 3 名；
要比较的着法（如你的着法和大师的着法）有不在前 3 名里的，
就用 searchmoves 只搜这几步和引擎的最佳着法，让它们的评估来自同一次搜索、可以直接比较
（两次独立搜索的评估有随机波动，见 review.py 开头的说明）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..engine import Limit, UciEngine
from ..llm import EngineLine
from .review import MULTIPV, lines_from


@dataclass
class Judgement:
    lines: list[EngineLine]  # 不限着法时引擎的前几名（走棋方视角，最好的在前）
    scores: dict[str, float]  # 要比较的着法和引擎最佳着法 → 走棋方期望得分（同一次搜索）

    @property
    def best(self) -> EngineLine | None:
        return self.lines[0] if self.lines else None

    @property
    def best_score(self) -> float | None:
        """引擎最佳着法的评估，和 scores 里其他着法来自同一次搜索。"""
        return self.scores.get(self.best.move) if self.best else None


async def judge_moves(
    engine: UciEngine,
    initial_fen: str,
    moves: Sequence[str],
    candidates: Sequence[str],
    *,
    limit: Limit,
) -> Judgement:
    """在「initial_fen + moves」这个局面里，评估 candidates 中的每一步（ICCS，须合法），
    连同引擎的最佳着法。"""
    wanted = list(dict.fromkeys(candidates))
    result = await engine.analyse(initial_fen, list(moves), limit=limit, multipv=MULTIPV)
    lines = lines_from(result)
    if lines:
        wanted = list(dict.fromkeys([*wanted, lines[0].move]))
    scores = {line.move: line.score for line in lines}
    if any(m not in scores for m in wanted):
        restricted = await engine.analyse(
            initial_fen, list(moves), limit=limit, multipv=len(wanted), searchmoves=wanted
        )
        scores = {line.move: line.score for line in lines_from(restricted)}
    return Judgement(lines, {m: scores[m] for m in wanted if m in scores})


def close_enough(score: float, reference: float, tolerance: float = 0.03) -> bool:
    """score 不比 reference 差超过 tolerance（期望得分）。"""
    return score >= reference - tolerance
