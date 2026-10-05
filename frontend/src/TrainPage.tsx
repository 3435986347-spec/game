import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "./api";
import type {
  CardResult,
  GuessSessionItem,
  Puzzle,
  PuzzleResult,
  TrainCard,
  TrainCardItem,
  TrainSummary,
} from "./api";
import Exercise from "./Exercise";
import { gameHash, guessHash, navigate, trainHash } from "./router";
import type { TrainMode } from "./router";

interface TrainPageProps {
  mode: TrainMode;
  theme: string | null;
}

/** 训练：今日复习（错题本）、做题、猜着练习的记录、错题本列表。 */
export default function TrainPage({ mode, theme }: TrainPageProps) {
  if (mode === "review") return <ReviewSession />;
  if (mode === "puzzle") return <PuzzleSession theme={theme} />;
  return <TrainHome />;
}

function TrainHome() {
  const [summary, setSummary] = useState<TrainSummary | null>(null);
  const [sessions, setSessions] = useState<GuessSessionItem[]>([]);
  const [cards, setCards] = useState<TrainCardItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.trainSummary().then(setSummary, (e) => setError(errorText(e)));
    api.listGuesses().then(setSessions, () => setSessions([]));
  }, []);
  useEffect(load, [load]);

  const showCards = () => {
    api.listCards().then(setCards, (e) => setError(errorText(e)));
  };
  const removeCard = async (id: number) => {
    await api.deleteCard(id);
    setCards((list) => list?.filter((c) => c.id !== id) ?? null);
    load();
  };

  if (error) return <main className="train-home"><p className="error">{error}</p></main>;
  if (!summary) return <main className="train-home"><p className="muted">正在加载……</p></main>;

  return (
    <main className="train-home">
      <div className="card train-card">
        <h2>今日复习</h2>
        <p className="train-big">{summary.due} <span className="muted small">题到期</span></p>
        <p className="muted small">
          错题本共 {summary.cards} 题（错题 {summary.due_mistakes} · 做错的题 {summary.due_puzzles} 道到期）。
          自己对局复盘里的失误、猜着失分最多的步、做错的题都会进来，按记忆规律安排复习：做对了间隔变长，做错了 10 分钟后再出。
        </p>
        <div className="buttons">
          <button className="primary" onClick={() => navigate(trainHash("review"))} disabled={summary.due === 0}>
            开始复习
          </button>
          <button onClick={showCards} disabled={summary.cards === 0}>查看错题本</button>
        </div>
        {cards && <CardList cards={cards} onRemove={(id) => void removeCard(id)} />}
      </div>

      <div className="card train-card">
        <h2>做题</h2>
        <p className="train-big">{Math.round(summary.rating)} <span className="muted small">等级分</span></p>
        <p className="muted small">
          题库共 {summary.puzzles} 道，做过 {summary.puzzles_attempted} 道，做对过 {summary.puzzles_solved} 道。
          题目从复盘过的对局里自动挑出「只有一步好棋」的局面，优先出没做过、难度接近你等级分的题。
        </p>
        {summary.puzzles === 0 ? (
          <p className="warning small">
            题库还是空的：在打谱页复盘对局会自动出题；也可以批量出题：
            <code>cd backend && uv run xiangqi-puzzles --games 10</code>
          </p>
        ) : (
          <>
            <div className="buttons">
              <button className="primary" onClick={() => navigate(trainHash("puzzle"))}>开始做题</button>
            </div>
            <div className="theme-chips small">
              按主题：
              {summary.themes.map((t) => (
                <button key={t.tag} className="chip" onClick={() => navigate(trainHash("puzzle", t.tag))}>
                  {t.tag} <span className="muted">{t.count}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="card train-card">
        <h2>猜着练习</h2>
        <p className="muted small">
          跟着大师的对局一步步猜下一着，引擎给你打分（0–3 分）。在棋谱库打开一盘棋，点「猜着练习」开始。
        </p>
        {sessions.length === 0 ? (
          <p className="muted small">还没有练过。</p>
        ) : (
          <ul className="guess-list">
            {sessions.map((s) => (
              <li key={s.id}>
                <a href={guessHash(s.id)}>
                  {s.red || "红方"} vs {s.black || "黑方"}
                </a>
                <span className="muted small">
                  执{s.side === "red" ? "红" : "黑"} · {s.score} / {s.max_score} 分 ·{" "}
                  {s.finished ? "已猜完" : `进行到第 ${s.current_ply} 步`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}

function CardList({ cards, onRemove }: { cards: TrainCardItem[]; onRemove: (id: number) => void }) {
  if (cards.length === 0) return <p className="muted small">错题本是空的。</p>;
  return (
    <ol className="card-list small">
      {cards.map((c) => (
        <li key={c.id}>
          <div>
            <span className="tag">{c.kind === "mistake" ? "错题" : "做错的题"}</span>
            正解 <strong>{c.solution_cn}</strong>
            {c.played_cn && <span className="muted">（当时走了 {c.played_cn}）</span>}
          </div>
          <div className="muted">
            {c.source} · 下次复习 {c.due_at.slice(0, 16)}
            {c.source_game_id !== null && c.source_ply !== null && (
              <> · <a href={`${gameHash(c.source_game_id)}?ply=${c.source_ply - (c.kind === "mistake" ? 1 : 0)}`}>原局面</a></>
            )}
            <button className="link" onClick={() => onRemove(c.id)}>移出</button>
          </div>
        </li>
      ))}
    </ol>
  );
}

function ReviewSession() {
  const [card, setCard] = useState<TrainCard | null>(null);
  const [due, setDue] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const next = useCallback(() => {
    api.nextCard().then(
      (r) => {
        setCard(r.card);
        setDue(r.due);
      },
      (e) => setError(errorText(e)),
    );
  }, []);
  useEffect(next, [next]);

  if (error) return <main className="train-home"><p className="error">{error}</p></main>;
  if (due === null) return <main className="train-home"><p className="muted">正在加载……</p></main>;
  if (!card) {
    return (
      <main className="train-home">
        <div className="card">
          <h2>今日复习</h2>
          <p>今天到期的题都复习完了。</p>
          <a href={trainHash()}>返回训练</a>
        </div>
      </main>
    );
  }
  return (
    <Exercise<CardResult>
      key={card.id}
      position={card}
      prompt={
        <>
          <p>{card.kind === "mistake" ? "错题" : "做错过的题"} · 还剩 {due} 题 · {card.source}</p>
          {card.played_cn && <p className="muted">当时走的是 {card.played_cn}，这次想想更好的走法。</p>}
          <p><a href={trainHash()}>返回训练</a></p>
        </>
      }
      onSubmit={(move) => api.answerCard(card.id, move)}
      resultExtra={(r) => (
        <p className="muted small">
          {r.interval_days > 0 ? `下次复习：${r.interval_days} 天后` : "10 分钟后会再出这题"}
        </p>
      )}
      onExplain={() => api.explainCard(card.id)}
      onNext={next}
    />
  );
}

function PuzzleSession({ theme }: { theme: string | null }) {
  const [puzzle, setPuzzle] = useState<Puzzle | null>(null);
  const [rating, setRating] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const next = useCallback(
    (exclude: number | null = null) => {
      api.nextPuzzle(theme, exclude).then(
        (r) => {
          setPuzzle(r.puzzle);
          setRating(r.rating);
        },
        (e) => setError(errorText(e)),
      );
    },
    [theme],
  );
  useEffect(() => next(), [next]);

  if (error) return <main className="train-home"><p className="error">{error}</p></main>;
  if (rating === null) return <main className="train-home"><p className="muted">正在加载……</p></main>;
  if (!puzzle) {
    return (
      <main className="train-home">
        <div className="card">
          <p>{theme ? `没有「${theme}」主题的题。` : "题库是空的。"}</p>
          <a href={trainHash()}>返回训练</a>
        </div>
      </main>
    );
  }
  return (
    <Exercise<PuzzleResult>
      key={puzzle.id}
      position={puzzle}
      prompt={
        <>
          <p>
            第 {puzzle.id} 题 · 难度 {puzzle.rating} · 你的等级分 {Math.round(rating)}
            {puzzle.theme && ` · 主题：${puzzle.theme}`}
          </p>
          <p><a href={trainHash()}>返回训练</a></p>
        </>
      }
      onSubmit={(move) => api.attemptPuzzle(puzzle.id, move)}
      resultExtra={(r) => (
        <div className="small">
          <p>
            等级分 {Math.round(r.rating_before)} → <strong>{Math.round(r.rating_after)}</strong>
            {r.card_added && <span className="muted">（已加入错题本）</span>}
          </p>
          <p className="explanation-tags">{r.tags.map((t) => <span key={t} className="tag">{t}</span>)}</p>
          {r.source_game_id !== null && r.source_ply !== null && (
            <p className="muted">
              出自棋谱：<a href={`${gameHash(r.source_game_id)}?ply=${r.source_ply}`}>打开这盘棋</a>
              {r.master_found === true && "（棋手当时走出了正解）"}
              {r.master_found === false && "（棋手当时错过了这步）"}
            </p>
          )}
        </div>
      )}
      onExplain={() => api.explainPuzzle(puzzle.id)}
      onNext={() => next(puzzle.id)}
    />
  );
}
