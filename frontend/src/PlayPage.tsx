import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AnalysisPanel, EvalBar } from "./AnalysisPanel";
import Board from "./Board";
import type { Arrow } from "./Board";
import ExplorerPanel from "./ExplorerPanel";
import MoveList from "./MoveList";
import NewGamePanel from "./NewGamePanel";
import { ApiError, api, errorText, squareFromIccs } from "./api";
import type { GameView, HintLevel, HintView, NewGameOptions, Side } from "./api";
import { sideToMove } from "./fen";
import { isTypingTarget } from "./keyboard";
import { MoveReview, useLlmStatus } from "./ReviewPanel";
import { gameHash, navigate, trainHash } from "./router";
import { loadLiveAnalysis, loadSavedGameId, saveGameId, saveLiveAnalysis } from "./settings";
import { useAnalysis } from "./useAnalysis";
import { useEngineStatus } from "./useEngineStatus";
import { useLiveAnalysis } from "./useLiveAnalysis";

const AI_MIN_DELAY_MS = 400; // AI 走得太快时稍等一下，看得清对方走了哪步

const sideText = (side: Side) => (side === "red" ? "红方" : "黑方");
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function resultText(game: GameView): string {
  const result = game.position.result!;
  if (game.mode !== "vs_ai" || result.winner === null) return result.text;
  return `${result.winner === game.user_side ? "你赢了！" : "AI 获胜。"}${result.text}`;
}

/** 对弈页面：人机对战 / 自由对弈、提示、引擎分析、棋谱库统计。 */
export default function PlayPage() {
  const [game, setGame] = useState<GameView | null>(null);
  const { engine, checkFailed: engineCheckFailed, check: checkEngine } = useEngineStatus();
  const [error, setError] = useState<string | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [showNewGame, setShowNewGame] = useState(false);
  const [moveInput, setMoveInput] = useState("");
  const [copied, setCopied] = useState(false);
  const [thinkingFor, setThinkingFor] = useState<string | null>(null); // 正在等 AI 走棋的对局
  const [aiRetry, setAiRetry] = useState(0);
  const [hint, setHint] = useState<(HintView & { gameId: string; fen: string }) | null>(null);
  const [hintLoading, setHintLoading] = useState(false);
  const [analysisOn, setAnalysisOn] = useState(false);
  const [explorerOn, setExplorerOn] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [liveOn, setLiveOn] = useState(loadLiveAnalysis);
  const aiRequested = useRef<string | null>(null);
  const llm = useLlmStatus();
  const [due, setDue] = useState(0);
  useEffect(() => {
    api.trainSummary().then((s) => setDue(s.due), () => setDue(0));
  }, []);

  const engineReady = engine?.ok ?? false;
  const thinking = !!game && thinkingFor === game.id;
  const pos = game?.position;
  const finished = !!pos?.result;

  const show = useCallback((next: GameView) => {
    setGame(next);
    saveGameId(next.id);
    setError(null);
  }, []);

  const run = useCallback(
    async (action: () => Promise<GameView>) => {
      try {
        show(await action());
        return true;
      } catch (e) {
        setError(errorText(e));
        if (e instanceof ApiError && e.status === 404) setShowNewGame(true); // 后端重启过，对局已丢失
        return false;
      }
    },
    [show],
  );

  // 打开页面：恢复上次的对局（后端重启过则显示新对局设置）。引擎状态由 useEngineStatus 检查。
  useEffect(() => {
    const saved = loadSavedGameId();
    (async () => {
      if (saved) {
        try {
          const restored = await api.getGame(saved);
          show(restored);
          setFlipped(restored.mode === "vs_ai" && restored.user_side === "black");
          return;
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404)) {
            setError(errorText(e));
            return;
          }
        }
      }
      setShowNewGame(true);
    })();
  }, [show]);

  // 轮到 AI 时自动请求 AI 走棋：每次轮到 AI 只请求一次（重试除外），切换到别的对局后丢弃旧结果。
  // 轮到你走时清掉记录——悔棋后再走到同样的步数，也要重新请求。
  useEffect(() => {
    if (!game?.ai_to_move) {
      aiRequested.current = null;
      return;
    }
    const key = `${game.id}:${game.moves.length}:${aiRetry}`;
    if (aiRequested.current === key) return;
    aiRequested.current = key;
    const gameId = game.id;
    setThinkingFor(gameId);
    const started = Date.now();
    api
      .aiMove(gameId)
      .then(async (next) => {
        await sleep(AI_MIN_DELAY_MS - (Date.now() - started));
        setGame((current) => (current?.id === next.id ? next : current));
        setError(null);
      })
      .catch(async (e) => {
        setError(errorText(e));
        // 例如刷新页面前发出的 AI 请求已经在后端完成：以服务端的状态为准
        try {
          const latest = await api.getGame(gameId);
          setGame((current) => (current?.id === latest.id ? latest : current));
          if (!latest.ai_to_move) setError(null);
        } catch {
          // 保留原来的错误信息
        }
      })
      .finally(() => setThinkingFor((current) => (current === gameId ? null : current)));
  }, [game, aiRetry]);

  const startGame = async (options: NewGameOptions) => {
    if (await run(() => api.newGame(options))) {
      setShowNewGame(false);
      setFlipped(options.mode === "vs_ai" && options.user_side === "black");
    }
  };

  const play = useCallback(
    (move: string) => (game ? run(() => api.move(game.id, move)) : Promise.resolve(false)),
    [game, run],
  );

  const busy = thinking || hintLoading;
  // 与后端一致：人机对战轮到你走时，一次撤回双方各一步（从棋谱某一步开始时，之前的着法也算）
  const canUndo = !!game && !!pos && !busy &&
    game.moves.length >= (game.mode === "vs_ai" && pos.turn === game.user_side ? 2 : 1);
  const undo = useCallback(() => {
    if (game && canUndo) void run(() => api.undo(game.id));
  }, [game, canUndo, run]);

  const askHint = async (level: HintLevel) => {
    if (!game || !pos) return;
    setHintLoading(true);
    try {
      const h = await api.hint(game.id, level);
      setHint({ ...h, gameId: game.id, fen: pos.fen });
      setGame((g) => (g && g.id === game.id ? { ...g, hints_used: g.hints_used + 1 } : g));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setHintLoading(false);
    }
  };

  // 保存到棋谱库：棋局结束时后端自动保存；没下完的对局可以手动保存，再次保存会更新同一条记录
  const saveToLibrary = async () => {
    if (!game) return;
    const gameId = game.id;
    setSaving(true);
    try {
      const { library_id } = await api.saveGame(gameId);
      setGame((g) => (g && g.id === gameId ? { ...g, library_id } : g));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  // 复盘：先保存到棋谱库（已保存且没变化时不重复保存），再到打谱页查看复盘进度和报告
  const reviewGame = async () => {
    if (!game) return;
    setReviewing(true);
    try {
      const { library_id } = await api.reviewLiveGame(game.id);
      navigate(gameHash(library_id));
    } catch (e) {
      setError(errorText(e));
      setReviewing(false);
    }
  };

  // 快捷键：Ctrl/⌘+Z 悔棋，F 翻转棋盘（在输入框中时不触发）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        undo();
      } else if (e.key.toLowerCase() === "f" && !e.ctrlKey && !e.metaKey) {
        setFlipped((f) => !f);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo]);

  const submitMove = async (e: FormEvent) => {
    e.preventDefault();
    if (moveInput.trim() && (await play(moveInput.trim()))) setMoveInput("");
  };

  const copyFen = async () => {
    if (!pos) return;
    try {
      await navigator.clipboard.writeText(pos.fen);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("无法写入剪贴板，请手动选择复制");
    }
  };

  const analysis = useAnalysis(
    analysisOn && engineReady,
    game ? { gameId: game.id } : undefined,
    pos?.fen,
    finished,
  );
  const live = useLiveAnalysis(game, liveOn && engineReady);
  const toggleLive = (on: boolean) => {
    setLiveOn(on);
    saveLiveAnalysis(on);
  };
  const visibleHint = hint && game && hint.gameId === game.id && hint.fen === game.position.fen ? hint : null;
  const arrows: Arrow[] = [];
  if (visibleHint?.move) arrows.push({ move: visibleHint.move, kind: "hint" });
  if (analysis.info?.lines[0]) arrows.push({ move: analysis.info.lines[0].move, kind: "analysis" });

  const firstMover: Side = game ? sideToMove(game.initial_fen) : "red";
  const userTurn = !!game && !finished && !game.ai_to_move && !thinking;
  const canHint = engineReady && userTurn && !hintLoading;
  const canReview = engineReady && !!game && game.moves.length > 0 && !busy && !reviewing;

  return (
    <main className="layout">
      <section className="board-panel">
        {analysisOn && engineReady && (
          <EvalBar redWin={analysis.info?.lines[0]?.red_win ?? null} flipped={flipped} />
        )}
        {pos ? (
          <Board
            position={pos}
            flipped={flipped}
            onMove={(m) => void play(m)}
            interactive={userTurn}
            highlight={visibleHint?.from_square ? squareFromIccs(visibleHint.from_square) : null}
            arrows={arrows}
          />
        ) : (
          <div className="board-placeholder">{error ?? "正在连接后端……"}</div>
        )}
      </section>

      <aside className="side-panel">
        {due > 0 && (
          <a className="card due-banner" href={trainHash("review")}>
            今日复习 <strong>{due}</strong> 题 →
          </a>
        )}
        {(showNewGame || !game) && (
          <NewGamePanel
            engineReady={engineReady}
            engineChecking={engine === null && !engineCheckFailed}
            levels={engine?.levels ?? []}
            onStart={(options) => void startGame(options)}
            onCancel={game ? () => setShowNewGame(false) : undefined}
          />
        )}

        {game && pos && (
          <div className="card status">
            {pos.result ? (
              <>
                <div className="result">{resultText(game)}</div>
                <div className="buttons">
                  <button className="primary" onClick={() => void reviewGame()} disabled={!canReview}
                    title={engineReady ? "用引擎分析整盘棋，找出失误和关键时刻" : "复盘需要先安装象棋引擎"}>
                    {reviewing ? "正在准备复盘……" : "复盘这局"}
                  </button>
                </div>
              </>
            ) : (
              <div className={`turn ${pos.turn}`}>
                <span className="turn-dot" />
                {game.mode === "vs_ai"
                  ? game.ai_to_move ? "AI 思考中……" : `轮到你走（${sideText(pos.turn)}）`
                  : `${sideText(pos.turn)}走棋`}
                {pos.in_check && <span className="check-badge">将军！</span>}
              </div>
            )}
            <p className="muted small game-meta">
              {game.mode === "vs_ai"
                ? `人机对战 · 你执${sideText(game.user_side!)} · 难度 ${game.ai_level}`
                : "自由对弈（双方都由你来走）"}
              {game.hints_used > 0 && ` · 已用提示 ${game.hints_used} 次`}
            </p>
            {error && (
              <div className="error" role="alert">
                {error}
                {game.ai_to_move && !thinking && (
                  <button className="link" onClick={() => setAiRetry((n) => n + 1)}>重试</button>
                )}
              </div>
            )}
            <div className="buttons">
              <button
                onClick={() => {
                  setShowNewGame(true);
                  if (!engineReady) checkEngine();
                }}
                disabled={showNewGame}
              >
                新对局
              </button>
              <button onClick={undo} disabled={!canUndo}>悔棋</button>
              <button onClick={() => setFlipped((f) => !f)}>翻转棋盘</button>
            </div>
            <div className="buttons hint-buttons">
              <button onClick={() => void askHint(1)} disabled={!userTurn || hintLoading}
                title="只指出方向（如哪个子有危险），不说具体走法">提示：方向</button>
              <button onClick={() => void askHint(2)} disabled={!canHint}
                title="只告诉你该动哪个子">动哪个子</button>
              <button onClick={() => void askHint(3)} disabled={!canHint}
                title="给出具体着法和后续变化">怎么走</button>
              {hintLoading && <span className="muted small">思考中……</span>}
            </div>
            <form className="inline-form" onSubmit={submitMove}>
              <input
                value={moveInput}
                onChange={(e) => setMoveInput(e.target.value)}
                placeholder="输入着法：炮二平五 或 h2e2"
                aria-label="输入着法"
                disabled={!userTurn}
              />
              <button type="submit" disabled={!moveInput.trim() || !userTurn}>走</button>
            </form>
          </div>
        )}

        {visibleHint && (
          <div className="card hint">
            <h2>{["提示：方向", "提示：动哪个子", "提示：推荐着法"][visibleHint.level - 1]}</h2>
            <p>{visibleHint.text}</p>
          </div>
        )}

        {game && (
          <div className="card live-analysis">
            <h2>每步分析</h2>
            <label className="toggle">
              <input type="checkbox" checked={liveOn} onChange={(e) => toggleLive(e.target.checked)} />
              走一步分析一步：每走一步，马上给这步评级，并指出更好的走法
              {game.mode === "vs_ai" ? "（只分析你自己的着法）" : ""}
            </label>
            {liveOn && !engineReady && (
              <p className="muted small">需要先安装象棋引擎（见 README「安装象棋引擎」）。</p>
            )}
            {liveOn && engineReady && (
              <>
                {live.pending !== null && <p className="muted small">正在分析第 {live.pending} 步……</p>}
                {live.error && (
                  <p className="error">
                    {live.error}
                    <button className="link" onClick={live.retry}>重试</button>
                  </p>
                )}
                {!live.latest && live.pending === null && !live.error && (
                  <p className="muted small">走一步之后，这里会显示这步的评级。</p>
                )}
              </>
            )}
          </div>
        )}

        {liveOn && engineReady && live.latest && (
          <MoveReview llm={llm} move={live.latest} loading={live.explaining} busy={live.explaining}
            onExplain={() => void live.explain()} canRefresh={false}
            title={live.latest.terminal ? `刚才这步 · ${live.latest.terminal}` : "刚才这步"}
            onAddCard={async () => {
              const m = live.latest!;
              const result = await api.addCard({
                fen: m.fen_before,
                solution: m.best_move!,
                played: m.iccs,
                source: `对弈：第 ${m.ply} 步（${m.grade}）`,
                explanation: m.explanation,
              });
              return result.created;
            }} />
        )}

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
                显示实时分析（会直接显示最佳着法，下棋练习时建议关闭）
              </label>
              {analysisOn && (
                <AnalysisPanel info={analysis.info} error={analysis.error} finished={finished} />
              )}
              <p className="muted small">引擎：{engine.name}</p>
            </>
          ) : (
            <div className="warning">
              <p>
                {engine.error}
                <button className="link" onClick={checkEngine}>重新检查</button>
              </p>
              <p className="small">安装方法见 README「安装象棋引擎」。安装前仍可以自由对弈。</p>
            </div>
          )}
        </div>

        {pos && (
          <div className="card explorer-card">
            <h2>棋谱库统计</h2>
            <label className="toggle">
              <input type="checkbox" checked={explorerOn}
                onChange={(e) => setExplorerOn(e.target.checked)} />
              显示当前局面在棋谱库中的统计（高手们在这里怎么走、胜率如何）
            </label>
            {explorerOn && <ExplorerPanel fen={pos.fen} />}
          </div>
        )}

        {game && (
          <div className="card moves">
            <h2>着法</h2>
            <MoveList moves={game.moves} firstMover={firstMover}
              grades={liveOn ? live.grades : undefined} />
            <div className="library-save small">
              {game.library_id ? (
                <>
                  <span>已保存到棋谱库 · <a href={gameHash(game.library_id)}>查看</a></span>
                  {!finished && (
                    <button className="link" onClick={() => void saveToLibrary()} disabled={saving}
                      title="把之后走的着法也保存进去">{saving ? "保存中……" : "更新"}</button>
                  )}
                </>
              ) : (
                <button onClick={() => void saveToLibrary()} disabled={saving || game.moves.length === 0}
                  title="下完的对局会自动保存；没下完的也可以先保存">
                  {saving ? "保存中……" : "保存到棋谱库"}
                </button>
              )}
              {!finished && (
                <button className="library-review" onClick={() => void reviewGame()} disabled={!canReview}
                  title={engineReady ? "保存到棋谱库并复盘到目前为止的着法" : "复盘需要先安装象棋引擎"}>
                  {reviewing ? "准备中……" : "复盘"}
                </button>
              )}
            </div>
          </div>
        )}

        {pos && (
          <div className="card fen">
            <h2>局面（FEN）</h2>
            <div className="fen-current">
              <code>{pos.fen}</code>
              <button onClick={() => void copyFen()}>{copied ? "已复制" : "复制"}</button>
            </div>
          </div>
        )}

        <p className="hint-keys">快捷键：Ctrl+Z 悔棋 · F 翻转棋盘</p>
      </aside>
    </main>
  );
}
