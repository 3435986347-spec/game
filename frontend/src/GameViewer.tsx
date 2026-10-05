import { useCallback, useEffect, useMemo, useState } from "react";
import { AnalysisPanel, EvalBar } from "./AnalysisPanel";
import Board from "./Board";
import type { Arrow } from "./Board";
import ExplorerPanel from "./ExplorerPanel";
import MoveList from "./MoveList";
import { ApiError, RESULT_TEXT, api, errorText } from "./api";
import type { GameRecord, Mode, ReviewMove, Side } from "./api";
import { positionFromFen, sideToMove } from "./fen";
import { isTypingTarget } from "./keyboard";
import AnnotatePanel, { useAnnotate } from "./AnnotatePanel";
import ReviewPanel, { MoveReview, useLlmStatus } from "./ReviewPanel";
import { gameHash, guessHash, libraryBackHash, navigate } from "./router";
import { loadSettings, saveGameId } from "./settings";
import { useAnalysis } from "./useAnalysis";
import { useEngineStatus } from "./useEngineStatus";
import { useReview } from "./useReview";

const noMove = () => {};

interface GameViewerProps {
  id: number;
  /** 打开时显示第几步之后的局面（地址中的 ?ply=） */
  initialPly: number | null;
}

/** 打谱：逐步回放棋谱库中的一盘棋，查看局面统计和引擎分析，可以从任意一步开始试走或和 AI 下。 */
export default function GameViewer({ id, initialPly }: GameViewerProps) {
  const [record, setRecord] = useState<GameRecord | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; notFound: boolean } | null>(null);
  const [reload, setReload] = useState(0);
  const [ply, setPly] = useState(initialPly ?? 0);
  const [flipped, setFlipped] = useState(false);
  const [analysisOn, setAnalysisOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const { engine, checkFailed: engineCheckFailed, check: checkEngine } = useEngineStatus();
  const engineReady = engine?.ok ?? false;
  const review = useReview(id);
  const llm = useLlmStatus();
  const report = review.view?.status === "done" ? review.view.report : null;
  const annotate = useAnnotate(id, !!report);
  const [guessForm, setGuessForm] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.libraryGame(id).then(
      (r) => {
        if (cancelled) return;
        setRecord(r);
        setLoadError(null);
        setPly((p) => Math.min(p, r.moves.length));
      },
      (e) => {
        if (cancelled) return;
        const notFound = e instanceof ApiError && e.status === 404;
        setLoadError({ message: notFound ? "找不到这盘棋，可能已被删除。" : errorText(e), notFound });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [id, reload]);

  const last = record?.moves.length ?? 0;
  const go = useCallback((n: number) => setPly(Math.max(0, Math.min(last, n))), [last]);

  // 当前步数写进地址（不产生新的历史记录），刷新页面后仍停在这一步
  useEffect(() => {
    if (!record) return;
    const hash = ply > 0 ? `${gameHash(id)}?ply=${ply}` : gameHash(id);
    if (location.hash !== hash) history.replaceState(history.state, "", hash);
  }, [record, id, ply]);

  // 快捷键：← → 后退前进，Home / End 开局 / 终局，F 翻转棋盘
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "ArrowLeft") setPly((p) => Math.max(0, p - 1));
      else if (e.key === "ArrowRight") setPly((p) => Math.min(last, p + 1));
      else if (e.key === "Home") setPly(0);
      else if (e.key === "End") setPly(last);
      else if (e.key.toLowerCase() === "f") setFlipped((f) => !f);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [last]);

  const fen = record?.fens[ply];
  const position = useMemo(
    () => record && fen
      ? positionFromFen(fen, ply > 0 ? record.moves[ply - 1].iccs : null, record.checks[ply] ?? false)
      : null,
    [record, fen, ply],
  );
  const playedMoves = useMemo(() => record?.moves.slice(0, ply).map((m) => m.iccs) ?? [], [record, ply]);
  const analysis = useAnalysis(
    analysisOn && engineReady,
    record ? { fen: record.initial_fen, moves: playedMoves } : undefined,
    fen,
    false,
  );

  // 从第 n 步之后的局面开始一盘新对局，切换到对弈页面。和 AI 下时你执该局面轮到的一方
  const startFrom = async (n: number, mode: Mode) => {
    if (!record) return;
    const settings = loadSettings();
    const side: Side = sideToMove(record.fens[n]);
    setBusy(true);
    try {
      const game = await api.newGame({
        mode,
        fen: record.initial_fen,
        moves: record.moves.slice(0, n).map((m) => m.iccs),
        user_side: mode === "vs_ai" ? side : settings.user_side,
        ai_level: settings.ai_level,
      });
      saveGameId(game.id);
      navigate("#/");
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  const startFromHere = (mode: Mode) => startFrom(ply, mode);

  // 猜着练习：执哪一方、开头跳过几步
  const startGuess = async (side: Side, skip: number) => {
    setBusy(true);
    try {
      const session = await api.startGuess(id, side, skip);
      navigate(guessHash(session.id));
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  // 把这一步加入错题本：局面是走这步之前，正确着法是引擎推荐
  const addCard = async (move: ReviewMove) => {
    if (!record || !move.best_move) return false;
    const result = await api.addCard({
      fen: record.fens[move.ply - 1],
      solution: move.best_move,
      played: move.iccs,
      source: `打谱：${players(record)} 第 ${move.ply} 步（${move.grade}）`,
      source_game_id: record.id,
      source_ply: move.ply,
      explanation: move.explanation,
    });
    return result.created;
  };
  // 复盘的「再试一次」：回到这步之前，由走这步的一方和 AI 重新下
  const retry = (move: ReviewMove) => startFrom(move.ply - 1, "vs_ai");

  const remove = async () => {
    if (!record) return;
    if (!window.confirm(`从棋谱库删除这盘棋？\n${players(record)}\n删除后不能恢复。`)) return;
    setBusy(true);
    try {
      await api.deleteLibraryGame(record.id);
      navigate(libraryBackHash());
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const copyFen = async () => {
    if (!fen) return;
    try {
      await navigator.clipboard.writeText(fen);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("无法写入剪贴板，请手动选择复制");
    }
  };

  if (!record || !position || !fen) {
    return (
      <main className="viewer-status">
        {loadError ? (
          <div className="card">
            <p className="error">
              {loadError.message}
              {!loadError.notFound && (
                <button className="link" onClick={() => setReload((n) => n + 1)}>重试</button>
              )}
            </p>
            <a href={libraryBackHash()}>返回棋谱库</a>
          </div>
        ) : (
          <p className="muted">正在加载棋谱……</p>
        )}
      </main>
    );
  }

  const arrows: Arrow[] = [];
  if (analysis.info?.lines[0]) arrows.push({ move: analysis.info.lines[0].move, kind: "analysis" });
  const toMove = sideToMove(fen);
  const lastMove = ply > 0 ? record.moves[ply - 1] : null;
  const round = record.round && /^\d+$/.test(record.round) ? `第 ${record.round} 轮` : record.round;
  const meta = [record.event, round, record.date, record.site].filter(Boolean);

  return (
    <main className="layout">
      <section className="board-column">
        <div className="board-panel">
          {analysisOn && engineReady ? (
            <EvalBar redWin={analysis.info?.lines[0]?.red_win ?? null} flipped={flipped} />
          ) : report ? (
            <EvalBar redWin={report.curve[ply] ?? null} flipped={flipped} />
          ) : null}
          <Board position={position} flipped={flipped} onMove={noMove} interactive={false} arrows={arrows} />
        </div>
        <div className="viewer-nav">
          <button onClick={() => go(0)} disabled={ply === 0} title="开局（Home）" aria-label="开局">⏮</button>
          <button onClick={() => go(ply - 1)} disabled={ply === 0} title="上一步（←）" aria-label="上一步">◀</button>
          <span className="viewer-ply">
            {lastMove ? <>第 {ply} 步 <strong>{lastMove.cn}</strong></> : "开局局面"}
            <span className="muted"> / 共 {last} 步</span>
          </span>
          <button onClick={() => go(ply + 1)} disabled={ply >= last} title="下一步（→）" aria-label="下一步">▶</button>
          <button onClick={() => go(last)} disabled={ply >= last} title="终局（End）" aria-label="终局">⏭</button>
          <button onClick={() => setFlipped((f) => !f)} title="翻转棋盘（F）">翻转</button>
        </div>
      </section>

      <aside className="side-panel">
        <div className="card viewer-info">
          <a className="back-link small" href={libraryBackHash()}>← 返回棋谱库</a>
          <h2 className="viewer-title">
            <span className="red-name">{record.red || "红方"}</span>
            <span className="muted"> vs </span>
            <span>{record.black || "黑方"}</span>
          </h2>
          {meta.length > 0 && <p className="muted small">{meta.join(" · ")}</p>}
          <p className="viewer-result">
            <span className={`result-badge result-${record.result === "1-0" ? "red" : record.result === "0-1" ? "black" : "other"}`}>
              {RESULT_TEXT[record.result] ?? record.result}
            </span>
            {record.opening && <span>开局：{record.opening}</span>}
            {record.kind === "my_game" && <span className="tag">我的对局</span>}
          </p>
          {error && <div className="error" role="alert">{error}</div>}
          <div className="buttons">
            <button onClick={() => void startFromHere("free")} disabled={busy}
              title="在对弈页面从当前局面接着走，双方都由你来走">从这里试走</button>
            <button onClick={() => void startFromHere("vs_ai")} disabled={busy || !engineReady}
              title={engineReady
                ? `你执${toMove === "red" ? "红" : "黑"}（当前轮到的一方），难度沿用上次新对局的设置`
                : "人机对战需要先安装象棋引擎"}>
              从这里和 AI 下
            </button>
            <button onClick={() => setGuessForm((v) => !v)} disabled={busy || !engineReady || last === 0}
              title={engineReady ? "跟着这盘棋一步步猜下一着，引擎打分" : "猜着练习要用引擎打分"}>
              猜着练习
            </button>
            <button onClick={() => void remove()} disabled={busy} className="danger">删除</button>
          </div>
          {guessForm && (
            <GuessForm busy={busy} onStart={(side, skip) => void startGuess(side, skip)}
              onCancel={() => setGuessForm(false)} />
          )}
          {record.source && <p className="muted small">来源：{record.source}</p>}
        </div>

        {record.moves.length > 0 && (
          <ReviewPanel review={review} llm={llm} ply={ply} engineReady={engineReady} busy={busy}
            onGo={go} onRetry={(m) => void retry(m)} />
        )}

        {report && ply > 0 && report.moves[ply - 1] && (
          <MoveReview llm={llm} move={report.moves[ply - 1]} loading={review.explaining === ply}
            busy={review.explaining !== null} onExplain={(refresh) => void review.explain(ply, refresh)}
            intent={annotate.view?.moves.find((m) => m.ply === ply)?.intent}
            onAddCard={() => addCard(report.moves[ply - 1])} />
        )}

        {report && <AnnotatePanel annotate={annotate} ply={ply} engineReady={engineReady} onGo={go} />}

        <div className="card moves">
          <h2>着法</h2>
          <MoveList moves={record.moves} firstMover={sideToMove(record.initial_fen)}
            currentIndex={ply - 1} onSelect={(i) => go(i + 1)} emptyText="这盘棋没有着法记录。"
            grades={report?.moves.map((m) => m.grade)} />
        </div>

        <div className="card">
          <h2>棋谱库统计</h2>
          <ExplorerPanel fen={fen} played={record.moves[ply]?.iccs ?? null} ply={ply} />
        </div>

        <div className="card engine">
          <h2>引擎分析</h2>
          {engine === null ? (
            engineCheckFailed ? (
              <p className="error">
                无法获取引擎状态（后端是否已启动？）
                <button className="link" onClick={checkEngine}>重试</button>
              </p>
            ) : (
              <p className="muted small">正在检查象棋引擎……</p>
            )
          ) : engineReady ? (
            <>
              <label className="toggle">
                <input type="checkbox" checked={analysisOn}
                  onChange={(e) => setAnalysisOn(e.target.checked)} />
                显示引擎对当前局面的实时分析
              </label>
              {analysisOn && <AnalysisPanel info={analysis.info} error={analysis.error} finished={false} />}
            </>
          ) : (
            <p className="muted small">
              {engine.error}（安装方法见 README「安装象棋引擎」）
              <button className="link" onClick={checkEngine}>重新检查</button>
            </p>
          )}
        </div>

        <div className="card fen">
          <h2>局面（FEN）</h2>
          <div className="fen-current">
            <code>{fen}</code>
            <button onClick={() => void copyFen()}>{copied ? "已复制" : "复制"}</button>
          </div>
        </div>

        <p className="hint-keys">快捷键：← → 后退 / 前进 · Home / End 开局 / 终局 · F 翻转棋盘</p>
      </aside>
    </main>
  );
}

function GuessForm({ busy, onStart, onCancel }: {
  busy: boolean;
  onStart: (side: Side, skip: number) => void;
  onCancel: () => void;
}) {
  const [side, setSide] = useState<Side>("red");
  const [skip, setSkip] = useState(10);
  return (
    <div className="guess-form small">
      <p>跟着这盘棋一步步猜你这一方的着法，每步 0–3 分，失分多的步会进错题本。</p>
      <div className="segmented">
        {(["red", "black"] as const).map((s) => (
          <label key={s} className={side === s ? "active" : undefined}>
            <input type="radio" name="guess-side" checked={side === s} onChange={() => setSide(s)} />
            我执{s === "red" ? "红" : "黑"}
          </label>
        ))}
      </div>
      <label className="guess-skip">
        开头跳过
        <input type="number" min={0} max={60} value={skip}
          onChange={(e) => setSkip(Math.max(0, Number(e.target.value) || 0))} />
        步不猜（把时间花在中局和残局）
      </label>
      <div className="buttons">
        <button className="primary" onClick={() => onStart(side, skip)} disabled={busy}>开始</button>
        <button onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}

function players(record: GameRecord): string {
  return `${record.red || "红方"} vs ${record.black || "黑方"}`;
}
