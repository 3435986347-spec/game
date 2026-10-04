// 只用于显示的 FEN 解析：把局面字符串变成棋盘数组，不做任何规则判断（规则都在后端）。
import type { PositionView, Side } from "./api";

/** FEN 中轮到哪一方走（"w" / "r" 为红方，"b" 为黑方）。 */
export function sideToMove(fen: string): Side {
  return fen.trim().split(/\s+/)[1] === "b" ? "black" : "red";
}

/** 棋子摆放 + 走棋方，用来判断两个 FEN 是否同一局面（忽略回合计数）。 */
export function positionKey(fen: string): string {
  return fen.trim().split(/\s+/).slice(0, 2).join(" ");
}

/**
 * 由 FEN 构造只读的 PositionView：board 长度 90，下标为 rank * 9 + file，
 * FEN 第一行是 rank 9（黑方底线）。没有合法着法和胜负信息。
 */
export function positionFromFen(fen: string, lastMove: string | null = null, inCheck = false): PositionView {
  const board: string[] = new Array(90).fill(".");
  const rows = fen.trim().split(/\s+/)[0].split("/");
  rows.forEach((row, i) => {
    const rank = 9 - i;
    let file = 0;
    for (const ch of row) {
      if (ch >= "1" && ch <= "9") {
        file += Number(ch);
      } else {
        if (rank >= 0 && file < 9) board[rank * 9 + file] = ch;
        file += 1;
      }
    }
  });
  return {
    fen,
    turn: sideToMove(fen),
    board,
    legal_moves: [],
    last_move: lastMove,
    in_check: inCheck,
    result: null,
  };
}
