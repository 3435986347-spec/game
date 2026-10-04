import socket

import pytest

from xiangqi.__main__ import pick_port, port_is_free


@pytest.fixture
def busy_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        yield s.getsockname()[1]


def test_busy_port_detected(busy_port):
    assert not port_is_free("127.0.0.1", busy_port)


def test_falls_back_to_next_free_port(busy_port, capsys):
    port = pick_port("127.0.0.1", busy_port, fallback=True)
    assert port != busy_port and port_is_free("127.0.0.1", port)
    assert "已被占用" in capsys.readouterr().out


def test_explicit_busy_port_exits_with_advice(busy_port):
    with pytest.raises(SystemExit, match="已被占用"):
        pick_port("127.0.0.1", busy_port, fallback=False)
