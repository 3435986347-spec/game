"""测试用的假 UCI 引擎。

用本项目的规则引擎生成合法着法，按 ICCS 字母顺序排列并给出固定分数，行为完全可预测。
支持 go ... searchmoves（只在给定的着法里排序打分）。
参数：
  --flavor=fairy   模拟 Fairy-Stockfish：行号 1–10，且必须先设置 UCI_Variant=xiangqi
  --die-on-go      收到 go 时像缺少权重文件的 Pikafish 一样报错退出
  --slow-start     收到 uci 后过 2 秒才回应（模拟加载权重文件很慢）
"""

import queue
import re
import sys
import threading
import time

from xiangqi.core import START_FEN, Position, move_to_iccs, parse_iccs

ARGS = sys.argv[1:]
FAIRY = "--flavor=fairy" in ARGS
DIE_ON_GO = "--die-on-go" in ARGS
SLOW_START = "--slow-start" in ARGS
MOVE_RE = re.compile(r"^([a-i])(\d{1,2})([a-i])(\d{1,2})$")


def out(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def shift(move: str, delta: int) -> str:
    f1, r1, f2, r2 = MOVE_RE.match(move).groups()
    return f"{f1}{int(r1) + delta}{f2}{int(r2) + delta}"


def to_engine(move: str) -> str:
    return shift(move, 1) if FAIRY else move


def from_engine(move: str) -> str:
    return shift(move, -1) if FAIRY else move


class State:
    position = Position.start()
    multipv = 1
    show_wdl = False
    variant = "chess"


def emit(depth: int, only: set[str] | None = None) -> str | None:
    pos = State.position
    moves = sorted(move_to_iccs(m) for m in pos.legal_moves())
    if only:
        moves = [m for m in moves if m in only]
    moves = moves[: State.multipv]
    for k, move in enumerate(moves, 1):
        cp = 60 - 20 * k
        wdl = f" wdl {300 + cp} 500 {200 - cp}" if State.show_wdl else ""
        out(
            f"info depth {depth} seldepth {depth} multipv {k} score cp {cp}{wdl} "
            f"nodes {1000 * depth} nps 100000 time {depth} pv {to_engine(move)}"
        )
    return moves[0] if moves else None


def main() -> None:
    commands: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        for line in sys.stdin:
            commands.put(line.strip())
        commands.put("quit")

    threading.Thread(target=reader, daemon=True).start()

    while True:
        cmd = commands.get()
        if cmd == "uci":
            if SLOW_START:
                time.sleep(2)
            out("id name FakeEngine 1.0")
            out("id author tests")
            out("option name Threads type spin default 1 min 1 max 8")
            out("option name Hash type spin default 16 min 1 max 1024")
            out("option name MultiPV type spin default 1 min 1 max 128")
            out("option name UCI_ShowWDL type check default false")
            if FAIRY:
                out("option name UCI_Variant type combo default chess var chess var xiangqi")
            out("uciok")
        elif cmd == "isready":
            out("readyok")
        elif cmd.startswith("setoption name "):
            name, _, value = cmd[len("setoption name ") :].partition(" value ")
            if name == "MultiPV":
                State.multipv = int(value)
            elif name == "UCI_ShowWDL":
                State.show_wdl = value == "true"
            elif name == "UCI_Variant":
                State.variant = value
        elif cmd.startswith("position "):
            tokens = cmd.split()
            if tokens[1] == "startpos":
                fen, rest = START_FEN, tokens[2:]
            else:
                end = tokens.index("moves") if "moves" in tokens else len(tokens)
                fen, rest = " ".join(tokens[2:end]), tokens[end:]
            pos = Position.from_fen(fen, validate=False)
            for move in rest[1:]:
                pos.push(parse_iccs(from_engine(move)))
            State.position = pos
        elif cmd.startswith("go"):
            if DIE_ON_GO:
                out("info string ERROR: Network evaluation parameters must be available.")
                out(
                    "info string ERROR: The network file pikafish.nnue was not loaded successfully."
                )
                sys.exit(1)
            if FAIRY and State.variant != "xiangqi":
                out("info string variant not set")
                out("bestmove (none)")
                continue
            if "infinite" in cmd:
                depth = 1
                while True:
                    best = emit(depth)
                    depth += 1
                    try:
                        nxt = commands.get(timeout=0.02)
                    except queue.Empty:
                        continue
                    if nxt in ("stop", "quit"):
                        break
                out(f"bestmove {to_engine(best) if best else '(none)'}")
                if nxt == "quit":
                    return
            else:
                tokens = cmd.split()
                only = None
                if "searchmoves" in tokens:
                    only = {from_engine(t) for t in tokens[tokens.index("searchmoves") + 1 :]}
                best = None
                for depth in (1, 2, 3):
                    best = emit(depth, only)
                if best is None:
                    out("info depth 0 score mate 0")
                out(f"bestmove {to_engine(best) if best else '(none)'}")
        elif cmd == "quit":
            return


if __name__ == "__main__":
    main()
