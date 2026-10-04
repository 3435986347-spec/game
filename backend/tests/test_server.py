"""启动真实的 uvicorn 服务，用真实的 HTTP / WebSocket 客户端访问。

TestClient 不经过 uvicorn 的协议层，测不出「服务器缺少 WebSocket 支持」这类问题。
"""

import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from conftest import FAKE_ENGINE
from websockets.sync.client import connect

from xiangqi.api import create_app
from xiangqi.config import AppConfig
from xiangqi.engine import EngineConfig


@pytest.fixture
def server_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    engine = EngineConfig(path=Path(sys.executable), args=(str(FAKE_ENGINE),))
    app = create_app(AppConfig(static_dir=None, engine=engine))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "服务没有启动"
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def test_real_server_websocket_analysis(server_url):
    request = urllib.request.Request(
        f"http://{server_url}/api/games", data=b"{}", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as resp:
        game = json.load(resp)
    with connect(f"ws://{server_url}/ws/analysis", open_timeout=5) as ws:
        ws.send(json.dumps({"type": "start", "game_id": game["id"], "multipv": 2}))
        for _ in range(20):  # 每收到一条候选着法推送一次，第一条消息可能只有一个候选
            message = json.loads(ws.recv(timeout=10))
            assert message["type"] == "info" and message["fen"] == game["position"]["fen"]
            if len(message["lines"]) == 2:
                break
    assert len(message["lines"]) == 2
