"""边下边分析（走一步分析一步）：对弈中每走一步，马上给这步评级、指出更好的走法，并可以讲解。

- POST /api/games/{id}/analysis      {ply}：第 ply 步的评级（和整盘复盘用同样的评级方法）；
- POST /api/games/{id}/analysis/explain {ply}：讲解第 ply 步（大模型或模板，和复盘共用讲解缓存）。
要分析哪些步由前端决定：人机对战只分析你自己的着法（分析 AI 的着法等于提示你哪里有机会），
自由对弈每步都分析。引擎用复盘的那个实例，不会拖慢 AI 走棋和实时分析；
局面评估按着法历史缓存在对局里，悔棋后再走回来不用重算。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..core import RED, Position, move_to_chinese, parse_iccs
from ..core.variation import pv_to_chinese
from ..engine import Limit
from ..training import MoveGrade, PositionEval, evaluate_position, grade_last_move
from .games import Game, run_engine
from .games import _get as get_game
from .review import move_context
from .schemas import ExplainRequest, ExplanationView, MoveAnalysisView

router = APIRouter(prefix="/api/games", tags=["边下边分析"])

Analysed = tuple[tuple[str, ...], MoveGrade, PositionEval, PositionEval]


async def _analyse(request: Request, game: Game, ply: int) -> Analysed:
    """(到这步为止的着法, 评级, 走之前的局面评估, 走之后的局面评估)。"""
    async with game.lock:  # 只取一份着法快照，分析期间不占着锁（AI 可以照常走棋）
        if not 1 <= ply <= len(game.records):
            raise HTTPException(400, f"第 {ply} 步不存在（可能已经悔棋）")
        history = tuple(game.move_list[:ply])
        initial_fen = game.initial_fen
    engines = request.app.state.engines
    if not engines.configured:
        raise HTTPException(503, "边下边分析需要象棋引擎，请先按 README「安装象棋引擎」安装")
    config = request.app.state.config
    limit = Limit(movetime_ms=config.review_movetime_ms)
    engine = await run_engine(engines.reviewer)
    evals: list[PositionEval] = []
    for key in (history[:-1], history):
        ev = game.position_evals.get(key)
        if ev is None:
            ev = await run_engine(
                lambda key=key: evaluate_position(
                    engine, initial_fen, list(key), limit=limit, rules=config.rules
                )
            )
            game.position_evals[key] = ev
        evals.append(ev)
    grade = grade_last_move(initial_fen, list(history), evals[0], evals[1])
    return history, grade, evals[0], evals[1]


@router.post("/{game_id}/analysis", response_model=MoveAnalysisView)
async def analyse_move(game_id: str, body: ExplainRequest, request: Request) -> MoveAnalysisView:
    game = get_game(request, game_id)
    history, g, before, after = await _analyse(request, game, body.ply)
    pos = Position.from_fen(game.initial_fen, validate=False)
    for text in history[:-1]:
        pos.push(parse_iccs(text))
    best = before.lines[0] if before.lines else None
    best_pv_cn = pv_to_chinese(pos, list(best.pv), 6) if best else []
    return MoveAnalysisView(
        ply=g.ply,
        iccs=g.move,
        cn=move_to_chinese(pos.board, parse_iccs(g.move)),
        side="red" if g.side == RED else "black",
        grade=g.grade,
        phase=g.phase,
        win_before=g.win_before,
        win_after=g.win_after,
        drop=round(g.drop, 4),
        best_move=best.move if best else None,
        best_cn=best_pv_cn[0] if best_pv_cn else None,
        best_pv_cn=best_pv_cn,
        explanation=game.move_explanations.get(history),
        red_win=after.red_win,
        terminal=after.terminal,
    )


@router.post("/{game_id}/analysis/explain", response_model=ExplanationView)
async def explain_move(game_id: str, body: ExplainRequest, request: Request) -> ExplanationView:
    game = get_game(request, game_id)
    history, g, before, after = await _analyse(request, game, body.ply)
    cached = game.move_explanations.get(history)
    if cached is not None:
        return ExplanationView(**cached)
    explainer = request.app.state.explainer
    ctx = move_context(
        {"initial_fen": game.initial_fen, "moves": list(history)},
        g.ply,
        before.lines,
        after.lines,
        grade=g.grade,
        win_before=g.win_before,
        win_after=g.win_after,
        level=explainer.level,
    )
    result = (await explainer.explain(ctx)).to_dict()
    game.move_explanations[history] = result
    return ExplanationView(**result)
