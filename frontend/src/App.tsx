import { useEffect } from "react";
import GameViewer from "./GameViewer";
import LibraryPage from "./LibraryPage";
import PlayPage from "./PlayPage";
import { useRoute } from "./router";

const TITLES = { play: "对弈", library: "棋谱库", game: "打谱" };

/** 页面外壳：顶部导航 + 按地址栏 hash 显示对弈、棋谱库或打谱页面。 */
export default function App() {
  const route = useRoute();
  const gameId = route.page === "game" ? route.id : null;

  // 换页面时回到顶部
  useEffect(() => {
    window.scrollTo(0, 0);
    document.title = `${TITLES[route.page]} - 象棋自学`;
  }, [route.page, gameId]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>象棋自学</h1>
        <nav className="tabs">
          <a href="#/" className={route.page === "play" ? "active" : undefined}>对弈</a>
          <a href="#/library" className={route.page !== "play" ? "active" : undefined}>棋谱库</a>
        </nav>
      </header>

      {route.page === "play" && <PlayPage />}
      {route.page === "library" && <LibraryPage query={route.query} />}
      {route.page === "game" && <GameViewer key={route.id} id={route.id} initialPly={route.ply} />}
    </div>
  );
}
