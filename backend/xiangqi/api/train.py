"""训练接口：错题本（间隔复习）和做题（docs 8.1、8.2 节）。

- GET    /api/train/due：今日要复习几题；
- GET    /api/train/summary：今日要复习几题、错题本和题库的规模、做题等级分；
- GET    /api/train/next?kind=：下一道到期的题（不含答案）；
- POST   /api/train/cards/{id}/answer {move | null}：作答，按结果安排下次复习；
- POST   /api/train/cards/{id}/explain：讲解（错着为什么错 / 正解好在哪）；
- GET / POST /api/train/cards，DELETE /api/train/cards/{id}：查看、手动加入、移出错题本；
- GET    /api/train/puzzle?theme=：挑一道题（优先没做过的，难度接近你的等级分）；
- POST   /api/train/puzzles/{id}/attempt {move | null}：做题；做错的加入错题本；
- POST   /api/train/puzzles/{id}/explain。
作答时走的就是正解算对；不是正解时请引擎比较，和正解相差不到 3% 期望得分也算对。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from ..core import NotationError, Position, move_to_chinese, move_to_iccs, parse_iccs, parse_move
from ..core.variation import pv_to_chinese
from ..engine import EngineError, EngineUnavailable, Limit
from ..llm import build_context
from ..training import (
    TrainingStore,
    close_enough,
    evaluate_position,
    grade_move,
    judge_moves,
    lines_from,
)
from ..training.puzzles import BASE_RATING, USER_RATING_KEY, update_rating
from ..training.srs import schedule
from .games import run_engine
from .schemas import (
    AddCardRequest,
    AddCardResult,
    CardItem,
    CardResult,
    CardView,
    ExplanationView,
    MoveAnswerRequest,
    NextCard,
    NextPuzzle,
    PuzzleResult,
    PuzzleView,
    TagCount,
    TrainSummary,
)

router = APIRouter(prefix="/api/train", tags=["训练"])


def _store(request: Request) -> TrainingStore:
    return request.app.state.training


def _limit(request: Request) -> Limit:
    return Limit(movetime_ms=request.app.state.config.review_movetime_ms)


def _position(fen: str) -> Position:
    return Position.from_fen(fen, validate=False)


def _board_fields(fen: str) -> dict:
    """前端棋盘需要的字段：轮到谁、合法着法、是否被将军。"""
    pos = _position(fen)
    return {
        "turn": "red" if pos.turn == 1 else "black",
        "legal_moves": [move_to_iccs(m) for m in pos.legal_moves()],
        "in_check": pos.in_check(),
    }


def _cn(fen: str, move: str | None) -> str | None:
    if not move:
        return None
    return move_to_chinese(_position(fen).board, parse_iccs(move))


def _pv_cn(fen: str, solution: str, pv: str) -> list[str]:
    """正解 + 之后的变化（中文）。"""
    return pv_to_chinese(_position(fen), [solution, *pv.split()], 9)


def _rating(store: TrainingStore) -> float:
    return float(store.get_value(USER_RATING_KEY, str(BASE_RATING)))


def _card_view(card: dict) -> CardView:
    return CardView(
        id=card["id"],
        kind=card["kind"],
        fen=card["fen"],
        **_board_fields(card["fen"]),
        played=card["played"],
        played_cn=_cn(card["fen"], card["played"]),
        source=card["source"],
        source_game_id=card["source_game_id"],
        source_ply=card["source_ply"],
        reps=card["reps"],
        lapses=card["lapses"],
        interval_days=card["interval_days"],
        due_at=card["due_at"],
        created_at=card["created_at"],
    )


async def _read_move(fen: str, text: str | None) -> str | None:
    """把作答（ICCS 或中文）转成 ICCS，并检查合法；null 表示不会。"""
    if not text:
        return None
    pos = _position(fen)
    try:
        return move_to_iccs(parse_move(pos.board, pos.turn, text))
    except NotationError as e:
        raise HTTPException(400, str(e)) from e


async def _is_correct(request: Request, fen: str, move: str | None, solution: str) -> bool:
    """走的就是正解算对；否则请引擎比较，和正解相差不到 3% 也算对（引擎不可用时只认正解）。"""
    if move is None:
        return False
    if move == solution:
        return True
    engines = request.app.state.engines
    if not engines.configured:
        return False
    try:
        engine = await engines.reviewer()
        judgement = await judge_moves(engine, fen, [], [move, solution], limit=_limit(request))
    except (EngineUnavailable, EngineError, TimeoutError):
        return False
    mine, best = judgement.scores.get(move), judgement.scores.get(solution)
    return mine is not None and best is not None and close_enough(mine, best)


async def _explain(request: Request, fen: str, move: str, *, judge_grade: bool) -> dict:
    """讲解 fen 局面里的 move：引擎分析走之前和走之后的局面，交给讲解流水线。
    judge_grade 为 True 时按得分下降给 move 评级（讲解错着），否则不评级（讲解正解）。"""
    engine = await run_engine(request.app.state.engines.reviewer)
    limit, rules = _limit(request), request.app.state.config.rules
    pos = _position(fen)
    before = lines_from(await run_engine(lambda: engine.analyse(fen, [], limit=limit, multipv=3)))
    # 走完就分出胜负（如一步杀）时按结果计分，不调用引擎
    after_eval = await run_engine(
        lambda: evaluate_position(engine, fen, [move], limit=limit, rules=rules, multipv=1)
    )
    after = after_eval.lines
    win_before = before[0].score if before else None
    win_after = after_eval.score_for(pos.turn) if after or after_eval.terminal else None
    grade = None
    if judge_grade and win_before is not None and win_after is not None:
        grade = grade_move(max(0.0, win_before - win_after), is_best=False, gap=None)
    explainer = request.app.state.explainer
    ctx = build_context(
        pos,
        parse_iccs(move),
        before=before,
        after=after,
        win_before=win_before,
        win_after=win_after,
        grade=grade,
        level=explainer.level,
    )
    return (await explainer.explain(ctx)).to_dict()


# ---------------------------------------------------------------------------
# 概况
# ---------------------------------------------------------------------------


@router.get("/due")
def due(request: Request) -> dict[str, int]:
    """今天要复习几题（导航上的数字）。"""
    return {"due": _store(request).due_count()}


@router.get("/summary", response_model=TrainSummary)
def summary(request: Request) -> TrainSummary:
    store = _store(request)
    stats = store.puzzle_stats()
    mistakes = store.due_count(kind="mistake")
    puzzles = store.due_count(kind="puzzle")
    return TrainSummary(
        due=mistakes + puzzles,
        due_mistakes=mistakes,
        due_puzzles=puzzles,
        cards=store.card_count(),
        puzzles=stats["total"],
        puzzles_attempted=stats["attempted"],
        puzzles_solved=stats["solved"],
        rating=_rating(store),
        themes=[TagCount(**t) for t in stats["tags"]],
    )


# ---------------------------------------------------------------------------
# 错题本
# ---------------------------------------------------------------------------


@router.get("/next", response_model=NextCard)
def next_card(request: Request, kind: str | None = Query(None)) -> NextCard:
    store = _store(request)
    card = store.next_due(kind=kind)
    return NextCard(card=_card_view(card) if card else None, due=store.due_count(kind=kind))


@router.post("/cards/{card_id}/answer", response_model=CardResult)
async def answer_card(card_id: int, body: MoveAnswerRequest, request: Request) -> CardResult:
    store = _store(request)
    card = await asyncio.to_thread(store.card, card_id)
    if card is None:
        raise HTTPException(404, "错题本里没有这一题")
    move = await _read_move(card["fen"], body.move)
    correct = await _is_correct(request, card["fen"], move, card["solution"])
    fields = schedule(card, correct)
    await asyncio.to_thread(store.update_card, card_id, fields)
    return CardResult(
        correct=correct,
        move=move,
        move_cn=_cn(card["fen"], move),
        solution=card["solution"],
        solution_cn=_cn(card["fen"], card["solution"]),
        pv_cn=_pv_cn(card["fen"], card["solution"], card["pv"]),
        explanation=card["explanation"],
        interval_days=fields["interval_days"],
        due_at=fields["due_at"],
        due=await asyncio.to_thread(store.due_count),
    )


@router.post("/cards/{card_id}/explain", response_model=ExplanationView)
async def explain_card(card_id: int, request: Request) -> ExplanationView:
    store = _store(request)
    card = await asyncio.to_thread(store.card, card_id)
    if card is None:
        raise HTTPException(404, "错题本里没有这一题")
    if card["explanation"]:
        return ExplanationView(**card["explanation"])
    # 有当时的错着就讲它为什么错（讲解里会给出正确走法），否则讲正解
    if card["played"]:
        result = await _explain(request, card["fen"], card["played"], judge_grade=True)
    else:
        result = await _explain(request, card["fen"], card["solution"], judge_grade=False)
    await asyncio.to_thread(store.set_card_explanation, card_id, result)
    return ExplanationView(**result)


@router.get("/cards", response_model=list[CardItem])
def list_cards(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[CardItem]:
    return [
        CardItem(
            **_card_view(card).model_dump(),
            solution=card["solution"],
            solution_cn=_cn(card["fen"], card["solution"]),
        )
        for card in _store(request).cards(limit=limit, offset=offset)
    ]


@router.post("/cards", response_model=AddCardResult)
def add_card(body: AddCardRequest, request: Request) -> AddCardResult:
    """手动加入错题本（打谱、复盘、边下边分析时点「加入错题本」）。"""
    try:
        pos = Position.from_fen(body.fen)
        solution = parse_iccs(body.solution)
        played = parse_iccs(body.played) if body.played else None
    except (ValueError, NotationError) as e:
        raise HTTPException(400, f"局面或着法无效：{e}") from e
    if not pos.is_legal(solution) or (played is not None and not pos.is_legal(played)):
        raise HTTPException(400, "着法在这个局面里不合法")
    card_id = _store(request).add_card(
        "mistake",
        pos.fen(),
        move_to_iccs(solution),  # 统一写法：作答时按字符串比较
        played=move_to_iccs(played) if played is not None else None,
        explanation=body.explanation.model_dump() if body.explanation else None,
        source=body.source,
        source_game_id=body.source_game_id,
        source_ply=body.source_ply,
    )
    return AddCardResult(card_id=card_id, created=card_id is not None)


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, request: Request) -> dict[str, bool]:
    if not _store(request).delete_card(card_id):
        raise HTTPException(404, "错题本里没有这一题")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# 做题
# ---------------------------------------------------------------------------


@router.get("/puzzle", response_model=NextPuzzle)
def next_puzzle(
    request: Request, theme: str | None = Query(None), exclude: int | None = Query(None)
) -> NextPuzzle:
    store = _store(request)
    rating = _rating(store)
    puzzle = store.pick_puzzle(rating, theme=theme or None, exclude=exclude)
    view = None
    if puzzle:
        view = PuzzleView(
            id=puzzle["id"],
            fen=puzzle["fen"],
            **_board_fields(puzzle["fen"]),
            rating=puzzle["rating"],
            attempts=puzzle["attempts"],
            theme=theme or None,
        )
    return NextPuzzle(puzzle=view, rating=rating)


@router.post("/puzzles/{puzzle_id}/attempt", response_model=PuzzleResult)
async def attempt_puzzle(puzzle_id: int, body: MoveAnswerRequest, request: Request) -> PuzzleResult:
    store = _store(request)
    puzzle = await asyncio.to_thread(store.puzzle, puzzle_id)
    if puzzle is None:
        raise HTTPException(404, "题目不存在")
    fen, solution = puzzle["fen"], puzzle["solution"]
    move = await _read_move(fen, body.move)
    correct = await _is_correct(request, fen, move, solution)
    before = _rating(store)
    after = update_rating(before, puzzle["rating"], correct)

    def save() -> bool:
        store.record_attempt(puzzle_id, correct)
        store.set_value(USER_RATING_KEY, str(after))
        if correct:
            return False
        card = store.add_card(
            "puzzle",
            fen,
            solution,
            pv=puzzle["pv"].split(),
            played=move,
            source=f"做题：第 {puzzle_id} 题（难度 {puzzle['rating']}）",
            source_game_id=puzzle["source_game_id"],
            source_ply=puzzle["source_ply"],
            puzzle_id=puzzle_id,
        )
        return card is not None

    card_added = await asyncio.to_thread(save)
    return PuzzleResult(
        correct=correct,
        move=move,
        move_cn=_cn(fen, move),
        solution=solution,
        solution_cn=_cn(fen, solution),
        pv_cn=_pv_cn(fen, solution, puzzle["pv"]),
        tags=puzzle["tags"],
        puzzle_rating=puzzle["rating"],
        rating_before=before,
        rating_after=after,
        card_added=card_added,
        master_found=None if puzzle["master_found"] is None else bool(puzzle["master_found"]),
        source_game_id=puzzle["source_game_id"],
        source_ply=puzzle["source_ply"],
    )


@router.post("/puzzles/{puzzle_id}/explain", response_model=ExplanationView)
async def explain_puzzle(puzzle_id: int, request: Request) -> ExplanationView:
    puzzle = await asyncio.to_thread(_store(request).puzzle, puzzle_id)
    if puzzle is None:
        raise HTTPException(404, "题目不存在")
    result = await _explain(request, puzzle["fen"], puzzle["solution"], judge_grade=False)
    return ExplanationView(**result)
