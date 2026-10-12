import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
const outputText = readFileSync(
  new URL("../node_modules/.cache/analysis-tests/analysisSession.js", import.meta.url), "utf8",
);
const { analysisRequest, AnalysisSession } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("相同棋盘的新历史不能接收旧请求", () => {
  const session = new AnalysisSession();
  const connection = session.connect();
  const old = session.start(connection, "game-a:history-1");
  const current = session.start(connection, "game-a:history-2");
  assert.equal(session.accepts(connection, old), false);
  assert.equal(session.accepts(connection, current), true);
  assert.equal(session.matches("game-a:history-1", current), false);
});

test("旧连接的迟到 close 不会清除新连接", () => {
  const session = new AnalysisSession();
  const old = session.connect();
  session.start(old, "old");
  const current = session.connect();
  const id = session.start(current, "current");
  assert.equal(session.disconnect(old), false);
  assert.equal(session.isConnected(current), true);
  assert.equal(session.accepts(current, id), true);
  assert.equal(session.start(old, "late"), null);
});

test("关闭当前连接后丢弃全部迟到消息", () => {
  const session = new AnalysisSession();
  const connection = session.connect();
  const id = session.start(connection, "position");
  assert.equal(session.disconnect(connection), true);
  assert.equal(session.accepts(connection, id), false);
  assert.equal(session.matches("position", id), false);
});

test("停止请求取代正在搜索的请求", () => {
  const session = new AnalysisSession();
  const connection = session.connect();
  const old = session.start(connection, "search");
  const stop = session.start(connection, "finished");
  assert.equal(session.accepts(connection, old), false);
  assert.equal(session.accepts(connection, undefined), false);
  assert.equal(session.accepts(connection, stop), true);
});

test("AI 行棋暂停搜索并保留连接，恢复时旧消息仍被隔离", () => {
  const session = new AnalysisSession();
  const connection = session.connect();
  const start = JSON.stringify({ type: "start", game_id: "game", multipv: 3 });
  const active = analysisRequest(start, "before-ai", false);
  const oldId = session.start(connection, active.key);
  assert.equal(active.message.type, "start");
  const paused = analysisRequest(start, "before-ai", false, true);
  const stopId = session.start(connection, paused.key);
  assert.deepEqual(paused.message, { type: "stop" });
  assert.notEqual(paused.key, active.key);
  assert.equal(session.isConnected(connection), true);
  assert.equal(session.accepts(connection, oldId), false);
  assert.equal(session.matches(paused.key, oldId), false);

  const resumed = analysisRequest(start, "after-ai", false);
  const currentId = session.start(connection, resumed.key);
  assert.deepEqual(resumed.message, { type: "start", game_id: "game", multipv: 3 });
  assert.equal(session.accepts(connection, stopId), false);
  assert.equal(session.accepts(connection, oldId), false);
  assert.equal(session.accepts(connection, currentId), true);
  assert.equal(session.matches(resumed.key, currentId), true);
});

test("同一局面暂停后恢复也生成新请求", () => {
  const session = new AnalysisSession();
  const connection = session.connect();
  const start = JSON.stringify({ type: "start", fen: "initial", moves: ["a0a1"] });
  const active = analysisRequest(start, "current", false);
  const old = session.start(connection, active.key);
  session.start(connection, analysisRequest(start, "current", false, true).key);
  const resumed = session.start(connection, analysisRequest(start, "current", false, false).key);
  assert.notEqual(old, resumed);
  assert.equal(session.accepts(connection, old), false);
  assert.equal(session.accepts(connection, resumed), true);
});

test("结束棋局或缺失分析目标时保持停止", () => {
  const start = JSON.stringify({ type: "start", game_id: "game" });
  for (const [request, fen, finished] of [[start, "fen", true], [undefined, "fen", false], [start, undefined, false]]) {
    assert.deepEqual(analysisRequest(request, fen, finished).message, { type: "stop" });
    assert.deepEqual(analysisRequest(request, fen, finished, true).message, { type: "stop" });
  }
});
