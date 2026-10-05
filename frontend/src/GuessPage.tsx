import { useEffect, useState } from "react";
import { percent } from "./AnalysisPanel";
import Board from "./Board";
import type { Arrow } from "./Board";
import { ApiError, api, errorText } from "./api";
import type { GuessAnswer, GuessSession, PositionView } from "./api";
import ExplanationBlock from "./ExplanationBlock";
import { positionFromFen } from "./fen";
import { gameHash, trainHash } from "./router";

const SIDE_TEXT = { red: "红方", black: "黑方" };
const STARS = ["☆☆☆", "★☆☆", "★★☆", "★★★"];

interface Reveal {
  answer: GuessAnswer;
  next: GuessSession;
}

/** 猜着练习：轮到你时走出你认为最好的一步，揭晓大师的着法和得分，再继续。 */
export default function GuessPage({ id }: { id: number }) {
  const [session, setSession] = useState<GuessSession | null>(null);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    api.getGuess(id).then(setSession, (e) => {
      setNotFound(e instanceof ApiError && e.status === 404);
      setError(errorText(e));
    });
  }, [id]);

  const answer = async (move: string | null) => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.answerGuess(session.id, move);
      setReveal({ answer: result.answer, next: result.session });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const proceed = () => {
    if (!reveal) return;
    setSession(reveal.next);
    setReveal(null);
  };

  if (!session) {
    return (
      <main className="train-home">
        {error ? (
          <div className="card">
            <p className="error">{error}</p>
            {notFound && <a href={trainHash()}>返回训练</a>}
          </div>
        ) : (
          <p className="muted">正在加载……</p>
        )}
      </main>
    );
  }

  // 揭晓时棋盘停在作答前的局面，画出你的着法（蓝）和大师的着法（绿）
  let board: PositionView;
  const arrows: Arrow[] = [];
  if (reveal) {
    board = positionFromFen(reveal.answer.fen_before);
    arrows.push({ move: reveal.answer.master_move, kind: "hint" });
    if (reveal.answer.user_move && reveal.answer.user_move !== reveal.answer.master_move) {
      arrows.push({ move: reveal.answer.user_move, kind: "analysis" });
    }
  } else {
    board = { ...positionFromFen(session.fen, session.last_move, session.in_check), legal_moves: session.legal_moves };
  }
  const shown = reveal ? reveal.next : session;
  const answered = shown.answers.length;
  const matched = shown.answers.filter((a) => a.same).length;

  return (
    <main className="layout">
      <section className="board-panel">
        <Board position={board} flipped={session.side === "black"} arrows={arrows}
          interactive={!session.finished && !reveal && !busy} onMove={(m) => void answer(m)} />
      </section>
      <aside className="side-panel">
        <div className="card">
          <a className="back-link small" href={gameHash(session.game_id)}>← 打开这盘棋</a>
          <h2 className="viewer-title">
            <span className="red-name">{session.red || "红方"}</span>
            <span className="muted"> vs </span>
            <span>{session.black || "黑方"}</span>
          </h2>
          {session.event && <p className="muted small">{session.event}</p>}
          <p className="small">
            你执{SIDE_TEXT[session.side]} · 第 {Math.min(shown.current_ply + 1, shown.total_plies)} 步 / 共{" "}
            {shown.total_plies} 步 · 得分 <strong>{shown.score}</strong> / {shown.max_score}
            {answered > 0 && ` · 和大师一样 ${matched}/${answered}`}
          </p>
          {error && <div className="error" role="alert">{error}</div>}
          {!session.finished && !reveal && (
            <div className="buttons">
              <button onClick={() => void answer(null)} disabled={busy}>不会，看大师着法</button>
              {busy && <span className="muted small">引擎打分中……</span>}
            </div>
          )}
          {!session.finished && !reveal && !busy && (
            <p className="muted small">轮到{SIDE_TEXT[session.side]}：你会怎么走？在棋盘上走出来。</p>
          )}
        </div>

        {reveal && <AnswerCard answer={reveal.answer} finished={reveal.next.finished} onNext={proceed} />}

        {session.finished && session.summary && (
          <div className="card guess-summary">
            <h2>猜完了</h2>
            <p className="train-big">{session.score} <span className="muted small">/ {session.max_score} 分</span></p>
            <p className="small">
              和大师着法一样：{session.summary.matched} / {session.summary.answered}
              {session.summary.match_rate !== null && `（${percent(session.summary.match_rate)}）`}
              {session.summary.avg_loss !== null && ` · 平均每步比大师差 ${percent(session.summary.avg_loss)}`}
            </p>
            {session.summary.worst.length > 0 && (
              <p className="small">失分最多：第 {session.summary.worst.join("、")} 步</p>
            )}
            <p className="small">
              {session.summary.cards_added > 0
                ? <>其中 {session.summary.cards_added} 步已加入错题本，<a href={trainHash()}>去训练页复习</a>。</>
                : "没有需要加入错题本的步。"}
            </p>
          </div>
        )}

        {shown.answers.length > 0 && (
          <div className="card">
            <h2>作答记录</h2>
            <ol className="answer-list small">
              {[...shown.answers].reverse().map((a) => (
                <li key={a.ply}>
                  <span className="answer-stars">{STARS[a.points]}</span>
                  第 {a.ply} 步：你 {a.user_cn ?? "（不会）"} · 大师 <strong>{a.master_cn}</strong>
                  {a.master_not_best && <span className="tag">大师也非最佳</span>}
                </li>
              ))}
            </ol>
          </div>
        )}
      </aside>
    </main>
  );
}

function AnswerCard({ answer: a, finished, onNext }: { answer: GuessAnswer; finished: boolean; onNext: () => void }) {
  let verdict: string;
  if (!a.user_move) verdict = "没关系，看看大师怎么走";
  else if (a.same) verdict = "和大师想的一样！";
  else if (a.as_good) verdict = "你找到了同样好的着法";
  else if (a.points === 2) verdict = "差不多，略逊于大师着法";
  else if (a.points === 1) verdict = "可以，但大师的着法明显更好";
  else verdict = "这步问题比较大";
  return (
    <div className={`card guess-answer points-${a.points}`}>
      <p className="guess-verdict">
        <span className="answer-stars">{STARS[a.points]}</span> {verdict}
      </p>
      <p className="small">
        大师：<strong>{a.master_cn}</strong>（绿色箭头）
        {a.user_cn && !a.same && <> · 你：<strong>{a.user_cn}</strong>（蓝色箭头）</>}
      </p>
      {a.user_score !== null && a.master_score !== null && !a.same && (
        <p className="muted small">
          走完后你这一方的期望得分：大师着法 {percent(a.master_score)} · 你的着法 {percent(a.user_score)}
        </p>
      )}
      {a.master_not_best && a.best_cn && (
        <p className="small">此处大师着法也非最佳：引擎推荐 <strong>{a.best_cn}</strong>。</p>
      )}
      {a.explanation && <ExplanationBlock explanation={a.explanation} />}
      <div className="buttons">
        <button className="primary" onClick={onNext}>{finished ? "看总结" : "继续"}</button>
      </div>
    </div>
  );
}
