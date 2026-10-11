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
from ..llm import EngineLine
from ..openings import match_move
from ..training import (
    MoveGrade,
    PositionEval,
    evaluate_position,
    grade_last_move,
    move_context,
    position_after,
)
from .games import Game, run_engine
from .games import _get as get_game
from .schemas import ExplainRequest, ExplanationView, MoveAnalysisView

router = APIRouter(prefix="/api/games", tags=["边下边分析"])

Analysed = tuple[tuple[str, ...], MoveGrade, PositionEval, PositionEval]


def _hint_position_eval(game: Game, history: tuple[str, ...]) -> PositionEval | None:
    """提示搜索（比评级搜索更深）分析过同一局面时，复用它的最佳着法做「走之前」的评估：
    引擎推荐 X、你走了 X，就按同一次搜索比较——不会被更浅的评级搜索误判成失误。"""
    cache = game._hint_cache
    if cache is None or cache[0] != history:
        return None
    best = cache[1].lines[0]
    pos = position_after(game.initial_fen, list(history), len(history))
    score = round(best.expected_score(), 4)
    red = score if pos.turn == RED else 1.0 - score
    return PositionEval(
        ply=len(history),
        turn=pos.turn,
        red_win=round(red, 4),
        lines=[EngineLine(best.move, tuple(best.pv), score, best.mate)],
    )


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
    engine = await run_engine(engines.reviewer)
    evals: list[PositionEval] = []
    for key, need_candidates in ((history[:-1], True), (history, False)):
        # 「走之前」要前 3 个候选（评级用）；「走之后」只要最佳应着（讲解与得分用）：
        # 后者用 MultiPV 1、2/3 的时间搜索——同一段搜索时间全部花在最佳着法上，更准也更快。
        ev = game.position_evals.get(key)
        if ev is None and not need_candidates:
            ev = game.reply_evals.get(key)
        if ev is None and need_candidates:
            ev = _hint_position_eval(game, key)  # 提示分析过 → 同一次搜索，推荐与评级一致
        if ev is None and need_candidates:
            # 走了上一轮「走之后」评估给出的最佳应着：直接复用那一次搜索（同样是一致性保证，
            # 而且省掉一次 MultiPV 3 的重复搜索——走最佳着法是最常见的情况）
            prev = game.reply_evals.get(key)
            if prev is not None and prev.lines and prev.lines[0].move == history[-1]:
                ev = prev
        if ev is None:
            multipv = 3 if need_candidates else 1
            movetime = (
                config.review_movetime_ms
                if need_candidates
                else max(600, config.review_movetime_ms * 2 // 3)
            )
            ev = await run_engine(
                lambda key=key, multipv=multipv, movetime=movetime: evaluate_position(
                    engine,
                    initial_fen,
                    list(key),
                    limit=Limit(movetime_ms=movetime),
                    rules=config.rules,
                    multipv=multipv,
                    cache=request.app.state.analysis_cache,
                )
            )
        target = game.position_evals if need_candidates else game.reply_evals
        target[key] = ev
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
        fen_before=pos.fen(),
    )


@router.post("/{game_id}/analysis/explain", response_model=ExplanationView)
async def explain_move(game_id: str, body: ExplainRequest, request: Request) -> ExplanationView:
    game = get_game(request, game_id)
    history, g, before, after = await _analyse(request, game, body.ply)
    cached = game.move_explanations.get(history)
    if cached is not None:
        return ExplanationView(**cached)
    explainer = request.app.state.explainer
    extra_facts = []
    before_pos = position_after(game.initial_fen, list(history[:-1]), len(history) - 1)
    match = match_move(request.app.state.trap_index, before_pos, g.move)
    if match is not None:
        # 这步属于飞刀剧本：说明它是执刀方的诱饵还是受害者踩进了陷阱，
        # 让引擎评分与「飞刀推荐」之间的关系一目了然（只写飞刀名，不带额外着法，保证白名单校验通过）
        if match.trap.side == g.side:
            extra_facts.append(f"这步是「{match.trap.name}」飞刀的诱饵着法（{match.trap.side_name}执刀弃子设陷阱）。")
        else:
            extra_facts.append(f"这步走进了对方「{match.trap.name}」飞刀的陷阱。")
    ctx = move_context(
        {"initial_fen": game.initial_fen, "moves": list(history)},
        g.ply,
        before.lines,
        after.lines,
        grade=g.grade,
        win_before=g.win_before,
        win_after=g.win_after,
        level=explainer.level,
        extra_facts=extra_facts,
    )
    result = (await explainer.explain(ctx)).to_dict()
    game.move_explanations[history] = result
    return ExplanationView(**result)
