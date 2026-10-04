"""引擎服务：按配置启动两个引擎实例。

- player：人机对战走棋、提示；
- analyser：实时分析（评估条），可以和 player 同时运行，互不阻塞；
- reviewer：整盘复盘的后台分析，复盘时照样可以下棋、看实时分析。
都在第一次使用时才启动；进程意外退出后，下次使用时自动重启。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .uci import FLAVORS, EngineError, UciEngine, engine_command, find_network


@dataclass(frozen=True)
class EngineConfig:
    path: Path
    args: tuple[str, ...] = ()  # 额外的命令行参数（一般不需要）
    flavor: str = "pikafish"
    eval_file: Path | None = None
    threads: int = 2
    hash_mb: int = 128
    hint_movetime_ms: int = 1000

    def __post_init__(self) -> None:
        if self.flavor not in FLAVORS:
            raise ValueError(f"[engine] flavor 应为 {' 或 '.join(FLAVORS)}，实际为 {self.flavor!r}")


class EngineUnavailable(RuntimeError):
    """没有配置引擎，或引擎无法使用。message 会直接显示给用户。"""


_NOT_CONFIGURED = (
    "未配置象棋引擎。请按 README「安装象棋引擎」一节下载 Pikafish，"
    "并在 config.toml 的 [engine] 中填写 path。"
)


class EngineService:
    def __init__(self, config: EngineConfig | None) -> None:
        self.config = config
        self._engines: dict[str, UciEngine] = {}
        self._start_lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return self.config is not None

    async def player(self) -> UciEngine:
        return await self._get("player")

    async def analyser(self) -> UciEngine:
        return await self._get("analyser")

    async def reviewer(self) -> UciEngine:
        return await self._get("reviewer")

    def running(self, role: str) -> UciEngine | None:
        """已经启动且还活着的引擎实例；没有时返回 None（不会去启动它）。"""
        engine = self._engines.get(role)
        return engine if engine is not None and engine.alive else None

    async def status(self) -> dict[str, object]:
        """检查引擎能否启动，供界面显示。"""
        if self.config is None:
            return {"configured": False, "ok": False, "error": _NOT_CONFIGURED}
        try:
            engine = await self.player()
        except EngineUnavailable as e:
            return {"configured": True, "ok": False, "error": str(e), "flavor": self.config.flavor}
        return {"configured": True, "ok": True, "name": engine.name, "flavor": self.config.flavor}

    async def close(self) -> None:
        engines, self._engines = list(self._engines.values()), {}
        await asyncio.gather(*(e.close() for e in engines), return_exceptions=True)

    async def _get(self, role: str) -> UciEngine:
        if self.config is None:
            raise EngineUnavailable(_NOT_CONFIGURED)
        async with self._start_lock:
            engine = self._engines.get(role)
            if engine is not None and engine.alive:
                return engine
            if engine is not None:
                await engine.close()
            engine = self._create()
            try:
                await engine.start()  # 失败或被取消时 start() 会结束进程
            except (EngineError, TimeoutError) as e:
                self.last_error = str(e) if str(e) else "引擎启动超时"
                raise EngineUnavailable(self.last_error) from e
            self._engines[role] = engine
            self.last_error = None
            return engine

    def _create(self) -> UciEngine:
        cfg = self.config
        assert cfg is not None
        if not cfg.path.exists() and not cfg.path.with_suffix(".exe").exists():
            raise EngineUnavailable(
                f"找不到引擎文件：{cfg.path}。请检查 config.toml 中 [engine] 的 path。"
            )
        options: dict[str, str | int | bool] = {"Threads": cfg.threads, "Hash": cfg.hash_mb}
        eval_file = cfg.eval_file
        if eval_file is None and cfg.flavor == "pikafish":
            eval_file = find_network(cfg.path)  # 权重文件常在上一级目录
        if eval_file is not None:
            options["EvalFile"] = str(Path(eval_file).absolute())
        return UciEngine(
            [*engine_command(cfg.path), *cfg.args],
            flavor=cfg.flavor,
            options=options,
            cwd=cfg.path.resolve().parent,  # 引擎默认在工作目录找 pikafish.nnue
        )
