"""棋谱库：SQLite 存储、导入、检索、局面统计。

表结构：
- games：一行一局（元数据、起始局面、ICCS 着法）。
  kind 为 library（导入的棋谱）或 my_game（自己下的对局）。
- positions：局面索引，只记录每局前 index_plies 步。key 为 Zobrist 哈希（含走棋方），
  next_move 为这个局面下实际走的下一步（from * 90 + to；终局为 NULL）。用于局面检索和开局统计。
- reviews / move_analysis：整盘复盘的结果（每局一份）。move_analysis 每个局面一行：
  引擎评估、走到这个局面的那步棋的评级，以及讲解。对局删除时一并删除；
  对局着法改过之后（reviews.moves_hash 对不上）读取时作废。
- explain_cache：大模型讲解的缓存（局面 + 着法 + 评级 + 水平 + 模型），同样的错误不重复花钱。
每个线程用自己的连接（导入在后台线程进行），数据库为 WAL 模式，导入时也可以正常查询。
导入时最多约 1 秒提交一次，写锁不会长时间占着，导入期间保存对局只需稍等。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..core import Position, move_to_chinese, move_to_iccs, parse_fen, parse_iccs
from ..core.zobrist import compute_key
from .importer import GameFormatError, ParsedGame, resolve_game
from .openings import classify
from .parse import RawGame

SCHEMA_VERSION = 2
_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- id 不复用：删掉的对局，旧链接不会指到别的对局
    kind TEXT NOT NULL DEFAULT 'library',
    event TEXT, site TEXT, date TEXT, round TEXT,
    red TEXT, red_team TEXT, black TEXT, black_team TEXT,
    result TEXT NOT NULL DEFAULT '*',
    opening TEXT,
    initial_fen TEXT NOT NULL,
    moves TEXT NOT NULL,
    ply_count INTEGER NOT NULL,
    source TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS games_date ON games(date);
CREATE INDEX IF NOT EXISTS games_opening ON games(opening);
CREATE INDEX IF NOT EXISTS games_kind ON games(kind);
CREATE TABLE IF NOT EXISTS positions (
    key INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    ply INTEGER NOT NULL,
    next_move INTEGER,
    PRIMARY KEY (key, game_id, ply)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
    game_id INTEGER PRIMARY KEY,
    moves_hash TEXT NOT NULL,  -- 复盘时的起始局面 + 着法；对局改过之后复盘作废
    engine TEXT,
    movetime_ms INTEGER,
    summary TEXT NOT NULL,  -- JSON：准确率、分阶段表现、各评级数量、关键时刻
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS move_analysis (
    game_id INTEGER NOT NULL,
    ply INTEGER NOT NULL,  -- 局面序号：0 为起始局面，i 为走完第 i 步之后
    red_win REAL NOT NULL,  -- 这个局面红方的期望得分
    lines TEXT NOT NULL,  -- JSON：引擎候选着法（走棋方视角）
    terminal TEXT,  -- 终局说明
    move TEXT,  -- 走到这个局面的那步棋（ply ≥ 1）及其评级
    grade TEXT,
    win_before REAL,
    win_after REAL,
    is_best INTEGER,
    phase TEXT,
    explanation TEXT,  -- JSON：讲解（关键时刻自动生成，其余按需生成）
    PRIMARY KEY (game_id, ply)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS explain_cache (
    cache_key TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""

RESULTS = ("1-0", "0-1", "1/2-1/2", "*")
_SUMMARY_COLUMNS = "id, kind, event, date, red, black, result, opening, ply_count, source"
_MAX_ERRORS = 100


@dataclass
class ImportReport:
    games_seen: int = 0
    imported: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)  # [{index, title, reason}]，最多 100 条

    def add_error(self, raw: RawGame, reason: str) -> None:
        self.failed += 1
        if len(self.errors) < _MAX_ERRORS:
            self.errors.append(
                {"index": raw.index, "title": game_title(raw.headers), "reason": reason}
            )


def game_title(headers: dict[str, str]) -> str:
    red, black, event = headers.get("Red"), headers.get("Black"), headers.get("Event")
    title = f"{red or '红方'} vs {black or '黑方'}"
    if event and event != "-":
        title += f"（{event}）"
    return title


def _clean(value: str | None) -> str | None:
    """PGN 里常用 "-" 或 "?" 表示未知。"""
    if value is None:
        return None
    value = value.strip()
    return None if value in ("", "-", "?", "??", "????.??.??") else value


def _game_row(parsed: ParsedGame) -> dict[str, object]:
    """games 表中由对局内容决定的列（不含 kind、source、content_hash）。"""
    h = parsed.headers
    return {
        "event": _clean(h.get("Event")),
        "site": _clean(h.get("Site")),
        "date": _clean(h.get("Date")),
        "round": _clean(h.get("Round")),
        "red": _clean(h.get("Red")),
        "red_team": _clean(h.get("RedTeam")),
        "black": _clean(h.get("Black")),
        "black_team": _clean(h.get("BlackTeam")),
        "result": parsed.result,
        "opening": _clean(h.get("Opening")) or classify(parsed.initial_fen, parsed.moves),
        "initial_fen": parsed.initial_fen,
        "moves": " ".join(parsed.moves),
        "ply_count": len(parsed.moves),
    }


class Library:
    def __init__(self, path: str | Path, *, index_plies: int = 40) -> None:
        self.path = str(path)
        self.index_plies = index_plies
        self._local = threading.local()
        self._memory_conn: sqlite3.Connection | None = None
        self._conns: list[sqlite3.Connection] = []  # 各线程打开的连接，close() 时统一关闭
        self._conns_lock = threading.Lock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- 连接 ----

    def connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":  # 测试用：所有线程共享一个连接
            if self._memory_conn is None:
                self._memory_conn = self._open()
            return self._memory_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn = conn
        return conn

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with self._conns_lock:
            self._conns.append(conn)
        return conn

    def _init_schema(self) -> None:
        conn = self.connect()
        conn.executescript(_SCHEMA)
        conn.execute(  # 新表都是 CREATE IF NOT EXISTS，旧库打开时自动补上
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()

    # ---- 导入 ----

    def import_games(
        self,
        raw_games: Iterable[RawGame],
        *,
        source: str | None = None,
        progress: Callable[[ImportReport], None] | None = None,
        batch_size: int = 500,
        report: ImportReport | None = None,
        cancel: threading.Event | None = None,
    ) -> ImportReport:
        """校验并导入棋谱。不合法的对局跳过并记录原因；重复的对局（见 _content_hash）跳过。
        传入 report 时就地更新它（其他线程可以随时读取进度）。
        cancel 被设置时停止导入，已导入的对局保留。出现意外错误时撤销尚未提交的部分再抛出。"""
        report = report if report is not None else ImportReport()
        conn = self.connect()
        pending = 0  # 已写入、尚未提交的对局数
        last_commit = time.monotonic()
        try:
            for raw in raw_games:
                if cancel is not None and cancel.is_set():
                    break
                report.games_seen += 1
                try:
                    parsed = resolve_game(raw)
                except GameFormatError as e:
                    report.add_error(raw, str(e))
                else:
                    if not parsed.moves:
                        report.add_error(raw, "没有着法")
                    elif self._insert(conn, parsed, kind="library", source=source) is None:
                        report.duplicates += 1
                    else:
                        report.imported += 1
                        pending += 1
                if pending and (pending >= batch_size or time.monotonic() - last_commit > 1.0):
                    conn.commit()
                    pending, last_commit = 0, time.monotonic()
                if progress is not None and report.games_seen % 200 == 0:
                    progress(report)
            conn.commit()
        except BaseException:
            conn.rollback()
            report.imported -= pending  # 这些没有写进库
            raise
        if progress is not None:
            progress(report)
        return report

    def _insert(
        self,
        conn: sqlite3.Connection,
        parsed: ParsedGame,
        *,
        kind: str,
        source: str | None,
        content_hash: str | None = None,
    ) -> int | None:
        row = _game_row(parsed)
        row.update(kind=kind, source=source, content_hash=content_hash or _content_hash(row))
        # 先查一下是否重复：只读查询不会开启写事务，重复导入时不占写锁
        if conn.execute(
            "SELECT 1 FROM games WHERE content_hash = ?", (row["content_hash"],)
        ).fetchone():
            return None
        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        cur = conn.execute(f"INSERT OR IGNORE INTO games ({columns}) VALUES ({placeholders})", row)
        if cur.rowcount == 0:
            return None
        game_id = cur.lastrowid
        self._index_positions(conn, game_id, parsed)
        return game_id

    def _index_positions(self, conn: sqlite3.Connection, game_id: int, parsed: ParsedGame) -> None:
        keys = parsed.keys or _replay_keys(parsed.initial_fen, parsed.moves)
        rows = []
        for ply in range(min(len(parsed.moves), self.index_plies) + 1):
            next_move = None
            if ply < len(parsed.moves):
                frm, to = parse_iccs(parsed.moves[ply])
                next_move = frm * 90 + to
            rows.append((keys[ply], game_id, ply, next_move))
        conn.executemany(
            "INSERT OR IGNORE INTO positions (key, game_id, ply, next_move) VALUES (?, ?, ?, ?)",
            rows,
        )

    # ---- 自己的对局 ----

    def save_game(
        self,
        *,
        initial_fen: str,
        moves: list[str],
        result: str,
        headers: dict[str, str],
        library_id: int | None = None,
    ) -> int:
        """保存（或更新）自己下的对局，返回库中的 id。"""
        parsed = ParsedGame(headers, initial_fen, list(moves), result)
        conn = self.connect()
        if library_id is not None:
            # 原地更新，id 不变；那一局已被删除（或不是自己的对局）时另存一局
            row = _game_row(parsed)
            assignments = ", ".join(f"{name} = :{name}" for name in row)
            updated = conn.execute(
                f"UPDATE games SET {assignments} WHERE id = :id AND kind = 'my_game'",
                {**row, "id": library_id},
            ).rowcount
            if updated:
                conn.execute("DELETE FROM positions WHERE game_id = ?", (library_id,))
                self._index_positions(conn, library_id, parsed)
                # 旧的复盘不用在这里删：读取时按 moves_hash 比较，着法变了才作废
                conn.commit()
                return library_id
        game_id = self._insert(
            conn, parsed, kind="my_game", source=None, content_hash=f"my_game:{uuid.uuid4().hex}"
        )
        conn.commit()
        assert game_id is not None
        return game_id

    # ---- 查询 ----

    def stats(self) -> dict[str, int]:
        conn = self.connect()
        total = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        mine = conn.execute("SELECT COUNT(*) FROM games WHERE kind = 'my_game'").fetchone()[0]
        return {"games": total, "my_games": mine, "indexed_plies": self.index_plies}

    def search(
        self,
        *,
        q: str | None = None,
        event: str | None = None,
        opening: str | None = None,
        result: str | None = None,
        kind: str | None = None,
        fen: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[int, list[dict]]:
        where, params = [], []
        if q:
            where.append("(red LIKE ? ESCAPE '\\' OR black LIKE ? ESCAPE '\\')")
            params += [_contains(q), _contains(q)]
        if event:
            where.append("event LIKE ? ESCAPE '\\'")
            params.append(_contains(event))
        if opening:
            where.append("opening = ?")
            params.append(opening)
        if result:
            where.append("result = ?")
            params.append(result)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if fen:
            where.append("id IN (SELECT game_id FROM positions WHERE key = ?)")
            params.append(fen_key(fen))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        conn = self.connect()
        total = conn.execute(f"SELECT COUNT(*) FROM games {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT {_SUMMARY_COLUMNS} FROM games {clause}
                ORDER BY (date IS NULL), date DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return total, [dict(row) for row in rows]

    def get(self, game_id: int) -> dict | None:
        row = self.connect().execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        moves = record.pop("moves").split()
        record.pop("content_hash", None)
        record.pop("created_at", None)
        pos = Position.from_fen(record["initial_fen"], validate=False)
        fens, checks, out = [pos.fen()], [pos.in_check()], []
        for text in moves:
            move = parse_iccs(text)
            out.append({"iccs": text, "cn": move_to_chinese(pos.board, move)})
            pos._push_unchecked(move)  # 导入时已校验
            fens.append(pos.fen())
            checks.append(pos.in_check())
        record.update(moves=out, fens=fens, checks=checks)
        return record

    def delete(self, game_id: int) -> bool:
        conn = self.connect()
        deleted = conn.execute("DELETE FROM games WHERE id = ?", (game_id,)).rowcount
        conn.execute("DELETE FROM positions WHERE game_id = ?", (game_id,))
        self._delete_review(conn, game_id)
        conn.commit()
        return deleted > 0

    # ---- 复盘 ----

    def game_moves(self, game_id: int) -> dict | None:
        """复盘需要的对局信息：起始局面、着法、对局双方、类型。"""
        row = (
            self.connect()
            .execute(
                "SELECT initial_fen, moves, red, black, kind FROM games WHERE id = ?", (game_id,)
            )
            .fetchone()
        )
        if row is None:
            return None
        return {**dict(row), "moves": row["moves"].split()}

    def save_review(
        self,
        game_id: int,
        *,
        moves_hash: str,
        engine: str | None,
        movetime_ms: int | None,
        summary: dict,
        rows: list[dict],
    ) -> None:
        """保存复盘结果（覆盖旧的）。rows 为 move_analysis 的各行（键与列名相同）。
        复盘期间对局被删掉了就不保存。"""
        conn = self.connect()
        try:
            self._delete_review(conn, game_id)  # 第一条写语句：拿到写锁，下面的检查不会被打断
            if not conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone():
                conn.rollback()
                return
            conn.execute(
                """INSERT INTO reviews (game_id, moves_hash, engine, movetime_ms, summary)
                   VALUES (?, ?, ?, ?, ?)""",
                (game_id, moves_hash, engine, movetime_ms, json.dumps(summary, ensure_ascii=False)),
            )
            conn.executemany(
                """INSERT INTO move_analysis (game_id, ply, red_win, lines, terminal, move, grade,
                       win_before, win_after, is_best, phase, explanation)
                   VALUES (:game_id, :ply, :red_win, :lines, :terminal, :move, :grade,
                       :win_before, :win_after, :is_best, :phase, :explanation)""",
                [
                    {
                        "game_id": game_id,
                        "terminal": None,
                        "move": None,
                        "grade": None,
                        "win_before": None,
                        "win_after": None,
                        "is_best": None,
                        "phase": None,
                        "explanation": None,
                        **row,
                    }
                    for row in rows
                ],
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def get_review(self, game_id: int) -> tuple[dict, list[dict]] | None:
        """(reviews 行，summary 已解析；move_analysis 各行，按 ply 排序)。没有复盘时返回 None。"""
        conn = self.connect()
        review = conn.execute("SELECT * FROM reviews WHERE game_id = ?", (game_id,)).fetchone()
        if review is None:
            return None
        info = dict(review)
        info["summary"] = json.loads(info["summary"])
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM move_analysis WHERE game_id = ? ORDER BY ply", (game_id,)
            )
        ]
        return info, rows

    def set_explanation(self, game_id: int, ply: int, explanation: dict) -> None:
        conn = self.connect()
        conn.execute(
            "UPDATE move_analysis SET explanation = ? WHERE game_id = ? AND ply = ?",
            (json.dumps(explanation, ensure_ascii=False), game_id, ply),
        )
        conn.commit()

    def delete_review(self, game_id: int) -> None:
        conn = self.connect()
        self._delete_review(conn, game_id)
        conn.commit()

    @staticmethod
    def _delete_review(conn: sqlite3.Connection, game_id: int) -> None:
        conn.execute("DELETE FROM reviews WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM move_analysis WHERE game_id = ?", (game_id,))

    # ---- 讲解缓存 ----

    def get_explanation(self, key: str) -> dict | None:
        row = (
            self.connect()
            .execute("SELECT content FROM explain_cache WHERE cache_key = ?", (key,))
            .fetchone()
        )
        return None if row is None else json.loads(row["content"])

    def put_explanation(self, key: str, content: dict, provider: str, model: str) -> None:
        conn = self.connect()
        conn.execute(
            """INSERT OR REPLACE INTO explain_cache (cache_key, content, provider, model)
               VALUES (?, ?, ?, ?)""",
            (key, json.dumps(content, ensure_ascii=False), provider, model),
        )
        conn.commit()

    def explorer(self, fen: str) -> dict:
        """某个局面在库中出现过多少局，以及之后各着法的局数和胜负。"""
        key = fen_key(fen)
        conn = self.connect()
        total = conn.execute(
            "SELECT COUNT(DISTINCT game_id) FROM positions WHERE key = ?", (key,)
        ).fetchone()[0]
        rows = conn.execute(
            """SELECT p.next_move AS move, COUNT(DISTINCT p.game_id) AS games,
                      COUNT(DISTINCT CASE WHEN g.result = '1-0' THEN g.id END) AS red_wins,
                      COUNT(DISTINCT CASE WHEN g.result = '1/2-1/2' THEN g.id END) AS draws,
                      COUNT(DISTINCT CASE WHEN g.result = '0-1' THEN g.id END) AS black_wins
               FROM positions p JOIN games g ON g.id = p.game_id
               WHERE p.key = ? AND p.next_move IS NOT NULL
               GROUP BY p.next_move ORDER BY games DESC""",
            (key,),
        ).fetchall()
        board, _, _, _ = parse_fen(fen)
        moves = []
        for row in rows:
            move = divmod(row["move"], 90)
            moves.append(
                {
                    "move": move_to_iccs(move),
                    "cn": move_to_chinese(board, move) if board[move[0]] else move_to_iccs(move),
                    "games": row["games"],
                    "red_wins": row["red_wins"],
                    "draws": row["draws"],
                    "black_wins": row["black_wins"],
                }
            )
        return {"fen": fen, "games": total, "indexed_plies": self.index_plies, "moves": moves}

    def openings(self) -> list[dict]:
        rows = (
            self.connect()
            .execute(
                """SELECT opening AS name, COUNT(*) AS games FROM games
               WHERE opening IS NOT NULL GROUP BY opening ORDER BY games DESC"""
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            conn.close()
        self._local = threading.local()
        self._memory_conn = None


def _contains(text: str) -> str:
    """LIKE 的「包含」模式；输入里的 % 和 _ 按字面匹配。"""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _content_hash(row: dict[str, object]) -> str:
    """判断重复用：起始局面、着法和对局信息都相同才算同一局。
    只比较着法不够——不同棋手下出一模一样的着法（尤其是早早议和的短局）并不少见。"""
    fields = ("initial_fen", "moves", "event", "round", "date", "red", "black")
    text = "|".join(str(row[name] or "") for name in fields)
    return hashlib.sha1(text.encode()).hexdigest()


def moves_hash(initial_fen: str, moves: list[str]) -> str:
    """对局内容（起始局面 + 着法）的指纹：复盘结果是否还对应当前的着法。"""
    return hashlib.sha1(f"{initial_fen}|{' '.join(moves)}".encode()).hexdigest()


def fen_key(fen: str) -> int:
    """局面的 Zobrist 哈希（只看棋子位置和走棋方）。"""
    board, turn, _, _ = parse_fen(fen)
    return compute_key(board, turn)


def _replay_keys(initial_fen: str, moves: list[str]) -> list[int]:
    pos = Position.from_fen(initial_fen, validate=False)
    keys = [pos.key]
    for text in moves:
        pos._push_unchecked(parse_iccs(text))
        keys.append(pos.key)
    return keys


def my_game_headers(*, mode: str, ai_level: int | None, user_side: str | None) -> dict[str, str]:
    """自己的对局在棋谱库中的标签。"""
    if mode == "vs_ai":
        ai = f"AI（{ai_level} 级）"
        red, black = ("我", ai) if user_side == "red" else (ai, "我")
        event = f"人机对战（难度 {ai_level}）"
    else:
        red, black, event = "我", "我", "自由对弈"
    return {"Event": event, "Date": date.today().isoformat(), "Red": red, "Black": black}
