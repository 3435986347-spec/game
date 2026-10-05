import { useState } from "react";
import type { ReactNode } from "react";
import Board from "./Board";
import type { Arrow } from "./Board";
import { errorText } from "./api";
import type { ExercisePosition, Explanation, PositionView, TrainResult } from "./api";
import ExplanationBlock from "./ExplanationBlock";
import { positionFromFen } from "./fen";

const SIDE_TEXT = { red: "红方", black: "黑方" };

/** 练习题局面 → 棋盘需要的 PositionView（合法着法由后端给出）。 */
export function exerciseBoard(p: ExercisePosition, lastMove: string | null = null): PositionView {
  return { ...positionFromFen(p.fen, lastMove, p.in_check), legal_moves: p.legal_moves };
}

interface ExerciseProps<R extends TrainResult> {
  position: ExercisePosition;
  /** 题目说明（来源、当时的错着等） */
  prompt: ReactNode;
  onSubmit: (move: string | null) => Promise<R>;
  /** 结果下方的补充信息（下次复习时间、等级分变化……） */
  resultExtra?: (result: R) => ReactNode;
  onExplain: () => Promise<Explanation>;
  onNext: () => void;
  nextLabel?: string;
}

/** 一道练习题：在棋盘上走出你认为最好的一步，然后揭晓正解，可以看讲解。错题复习和做题共用。 */
export default function Exercise<R extends TrainResult>({
  position,
  prompt,
  onSubmit,
  resultExtra,
  onExplain,
  onNext,
  nextLabel = "下一题",
}: ExerciseProps<R>) {
  const [result, setResult] = useState<R | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [explaining, setExplaining] = useState(false);
  const flipped = position.turn === "black"; // 解题的一方在下面

  const submit = async (move: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const r = await onSubmit(move);
      setResult(r);
      setExplanation(r.explanation);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const explain = async () => {
    setExplaining(true);
    try {
      setExplanation(await onExplain());
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setExplaining(false);
    }
  };

  const arrows: Arrow[] = [];
  if (result) {
    arrows.push({ move: result.solution, kind: "hint" });
    if (result.move && result.move !== result.solution) arrows.push({ move: result.move, kind: "analysis" });
  }

  return (
    <main className="layout">
      <section className="board-panel">
        <Board position={exerciseBoard(position)} flipped={flipped} interactive={!result && !busy}
          onMove={(m) => void submit(m)} arrows={arrows} />
      </section>
      <aside className="side-panel">
        <div className="card exercise">
          <h2>轮到{SIDE_TEXT[position.turn]}：找出最好的一步</h2>
          <div className="small exercise-prompt">{prompt}</div>
          {error && <div className="error" role="alert">{error}</div>}
          {!result ? (
            <div className="buttons">
              <button onClick={() => void submit(null)} disabled={busy}>不会，看答案</button>
              {busy && <span className="muted small">判断中……</span>}
            </div>
          ) : (
            <div className={`exercise-result ${result.correct ? "correct" : "wrong"}`}>
              <p className="exercise-verdict">{result.correct ? "✓ 对了" : "✗ 不对"}</p>
              {result.move_cn && result.move !== result.solution && (
                <p className="small">你走的是 <strong>{result.move_cn}</strong>（蓝色箭头）
                  {result.correct && "，和正解差不多好"}</p>
              )}
              <p className="small">
                正解：<strong>{result.solution_cn}</strong>（绿色箭头）
                {result.pv_cn.length > 1 && <span className="muted"> 后续：{result.pv_cn.slice(1).join(" ")}</span>}
              </p>
              {resultExtra?.(result)}
              {explanation ? (
                <ExplanationBlock explanation={explanation} />
              ) : (
                <button className="link" onClick={() => void explain()} disabled={explaining}>
                  {explaining ? "讲解中……" : "讲解"}
                </button>
              )}
              <div className="buttons">
                <button className="primary" onClick={onNext}>{nextLabel}</button>
              </div>
            </div>
          )}
        </div>
        <p className="hint-keys">在棋盘上点棋子、再点落点作答。</p>
      </aside>
    </main>
  );
}
