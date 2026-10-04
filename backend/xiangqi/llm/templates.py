"""模板讲解：不调用大模型时使用（没配 Key、网络错误、大模型输出两次都不合格）。

只用上下文里算好的事实拼句子，着法也只取自上下文，所以不会出现编造的着法；只是没那么自然。
"""

from __future__ import annotations

from .base import Explanation
from .context import ExplainContext

_BAD = ("漏着", "失误")

# 按标签给出可迁移的原则（越靠前越优先）
_PRINCIPLES = [
    ("漏看杀棋", "每走一步之前，先看看对方有没有将军和杀棋的手段。"),
    ("无根子", "走子前先看落点：对方能不能吃到？我有没有子保护它？"),
    ("贪吃", "吃子前先算对方能不能吃回来，不要为了小利丢掉大子。"),
    ("没有解除威胁", "自己的子被捉时，先想办法解围（躲开、保护或反击），再考虑别的。"),
    ("漏看对方攻击", "走子之前看一眼：这步走完，我的其他子会不会暴露在对方的攻击下？"),
    ("亏子", "交换之前数一数：双方各吃掉什么，算下来是赚是亏。"),
    ("杀棋威胁", "制造杀棋威胁能逼对方被动应付，是夺取主动的好办法。"),
    ("捉双", "一步棋同时攻击两个目标（捉双），对方往往顾此失彼。"),
    ("将军", "将军要有目的：能不能借此得子，或者把子走到更好的位置？"),
    ("开局", "开局要尽快出动车马炮，少走重复的着法，别过早用大子冒进。"),
    ("中局", "中局先找对方的弱点（无根子、空虚的九宫），再定计划。"),
    ("残局", "残局里帅（将）也能参加战斗，兵卒越往前越有价值。"),
]


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def template_explanation(ctx: ExplainContext) -> Explanation:
    grade = ctx.grade
    best_differs = bool(ctx.best) and ctx.best[0][0] != ctx.move_cn
    swing = ""
    if ctx.win_before is not None and ctx.win_after is not None:
        swing = f"期望得分从{_pct(ctx.win_before)}降到{_pct(ctx.win_after)}"
    key = ctx.key_facts[0] if ctx.key_facts else None

    # 标题：坏棋优先用最能说明问题的事实；没有这类事实时说损失了多少，不拿「吃了什么」凑数
    if grade in _BAD:
        headline_fact = key
        headline = f"{ctx.move_cn}是{grade}：{key or swing or '局面因此明显变差'}。"
    elif grade == "缓着":
        headline_fact = None
        headline = f"{ctx.move_cn}有点缓，错过了更好的机会。"
    elif grade:
        headline_fact = None
        headline = f"{ctx.move_cn}是{grade}。"
    else:
        headline_fact = key or (ctx.facts[0] if ctx.facts else None)
        headline = f"{ctx.move_cn}：{headline_fact}。" if headline_fact else f"关于{ctx.move_cn}。"

    # 原因：其余的事实，不重复标题，也不重复「更好的下法」那一栏
    why_parts: list[str] = []
    for fact in [*ctx.key_facts, *ctx.facts]:
        if fact != headline_fact and fact not in why_parts and not fact.startswith("引擎认为"):
            why_parts.append(fact)
    why_parts = why_parts[:2]
    if ctx.reply and not any(ctx.reply[0] in f for f in why_parts):
        effect = f"，{ctx.reply[1]}" if ctx.reply[1] else ""
        why_parts.append(f"这步之后对方最好的应着是{ctx.reply[0]}{effect}")
    if swing and swing not in headline:
        why_parts.append(f"走这步前期望得分约{_pct(ctx.win_before)}，走完后约{_pct(ctx.win_after)}")
    why = "；".join(why_parts) + "。" if why_parts else "引擎没有发现明显的问题。"

    if best_differs:
        name, score, pv = ctx.best[0]
        better = f"可以走{name}（走完后期望得分约{_pct(score)}）。"
        if len(pv) > 1:
            better += f"后续可能：{' '.join(pv[:5])}。"
    elif ctx.best:
        better = "这步就是引擎推荐的着法。"
    else:
        better = "没有引擎分析，无法给出更好的着法。"

    principle = next(
        (text for name, text in _PRINCIPLES if name in ctx.tags),
        "每一步都先看对方的威胁，再想自己的计划。",
    )
    return Explanation(
        headline=headline, why=why, better=better, principle=principle, tags=list(ctx.tags)
    )
