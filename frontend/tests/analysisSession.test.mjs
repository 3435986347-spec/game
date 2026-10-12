import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
const outputText = readFileSync(
  new URL("../node_modules/.cache/analysis-tests/analysisSession.js", import.meta.url), "utf8",
);
const { AnalysisSession } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

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
