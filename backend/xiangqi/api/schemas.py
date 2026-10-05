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
    library_id: int | None = Field(default=None, description="已保存到棋谱库时的 id")
    moves: list[MoveRecord]
    position: PositionView


class NewGameRequest(BaseModel):
    fen: str | None = Field(default=None, description="起始局面，不填为标准开局")
    moves: list[str] = Field(
        default_factory=list,
        description="从起始局面开始先走的着法（ICCS），用于从棋谱中的某一步开始",
    )
    mode: Mode = "free"
    user_side: Side = "red"
    ai_level: int = Field(default=3, ge=1, le=10)


class MoveRequest(BaseModel):
    move: str = Field(description="ICCS（h2e2、H2-E2）或中文记谱（炮二平五）", examples=["h2e2"])


class HintRequest(BaseModel):
    level: Literal[1, 2, 3] = Field(
        description="1：只指出方向（不泄露着法）；2：提示该动哪个子；3：给出具体着法和主要变化"
    )


class HintView(BaseModel):
    level: int
    from_square: str | None = Field(
        default=None, description="该动的棋子所在格（ICCS 坐标，如 h0）；1 级提示为 null"
    )
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


# ---- 棋谱库 ----

GameResultCode = Literal["1-0", "0-1", "1/2-1/2", "*"]


class LibraryStats(BaseModel):
    games: int
    my_games: int
    indexed_plies: int = Field(description="局面索引只包含每局前多少步（半回合）")


class ImportJobView(BaseModel):
    job_id: str
    filename: str
    done: bool
    games_seen: int
    imported: int
    duplicates: int
    failed: int
    errors: list[dict] = Field(description="[{index, title, reason}]，最多 100 条")
    error: str | None = Field(description="导致整个文件无法导入的错误")


class ImportStarted(BaseModel):
    job_id: str


class GameSummary(BaseModel):
    id: int
    kind: Literal["library", "my_game"]
    event: str | None
    date: str | None
    red: str | None
    black: str | None
    result: GameResultCode
    opening: str | None
    ply_count: int
    source: str | None


class SearchResult(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[GameSummary]


class GameRecordView(GameSummary):
    site: str | None
    round: str | None
    red_team: str | None
    black_team: str | None
    initial_fen: str
    moves: list[MoveRecord]
    fens: list[str] = Field(description="fens[i] 为走了 i 步之后的局面，长度 = 着法数 + 1")
    checks: list[bool] = Field(description="checks[i]：fens[i] 中走棋方是否被将军")


class ExplorerMove(BaseModel):
    move: str
    cn: str
    games: int
    red_wins: int
    draws: int
    black_wins: int


class ExplorerView(BaseModel):
    fen: str
    games: int = Field(description="库中走到这个局面的对局数")
    indexed_plies: int
    moves: list[ExplorerMove]


class OpeningCount(BaseModel):
    name: str
    games: int


class SavedGame(BaseModel):
    library_id: int


# ---- 讲解与复盘 ----

Grade = Literal["妙着", "好棋", "可以", "缓着", "失误", "漏着"]


class LLMStatusView(BaseModel):
    provider: str = Field(description="none | claude | openai_compat")
    model: str | None
    ready: bool = Field(description="配置齐全（不代表 Key 一定有效：检查时不实际调用大模型）")
    problem: str | None
    level: str = Field(description="学生水平，决定讲解深浅")


class ExplanationView(BaseModel):
    headline: str
    why: str
    better: str
    principle: str
    tags: list[str]
    source: Literal["llm", "template"] = Field(description="大模型讲解，或模板讲解")
    provider: str | None = None
    model: str | None = None
    note: str | None = Field(default=None, description="使用模板讲解的原因")


class ReviewMove(BaseModel):
    ply: int = Field(description="第几步（从 1 开始）；走完这步后的局面序号")
    iccs: str
    cn: str
    side: Side
    grade: Grade
    phase: str
    win_before: float = Field(description="走棋方视角：走这步之前（按引擎最佳着法）的期望得分")
    win_after: float = Field(description="走棋方视角：走完这步之后的期望得分")
    drop: float = Field(description="期望得分下降（走了引擎最佳着法时为 0）")
    best_move: str | None = Field(description="引擎在走这步之前推荐的着法（ICCS）")
    best_cn: str | None
    best_pv_cn: list[str]
    explanation: ExplanationView | None


class MoveAnalysisView(ReviewMove):
    """边下边分析：对局中一步棋的评级。"""

    red_win: float = Field(description="走完这步之后红方的期望得分")
    terminal: str | None = Field(default=None, description="走完这步棋局结束时的说明")
    fen_before: str = Field(description="走这步之前的局面（加入错题本用）")


class SideStats(BaseModel):
    moves: int
    accuracy: float | None = Field(description="准确率 0–100")
    phases: dict[str, float | None] = Field(description="开局 / 中局 / 残局各自的准确率")
    grades: dict[str, int]


class TagCount(BaseModel):
    tag: str
    count: int


class ReviewReport(BaseModel):
    engine: str | None
    movetime_ms: int | None
    created_at: str
    focus: list[Side] = Field(description="关注的一方：自己的对局只看自己，棋谱库的对局看双方")
    curve: list[float] = Field(description="curve[i]：走了 i 步之后红方的期望得分")
    terminal: str | None = Field(description="终局说明")
    moves: list[ReviewMove]
    stats: dict[str, SideStats] = Field(description="red / black")
    key_moments: list[int] = Field(description="关键时刻（步数），最多 3 个")
    tags: list[TagCount] = Field(description="关键时刻讲解中出现的问题标签")


class ReviewView(BaseModel):
    game_id: int
    status: Literal["none", "running", "done", "error"]
    phase: str | None = Field(default=None, description="analysing（引擎分析）| explaining（讲解）")
    progress: int = 0
    total: int = 0
    error: str | None = None
    report: ReviewReport | None = None


class StartReviewRequest(BaseModel):
    force: bool = Field(default=False, description="已有复盘时重新分析")


class ExplainRequest(BaseModel):
    ply: int = Field(ge=1, description="讲解第几步")


class GameReviewStarted(BaseModel):
    library_id: int


# ---- 名局解读 ----


class GameSummaryView(BaseModel):
    opening: str
    middlegame: str
    endgame: str
    overall: str
    source: Literal["llm", "template"]
    provider: str | None = None
    model: str | None = None
    note: str | None = None


class AnnotatedMove(BaseModel):
    ply: int
    side: Side
    cn: str
    grade: Grade
    phase: str
    red_before: float = Field(description="走这步之前红方的期望得分")
    red_after: float
    intent: ExplanationView = Field(description="这步棋的意图（headline 为一句话结论）")


class AnnotateView(BaseModel):
    game_id: int
    status: Literal["none", "running", "done", "error"]
    progress: int = 0
    total: int = 0
    error: str | None = None
    summary: GameSummaryView | None = None
    moves: list[AnnotatedMove] = Field(default_factory=list, description="解读过的关键着法")


# ---- 猜着练习 ----


class StartGuessRequest(BaseModel):
    game_id: int = Field(description="棋谱库中的对局")
    side: Side = Field(description="你执哪一方（猜这一方的着法）")
    skip_plies: int = Field(default=0, ge=0, description="开头跳过多少步（半回合）不猜")


class MoveAnswerRequest(BaseModel):
    move: str | None = Field(
        default=None, description="你的着法（ICCS 或中文记谱）；null 表示不会，直接看答案"
    )


class GuessAnswerView(BaseModel):
    ply: int = Field(description="这步棋是第几步（从 1 开始）")
    fen_before: str
    user_move: str | None
    user_cn: str | None
    master_move: str
    master_cn: str
    points: int = Field(description="0–3 分")
    loss: float | None = Field(description="比大师着法差多少期望得分（不差为 0；放弃时为 null）")
    same: bool = Field(description="和大师着法相同")
    as_good: bool = Field(description="和大师不同，但引擎认为不差")
    master_not_best: bool = Field(description="此处大师着法也非最佳")
    best_move: str | None
    best_cn: str | None
    user_score: float | None = Field(description="你的着法走完后，你这一方的期望得分")
    master_score: float | None
    explanation: ExplanationView | None


class GuessSummary(BaseModel):
    answered: int
    matched: int = Field(description="和大师着法相同的步数")
    match_rate: float | None
    avg_loss: float | None = Field(description="平均每步比大师着法差多少期望得分")
    worst: list[int] = Field(description="失分最多的几步（步数）")
    cards_added: int = Field(description="加入错题本的步数")


class GuessView(BaseModel):
    id: int
    game_id: int
    red: str | None
    black: str | None
    event: str | None
    side: Side
    start_ply: int
    current_ply: int = Field(description="当前局面走了多少步；没结束时轮到你猜下一步")
    total_plies: int
    score: int
    max_score: int
    finished: bool
    initial_fen: str
    fen: str = Field(description="当前局面")
    last_move: str | None
    in_check: bool
    legal_moves: list[str] = Field(description="当前局面的合法着法（ICCS）；猜完后为空")
    moves: list[MoveRecord] = Field(description="到当前局面为止的着法")
    answers: list[GuessAnswerView]
    summary: GuessSummary | None = Field(description="猜完之后的总结")


class GuessAnswerResult(BaseModel):
    answer: GuessAnswerView
    session: GuessView


class GuessSessionItem(BaseModel):
    id: int
    game_id: int
    red: str | None
    black: str | None
    event: str | None
    side: Side
    score: int
    max_score: int
    finished: bool
    current_ply: int
    total_plies: int | None
    created_at: str


# ---- 训练：错题本、做题 ----


class TrainSummary(BaseModel):
    due: int = Field(description="今天要复习的题数（错题 + 做错的题）")
    due_mistakes: int
    due_puzzles: int
    cards: int = Field(description="错题本里一共多少题")
    puzzles: int = Field(description="题库里一共多少题")
    puzzles_attempted: int
    puzzles_solved: int
    rating: float = Field(description="做题等级分")
    themes: list[TagCount] = Field(description="题库里各主题的题数")


class CardView(BaseModel):
    id: int
    kind: Literal["mistake", "puzzle"]
    fen: str
    turn: Side
    legal_moves: list[str]
    in_check: bool
    played: str | None = Field(description="当时走的错着（ICCS）")
    played_cn: str | None
    source: str | None
    source_game_id: int | None
    source_ply: int | None
    reps: int
    lapses: int
    interval_days: float
    due_at: str
    created_at: str


class CardItem(CardView):
    solution: str
    solution_cn: str


class NextCard(BaseModel):
    card: CardView | None
    due: int


class TrainResult(BaseModel):
    """复习一题或做一道题的结果。"""

    correct: bool
    move: str | None
    move_cn: str | None
    solution: str
    solution_cn: str
    pv_cn: list[str] = Field(description="正解之后的变化")
    explanation: ExplanationView | None = None


class CardResult(TrainResult):
    interval_days: float = Field(description="下次复习间隔（天）；0 表示 10 分钟后再出")
    due_at: str
    due: int = Field(description="剩下要复习的题数")


class AddCardRequest(BaseModel):
    fen: str
    solution: str = Field(description="正确着法（ICCS）")
    played: str | None = Field(default=None, description="走错的着法（ICCS）")
    source: str | None = None
    source_game_id: int | None = None
    source_ply: int | None = None
    explanation: ExplanationView | None = None


class AddCardResult(BaseModel):
    card_id: int | None
    created: bool = Field(description="false 表示这一题已经在错题本里")


class PuzzleView(BaseModel):
    id: int
    fen: str
    turn: Side
    legal_moves: list[str]
    in_check: bool
    rating: int
    attempts: int
    theme: str | None = Field(description="按主题出题时的主题（其余标签做完才显示）")


class NextPuzzle(BaseModel):
    puzzle: PuzzleView | None
    rating: float


class PuzzleResult(TrainResult):
    tags: list[str]
    puzzle_rating: int
    rating_before: float
    rating_after: float
    card_added: bool = Field(description="做错了，加入错题本")
    master_found: bool | None = Field(description="棋谱里实际走出了正解")
    source_game_id: int | None
    source_ply: int | None
