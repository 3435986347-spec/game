"""UCI 协议适配器：和 Pikafish（或 Fairy-Stockfish）子进程通信。

对外统一使用本项目的坐标（ICCS，行号 0–9）和红方/走棋方视角的数据结构，引擎之间的差异在这里抹平：
- Pikafish：行号 0–9（h2e2），与本项目一致。
- Fairy-Stockfish：行号 1–10（h3e3、h10g8），并且需要先设置 UCI_Variant=xiangqi。

进程读写用「子进程 + 读线程 + asyncio 队列」实现，而不是 asyncio 子进程：
Windows 上某些事件循环（如 uvicorn 开启 reload 时用的 SelectorEventLoop）不支持 asyncio 子进程。
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

FLAVORS = ("pikafish", "fairy-stockfish")

_MOVE_RE = re.compile(r"^([a-i])(\d{1,2})([a-i])(\d{1,2})$")


class EngineError(RuntimeError):
    """引擎无法启动、崩溃、超时，或返回了无法理解的内容。"""


# ---------------------------------------------------------------------------
# 坐标转换
# ---------------------------------------------------------------------------


def to_engine_move(move: str, flavor: str) -> str:
    if flavor == "pikafish":
        return move
    f1, r1, f2, r2 = _split_move(move)
    return f"{f1}{r1 + 1}{f2}{r2 + 1}"


def from_engine_move(move: str, flavor: str) -> str:
    """引擎坐标 → 本项目坐标。行号超出 0–9 通常说明 flavor 设置错了。"""
    f1, r1, f2, r2 = _split_move(move)
    if flavor != "pikafish":
        r1, r2 = r1 - 1, r2 - 1
    if not (0 <= r1 <= 9 and 0 <= r2 <= 9):
        raise EngineError(
            f"引擎返回的着法 {move} 超出棋盘，"
            "请检查 config.toml 中 [engine] 的 flavor 是否与引擎一致"
        )
    return f"{f1}{r1}{f2}{r2}"


def _split_move(move: str) -> tuple[str, int, str, int]:
    m = _MOVE_RE.match(move)
    if not m:
        raise EngineError(f"无法识别引擎返回的着法：{move!r}")
    return m.group(1), int(m.group(2)), m.group(3), int(m.group(4))


# ---------------------------------------------------------------------------
# 分析结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Limit:
    """搜索限制，至少给一个。"""

    nodes: int | None = None
    depth: int | None = None
    movetime_ms: int | None = None

    def go_command(self) -> str:
        parts = ["go"]
        if self.nodes:
            parts += ["nodes", str(self.nodes)]
        if self.depth:
            parts += ["depth", str(self.depth)]
        if self.movetime_ms:
            parts += ["movetime", str(self.movetime_ms)]
        if len(parts) == 1:
            raise ValueError("Limit 至少要设置 nodes、depth、movetime_ms 中的一个")
        return " ".join(parts)

    def timeout(self) -> float:
        """等待 bestmove 的最长时间（秒）。超时后会发送 stop。"""
        if self.movetime_ms:
            return self.movetime_ms / 1000 + 15
        return 120.0


@dataclass(frozen=True)
class InfoLine:
    """一条候选着法的分析（引擎 info 行）。分数为「当前走棋方」视角。"""

    multipv: int
    depth: int
    pv: list[str]  # ICCS，行号 0–9
    score_cp: int | None = None
    mate: int | None = None  # 正数：走棋方 N 步内杀；负数：被杀；0：已被将死
    wdl: tuple[int, int, int] | None = None  # 胜/和/负，千分比
    nodes: int | None = None
    bound: str | None = None  # "lower" / "upper"，不是精确值时

    @property
    def move(self) -> str:
        return self.pv[0]

    def expected_score(self) -> float:
        """走棋方的期望得分 0..1（胜 1、和 0.5、负 0）。"""
        if self.wdl is not None:
            w, d, _ = self.wdl
            return (w + d / 2) / 1000
        if self.mate is not None:
            return 1.0 if self.mate > 0 else 0.0
        return cp_to_expected(self.score_cp or 0)


def cp_to_expected(cp: float, k: float = 200.0) -> float:
    """引擎不提供 WDL 时，用逻辑函数把分数近似成期望得分。k 为经验值，需按实际引擎标定。"""
    return 1.0 / (1.0 + math.exp(-cp / k))


@dataclass
class AnalysisResult:
    lines: list[InfoLine]  # 按 multipv 排序，第一条为最佳
    bestmove: str | None  # ICCS；无子可走时为 None
    engine_messages: list[str] = field(default_factory=list)  # 引擎输出的 info string


def parse_info(line: str, flavor: str) -> InfoLine | None:
    """解析 info 行。没有 pv 或分数的行（如 currmove、info string）返回 None。"""
    tokens = line.split()
    if not tokens or tokens[0] != "info" or "string" in tokens[1:2]:
        return None
    values: dict[str, object] = {}
    i = 1
    while i < len(tokens):
        key = tokens[i]
        if key == "pv":
            values["pv"] = [from_engine_move(t, flavor) for t in tokens[i + 1 :]]
            break
        if key == "score" and i + 2 < len(tokens):
            kind, number = tokens[i + 1], int(tokens[i + 2])
            values["mate" if kind == "mate" else "score_cp"] = number
            i += 3
            continue
        if key in ("lowerbound", "upperbound"):  # 可能紧跟分数，也可能在 wdl 之后
            values["bound"] = key[:5]
            i += 1
            continue
        if key == "wdl" and i + 3 < len(tokens):
            values["wdl"] = (int(tokens[i + 1]), int(tokens[i + 2]), int(tokens[i + 3]))
            i += 4
            continue
        if key in ("depth", "multipv", "nodes") and i + 1 < len(tokens):
            values[key] = int(tokens[i + 1])
            i += 2
            continue
        i += 1  # 其他字段（seldepth、nps、time、hashfull……）跳过键，值在下一轮被当作未知键跳过
    if (
        "pv" not in values
        or not values["pv"]
        or ("score_cp" not in values and "mate" not in values)
    ):
        return None
    return InfoLine(
        multipv=int(values.get("multipv", 1)),
        depth=int(values.get("depth", 0)),
        pv=values["pv"],  # type: ignore[arg-type]
        score_cp=values.get("score_cp"),  # type: ignore[arg-type]
        mate=values.get("mate"),  # type: ignore[arg-type]
        wdl=values.get("wdl"),  # type: ignore[arg-type]
        nodes=values.get("nodes"),  # type: ignore[arg-type]
        bound=values.get("bound"),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 引擎进程
# ---------------------------------------------------------------------------

_NETWORK_HINT = (
    "引擎没有加载到神经网络权重文件（pikafish.nnue）。"
    "请把 pikafish.nnue 放在引擎可执行文件的同一目录，"
    "或在 config.toml 的 [engine] 中设置 eval_file。"
)


class UciEngine:
    """一个 UCI 引擎子进程。同一时间只处理一个请求（内部加锁）。"""

    def __init__(
        self,
        command: Sequence[str],
        *,
        flavor: str = "pikafish",
        options: dict[str, str | int | bool] | None = None,
        cwd: str | Path | None = None,
        startup_timeout: float = 15.0,
    ) -> None:
        if flavor not in FLAVORS:
            raise ValueError(f"不支持的引擎类型：{flavor}（可选：{', '.join(FLAVORS)}）")
        self.command = list(command)
        if cwd is not None and os.path.dirname(self.command[0]):
            # 子进程会在 cwd 中启动，相对路径的可执行文件必须先变成绝对路径
            self.command[0] = os.path.abspath(self.command[0])
        self.flavor = flavor
        self.options = dict(options or {})
        self.cwd = str(cwd) if cwd else None
        self.startup_timeout = startup_timeout
        self.name: str | None = None
        self.supported_options: set[str] = set()
        self._proc: subprocess.Popen[str] | None = None
        self._queue: asyncio.Queue[str | None] | None = None
        self._lock = asyncio.Lock()
        self._messages: list[str] = []  # 最近的 info string，出错时用于说明原因
        self._multipv = 1
        self._eof = False  # 引擎输出已结束（进程正在或已经退出）

    # ---- 生命周期 ----

    @property
    def alive(self) -> bool:
        return self._proc is not None and not self._eof and self._proc.poll() is None

    async def start(self) -> None:
        async with self._lock:
            if self.alive:
                return
            loop = asyncio.get_running_loop()
            self._queue = asyncio.Queue()
            self._messages = []
            self._eof = False
            self.supported_options = set()
            try:
                self._proc = subprocess.Popen(
                    self.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=self.cwd,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as e:
                raise EngineError(f"无法启动引擎 {self.command[0]}：{e}") from e
            threading.Thread(
                target=_read_lines, args=(self._proc, self._queue, loop), daemon=True
            ).start()
            try:
                await self._handshake()
            except BaseException:  # 包括超时和任务被取消：不留下半启动的进程
                self.kill()
                raise

    async def _handshake(self) -> None:
        """uci → uciok → 设置选项 → isready。"""
        self._send("uci")
        while True:
            line = await self._readline(self.startup_timeout)
            if line.startswith("id name "):
                self.name = line[len("id name ") :].strip()
            elif line.startswith("option name "):
                self.supported_options.add(_option_name(line))
            elif line == "uciok":
                break

        options = dict(self.options)
        if self.flavor == "fairy-stockfish":
            options.setdefault("UCI_Variant", "xiangqi")
        options.setdefault("UCI_ShowWDL", True)
        for name, value in options.items():
            if name in self.supported_options:
                self._send(f"setoption name {name} value {_option_value(value)}")
        self._multipv = 1
        await self._ready(self.startup_timeout)

    def kill(self) -> None:
        """立即结束进程（不等待引擎响应）。"""
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    async def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is None:
            try:
                proc.stdin.write("quit\n")  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except OSError:
                pass
            try:
                await asyncio.to_thread(proc.wait, 3)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait)

    # ---- 分析 ----

    async def analyse(
        self,
        fen: str,
        moves: Sequence[str] = (),
        *,
        limit: Limit,
        multipv: int = 1,
    ) -> AnalysisResult:
        """在给定局面（初始 FEN + 之后的着法）上搜索，直到满足 limit。"""
        async with self._lock:
            self._ensure_alive()
            self._messages = []
            await self._prepare(fen, moves, multipv)
            self._send(limit.go_command())
            lines: dict[int, InfoLine] = {}
            latest: dict[int, InfoLine] = {}  # 每条候选最后一次输出（包括上下界）
            try:
                bestmove = await self._collect_until_bestmove(lines, limit.timeout(), latest)
            except TimeoutError:
                self._send("stop")
                bestmove = await self._collect_until_bestmove(lines, 10.0, latest)
            first = lines.get(1)
            if bestmove and first is not None and first.move != bestmove:
                # 搜索在窗口失败时被打断，引擎按最后（上下界）结果给出 bestmove：以它为准
                if 1 in latest and latest[1].move == bestmove:
                    lines[1] = latest[1]
            return AnalysisResult(
                lines=_snapshot(lines),
                bestmove=bestmove,
                engine_messages=list(self._messages),
            )

    async def analyse_infinite(
        self, fen: str, moves: Sequence[str] = (), *, multipv: int = 1
    ) -> AsyncIterator[list[InfoLine]]:
        """持续分析，每收到一条新的 info 就产出当前全部候选着法。

        调用方停止迭代（break 或任务被取消）时自动发送 stop 并等引擎停下。
        """
        async with self._lock:
            self._ensure_alive()
            self._messages = []
            await self._prepare(fen, moves, multipv)
            self._send("go infinite")
            lines: dict[int, InfoLine] = {}
            finished = False
            try:
                while True:
                    raw = await self._readline(None)
                    if raw.startswith("bestmove"):
                        finished = True
                        return
                    info = self._handle_line(raw)
                    if info is not None:
                        _merge(lines, info)
                        yield _snapshot(lines)
            finally:
                if not finished and self.alive:
                    self._send("stop")
                    try:
                        await self._collect_until_bestmove({}, 10.0)
                    except (EngineError, TimeoutError):
                        await self._kill()

    # ---- 内部 ----

    async def _prepare(self, fen: str, moves: Sequence[str], multipv: int) -> None:
        if multipv != self._multipv and "MultiPV" in self.supported_options:
            self._send(f"setoption name MultiPV value {multipv}")
            self._multipv = multipv
        position = f"position fen {fen}"
        if moves:
            position += " moves " + " ".join(to_engine_move(m, self.flavor) for m in moves)
        self._send(position)
        await self._ready(15.0)

    async def _ready(self, timeout: float) -> None:
        self._send("isready")
        while (await self._readline(timeout)) != "readyok":
            pass

    async def _collect_until_bestmove(
        self,
        lines: dict[int, InfoLine],
        timeout: float,
        latest: dict[int, InfoLine] | None = None,
    ) -> str | None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            raw = await self._readline(remaining)
            if raw.startswith("bestmove"):
                parts = raw.split()
                if len(parts) < 2 or parts[1] in ("(none)", "0000", "none"):
                    return None
                return from_engine_move(parts[1], self.flavor)
            info = self._handle_line(raw)
            if info is not None:
                _merge(lines, info)
                if latest is not None:
                    latest[info.multipv] = info

    def _handle_line(self, raw: str) -> InfoLine | None:
        return parse_info(raw, self.flavor) if raw.startswith("info ") else None

    async def _readline(self, timeout: float | None) -> str:
        """读一行输出；超时抛 TimeoutError，进程退出抛 EngineError。顺便记录 info string。"""
        assert self._queue is not None
        line = await asyncio.wait_for(self._queue.get(), timeout)
        if line is None:
            self._eof = True
            raise EngineError(self._exit_message())
        if line.startswith("info string "):
            self._messages.append(line[len("info string ") :])
            del self._messages[:-20]
        return line

    def _send(self, command: str) -> None:
        if not self.alive:
            raise EngineError(self._exit_message())
        try:
            self._proc.stdin.write(command + "\n")  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
        except OSError as e:
            raise EngineError(self._exit_message()) from e

    def _ensure_alive(self) -> None:
        if not self.alive:
            raise EngineError(self._exit_message() if self._proc else "引擎尚未启动")

    async def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            await asyncio.to_thread(self._proc.wait)

    def _exit_message(self) -> str:
        errors = [m for m in self._messages if "error" in m.lower()] or self._messages[-2:]
        message = "引擎进程已退出"
        if self._proc is not None and self._proc.poll() is not None:
            message += f"（退出码 {self._proc.returncode}）"
        if errors:
            message += "：" + " ".join(errors[:2])
        if any("network" in m.lower() or "nnue" in m.lower() for m in errors):
            message += " —— " + _NETWORK_HINT
        return message


def _merge(lines: dict[int, InfoLine], info: InfoLine) -> None:
    """记录最新的候选着法。

    搜索窗口失败时引擎会先输出上界 / 下界（lowerbound / upperbound），分数可能严重偏高或偏低，
    随后才是精确结果。所以带 bound 的行只在还没有任何结果时使用，不覆盖已有结果。
    """
    if info.bound is not None and info.multipv in lines:
        return
    lines[info.multipv] = info


def _snapshot(lines: dict[int, InfoLine]) -> list[InfoLine]:
    """按 multipv 排序的候选着法，去掉重复的着法。

    各条候选来自不同的搜索深度（引擎逐条更新），同一着法可能暂时出现在两条里，只保留排在前面的。
    """
    seen: set[str] = set()
    out = []
    for k in sorted(lines):
        if lines[k].move not in seen:
            seen.add(lines[k].move)
            out.append(lines[k])
    return out


def _read_lines(
    proc: subprocess.Popen[str], queue: asyncio.Queue[str | None], loop: asyncio.AbstractEventLoop
) -> None:
    """读线程：把引擎输出逐行放进队列，进程结束时放入 None。"""
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\r\n"))
    except (OSError, ValueError):
        pass
    try:
        loop.call_soon_threadsafe(queue.put_nowait, None)
    except RuntimeError:
        pass  # 事件循环已关闭


def _option_name(line: str) -> str:
    rest = line[len("option name ") :]
    return rest.split(" type ", 1)[0].strip()


def _option_value(value: str | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def engine_command(path: str | Path) -> list[str]:
    """引擎可执行文件路径 → 启动命令（绝对路径）。Windows 上省略 .exe 时自动补上。"""
    p = Path(path).expanduser()
    if sys.platform == "win32" and not p.suffix and p.with_suffix(".exe").exists():
        p = p.with_suffix(".exe")
    return [os.fspath(p.absolute())]


NETWORK_FILE = "pikafish.nnue"


def find_network(binary: str | Path, levels: int = 2) -> Path | None:
    """在引擎所在目录及往上 levels 层目录中查找 pikafish.nnue。

    发布包里权重文件通常在压缩包根目录，而可执行文件在 Windows/、MacOS/ 等子目录中。
    """
    directory = Path(binary).expanduser().absolute().parent
    for candidate_dir in [directory, *directory.parents][: levels + 1]:
        candidate = candidate_dir / NETWORK_FILE
        if candidate.is_file():
            return candidate
    return None
