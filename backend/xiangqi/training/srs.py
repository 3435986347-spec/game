"""间隔复习（docs 8.2 节）：用简化的 SM-2 安排错题本里每张卡片的下次复习时间。

做对了间隔变长（1 天 → 3 天 → 每次乘以「容易度」），做错了 10 分钟后再出，容易度降低。
FSRS 更精细，但需要较多复习记录才能调准参数；个人自用先用 SM-2。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .store import now_text

MIN_EASE = 1.3
MAX_EASE = 3.0
RELEARN = timedelta(minutes=10)


def schedule(card: dict, correct: bool, now: datetime | None = None) -> dict:
    """按这次的结果算出卡片新的复习参数（可直接用于 TrainingStore.update_card）。"""
    now = now or datetime.now()
    reps, lapses = card["reps"], card["lapses"]
    interval, ease = card["interval_days"], card["ease"]
    if correct:
        reps += 1
        interval = 1.0 if reps == 1 else 3.0 if reps == 2 else round(max(interval, 1.0) * ease, 1)
        ease = min(MAX_EASE, ease + 0.1)
        due = now + timedelta(days=interval)
    else:
        reps, lapses = 0, lapses + 1
        interval = 0.0
        ease = max(MIN_EASE, ease - 0.2)
        due = now + RELEARN
    return {
        "reps": reps,
        "lapses": lapses,
        "interval_days": interval,
        "ease": round(ease, 2),
        "due_at": now_text(due),
        "last_reviewed": now_text(now),
    }
