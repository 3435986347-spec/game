// 极简 hash 路由：#/ 对弈，#/library 棋谱库（可带搜索条件），#/library/<id> 打谱。
import { useEffect, useState } from "react";

export type Route =
  | { page: "play" }
  | { page: "library"; query: string }
  | { page: "game"; id: number; ply: number | null };

export function parseRoute(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const q = raw.indexOf("?");
  const path = q < 0 ? raw : raw.slice(0, q);
  const query = q < 0 ? "" : raw.slice(q + 1);
  const parts = path.split("/").filter(Boolean);
  if (parts[0] !== "library") return { page: "play" };
  if (parts.length === 2 && /^\d+$/.test(parts[1])) {
    const ply = new URLSearchParams(query).get("ply");
    return { page: "game", id: Number(parts[1]), ply: ply && /^\d+$/.test(ply) ? Number(ply) : null };
  }
  return { page: "library", query };
}

export const gameHash = (id: number) => `#/library/${id}`;

export function libraryHash(params?: URLSearchParams): string {
  const query = params?.toString();
  return query ? `#/library?${query}` : "#/library";
}

export function navigate(hash: string) {
  location.hash = hash;
}

// 最近一次浏览的棋谱库页面（含搜索条件），打谱页「返回棋谱库」回到这里
let lastLibraryHash = "#/library";
export const libraryBackHash = () => lastLibraryHash;

/** 当前路由，随地址栏 hash 变化。 */
export function useRoute(): Route {
  const [hash, setHash] = useState(() => location.hash);
  useEffect(() => {
    const onChange = () => setHash(location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const route = parseRoute(hash);
  const libraryQuery = route.page === "library" ? route.query : null;
  useEffect(() => {
    if (libraryQuery !== null) lastLibraryHash = libraryHash(new URLSearchParams(libraryQuery));
  }, [libraryQuery]);
  return route;
}
