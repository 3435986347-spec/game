"""整盘复盘与讲解接口。

复盘以棋谱库中的对局为单位（对弈页的对局先保存进棋谱库）。整盘分析在后台进行，前端轮询进度：
- POST /api/library/games/{id}/review：开始复盘（已有结果且着法没变时直接返回）；
- GET  /api/library/games/{id}/review：状态、进度和报告；
- POST /api/library/games/{id}/review/explain：讲解任意一步
  （用复盘时存下的引擎分析，按需调用大模型）；
- POST /api/games/{id}/review：对弈页的对局先保存到棋谱库，再开始复盘；
- GET  /api/llm：大模型的配置状态。
关键时刻（损失最大的 3 步）在复盘时自动讲解，其余各步在打谱时按需讲解。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request

from ..core import RED, Position, move_to_chinese, parse_iccs
from ..core.variation import pv_to_chinese
from ..engine import EngineError, EngineUnavailable, Limit
from ..library import Library, moves_hash
from ..llm import EngineLine, ExplainService
from ..training import collect_from_review, move_context, review_game, review_rows
from ..training.review import PHASES
from .games import _get as get_live_game
from .games import save_to_library
from .schemas import (
    ExplainRequest,
    ExplanationView,
    GameReviewStarted,
    LLMStatusView,
    ReviewMove,
    ReviewReport,
    ReviewView,
    StartReviewRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["复盘与讲解"])


@dataclass
class ReviewJob:
    game_id: int
    phase: str = "analysing"  # analysing | explaining
    progress: int = 0
    total: int = 0
    error: str | None = None
    done: bool = False
    task: asyncio.Task | None = None  # 保留引用，避免后台任务被回收

    def view(self) -> ReviewView:
        if self.error:
            return ReviewView(game_id=self.game_id, status="error", error=self.error)
        return ReviewView(
            game_id=self.game_id,
            status="running",
            phase=self.phase,
            progress=self.progress,
            total=self.total,
        )


def _library(request: Request) -> Library:
    return request.app.state.library


def _explainer(request: Request) -> ExplainService:
    return request.app.state.explainer


def _jobs(request: Request) -> dict[int, ReviewJob]:
    return request.app.state.review_jobs


# ---------------------------------------------------------------------------
# 后台复盘
# ---------------------------------------------------------------------------


async def _run_review(app, job: ReviewJob, record: dict) -> None:
    library: Library = app.state.library
    explainer: ExplainService = app.state.explainer
    config = app.state.config
    fen, moves = record["initial_fen"], record["moves"]

    def on_progress(done: int, total: int) -> None:
        job.progress, job.total = done, total

    try:
        engine = await app.state.engines.reviewer()
        evals, grades, summary = await review_game(
            engine,
            record,
            limit=Limit(movetime_ms=config.review_movetime_ms),
            rules=config.rules,
            progress=on_progress,
        )

        job.phase, job.progress, job.total = "explaining", 0, len(summary["key_moments"])
        explanations: dict[int, dict] = {}
        for ply in summary["key_moments"]:
            g = grades[ply - 1]
            ctx = move_context(
                record,
                ply,
                evals[ply - 1].lines,
                evals[ply].lines,
                grade=g.grade,
                win_before=g.win_before,
                win_after=g.win_after,
                level=explainer.level,
            )
            explanations[ply] = (await explainer.explain(ctx)).to_dict()
            job.progress += 1
        tags = Counter(t for e in explanations.values() for t in e["tags"] if t not in PHASES)
        summary["tags"] = [{"tag": t, "count": n} for t, n in tags.most_common()]

        await asyncio.to_thread(
            library.save_review,
            job.game_id,
            moves_hash=moves_hash(fen, moves),
            engine=engine.name,
            movetime_ms=config.review_movetime_ms,
            summary=summary,
            rows=review_rows(evals, grades, explanations),
        )
        # 自动出题；自己的对局里的失误加入错题本。复盘结果已经存好，这一步出错只记日志
        try:
            await asyncio.to_thread(
                collect_from_review,
                app.state.training,
                record,
                job.game_id,
                evals,
                grades,
                explanations,
            )
        except Exception:
            logger.exception("复盘后自动出题失败")
    except (EngineUnavailable, EngineError, TimeoutError) as e:
        job.error = f"复盘失败：{e or '引擎响应超时'}"
    except sqlite3.Error as e:
        job.error = f"复盘结果保存失败（棋谱库正忙或无法写入）：{e}"
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("复盘出错")
        job.error = "复盘出错，详见后端日志"
    finally:
        job.done = True
        if job.error is None:
            app.state.review_jobs.pop(job.game_id, None)  # 结果已存库


async def start_review(request: Request, game_id: int, *, force: bool = False) -> ReviewView:
    library = _library(request)
    record = await asyncio.to_thread(library.game_moves, game_id)
    if record is None:
        raise HTTPException(404, "棋谱不存在")
    if not record["moves"]:
        raise HTTPException(400, "这盘棋没有着法，无法复盘")
    jobs = _jobs(request)
    job = jobs.get(game_id)
    if job is not None and not job.done:
        return job.view()
    if not force:
        existing = await _report_view(request, game_id, record)
        if existing is not None:
            return existing
    if not request.app.state.engines.configured:
        raise HTTPException(503, "复盘需要象棋引擎，请先按 README「安装象棋引擎」安装")
    job = ReviewJob(game_id, total=len(record["moves"]) + 1)
    jobs[game_id] = job
    job.task = asyncio.create_task(_run_review(request.app, job, record))
    return job.view()


async def stop_reviews(jobs: dict[int, ReviewJob]) -> None:
    """服务停止时调用：取消进行中的复盘。"""
    running = [job.task for job in jobs.values() if job.task is not None and not job.done]
    for task in running:
        task.cancel()
    await asyncio.gather(*running, return_exceptions=True)


# ---------------------------------------------------------------------------
# 读取复盘结果
# ---------------------------------------------------------------------------


async def _load_review(request: Request, game_id: int, record: dict):
    """(reviews 行, move_analysis 各行)；没有复盘、或对局着法已经变了时返回 None。"""
    library = _library(request)
    stored = await asyncio.to_thread(library.get_review, game_id)
    if stored is None:
        return None
    info, rows = stored
    if info["moves_hash"] != moves_hash(record["initial_fen"], record["moves"]):
        await asyncio.to_thread(library.delete_review, game_id)
        return None
    return info, rows


async def _report_view(request: Request, game_id: int, record: dict) -> ReviewView | None:
    loaded = await _load_review(request, game_id, record)
    if loaded is None:
        return None
    info, rows = loaded
    return ReviewView(game_id=game_id, status="done", report=_report(record, info, rows))


def _lines(row: dict) -> list[EngineLine]:
    return [EngineLine.from_dict(d) for d in json.loads(row["lines"])]


def _report(record: dict, info: dict, rows: list[dict]) -> ReviewReport:
    pos = Position.from_fen(record["initial_fen"], validate=False)
    moves = []
    for i, text in enumerate(record["moves"]):
        row = rows[i + 1]
        before = _lines(rows[i])
        best = before[0] if before else None
        best_pv_cn = pv_to_chinese(pos, list(best.pv), 6) if best else []
        move = parse_iccs(text)
        win_before, win_after = row["win_before"], row["win_after"]
        moves.append(
            ReviewMove(
                ply=i + 1,
                iccs=text,
                cn=move_to_chinese(pos.board, move),
                side="red" if pos.turn == RED else "black",
                grade=row["grade"],
                phase=row["phase"],
                win_before=win_before,
                win_after=win_after,
                drop=0.0 if row["is_best"] else round(max(0.0, win_before - win_after), 4),
                best_move=best.move if best else None,
                best_cn=best_pv_cn[0] if best_pv_cn else None,
                best_pv_cn=best_pv_cn,
                explanation=json.loads(row["explanation"]) if row["explanation"] else None,
            )
        )
        pos.push(move)
    summary = info["summary"]
    return ReviewReport(
        engine=info["engine"],
        movetime_ms=info["movetime_ms"],
        created_at=info["created_at"],
        focus=summary["focus"],
        curve=[row["red_win"] for row in rows],
        terminal=rows[-1]["terminal"],
        moves=moves,
        stats=summary["stats"],
        key_moments=summary["key_moments"],
        tags=summary.get("tags", []),
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/api/llm", response_model=LLMStatusView)
def llm_status(request: Request) -> LLMStatusView:
    return LLMStatusView(**_explainer(request).status())


@router.post("/api/library/games/{game_id}/review", response_model=ReviewView)
async def post_review(
    game_id: int, request: Request, body: StartReviewRequest | None = None
) -> ReviewView:
    return await start_review(request, game_id, force=bool(body and body.force))


@router.get("/api/library/games/{game_id}/review", response_model=ReviewView)
async def get_review(game_id: int, request: Request) -> ReviewView:
    job = _jobs(request).get(game_id)
    if job is not None and (not job.done or job.error):
        return job.view()
    record = await asyncio.to_thread(_library(request).game_moves, game_id)
    if record is None:
        raise HTTPException(404, "棋谱不存在")
    view = await _report_view(request, game_id, record)
    return view or ReviewView(game_id=game_id, status="none")


@router.post("/api/library/games/{game_id}/review/explain", response_model=ExplanationView)
async def explain_move(
    game_id: int, body: ExplainRequest, request: Request, refresh: bool = False
) -> ExplanationView:
    """讲解第 ply 步。已有讲解时直接返回；refresh=true 时重新生成（如刚配置好大模型）。"""
    library = _library(request)
    record = await asyncio.to_thread(library.game_moves, game_id)
    if record is None:
        raise HTTPException(404, "棋谱不存在")
    if body.ply > len(record["moves"]):
        raise HTTPException(400, f"这盘棋只有 {len(record['moves'])} 步")
    loaded = await _load_review(request, game_id, record)
    if loaded is None:
        raise HTTPException(409, "请先复盘这盘棋（讲解要用到复盘时的引擎分析）")
    _, rows = loaded
    row = rows[body.ply]
    if row["explanation"] and not refresh:
        return ExplanationView(**json.loads(row["explanation"]))
    explainer = _explainer(request)
    ctx = move_context(
        record,
        body.ply,
        _lines(rows[body.ply - 1]),
        _lines(row),
        grade=row["grade"],
        win_before=row["win_before"],
        win_after=row["win_after"],
        level=explainer.level,
    )
    result = (await explainer.explain(ctx, use_cache=not refresh)).to_dict()
    try:
        await asyncio.to_thread(library.set_explanation, game_id, body.ply, result)
    except sqlite3.Error:
        logger.exception("保存讲解失败")  # 讲解照样返回，只是下次要重新生成
    return ExplanationView(**result)


@router.post("/api/games/{game_id}/review", response_model=GameReviewStarted)
async def review_live_game(game_id: str, request: Request) -> GameReviewStarted:
    """对弈页的对局：先保存（或更新）到棋谱库，再开始复盘。"""
    game = get_live_game(request, game_id)
    async with game.lock:
        if not game.records:
            raise HTTPException(400, "还没有走棋，无法复盘")
        # 每次都保存：已保存过的就原地更新（着法没变时复盘结果仍然有效），
        # 棋谱库里那一局被删掉了则另存一局
        try:
            library_id = await asyncio.to_thread(save_to_library, game, _library(request))
        except sqlite3.Error as e:
            raise HTTPException(503, f"保存失败（棋谱库正忙或无法写入）：{e}") from e
    await start_review(request, library_id)
    return GameReviewStarted(library_id=library_id)
