import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, beforeEach, test } from "node:test";

const outputText = readFileSync(
  new URL("../node_modules/.cache/analysis-tests/settings.js", import.meta.url), "utf8",
);
const { effectiveAiStyle, loadSettings, saveSettings } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);
const previousStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
const values = new Map();
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  },
});
beforeEach(() => values.clear());
after(() => {
  if (previousStorage) Object.defineProperty(globalThis, "localStorage", previousStorage);
  else delete globalThis.localStorage;
});

test("旧设置缺少棋风时保留级别与执棋方，默认标准", () => {
  values.set("xiangqi.newGame", JSON.stringify({ mode: "vs_ai", ai_level: 10, user_side: "black" }));
  assert.deepEqual(loadSettings(), { mode: "vs_ai", ai_level: 10, user_side: "black", ai_style: "standard" });
});

test("8–10 级持久化强棋多变", () => {
  for (const level of [8, 9, 10]) {
    const settings = { mode: "vs_ai", user_side: "red", ai_level: level, ai_style: "varied" };
    saveSettings(settings);
    assert.deepEqual(loadSettings(), settings);
  }
});

test("1–7 级不继承隐藏的多变选项", () => {
  for (let level = 1; level <= 7; level++) {
    values.set("xiangqi.newGame", JSON.stringify({ mode: "vs_ai", ai_level: level, ai_style: "varied" }));
    assert.equal(loadSettings().ai_style, "standard");
    saveSettings({ mode: "vs_ai", ai_level: level, ai_style: "varied" });
    assert.equal(JSON.parse(values.get("xiangqi.newGame")).ai_style, "standard");
    assert.equal(effectiveAiStyle(level, "varied"), "standard");
  }
});

test("损坏和未知设置使用有效默认值", () => {
  const defaults = { mode: "vs_ai", user_side: "red", ai_level: 3, ai_style: "standard" };
  for (const saved of ["invalid-json", "null", "[]", '{"mode":"bad","ai_level":12,"ai_style":"varied"}']) {
    values.set("xiangqi.newGame", saved);
    assert.deepEqual(loadSettings(), defaults);
  }
  assert.equal(effectiveAiStyle(10, "unknown"), "standard");
  assert.equal(effectiveAiStyle(null, "varied"), "standard");
});
