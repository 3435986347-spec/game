/** 连接与请求分别编号；相同棋盘、不同历史也不能接收上次分析。 */
export function analysisRequest(
  start: string | undefined, fen: string | undefined, finished: boolean, paused = false,
): { key: string; message: { type: "start" | "stop"; [key: string]: unknown } } {
  return {
    key: JSON.stringify([start, fen, finished, paused]),
    message: paused || finished || !start || !fen ? { type: "stop" } : JSON.parse(start),
  };
}

export class AnalysisSession {
  private connectionCounter = 0;
  private requestCounter = 0;
  private connection: number | null = null;
  private request: { id: string; key: string } | null = null;

  connect(): number {
    this.connection = ++this.connectionCounter;
    this.request = null;
    return this.connection;
  }

  isConnected(connection: number): boolean {
    return this.connection === connection;
  }

  disconnect(connection: number): boolean {
    if (!this.isConnected(connection)) return false;
    this.connection = null;
    this.request = null;
    return true;
  }

  start(connection: number, key: string): string | null {
    if (!this.isConnected(connection)) return null;
    const id = `${connection}:${++this.requestCounter}`;
    this.request = { id, key };
    return id;
  }

  accepts(connection: number, requestId: unknown): boolean {
    return this.isConnected(connection) && this.request !== null && this.request.id === requestId;
  }

  matches(key: string, requestId: unknown): boolean {
    return this.request !== null && this.request.key === key && this.request.id === requestId;
  }
}
