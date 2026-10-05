"""名局 AI 解读接口（docs 5.5 节）：要先复盘。在后台推断关键着法的意图，再做分阶段总结，结果存库。

- POST /api/library/games/{id}/annotate {force}：开始（已有解读时直接返回，force 时重做）；
- GET  /api/library/games/{id}/annotate：状态、进度和解读。
解读依赖复盘结果：重新复盘或对局着法改了之后，旧的解读一并作废。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request

from ..core import BLACK, RED
from ..engine import EngineError, EngineUnavailable, Limit
from ..library import Library
from ..llm import EngineLine
from ..training import move_context, position_after
from ..training.annotate import (
    build_summary_context,
    null_move_threat,
    side_of_ply,
    threat_fact,
    turning_points,
)
from .review import _load_review
from .schemas import AnnotatedMove, AnnotateView, StartReviewRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library/games", tags=["名局解读"])


@dataclass
class AnnotateJob:
    game_id: int
    progress: int = 0
    total: int = 0
    error: str | None = None
    done: bool = False
    task: asyncio.Task | None = None

    def view(self) -> AnnotateView:
        if self.error:
            return AnnotateView(game_id=self.game_id, status="error", error=self.error)
        return AnnotateView(
            game_id=self.game_id, status="running", progress=self.progress, total=self.total
        )


def _library(request: Request) -> Library:
    return request.app.state.library


def _lines(row: dict) -> list[EngineLine]:
    return [EngineLine.from_dict(d) for d in json.loads(row["lines"])]


def _stored_view(game_id: int, items: list[dict]) -> AnnotateView:
    summary = next((i["content"] for i in items if i["kind"] == "summary"), None)
    moves = []
    for item in items:
        if item["kind"] != "intent":
            continue
        content = dict(item["content"])
        meta = content.pop("meta")
        moves.append(AnnotatedMove(ply=item["ply"], intent=content, **meta))
    return AnnotateView(game_id=game_id, status="done", summary=summary, moves=moves)


async def _run(app, job: AnnotateJob, record: dict, info: dict, rows: list[dict]) -> None:
    config, explainer = app.state.config, app.state.explainer
    fen, moves = record["initial_fen"], record["moves"]
    try:
        curve = [row["red_win"] for row in rows]
        points = turning_points(curve, {row["ply"]: row["grade"] for row in rows[1:]})
        job.total = len(points) + 1
        engine = await app.state.engines.reviewer()
        limit = Limit(movetime_ms=config.review_movetime_ms)
        items: list[tuple[int, str, dict]] = []
        summary_points = []
        for ply in points:
            row, before = rows[ply], rows[ply - 1]
            side = side_of_ply(fen, ply)
            mover = RED if side == "red" else BLACK
            after = position_after(fen, moves, ply)
            extra_facts, extra_allowed = [], []
            threat = await null_move_threat(engine, after, mover, limit=limit)
            if threat is not None:
                text, threat_move = threat_fact(after, mover, threat)
                extra_facts.append(text)
                extra_allowed.append((after.board, threat_move))
            ctx = move_context(
                record,
                ply,
                _lines(before),
                _lines(row),
                grade=row["grade"],
                win_before=row["win_before"],
                win_after=row["win_after"],
                level=explainer.level,
                extra_facts=extra_facts,
                extra_allowed=extra_allowed,
            )
            intent = (await explainer.explain(ctx, task="intent")).to_dict()
            meta = {
                "side": side,
                "cn": ctx.move_cn,
                "grade": row["grade"],
                "phase": row["phase"],
                "red_before": before["red_win"],
                "red_after": row["red_win"],
            }
            items.append((ply, "intent", {**intent, "meta": meta}))
            summary_points.append({**meta, "ply": ply, "intent": intent["headline"]})
            job.progress += 1
        summary_ctx = build_summary_context(
            record,
            info["summary"]["stats"],
            summary_points,
            level=explainer.level,
        )
        summary = (await explainer.explain(summary_ctx, task="summary")).to_dict()
        items.append((0, "summary", summary))
        job.progress += 1
        saved = await asyncio.to_thread(
            app.state.library.save_annotations,
            job.game_id,
            items,
            reviewed_at=info["created_at"],
        )
        if not saved:
            job.error = "解读期间这盘棋重新复盘了，请重新解读"
    except (EngineUnavailable, EngineError, TimeoutError) as e:
        job.error = f"解读失败：{e or '引擎响应超时'}"
    except sqlite3.Error as e:
        job.error = f"解读结果保存失败：{e}"
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("名局解读出错")
        job.error = "解读出错，详见后端日志"
    finally:
        job.done = True
        if job.error is None:
            app.state.annotate_jobs.pop(job.game_id, None)


@router.post("/{game_id}/annotate", response_model=AnnotateView)
async def start_annotate(
    game_id: int, request: Request, body: StartReviewRequest | None = None
) -> AnnotateView:
    library = _library(request)
    record = await asyncio.to_thread(library.game_moves, game_id)
    if record is None:
        raise HTTPException(404, "棋谱不存在")
    jobs: dict[int, AnnotateJob] = request.app.state.annotate_jobs
    job = jobs.get(game_id)
    if job is not None and not job.done:
        return job.view()
    loaded = await _load_review(request, game_id, record)
    if loaded is None:
        raise HTTPException(409, "请先复盘这盘棋（解读要用到复盘时的引擎分析）")
    if not (body and body.force):
        items = await asyncio.to_thread(library.get_annotations, game_id)
        if items:
            return _stored_view(game_id, items)
    if not request.app.state.engines.configured:
        raise HTTPException(503, "解读需要象棋引擎，请先按 README「安装象棋引擎」安装")
    info, rows = loaded
    job = AnnotateJob(game_id)
    jobs[game_id] = job
    job.task = asyncio.create_task(_run(request.app, job, record, info, rows))
    return job.view()


@router.get("/{game_id}/annotate", response_model=AnnotateView)
async def get_annotate(game_id: int, request: Request) -> AnnotateView:
    job = request.app.state.annotate_jobs.get(game_id)
    if job is not None and (not job.done or job.error):
        return job.view()
    library = _library(request)
    record = await asyncio.to_thread(library.game_moves, game_id)
    if record is None:
        raise HTTPException(404, "棋谱不存在")
    if await _load_review(request, game_id, record) is None:  # 着法改过：复盘和解读都作废
        return AnnotateView(game_id=game_id, status="none")
    items = await asyncio.to_thread(library.get_annotations, game_id)
    return _stored_view(game_id, items) if items else AnnotateView(game_id=game_id, status="none")
