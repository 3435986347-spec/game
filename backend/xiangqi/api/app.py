"""FastAPI 应用：/api 下是接口，/ws 下是 WebSocket，其余路径提供前端页面。"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import AppConfig
from ..engine import AnalysisCache, EngineService
from ..library import Library
from ..llm import ExplainService
from ..openings import build_trap_index
from ..training import TrainingStore
from . import analysis, annotate, coach, games, guess, review, train, traps
from . import library as library_api

_NOT_BUILT_HTML = """<!doctype html><meta charset="utf-8"><title>象棋自学</title>
<p>后端已启动，但前端还没有构建。请运行：</p>
<pre>cd frontend
npm install
npm run build</pre>
<p>开发时也可以运行 <code>npm run dev</code>，然后打开 Vite 显示的地址。</p>
<p>接口文档见 <a href="/docs">/docs</a>。</p>"""


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig()
    engines = EngineService(config.engine)
    library = Library(config.library_db or ":memory:", index_plies=config.library_index_plies)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await library_api.stop_imports(
            app.state.import_jobs
        )  # 中止进行中的导入，否则要等它导完才能退出
        await review.stop_reviews(app.state.review_jobs)
        await review.stop_reviews(app.state.annotate_jobs)
        await engines.close()  # 退出时关闭引擎进程
        library.close()

    app = FastAPI(title="象棋自学", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.games = {}
    app.state.engines = engines
    app.state.analysis_task = None  # 当前正在进行的实时分析（全局只有一个）
    app.state.analysis_socket = None
    app.state.analysis_request_id = None
    app.state.analysis_lock = asyncio.Lock()  # 抢占与注册新分析必须作为一次操作
    app.state.library = library
    app.state.import_jobs = {}
    app.state.review_jobs = {}
    app.state.annotate_jobs = {}
    app.state.explainer = ExplainService(config.llm, cache=library)
    app.state.training = TrainingStore(library)
    app.state.guess_locks = {}  # 猜着练习：同一次练习的作答依次处理
    app.state.trap_index = build_trap_index()  # 飞刀剧本的局面索引（不可变，只读）
    # 全局引擎分析缓存：同样的局面 + 同样的搜索参数直接复用（强度不变，只是不重复算）
    app.state.analysis_cache = AnalysisCache()
    app.include_router(games.router)
    app.include_router(analysis.router)
    app.include_router(library_api.router)
    app.include_router(review.router)
    app.include_router(annotate.router)
    app.include_router(coach.router)
    app.include_router(guess.router)
    app.include_router(train.router)
    app.include_router(traps.router)

    @app.get("/api/health", tags=["系统"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    static_dir = config.static_dir
    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        def not_built() -> HTMLResponse:
            return HTMLResponse(_NOT_BUILT_HTML)

    return app
