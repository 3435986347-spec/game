"""棋谱库接口：导入、检索、单局详情（打谱）、局面统计。

数据库查询都很快（百毫秒以内），接口写成普通函数，由 FastAPI 放在线程池中执行，不阻塞实时分析。
导入在后台线程中进行，前端轮询进度。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from ..core import FenError
from ..library import ImportReport, Library, decode_bytes, iter_games
from .schemas import (
    ExplorerView,
    GameRecordView,
    ImportJobView,
    ImportStarted,
    LibraryStats,
    OpeningCount,
    SearchResult,
)

router = APIRouter(prefix="/api/library", tags=["棋谱库"])


@dataclass
class ImportJob:
    job_id: str
    filename: str
    report: ImportReport = field(default_factory=ImportReport)
    done: bool = False
    error: str | None = None
    task: asyncio.Task | None = None  # 保留引用，避免后台任务被回收

    def view(self) -> ImportJobView:
        r = self.report
        return ImportJobView(
            job_id=self.job_id,
            filename=self.filename,
            done=self.done,
            games_seen=r.games_seen,
            imported=r.imported,
            duplicates=r.duplicates,
            failed=r.failed,
            errors=list(r.errors),
            error=self.error,
        )


def _library(request: Request) -> Library:
    return request.app.state.library


def _run_import(library: Library, job: ImportJob, path: Path) -> None:
    try:
        text = decode_bytes(path.read_bytes())
        library.import_games(iter_games(text), source=job.filename, report=job.report)
    except Exception as e:  # 文件无法读取等：整个任务失败，原因显示给用户
        job.error = f"导入失败：{e}"
    finally:
        job.done = True
        path.unlink(missing_ok=True)


@router.post("/import", response_model=ImportStarted)
async def start_import(request: Request, filename: str = Query("棋谱")) -> ImportStarted:
    """请求体是原始文件内容（application/octet-stream）。在后台导入，用返回的 job_id 查询进度。"""
    fd, tmp = tempfile.mkstemp(prefix="xiangqi-import-", suffix=Path(filename).suffix)
    size = 0
    with os.fdopen(fd, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            size += len(chunk)
    if size == 0:
        Path(tmp).unlink(missing_ok=True)
        raise HTTPException(400, "文件是空的")
    job = ImportJob(uuid.uuid4().hex[:12], Path(filename).name)
    request.app.state.import_jobs[job.job_id] = job
    job.task = asyncio.create_task(
        asyncio.to_thread(_run_import, _library(request), job, Path(tmp))
    )
    return ImportStarted(job_id=job.job_id)


@router.get("/import/{job_id}", response_model=ImportJobView)
def import_status(job_id: str, request: Request) -> ImportJobView:
    job = request.app.state.import_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "导入任务不存在（服务重启后会清空）")
    return job.view()


@router.get("/stats", response_model=LibraryStats)
def stats(request: Request) -> LibraryStats:
    return LibraryStats(**_library(request).stats())


@router.get("/games", response_model=SearchResult)
def search(
    request: Request,
    q: str | None = None,
    event: str | None = None,
    opening: str | None = None,
    result: str | None = None,
    kind: str | None = None,
    fen: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> SearchResult:
    try:
        total, items = _library(request).search(
            q=q or None,
            event=event or None,
            opening=opening or None,
            result=result or None,
            kind=kind or None,
            fen=fen or None,
            page=page,
            page_size=page_size,
        )
    except FenError as e:
        raise HTTPException(400, f"FEN 无效：{e}") from e
    return SearchResult(total=total, page=page, page_size=page_size, items=items)


@router.get("/games/{game_id}", response_model=GameRecordView)
def get_game(game_id: int, request: Request) -> GameRecordView:
    record = _library(request).get(game_id)
    if record is None:
        raise HTTPException(404, "棋谱不存在")
    return GameRecordView(**record)


@router.delete("/games/{game_id}")
def delete_game(game_id: int, request: Request) -> dict[str, bool]:
    if not _library(request).delete(game_id):
        raise HTTPException(404, "棋谱不存在")
    return {"deleted": True}


@router.get("/explorer", response_model=ExplorerView)
def explorer(request: Request, fen: str) -> ExplorerView:
    try:
        return ExplorerView(**_library(request).explorer(fen))
    except FenError as e:
        raise HTTPException(400, f"FEN 无效：{e}") from e


@router.get("/openings", response_model=list[OpeningCount])
def openings(request: Request) -> list[OpeningCount]:
    return [OpeningCount(**row) for row in _library(request).openings()]
