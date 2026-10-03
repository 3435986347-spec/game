import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import Board from "./Board";
import MoveList from "./MoveList";
import { ApiError, api } from "./api";
import type { GameView, Side } from "./api";

const GAME_KEY = "xiangqi.gameId";

function loadSavedGameId(): string | null {
  try {
    return localStorage.getItem(GAME_KEY);
  } catch {
    return null;
  }
}

function saveGameId(id: string) {
  try {
    localStorage.setItem(GAME_KEY, id);
  } catch {
    // 浏览器禁用了本地存储时忽略
  }
}

const sideText = (side: Side) => (side === "red" ? "红方" : "黑方");

export default function App() {
  const [game, setGame] = useState<GameView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [moveInput, setMoveInput] = useState("");
  const [fenInput, setFenInput] = useState("");
  const [copied, setCopied] = useState(false);

  const run = useCallback(async (action: () => Promise<GameView>) => {
    try {
      const next = await action();
      setGame(next);
      saveGameId(next.id);
      setError(null);
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "无法连接后端，请确认 Python 服务已启动");
      return false;
    }
  }, []);

  // 打开页面时恢复上次的对局；后端重启过则新开一局
  useEffect(() => {
    const saved = loadSavedGameId();
    void run(async () => {
      if (saved) {
        try {
          return await api.getGame(saved);
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404)) throw e;
        }
      }
      return api.newGame();
    });
  }, [run]);

  const play = useCallback(
    (move: string) => (game ? run(() => api.move(game.id, move)) : Promise.resolve(false)),
    [game, run],
  );
  const undo = useCallback(() => {
    if (game && game.moves.length > 0) void run(() => api.undo(game.id));
  }, [game, run]);

  // 快捷键：Ctrl/⌘+Z 悔棋，F 翻转棋盘（在输入框中时不触发）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
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

  const submitFen = async (e: FormEvent) => {
    e.preventDefault();
    if (await run(() => api.newGame(fenInput.trim()))) setFenInput("");
  };

  const copyFen = async () => {
    if (!game) return;
    try {
      await navigator.clipboard.writeText(game.position.fen);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("无法写入剪贴板，请手动选择复制");
    }
  };

  const pos = game?.position;
  const firstMover: Side = game?.initial_fen.split(" ")[1] === "b" ? "black" : "red";

  return (
    <div className="app">
      <header className="app-header">
        <h1>象棋自学</h1>
        <span className="stage">第一阶段 · 规则引擎与自由对弈（双方都由你来走）</span>
      </header>

      <main className="layout">
        <section className="board-panel">
          {pos ? (
            <Board position={pos} flipped={flipped} onMove={(m) => void play(m)} />
          ) : (
            <div className="board-placeholder">正在连接后端……</div>
          )}
        </section>

        <aside className="side-panel">
          <div className="card status">
            {pos?.result ? (
              <div className="result">{pos.result.text}</div>
            ) : pos ? (
              <div className={`turn ${pos.turn}`}>
                <span className="turn-dot" />
                {sideText(pos.turn)}走棋
                {pos.in_check && <span className="check-badge">将军！</span>}
              </div>
            ) : null}
            {error && <div className="error" role="alert">{error}</div>}
            <div className="buttons">
              <button onClick={() => void run(() => api.newGame())}>新对局</button>
              <button onClick={undo} disabled={!game || game.moves.length === 0}>
                悔棋
              </button>
              <button onClick={() => setFlipped((f) => !f)}>翻转棋盘</button>
            </div>
            <form className="inline-form" onSubmit={submitMove}>
              <input
                value={moveInput}
                onChange={(e) => setMoveInput(e.target.value)}
                placeholder="输入着法：炮二平五 或 h2e2"
                aria-label="输入着法"
                disabled={!pos || !!pos.result}
              />
              <button type="submit" disabled={!moveInput.trim()}>走</button>
            </form>
          </div>

          <div className="card moves">
            <h2>着法</h2>
            {game && <MoveList moves={game.moves} firstMover={firstMover} />}
          </div>

          <div className="card fen">
            <h2>局面（FEN）</h2>
            <div className="fen-current">
              <code>{pos?.fen}</code>
              <button onClick={() => void copyFen()} disabled={!pos}>
                {copied ? "已复制" : "复制"}
              </button>
            </div>
            <form className="inline-form" onSubmit={submitFen}>
              <input
                value={fenInput}
                onChange={(e) => setFenInput(e.target.value)}
                placeholder="粘贴 FEN，从该局面开始新对局"
                aria-label="粘贴 FEN"
              />
              <button type="submit" disabled={!fenInput.trim()}>载入</button>
            </form>
          </div>

          <p className="hint">快捷键：Ctrl+Z 悔棋 · F 翻转棋盘</p>
        </aside>
      </main>
    </div>
  );
}
