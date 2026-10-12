import os
import sys
import time
from pathlib import Path

import pytest
from conftest import FAKE_ENGINE
from fastapi.testclient import TestClient

from xiangqi.api import create_app
from xiangqi.config import AppConfig, load_config, load_dotenv
from xiangqi.core import START_FEN
from xiangqi.engine import EngineConfig
from xiangqi.llm import LLMConfig, OpenAICompatSettings

PGN = """[Event "测试赛"]
[Red "张三"]
[Black "李四"]
[Result "1-0"]
1. H2-E2 H9-G7 2. E2-E6 G7-E6 1-0
""".encode()


def make_config(tmp_path, *, engine: bool = True, **extra) -> AppConfig:
    engine_config = None
    if engine:
        engine_config = EngineConfig(
            path=Path(sys.executable), args=(str(FAKE_ENGINE),), hint_movetime_ms=50
        )
    return AppConfig(
        static_dir=None,
        engine=engine_config,
        library_db=tmp_path / "lib.db",
        review_movetime_ms=30,
        **extra,
    )


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_config(tmp_path))) as c:
        yield c


def import_game(client) -> int:
    resp = client.post(
        "/api/library/import", params={"filename": "t.pgn"}, content=PGN,
        headers={"Content-Type": "application/octet-stream"},
    )  # fmt: skip
    job_id = resp.json()["job_id"]
    for _ in range(100):
        if client.get(f"/api/library/import/{job_id}").json()["done"]:
            break
        time.sleep(0.05)
    return client.get("/api/library/games").json()["items"][0]["id"]


def wait_review(client, game_id: int) -> dict:
    for _ in range(200):
        view = client.get(f"/api/library/games/{game_id}/review").json()
        if view["status"] in ("done", "error"):
            return view
        time.sleep(0.05)
    raise AssertionError("复盘没有完成")


def test_review_flow(client):
    gid = import_game(client)
    assert client.get(f"/api/library/games/{gid}/review").json()["status"] == "none"

    started = client.post(f"/api/library/games/{gid}/review").json()
    assert started["status"] == "running" and started["total"] == 5
    view = wait_review(client, gid)
    assert view["status"] == "done", view
    report = view["report"]
    assert report["engine"] == "FakeEngine 1.0" and report["movetime_ms"] == 30
    assert len(report["curve"]) == 5 and len(report["moves"]) == 4
    assert report["focus"] == ["red", "black"]
    first = report["moves"][0]
    assert first["cn"] == "炮二平五" and first["side"] == "red" and first["phase"] == "开局"
    assert first["best_move"] == "a0a1"  # 假引擎总是推荐字母序第一的着法
    assert first["best_cn"] == "车九进一"
    # 假引擎对走棋方总是 0.59：不是最佳着法 → 0.59 − 0.41 = 0.18，评为失误
    assert first["grade"] == "失误" and first["drop"] == pytest.approx(0.18)
    assert report["stats"]["red"]["moves"] == 2
    assert len(report["key_moments"]) == 3
    moment = report["moves"][report["key_moments"][0] - 1]
    assert moment["explanation"]["source"] == "template"  # 没配大模型
    assert moment["explanation"]["headline"]

    # 再次请求复盘：结果已存库，直接返回
    again = client.post(f"/api/library/games/{gid}/review").json()
    assert again["status"] == "done"
    # 讲解任意一步（按需生成并保存）
    plain = next(m for m in report["moves"] if m["ply"] not in report["key_moments"])
    resp = client.post(f"/api/library/games/{gid}/review/explain", json={"ply": plain["ply"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "template" and "未配置大模型" in resp.json()["note"]
    stored = client.get(f"/api/library/games/{gid}/review").json()["report"]
    assert stored["moves"][plain["ply"] - 1]["explanation"] == resp.json()

    resp = client.post(f"/api/library/games/{gid}/review/explain", json={"ply": 9})
    assert resp.status_code == 400
    client.delete(f"/api/library/games/{gid}")
    assert client.get(f"/api/library/games/{gid}/review").status_code == 404


def test_review_persists_across_restart(tmp_path):
    with TestClient(create_app(make_config(tmp_path))) as c:
        gid = import_game(c)
        c.post(f"/api/library/games/{gid}/review")
        assert wait_review(c, gid)["status"] == "done"
    with TestClient(create_app(make_config(tmp_path, engine=False))) as c:
        view = c.get(f"/api/library/games/{gid}/review").json()
        assert view["status"] == "done" and len(view["report"]["moves"]) == 4


def test_review_requires_engine_and_moves(tmp_path):
    with TestClient(create_app(make_config(tmp_path, engine=False))) as c:
        gid = import_game(c)
        resp = c.post(f"/api/library/games/{gid}/review")
        assert resp.status_code == 503 and "引擎" in resp.json()["detail"]
        resp = c.post(f"/api/library/games/{gid}/review/explain", json={"ply": 1})
        assert resp.status_code == 409
        assert c.post("/api/library/games/999/review").status_code == 404


def test_live_game_review_and_stale_after_more_moves(client):
    gid = client.post("/api/games", json={"mode": "free"}).json()["id"]
    assert client.post(f"/api/games/{gid}/review").status_code == 400  # 还没走棋
    for move in ("h2e2", "h9g7"):
        client.post(f"/api/games/{gid}/moves", json={"move": move})
    library_id = client.post(f"/api/games/{gid}/review").json()["library_id"]
    report = wait_review(client, library_id)["report"]
    assert len(report["moves"]) == 2 and report["focus"] == ["red", "black"]  # 自由对弈：双方都是我

    client.post(f"/api/games/{gid}/moves", json={"move": "h0g2"})
    client.post(f"/api/games/{gid}/save")  # 着法变了：旧的复盘作废
    assert client.get(f"/api/library/games/{library_id}/review").json()["status"] == "none"
    client.post(f"/api/games/{gid}/review")
    assert len(wait_review(client, library_id)["report"]["moves"]) == 3


def test_vs_ai_review_focuses_on_user(client):
    gid = client.post(
        "/api/games", json={"mode": "vs_ai", "user_side": "black", "ai_level": 1}
    ).json()["id"]
    client.post(f"/api/games/{gid}/ai-move")
    library_id = client.post(f"/api/games/{gid}/review").json()["library_id"]
    report = wait_review(client, library_id)["report"]
    assert report["focus"] == ["black"] and report["key_moments"] == []  # 黑方还没走


def test_hint_level_1(client):
    gid = client.post("/api/games", json={"mode": "vs_ai", "user_side": "red"}).json()["id"]
    resp = client.post(f"/api/games/{gid}/hint", json={"level": 1})
    assert resp.status_code == 200, resp.text
    hint = resp.json()
    assert hint["level"] == 1 and hint["from_square"] is None and hint["move"] is None
    assert hint["text"]
    assert client.get(f"/api/games/{gid}").json()["hints_used"] == 1


def test_hint_level_1_without_engine(tmp_path):
    with TestClient(create_app(make_config(tmp_path, engine=False))) as c:
        gid = c.post("/api/games", json={"mode": "free"}).json()["id"]
        assert c.post(f"/api/games/{gid}/hint", json={"level": 1}).status_code == 200
        assert c.post(f"/api/games/{gid}/hint", json={"level": 2}).status_code == 503


def test_llm_status(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(create_app(make_config(tmp_path))) as c:
        assert c.get("/api/llm").json()["provider"] == "none"
    config = make_config(tmp_path, llm=LLMConfig(provider="claude"))
    with TestClient(create_app(config)) as c:
        status = c.get("/api/llm").json()
    assert status["provider"] == "claude" and status["model"] == "claude-opus-5-5"
    # 没设环境变量时 SDK 还会用 ant auth login 的登录信息：算就绪，但给出说明
    assert status["ready"] is True and "ANTHROPIC_API_KEY" in status["problem"]
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = make_config(
        tmp_path,
        llm=LLMConfig(provider="openai_compat", openai_compat=OpenAICompatSettings(model="m")),
    )
    with TestClient(create_app(config)) as c:
        status = c.get("/api/llm").json()
    assert status["ready"] is False and "DEEPSEEK_API_KEY" in status["problem"]


def test_dotenv_values(tmp_path, monkeypatch):
    for key in ("XQ_A", "XQ_B", "XQ_C", "XQ_D"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("XQ_D", "from-system")
    env = tmp_path / ".env"
    env.write_text(
        'XQ_A=sk-abc  # 我的 key\nexport XQ_B="quoted # not a comment"  # comment\n'
        "XQ_C='single'\nXQ_D=from-dotenv\n",
        encoding="utf-8",
    )
    load_dotenv(env)
    assert os.environ["XQ_A"] == "sk-abc"
    assert os.environ["XQ_B"] == "quoted # not a comment"
    assert os.environ["XQ_C"] == "single"
    assert os.environ["XQ_D"] == "from-system"  # 不覆盖已有的环境变量


def test_live_review_after_library_copy_was_deleted(client):
    gid = client.post("/api/games", json={"mode": "free"}).json()["id"]
    client.post(f"/api/games/{gid}/moves", json={"move": "h2e2"})
    first = client.post(f"/api/games/{gid}/save").json()["library_id"]
    client.delete(f"/api/library/games/{first}")
    resp = client.post(f"/api/games/{gid}/review")  # 重新另存一局，再复盘
    assert resp.status_code == 200, resp.text
    second = resp.json()["library_id"]
    assert second != first and wait_review(client, second)["status"] == "done"


def test_review_of_deleted_game_is_not_saved(tmp_path):
    from xiangqi.library import Library

    library = Library(tmp_path / "lib.db")
    gid = library.save_game(
        initial_fen=START_FEN, moves=["h2e2"], result="*", headers={"Red": "我", "Black": "我"}
    )
    library.delete(gid)
    library.save_review(gid, moves_hash="x", engine=None, movetime_ms=None, summary={}, rows=[])
    assert library.get_review(gid) is None
    library.close()


def test_load_llm_config_and_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TEST_LLM_KEY", raising=False)
    (tmp_path / "config.toml").write_text(
        """
[llm]
provider = "openai_compat"
level = "中级"

[llm.openai_compat]
base_url = "http://localhost:1234/v1"
api_key_env = "MY_TEST_LLM_KEY"
model = "local-model"
json_mode = false

[review]
movetime_ms = 250
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("# 本地密钥\nMY_TEST_LLM_KEY='sk-from-dotenv'\n", "utf-8")
    config = load_config(tmp_path / "config.toml")
    assert config.llm.provider == "openai_compat" and config.llm.level == "中级"
    assert config.llm.openai_compat.model == "local-model"
    assert config.llm.openai_compat.json_mode is False
    assert config.llm.claude.model == "claude-opus-5-5"  # 没写的用默认值
    assert config.review_movetime_ms == 250

    assert os.environ["MY_TEST_LLM_KEY"] == "sk-from-dotenv"
    monkeypatch.delenv("MY_TEST_LLM_KEY")


@pytest.mark.parametrize("black", [False, True])
def test_old_terminal_best_review_upgrades_without_engine_and_persists(tmp_path, black):
    from xiangqi.core import Position
    from xiangqi.library import moves_hash
    from xiangqi.llm import EngineLine
    from xiangqi.training import PositionEval, grade_moves, review_rows, summarize

    with TestClient(create_app(make_config(tmp_path, engine=False))) as client:
        library = client.app.state.library
        position = Position.start()
        position.turn = -1 if black else 1
        position.halfmove_clock = 1
        side = position.turn
        move, other = ("a9a8", "h7e7") if black else ("a0a1", "h2e2")
        fen = position.fen()
        gid = library.save_game(
            initial_fen=fen, moves=[move], result="1/2-1/2", headers={"Red": "我", "Black": "我"}
        )
        before = PositionEval(
            0, side, 0.1 if black else 0.9,
            [EngineLine(move, (move,), 0.9), EngineLine(other, (other,), 0.3)],
        )
        after = PositionEval(1, -side, 0.5, [], "和棋（自然限着）")
        grades = grade_moves(fen, [move], [before, after])
        # 构造旧策略留下的完整分析证据及错误评级、准确率、关键时刻。
        grades[0].terminal = False
        grades[0].grade = "妙着"
        summary = summarize(grades, [1, -1])
        summary["tags"] = [{"tag": "捉双", "count": 1}]
        rows = review_rows([before, after], grades, {1: {"headline": "旧妙着", "tags": ["捉双"]}})
        library.save_review(
            gid, moves_hash=moves_hash(fen, [move]), engine="旧引擎", movetime_ms=1500,
            summary=summary, rows=rows,
        )
        conn = library.connect()
        conn.execute(
            "UPDATE reviews SET created_at = '2000-01-01T00:00:00Z' WHERE game_id = ?", (gid,)
        )
        conn.commit()
        original_info, original_rows = library.get_review(gid)
        assert not client.app.state.engines.configured
        report = client.get(f"/api/library/games/{gid}/review").json()["report"]
        assert report["moves"][0]["grade"] == "漏着"
        assert report["moves"][0]["drop"] == 0.4
        assert report["moves"][0]["explanation"] is None
        assert report["key_moments"] == [1] and report["tags"] == []
        stats = report["stats"]["black" if black else "red"]
        assert stats["accuracy"] < 100.0 and stats["grades"]["漏着"] == 1
        info, saved_rows = library.get_review(gid)
        assert info["created_at"] == report["created_at"] == "2000-01-01T00:00:00Z"
        assert info["summary"]["grade_policy"] == 2
        assert saved_rows[1]["is_best"] == 1  # 保持实际搜索里的最佳着语义。
        assert saved_rows[1]["grade"] == "漏着"
        assert [row["lines"] for row in saved_rows] == [row["lines"] for row in original_rows]
        assert not library.update_review_grades(
            gid, moves_hash=original_info["moves_hash"], previous_summary=original_info["summary"],
            summary={"wrong": True}, rows=original_rows,
        )  # 第一次升级后的报告不会被并发拿着旧summary的请求覆盖。
        assert client.get(f"/api/library/games/{gid}/review").json()["report"] == report


@pytest.mark.parametrize("had_explanation", [False, True])
def test_grade_upgrade_preserves_concurrently_saved_unchanged_explanation(
    tmp_path, monkeypatch, had_explanation
):
    from xiangqi.library import moves_hash
    from xiangqi.llm import EngineLine
    from xiangqi.training import PositionEval, grade_moves, review_rows, summarize

    fresh = {
        "headline": "新保存的讲解", "why": "已核对的原因", "better": "本步走法",
        "principle": "保持先手", "tags": [], "source": "template",
        "provider": None, "model": None, "note": None,
    }
    old = {**fresh, "headline": "旧快照里的讲解"}
    moves = ["a0a1", "a9a8"]
    evals = [
        PositionEval(0, 1, 0.9, [EngineLine("a0a1", ("a0a1",), 0.9)]),
        PositionEval(1, -1, 0.1, [EngineLine("a9a8", ("a9a8",), 0.9)]),
        PositionEval(2, 1, 0.5, [], "和棋（自然限着）"),
    ]
    grades = grade_moves(START_FEN, moves, evals)
    grades[1].terminal = False
    grades[1].grade = "好棋"  # 第二步需要升级并清掉不适用的旧讲解。
    explanations = {1: old, 2: old} if had_explanation else {}
    with TestClient(create_app(make_config(tmp_path, engine=False))) as client:
        library = client.app.state.library
        gid = library.save_game(
            initial_fen=START_FEN, moves=moves, result="1/2-1/2",
            headers={"Red": "我", "Black": "我"},
        )
        library.save_review(
            gid, moves_hash=moves_hash(START_FEN, moves), engine="旧引擎", movetime_ms=1500,
            summary=summarize(grades, [1, -1]), rows=review_rows(evals, grades, explanations),
        )
        update = library.update_review_grades

        def concurrent_explanations(*args, **kwargs):
            # _load_review 已取得快照；仅保存讲解，不改变reviews.summary或moves_hash。
            library.set_explanation(gid, 1, fresh)
            library.set_explanation(gid, 2, fresh)
            return update(*args, **kwargs)

        monkeypatch.setattr(library, "update_review_grades", concurrent_explanations)
        response = client.get(f"/api/library/games/{gid}/review")
        assert response.status_code == 200, response.text
        report = response.json()["report"]
        assert report["moves"][0]["grade"] == "好棋"
        assert report["moves"][0]["explanation"] == fresh
        assert report["moves"][1]["grade"] == "漏着"
        assert report["moves"][1]["explanation"] is None
        _, stored = library.get_review(gid)
        assert stored[1]["explanation"] is not None
        assert stored[2]["explanation"] is None
