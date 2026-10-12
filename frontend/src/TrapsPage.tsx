import { useEffect, useRef, useState } from "react";
import { api, errorText, squareFromIccs } from "./api";
import type { Side, TrapDetail, TrapSummary } from "./api";
import Board from "./Board";
import type { Arrow } from "./Board";
import { positionFromFen, START_FEN } from "./fen";
import { isTypingTarget } from "./keyboard";
import { navigate } from "./router";
import { loadSettings, saveGameId } from "./settings";
import { useEngineStatus } from "./useEngineStatus";

const sideText = (side: Side) => side === "red" ? "红方" : "黑方";
const otherSide = (side: Side): Side => side === "red" ? "black" : "red";
const noMove = () => {};

function sourceUrl(source: string): string | null {
  try {
    const url = new URL(source);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

/** 开局飞刀：看主变、学习破解，再选一方与 AI 实战。 */
export default function TrapsPage() {
  const [traps, setTraps] = useState<TrapSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const mounted = useRef(true);
  const { engine, checkFailed, check } = useEngineStatus();
  const engineReady = engine?.ok ?? false;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setListError(null);
    setTraps(null);
    api.listTraps().then(
      (items) => { if (active) setTraps(items); },
      (e) => { if (active) setListError(errorText(e)); },
    );
    return () => { active = false; };
  }, [reload]);

  const start = async (trap: TrapSummary, aiPlays: "trap" | "victim") => {
    if (starting || !engineReady) return;
    setStarting(`${trap.id}:${aiPlays}`);
    setStartError(null);
    try {
      const settings = loadSettings();
      const game = await api.newGame({
        mode: "vs_ai",
        trap_id: trap.id,
        ai_plays: aiPlays,
        user_side: aiPlays === "trap" ? otherSide(trap.side) : trap.side,
        ai_level: settings.ai_level,
        ai_style: settings.ai_style,
      });
      if (!mounted.current) return;
      saveGameId(game.id);
      navigate("#/");
    } catch (e) {
      if (mounted.current) setStartError(errorText(e));
    } finally {
      if (mounted.current) setStarting(null);
    }
  };

  return (
    <main className="traps-page">
      <h2>飞刀开局</h2>
      <p className="muted">
        飞刀用弃子、诱敌制造开局陷阱。先看对方贪子后的主变，再学破解；实战中变招或剧本走完后，AI 会继续应战。
      </p>
      {!engineReady && (
        <p className="warning small" role="status">
          {!engine && !checkFailed
            ? "正在检查引擎，回放无需等待。"
            : checkFailed
              ? "暂时无法检查引擎，回放仍可使用。"
              : engine?.error || "引擎暂不可用，请配置引擎后开始实战。"}
          {(engine || checkFailed) && <button className="link" onClick={check}>重新检查</button>}
        </p>
      )}
      {startError && <p className="error" role="alert">{startError}</p>}
      {listError ? (
        <div className="card">
          <p className="error" role="alert">{listError}</p>
          <button onClick={() => setReload((n) => n + 1)}>重试</button>
        </div>
      ) : traps === null ? (
        <p className="muted" role="status">正在加载飞刀开局……</p>
      ) : traps.length === 0 ? (
        <p className="muted">暂无飞刀开局。</p>
      ) : (
        <div className="trap-cards">
          {traps.map((trap) => (
            <article className="card trap-card" key={trap.id}>
              <h3>
                {trap.name}
                <span className={`side-tag ${trap.side}`}>{sideText(trap.side)}执刀</span>
              </h3>
              <p>{trap.intro}</p>
              <p className="muted small">主变共 {trap.ply_count} 步 · 第 {trap.trap_ply} 步设下陷阱</p>
              <div className="buttons">
                <button aria-expanded={selected === trap.id} aria-controls={`trap-detail-${trap.id}`}
                  onClick={() => setSelected((id) => id === trap.id ? null : trap.id)}>
                  {selected === trap.id ? "收起回放" : "查看主变与破解"}
                </button>
                <button className="primary" disabled={!engineReady || starting !== null}
                  onClick={() => void start(trap, "trap")}
                  title={`你执${sideText(otherSide(trap.side))}，破解 AI 的飞刀`}>
                  {starting === `${trap.id}:trap` ? "正在开始……" : "破飞刀"}
                </button>
                <button disabled={!engineReady || starting !== null}
                  onClick={() => void start(trap, "victim")}
                  title={`你执${sideText(trap.side)}，练习飞刀主变`}>
                  {starting === `${trap.id}:victim` ? "正在开始……" : "练飞刀"}
                </button>
              </div>
              {selected === trap.id && <TrapDetailPanel key={trap.id} id={trap.id} />}
            </article>
          ))}
        </div>
      )}
    </main>
  );
}

function TrapDetailPanel({ id }: { id: string }) {
  const [detail, setDetail] = useState<TrapDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    setDetail(null);
    api.trapDetail(id).then(
      (value) => { if (active) setDetail(value); },
      (e) => { if (active) setError(errorText(e)); },
    );
    return () => { active = false; };
  }, [id, reload]);

  return (
    <section id={`trap-detail-${id}`} aria-label="主变与破解回放">
      {error ? (
        <p className="error" role="alert">
          {error}<button className="link" onClick={() => setReload((n) => n + 1)}>重试</button>
        </p>
      ) : !detail ? (
        <p className="muted" role="status">正在加载开局详情……</p>
      ) : <TrapReplay key={detail.id} detail={detail} />}
    </section>
  );
}

function TrapReplay({ detail }: { detail: TrapDetail }) {
  const [line, setLine] = useState<"main" | "refute">("main");
  const [step, setStep] = useState(0);
  const [flipped, setFlipped] = useState(detail.side === "black");
  const moves = line === "main" ? detail.moves : detail.refute;
  const last = moves.length;
  const current = step > 0 ? moves[step - 1] : null;
  const trapIndex = detail.moves.findIndex((move) => move.ply === detail.trap_ply);
  const trapMove = trapIndex >= 0 ? detail.moves[trapIndex] : null;
  const atTrap = line === "main" ? step === trapIndex + 1 : step === 0;
  const beforeTrap = line === "main" && trapIndex >= 0 && step === trapIndex;
  const initialFen = line === "main" ? START_FEN : detail.trap_fen;
  const position = positionFromFen(current?.fen ?? initialFen,
    current?.iccs ?? (line === "refute" ? trapMove?.iccs ?? null : null));
  const highlight = trapMove && (atTrap || beforeTrap)
    ? squareFromIccs(trapMove.iccs.slice(beforeTrap ? 0 : 2, beforeTrap ? 2 : 4))
    : null;
  const arrows: Arrow[] = beforeTrap && trapMove ? [{ move: trapMove.iccs, kind: "hint" }] : [];
  const source = sourceUrl(detail.source);

  const chooseLine = (next: "main" | "refute") => {
    setLine(next);
    setStep(0);
  };
  const jumpToTrap = () => {
    setLine("main");
    setStep(trapIndex + 1);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "ArrowLeft") setStep((s) => Math.max(0, s - 1));
      else if (e.key === "ArrowRight") setStep((s) => Math.min(last, s + 1));
      else if (e.key === "Home") setStep(0);
      else if (e.key === "End") setStep(last);
      else if (e.key.toLowerCase() === "f") setFlipped((f) => !f);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [last]);

  return (
    <>
      <p><strong>飞刀原理：</strong>{detail.principle}</p>
      {source && (
        <p className="small">
          <a href={source} target="_blank" rel="noopener noreferrer">棋谱来源</a>
        </p>
      )}
      <div className="buttons" role="group" aria-label="回放变化">
        <button className={line === "main" ? "primary" : undefined}
          aria-pressed={line === "main"} onClick={() => chooseLine("main")}>主变：落入陷阱</button>
        <button className={line === "refute" ? "primary" : undefined}
          aria-pressed={line === "refute"} onClick={() => chooseLine("refute")}
          disabled={detail.refute.length === 0}>破解：正确应对</button>
        <button onClick={jumpToTrap} disabled={trapIndex < 0}>跳到陷阱点</button>
      </div>
      {detail.refute.length === 0 && <p className="muted small">此开局暂未提供破解变化。</p>}
      <div className="trap-replay"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))" }}>
        <div className="trap-replay-board">
          <Board position={position} flipped={flipped} onMove={noMove} interactive={false}
            highlight={highlight} arrows={arrows} />
          <div className="viewer-nav" role="group" aria-label="回放步数控制">
            <button onClick={() => setStep(0)} disabled={step === 0}
              title="起始局面（Home）" aria-label="回到起始局面">⏮</button>
            <button onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}
              title="上一步（←）" aria-label="上一步">◀</button>
            <span className="viewer-ply" aria-live="polite" aria-atomic="true">
              {current ? `第 ${current.ply} 步 ${current.cn}` : line === "main" ? "开局局面" : "陷阱点局面"}
              <span className="muted"> · {step} / {last}</span>
            </span>
            <button onClick={() => setStep((s) => Math.min(last, s + 1))} disabled={step === last}
              title="下一步（→）" aria-label="下一步">▶</button>
            <button onClick={() => setStep(last)} disabled={step === last}
              title="变化终点（End）" aria-label="跳到变化终点">⏭</button>
            <button onClick={() => setFlipped((f) => !f)} title="翻转棋盘（F）">翻转</button>
          </div>
          <p className="muted small">{sideText(position.turn)}走棋 · ← / → 逐步回放，F 翻转棋盘</p>
        </div>
        <div className="trap-replay-notes">
          <h4 style={{ margin: 0 }}>{line === "main" ? "主变说明" : "破解说明"}</h4>
          {atTrap && <p className="trap-alert warn">陷阱点：轮到{sideText(otherSide(detail.side))}应对，先判断弃子的用意。</p>}
          <p aria-live="polite">
            {current?.note || (step === 0
              ? line === "main" ? "从标准开局逐步查看双方如何走进陷阱。" : "从弃子后的陷阱点出发，查看正确应对。"
              : "这一着没有单独说明，请结合飞刀原理和后续变化观察。")}
          </p>
          <div className="move-list selectable" style={{ maxHeight: 360, overflowY: "auto" }}>
            <ol aria-label={line === "main" ? "主变着法" : "破解着法"}>
              {moves.map((move, index) => (
                <li key={`${move.ply}:${move.iccs}`}>
                  <span className="move-no">{move.ply}.</span>
                  <button className="move" aria-current={step === index + 1 ? "step" : undefined}
                    onClick={() => setStep(index + 1)}
                    style={step === index + 1 ? { background: "var(--warning-bg)", fontWeight: 600 } : undefined}>
                    {move.cn}
                  </button>
                  {line === "main" && move.ply === detail.trap_ply && <span className="trap-chip">陷阱点</span>}
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </>
  );
}
