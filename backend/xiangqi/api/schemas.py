"""API 的请求与响应结构。"""

from typing import Literal

from pydantic import BaseModel, Field

Side = Literal["red", "black"]
Mode = Literal["free", "vs_ai"]


class MoveRecord(BaseModel):
    iccs: str = Field(examples=["h2e2"])
    cn: str = Field(examples=["炮二平五"])
    by_ai: bool = False


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
    mode: Mode
    user_side: Side | None = Field(description="人机对战时你执哪一方；自由对弈为 null")
    ai_level: int | None
    ai_to_move: bool = Field(description="人机对战中现在是否轮到 AI 走")
    hints_used: int
    moves: list[MoveRecord]
    position: PositionView


class NewGameRequest(BaseModel):
    fen: str | None = Field(default=None, description="起始局面，不填为标准开局")
    mode: Mode = "free"
    user_side: Side = "red"
    ai_level: int = Field(default=3, ge=1, le=10)


class MoveRequest(BaseModel):
    move: str = Field(description="ICCS（h2e2、H2-E2）或中文记谱（炮二平五）", examples=["h2e2"])


class HintRequest(BaseModel):
    level: Literal[2, 3] = Field(description="2：只提示该动哪个子；3：给出具体着法和主要变化")


class HintView(BaseModel):
    level: int
    from_square: str = Field(description="该动的棋子所在格（ICCS 坐标，如 h0）")
    move: str | None = Field(default=None, description="推荐着法（仅 3 级提示）")
    cn: str | None = None
    pv_cn: list[str] | None = Field(default=None, description="主要变化（仅 3 级提示）")
    red_win: float | None = Field(default=None, description="走完推荐着法后红方期望得分 0..1")
    text: str


class LevelView(BaseModel):
    level: int
    name: str


class EngineStatusView(BaseModel):
    configured: bool
    ok: bool
    name: str | None = None
    flavor: str | None = None
    error: str | None = None
    levels: list[LevelView]
