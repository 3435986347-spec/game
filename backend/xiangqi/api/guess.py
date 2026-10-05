"""猜着练习接口（docs 5.4 节）。

- POST   /api/guess                {game_id, side, skip_plies}：开始，局面停在第一个轮到你猜的地方；
- GET    /api/guess / {id}：最近的练习 / 一次练习的进度；
- POST   /api/guess/{id}/answer    {move | null}：猜一步（null 为不会，直接看大师着法）。
  引擎比较你的着法和大师的着法（同一次搜索），按 docs 5.4 节的表打分；0 分附讲解。
  然后棋局沿大师的着法继续（大师这步和对方的下一步），停在下一个轮到你的地方；
- DELETE /api/guess/{id}。
猜完时，失分最多的 3 步（得分不超过 1）加入错题本：正确着法是大师的着法，
「此处大师着法也非最佳」时用引擎推荐的着法。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ..core import NotationError, move_to_chinese, move_to_iccs, parse_iccs, parse_move
from ..engine import Limit
from ..library import Library
from ..llm import EngineLine
from ..training import (
    TrainingStore,
    grade_move,
    judge_moves,
    lines_from,
    move_context,
    position_after,
)
from ..training.guess import MASTER_NOT_BEST, first_turn, guess_points, worst_answers
from .games import run_engine
from .schemas import (
    GuessAnswerResult,
    GuessAnswerView,
    GuessSessionItem,
    GuessSummary,
    GuessView,
    MoveAnswerRequest,
    MoveRecord,
    StartGuessRequest,
)

router = APIRouter(prefix="/api/guess", tags=["猜着练习"])

_SIDE_TEXT = {"red": "红", "black": "黑"}


def _store(request: Request) -> TrainingStore:
    return request.app.state.training


def _library(request: Request) -> Library:
    return request.app.state.library


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


async def _load(request: Request, session_id: int) -> tuple[dict, dict]:
    session = await asyncio.to_thread(_store(request).session, session_id)
    if session is None:
        raise HTTPException(404, "猜着练习不存在")
    record = await asyncio.to_thread(_library(request).game_moves, session["game_id"])
    if record is None:
        raise HTTPException(404, "这盘棋已经从棋谱库删除了")
    return session, record


def _answer_view(row: dict) -> GuessAnswerView:
    d = row["detail"]
    return GuessAnswerView(
        ply=row["ply"],
        fen_before=d["fen_before"],
        user_move=row["user_move"],
        user_cn=d.get("user_cn"),
        master_move=row["master_move"],
        master_cn=d["master_cn"],
        points=row["points"],
        loss=row["loss"],
        same=d.get("same", False),
        as_good=d.get("as_good", False),
        master_not_best=d.get("master_not_best", False),
        best_move=d.get("best_move"),
        best_cn=d.get("best_cn"),
        user_score=d.get("user_score"),
        master_score=d.get("master_score"),
        explanation=d.get("explanation"),
    )


def _view(session: dict, record: dict, answers: list[dict]) -> GuessView:
    fen, moves = record["initial_fen"], record["moves"]
    current = session["current_ply"]
    pos = position_after(fen, moves, 0)
    played: list[MoveRecord] = []
    for text in moves[:current]:
        move = parse_iccs(text)
        played.append(MoveRecord(iccs=text, cn=move_to_chinese(pos.board, move)))
        pos.push(move)
    summary = None
    if session["finished"]:
        scored = [a for a in answers if a["loss"] is not None]
        matched = sum(1 for a in answers if a["user_move"] == a["master_move"])
        summary = GuessSummary(
            answered=len(answers),
            matched=matched,
            match_rate=round(matched / len(answers), 3) if answers else None,
            avg_loss=round(sum(a["loss"] for a in scored) / len(scored), 4) if scored else None,
            worst=[a["ply"] for a in worst_answers(answers)],
            cards_added=session["cards_added"],
        )
    return GuessView(
        id=session["id"],
        game_id=session["game_id"],
        red=record.get("red"),
        black=record.get("black"),
        event=record.get("event"),
        side=session["side"],
        start_ply=session["start_ply"],
        current_ply=current,
        total_plies=len(moves),
        score=session["score"],
        max_score=session["max_score"],
        finished=bool(session["finished"]),
        initial_fen=fen,
        fen=pos.fen(),
        last_move=moves[current - 1] if current > 0 else None,
        in_check=pos.in_check(),
        legal_moves=[] if session["finished"] else [move_to_iccs(m) for m in pos.legal_moves()],
        moves=played,
        answers=[_answer_view(a) for a in answers],
        summary=summary,
    )


async def _full_view(request: Request, session_id: int) -> GuessView:
    session, record = await _load(request, session_id)
    answers = await asyncio.to_thread(_store(request).answers, session_id)
    return _view(session, record, answers)


@router.post("", response_model=GuessView)
async def start_guess(body: StartGuessRequest, request: Request) -> GuessView:
    record = await asyncio.to_thread(_library(request).game_moves, body.game_id)
    if record is None:
        raise HTTPException(404, "棋谱不存在")
    if not request.app.state.engines.configured:
        raise HTTPException(503, "猜着练习要用引擎打分，请先按 README「安装象棋引擎」安装")
    start = first_turn(record["initial_fen"], record["moves"], body.side, body.skip_plies)
    if start is None:
        raise HTTPException(
            400, f"跳过 {body.skip_plies} 步之后，这盘棋没有{_SIDE_TEXT[body.side]}方的着法可猜了"
        )
    session_id = await asyncio.to_thread(
        _store(request).create_session, body.game_id, body.side, start
    )
    return await _full_view(request, session_id)


@router.get("", response_model=list[GuessSessionItem])
def list_guesses(request: Request) -> list[GuessSessionItem]:
    return [
        GuessSessionItem(
            id=s["id"],
            game_id=s["game_id"],
            red=s["red"],
            black=s["black"],
            event=s["event"],
            side=s["side"],
            score=s["score"],
            max_score=s["max_score"],
            finished=bool(s["finished"]),
            current_ply=s["current_ply"],
            total_plies=s["ply_count"],
            created_at=s["created_at"],
        )
        for s in _store(request).sessions()
    ]


@router.get("/{session_id}", response_model=GuessView)
async def get_guess(session_id: int, request: Request) -> GuessView:
    return await _full_view(request, session_id)


@router.delete("/{session_id}")
def delete_guess(session_id: int, request: Request) -> dict[str, bool]:
    if not _store(request).delete_session(session_id):
        raise HTTPException(404, "猜着练习不存在")
    return {"deleted": True}


@router.post("/{session_id}/answer", response_model=GuessAnswerResult)
async def answer_guess(
    session_id: int, body: MoveAnswerRequest, request: Request
) -> GuessAnswerResult:
    locks: dict[int, asyncio.Lock] = request.app.state.guess_locks
    async with locks.setdefault(session_id, asyncio.Lock()):  # 同一次练习的作答依次处理
        session, record = await _load(request, session_id)
        if session["finished"]:
            raise HTTPException(400, "这盘已经猜完了")
        fen, moves = record["initial_fen"], record["moves"]
        index = session["current_ply"]
        pos = position_after(fen, moves, index)
        master = moves[index]
        user = None
        if body.move:
            try:
                user = move_to_iccs(parse_move(pos.board, pos.turn, body.move))
            except NotationError as e:
                raise HTTPException(400, str(e)) from e
        detail = await _judge(request, record, index, user, master)
        points = detail.pop("points")
        loss = detail.pop("loss")
        answer = {
            "ply": index + 1,
            "user_move": user,
            "master_move": master,
            "points": points,
            "loss": loss,
            "detail": detail,
        }
        # 棋局沿大师的着法继续：大师这步和对方的下一步，停在下一个轮到你的地方
        nxt = index + 2
        finished = nxt >= len(moves)
        store = _store(request)
        await asyncio.to_thread(
            store.save_answer,
            session_id,
            answer,
            current_ply=min(nxt, len(moves)),
            points=points,
            finished=finished,
        )
        if finished:
            await asyncio.to_thread(
                _add_worst_to_cards, store, session_id, record, session["game_id"]
            )
        view = await _full_view(request, session_id)
        return GuessAnswerResult(answer=_answer_view(answer), session=view)


async def _judge(request: Request, record: dict, index: int, user: str | None, master: str) -> dict:
    """比较你的着法和大师的着法，算分；0 分时生成讲解。返回 answer 的 detail（含 points、loss）。"""
    fen, moves = record["initial_fen"], record["moves"]
    pos = position_after(fen, moves, index)
    master_cn = move_to_chinese(pos.board, parse_iccs(master))
    detail: dict = {"fen_before": pos.fen(), "master_cn": master_cn}
    if user is None:
        return {**detail, "points": 0, "loss": None}
    detail["user_cn"] = move_to_chinese(pos.board, parse_iccs(user))
    engines, config = request.app.state.engines, request.app.state.config
    limit = Limit(movetime_ms=config.review_movetime_ms)
    engine = await run_engine(engines.reviewer)
    history = moves[:index]
    judgement = await run_engine(
        lambda: judge_moves(engine, fen, history, [user, master], limit=limit)
    )
    user_score, master_score = judgement.scores.get(user), judgement.scores.get(master)
    best = judgement.best
    same = user == master
    if user_score is None or master_score is None:
        # 引擎没给出评估：只能按是否相同打分
        return {**detail, "same": same, "points": 3 if same else 0, "loss": None}
    points = guess_points(user_score, master_score, same=same)
    loss = 0.0 if same else round(max(0.0, master_score - user_score), 4)
    best_score = judgement.best_score  # 和 master_score 来自同一次搜索
    master_not_best = (
        best is not None
        and best.move != master
        and best_score is not None
        and best_score - master_score >= MASTER_NOT_BEST
    )
    detail.update(
        same=same,
        as_good=not same and points == 3,
        master_not_best=master_not_best,
        user_score=round(user_score, 4),
        master_score=round(master_score, 4),
    )
    if best is not None:
        detail.update(
            best_move=best.move,
            best_cn=move_to_chinese(pos.board, parse_iccs(best.move)),
            best_pv=list(best.pv[1:9]),
        )
    if points == 0:
        after = lines_from(
            await run_engine(lambda: engine.analyse(fen, [*history, user], limit=limit, multipv=1))
        )
        before = list(judgement.lines[:2])
        if master not in [line.move for line in before]:
            before.append(EngineLine(master, (master,), master_score))
        explainer = request.app.state.explainer
        ctx = move_context(
            {"initial_fen": fen, "moves": [*history, user]},
            index + 1,
            before,
            after,
            grade=grade_move(loss, is_best=False, gap=None),
            win_before=master_score,
            win_after=user_score,
            level=explainer.level,
            extra_facts=[f"大师走的是{master_cn}（走完后期望得分约{_pct(master_score)}）"],
        )
        detail["explanation"] = (await explainer.explain(ctx)).to_dict()
    return {**detail, "points": points, "loss": loss}


def _add_worst_to_cards(store: TrainingStore, session_id: int, record: dict, game_id: int) -> None:
    """猜完：失分最多的几步加入错题本。会访问数据库，在线程中调用。"""
    title = f"{record.get('red') or '红方'} vs {record.get('black') or '黑方'}"
    added = 0
    for a in worst_answers(store.answers(session_id)):
        d = a["detail"]
        use_best = d.get("master_not_best") and d.get("best_move")
        card = store.add_card(
            "mistake",
            d["fen_before"],
            d["best_move"] if use_best else a["master_move"],
            pv=d.get("best_pv", []) if use_best or d.get("best_move") == a["master_move"] else [],
            played=a["user_move"],
            explanation=d.get("explanation"),
            source=f"猜着：{title} 第 {a['ply']} 步",
            source_game_id=game_id,
            source_ply=a["ply"],
        )
        added += card is not None
    store.set_cards_added(session_id, added)
