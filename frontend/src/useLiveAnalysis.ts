import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "./api";
import type { Grade, GameView, MoveAnalysis } from "./api";
import { sideToMove } from "./fen";

export interface LiveAnalysisState {
  /** 最近一步要分析的棋（人机对战只看你自己的着法）的分析结果；分析中或还没有时为 null */
  latest: MoveAnalysis | null;
  /** 正在分析第几步 */
  pending: number | null;
  error: string | null;
  /** 各步的评级（下标与着法列表相同，没分析的为 undefined），给着法列表加标记 */
  grades: (Grade | undefined)[];
  explaining: boolean;
  explain: () => Promise<void>;
  /** 分析出错后重试 */
  retry: () => void;
}

/** 结果按「对局 + 到这步为止的着法」作键：悔棋后走了别的着法不会显示旧结果。 */
function keyOf(game: GameView, ply: number): string {
  return `${game.id}|${game.moves.slice(0, ply).map((m) => m.iccs).join(" ")}`;
}

/**
 * 走一步分析一步：每当出现新的着法，就请后端给最近一步评级。
 * 人机对战只分析你自己的着法——分析 AI 的着法等于提示你对方哪里走错了；自由对弈每步都分析。
 */
export function useLiveAnalysis(game: GameView | null, enabled: boolean): LiveAnalysisState {
  const [results, setResults] = useState<Map<string, MoveAnalysis>>(() => new Map());
  const [pending, setPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [retries, setRetries] = useState(0);
  const requested = useRef(new Set<string>());

  // 要分析的那一步：最近一步该分析的着法
  const target = useMemo(() => {
    if (!game || !enabled) return null;
    const first = sideToMove(game.initial_fen);
    for (let i = game.moves.length - 1; i >= 0; i--) {
      const side = i % 2 === 0 ? first : first === "red" ? "black" : "red";
      if (game.mode !== "vs_ai" || side === game.user_side) return i + 1;
    }
    return null;
  }, [game, enabled]);
  const targetKey = game && target ? keyOf(game, target) : null;

  useEffect(() => {
    if (!game || !target || !targetKey || requested.current.has(targetKey)) return;
    requested.current.add(targetKey);
    setPending(target);
    setError(null);
    api.analyseMove(game.id, target).then(
      (result) => setResults((prev) => new Map(prev).set(targetKey, result)),
      (e) => {
        requested.current.delete(targetKey); // 出错（如引擎没准备好）后允许重试
        setError(errorText(e));
      },
    ).finally(() => setPending((p) => (p === target ? null : p)));
    // game 每次走子都会变；只在要分析的那一步变化（或手动重试）时请求
  }, [targetKey, retries]);

  const latest = targetKey ? results.get(targetKey) ?? null : null;

  const grades = useMemo(() => {
    if (!game) return [];
    return game.moves.map((_, i) => results.get(keyOf(game, i + 1))?.grade);
  }, [game, results]);

  const explain = useCallback(async () => {
    if (!game || !latest || !targetKey) return;
    setExplaining(true);
    try {
      const explanation = await api.explainGameMove(game.id, latest.ply);
      setResults((prev) => new Map(prev).set(targetKey, { ...latest, explanation }));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setExplaining(false);
    }
  }, [game, latest, targetKey]);

  const retry = useCallback(() => setRetries((n) => n + 1), []);
  return { latest, pending, error, grades, explaining, explain, retry };
}
