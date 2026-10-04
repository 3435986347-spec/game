"""启动入口：python -m xiangqi 或 uv run xiangqi。"""

import argparse
import socket
import sys
import threading
import webbrowser

import uvicorn

from .api import create_app
from .config import load_config

_PORT_TRIES = 20


def port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":  # 与 uvicorn 一致；Windows 上 SO_REUSEADDR 含义不同，不设置
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, port: int, *, fallback: bool) -> int:
    """端口被占用时（常见原因：之前启动的程序还开着），自动换下一个空闲端口。"""
    if port_is_free(host, port):
        return port
    if fallback:
        for candidate in range(port + 1, port + _PORT_TRIES):
            if port_is_free(host, candidate):
                print(f"端口 {port} 已被占用（可能之前启动的程序还在运行），改用端口 {candidate}。")
                return candidate
    sys.exit(
        f"端口 {port} 已被占用。可以关掉占用它的程序"
        f"（macOS / Linux 用 lsof -nP -iTCP:{port} -sTCP:LISTEN 查看），或用 --port 指定其他端口。"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xiangqi", description="启动象棋自学应用")
    parser.add_argument("--config", help="配置文件路径（默认向上查找 config.toml）")
    parser.add_argument("--host", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, help="端口（默认 8000，被占用时自动换一个）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    host = args.host or config.host
    port = pick_port(host, args.port or config.port, fallback=args.port is None)
    app = create_app(config)

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print(f"象棋自学：{url}")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
