import { useMemo, useState } from "react";
import type { PositionView } from "./api";
import { iccsToSquares, squareToIccs } from "./api";

const CELL = 64;
const PAD = 66;
const WIDTH = PAD * 2 + CELL * 8;
const HEIGHT = PAD * 2 + CELL * 9;
const RADIUS = CELL * 0.43;

const PIECE_CHARS: Record<string, string> = {
  K: "帅", A: "仕", B: "相", N: "马", R: "车", C: "炮", P: "兵",
  k: "将", a: "士", b: "象", n: "马", r: "车", c: "炮", p: "卒",
};
const RED_NUM = "零一二三四五六七八九";

export interface Arrow {
  move: string; // ICCS，如 h0g2
  kind: "hint" | "analysis";
}

interface BoardProps {
  position: PositionView;
  flipped: boolean;
  onMove: (iccs: string) => void;
  /** 为 false 时不能走子（如轮到 AI 走） */
  interactive?: boolean;
  /** 需要突出显示的格子（2 级提示：该动的棋子） */
  highlight?: number | null;
  arrows?: Arrow[];
}

const NO_TARGETS = new Map<number, Set<number>>();

/** 格子在画布上的坐标。不翻转时红方在下。 */
function pointOf(sq: number, flipped: boolean): [number, number] {
  const file = sq % 9;
  const rank = Math.floor(sq / 9);
  const col = flipped ? 8 - file : file;
  const row = flipped ? rank : 9 - rank;
  return [PAD + col * CELL, PAD + row * CELL];
}

const isRed = (piece: string) => piece !== "." && piece === piece.toUpperCase();

export default function Board({
  position,
  flipped,
  onMove,
  interactive = true,
  highlight = null,
  arrows = [],
}: BoardProps) {
  const [selected, setSelected] = useState<number | null>(null);
  const { board, turn, legal_moves, last_move, in_check } = position;

  // 起点 → 可走到的终点（不能走子时为空）
  const targets = useMemo(() => {
    if (!interactive) return NO_TARGETS;
    const map = new Map<number, Set<number>>();
    for (const m of legal_moves) {
      const [from, to] = iccsToSquares(m);
      if (!map.has(from)) map.set(from, new Set());
      map.get(from)!.add(to);
    }
    return map;
  }, [legal_moves, interactive]);

  // 局面变化或不能走子时取消选择
  const [lastFen, setLastFen] = useState(position.fen);
  if (lastFen !== position.fen) {
    setLastFen(position.fen);
    setSelected(null);
  }
  if (!interactive && selected !== null) setSelected(null);

  const handleClick = (sq: number) => {
    if (selected !== null && targets.get(selected)?.has(sq)) {
      onMove(squareToIccs(selected) + squareToIccs(sq));
      setSelected(null);
    } else if (targets.has(sq)) {
      setSelected(sq === selected ? null : sq);
    } else {
      setSelected(null);
    }
  };

  const lastSquares = last_move ? iccsToSquares(last_move) : [];
  const kingInCheck = in_check ? board.indexOf(turn === "red" ? "K" : "k") : -1;
  const selectedTargets = selected !== null ? (targets.get(selected) ?? new Set<number>()) : null;

  return (
    <svg
      className="board"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label="象棋棋盘"
    >
      <defs>
        <radialGradient id="piece-face" cx="40%" cy="35%" r="70%">
          <stop offset="0%" stopColor="#fff8e6" />
          <stop offset="100%" stopColor="#e9cf9c" />
        </radialGradient>
        <filter id="piece-shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="2" stdDeviation="1.6" floodOpacity="0.35" />
        </filter>
        {(["hint", "analysis"] as const).map((kind) => (
          <marker key={kind} id={`arrow-${kind}`} className={`arrow-head ${kind}`} viewBox="0 0 10 10"
            refX="5" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" />
          </marker>
        ))}
      </defs>

      <rect className="board-bg" x={0} y={0} width={WIDTH} height={HEIGHT} rx={10} />
      <Grid />
      <FileLabels flipped={flipped} />

      {lastSquares.map((sq) => {
        const [x, y] = pointOf(sq, flipped);
        return <rect key={`last-${sq}`} className="last-move" x={x - CELL / 2 + 3} y={y - CELL / 2 + 3}
          width={CELL - 6} height={CELL - 6} rx={6} />;
      })}

      {board.map((piece, sq) => {
        if (piece === ".") return null;
        const [x, y] = pointOf(sq, flipped);
        const red = isRed(piece);
        const classes = ["piece", red ? "red" : "black"];
        if (sq === selected) classes.push("selected");
        if (targets.has(sq)) classes.push("movable");
        return (
          <g key={`p-${sq}`} className={classes.join(" ")} transform={`translate(${x} ${y})`}>
            {sq === kingInCheck && <circle className="check-glow" r={RADIUS + 7} />}
            <circle className="piece-body" r={RADIUS} filter="url(#piece-shadow)" />
            <circle className="piece-ring" r={RADIUS - 5} />
            <text className="piece-text" dy="0.36em">{PIECE_CHARS[piece]}</text>
          </g>
        );
      })}

      {highlight !== null && (() => {
        const [x, y] = pointOf(highlight, flipped);
        return <circle className="hint-ring" cx={x} cy={y} r={RADIUS + 6} />;
      })()}

      {arrows.map(({ move, kind }) => {
        const [from, to] = iccsToSquares(move);
        const [x1, y1] = pointOf(from, flipped);
        const [x2, y2] = pointOf(to, flipped);
        const len = Math.hypot(x2 - x1, y2 - y1);
        const end = (len - RADIUS * 0.75) / len; // 箭头停在目标棋子边缘
        return (
          <line key={`arrow-${kind}-${move}`} className={`arrow ${kind}`}
            x1={x1} y1={y1} x2={x1 + (x2 - x1) * end} y2={y1 + (y2 - y1) * end}
            markerEnd={`url(#arrow-${kind})`} />
        );
      })}

      {selectedTargets && [...selectedTargets].map((sq) => {
        const [x, y] = pointOf(sq, flipped);
        return board[sq] === "."
          ? <circle key={`t-${sq}`} className="target-dot" cx={x} cy={y} r={8} />
          : <circle key={`t-${sq}`} className="target-capture" cx={x} cy={y} r={RADIUS + 3} />;
      })}

      {/* 透明点击区域覆盖全部 90 个交叉点 */}
      {board.map((_, sq) => {
        const [x, y] = pointOf(sq, flipped);
        const clickable = targets.has(sq) || (selectedTargets?.has(sq) ?? false);
        return (
          <rect key={`hit-${sq}`} className={clickable ? "hit clickable" : "hit"}
            x={x - CELL / 2} y={y - CELL / 2} width={CELL} height={CELL}
            onClick={() => handleClick(sq)} data-square={squareToIccs(sq)} />
        );
      })}
    </svg>
  );
}

/** 棋盘线、九宫、河界和炮兵位标记（上下对称，与是否翻转无关）。 */
function Grid() {
  const x = (col: number) => PAD + col * CELL;
  const y = (row: number) => PAD + row * CELL;
  const lines: [number, number, number, number][] = [];
  for (let row = 0; row < 10; row++) lines.push([x(0), y(row), x(8), y(row)]);
  for (let col = 0; col < 9; col++) {
    if (col === 0 || col === 8) lines.push([x(col), y(0), x(col), y(9)]);
    else {
      lines.push([x(col), y(0), x(col), y(4)]);
      lines.push([x(col), y(5), x(col), y(9)]);
    }
  }
  lines.push([x(3), y(0), x(5), y(2)], [x(5), y(0), x(3), y(2)]);
  lines.push([x(3), y(7), x(5), y(9)], [x(5), y(7), x(3), y(9)]);

  const markers: [number, number][] = [[1, 2], [7, 2], [1, 7], [7, 7]];
  for (const col of [0, 2, 4, 6, 8]) markers.push([col, 3], [col, 6]);

  return (
    <g className="grid">
      <rect className="grid-border" x={x(0) - 6} y={y(0) - 6} width={CELL * 8 + 12} height={CELL * 9 + 12} />
      {lines.map(([x1, y1, x2, y2], i) => <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} />)}
      {markers.map(([col, row]) => <Marker key={`${col}-${row}`} cx={x(col)} cy={y(row)} col={col} />)}
      <text className="river" x={x(2)} y={y(4.5)} dy="0.35em">楚　河</text>
      <text className="river" x={x(6)} y={y(4.5)} dy="0.35em">汉　界</text>
    </g>
  );
}

/** 炮位、兵位旁的小直角标记，靠边的一侧不画。 */
function Marker({ cx, cy, col }: { cx: number; cy: number; col: number }) {
  const gap = 5;
  const len = 11;
  const paths: string[] = [];
  for (const dx of [-1, 1]) {
    if ((col === 0 && dx < 0) || (col === 8 && dx > 0)) continue;
    for (const dy of [-1, 1]) {
      const px = cx + dx * gap;
      const py = cy + dy * gap;
      paths.push(`M${px} ${py + dy * len} L${px} ${py} L${px + dx * len} ${py}`);
    }
  }
  return <path className="marker" d={paths.join(" ")} />;
}

/** 上下两侧的纵线编号：红方用中文数字，黑方用阿拉伯数字，都从各自的右手边数起。 */
function FileLabels({ flipped }: { flipped: boolean }) {
  const label = (col: number, redSide: boolean) => {
    const file = flipped ? 8 - col : col;
    return redSide ? RED_NUM[9 - file] : String(file + 1);
  };
  const cols = [0, 1, 2, 3, 4, 5, 6, 7, 8];
  return (
    <g className="file-labels">
      {cols.map((col) => (
        <text key={`top-${col}`} x={PAD + col * CELL} y={16} dy="0.35em">
          {label(col, flipped)}
        </text>
      ))}
      {cols.map((col) => (
        <text key={`bottom-${col}`} x={PAD + col * CELL} y={HEIGHT - 16} dy="0.35em">
          {label(col, !flipped)}
        </text>
      ))}
    </g>
  );
}
