import { useCallback, useEffect, useState } from "react";
import { percent } from "./AnalysisPanel";
import { api, errorText } from "./api";
import type { AnnotateView } from "./api";
import { GradeBadge } from "./ReviewPanel";

const POLL_MS = 800;
const SIDE_TEXT = { red: "红方", black: "黑方" };

export interface AnnotateState {
  view: AnnotateView | null;
  error: string | null;
  start: (force?: boolean) => Promise<void>;
}

/** 名局解读的状态：复盘完成后读取已有解读，解读进行中时轮询进度。 */
export function useAnnotate(libraryId: number, enabled: boolean): AnnotateState {
  const [view, setView] = useState<AnnotateView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setView(null);
      return;
    }
    let cancelled = false;
    api.getAnnotate(libraryId).then(
      (v) => !cancelled && setView(v),
      (e) => !cancelled && setError(errorText(e)),
    );
    return () => {
      cancelled = true;
    };
  }, [libraryId, enabled]);

  const running = view?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      api.getAnnotate(libraryId).then(setView, (e) => setError(errorText(e)));
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [libraryId, running]);

  const start = useCallback(async (force = false) => {
    setError(null);
    try {
      setView(await api.startAnnotate(libraryId, force));
    } catch (e) {
      setError(errorText(e));
    }
  }, [libraryId]);

  return { view, error, start };
}

interface AnnotatePanelProps {
  annotate: AnnotateState;
  ply: number;
  engineReady: boolean;
  onGo: (ply: number) => void;
}

/** 名局解读：关键着法的意图 + 分阶段总结。 */
export default function AnnotatePanel({ annotate, ply, engineReady, onGo }: AnnotatePanelProps) {
  const { view, error } = annotate;
  return (
    <div className="card annotate">
      <h2>名局解读</h2>
      {error && <div className="error" role="alert">{error}</div>}
      {!view ? (
        !error && <p className="muted small">正在读取……</p>
      ) : view.status === "none" ? (
        <>
          <p className="small review-intro">
            推断转折点上每步棋「想干什么」（包括这步制造的威胁），再分开局、中局、残局总结这盘棋。
          </p>
          <button onClick={() => void annotate.start()} disabled={!engineReady}>AI 解读</button>
        </>
      ) : view.status === "running" ? (
        <div className="review-progress">
          <p className="small">正在解读：{view.progress} / {view.total}</p>
          <div className="progress-bar">
            <div style={{ width: `${view.total ? Math.round((view.progress / view.total) * 100) : 0}%` }} />
          </div>
        </div>
      ) : view.status === "error" ? (
        <>
          <div className="error" role="alert">{view.error}</div>
          <button onClick={() => void annotate.start(true)} disabled={!engineReady}>重新解读</button>
        </>
      ) : (
        <>
          {view.summary && (
            <dl className="annotate-summary small">
              <dt>开局</dt><dd>{view.summary.opening}</dd>
              <dt>中局</dt><dd>{view.summary.middlegame}</dd>
              <dt>残局</dt><dd>{view.summary.endgame}</dd>
              <dt>总评</dt><dd>{view.summary.overall}</dd>
            </dl>
          )}
          {view.moves.length > 0 && (
            <ol className="annotate-moves">
              {view.moves.map((m) => (
                <li key={m.ply} className={m.ply === ply ? "current" : undefined}>
                  <button className="link" onClick={() => onGo(m.ply)}>
                    第 {m.ply} 步 · {SIDE_TEXT[m.side]} <strong>{m.cn}</strong>
                  </button>
                  <GradeBadge grade={m.grade} />
                  <span className="muted small">红方 {percent(m.red_before)} → {percent(m.red_after)}</span>
                  <p className="small">{m.intent.headline}</p>
                </li>
              ))}
            </ol>
          )}
          <p className="muted small">
            {view.summary?.source === "llm" ? "解读：大模型" : "模板解读（没有配置大模型时）"}
            <button className="link" onClick={() => void annotate.start(true)} disabled={!engineReady}>重新解读</button>
          </p>
        </>
      )}
    </div>
  );
}
