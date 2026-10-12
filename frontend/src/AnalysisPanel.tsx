import { useState } from "react";
import type { AnalysisInfo, AnalysisLine, TrapAlert, TrapNote } from "./api";

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
  paused?: boolean;
}

/** 引擎实时分析：前几个候选着法、各自的红方期望得分、后续变化，以及每步的目的和飞刀标注。 */
export function AnalysisPanel({ info, error, finished, paused = false }: AnalysisPanelProps) {
  if (finished) return <p className="muted small">棋局已结束。</p>;
  if (paused) return <p className="muted small" role="status">AI 行棋期间暂停实时分析，落子后自动恢复。</p>;
  if (error) return <p className="error">{error}</p>;
  if (!info) return <p className="muted small">引擎计算中……</p>;
  if (info.lines.length === 0) return <p className="muted small">这个局面没有可走的着法。</p>;
  const search = info.search;
  const mixedDepths = info.lines.some((line) => line.depth !== info.lines[0].depth);
  const progress = search ? [
    `深度 ${search.depth}`,
    search.nodes !== null ? `累计 ${search.nodes.toLocaleString("zh-CN")} 个搜索节点` : null,
    search.elapsed_ms !== null ? `耗时 ${(search.elapsed_ms / 1000).toFixed(1)} 秒` : null,
    search.nps !== null ? `${search.nps.toLocaleString("zh-CN")} 节点/秒` : null,
  ].filter(Boolean).join(" · ") : null;
  return (
    <div className="analysis">
      {info.seeded && <p className="muted small">候选着法来自上次搜索结果 · 深度 {info.depth}</p>}
      <p className="muted small" role="status">
        {info.status === "complete" ? "搜索已完成" : "继续搜索"}
        {progress ? ` · ${progress}` : info.seeded ? " · 正在重新计算本局面……" : ` · 深度 ${info.depth}`}
      </p>
      {info.best_unchanged && info.status === "searching" && !info.seeded && (
        <p className="muted small">最佳着法暂未改变，引擎仍在复核更深的应对。</p>
      )}
      {mixedDepths && <p className="muted small">候选着法逐条更新，当前搜索深度暂不一致。</p>}
      {info.trap_alert && <TrapAlertBanner alert={info.trap_alert} />}
      <p className="muted small">后续着法是引擎当前推演的主要变化，显示步数不等于搜索深度。</p>
      <ol>
        {info.lines.map((line) => (
          <CandidateLine key={line.move} line={line} showDepth={mixedDepths} />
        ))}
      </ol>
    </div>
  );
}

function CandidateLine({ line, showDepth }: { line: AnalysisLine; showDepth: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const continuation = line.pv_cn.slice(1);
  const hasMore = continuation.length > 7;
  return (
    <li>
      <div className="analysis-head">
        <strong>{line.cn}</strong>
        {showDepth && <span className="muted small">深度 {line.depth}</span>}
        {line.trap && <TrapChip trap={line.trap} />}
        <span className="analysis-score">
          {line.mate !== null ? mateText(line.mate) : `红 ${percent(line.red_win)}`}
        </span>
      </div>
      <div className="analysis-pv">
        {(expanded ? continuation : continuation.slice(0, 7)).join(" ")}
        {hasMore && (
          <button className="link small" aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}>
            {expanded ? "收起" : "展开后续"}
          </button>
        )}
      </div>
      <div className="analysis-purpose" title={line.trap?.note ?? undefined}>
        {line.trap ? `${line.trap.note} · ` : ""}{line.purpose}
      </div>
    </li>
  );
}

function mateText(mate: number): string {
  if (mate === 0) return "已被将死";
  return mate > 0 ? `红方 ${mate} 步杀` : `黑方 ${-mate} 步杀`;
}
