"""象棋引擎接入：UCI 协议适配、难度控制、引擎服务。"""

from .difficulty import LEVELS, MAX_LEVEL, MIN_LEVEL, Level, choose_move, get_level
from .service import EngineConfig, EngineService, EngineUnavailable
from .uci import AnalysisResult, EngineError, InfoLine, Limit, UciEngine

__all__ = [
    "LEVELS",
    "MAX_LEVEL",
    "MIN_LEVEL",
    "AnalysisResult",
    "EngineConfig",
    "EngineError",
    "EngineService",
    "EngineUnavailable",
    "InfoLine",
    "Level",
    "Limit",
    "UciEngine",
    "choose_move",
    "get_level",
]
