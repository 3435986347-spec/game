"""飞刀教学 API：列出实例，以及带每步局面和注解的主变/破解变。"""

from fastapi import APIRouter, HTTPException

from ..core.board import RED
from ..openings import TRAP_LINES
from .schemas import TrapDetailView, TrapMoveView, TrapSummaryView

router = APIRouter(prefix="/api/traps", tags=["飞刀开局"])


@router.get("", response_model=list[TrapSummaryView])
def list_traps() -> list[TrapSummaryView]:
    return [
        TrapSummaryView(
            id=line.trap.id,
            name=line.trap.name,
            side="red" if line.trap.side == RED else "black",
            intro=line.trap.intro,
            ply_count=len(line.moves),
            trap_ply=line.trap.trap_ply,
        )
        for line in TRAP_LINES.values()
    ]


@router.get("/{trap_id}", response_model=TrapDetailView)
def trap_detail(trap_id: str) -> TrapDetailView:
    line = TRAP_LINES.get(trap_id)
    if line is None:
        raise HTTPException(404, f"飞刀 {trap_id!r} 不存在")
    trap = line.trap
    return TrapDetailView(
        id=trap.id,
        name=trap.name,
        side="red" if trap.side == RED else "black",
        intro=trap.intro,
        principle=trap.principle,
        source=trap.source,
        trap_ply=trap.trap_ply,
        trap_fen=line.trap_fen,
        start_fen=line.start_fen,
        moves=[
            TrapMoveView(ply=s.ply, cn=s.cn, iccs=s.iccs, fen=s.fen, note=s.note)
            for s in line.steps
        ],
        refute=[
            TrapMoveView(ply=s.ply, cn=s.cn, iccs=s.iccs, fen=s.fen, note=s.note)
            for s in line.refute_steps
        ],
    )
