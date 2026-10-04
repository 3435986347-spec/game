"""人机对战难度。

Pikafish 没有「技能等级 / 限制 Elo」选项，难度由这里控制：
1. 限制搜索量（节点数），级别越低搜得越浅；
2. 用 MultiPV 拿到多个候选着法，按期望得分做 softmax 随机选择：温度越高，越常走次优着法；
3. 只在「比最佳着法差得不多」的候选中选（max_drop），避免走出离谱的着法。
比较的依据见 utility()：一般是走棋方视角的期望得分 0..1（胜 1、和 0.5、负 0，来自引擎的 WDL），
杀棋另外计分，保证高级别不会放着一步杀不走。

各级参数是初始值，需要实际对弈后再调整。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .uci import AnalysisResult, InfoLine, Limit


@dataclass(frozen=True)
class Level:
    level: int
    name: str
    nodes: int | None  # None 表示按时间搜索
    movetime_ms: int | None
    multipv: int
    temperature: float  # 0 表示总是走最佳着法
    max_drop: float  # 候选着法的期望得分最多比最佳低多少

    def limit(self) -> Limit:
        return Limit(nodes=self.nodes, movetime_ms=self.movetime_ms)


LEVELS: dict[int, Level] = {
    lv.level: lv
    for lv in [
        Level(1, "启蒙", 1_000, None, 8, 0.20, 0.60),
        Level(2, "入门", 3_000, None, 7, 0.12, 0.45),
        Level(3, "初级", 8_000, None, 6, 0.08, 0.35),
        Level(4, "进阶", 20_000, None, 5, 0.05, 0.25),
        Level(5, "中级", 50_000, None, 5, 0.035, 0.18),
        Level(6, "中高级", 100_000, None, 4, 0.025, 0.12),
        Level(7, "高级", 250_000, None, 3, 0.015, 0.08),
        Level(8, "业余强手", 600_000, None, 3, 0.008, 0.05),
        Level(9, "大师", 1_500_000, None, 2, 0.003, 0.02),
        Level(10, "全力", None, 2_000, 1, 0.0, 0.0),
    ]
}
MIN_LEVEL, MAX_LEVEL = min(LEVELS), max(LEVELS)


def get_level(level: int) -> Level:
    if level not in LEVELS:
        raise ValueError(f"难度应在 {MIN_LEVEL}–{MAX_LEVEL} 之间")
    return LEVELS[level]


def utility(line: InfoLine) -> float:
    """候选着法对走棋方的价值，用于比较和随机选择。

    通常就是期望得分（0..1）。但大优势时 WDL 会饱和成 1000/0/0，「一步杀」和「两步杀」看起来一样，
    所以杀棋单独计分（越快越好，被杀则越晚越好），并用分数（cp）做极小的平局决胜。
    """
    if line.mate is not None:
        if line.mate > 0:
            return 1.0 + 0.5 / line.mate  # 一步杀 1.5，两步杀 1.25……都高于任何非杀棋着法
        return -0.5 / max(-line.mate, 1)  # 被一步杀 -0.5，被五步杀 -0.1
    return line.expected_score() + (line.score_cp or 0) * 1e-6


def candidate_pool(result: AnalysisResult, level: Level) -> list[tuple[str, float]]:
    """可供选择的候选着法及其价值：只保留比最佳着法差得不多（max_drop 以内）的。"""
    candidates = [(line.move, utility(line)) for line in result.lines if line.pv]
    if not candidates:
        return []
    best = max(u for _, u in candidates)
    return [(move, u) for move, u in candidates if u >= best - level.max_drop]


def choose_move(result: AnalysisResult, level: Level, rng: random.Random) -> str | None:
    """按难度从候选着法中选一步。无子可走时返回 None。"""
    pool = candidate_pool(result, level)
    if not pool:
        return result.bestmove
    best = max(u for _, u in pool)
    if level.temperature <= 0:
        return next(move for move, u in pool if u == best)
    weights = [math.exp((u - best) / level.temperature) for _, u in pool]
    return rng.choices([move for move, _ in pool], weights=weights, k=1)[0]
