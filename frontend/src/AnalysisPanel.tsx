import type { AnalysisInfo, TrapAlert, TrapNote } from "./api";

/** 红方期望得分 → 百分比文字。 */
export const percent = (x: number) => `${Math.round(x * 100)}%`;

interface EvalBarProps {
  redWin: number | null;
  flipped: boolean;
}

/** 棋盘旁的竖直评估条：红色部分占比 = 红方期望得分。红方在哪一侧，红色就从哪一侧长出。 */
export function EvalBar({ redWin, flipped }: EvalBarProps) {
  const red = redWin ?? 0.5;
  return (
    <div className={`eval-bar ${flipped ? "flipped" : ""}`} aria-label={`红方期望得分 ${percent(red)}`}>
      <div className="eval-red" style={{ height: `${red * 100}%` }} />
      <span className="eval-label">{redWin === null ? "—" : percent(red)}</span>
    </div>
  );
}

/** 候选着法上的飞刀标签：命中剧本的显示剧本名，通用弃子抢攻显示「飞刀」。 */
export function TrapChip({ trap }: { trap: TrapNote }) {
  const generic = trap.id === "sacrifice";
  return (
    <span className={`trap-chip ${generic ? "generic" : ""}`} title={trap.note}>
      {generic ? "飞刀" : `飞刀 · ${trap.name}`}
    </span>
  );
}

/** 当前局面疑似飞刀时的告警/推荐条。 */
export function TrapAlertBanner({ alert }: { alert: TrapAlert }) {
  return (
    <div className={`trap-alert ${alert.kind}`} role="alert">
      <strong>{alert.kind === "warn" ? "小心飞刀" : "飞刀机会"}</strong>
      <span>{alert.advice}</span>
    </div>
  );
}

interface AnalysisPanelProps {
  info: AnalysisInfo | null;
  error: string | null;
  finished: boolean;
}

/** 引擎实时分析：前几个候选着法、各自的红方期望得分、后续变化，以及每步的目的和飞刀标注。 */
export function AnalysisPanel({ info, error, finished }: AnalysisPanelProps) {
  if (finished) return <p className="muted small">棋局已结束。</p>;
  if (error) return <p className="error">{error}</p>;
  if (!info) return <p className="muted small">引擎计算中……</p>;
  if (info.lines.length === 0) return <p className="muted small">这个局面没有可走的着法。</p>;
  return (
    <div className="analysis">
      <p className="muted small">深度 {info.depth}</p>
      {info.trap_alert && <TrapAlertBanner alert={info.trap_alert} />}
      <ol>
        {info.lines.map((line, i) => (
          <li key={i}>
            <div className="analysis-head">
              <strong>{line.cn}</strong>
              {line.trap && <TrapChip trap={line.trap} />}
              <span className="analysis-score">
                {line.mate !== null ? mateText(line.mate) : `红 ${percent(line.red_win)}`}
              </span>
            </div>
            <div className="analysis-pv">{line.pv_cn.slice(1, 8).join(" ")}</div>
            <div className="analysis-purpose" title={line.trap?.note ?? undefined}>
              {line.trap ? `${line.trap.note} · ` : ""}{line.purpose}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function mateText(mate: number): string {
  if (mate === 0) return "已被将死";
  return mate > 0 ? `红方 ${mate} 步杀` : `黑方 ${-mate} 步杀`;
}
