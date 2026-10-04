"""FastAPI 应用：/api 下是接口，/ws 下是 WebSocket，其余路径提供前端页面。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import AppConfig
from ..engine import EngineService
from ..library import Library
from ..llm import ExplainService
from . import analysis, coach, games, review
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
        await engines.close()  # 退出时关闭引擎进程
        library.close()

    app = FastAPI(title="象棋自学", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.games = {}
    app.state.engines = engines
    app.state.analysis_task = None  # 当前正在进行的实时分析（全局只有一个）
    app.state.analysis_socket = None
    app.state.library = library
    app.state.import_jobs = {}
    app.state.review_jobs = {}
    app.state.explainer = ExplainService(config.llm, cache=library)
    app.include_router(games.router)
    app.include_router(analysis.router)
    app.include_router(library_api.router)
    app.include_router(review.router)
    app.include_router(coach.router)

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
