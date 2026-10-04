import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AnalysisPanel, EvalBar } from "./AnalysisPanel";
import Board from "./Board";
import type { Arrow } from "./Board";
import ExplorerPanel from "./ExplorerPanel";
import MoveList from "./MoveList";
import NewGamePanel from "./NewGamePanel";
import { ApiError, api, errorText, squareFromIccs } from "./api";
import type { GameView, HintView, NewGameOptions, Side } from "./api";
import { sideToMove } from "./fen";
import { isTypingTarget } from "./keyboard";
import { gameHash } from "./router";
import { loadSavedGameId, saveGameId } from "./settings";
import { useAnalysis } from "./useAnalysis";
import { useEngineStatus } from "./useEngineStatus";

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
  const aiRequested = useRef<string | null>(null);

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
  const canUndo = !!game && game.moves.length > 0 && !busy &&
    !(game.mode === "vs_ai" && game.moves.length === 1 && game.moves[0].by_ai);
  const undo = useCallback(() => {
    if (game && canUndo) void run(() => api.undo(game.id));
  }, [game, canUndo, run]);

  const askHint = async (level: 2 | 3) => {
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
  const visibleHint = hint && game && hint.gameId === game.id && hint.fen === game.position.fen ? hint : null;
  const arrows: Arrow[] = [];
  if (visibleHint?.move) arrows.push({ move: visibleHint.move, kind: "hint" });
  if (analysis.info?.lines[0]) arrows.push({ move: analysis.info.lines[0].move, kind: "analysis" });

  const firstMover: Side = game ? sideToMove(game.initial_fen) : "red";
  const userTurn = !!game && !finished && !game.ai_to_move && !thinking;
  const canHint = engineReady && userTurn && !hintLoading;

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
            highlight={visibleHint ? squareFromIccs(visibleHint.from_square) : null}
            arrows={arrows}
          />
        ) : (
          <div className="board-placeholder">{error ?? "正在连接后端……"}</div>
        )}
      </section>

      <aside className="side-panel">
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
              <div className="result">{resultText(game)}</div>
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
            <div className="buttons">
              <button onClick={() => void askHint(2)} disabled={!canHint}
                title="只告诉你该动哪个子">提示：动哪个子</button>
              <button onClick={() => void askHint(3)} disabled={!canHint}
                title="给出具体着法和后续变化">提示：怎么走</button>
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
            <h2>{visibleHint.level === 2 ? "提示" : "提示：推荐着法"}</h2>
            <p>{visibleHint.text}</p>
          </div>
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
            <MoveList moves={game.moves} firstMover={firstMover} />
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
