"""读取 config.toml。

查找顺序：命令行 --config 指定的文件 → 环境变量 XIANGQI_CONFIG → 从当前目录向上查找 config.toml。
找不到时使用默认值。配置文件中的相对路径以配置文件所在目录为基准。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .core import RuleConfig

# backend/xiangqi/config.py → 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    static_dir: Path | None = REPO_ROOT / "frontend" / "dist"  # 前端构建产物
    rules: RuleConfig = field(default_factory=RuleConfig)


def find_config_file(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    if env := os.environ.get("XIANGQI_CONFIG"):
        return Path(env)
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / "config.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config(path: str | Path | None = None) -> AppConfig:
    file = find_config_file(path)
    if file is None:
        return AppConfig()
    data = tomllib.loads(file.read_text(encoding="utf-8"))
    base = file.resolve().parent
    server = data.get("server", {})
    rules = data.get("rules", {})
    static_dir = server.get("static_dir")
    defaults = AppConfig()
    return AppConfig(
        host=server.get("host", defaults.host),
        port=int(server.get("port", defaults.port)),
        static_dir=(base / static_dir) if static_dir else defaults.static_dir,
        rules=RuleConfig(
            move_limit=int(rules.get("move_limit", defaults.rules.move_limit)),
            repetition_count=int(rules.get("repetition_count", defaults.rules.repetition_count)),
        ),
    )
