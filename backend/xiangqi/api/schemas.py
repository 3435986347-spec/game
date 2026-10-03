"""API 的请求与响应结构。"""

from typing import Literal

from pydantic import BaseModel, Field

Side = Literal["red", "black"]


class MoveRecord(BaseModel):
    iccs: str = Field(examples=["h2e2"])
    cn: str = Field(examples=["炮二平五"])


class ResultView(BaseModel):
    winner: Side | None
    reason: str = Field(
        description="checkmate | stalemate | perpetual_check | repetition | move_limit"
    )
    text: str = Field(examples=["红方胜（将死）"])


class PositionView(BaseModel):
    fen: str
    turn: Side
    board: list[str] = Field(
        description="长度 90，下标为 rank * 9 + file；'.' 为空，其余为 FEN 字母"
    )
    legal_moves: list[str] = Field(description="当前全部合法着法（ICCS）；棋局结束时为空")
    last_move: str | None
    in_check: bool
    result: ResultView | None


class GameView(BaseModel):
    id: str
    initial_fen: str
    moves: list[MoveRecord]
    position: PositionView


class NewGameRequest(BaseModel):
    fen: str | None = Field(default=None, description="起始局面，不填为标准开局")


class MoveRequest(BaseModel):
    move: str = Field(description="ICCS（h2e2、H2-E2）或中文记谱（炮二平五）", examples=["h2e2"])
