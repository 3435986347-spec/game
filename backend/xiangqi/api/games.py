"""对局：新建、走子、悔棋、AI 走棋、提示。对局只保存在内存里，重启后清空。"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request

from ..core import (
    BLACK,
    RED,
    FenError,
    NotationError,
    Position,
    RuleConfig,
    game_result,
    move_to_chinese,
    move_to_iccs,
    parse_iccs,
    parse_move,
)
from ..core.fen import piece_to_letter
from ..core.notation import BLACK_NAME, RED_NAME
from ..engine import (
    AnalysisResult,
    EngineError,
    EngineService,
    EngineUnavailable,
    Limit,
    choose_move,
    get_level,
)
from ..library import Library, my_game_headers
from .schemas import (
    GameView,
    HintRequest,
    HintView,
    MoveRecord,
    MoveRequest,
    NewGameRequest,
    PositionView,
    ResultView,
    SavedGame,
)

logger = logging.getLogger(__name__)


class GameError(Exception):
    """对局状态不允许这个操作（如棋局已结束、不是你的回合），message 直接显示给用户。"""


@dataclass
class Game:
    id: str
    initial_fen: str
    position: Position
    rules: RuleConfig
    mode: str = "free"  # free | vs_ai
    ai_side: int | None = None  # 人机对战时 AI 执哪一方
    ai_level: int | None = None
    records: list[MoveRecord] = field(default_factory=list)
    hints_used: int = 0
    library_id: int | None = None  # 保存到棋谱库后的 id
    saved_ply: int | None = None  # 保存时走到第几步（避免重复保存）
    rng: random.Random = field(default_factory=random.Random)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # 同一对局的操作依次执行
    # 提示的分析结果，按「完整着法历史」缓存：同一局面、同一历史才复用
    _hint_cache: tuple[tuple[str, ...], AnalysisResult] | None = None

    @classmethod
    def create(cls, body: NewGameRequest, rules: RuleConfig) -> Game:
        """新对局。body.moves 是从起始局面先走的着法（从棋谱某一步开始时保留历史）。"""
        position = Position.from_fen(body.fen) if body.fen else Position.start()
        game = cls(uuid.uuid4().hex[:12], position.fen(), position, rules, mode=body.mode)
        for ply, text in enumerate(body.moves, start=1):
            try:
                move = parse_iccs(text)
            except NotationError as e:
                raise GameError(f"第 {ply} 步 {text} 不是 ICCS 着法") from e
            if not position.is_legal(move):
                raise GameError(f"第 {ply} 步 {text} 不合法")
            game.records.append(
                MoveRecord(iccs=move_to_iccs(move), cn=move_to_chinese(position.board, move))
            )
            position.push(move)
        if body.mode == "vs_ai":
            game.ai_side = BLACK if body.user_side == "red" else RED
            game.ai_level = body.ai_level
        return game

    @property
    def move_list(self) -> list[str]:
        return [r.iccs for r in self.records]

    def result(self):
        return game_result(self.position, self.rules)

    @property
    def ai_to_move(self) -> bool:
        return self.mode == "vs_ai" and self.position.turn == self.ai_side and self.result() is None

    def play(self, text: str) -> None:
        """玩家走一步。"""
        if self.ai_to_move:
            raise GameError("现在轮到 AI 走棋")
        self._apply(text, by_ai=False)

    def play_ai(self, iccs: str) -> None:
        self._apply(iccs, by_ai=True)

    def _apply(self, text: str, *, by_ai: bool) -> None:
        if self.result() is not None:
            raise GameError("棋局已经结束")
        pos = self.position
        move = parse_move(pos.board, pos.turn, text)
        record = MoveRecord(
            iccs=move_to_iccs(move), cn=move_to_chinese(pos.board, move), by_ai=by_ai
        )
        pos.push(move)
        self.records.append(record)

    def undo(self) -> None:
        """悔棋。人机对战时撤回到轮到你走，并且至少撤掉你自己的一步。"""
        count = 1
        if self.mode == "vs_ai" and self.records and self.records[-1].by_ai:
            count = 2
        if count > len(self.records):
            raise GameError("没有可以悔的棋")
        for _ in range(count):
            self.position.pop()
            self.records.pop()

    def view(self) -> GameView:
        pos = self.position
        result = self.result()
        result_view = None
        if result is not None:
            winner = None if result.winner is None else _side_name(result.winner)
            result_view = ResultView(winner=winner, reason=result.reason, text=result.text)
        return GameView(
            id=self.id,
            initial_fen=self.initial_fen,
            mode=self.mode,  # type: ignore[arg-type]
            user_side=None if self.ai_side is None else _side_name(-self.ai_side),
            ai_level=self.ai_level,
            ai_to_move=self.ai_to_move,
            hints_used=self.hints_used,
            library_id=self.library_id,
            moves=self.records,
            position=PositionView(
                fen=pos.fen(),
                turn=_side_name(pos.turn),
                board=[piece_to_letter(p) if p else "." for p in pos.board],
                legal_moves=[] if result else [move_to_iccs(m) for m in pos.legal_moves()],
                last_move=self.records[-1].iccs if self.records else None,
                in_check=pos.in_check(),
                result=result_view,
            ),
        )


def save_to_library(game: Game, library: Library) -> int:
    """把对局保存（或更新）到棋谱库，返回库中的 id。"""
    result = game.result()
    code = "*"
    if result is not None:
        code = {RED: "1-0", BLACK: "0-1", None: "1/2-1/2"}[result.winner]
    user_side = None if game.ai_side is None else _side_name(-game.ai_side)
    headers = my_game_headers(mode=game.mode, ai_level=game.ai_level, user_side=user_side)
    game.library_id = library.save_game(
        initial_fen=game.initial_fen,
        moves=game.move_list,
        result=code,
        headers=headers,
        library_id=game.library_id,
    )
    game.saved_ply = len(game.records)
    return game.library_id


def _autosave(request: Request, game: Game) -> None:
    """棋局结束时自动保存到棋谱库（悔棋后再下完会更新同一条记录）。"""
    if game.result() is not None and game.saved_ply != len(game.records):
        try:
            save_to_library(game, request.app.state.library)
        except sqlite3.Error:
            logger.exception("自动保存对局失败")


def _side_name(side: int) -> str:
    return "red" if side == RED else "black"


def pv_to_chinese(pos: Position, pv: list[str], limit: int = 8) -> list[str]:
    """把引擎主要变化（ICCS）转成中文记谱，遇到不合法的着法就停止。"""
    p = pos.copy()
    out: list[str] = []
    for text in pv[:limit]:
        try:
            move = parse_iccs(text)
        except NotationError:
            break
        if not p.is_legal(move):
            break
        out.append(move_to_chinese(p.board, move))
        p.push(move)
    return out


def red_expected(score: float, turn: int) -> float:
    """走棋方视角的期望得分 → 红方视角。"""
    return score if turn == RED else 1.0 - score


async def run_engine(coro_factory):
    """调用引擎，把不可用 / 出错转换成 503，信息直接给用户看。"""
    try:
        return await coro_factory()
    except (EngineUnavailable, EngineError) as e:
        raise HTTPException(503, str(e)) from e


router = APIRouter(prefix="/api/games", tags=["对局"])


def _store(request: Request) -> dict[str, Game]:
    return request.app.state.games


def _engines(request: Request) -> EngineService:
    return request.app.state.engines


def _get(request: Request, game_id: str) -> Game:
    game = _store(request).get(game_id)
    if game is None:
        raise HTTPException(404, "对局不存在（服务重启后内存中的对局会清空）")
    return game


@router.post("", response_model=GameView)
async def new_game(body: NewGameRequest, request: Request) -> GameView:
    try:
        game = Game.create(body, request.app.state.config.rules)
    except FenError as e:
        raise HTTPException(400, f"FEN 无效：{e}") from e
    except GameError as e:
        raise HTTPException(400, str(e)) from e
    _store(request)[game.id] = game
    return game.view()


@router.get("/{game_id}", response_model=GameView)
async def get_game(game_id: str, request: Request) -> GameView:
    game = _get(request, game_id)
    async with game.lock:  # 等正在进行的 AI 走棋完成，避免返回走到一半的状态
        return game.view()


@router.post("/{game_id}/moves", response_model=GameView)
async def play_move(game_id: str, body: MoveRequest, request: Request) -> GameView:
    game = _get(request, game_id)
    async with game.lock:
        try:
            game.play(body.move)
        except (NotationError, GameError) as e:
            raise HTTPException(400, str(e)) from e
        _autosave(request, game)
        return game.view()


@router.post("/{game_id}/undo", response_model=GameView)
async def undo_move(game_id: str, request: Request) -> GameView:
    game = _get(request, game_id)
    async with game.lock:
        try:
            game.undo()
        except GameError as e:
            raise HTTPException(400, str(e)) from e
        return game.view()


@router.post("/{game_id}/save", response_model=SavedGame)
async def save_game(game_id: str, request: Request) -> SavedGame:
    """手动把（未下完的）对局保存到棋谱库；再次保存会更新同一条记录。"""
    game = _get(request, game_id)
    async with game.lock:
        return SavedGame(library_id=save_to_library(game, request.app.state.library))


@router.post("/{game_id}/ai-move", response_model=GameView)
async def ai_move(game_id: str, request: Request) -> GameView:
    """人机对战中让 AI 走一步（按对局的难度级别）。"""
    game = _get(request, game_id)
    async with game.lock:
        if not game.ai_to_move:
            raise HTTPException(400, "现在不是 AI 走棋")
        level = get_level(game.ai_level or 1)
        engine = await run_engine(_engines(request).player)
        result = await run_engine(
            lambda: engine.analyse(
                game.initial_fen, game.move_list, limit=level.limit(), multipv=level.multipv
            )
        )
        move = choose_move(result, level, game.rng)
        if move is None:
            raise HTTPException(500, "引擎没有给出着法")
        try:
            game.play_ai(move)
            _autosave(request, game)
        except NotationError as e:
            raise HTTPException(
                503, f"引擎给出了不合法的着法 {move}，请检查 config.toml 中 [engine] 的 flavor"
            ) from e
        return game.view()


@router.post("/{game_id}/hint", response_model=HintView)
async def hint(game_id: str, body: HintRequest, request: Request) -> HintView:
    """2 级提示：该动哪个子；3 级提示：具体着法、主要变化和走完后的胜率。"""
    game = _get(request, game_id)
    async with game.lock:
        if game.result() is not None:
            raise HTTPException(400, "棋局已经结束")
        if game.ai_to_move:
            raise HTTPException(400, "现在轮到 AI 走棋")
        pos = game.position
        history = tuple(game.move_list)
        if game._hint_cache is not None and game._hint_cache[0] == history:
            analysis = game._hint_cache[1]
        else:
            engines = _engines(request)
            engine = await run_engine(engines.player)
            movetime = engines.config.hint_movetime_ms if engines.config else 1000
            analysis = await run_engine(
                lambda: engine.analyse(
                    game.initial_fen, game.move_list, limit=Limit(movetime_ms=movetime), multipv=1
                )
            )
        if not analysis.lines or not _is_legal_text(pos, analysis.lines[0].move):
            game._hint_cache = None
            raise HTTPException(
                503, "引擎没有给出合法的着法，请检查 config.toml 中 [engine] 的 flavor"
            )
        game._hint_cache = (history, analysis)
        best = analysis.lines[0]
        game.hints_used += 1
        return _hint_view(pos, best, body.level)


def _is_legal_text(pos: Position, text: str) -> bool:
    try:
        return pos.is_legal(parse_iccs(text))
    except NotationError:
        return False


def _hint_view(pos: Position, best, level: int) -> HintView:
    from_square = best.move[:2]
    piece = pos.board[parse_iccs(best.move)[0]]
    name = (RED_NAME if piece > 0 else BLACK_NAME)[abs(piece)]
    if level == 2:
        return HintView(level=2, from_square=from_square, text=f"提示：想想这个{name}可以怎么走。")
    pv_cn = pv_to_chinese(pos, best.pv) or [move_to_chinese(pos.board, parse_iccs(best.move))]
    red_win = red_expected(best.expected_score(), pos.turn)
    side_win = red_win if pos.turn == RED else 1 - red_win
    text = f"引擎推荐：{pv_cn[0]}。"
    if len(pv_cn) > 1:
        text += f"后续可能：{' '.join(pv_cn[:5])}。"
    text += f"走完后{'红' if pos.turn == RED else '黑'}方期望得分约 {side_win:.0%}。"
    return HintView(
        level=3,
        from_square=from_square,
        move=best.move,
        cn=pv_cn[0],
        pv_cn=pv_cn,
        red_win=round(red_win, 4),
        text=text,
    )
