import { useEffect, useRef, useState } from "react";
import { api, errorText } from "./api";
import type { ExplorerMove, ExplorerView } from "./api";
import { libraryHash } from "./router";

const SHOW_FIRST = 8; // 默认只列出最常见的几步
const FETCH_DELAY_MS = 120; // 连续翻页时只查询停下来的局面

const count = (n: number) => n.toLocaleString("zh-CN");

interface ExplorerPanelProps {
  fen: string;
  /** 本局实际走的下一步（ICCS），突出显示 */
  played?: string | null;
  /** 打谱时当前是第几步，超出索引范围时给出说明 */
  ply?: number;
}

interface Loaded {
  fen: string;
  data: ExplorerView | null;
  error: string | null;
}

/** 局面统计（开局浏览器）：棋谱库中这个局面之后各着法的局数和红胜 / 和 / 黑胜比例。 */
export default function ExplorerPanel({ fen, played = null, ply }: ExplorerPanelProps) {
  const cache = useRef(new Map<string, ExplorerView>());
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [retry, setRetry] = useState(0);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const cached = cache.current.get(fen);
    if (cached) {
      setLoaded({ fen, data: cached, error: null });
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api.explorer(fen, controller.signal).then(
        (data) => {
          cache.current.set(fen, data);
          setLoaded({ fen, data, error: null });
        },
        (e) => {
          if (!controller.signal.aborted) setLoaded({ fen, data: null, error: errorText(e) });
        },
      );
    }, FETCH_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [fen, retry]);

  const current = loaded?.fen === fen ? loaded : null;
  if (current?.error) {
    return (
      <p className="error">
        {current.error}
        <button className="link" onClick={() => setRetry((n) => n + 1)}>重试</button>
      </p>
    );
  }
  // 新局面查询中时先显示上一个局面的结果（变淡），避免来回闪烁
  const data = current?.data ?? loaded?.data ?? null;
  if (!data) return <p className="muted small">正在查询棋谱库……</p>;

  const top = data.moves.slice(0, SHOW_FIRST);
  const playedRow = data.moves.find((m) => m.move === played);
  const rows = showAll ? data.moves : playedRow && !top.includes(playedRow) ? [...top, playedRow] : top;

  return (
    <div className={current ? "explorer" : "explorer stale"}>
      <p className="explorer-summary">
        {data.games > 0
          ? <>棋谱库中有 <strong>{count(data.games)}</strong> 局到达这个局面</>
          : "棋谱库中没有对局到达这个局面"}
        <span className="muted small"> · 只统计每局前 {data.indexed_plies} 步</span>
      </p>
      {ply !== undefined && ply > data.indexed_plies && (
        <p className="muted small">当前是第 {ply} 步，已超出统计范围。</p>
      )}
      {rows.length > 0 && (
        <>
          <div className="wdl-legend small muted">
            <span><i className="wdl-red" />红胜</span>
            <span><i className="wdl-draw" />和</span>
            <span><i className="wdl-black" />黑胜</span>
          </div>
          <ol className="explorer-moves">
            {rows.map((m) => (
              <ExplorerRow key={m.move} move={m} total={data.games} played={m.move === played} />
            ))}
          </ol>
        </>
      )}
      <div className="explorer-links small">
        {data.moves.length > SHOW_FIRST && (
          <button className="link" onClick={() => setShowAll((s) => !s)}>
            {showAll ? "收起" : `显示全部 ${data.moves.length} 种着法`}
          </button>
        )}
        {data.games > 0 && (
          <a href={libraryHash(new URLSearchParams({ fen }))}>查看到达此局面的对局</a>
        )}
      </div>
    </div>
  );
}

function ExplorerRow({ move, total, played }: { move: ExplorerMove; total: number; played: boolean }) {
  const decided = move.red_wins + move.draws + move.black_wins;
  const parts = [
    { kind: "wdl-red", n: move.red_wins },
    { kind: "wdl-draw", n: move.draws },
    { kind: "wdl-black", n: move.black_wins },
  ];
  const share = total > 0 ? Math.round((move.games / total) * 100) : 0;
  return (
    <li className={played ? "played" : undefined}>
      <span className="explorer-move" title={move.move}>
        {move.cn}
        {played && <span className="tag">本局</span>}
      </span>
      <span className="explorer-count">
        {count(move.games)} 局<span className="muted small"> {share}%</span>
      </span>
      <div className="wdl-bar"
        title={`红胜 ${count(move.red_wins)} · 和 ${count(move.draws)} · 黑胜 ${count(move.black_wins)}`}>
        {decided === 0 ? (
          <span className="wdl-empty">无胜负记录</span>
        ) : (
          parts.map(({ kind, n }) => {
            const pct = (n / decided) * 100;
            return pct > 0 ? (
              <span key={kind} className={kind} style={{ width: `${pct}%` }}>
                {pct >= 14 ? `${Math.round(pct)}%` : ""}
              </span>
            ) : null;
          })
        )}
      </div>
    </li>
  );
}
