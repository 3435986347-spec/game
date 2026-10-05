"""模板讲解：不调用大模型时使用（没配 Key、网络错误、大模型输出两次都不合格）。

只用上下文里算好的事实拼句子，着法也只取自上下文，所以不会出现编造的着法；只是没那么自然。
"""

from __future__ import annotations

from .base import Explanation, GameSummaryText
from .context import ExplainContext, SummaryContext

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


def _is_positive(fact: str) -> bool:
    """这步棋做成了什么：吃子、将军、捉子、杀棋威胁、解除威胁。"""
    return (
        "吃掉了" in fact
        or fact.endswith("将军")
        or fact.startswith("这步棋之后")
        or "就能将死对方" in fact
        or "不再处于危险之中" in fact
        or "躲开了" in fact
    )


def template_explanation(ctx: ExplainContext) -> Explanation:
    grade = ctx.grade
    best_differs = bool(ctx.best) and ctx.best[0][0] != ctx.move_cn
    swing = ""
    if ctx.win_before is not None and ctx.win_after is not None:
        swing = f"期望得分从{_pct(ctx.win_before)}降到{_pct(ctx.win_after)}"
    key = ctx.key_facts[0] if ctx.key_facts else None
    good = grade is None or grade not in (*_BAD, "缓着")
    # 讲好棋（或不评级，如讲正解）时先说这步做成了什么，不先说别处的问题
    positive = [f for f in ctx.facts if _is_positive(f)]
    ordered = [*positive, *ctx.key_facts] if good else [*ctx.key_facts, *positive]

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
        headline_fact = next(iter(ordered), None) or (ctx.facts[0] if ctx.facts else None)
        headline = f"{ctx.move_cn}：{headline_fact}。" if headline_fact else f"关于{ctx.move_cn}。"

    # 原因：其余的事实，不重复标题，也不重复「更好的下法」那一栏
    why_parts: list[str] = []
    for fact in [*ordered, *ctx.facts]:
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
        if ctx.win_after is not None and score - ctx.win_after <= 0.02:
            better = f"这步和引擎推荐的{name}差不多好（走完后都约{_pct(score)}）。"
        else:
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


def template_intent(ctx: ExplainContext) -> Explanation:
    """名局解读：按事实推断这步棋的意图（docs 5.5 节）。"""
    facts = [f for f in ctx.facts if not f.startswith("引擎认为")]
    threat = next((f for f in facts if f.startswith("如果")), None)
    target = next((f for f in facts if f.startswith("这步棋之后")), None)
    capture = next((f for f in facts if "吃掉了" in f), None)
    if "杀棋威胁" in ctx.tags:
        headline, used = f"{ctx.move_cn}：制造杀棋威胁，逼对方应付。", None
    elif threat:
        headline, used = f"{ctx.move_cn}：{threat}。", threat
    elif "捉双" in ctx.tags:
        headline, used = f"{ctx.move_cn}：一步棋同时攻击两个目标。", None
    elif target:
        headline, used = f"{ctx.move_cn}：{target}。", target
    elif capture:
        headline, used = f"{ctx.move_cn}：{capture}。", capture
    elif "解除威胁" in ctx.tags:
        headline, used = f"{ctx.move_cn}：先解除自己受到的威胁。", None
    else:
        headline, used = f"{ctx.move_cn}：调整子力位置，为后面的走法做准备。", None
    why_parts = [f for f in facts if f != used][:3]
    why = "；".join(why_parts) + "。" if why_parts else "局面比较平稳，这步没有直接的战术目的。"

    if ctx.best and ctx.best[0][0] != ctx.move_cn:
        name, score, pv = ctx.best[0]
        better = f"引擎更推荐{name}（走完后期望得分约{_pct(score)}）"
        if ctx.win_after is not None:
            better += f"，这步走完后约{_pct(ctx.win_after)}"
        better += "。"
    elif ctx.best:
        better = "引擎也认为这是最好的走法。"
        if len(ctx.best[0][2]) > 1:
            better += f"后续可能：{' '.join(ctx.best[0][2][:5])}。"
    else:
        better = "没有引擎分析。"
    principle = next(
        (text for name, text in _PRINCIPLES if name in ctx.tags),
        "看高手的着法时，先问「这步棋威胁什么」，再问「它防住了什么」。",
    )
    return Explanation(
        headline=headline, why=why, better=better, principle=principle, tags=list(ctx.tags)
    )


def template_summary(ctx: SummaryContext) -> GameSummaryText:
    """名局解读的分阶段总结：把各阶段的准确率和转折点串成句子。"""

    def section(phase: str, quiet: str) -> str:
        points = ctx.phase_points.get(phase, [])
        accuracy = ctx.accuracy.get(phase)
        text = f"准确率 {accuracy}。" if accuracy else ""
        if points:
            return text + "转折点：" + "；".join(points) + "。"
        return text + quiet

    opening_name = f"布局是{ctx.opening}。" if ctx.opening else ""
    all_points = [p for phase in ("开局", "中局", "残局") for p in ctx.phase_points.get(phase, [])]
    overall = ctx.result_text
    if all_points:
        overall += f"；最关键的一步：{all_points[0]}"
    reached_endgame = "残局" in ctx.accuracy
    return GameSummaryText(
        opening=opening_name + section("开局", "双方开局都比较稳健，没有明显失误。"),
        middlegame=section("中局", "中局双方没有出现大的失误。"),
        endgame=section(
            "残局", "残局阶段双方没有大的失误。" if reached_endgame else "棋局没有进入残局。"
        ),
        overall=overall + "。",
    )
