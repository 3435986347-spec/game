"""引擎状态与实时分析（评估条）。

实时分析通过 WebSocket /ws/analysis 进行：
- 客户端发送 {"type": "start", "game_id": "...", "multipv": 3} 开始分析该对局的当前局面，
  或 {"type": "start", "fen": "...", "moves": [...], "multipv": 3} 分析任意局面（如打谱时）；
  再次发送 start 会停止上一次分析、开始新的；发送 {"type": "stop"} 停止。
- 服务端推送 {"type": "info", "game_id", "ply", "fen", "depth", "lines": [...]}，
  每条 line 含 move、cn（中文）、pv_cn、red_win（红方期望得分 0..1）、score_cp / mate（红方视角）。
  出错时推送 {"type": "error", "message": "..."}。
分析引擎全局只有一个，新的分析请求（包括其他浏览器标签页的）会打断正在进行的分析。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import aclosing
from dataclasses import dataclass

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..core import RED, FenError, NotationError, Position, parse_iccs
from ..core.variation import pv_to_chinese
from ..engine import LEVELS, EngineError, EngineService, EngineUnavailable, InfoLine
from .games import Game, red_expected
from .schemas import EngineStatusView, LevelView

router = APIRouter(tags=["引擎"])
logger = logging.getLogger(__name__)

_MIN_INTERVAL = 0.25  # 两次推送的最小间隔（秒）
_MAX_STALE = 1.0  # 候选着法迟迟凑不齐同一深度时，最多隔这么久也推送一次


@router.get("/api/engine", response_model=EngineStatusView)
async def engine_status(request: Request) -> EngineStatusView:
    status = await request.app.state.engines.status()
    levels = [LevelView(level=lv.level, name=lv.name) for lv in LEVELS.values()]
    return EngineStatusView(levels=levels, **status)


@router.websocket("/ws/analysis")
async def analysis_socket(ws: WebSocket) -> None:
    await ws.accept()
    app = ws.app
    task: asyncio.Task[None] | None = None
    try:
        while True:
            message = await ws.receive_json()
            await _cancel(task)
            task = None
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "start":
                try:
                    target = _target(app, message)
                except ValueError as e:
                    await ws.send_json({"type": "error", "message": str(e)})
                    continue
                multipv = max(1, min(int(message.get("multipv", 3)), 5))
                await _preempt_other(app, ws)
                task = asyncio.create_task(_stream(ws, app.state.engines, target, multipv))
                app.state.analysis_task, app.state.analysis_socket = task, ws
            elif kind == "stop":
                await ws.send_json({"type": "stopped"})
            else:
                await ws.send_json({"type": "error", "message": f"未知的消息类型：{kind!r}"})
    except WebSocketDisconnect:
        pass
    finally:
        await _cancel(task)


async def _preempt_other(app, ws: WebSocket) -> None:
    """分析引擎只有一个：其他连接（如另一个浏览器标签页）正在分析时，停掉它并告诉它原因。"""
    other_task = app.state.analysis_task
    other_ws = app.state.analysis_socket
    if other_task is None or other_ws is ws or other_task.done():
        return
    await _cancel(other_task)
    await _send_error(other_ws, "实时分析已在另一个页面打开，这里暂停了")


async def _cancel(task: asyncio.Task[None] | None) -> None:
    """停止分析任务并等它结束（已结束的任务也 await 一次，取走其中的异常）。"""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


@dataclass(frozen=True)
class _Target:
    """要分析的局面：起始局面 + 着法（保留历史，引擎才能判断重复局面）。"""

    game_id: str | None
    initial_fen: str
    moves: list[str]
    position: Position  # 走完 moves 之后的局面


def _target(app, message: dict) -> _Target:
    """start 消息里的分析对象：进行中的对局（game_id），或任意局面（fen + moves）。"""
    if message.get("game_id") is not None:
        game: Game | None = app.state.games.get(message.get("game_id"))
        if game is None:
            raise ValueError("对局不存在")
        # 记下请求时的局面；之后对局再变化，客户端会发来新的 start
        return _Target(game.id, game.initial_fen, game.move_list, game.position.copy())
    fen = message.get("fen")
    if not isinstance(fen, str):
        raise ValueError("缺少 game_id 或 fen")
    moves = message.get("moves") or []
    try:
        position = Position.from_fen(fen)
        for text in moves:
            move = parse_iccs(str(text))
            if not position.is_legal(move):
                raise ValueError(f"着法 {text} 不合法")
            position.push(move)
    except (FenError, NotationError) as e:
        raise ValueError(f"局面无效：{e}") from e
    return _Target(None, Position.from_fen(fen).fen(), [str(m) for m in moves], position)


async def _stream(ws: WebSocket, engines: EngineService, target: _Target, multipv: int) -> None:
    initial_fen, moves, position = target.initial_fen, target.moves, target.position
    ply = len(moves)
    if not position.legal_moves():  # 将死或困毙：没有可分析的着法，直接告诉前端
        await ws.send_json(_info_message(target.game_id, ply, position, []))
        return
    try:
        engine = await engines.analyser()
        loop = asyncio.get_running_loop()
        last_sent = 0.0
        async with aclosing(engine.analyse_infinite(initial_fen, moves, multipv=multipv)) as gen:
            async for lines in gen:
                # 引擎逐条输出同一深度的各个候选；凑齐（深度一致）再推送，避免显示新旧混杂的结果
                complete = all(line.depth == lines[0].depth for line in lines)
                elapsed = loop.time() - last_sent
                if (complete and elapsed >= _MIN_INTERVAL) or elapsed >= _MAX_STALE:
                    last_sent = loop.time()
                    await ws.send_json(_info_message(target.game_id, ply, position, lines))
    except (EngineUnavailable, EngineError) as e:
        await _send_error(ws, str(e))
    except WebSocketDisconnect:
        pass
    except Exception:  # 意外错误也要告诉前端，否则界面会一直等待
        logger.exception("实时分析出错")
        await _send_error(ws, "实时分析出错，详见后端日志")


async def _send_error(ws: WebSocket, message: str) -> None:
    with contextlib.suppress(Exception):
        await ws.send_json({"type": "error", "message": message})


def _info_message(game_id: str | None, ply: int, position: Position, lines: list[InfoLine]) -> dict:
    sign = 1 if position.turn == RED else -1
    out = []
    for line in lines:
        pv_cn = pv_to_chinese(position, line.pv, limit=10)
        out.append(
            {
                "move": line.move,
                "cn": pv_cn[0] if pv_cn else line.move,
                "pv_cn": pv_cn,
                "red_win": round(red_expected(line.expected_score(), position.turn), 4),
                "score_cp": None if line.score_cp is None else sign * line.score_cp,
                "mate": None if line.mate is None else sign * line.mate,
                "depth": line.depth,
            }
        )
    return {
        "type": "info",
        "game_id": game_id,
        "ply": ply,
        "fen": position.fen(),
        "depth": lines[0].depth if lines else 0,
        "lines": out,
    }
