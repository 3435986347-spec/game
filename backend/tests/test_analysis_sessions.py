"""分析连接的抢占、请求编号和停止握手不能被并发 start / stop 打断。"""

import asyncio
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

from xiangqi.api import analysis
from xiangqi.api.app import create_app
from xiangqi.core import START_FEN


class RecordingSocket:
    def __init__(self, app=None):
        self.app = app
        self.messages = []
        self.incoming = asyncio.Queue()
        self.received = asyncio.Queue()

    async def accept(self):
        pass

    async def receive_json(self):
        message = await self.incoming.get()
        if message is None:
            raise WebSocketDisconnect()
        self.received.put_nowait(message)
        return message

    async def send_json(self, message):
        self.messages.append(message)


def test_preempt_error_retains_cancelled_request_id_during_stop_handshake():
    async def scenario():
        running, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def old_search():
            running.set()
            try:
                await asyncio.Event().wait()
            finally:
                draining.set()
                await release.wait()

        old_task = asyncio.create_task(old_search())
        await running.wait()
        old_socket, next_socket = RecordingSocket(), RecordingSocket()
        app = SimpleNamespace(state=SimpleNamespace(
            analysis_task=old_task, analysis_socket=old_socket, analysis_request_id="old",
        ))
        preempt = asyncio.create_task(analysis._preempt_other(app, next_socket))
        try:
            await asyncio.wait_for(draining.wait(), 1)
            # 模拟停止握手期间其他处理程序更新注册；通知仍属于被取消的旧请求。
            app.state.analysis_request_id = "new"
            release.set()
            await asyncio.wait_for(preempt, 1)
            assert old_socket.messages == [{
                "type": "error", "message": "实时分析已在另一个页面打开，这里暂停了",
                "request_id": "old",
            }]
        finally:
            release.set()
            await analysis._cancel(old_task)
            await preempt

    asyncio.run(scenario())


def test_concurrent_stops_cancel_once_and_both_wait_for_search_cleanup():
    async def scenario():
        running, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def search():
            running.set()
            try:
                await asyncio.Event().wait()
            finally:
                draining.set()
                await release.wait()

        task = asyncio.create_task(search())
        await running.wait()
        first = asyncio.create_task(analysis._cancel(task))
        second = None
        try:
            await asyncio.wait_for(draining.wait(), 1)
            second = asyncio.create_task(analysis._cancel(task))
            await asyncio.sleep(0)  # 让第二个停止调用进入等待。
            assert task.cancelling() == 1
            assert not task.done()
            assert not first.done() and not second.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 1)
            assert task.cancelled()
        finally:
            release.set()
            await analysis._cancel(task)
            await first
            if second is not None:
                await second

    asyncio.run(scenario())


def test_three_socket_starts_serialize_preemption_and_register_each_successor(monkeypatch):
    async def scenario():
        app = create_app()
        assert isinstance(app.state.analysis_lock, asyncio.Lock)
        sockets = [RecordingSocket(app) for _ in range(3)]
        handlers = [asyncio.create_task(analysis.analysis_socket(ws)) for ws in sockets]
        started = asyncio.Queue()
        draining, release = asyncio.Event(), asyncio.Event()
        active = set()
        maximum = 0

        async def stream(ws, engines, target, multipv, trap_index, cache):
            nonlocal maximum
            request_id = target.request_id
            active.add(request_id)
            maximum = max(maximum, len(active))
            started.put_nowait(request_id)
            await ws.send_json({"type": "info", "request_id": request_id})
            try:
                await asyncio.Event().wait()
            finally:
                if request_id == "first":
                    draining.set()
                    await release.wait()
                active.remove(request_id)

        monkeypatch.setattr(analysis, "_stream", stream)

        def start(ws, request_id):
            ws.incoming.put_nowait({
                "type": "start", "fen": START_FEN, "request_id": request_id,
            })

        try:
            start(sockets[0], "first")
            assert await asyncio.wait_for(started.get(), 1) == "first"
            start(sockets[1], "second")
            await asyncio.wait_for(draining.wait(), 1)
            start(sockets[2], "third")
            await asyncio.wait_for(sockets[2].received.get(), 1)
            await asyncio.sleep(0)  # 第三个连接也进入 start，第一条仍在停止握手中。
            assert active == {"first"}
            release.set()
            assert await asyncio.wait_for(started.get(), 1) == "second"
            assert await asyncio.wait_for(started.get(), 1) == "third"
            assert maximum == 1 and active == {"third"}
            for socket, request_id in zip(sockets[:2], ("first", "second"), strict=True):
                errors = [message for message in socket.messages if message["type"] == "error"]
                assert len(errors) == 1 and errors[0]["request_id"] == request_id
            assert app.state.analysis_request_id == "third"
            assert app.state.analysis_socket is sockets[2]
        finally:
            release.set()
            for socket in sockets:
                socket.incoming.put_nowait(None)
            await asyncio.wait_for(asyncio.gather(*handlers), 1)
            app.state.library.close()
        assert not active

    asyncio.run(scenario())
