"""对局：新建、走子、悔棋。当前阶段对局只保存在内存里，重启后清空。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request

from ..core import (
    RED,
    FenError,
    NotationError,
    Position,
    RuleConfig,
    game_result,
    move_to_chinese,
    move_to_iccs,
    parse_move,
)
from ..core.fen import piece_to_letter
from .schemas import GameView, MoveRecord, MoveRequest, NewGameRequest, PositionView, ResultView


class GameOverError(Exception):
    pass


@dataclass
class Game:
    id: str
    initial_fen: str
    position: Position
    rules: RuleConfig
    records: list[MoveRecord] = field(default_factory=list)

    @classmethod
    def create(cls, fen: str | None, rules: RuleConfig) -> Game:
        position = Position.from_fen(fen) if fen else Position.start()
        return cls(uuid.uuid4().hex[:12], position.fen(), position, rules)

    def result(self):
        return game_result(self.position, self.rules)

    def play(self, text: str) -> None:
        if self.result() is not None:
            raise GameOverError("棋局已经结束")
        pos = self.position
        move = parse_move(pos.board, pos.turn, text)
        record = MoveRecord(iccs=move_to_iccs(move), cn=move_to_chinese(pos.board, move))
        pos.push(move)
        self.records.append(record)

    def undo(self) -> None:
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


def _side_name(side: int) -> str:
    return "red" if side == RED else "black"


router = APIRouter(prefix="/api/games", tags=["对局"])


def _store(request: Request) -> dict[str, Game]:
    return request.app.state.games


def _get(request: Request, game_id: str) -> Game:
    game = _store(request).get(game_id)
    if game is None:
        raise HTTPException(404, "对局不存在（服务重启后内存中的对局会清空）")
    return game


@router.post("", response_model=GameView)
def new_game(body: NewGameRequest, request: Request) -> GameView:
    try:
        game = Game.create(body.fen, request.app.state.config.rules)
    except FenError as e:
        raise HTTPException(400, f"FEN 无效：{e}") from e
    _store(request)[game.id] = game
    return game.view()


@router.get("/{game_id}", response_model=GameView)
def get_game(game_id: str, request: Request) -> GameView:
    return _get(request, game_id).view()


@router.post("/{game_id}/moves", response_model=GameView)
def play_move(game_id: str, body: MoveRequest, request: Request) -> GameView:
    game = _get(request, game_id)
    try:
        game.play(body.move)
    except (NotationError, GameOverError) as e:
        raise HTTPException(400, str(e)) from e
    return game.view()


@router.post("/{game_id}/undo", response_model=GameView)
def undo_move(game_id: str, request: Request) -> GameView:
    game = _get(request, game_id)
    if not game.records:
        raise HTTPException(400, "已经是初始局面，没有可以悔的棋")
    game.undo()
    return game.view()
