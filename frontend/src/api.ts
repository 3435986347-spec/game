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
}

export interface NewGameOptions {
  fen?: string;
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
  game_id: string;
  ply: number;
  fen: string;
  depth: number;
  lines: AnalysisLine[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

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
};

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
