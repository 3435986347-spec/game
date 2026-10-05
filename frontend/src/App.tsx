import { useEffect, useState } from "react";
import { TRAIN_CHANGED, api } from "./api";
import GameViewer from "./GameViewer";
import GuessPage from "./GuessPage";
import LibraryPage from "./LibraryPage";
import PlayPage from "./PlayPage";
import { useRoute } from "./router";
import TrainPage from "./TrainPage";

const TITLES = { play: "对弈", library: "棋谱库", game: "打谱", train: "训练", guess: "猜着练习" };

/** 页面外壳：顶部导航 + 按地址栏 hash 显示对弈、棋谱库、打谱、训练或猜着练习页面。 */
export default function App() {
  const route = useRoute();
  const gameId = route.page === "game" ? route.id : null;
  const [due, setDue] = useState(0);

  // 换页面时回到顶部
  useEffect(() => {
    window.scrollTo(0, 0);
    document.title = `${TITLES[route.page]} - 象棋自学`;
  }, [route.page, gameId]);

  // 导航上显示今天要复习几题：换页面、作答或增删错题时刷新
  const routeKey = JSON.stringify(route);
  useEffect(() => {
    const refresh = () => {
      api.trainDue().then((r) => setDue(r.due), () => setDue(0));
    };
    refresh();
    window.addEventListener(TRAIN_CHANGED, refresh);
    return () => window.removeEventListener(TRAIN_CHANGED, refresh);
  }, [routeKey]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>象棋自学</h1>
        <nav className="tabs">
          <a href="#/" className={route.page === "play" ? "active" : undefined}>对弈</a>
          <a href="#/library" className={route.page === "library" || route.page === "game" ? "active" : undefined}>
            棋谱库
          </a>
          <a href="#/train" className={route.page === "train" || route.page === "guess" ? "active" : undefined}>
            训练{due > 0 && <span className="tab-badge" title={`今天要复习 ${due} 题`}>{due}</span>}
          </a>
        </nav>
      </header>

      {route.page === "play" && <PlayPage />}
      {route.page === "library" && <LibraryPage query={route.query} />}
      {route.page === "game" && <GameViewer key={route.id} id={route.id} initialPly={route.ply} />}
      {route.page === "train" && <TrainPage key={`${route.mode}-${route.theme}`} mode={route.mode} theme={route.theme} />}
      {route.page === "guess" && <GuessPage key={route.id} id={route.id} />}
    </div>
  );
}
