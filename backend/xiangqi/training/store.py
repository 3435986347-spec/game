"""训练数据：猜着练习、题库、错题本（间隔复习）、个人数据。和棋谱库共用一个 SQLite 文件和连接管理。

表（docs 9.4 节）：
- guess_sessions / guess_answers：猜着练习的进度和每一步的作答；
- puzzles：自动出的题（「唯一好棋」局面），附规则引擎打的标签和难度分；
- cards：错题本里的卡片（kind = mistake 错题 / puzzle 做错的题），按间隔复习安排到期时间；
- kv：个人数据（如做题等级分）。
时间一律用本地时间字符串「YYYY-MM-DD HH:MM:SS」，可以直接按字符串比较先后。
"""

from __future__ import annotations

import json
from datetime import datetime

from ..library import Library

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guess_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    side TEXT NOT NULL,  -- red | black：你执哪一方
    start_ply INTEGER NOT NULL,  -- 从第几步开始猜（前面的开局跳过）
    current_ply INTEGER NOT NULL,  -- 当前局面走了多少步；没结束时轮到你猜下一步
    score INTEGER NOT NULL DEFAULT 0,
    max_score INTEGER NOT NULL DEFAULT 0,
    finished INTEGER NOT NULL DEFAULT 0,
    cards_added INTEGER NOT NULL DEFAULT 0,  -- 猜完后加入错题本的步数
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS guess_answers (
    session_id INTEGER NOT NULL,
    ply INTEGER NOT NULL,  -- 这步棋是第几步（从 1 开始）
    user_move TEXT,  -- 你猜的着法（ICCS）；放弃时为 NULL
    master_move TEXT NOT NULL,
    points INTEGER NOT NULL,
    loss REAL,  -- 比大师着法差多少期望得分（不差为 0；放弃时为 NULL）
    detail TEXT NOT NULL,  -- JSON：评估、标注、讲解
    PRIMARY KEY (session_id, ply)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS puzzles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fen TEXT NOT NULL,  -- 题目局面，轮到解题方走
    solution TEXT NOT NULL,  -- 正解（ICCS）
    pv TEXT NOT NULL DEFAULT '',  -- 正解之后的变化（ICCS，空格分隔）
    tags TEXT NOT NULL,  -- JSON 数组：杀法、弃子、捉双……
    rating INTEGER NOT NULL,  -- 难度分
    source_game_id INTEGER,
    source_ply INTEGER,  -- 题目局面在来源对局里是走了多少步之后
    master_found INTEGER,  -- 棋谱里实际走出了正解
    attempts INTEGER NOT NULL DEFAULT 0,
    solved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (fen, solution)
);
CREATE INDEX IF NOT EXISTS puzzles_rating ON puzzles(rating);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,  -- mistake | puzzle
    fen TEXT NOT NULL,  -- 局面，轮到要找正确着法的一方走
    solution TEXT NOT NULL,  -- 正确着法（ICCS）
    pv TEXT NOT NULL DEFAULT '',  -- 正确着法之后的变化（ICCS，空格分隔）
    played TEXT,  -- 当时走的错着
    explanation TEXT,  -- JSON：讲解
    source TEXT,  -- 来源说明
    source_game_id INTEGER,
    source_ply INTEGER,
    puzzle_id INTEGER,
    interval_days REAL NOT NULL DEFAULT 0,
    ease REAL NOT NULL DEFAULT 2.5,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL,
    last_reviewed TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (kind, fen, solution)
);
CREATE INDEX IF NOT EXISTS cards_due ON cards(due_at);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime(TIME_FORMAT)


def _loads(text: str | None):
    return json.loads(text) if text else None


class TrainingStore:
    def __init__(self, library: Library) -> None:
        self.library = library
        conn = library.connect()
        conn.executescript(_SCHEMA)
        conn.commit()

    def _conn(self):
        return self.library.connect()

    # ---- 猜着练习 ----

    def create_session(self, game_id: int, side: str, start_ply: int) -> int:
        conn = self._conn()
        cur = conn.execute(
            """INSERT INTO guess_sessions (game_id, side, start_ply, current_ply)
               VALUES (?, ?, ?, ?)""",
            (game_id, side, start_ply, start_ply),
        )
        conn.commit()
        return cur.lastrowid

    def session(self, session_id: int) -> dict | None:
        row = (
            self._conn()
            .execute("SELECT * FROM guess_sessions WHERE id = ?", (session_id,))
            .fetchone()
        )
        return dict(row) if row else None

    def sessions(self, limit: int = 20) -> list[dict]:
        rows = self._conn().execute(
            """SELECT s.*, g.red, g.black, g.event, g.ply_count FROM guess_sessions s
               LEFT JOIN games g ON g.id = s.game_id ORDER BY s.id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def answers(self, session_id: int) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM guess_answers WHERE session_id = ? ORDER BY ply", (session_id,)
        )
        return [{**dict(r), "detail": _loads(r["detail"])} for r in rows]

    def save_answer(
        self,
        session_id: int,
        answer: dict,
        *,
        current_ply: int,
        points: int,
        finished: bool,
    ) -> None:
        """保存一步作答，并推进进度、加分（同一个事务）。answer 的键与 guess_answers 的列相同。"""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO guess_answers
                   (session_id, ply, user_move, master_move, points, loss, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    answer["ply"],
                    answer["user_move"],
                    answer["master_move"],
                    answer["points"],
                    answer["loss"],
                    json.dumps(answer["detail"], ensure_ascii=False),
                ),
            )
            conn.execute(
                """UPDATE guess_sessions SET current_ply = ?, score = score + ?,
                   max_score = max_score + 3, finished = ? WHERE id = ?""",
                (current_ply, points, int(finished), session_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def set_cards_added(self, session_id: int, count: int) -> None:
        conn = self._conn()
        conn.execute("UPDATE guess_sessions SET cards_added = ? WHERE id = ?", (count, session_id))
        conn.commit()

    def delete_session(self, session_id: int) -> bool:
        conn = self._conn()
        deleted = conn.execute("DELETE FROM guess_sessions WHERE id = ?", (session_id,)).rowcount
        conn.execute("DELETE FROM guess_answers WHERE session_id = ?", (session_id,))
        conn.commit()
        return deleted > 0

    # ---- 错题本 ----

    def add_card(
        self,
        kind: str,
        fen: str,
        solution: str,
        *,
        played: str | None = None,
        pv: list[str] | tuple[str, ...] = (),
        explanation: dict | None = None,
        source: str | None = None,
        source_game_id: int | None = None,
        source_ply: int | None = None,
        puzzle_id: int | None = None,
        now: datetime | None = None,
    ) -> int | None:
        """加入错题本，马上到期。同一局面同一正解已经在本子里时返回 None。"""
        conn = self._conn()
        cur = conn.execute(
            """INSERT OR IGNORE INTO cards (kind, fen, solution, pv, played, explanation,
                   source, source_game_id, source_ply, puzzle_id, due_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kind,
                fen,
                solution,
                " ".join(pv),
                played,
                json.dumps(explanation, ensure_ascii=False) if explanation else None,
                source,
                source_game_id,
                source_ply,
                puzzle_id,
                now_text(now),
            ),
        )
        conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def card(self, card_id: int) -> dict | None:
        row = self._conn().execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return self._card(row) if row else None

    @staticmethod
    def _card(row) -> dict:
        return {**dict(row), "explanation": _loads(row["explanation"])}

    def next_due(self, *, kind: str | None = None, now: datetime | None = None) -> dict | None:
        row = (
            self._conn()
            .execute(
                """SELECT * FROM cards WHERE due_at <= ? AND (? IS NULL OR kind = ?)
                   ORDER BY due_at, id LIMIT 1""",
                (now_text(now), kind, kind),
            )
            .fetchone()
        )
        return self._card(row) if row else None

    def due_count(self, *, kind: str | None = None, now: datetime | None = None) -> int:
        return (
            self._conn()
            .execute(
                "SELECT COUNT(*) FROM cards WHERE due_at <= ? AND (? IS NULL OR kind = ?)",
                (now_text(now), kind, kind),
            )
            .fetchone()[0]
        )

    def card_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM cards").fetchone()[0]

    def cards(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM cards ORDER BY due_at, id LIMIT ? OFFSET ?", (limit, offset)
        )
        return [self._card(r) for r in rows]

    def update_card(self, card_id: int, fields: dict) -> None:
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        conn = self._conn()
        conn.execute(f"UPDATE cards SET {assignments} WHERE id = :id", {**fields, "id": card_id})
        conn.commit()

    def set_card_explanation(self, card_id: int, explanation: dict) -> None:
        self.update_card(card_id, {"explanation": json.dumps(explanation, ensure_ascii=False)})

    def delete_card(self, card_id: int) -> bool:
        conn = self._conn()
        deleted = conn.execute("DELETE FROM cards WHERE id = ?", (card_id,)).rowcount
        conn.commit()
        return deleted > 0

    # ---- 题库 ----

    def add_puzzles(self, puzzles: list[dict]) -> int:
        """加入题库（同一局面同一正解只收一次），返回新增的题数。"""
        if not puzzles:
            return 0
        conn = self._conn()
        before = conn.total_changes
        conn.executemany(
            """INSERT OR IGNORE INTO puzzles (fen, solution, pv, tags, rating, source_game_id,
                   source_ply, master_found)
               VALUES (:fen, :solution, :pv, :tags, :rating, :source_game_id, :source_ply,
                   :master_found)""",
            [{**p, "tags": json.dumps(p["tags"], ensure_ascii=False)} for p in puzzles],
        )
        conn.commit()
        return conn.total_changes - before

    def puzzle(self, puzzle_id: int) -> dict | None:
        row = self._conn().execute("SELECT * FROM puzzles WHERE id = ?", (puzzle_id,)).fetchone()
        return {**dict(row), "tags": json.loads(row["tags"])} if row else None

    def pick_puzzle(
        self, rating: float, *, theme: str | None = None, exclude: int | None = None
    ) -> dict | None:
        """挑一道题：优先没做过的，难度分接近 rating，带一点随机。"""
        pattern = None if not theme else f'%"{theme}"%'
        row = (
            self._conn()
            .execute(
                """SELECT * FROM puzzles WHERE (? IS NULL OR tags LIKE ?) AND id IS NOT ?
                   ORDER BY attempts > 0, ABS(rating - ?) + ABS(RANDOM()) % 300 LIMIT 1""",
                (pattern, pattern, exclude, rating),
            )
            .fetchone()
        )
        return {**dict(row), "tags": json.loads(row["tags"])} if row else None

    def record_attempt(self, puzzle_id: int, solved: bool) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE puzzles SET attempts = attempts + 1, solved = solved + ? WHERE id = ?",
            (int(solved), puzzle_id),
        )
        conn.commit()

    def puzzle_stats(self) -> dict:
        conn = self._conn()
        total, attempted, solved = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(attempts > 0), 0), COALESCE(SUM(solved > 0), 0)
               FROM puzzles"""
        ).fetchone()
        tags: dict[str, int] = {}
        for (text,) in conn.execute("SELECT tags FROM puzzles"):
            for tag in json.loads(text):
                tags[tag] = tags.get(tag, 0) + 1
        ordered = sorted(tags.items(), key=lambda kv: -kv[1])
        return {
            "total": total,
            "attempted": attempted,
            "solved": solved,
            "tags": [{"tag": t, "count": n} for t, n in ordered],
        }

    # ---- 个人数据 ----

    def get_value(self, key: str, default: str | None = None) -> str | None:
        row = self._conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_value(self, key: str, value: str) -> None:
        conn = self._conn()
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
