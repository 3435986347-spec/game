// 与 Python 后端通信。所有规则判断都在后端，前端只负责显示和交互。

export type Side = "red" | "black";
export type Mode = "free" | "vs_ai";

export interface MoveRecord {
  iccs: string;
  cn: string;
  by_ai: boolean;
}

export interface ResultView {
  winner: Side | null;
  reason: string;
  text: string;
}

export interface PositionView {
  fen: string;
  turn: Side;
  /** 长度 90，下标为 rank * 9 + file；"." 为空，其余为 FEN 字母（大写红方） */
  board: string[];
  /** 当前全部合法着法（ICCS），棋局结束时为空 */
  legal_moves: string[];
  last_move: string | null;
  in_check: boolean;
  result: ResultView | null;
}

export interface GameView {
  id: string;
  initial_fen: string;
  mode: Mode;
  /** 人机对战时你执哪一方；自由对弈为 null */
  user_side: Side | null;
  ai_level: number | null;
  /** 人机对战中现在是否轮到 AI 走 */
  ai_to_move: boolean;
  hints_used: number;
  moves: MoveRecord[];
  position: PositionView;
  /** 已保存到棋谱库时为棋谱编号 */
  library_id: number | null;
}

export interface NewGameOptions {
  fen?: string;
  /** 从 fen（或标准开局）开始先走这些着法（ICCS），用于从棋谱中的某一步开始 */
  moves?: string[];
  mode: Mode;
  user_side: Side;
  ai_level: number;
}

export interface HintView {
  level: 2 | 3;
  /** 该动的棋子所在格，如 "h0" */
  from_square: string;
  move: string | null;
  cn: string | null;
  pv_cn: string[] | null;
  /** 走完推荐着法后红方的期望得分 0..1 */
  red_win: number | null;
  text: string;
}

export interface LevelInfo {
  level: number;
  name: string;
}

export interface EngineStatus {
  configured: boolean;
  ok: boolean;
  name: string | null;
  flavor: string | null;
  error: string | null;
  levels: LevelInfo[];
}

export interface AnalysisLine {
  move: string;
  cn: string;
  pv_cn: string[];
  /** 红方期望得分 0..1 */
  red_win: number;
  score_cp: number | null;
  mate: number | null;
  depth: number;
}

export interface AnalysisInfo {
  type: "info";
  /** 分析任意局面（fen + moves）时为 null */
  game_id: string | null;
  ply: number;
  fen: string;
  depth: number;
  lines: AnalysisLine[];
}

// ---------- 棋谱库 ----------

export type GameKind = "library" | "my_game";
export type GameResult = "1-0" | "0-1" | "1/2-1/2" | "*";

export const RESULT_TEXT: Record<GameResult, string> = {
  "1-0": "红胜",
  "0-1": "黑胜",
  "1/2-1/2": "和",
  "*": "未完",
};

export interface LibraryStats {
  games: number;
  my_games: number;
  /** 每局只索引前这么多步（局面统计和按局面搜索只看得到这些） */
  indexed_plies: number;
}

export interface ImportErrorItem {
  /** 该局在文件中的序号，从 1 开始 */
  index: number;
  title: string;
  reason: string;
}

export interface ImportJob {
  job_id: string;
  filename: string;
  done: boolean;
  games_seen: number;
  imported: number;
  duplicates: number;
  failed: number;
  /** 最多 100 条 */
  errors: ImportErrorItem[];
  /** 整个文件无法导入的原因（如无法读取），否则为 null */
  error: string | null;
}

export interface GameSummary {
  id: number;
  kind: GameKind;
  event: string | null;
  date: string | null;
  red: string | null;
  black: string | null;
  result: GameResult;
  opening: string | null;
  ply_count: number;
  source: string | null;
}

export interface LibraryMove {
  iccs: string;
  cn: string;
}

export interface GameRecord extends GameSummary {
  site: string | null;
  round: string | null;
  red_team: string | null;
  black_team: string | null;
  initial_fen: string;
  moves: LibraryMove[];
  /** fens[i]：走了 i 步之后的局面，长度 = moves.length + 1 */
  fens: string[];
  /** checks[i]：fens[i] 中走棋方是否被将军 */
  checks: boolean[];
}

export interface SearchParams {
  q?: string;
  event?: string;
  opening?: string;
  result?: GameResult;
  kind?: GameKind;
  fen?: string;
  page?: number;
  page_size?: number;
}

export interface SearchResult {
  total: number;
  page: number;
  page_size: number;
  items: GameSummary[];
}

export interface ExplorerMove {
  move: string;
  cn: string;
  games: number;
  red_wins: number;
  draws: number;
  black_wins: number;
}

export interface ExplorerView {
  fen: string;
  /** 到达过这个局面的对局数 */
  games: number;
  indexed_plies: number;
  /** 按局数从多到少 */
  moves: ExplorerMove[];
}

export interface OpeningInfo {
  name: string;
  games: number;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** 显示给用户的错误信息：后端的说明，或连不上后端。 */
export const errorText = (e: unknown) =>
  e instanceof ApiError ? e.message : "无法连接后端，请确认 Python 服务已启动";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // 响应不是 JSON，保留状态文本
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  newGame: (options: NewGameOptions) =>
    request<GameView>("/api/games", {
      method: "POST",
      body: JSON.stringify({ ...options, fen: options.fen || null }),
    }),
  getGame: (id: string) => request<GameView>(`/api/games/${id}`),
  move: (id: string, move: string) =>
    request<GameView>(`/api/games/${id}/moves`, {
      method: "POST",
      body: JSON.stringify({ move }),
    }),
  undo: (id: string) => request<GameView>(`/api/games/${id}/undo`, { method: "POST" }),
  aiMove: (id: string) => request<GameView>(`/api/games/${id}/ai-move`, { method: "POST" }),
  hint: (id: string, level: 2 | 3) =>
    request<HintView>(`/api/games/${id}/hint`, {
      method: "POST",
      body: JSON.stringify({ level }),
    }),
  engineStatus: () => request<EngineStatus>("/api/engine"),
  saveGame: (id: string) =>
    request<{ library_id: number }>(`/api/games/${id}/save`, { method: "POST" }),

  libraryStats: () => request<LibraryStats>("/api/library/stats"),
  /** 上传一个棋谱文件，后台导入；用 importJob 查询进度 */
  importFile: (file: File) =>
    request<{ job_id: string }>(`/api/library/import?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    }),
  importJob: (jobId: string) => request<ImportJob>(`/api/library/import/${jobId}`),
  searchGames: (params: SearchParams) =>
    request<SearchResult>(`/api/library/games?${queryString(params)}`),
  libraryGame: (id: number) => request<GameRecord>(`/api/library/games/${id}`),
  deleteLibraryGame: (id: number) =>
    request<{ deleted: boolean }>(`/api/library/games/${id}`, { method: "DELETE" }),
  explorer: (fen: string, signal?: AbortSignal) =>
    request<ExplorerView>(`/api/library/explorer?fen=${encodeURIComponent(fen)}`, { signal }),
  openings: () => request<OpeningInfo[]>("/api/library/openings"),
};

/** 查询参数，空值不发送。 */
function queryString(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  return search.toString();
}

const FILES = "abcdefghi";

export function squareFromIccs(square: string): number {
  return Number(square[1]) * 9 + FILES.indexOf(square[0]);
}

export function squareToIccs(sq: number): string {
  return FILES[sq % 9] + Math.floor(sq / 9);
}

export function iccsToSquares(move: string): [number, number] {
  const sq = (f: string, r: string) => Number(r) * 9 + FILES.indexOf(f);
  return [sq(move[0], move[1]), sq(move[2], move[3])];
}
