"""读取 config.toml。

查找顺序：命令行 --config 指定的文件 → 环境变量 XIANGQI_CONFIG → 从当前目录向上查找 config.toml。
找不到时使用默认值。配置文件中的相对路径以配置文件所在目录为基准。

API Key 不写进配置文件：配置里只写环境变量名，Key 放在系统环境变量，或配置文件旁边的 .env 文件
（已在 .gitignore 中，不会提交）。.env 里的值不会覆盖已经设置的环境变量。
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .core import RuleConfig
from .engine import EngineConfig
from .llm import ClaudeSettings, LLMConfig, OpenAICompatSettings

# backend/xiangqi/config.py → 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY_DB = REPO_ROOT / "data" / "xiangqi.db"


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    static_dir: Path | None = REPO_ROOT / "frontend" / "dist"  # 前端构建产物
    rules: RuleConfig = field(default_factory=RuleConfig)
    engine: EngineConfig | None = None  # 未配置时人机对战、提示、分析不可用
    # 棋谱库（SQLite）。None 表示只在内存中（测试用）；load_config 默认用 data/xiangqi.db
    library_db: Path | None = None
    library_index_plies: int = 40  # 局面索引只记录每局前多少步（半回合）
    llm: LLMConfig = field(default_factory=LLMConfig)
    review_movetime_ms: int = 800  # 复盘时引擎分析每个局面的时间


_DOTENV_COMMENT = re.compile(r"\s+#.*$")


def load_dotenv(path: Path) -> None:
    """读取 .env（KEY=VALUE，每行一个；# 开头为注释，没加引号的值后面也可以写「 # 注释」）。
    已存在的环境变量不覆盖。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[0] in value[1:]:
            value = value[1 : value.index(value[0], 1)]
        else:
            value = _DOTENV_COMMENT.sub("", value)
        if key:
            os.environ.setdefault(key, value)


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
        load_dotenv(REPO_ROOT / ".env")
        return AppConfig(library_db=DEFAULT_LIBRARY_DB)
    data = tomllib.loads(file.read_text(encoding="utf-8"))
    base = file.resolve().parent
    load_dotenv(base / ".env")
    server = data.get("server", {})
    rules = data.get("rules", {})
    engine = data.get("engine", {})
    library = data.get("library", {})
    review = data.get("review", {})
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
        engine=_engine_config(engine, base),
        library_db=(base / library["db_path"]) if library.get("db_path") else DEFAULT_LIBRARY_DB,
        library_index_plies=int(library.get("index_plies", defaults.library_index_plies)),
        llm=_llm_config(data.get("llm", {})),
        review_movetime_ms=int(review.get("movetime_ms", defaults.review_movetime_ms)),
    )


def _llm_config(section: dict) -> LLMConfig:
    claude = section.get("claude", {})
    compat = section.get("openai_compat", {})
    c, o, d = ClaudeSettings(), OpenAICompatSettings(), LLMConfig()
    return LLMConfig(
        provider=section.get("provider", d.provider),
        level=section.get("level", d.level),
        timeout_s=float(section.get("timeout_s", d.timeout_s)),
        claude=ClaudeSettings(
            model=claude.get("model", c.model),
            api_key_env=claude.get("api_key_env", c.api_key_env),
            effort=claude.get("effort", c.effort),
            fallbacks=bool(claude.get("fallbacks", c.fallbacks)),
        ),
        openai_compat=OpenAICompatSettings(
            base_url=compat.get("base_url", o.base_url),
            api_key_env=compat.get("api_key_env", o.api_key_env),
            model=compat.get("model", o.model),
            json_mode=bool(compat.get("json_mode", o.json_mode)),
        ),
    )


def _engine_config(section: dict, base: Path) -> EngineConfig | None:
    if not section.get("path"):
        return None
    eval_file = section.get("eval_file")
    return EngineConfig(
        path=base / section["path"],
        args=tuple(str(a) for a in section.get("args", ())),
        flavor=section.get("flavor", "pikafish"),
        eval_file=(base / eval_file) if eval_file else None,
        threads=int(section.get("threads", 2)),
        hash_mb=int(section.get("hash_mb", 128)),
        hint_movetime_ms=int(section.get("hint_movetime_ms", 1000)),
    )
