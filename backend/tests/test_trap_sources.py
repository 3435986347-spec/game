"""公开棋谱来源作为详情的一部分返回，兼容未填写来源的旧条目。"""

from dataclasses import replace

from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.api.schemas import TrapDetailView
from xiangqi.config import AppConfig
from xiangqi.openings import TRAP_LINES


def test_trap_detail_returns_public_source():
    with TestClient(create_app(AppConfig(static_dir=None))) as client:
        for trap_id, line in TRAP_LINES.items():
            response = client.get(f"/api/traps/{trap_id}")
            assert response.status_code == 200
            detail = response.json()
            assert detail["source"] == line.trap.source
            assert detail["trap_fen"] == line.trap_fen
            assert detail["moves"][0]["iccs"] == line.moves[0]
        assert client.get("/api/traps/missing").status_code == 404


def test_missing_source_is_empty_and_response_schema_is_backward_compatible(monkeypatch):
    trap_id, line = next(iter(TRAP_LINES.items()))
    monkeypatch.setitem(TRAP_LINES, trap_id, replace(line, trap=replace(line.trap, source="")))
    with TestClient(create_app(AppConfig(static_dir=None))) as client:
        detail = client.get(f"/api/traps/{trap_id}").json()
    assert detail["source"] == ""
    detail.pop("source")
    assert TrapDetailView.model_validate(detail).source == ""
