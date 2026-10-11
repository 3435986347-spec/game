import { useEffect, useRef, useState } from "react";
import type { AnalysisInfo } from "./api";
import { positionKey } from "./fen";

/** 分析对象：对弈中的对局（分析其当前局面），或任意局面（起始 FEN + 着法）。 */
export type AnalysisTarget = { gameId: string } | { fen: string; moves: string[] };

interface AnalysisState {
  /** 当前局面的最新分析；局面变化后、新结果到达前为 null */
  info: AnalysisInfo | null;
  error: string | null;
}

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000];

/**
 * 通过 WebSocket 获取引擎对当前局面的实时分析。
 * enabled 为 false 时断开连接；分析对象或局面（fen）变化时自动重新开始分析，棋局结束时停止。
 * fen 是当前局面，只显示这个局面的分析结果。连接断开（如后端重启）时自动重连。
 */
export function useAnalysis(
  enabled: boolean,
  target: AnalysisTarget | undefined,
  fen: string | undefined,
  finished: boolean,
): AnalysisState {
  const [info, setInfo] = useState<AnalysisInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [socket, setSocket] = useState<WebSocket | null>(null);
  const [generation, setGeneration] = useState(0); // 每次重连加一，触发重新建立连接
  const failures = useRef(0); // 连续失败次数，决定重连等待时间
  const fenRef = useRef(fen);
  fenRef.current = fen;
  // 收到过「缓存里的更深结果」（seeded）后，忽略比它浅的实时更新，直到实时超过它。
  // 换局面时必须清零（在 start 发送时做），否则新局面的浅层结果会被旧门槛吞掉。
  const seededDepth = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    let closedByUs = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${location.host}/ws/analysis`);
    ws.onopen = () => {
      failures.current = 0;
      setError(null);
      setSocket(ws);
    };
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "info" && sameFen(message.fen, fenRef.current)) {
        if (message.seeded) {
          // 缓存结果先顶上：立刻有内容，但实时搜索没超过这个深度前不再更新
          seededDepth.current = Math.max(seededDepth.current, message.depth);
          setInfo(message);
          setError(null);
        } else if (message.depth > seededDepth.current) {
          seededDepth.current = 0; // 实时搜索超过了缓存深度，恢复逐次更新
          setInfo(message);
          setError(null);
        }
      } else if (message.type === "error") {
        setError(message.message);
      }
    };
    ws.onclose = () => {
      setSocket(null);
      if (closedByUs) return;
      setError("实时分析连接已断开，正在重连……");
      const delay = RECONNECT_DELAYS_MS[Math.min(failures.current, RECONNECT_DELAYS_MS.length - 1)];
      failures.current += 1;
      retryTimer = setTimeout(() => setGeneration((g) => g + 1), delay);
    };
    return () => {
      closedByUs = true;
      clearTimeout(retryTimer);
      ws.close();
      setSocket(null);
      setInfo(null);
      setError(null);
    };
  }, [enabled, generation]);

  // 序列化成字符串，每次渲染新建的 target 对象内容不变时不会重发
  const start = target && JSON.stringify(
    "gameId" in target
      ? { type: "start", game_id: target.gameId, multipv: 3 }
      : { type: "start", fen: target.fen, moves: target.moves, multipv: 3 },
  );

  useEffect(() => {
    if (!socket || !start) return;
    seededDepth.current = 0; // 换了局面：旧局面的缓存深度门槛作废，新局面的实时结果照常显示
    socket.send(finished ? JSON.stringify({ type: "stop" }) : start);
  }, [socket, start, fen, finished]);

  const current = info && sameFen(info.fen, fen) ? info : null;
  return { info: enabled ? current : null, error: enabled ? error : null };
}

/** 是否同一局面：只比较棋子摆放和走棋方（回合计数可能因计算方式不同而不同）。 */
function sameFen(a: string | undefined, b: string | undefined): boolean {
  return !!a && !!b && positionKey(a) === positionKey(b);
}
