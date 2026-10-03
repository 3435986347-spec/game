"""启动入口：python -m xiangqi 或 uv run xiangqi。"""

import argparse
import threading
import webbrowser

import uvicorn

from .api import create_app
from .config import load_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xiangqi", description="启动象棋自学应用")
    parser.add_argument("--config", help="配置文件路径（默认向上查找 config.toml）")
    parser.add_argument("--host", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, help="端口（默认 8000）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    host = args.host or config.host
    port = args.port or config.port
    app = create_app(config)

    if not args.no_browser:
        url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
