import { useRef } from "react";
import type { ChangeEvent } from "react";
import { addFiles, clearFinished, useImportQueue } from "./importQueue";
import type { ImportItem } from "./importQueue";

const count = (n: number) => n.toLocaleString("zh-CN");

function sizeText(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** 导入棋谱文件：可一次选择多个文件，逐个上传并显示进度和结果。 */
export default function ImportPanel() {
  const { items } = useImportQueue();
  const inputRef = useRef<HTMLInputElement>(null);
  const hasFinished = items.some((it) => it.status === "done" || it.status === "failed");

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files ? [...e.target.files] : [];
    if (files.length) addFiles(files);
    e.target.value = ""; // 允许再次选择同一个文件
  };

  return (
    <div className="card import-panel">
      <h2>导入棋谱</h2>
      <p className="small">
        支持 PGN 棋谱（ICCS 坐标、中文记谱或 WXF 记法），一个文件可以包含很多局；已经导入过的对局会自动跳过。
      </p>
      <input ref={inputRef} type="file" multiple hidden onChange={pick} />
      <button className="primary" onClick={() => inputRef.current?.click()}>选择棋谱文件……</button>

      {items.length > 0 && (
        <ul className="import-list">
          {items.map((item) => <ImportRow key={item.key} item={item} />)}
        </ul>
      )}
      {hasFinished && (
        <button className="link import-clear" onClick={clearFinished}>清除已完成的记录</button>
      )}
      <p className="muted small">
        很大的棋谱集（几万局以上）用命令行导入更快：
        <code>cd backend && uv run xiangqi-import &lt;文件&gt;</code>
      </p>
    </div>
  );
}

const STATUS_TEXT: Record<ImportItem["status"], string> = {
  queued: "等待中",
  uploading: "上传中……",
  running: "导入中……",
  done: "完成",
  failed: "失败",
};

function ImportRow({ item }: { item: ImportItem }) {
  const { job, status } = item;
  const active = status === "uploading" || status === "running";
  const ended = status === "done" || status === "failed";
  return (
    <li className={`import-item ${status}`}>
      <div className="import-head">
        <span className="import-name" title={item.filename}>{item.filename}</span>
        <span className="muted small">{sizeText(item.size)}</span>
        <span className="import-status">{STATUS_TEXT[status]}</span>
      </div>
      {active && <div className="import-progress" />}
      {job && (
        <p className="import-counts small">
          {ended ? "结果：" : `已读取 ${count(job.games_seen)} 局 · `}
          新增 <strong>{count(job.imported)}</strong> 局 · 重复 {count(job.duplicates)} 局 · 失败{" "}
          <span className={job.failed > 0 ? "import-failed" : undefined}>{count(job.failed)}</span> 局
        </p>
      )}
      {item.error && <p className={status === "failed" ? "error" : "muted small"}>{item.error}</p>}
      {job && job.errors.length > 0 && (
        <details className="import-errors">
          <summary>
            失败原因{job.failed > job.errors.length ? `（只列出前 ${job.errors.length} 条）` : ""}
          </summary>
          <ol>
            {job.errors.map((e) => (
              <li key={e.index}>
                <span className="muted">第 {e.index} 局{e.title ? ` ${e.title}` : ""}：</span>
                {e.reason}
              </li>
            ))}
          </ol>
        </details>
      )}
    </li>
  );
}
