"""检查象棋引擎能否在这台电脑上运行。

用法：
  uv run xiangqi-engine-check                 检查 config.toml 中配置的引擎
  uv run xiangqi-engine-check <目录>           在解压出来的目录里找出能运行的最快版本
  uv run xiangqi-engine-check <可执行文件>     检查指定的文件

Pikafish 的发布包里通常有针对不同 CPU 指令集的多个版本（如 avx2、bmi2、avx512），
新指令集更快，但老 CPU 运行不了。这里按从快到慢的顺序逐个试运行，第一个能正常搜索的就是推荐版本。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import time
from pathlib import Path

from ..core import START_FEN, Position, move_to_chinese, parse_iccs
from .uci import FLAVORS, EngineError, Limit, UciEngine, engine_command, find_network

# 指令集关键字，从快到慢
_X86_ORDER = [
    "vnni512",
    "avx512icl",
    "avx512",
    "avxvnni",
    "bmi2",
    "avx2",
    "sse41",
    "ssse3",
    "x86-64",
]
_ARM_ORDER = ["apple-silicon", "dotprod", "neon", "armv8"]
_OS_DIRS = {"win32": ("windows", "win"), "darwin": ("macos", "mac", "osx", "darwin")}


def candidate_rank(path: Path) -> int:
    name = path.name.lower()
    if "universal" in name or name in ("pikafish", "pikafish.exe"):
        return 0  # 通用版会自己选择指令集
    arm = platform.machine().lower() in ("arm64", "aarch64")
    order = _ARM_ORDER + _X86_ORDER if arm else _X86_ORDER + _ARM_ORDER
    for i, token in enumerate(order, start=1):
        if token in name:
            return i
    return len(order) + 1


def find_candidates(directory: Path) -> list[Path]:
    """目录（含 3 层子目录）中名字含 pikafish 或 stockfish 的可执行文件，按推荐顺序排列。"""
    windows = sys.platform == "win32"
    other_os = {d for plat, dirs in _OS_DIRS.items() if plat != sys.platform for d in dirs}
    if sys.platform not in ("win32", "darwin"):
        other_os = {d for dirs in _OS_DIRS.values() for d in dirs}
    found = []
    for path in directory.rglob("*"):
        if len(path.relative_to(directory).parts) > 4 or not path.is_file():
            continue
        name = path.name.lower()
        if "pikafish" not in name and "stockfish" not in name:
            continue
        if name.endswith((".nnue", ".txt", ".md", ".pdf", ".zip", ".7z")):
            continue
        if windows != name.endswith(".exe"):
            continue
        if any(part.lower() in other_os for part in path.relative_to(directory).parts[:-1]):
            continue
        found.append(path)
    return sorted(found, key=lambda p: (candidate_rank(p), str(p)))


async def probe(path: Path, flavor: str, eval_file: Path | None = None) -> str:
    """试运行引擎：握手并从初始局面搜索约 1 秒。成功返回说明文字，失败抛 EngineError。"""
    options: dict[str, str | int | bool] = {"Threads": 1, "Hash": 16}
    if eval_file is None and flavor == "pikafish":
        eval_file = find_network(path)  # 与 xiangqi 运行时的查找规则一致
    if eval_file is not None:
        options["EvalFile"] = str(Path(eval_file).absolute())
    path = Path(engine_command(path)[0])
    engine = UciEngine([str(path)], flavor=flavor, options=options, cwd=path.parent)
    try:
        await engine.start()
        start = time.perf_counter()
        result = await engine.analyse(START_FEN, limit=Limit(movetime_ms=1000))
        elapsed = time.perf_counter() - start
    finally:
        await engine.close()
    if not result.lines or result.bestmove is None:
        raise EngineError("引擎没有给出着法")
    best = result.lines[0]
    pos = Position.start()
    move = parse_iccs(result.bestmove)
    if not pos.is_legal(move):
        raise EngineError(
            f"引擎给出的着法 {result.bestmove} 不是象棋着法，引擎类型（flavor）是否选对了？"
        )
    cn = move_to_chinese(pos.board, move)
    speed = f"，约 {best.nodes / elapsed / 1000:.0f}k 节点/秒" if best.nodes else ""
    return f"{engine.name}：1 秒搜索到第 {best.depth} 层{speed}，推荐 {cn}"


# 进程因非法指令退出：POSIX 为 SIGILL（-4），Windows 为 STATUS_ILLEGAL_INSTRUCTION
_ILLEGAL_INSTRUCTION_CODES = ("退出码 -4）", "退出码 3221225501）", "退出码 -1073741795）")


def guess_flavor(path: Path) -> str:
    return "fairy-stockfish" if "stockfish" in path.name.lower() else "pikafish"


def explain_failure(path: Path, error: Exception) -> str:
    text = str(error)
    if any(code in text for code in _ILLEGAL_INSTRUCTION_CODES):
        return text + "\n    这台电脑的 CPU 不支持这个版本使用的指令集，换下一个版本即可。"
    if sys.platform == "darwin" and (
        "-9" in text or "Permission" in text or "Operation not permitted" in text
    ):
        text += (
            f"\n    macOS 可能拦截了从网上下载的程序，可以运行：\n"
            f"      xattr -d com.apple.quarantine '{path}'\n      chmod +x '{path}'"
        )
    elif isinstance(error, EngineError) and "无法启动" in text and not os.access(path, os.X_OK):
        text += f"\n    文件没有执行权限，可以运行：chmod +x '{path}'"
    return text


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="xiangqi-engine-check", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "target", nargs="?", help="引擎可执行文件或解压后的目录（默认用 config.toml 的配置）"
    )
    parser.add_argument("--flavor", choices=FLAVORS, help="引擎类型（默认 pikafish）")
    parser.add_argument(
        "--eval-file", help="NNUE 权重文件路径（默认在引擎所在目录找 pikafish.nnue）"
    )
    args = parser.parse_args(argv)

    from ..config import find_config_file, load_config  # 避免循环导入

    config = load_config()
    engine_cfg = config.engine
    eval_file = (
        Path(args.eval_file) if args.eval_file else (engine_cfg.eval_file if engine_cfg else None)
    )

    if args.target:
        target = Path(args.target).expanduser()
    elif engine_cfg is not None:
        target = engine_cfg.path
    else:
        sys.exit(
            "config.toml 中还没有配置 [engine] path。请指定引擎文件或解压后的目录，例如：\n"
            "  uv run xiangqi-engine-check ~/Downloads/Pikafish"
        )

    if target.is_dir():
        candidates = find_candidates(target)
        if not candidates:
            sys.exit(f"在 {target} 中没有找到 Pikafish 可执行文件。")
        print(f"找到 {len(candidates)} 个候选版本，按从快到慢逐个试运行：")
    elif target.exists() or target.with_suffix(".exe").exists():
        candidates = [target]
    else:
        sys.exit(f"找不到 {target}")

    for path in candidates:
        flavor = args.flavor or (engine_cfg.flavor if engine_cfg and not args.target else None)
        flavor = flavor or guess_flavor(path)
        print(f"  {path} … ", end="", flush=True)
        try:
            summary = asyncio.run(probe(path, flavor, eval_file))
        except (EngineError, TimeoutError, OSError) as e:
            print("不可用")
            print("    " + explain_failure(path, e))
            continue
        print("可以运行")
        print(f"    {summary}")
        explicit_eval = Path(args.eval_file).expanduser() if args.eval_file else None
        _print_config_hint(path, flavor, explicit_eval, find_config_file())
        return
    sys.exit("没有可以运行的版本。")


def toml_path(path: Path, config_file: Path | None) -> str:
    """配置文件中的路径写法：能相对 config.toml 就写相对路径；一律用正斜杠（Windows 也认），
    并按 TOML 字符串规则转义。"""
    resolved = path.expanduser().resolve()
    shown = resolved.as_posix()
    if config_file is not None:
        try:
            shown = resolved.relative_to(config_file.resolve().parent).as_posix()
        except ValueError:
            pass
    return json.dumps(shown, ensure_ascii=False)


def _print_config_hint(
    path: Path, flavor: str, eval_file: Path | None, config_file: Path | None
) -> None:
    print("\n在 config.toml 中这样配置：\n")
    print("[engine]")
    print(f"path = {toml_path(path, config_file)}")
    if flavor != "pikafish":
        print(f'flavor = "{flavor}"')
    if eval_file is not None:
        print(f"eval_file = {toml_path(eval_file, config_file)}")


if __name__ == "__main__":
    main()
