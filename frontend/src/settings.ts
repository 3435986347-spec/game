// 保存在浏览器本地存储中的设置：当前对局、新对局选项。
import type { NewGameOptions } from "./api";

const GAME_KEY = "xiangqi.gameId";
const SETTINGS_KEY = "xiangqi.newGame";

export type NewGameSettings = Omit<NewGameOptions, "fen" | "moves">;

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

/** 上次新对局的设置（模式、执哪一方、难度）。 */
export function loadSettings(): NewGameSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null");
    if (saved && (saved.mode === "free" || saved.mode === "vs_ai")) return saved;
  } catch {
    // 忽略损坏或不可用的本地存储
  }
  return { mode: "vs_ai", user_side: "red", ai_level: 3 };
}

export function saveSettings(settings: NewGameSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // 浏览器禁用了本地存储时忽略
  }
}
