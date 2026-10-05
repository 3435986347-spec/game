"""训练：整盘复盘、提示、猜着练习、自动出题、错题本（间隔复习）。"""

from .collect import collect_from_review
from .hints import direction_hint
from .judge import Judgement, close_enough, judge_moves
from .review import (
    BAD_GRADES,
    GRADES,
    MoveGrade,
    PositionEval,
    analyse_positions,
    evaluate_position,
    focus_sides,
    grade_last_move,
    grade_move,
    grade_moves,
    lines_from,
    move_accuracy,
    move_context,
    position_after,
    review_game,
    review_rows,
    summarize,
)
from .store import TrainingStore

__all__ = [
    "BAD_GRADES",
    "GRADES",
    "Judgement",
    "MoveGrade",
    "PositionEval",
    "TrainingStore",
    "analyse_positions",
    "close_enough",
    "collect_from_review",
    "direction_hint",
    "evaluate_position",
    "focus_sides",
    "grade_last_move",
    "grade_move",
    "grade_moves",
    "judge_moves",
    "lines_from",
    "move_accuracy",
    "move_context",
    "position_after",
    "review_game",
    "review_rows",
    "summarize",
]
