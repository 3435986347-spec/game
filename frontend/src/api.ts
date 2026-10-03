// 与 Python 后端通信。所有规则判断都在后端，前端只负责显示和交互。

export type Side = "red" | "black";

export interface MoveRecord {
  iccs: string;
  cn: string;
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
  moves: MoveRecord[];
  position: PositionView;
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
  newGame: (fen?: string) =>
    request<GameView>("/api/games", {
      method: "POST",
      body: JSON.stringify({ fen: fen || null }),
    }),
  getGame: (id: string) => request<GameView>(`/api/games/${id}`),
  move: (id: string, move: string) =>
    request<GameView>(`/api/games/${id}/moves`, {
      method: "POST",
      body: JSON.stringify({ move }),
    }),
  undo: (id: string) => request<GameView>(`/api/games/${id}/undo`, { method: "POST" }),
};

const FILES = "abcdefghi";

export function squareToIccs(sq: number): string {
  return FILES[sq % 9] + Math.floor(sq / 9);
}

export function iccsToSquares(move: string): [number, number] {
  const sq = (f: string, r: string) => Number(r) * 9 + FILES.indexOf(f);
  return [sq(move[0], move[1]), sq(move[2], move[3])];
}
