// 棋谱导入队列：逐个上传文件、轮询后台导入进度。
// 放在组件之外，切换到别的页面再回来，进度仍在（刷新页面后不保留，但后端的导入会继续）。
import { useSyncExternalStore } from "react";
import { ApiError, api, errorText } from "./api";
import type { ImportJob } from "./api";

const POLL_MS = 500;
const MAX_POLL_FAILURES = 20; // 连续这么多次查询失败才放弃（约 10 秒）

export interface ImportItem {
  key: number;
  filename: string;
  size: number;
  status: "queued" | "uploading" | "running" | "done" | "failed";
  job: ImportJob | null;
  /** 上传或查询进度失败的原因 */
  error: string | null;
}

interface State {
  items: ImportItem[];
  /** 已结束的导入任务数，变化时棋谱库页面重新加载数据 */
  finished: number;
}

let state: State = { items: [], finished: 0 };
const files = new Map<number, File>(); // 等待上传的文件，上传后释放
const listeners = new Set<() => void>();
let nextKey = 1;
let running = false;

function update(key: number, patch: Partial<ImportItem>) {
  state = { ...state, items: state.items.map((it) => (it.key === key ? { ...it, ...patch } : it)) };
  listeners.forEach((fn) => fn());
}

function finish(key: number, patch: Partial<ImportItem>) {
  state = { ...state, finished: state.finished + 1 };
  update(key, patch);
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useImportQueue(): State {
  return useSyncExternalStore(subscribe, () => state);
}

export function addFiles(list: File[]) {
  const added = list.map((file) => {
    const key = nextKey++;
    files.set(key, file);
    return { key, filename: file.name, size: file.size, status: "queued", job: null, error: null } as ImportItem;
  });
  state = { ...state, items: [...state.items, ...added] };
  listeners.forEach((fn) => fn());
  void pump();
}

/** 清除已结束的任务。 */
export function clearFinished() {
  state = { ...state, items: state.items.filter((it) => it.status !== "done" && it.status !== "failed") };
  listeners.forEach((fn) => fn());
}

// 一次只导入一个文件，避免多个任务同时写数据库
async function pump() {
  if (running) return;
  running = true;
  try {
    for (;;) {
      const next = state.items.find((it) => it.status === "queued");
      if (!next) break;
      await runOne(next.key);
    }
  } finally {
    running = false;
  }
}

async function runOne(key: number) {
  const file = files.get(key)!;
  update(key, { status: "uploading" });
  let jobId: string;
  try {
    jobId = (await api.importFile(file)).job_id;
  } catch (e) {
    finish(key, { status: "failed", error: errorText(e) });
    return;
  } finally {
    files.delete(key);
  }
  update(key, { status: "running" });

  let failures = 0;
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    try {
      const job = await api.importJob(jobId);
      failures = 0;
      if (job.done) {
        finish(key, { status: job.error ? "failed" : "done", job, error: job.error });
        return;
      }
      update(key, { job, error: null });
    } catch (e) {
      failures += 1;
      if (e instanceof ApiError && e.status === 404) {
        finish(key, { status: "failed", error: "找不到导入任务（后端可能重启过），请检查已导入的棋谱后重新导入" });
        return;
      }
      if (failures >= MAX_POLL_FAILURES) {
        finish(key, { status: "failed", error: `无法获取导入进度：${errorText(e)}` });
        return;
      }
      update(key, { error: "暂时无法获取进度，正在重试……" });
    }
  }
}
