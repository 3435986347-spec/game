// 保存在浏览器本地存储中的设置：当前对局、新对局选项。
import type { AiStyle, NewGameOptions } from "./api";

const GAME_KEY = "xiangqi.gameId";
const SETTINGS_KEY = "xiangqi.newGame";
const LIVE_ANALYSIS_KEY = "xiangqi.liveAnalysis";

export type NewGameSettings = Omit<NewGameOptions, "fen" | "moves"> & { ai_style: AiStyle };

/** 低级别沿用教学选着；旧设置和未知棋风采用标准模式。 */
export function effectiveAiStyle(level: number | null, style: unknown): AiStyle {
  return level !== null && level >= 8 && level <= 10 && style === "varied" ? "varied" : "standard";
}

function normalizeSettings(saved: unknown): NewGameSettings {
  const value = saved && typeof saved === "object" ? saved as Partial<NewGameOptions> : {};
  const level = Number.isInteger(value.ai_level) && value.ai_level! >= 1 && value.ai_level! <= 10
    ? value.ai_level! : 3;
  return {
    mode: value.mode === "free" ? "free" : "vs_ai",
    user_side: value.user_side === "black" ? "black" : "red",
    ai_level: level,
    ai_style: effectiveAiStyle(level, value.ai_style),
  };
}

export function loadSavedGameId(): string | null {
  try {
    return localStorage.getItem(GAME_KEY);
  } catch {
    return null;
  }
}

/** 记下当前对局，对弈页面打开时恢复它。 */
export function saveGameId(id: string) {
  try {
    localStorage.setItem(GAME_KEY, id);
  } catch {
    // 浏览器禁用了本地存储时忽略
  }
}

/** 上次新对局的设置（模式、执哪一方、难度、棋风），兼容没有棋风字段的旧数据。 */
export function loadSettings(): NewGameSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null");
    return normalizeSettings(saved);
  } catch {
    // 忽略损坏或不可用的本地存储
  }
  return normalizeSettings(null);
}

export function saveSettings(settings: NewGameSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(normalizeSettings(settings)));
  } catch {
    // 浏览器禁用了本地存储时忽略
  }
}

/** 是否打开「走一步分析一步」。 */
export function loadLiveAnalysis(): boolean {
  try {
    return localStorage.getItem(LIVE_ANALYSIS_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveLiveAnalysis(on: boolean) {
  try {
    localStorage.setItem(LIVE_ANALYSIS_KEY, on ? "1" : "0");
  } catch {
    // 浏览器禁用了本地存储时忽略
  }
}
