import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "./api";
import type { Explanation, Grade, ReviewView } from "./api";

const POLL_MS = 800;

/** 评级 → 样式类名和着法列表里的标记。 */
export const GRADE_STYLE: Record<Grade, { className: string; mark: string }> = {
  妙着: { className: "grade-brilliant", mark: "!" },
  好棋: { className: "grade-good", mark: "" },
  可以: { className: "grade-ok", mark: "" },
  缓着: { className: "grade-inaccuracy", mark: "?!" },
  失误: { className: "grade-mistake", mark: "?" },
  漏着: { className: "grade-blunder", mark: "??" },
};

export interface ReviewState {
  view: ReviewView | null;
  /** 读取或开始复盘失败（不是复盘本身失败） */
  error: string | null;
  start: (force?: boolean) => Promise<void>;
  /** 讲解第 ply 步，结果写进复盘报告 */
  explain: (ply: number, refresh?: boolean) => Promise<void>;
  explaining: number | null;
  reload: () => void;
}

/** 棋谱库中一盘棋的复盘：读取已有结果，复盘进行中时轮询进度。 */
export function useReview(libraryId: number): ReviewState {
  const [view, setView] = useState<ReviewView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [explaining, setExplaining] = useState<number | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setView(null);
    api.getReview(libraryId).then(
      (v) => !cancelled && setView(v),
      (e) => !cancelled && setError(errorText(e)),
    );
    return () => {
      cancelled = true;
    };
  }, [libraryId, reloadKey]);

  const running = view?.status === "running";
  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    const timer = setInterval(() => {
      api.getReview(libraryId).then(
        (v) => {
          if (!cancelled) {
            setView(v);
            setError(null);
          }
        },
        (e) => !cancelled && setError(errorText(e)),
      );
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [libraryId, running]);

  const start = useCallback(async (force = false) => {
    setError(null);
    try {
      setView(await api.startReview(libraryId, force));
    } catch (e) {
      setError(errorText(e));
    }
  }, [libraryId]);

  const explain = useCallback(async (ply: number, refresh = false) => {
    setExplaining(ply);
    setError(null);
    try {
      const explanation: Explanation = await api.explainMove(libraryId, ply, refresh);
      setView((v) => {
        if (!v?.report) return v;
        const moves = v.report.moves.map((m) => (m.ply === ply ? { ...m, explanation } : m));
        return { ...v, report: { ...v.report, moves } };
      });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setExplaining(null);
    }
  }, [libraryId]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  return { view, error, start, explain, explaining, reload };
}
