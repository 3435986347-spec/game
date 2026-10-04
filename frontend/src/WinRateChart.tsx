import type { MouseEvent } from "react";
import { percent } from "./AnalysisPanel";
import type { ReviewMove } from "./api";
import { GRADE_STYLE } from "./useReview";

const W = 600;
const H = 150;
const PAD_X = 10;
const PAD_Y = 12;
const MARKED = new Set(["缓着", "失误", "漏着", "妙着"]);

interface WinRateChartProps {
  /** curve[i]：走了 i 步之后红方的期望得分 */
  curve: number[];
  moves: ReviewMove[];
  /** 当前显示的局面（步数） */
  ply: number;
  onSelect: (ply: number) => void;
}

/** 胜率曲线：红方期望得分随步数的变化。失误、漏着等标出颜色，点击跳到那一步。 */
export default function WinRateChart({ curve, moves, ply, onSelect }: WinRateChartProps) {
  const n = curve.length;
  const x = (i: number) => PAD_X + (n > 1 ? i / (n - 1) : 0) * (W - 2 * PAD_X);
  const y = (v: number) => PAD_Y + (1 - v) * (H - 2 * PAD_Y);
  const line = curve.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${x(0)},${y(0)} ${line} ${x(n - 1)},${y(0)}`;

  const select = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((sx - PAD_X) / (W - 2 * PAD_X)) * (n - 1));
    onSelect(Math.max(0, Math.min(n - 1, i)));
  };

  return (
    <svg className="win-chart" viewBox={`0 0 ${W} ${H}`} onClick={select} role="img"
      aria-label="胜率曲线：红方期望得分随步数的变化，点击跳到对应的步">
      <rect className="win-chart-bg" x={PAD_X} y={PAD_Y} width={W - 2 * PAD_X} height={H - 2 * PAD_Y} />
      <polygon className="win-chart-area" points={area} />
      <line className="win-chart-mid" x1={PAD_X} x2={W - PAD_X} y1={y(0.5)} y2={y(0.5)} />
      <polyline className="win-chart-line" points={line} />
      <line className="win-chart-cursor" x1={x(ply)} x2={x(ply)} y1={PAD_Y - 6} y2={H - PAD_Y + 6} />
      {moves.filter((m) => MARKED.has(m.grade)).map((m) => (
        <circle key={m.ply} className={`win-chart-mark ${GRADE_STYLE[m.grade].className}`}
          cx={x(m.ply)} cy={y(curve[m.ply])} r={m.grade === "漏着" ? 5 : 4}>
          <title>{`第 ${m.ply} 步 ${m.cn}：${m.grade}` +
            (m.drop > 0 ? `（期望得分 −${percent(m.drop)}）` : "")}</title>
        </circle>
      ))}
      <text className="win-chart-label" x={PAD_X + 4} y={PAD_Y + 12}>红方 {percent(curve[ply] ?? 0.5)}</text>
    </svg>
  );
}
