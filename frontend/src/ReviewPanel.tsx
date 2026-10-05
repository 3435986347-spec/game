import { useEffect, useState } from "react";
import { percent } from "./AnalysisPanel";
import { api } from "./api";
import type { Explanation, Grade, LLMStatus, ReviewMove, ReviewReport, Side, SideStats } from "./api";
import ExplanationBlock from "./ExplanationBlock";
import { GRADE_STYLE } from "./useReview";
import type { ReviewState } from "./useReview";
import WinRateChart from "./WinRateChart";

const SIDE_TEXT: Record<Side, string> = { red: "红方", black: "黑方" };
const COUNTED: Grade[] = ["妙着", "缓着", "失误", "漏着"];
const PHASES = ["开局", "中局", "残局"];

/** 大模型配置状态（只检查配置，不实际调用）。 */
export function useLlmStatus(): LLMStatus | null {
  const [status, setStatus] = useState<LLMStatus | null>(null);
  useEffect(() => {
    api.llmStatus().then(setStatus, () => setStatus(null));
  }, []);
  return status;
}

function llmText(llm: LLMStatus | null): string {
  if (!llm) return "";
  if (llm.provider === "none") return "讲解使用模板（没有配置大模型，见 README「大模型讲解」）";
  const name = llm.provider === "claude" ? "Claude" : "大模型";
  if (!llm.ready) return `讲解：${name} 未就绪（${llm.problem}），暂用模板`;
  return `讲解：${name}（${llm.model}），学生水平「${llm.level}」` + (llm.problem ? `。${llm.problem}` : "");
}

export function GradeBadge({ grade }: { grade: Grade }) {
  return <span className={`grade-badge ${GRADE_STYLE[grade].className}`}>{grade}</span>;
}

interface ReviewPanelProps {
  review: ReviewState;
  llm: LLMStatus | null;
  ply: number;
  engineReady: boolean;
  busy: boolean;
  onGo: (ply: number) => void;
  /** 从第 ply 步之前的局面开始，由走这步的一方和 AI 重新下 */
  onRetry: (move: ReviewMove) => void;
}

/** 复盘：开始 / 进度 / 报告（准确率、胜率曲线、关键时刻）。 */
export default function ReviewPanel({ review, llm, ply, engineReady, busy, onGo, onRetry }: ReviewPanelProps) {
  const { view, error } = review;
  return (
    <div className="card review">
      <h2>复盘</h2>
      {error && <div className="error" role="alert">{error}</div>}
      {!view ? (
        !error && <p className="muted small">正在读取复盘……</p>
      ) : view.status === "none" ? (
        <>
          <p className="small review-intro">
            用引擎逐步分析整盘棋：每步评级，算出准确率和胜率曲线，找出 3 个关键时刻并讲解。
          </p>
          <button className="primary" onClick={() => void review.start()} disabled={!engineReady}
            title={engineReady ? undefined : "复盘需要先安装象棋引擎"}>开始复盘</button>
          {!engineReady && <p className="muted small">复盘需要象棋引擎（见 README「安装象棋引擎」）。</p>}
        </>
      ) : view.status === "running" ? (
        <Progress phase={view.phase} done={view.progress} total={view.total} />
      ) : view.status === "error" ? (
        <>
          <div className="error" role="alert">{view.error}</div>
          <div className="buttons">
            <button onClick={() => void review.start(true)} disabled={!engineReady}>重新复盘</button>
          </div>
        </>
      ) : view.report ? (
        <Report report={view.report} ply={ply} busy={busy} onGo={onGo} onRetry={onRetry}
          onRedo={() => void review.start(true)} engineReady={engineReady} />
      ) : null}
      {llm && <p className="muted small review-llm">{llmText(llm)}</p>}
    </div>
  );
}

function Progress({ phase, done, total }: { phase: string | null; done: number; total: number }) {
  const ratio = total > 0 ? done / total : 0;
  return (
    <div className="review-progress">
      <p className="small">
        {phase === "explaining"
          ? `正在讲解关键时刻：${done} / ${total}`
          : `引擎分析中：${done} / ${total} 个局面`}
      </p>
      <div className="progress-bar"><div style={{ width: `${Math.round(ratio * 100)}%` }} /></div>
    </div>
  );
}

interface ReportProps {
  report: ReviewReport;
  ply: number;
  busy: boolean;
  engineReady: boolean;
  onGo: (ply: number) => void;
  onRetry: (move: ReviewMove) => void;
  onRedo: () => void;
}

function Report({ report, ply, busy, engineReady, onGo, onRetry, onRedo }: ReportProps) {
  const sides: Side[] = ["red", "black"];
  const mine = report.focus.length === 1 ? report.focus[0] : null;
  const moments = report.key_moments.map((p) => report.moves[p - 1]);
  return (
    <div className="review-report">
      <div className="accuracy-row">
        {sides.map((side) => (
          <Accuracy key={side} side={side} stats={report.stats[side]} mine={mine === side}
            dim={mine !== null && mine !== side} />
        ))}
      </div>
      <WinRateChart curve={report.curve} moves={report.moves} ply={ply} onSelect={onGo} />
      {report.terminal && <p className="muted small">终局：{report.terminal}</p>}

      <h3>关键时刻</h3>
      {moments.length === 0 ? (
        <p className="muted small">
          {mine ? "你这盘没有明显的失误，" : "这盘棋没有明显的失误，"}没有需要特别讲解的关键时刻。
        </p>
      ) : (
        <ol className="key-moments">
          {moments.map((m) => (
            <li key={m.ply} className={m.ply === ply ? "current" : undefined}>
              <div className="key-moment-head">
                <button className="link" onClick={() => onGo(m.ply)}>
                  第 {m.ply} 步 · {SIDE_TEXT[m.side]} <strong>{m.cn}</strong>
                </button>
                <GradeBadge grade={m.grade} />
                <span className="muted small">期望得分 −{percent(m.drop)}</span>
              </div>
              {m.explanation && <ExplanationBlock explanation={m.explanation} />}
              <div className="buttons">
                <button onClick={() => onGo(m.ply)}>看这步</button>
                <button onClick={() => onRetry(m)} disabled={busy || !engineReady}
                  title={`回到这步之前，由你执${SIDE_TEXT[m.side]}和 AI 重新下`}>再试一次</button>
              </div>
            </li>
          ))}
        </ol>
      )}
      {report.tags.length > 0 && (
        <p className="small review-tags">
          本局的问题：{report.tags.map((t) => (
            <span key={t.tag} className="tag">{t.tag}{t.count > 1 ? ` ×${t.count}` : ""}</span>
          ))}
        </p>
      )}
      <p className="muted small review-meta">
        {report.engine ?? "引擎"} · 每个局面 {report.movetime_ms ?? "?"} 毫秒 · {report.created_at}
        <button className="link" onClick={onRedo} disabled={!engineReady}>重新复盘</button>
      </p>
    </div>
  );
}

function Accuracy({ side, stats, mine, dim }: { side: Side; stats: SideStats; mine: boolean; dim: boolean }) {
  const counts = COUNTED.filter((g) => stats.grades[g] > 0);
  const phases = PHASES.filter((p) => stats.phases[p] !== null && stats.phases[p] !== undefined);
  return (
    <div className={`accuracy ${side}${dim ? " dim" : ""}`}>
      <div className="accuracy-title">{SIDE_TEXT[side]}{mine && "（你）"}</div>
      <div className="accuracy-value">
        {stats.accuracy === null ? "—" : Math.round(stats.accuracy)}
        <span className="muted small"> 准确率</span>
      </div>
      {phases.length > 0 && (
        <div className="muted small">
          {phases.map((p) => `${p} ${Math.round(stats.phases[p]!)}`).join(" · ")}
        </div>
      )}
      <div className="accuracy-grades small">
        {counts.length === 0
          ? <span className="muted">没有失误</span>
          : counts.map((g) => (
            <span key={g} className={GRADE_STYLE[g].className}>{g} {stats.grades[g]}</span>
          ))}
      </div>
    </div>
  );
}

interface MoveReviewProps {
  llm: LLMStatus | null;
  move: ReviewMove;
  /** 正在讲解这一步 */
  loading: boolean;
  /** 正在讲解（任何一步），按钮暂不可用 */
  busy: boolean;
  /** 生成讲解；refresh 为 true 时重新生成（模板讲解换成大模型讲解） */
  onExplain: (refresh: boolean) => void;
  /** 能否重新生成讲解 */
  canRefresh?: boolean;
  title?: string;
  /** 名局解读里这步棋的意图 */
  intent?: Explanation | null;
  /** 加入错题本（这步不是引擎最佳时显示按钮）；返回 false 表示本来就在错题本里 */
  onAddCard?: () => Promise<boolean>;
}

/** 一步棋的评级、引擎推荐和讲解（没有讲解时可以按需生成）。打谱和边下边分析共用。 */
export function MoveReview({
  llm, move: m, loading, busy, onExplain, canRefresh = true, title, intent, onAddCard,
}: MoveReviewProps) {
  const canUpgrade = canRefresh && m.explanation?.source === "template" && !!llm?.ready;
  const [cardState, setCardState] = useState<"idle" | "saving" | "added" | "exists" | "error">("idle");
  const addCard = async () => {
    if (!onAddCard) return;
    setCardState("saving");
    try {
      setCardState((await onAddCard()) ? "added" : "exists");
    } catch {
      setCardState("error");
    }
  };
  const notBest = !!m.best_move && m.best_move !== m.iccs;
  return (
    <div className="card move-review">
      {title && <p className="muted small move-review-title">{title}</p>}
      <h2>
        第 {m.ply} 步 · {SIDE_TEXT[m.side]} <span className="move-review-cn">{m.cn}</span>
        <GradeBadge grade={m.grade} />
      </h2>
      <p className="small">
        {SIDE_TEXT[m.side]}期望得分：{percent(m.win_before)} → {percent(m.win_after)}
        {m.drop > 0.005 && <span className="muted">（−{percent(m.drop)}）</span>}
        <span className="muted"> · {m.phase}</span>
      </p>
      {m.best_move && m.best_move !== m.iccs ? (
        <p className="small">
          引擎推荐：<strong>{m.best_cn}</strong>
          {m.best_pv_cn.length > 1 && <span className="muted"> {m.best_pv_cn.slice(1).join(" ")}</span>}
        </p>
      ) : m.best_move ? (
        <p className="small muted">这步就是引擎推荐的着法。</p>
      ) : null}
      {m.explanation ? (
        <>
          <ExplanationBlock explanation={m.explanation} />
          {canUpgrade && (
            <button className="link" onClick={() => onExplain(true)} disabled={busy}>
              {loading ? "讲解中……" : "用大模型重新讲解"}
            </button>
          )}
        </>
      ) : (
        <button onClick={() => onExplain(false)} disabled={busy}>
          {loading ? "讲解中……" : "讲解这步"}
        </button>
      )}
      {intent && (
        <div className="move-intent">
          <p className="muted small">名局解读：这步想干什么</p>
          <ExplanationBlock explanation={intent} />
        </div>
      )}
      {onAddCard && notBest && (
        <p className="small move-card">
          {cardState === "added" ? "已加入错题本。" : cardState === "exists" ? "这一题已经在错题本里了。" : (
            <button className="link" onClick={() => void addCard()} disabled={cardState === "saving"}
              title="以后在训练页复习：从这个局面找出引擎推荐的着法">
              {cardState === "error" ? "加入失败，再试一次" : "加入错题本"}
            </button>
          )}
        </p>
      )}
    </div>
  );
}
