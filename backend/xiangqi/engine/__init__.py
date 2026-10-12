"""象棋引擎接入：UCI 协议适配、难度控制、引擎服务、分析缓存。"""

from .cache import AnalysisCache
from .difficulty import (
    LEVELS,
    MAX_LEVEL,
    MIN_LEVEL,
    Level,
    PlayStyle,
    choose_move,
    get_level,
    search_multipv,
)
from .service import EngineConfig, EngineService, EngineUnavailable
from .uci import AnalysisResult, EngineError, InfoLine, Limit, UciEngine

__all__ = [
    "LEVELS",
    "MAX_LEVEL",
    "MIN_LEVEL",
    "AnalysisCache",
    "AnalysisResult",
    "EngineConfig",
    "EngineError",
    "EngineService",
    "EngineUnavailable",
    "InfoLine",
    "Level",
    "Limit",
    "PlayStyle",
    "UciEngine",
    "choose_move",
    "get_level",
    "search_multipv",
]
