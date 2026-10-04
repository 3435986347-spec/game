import { useEffect, useRef, useState } from "react";
import type { AnalysisInfo } from "./api";

interface AnalysisState {
  /** 当前局面的最新分析；局面变化后、新结果到达前为 null */
  info: AnalysisInfo | null;
  error: string | null;
}

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000];

/**
 * 通过 WebSocket 获取引擎对当前局面的实时分析。
 * enabled 为 false 时断开连接；局面（fen）变化时自动重新开始分析，棋局结束时停止。
 * 连接断开（如后端重启）时自动重连。
 */
export function useAnalysis(
  enabled: boolean,
  gameId: string | undefined,
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
      if (message.type === "info" && message.fen === fenRef.current) {
        setInfo(message);
        setError(null);
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

  useEffect(() => {
    if (!socket || !gameId) return;
    socket.send(
      JSON.stringify(finished ? { type: "stop" } : { type: "start", game_id: gameId, multipv: 3 }),
    );
  }, [socket, gameId, fen, finished]);

  const current = info && info.fen === fen ? info : null;
  return { info: enabled ? current : null, error: enabled ? error : null };
}
