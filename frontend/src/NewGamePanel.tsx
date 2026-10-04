import { useState } from "react";
import type { FormEvent } from "react";
import type { LevelInfo, Mode, NewGameOptions, Side } from "./api";

const SETTINGS_KEY = "xiangqi.newGame";

const DEFAULT_LEVELS: LevelInfo[] = Array.from({ length: 10 }, (_, i) => ({
  level: i + 1,
  name: "",
}));

function loadSettings(): Omit<NewGameOptions, "fen"> {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null");
    if (saved && (saved.mode === "free" || saved.mode === "vs_ai")) return saved;
  } catch {
    // 忽略损坏或不可用的本地存储
  }
  return { mode: "vs_ai", user_side: "red", ai_level: 3 };
}

function saveSettings(settings: Omit<NewGameOptions, "fen">) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // 浏览器禁用了本地存储时忽略
  }
}

interface NewGamePanelProps {
  engineReady: boolean;
  /** 引擎状态还在检查中 */
  engineChecking?: boolean;
  levels: LevelInfo[];
  onStart: (options: NewGameOptions) => void;
  onCancel?: () => void;
}

/** 新对局设置：模式、执哪一方、难度，可选从 FEN 局面开始。 */
export default function NewGamePanel({
  engineReady,
  engineChecking = false,
  levels,
  onStart,
  onCancel,
}: NewGamePanelProps) {
  const [settings, setSettings] = useState(loadSettings);
  const [fen, setFen] = useState("");
  const mode: Mode = engineReady ? settings.mode : "free";
  const levelList = levels.length ? levels : DEFAULT_LEVELS;
  const levelName = levelList.find((l) => l.level === settings.ai_level)?.name;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    saveSettings(settings); // 保存用户自己的选择，而不是因引擎不可用临时改成的自由对弈
    onStart({ ...settings, mode, fen: fen.trim() || undefined });
  };

  return (
    <form className="card new-game" onSubmit={submit}>
      <h2>新对局</h2>
      <fieldset className="segmented" aria-label="模式">
        <label className={mode === "vs_ai" ? "active" : ""}>
          <input type="radio" name="mode" checked={mode === "vs_ai"} disabled={!engineReady}
            onChange={() => setSettings({ ...settings, mode: "vs_ai" })} />
          人机对战
        </label>
        <label className={mode === "free" ? "active" : ""}>
          <input type="radio" name="mode" checked={mode === "free"}
            onChange={() => setSettings({ ...settings, mode: "free" })} />
          自由对弈
        </label>
      </fieldset>
      {!engineReady && (
        <p className="muted small">
          {engineChecking ? "正在检查象棋引擎……" : "人机对战需要先安装象棋引擎（见下方提示）。"}
        </p>
      )}

      {mode === "vs_ai" && (
        <>
          <fieldset className="segmented" aria-label="我执">
            {(["red", "black"] as Side[]).map((side) => (
              <label key={side} className={settings.user_side === side ? "active" : ""}>
                <input type="radio" name="side" checked={settings.user_side === side}
                  onChange={() => setSettings({ ...settings, user_side: side })} />
                {side === "red" ? "我执红（先走）" : "我执黑"}
              </label>
            ))}
          </fieldset>
          <label className="level">
            <span>
              难度 <strong>{settings.ai_level}</strong>
              {levelName && <span className="muted"> · {levelName}</span>}
            </span>
            <input type="range" min={1} max={10} step={1} value={settings.ai_level}
              onChange={(e) => setSettings({ ...settings, ai_level: Number(e.target.value) })} />
          </label>
        </>
      )}

      <input className="fen-input" value={fen} onChange={(e) => setFen(e.target.value)}
        placeholder="可选：粘贴 FEN，从该局面开始" aria-label="起始局面 FEN" />
      <div className="buttons">
        <button type="submit" className="primary">开始</button>
        {onCancel && <button type="button" onClick={onCancel}>取消</button>}
      </div>
    </form>
  );
}
