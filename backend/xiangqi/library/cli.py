"""命令行导入棋谱：uv run xiangqi-import <文件> [<文件> ...]

大棋谱文件（如十万局的合集）用命令行导入比在网页上传更快，并且能看到进度。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .db import ImportReport, Library
from .parse import decode_bytes, iter_games


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xiangqi-import", description="把棋谱文件导入棋谱库")
    parser.add_argument("files", nargs="+", help="棋谱文件（PGN / 中文记谱文本，可含多局）")
    parser.add_argument("--db", help="棋谱库文件（默认用 config.toml 中 [library] 的 db_path）")
    parser.add_argument("--config", help="配置文件路径")
    args = parser.parse_args(argv)

    from ..config import DEFAULT_LIBRARY_DB, load_config  # 避免循环导入

    config = load_config(args.config)
    db = args.db or config.library_db or DEFAULT_LIBRARY_DB
    library = Library(db, index_plies=config.library_index_plies)
    print(f"棋谱库：{library.path}")
    total = ImportReport()
    for name in args.files:
        path = Path(name).expanduser()
        if not path.is_file():
            print(f"跳过 {path}：文件不存在")
            continue
        print(f"\n导入 {path.name} …")
        start = time.perf_counter()
        text = decode_bytes(path.read_bytes())

        def show(report: ImportReport, start: float = start) -> None:
            elapsed = time.perf_counter() - start
            print(
                f"\r  已处理 {report.games_seen} 局：导入 {report.imported}，"
                f"重复 {report.duplicates}，失败 {report.failed}（{elapsed:.0f} 秒）",
                end="",
                flush=True,
            )

        report = library.import_games(iter_games(text), source=path.name, progress=show)
        print()
        for err in report.errors[:20]:
            print(f"  第 {err['index']} 局 {err['title']}：{err['reason']}")
        if report.failed > 20:
            print(f"  ……共 {report.failed} 局失败")
        for attr in ("games_seen", "imported", "duplicates", "failed"):
            setattr(total, attr, getattr(total, attr) + getattr(report, attr))
    stats = library.stats()
    print(
        f"\n完成：导入 {total.imported} 局，重复 {total.duplicates} 局，失败 {total.failed} 局。"
        f"棋谱库现有 {stats['games']} 局。"
    )
    if total.games_seen == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
