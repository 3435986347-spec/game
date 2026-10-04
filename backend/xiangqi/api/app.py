"""FastAPI 应用：/api 下是接口，/ws 下是 WebSocket，其余路径提供前端页面。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import AppConfig
from ..engine import EngineService
from . import analysis, games

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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await engines.close()  # 退出时关闭引擎进程

    app = FastAPI(title="象棋自学", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.games = {}
    app.state.engines = engines
    app.state.analysis_task = None  # 当前正在进行的实时分析（全局只有一个）
    app.state.analysis_socket = None
    app.include_router(games.router)
    app.include_router(analysis.router)

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
