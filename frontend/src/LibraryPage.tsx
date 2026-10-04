import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import ImportPanel from "./ImportPanel";
import { RESULT_TEXT, api, errorText } from "./api";
import type { GameResult, LibraryStats, OpeningInfo, SearchParams, SearchResult } from "./api";
import { useImportQueue } from "./importQueue";
import { gameHash, libraryHash, navigate } from "./router";

const count = (n: number) => n.toLocaleString("zh-CN");

const FIELDS = ["q", "event", "opening", "result", "kind", "fen"] as const;
type Filters = Record<(typeof FIELDS)[number], string>;

const RESULTS = Object.keys(RESULT_TEXT) as GameResult[];

/** 地址栏中的搜索条件 → 表单字段和页码。 */
function parseQuery(query: string): { filters: Filters; page: number } {
  const params = new URLSearchParams(query);
  const filters = Object.fromEntries(FIELDS.map((f) => [f, params.get(f) ?? ""])) as Filters;
  const page = Number(params.get("page"));
  return { filters, page: Number.isInteger(page) && page > 1 ? page : 1 };
}

/** 表单字段 → 搜索接口参数（忽略地址栏里不认识的取值）。 */
function searchParams(filters: Filters, page: number): SearchParams {
  const { q, event, opening, fen, result, kind } = filters;
  return {
    q, event, opening, fen, page,
    result: RESULTS.find((r) => r === result),
    kind: kind === "library" || kind === "my_game" ? kind : undefined,
  };
}

function toParams(filters: Filters, page = 1): URLSearchParams {
  const params = new URLSearchParams();
  for (const f of FIELDS) {
    const value = filters[f].trim();
    if (value) params.set(f, value);
  }
  if (page > 1) params.set("page", String(page));
  return params;
}

interface Loaded<T> {
  key: string;
  data: T | null;
  error: string | null;
}

/** 棋谱库：统计、搜索、结果列表和导入。搜索条件保存在地址栏中，可以前进后退。 */
export default function LibraryPage({ query }: { query: string }) {
  const { filters } = useMemo(() => parseQuery(query), [query]);
  const [draft, setDraft] = useState(filters);
  const [lastQuery, setLastQuery] = useState(query);
  if (lastQuery !== query) {
    // 前进后退或点击链接改变了搜索条件：表单跟着变
    setLastQuery(query);
    setDraft(filters);
  }

  const { finished } = useImportQueue(); // 每导入完一个文件就重新加载
  const [reload, setReload] = useState(0);
  const [stats, setStats] = useState<Loaded<LibraryStats> | null>(null);
  const [openings, setOpenings] = useState<OpeningInfo[]>([]);
  const [results, setResults] = useState<Loaded<SearchResult> | null>(null);

  const statsKey = `${finished}:${reload}`;
  useEffect(() => {
    let cancelled = false;
    api.libraryStats().then(
      (data) => !cancelled && setStats({ key: statsKey, data, error: null }),
      (e) => !cancelled && setStats({ key: statsKey, data: null, error: errorText(e) }),
    );
    // 开局列表取不到时下拉框只有「全部」，不单独报错
    api.openings().then((data) => !cancelled && setOpenings(data), () => {});
    return () => {
      cancelled = true;
    };
  }, [statsKey]);

  const searchKey = `${query}|${statsKey}`;
  useEffect(() => {
    let cancelled = false;
    const { filters, page } = parseQuery(query);
    api.searchGames(searchParams(filters, page)).then(
      (data) => !cancelled && setResults({ key: searchKey, data, error: null }),
      (e) => !cancelled && setResults({ key: searchKey, data: null, error: errorText(e) }),
    );
    return () => {
      cancelled = true;
    };
  }, [query, searchKey]);

  const go = (params: URLSearchParams) => {
    const hash = libraryHash(params);
    if (hash === location.hash) {
      setReload((n) => n + 1); // 条件没变：重新搜索一次
    } else {
      navigate(hash);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    go(toParams(draft));
  };

  const goPage = (p: number) => {
    navigate(libraryHash(toParams(filters, p)));
    window.scrollTo(0, 0);
  };

  if (!stats) return <main className="library"><p className="muted">正在加载棋谱库……</p></main>;
  if (stats.error || !stats.data) {
    return (
      <main className="library">
        <div className="card">
          <p className="error">
            无法读取棋谱库：{stats.error}
            <button className="link" onClick={() => setReload((n) => n + 1)}>重试</button>
          </p>
        </div>
      </main>
    );
  }

  const info = stats.data;
  if (info.games === 0) return <EmptyLibrary />;

  const current = results?.key === searchKey ? results : null;
  const shown = current ?? results; // 新的搜索结果到达前先显示旧的（变淡）
  const openingOptions = openings.some((o) => o.name === draft.opening) || !draft.opening
    ? openings
    : [{ name: draft.opening, games: 0 }, ...openings];
  const set = (field: keyof Filters) => (e: { target: { value: string } }) =>
    setDraft({ ...draft, [field]: e.target.value });

  return (
    <main className="library">
      <div className="library-layout">
        <section className="library-main">
          <p className="library-stats">
            棋谱库共 <strong>{count(info.games)}</strong> 局
            {info.my_games > 0 && <>，其中我的对局 <strong>{count(info.my_games)}</strong> 局</>}
          </p>

          <form className="card search-form" onSubmit={submit}>
            <label>
              <span>棋手</span>
              <input value={draft.q} onChange={set("q")} placeholder="红方或黑方的名字" />
            </label>
            <label>
              <span>赛事</span>
              <input value={draft.event} onChange={set("event")} placeholder="如：全国象棋个人赛" />
            </label>
            <label>
              <span>开局</span>
              <select value={draft.opening} onChange={set("opening")}>
                <option value="">全部</option>
                {openingOptions.map((o) => (
                  <option key={o.name} value={o.name}>
                    {o.name}{o.games ? `（${count(o.games)}）` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>结果</span>
              <select value={draft.result} onChange={set("result")}>
                <option value="">全部</option>
                {RESULTS.map((r) => <option key={r} value={r}>{RESULT_TEXT[r]}</option>)}
              </select>
            </label>
            <label>
              <span>类型</span>
              <select value={draft.kind} onChange={set("kind")}>
                <option value="">全部</option>
                <option value="library">棋谱</option>
                <option value="my_game">我的对局</option>
              </select>
            </label>
            <label className="search-fen">
              <span>局面</span>
              <input value={draft.fen} onChange={set("fen")}
                placeholder={`可选：粘贴 FEN，只找到达过这个局面的对局（每局前 ${info.indexed_plies} 步）`} />
            </label>
            <div className="search-buttons">
              <button type="submit" className="primary">搜索</button>
              <button type="button" onClick={() => {
                setDraft(parseQuery("").filters); // 地址里本来就没有条件时，go() 不会触发重置
                go(new URLSearchParams());
              }}>清空条件</button>
            </div>
          </form>

          {current?.error ? (
            <div className="card">
              <p className="error">
                搜索失败：{current.error}
                <button className="link" onClick={() => setReload((n) => n + 1)}>重试</button>
              </p>
            </div>
          ) : !shown?.data ? (
            <p className="muted">正在搜索……</p>
          ) : (
            <ResultTable result={shown.data} stale={!current} onPage={goPage} />
          )}
        </section>

        <aside className="library-side">
          <ImportPanel />
        </aside>
      </div>
    </main>
  );
}

function ResultTable({ result, stale, onPage }: {
  result: SearchResult;
  stale: boolean;
  onPage: (page: number) => void;
}) {
  const { total, page, page_size, items } = result;
  const pages = Math.max(1, Math.ceil(total / page_size));
  if (total === 0) {
    return <div className="card"><p className="muted">没有符合条件的对局。</p></div>;
  }
  return (
    <div className={stale ? "card results stale" : "card results"}>
      <table className="game-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>赛事</th>
            <th>红方</th>
            <th>黑方</th>
            <th>结果</th>
            <th>开局</th>
            <th className="num">步数</th>
          </tr>
        </thead>
        <tbody>
          {items.map((g) => (
            <tr key={g.id} tabIndex={0} onClick={() => navigate(gameHash(g.id))}
              onKeyDown={(e) => e.key === "Enter" && navigate(gameHash(g.id))}>
              <td className="nowrap">{g.date ?? ""}</td>
              <td>
                {g.kind === "my_game" && <span className="tag">我的对局</span>}
                {g.event ?? ""}
              </td>
              <td>{g.red ?? ""}</td>
              <td>{g.black ?? ""}</td>
              <td className={`nowrap result-${g.result === "1-0" ? "red" : g.result === "0-1" ? "black" : "other"}`}>
                {RESULT_TEXT[g.result] ?? g.result}
              </td>
              <td>{g.opening ?? ""}</td>
              <td className="num">{g.ply_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="pager">
        <button onClick={() => onPage(page - 1)} disabled={page <= 1}>上一页</button>
        <span>第 {page} / {pages} 页<span className="muted"> · 共 {count(total)} 局</span></span>
        <button onClick={() => onPage(page + 1)} disabled={page >= pages}>下一页</button>
      </div>
    </div>
  );
}

/** 棋谱库还没有对局时：说明能做什么、棋谱从哪里来，以及导入入口。 */
function EmptyLibrary() {
  return (
    <main className="library">
      <div className="library-empty">
        <div className="card">
          <h2>棋谱库还是空的</h2>
          <p>
            导入棋谱文件后，可以按棋手、赛事、开局搜索对局，逐步打谱回放，
            并查看每个局面下高手们最常走的着法和胜率。你自己下完的对局也会自动保存到这里。
          </p>
          <p>
            棋谱文件从哪里来：见 README「棋谱来源」，例如东萍象棋网约 10 万局的 PGN 棋谱集。
            下载后放到仓库的 <code>data/</code> 目录（不会提交到仓库），再在下面导入。
          </p>
        </div>
        <ImportPanel />
      </div>
    </main>
  );
}
