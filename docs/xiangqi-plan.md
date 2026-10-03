# 象棋自学 · AI 提示 · 人机对战 Web 应用 —— 技术方案

> 版本：v0.2（方案稿）
> 目标：一个在电脑浏览器里使用、以「自我提升」为核心的中国象棋应用：能和 AI 下棋，下棋时能要提示，下完能复盘并听懂自己错在哪；能导入棋谱打谱、猜大师的着法、让 AI 解读名局；平时能做题、练开局和残局。
>
> **v0.2 变更**：按已确认的需求改为「个人自用 + 电脑 + Python 后端」；新增第 4 节「LLM 适配层（OpenAI 兼容 / Claude）」和第 5 节「棋谱模块」；规则引擎改为 Python 实现，相关代码均已用原型验证。
>
> **实施进度（2026-10-03）**：M0、M1 已完成，代码在 `backend/`（规则引擎 + API）和 `frontend/`（自由对弈界面）。规则引擎的验证结果：
> - 初始局面 perft 1–4 与标准值一致。
> - 用东萍象棋网棋谱仓库的 99,813 局（共 8,114,143 步）逐步回放：
>   - 中文记谱零错误：每一步的记谱在当前局面的合法着法中唯一，并能解析回原着法。
>   - 32 局含有引擎判为不合法的着法。逐一分类后都是棋谱本身的错误：21 步是被将军时没有应将，11 步是走完后己方被将；没有一步是引擎漏生成的着法。
>   - 12 局的终局判定与棋谱记录的结果不一致，原因是棋谱结果记录有误，或长将判定与实际裁判有出入（简化规则的已知限制）。
>
> 这说明导入真实棋谱时必须逐步校验并跳过错误对局（见 5.2 节）。

---

## 0. 需求决策与核心结论

### 0.1 已确认的需求

| 问题 | 决定 | 对方案的影响 |
|---|---|---|
| 使用范围 | 个人自用 | 不需要账号系统；数据存在一个 SQLite 文件里；在本机运行，一条命令启动 |
| 设备 | 电脑 | 宽屏布局（左边棋盘、右边分析面板）、支持键盘快捷键；引擎直接用本机原生 Pikafish，**不再需要浏览器端 WASM 引擎** |
| 后端 | Python | FastAPI；规则引擎用纯 Python 写，是**唯一的规则来源**；前端只负责显示和交互 |
| 大模型 | DeepSeek 一类（OpenAI 兼容接口）或 Claude | 统一的 `LLMProvider` 接口，两种实现，改配置文件即可切换；**没配 Key 时也能用**（退回模板讲解） |
| 新增需求 | 棋谱 | 棋谱导入、打谱、猜着练习、名局 AI 解读、局面检索、从棋谱自动出题 |

### 0.2 核心结论

1. **不要让大模型直接下棋或算棋。** 大模型直接读棋盘时，常把子的位置看错、编出不合法的着法，也算不深。分工如下：

   | 角色 | 由谁担任 | 负责什么 |
   |---|---|---|
   | 裁判 | 自写的**规则引擎**（Python） | 着法是否合法、将军、胜负、记谱转换、棋谱解析、战术特征提取 |
   | 棋手 / 计算器 | 开源**象棋引擎** Pikafish | 算最佳着法、评估分数、给出主要变化 |
   | 教练（讲解员） | **大模型**（DeepSeek / Claude） | 把算好的结果「翻译」成人话，推断着法意图，总结原则 |

2. **「棋盘转成数据」不是一种格式，而是分层的几种表示**，每一层喂给不同的「模型」：

   ```
   内部数组 list[int]（长度 90）──► 规则引擎（合法性、特征、棋谱回放校验）
          │
          ├──► FEN + ICCS 坐标着法 ──► 象棋引擎（UCI 协议）
          │
          ├──► 张量 [14, 10, 9] + 2062 维策略 ──► 神经网络（可选，自训练时才需要）
          │
          └──► 结构化 JSON：文字棋盘 + 中文记谱 + 已算好的事实 ──► 大模型（只负责讲解）
   ```

3. **MVP 不需要自己训练模型**：规则引擎 + Pikafish + 大模型讲解，已经能做出完整的「对战 → 提示 → 复盘 → 棋谱训练」闭环。自训练神经网络放到最后作为可选项；到那时导入的棋谱库正好是训练数据。

---

## 1. 功能清单

| 模块 | 功能 | 优先级 |
|---|---|---|
| 规则引擎 | 着法生成、将军与胜负判定、中文记谱双向转换、FEN | P0（一切的基础） |
| 人机对战 | 执红 / 执黑；10 个难度级别；悔棋；认输 / 求和 | P0 |
| AI 提示 | 三级渐进提示（方向 → 该动哪个子 → 具体着法 + 原因） | P0 |
| 棋谱库 | 导入棋谱文件；按棋手、赛事、开局、局面搜索；打谱；试走变化 | P0 |
| 对局复盘 | 胜率曲线；每步评级；关键时刻；大模型讲解 | P1 |
| 猜着练习 | 跟着大师对局一步步猜下一着，引擎打分 | P1 |
| 名局 AI 解读 | 引擎整盘分析 + 大模型推断关键着法的意图、分阶段总结 | P1 |
| 局面统计 | 当前局面在棋谱库里出现过多少次，后续各着法的胜率（开局浏览器） | P1 |
| 题库 / 错题本 | 从棋谱和自己的对局自动出题；失误局面自动入错题本；间隔重复复习 | P1 |
| 开局 / 残局训练 | 开局跟练；指定残局和引擎对下 | P2 |
| 学习画像 | 个人棋力分；各主题正确率；弱项推荐 | P2 |
| 大师风格 AI | 用棋谱库训练「模仿人类着法」的神经网络 | P3（可选） |

---

## 2. 总体架构（本机运行）

```
┌──────────────── 浏览器（http://localhost:8000）─────────────────┐
│  React + TypeScript：SVG 棋盘、着法列表、胜率曲线、讲解面板        │
│  只负责显示和交互，所有规则判断都交给后端                          │
└─────────────┬─────────────────────────────────┬────────────────┘
              │ HTTP（走子、查询、导入）           │ WebSocket（引擎实时分析）
┌─────────────▼─────────────────────────────────▼────────────────┐
│  Python 后端（FastAPI + uvicorn）                                 │
│   xiangqi.core      规则引擎：合法着法、胜负、记谱、FEN、特征       │
│   xiangqi.engine    Pikafish 子进程 ×2（对局用 / 分析用，互不阻塞） │
│   xiangqi.llm       LLMProvider：OpenAI 兼容 | Claude | 模板兜底   │
│   xiangqi.games     棋谱解析、导入校验、局面索引、开局识别          │
│   xiangqi.training  复盘、猜着、题库、错题本                       │
│   SQLite：data/xiangqi.db                                         │
└──────────┬────────────────────────────────────┬─────────────────┘
           │ stdin/stdout（UCI 协议）              │ HTTPS
     Pikafish 可执行文件                    DeepSeek / Claude API
```

设计要点：
- **规则只在后端写一份。** 每次局面变化，后端返回的数据里直接带上当前全部合法着法，前端据此高亮可走位置、拦截非法拖动，不需要额外请求。本机通信延迟只有毫秒级，体验上和前端自己判断没有区别。
- **两个引擎实例**：一个给 AI 对手下棋用，一个给分析面板做持续分析，互不干扰。
- **一条命令启动**：`uv run xiangqi` 启动后端，后端同时提供构建好的前端页面并自动打开浏览器。

---

## 3. 核心难点：棋盘如何变成数据

### 3.1 第一步：统一坐标系（不先统一，后面一定乱）

```
        a   b   c   d   e   f   g   h   i        ← 列（file），红方视角从左到右
   9    車──馬──象──士──将──士──象──馬──車        ← 黑方底线
   8    │   │   │   │   │   │   │   │   │        ← 7–9 行 d–f 列为黑方九宫
   7    │   砲  │   │   │   │   │   砲  │
   6    卒──┼──卒──┼──卒──┼──卒──┼──卒
   5    ─────────── 楚 河    汉 界 ───────────
   4    │   │   │   │   │   │   │   │   │
   3    兵──┼──兵──┼──兵──┼──兵──┼──兵
   2    │   炮  │   │   │   │   │   炮  │
   1    │   │   │   │   │   │   │   │   │        ← 0–2 行 d–f 列为红方九宫
   0    车──马──相──仕──帅──仕──相──马──车        ← 红方底线
       九  八  七  六  五  四  三  二  一        ← 红方纵线编号（中文数字）
       1   2   3   4   5   6   7   8   9        ← 黑方纵线编号（阿拉伯数字，黑方从自己右手数起）
```

约定：
- 棋盘 10 行 × 9 列 = **90 个交叉点**。
- 列 `a`–`i`（红方从左到右），行 `0`–`9`（红方底线为 0）。
- 一维下标：`sq = rank * 9 + file`，范围 0–89。例如红方右车 `i0` = 8，黑将 `e9` = 85。
- 纵线编号：红方 `a..i` 对应 `九..一`，即 `9 - file`；黑方 `a..i` 对应 `1..9`，即 `file + 1`。两方都是**从自己的右手边数起**。

### 3.2 第 1 层：内部表示与规则引擎

#### 数据结构

```python
# xiangqi/core/board.py
from dataclasses import dataclass, field

KING, ADVISOR, BISHOP, KNIGHT, ROOK, CANNON, PAWN = range(1, 8)  # 帅仕相马车炮兵；红方为正，黑方为负

def sq(file: int, rank: int) -> int: return rank * 9 + file
def file_of(s: int) -> int: return s % 9
def rank_of(s: int) -> int: return s // 9
def on_board(f: int, r: int) -> bool: return 0 <= f < 9 and 0 <= r < 10

@dataclass
class Position:
    board: list[int]                 # 长度 90，0 为空
    turn: int = 1                    # 1 = 红走，-1 = 黑走
    halfmove_clock: int = 0          # 距上次吃子的半回合数（自然限着用）
    history: list[int] = field(default_factory=list)  # 历史局面的 Zobrist 哈希（重复局面、长将检测用）

Move = tuple[int, int]               # (起点, 终点)；对外和存库时统一用 ICCS 字符串，如 "h2e2"
```

为什么用简单的数组而不用位棋盘（bitboard）？
- 象棋 90 个点超出 64 位，位棋盘要 128 位；Pikafish 内部就是这么做的，因为它每秒要搜索上千万个局面。
- 我们的规则引擎**只负责正确性**，搜索交给 Pikafish。数组最直观、最好调试，配合下面的预计算表，速度完全够用。

#### Python 提速关键：预计算表

程序启动时把「每个格子上的马能跳到哪、马腿在哪」「每个格子四个方向的射线」算好，生成着法时直接查表，省掉大量边界判断：

```python
# 马：[落点 df, dr, 马腿 lf, lr]
KNIGHT_STEPS = [(1, 2, 0, 1), (-1, 2, 0, 1), (1, -2, 0, -1), (-1, -2, 0, -1),
                (2, 1, 1, 0), (2, -1, 1, 0), (-2, 1, -1, 0), (-2, -1, -1, 0)]

KNIGHT_MOVES = [[] for _ in range(90)]       # KNIGHT_MOVES[s] = [(落点, 马腿), ...]
for s in range(90):
    f, r = file_of(s), rank_of(s)
    for df, dr, lf, lr in KNIGHT_STEPS:
        if on_board(f + df, r + dr):
            KNIGHT_MOVES[s].append((sq(f + df, r + dr), sq(f + lf, r + lr)))

KNIGHT_ATTACKERS = [[] for _ in range(90)]   # 反查表：哪些格子上的马能跳到 s，以及那匹马的马腿
for n in range(90):
    for to, leg in KNIGHT_MOVES[n]:
        KNIGHT_ATTACKERS[to].append((n, leg))

RAYS = [[] for _ in range(90)]               # RAYS[s] = 四个方向上由近到远的格子列表（车、炮、将帅照面用）
for s in range(90):
    for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ray, f, r = [], file_of(s) + df, rank_of(s) + dr
        while on_board(f, r):
            ray.append(sq(f, r))
            f, r = f + df, r + dr
        RAYS[s].append(ray)
```

#### 着法规则（象棋特有的部分）

| 棋子 | 规则 | 实现要点 |
|---|---|---|
| 车 | 直线任意格，不能越子 | 沿 `RAYS` 走，遇子停止（对方子可吃） |
| 炮 | 不吃子时同车；**吃子必须隔一个子（炮架）** | 沿 `RAYS` 走，记录是否已越过炮架 |
| 马 | 走日字；**蹩马腿** | 查 `KNIGHT_MOVES`，马腿有子则不能走 |
| 相/象 | 走田字；**塞象眼**；**不能过河** | 田字中心有子则不能走；红相 rank ≤ 4，黑象 rank ≥ 5 |
| 仕/士 | 斜走一格，限九宫 | 九宫：file 3–5，红 rank 0–2，黑 rank 7–9 |
| 帅/将 | 直走一格，限九宫；**两将不能照面** | 照面检查合并进「是否被将军」 |
| 兵/卒 | 过河前只能前进；过河后可左右 | 红 rank ≥ 5 视为已过河，黑 rank ≤ 4 |

#### 将军检测：从帅的位置反向查（最容易出 bug 的地方）

最直接的写法是「生成对方所有着法，看有没有能吃到帅的」，但这很慢。更好的办法是从帅的位置**反向查找**：

```python
def in_check(b: list[int], side: int) -> bool:
    """side 方（1 红 / -1 黑）的帅/将是否正被将军。
    仕、相不能离开己方半场，永远将不到对方，所以只需要查车、炮、马、兵和将帅照面。"""
    k = b.index(KING * side)
    enemy = -side
    for ray in RAYS[k]:                                   # 车、炮，以及将帅照面
        screen = False
        for t in ray:
            p = b[t]
            if p == 0:
                continue
            if not screen:
                if p == enemy * ROOK or p == enemy * KING:  # 中间无子直接碰到对方将帅 = 照面
                    return True
                screen = True                                # 第一个子当作炮架
            else:
                if p == enemy * CANNON:
                    return True
                break
    for n, leg in KNIGHT_ATTACKERS[k]:                    # 马：马腿在「马」旁边，不在帅旁边！
        if b[n] == enemy * KNIGHT and b[leg] == 0:
            return True
    f, r = file_of(k), rank_of(k)
    if on_board(f, r + side) and b[sq(f, r + side)] == enemy * PAWN:   # 对方兵从正前方攻来
        return True
    for df in (-1, 1):                                    # 能走到我方九宫旁的兵一定已过河，可横向攻击
        if on_board(f + df, r) and b[sq(f + df, r)] == enemy * PAWN:
            return True
    return False

def legal_moves(pos: Position) -> list[Move]:
    b, side, out = pos.board, pos.turn, []
    for frm, to in pseudo_legal_moves(pos):               # 按上表生成的「伪合法」着法
        cap = b[to]; b[to] = b[frm]; b[frm] = 0           # 走一步
        if not in_check(b, side):                         # 走完后己方不能被将（含照面）
            out.append((frm, to))
        b[frm] = b[to]; b[to] = cap                       # 撤销
    return out
```

> **典型 bug**：反查马的攻击时，直接用帅旁边的格子当马腿。马腿永远是**马自己旁边**、沿长边方向的那一格，所以要用单独的反查表 `KNIGHT_ATTACKERS`。测试里一定要有「马腿被堵住，但帅旁边那一格是空的」这种局面。

#### 胜负与特殊规则

- **将死**：被将军且无合法着法 → 负。
- **困毙**：没被将军但无合法着法 → **也判负**（和国际象棋的「逼和」不同）。
- **重复局面**：用 Zobrist 哈希记录历史局面；同一局面出现 3 次进入判定。
- **长将**：一方连续将军造成重复 → 长将方判负。
- **长捉**：连续捉对方无根子造成重复 → 判负。这是规则引擎最复杂的部分（要判断「捉」「有根」「兑子」等），**MVP 先只实现「长将判负 + 其他重复判和」**，长捉以后再迭代。
- **自然限着**：连续若干回合（常用 60 回合）无吃子 → 判和，做成可配置项。

#### 用 perft 测试保证正确性

perft(n) = 从某局面出发走 n 步的所有合法着法序列数，这是检验着法生成器的标准方法。初始局面的标准值，以及按上面的写法用纯 CPython 单线程实测的耗时：

| 深度 | 节点数 | 实测耗时 |
|---|---|---|
| 1 | 44 | < 0.01 秒 |
| 2 | 1,920 | 0.01 秒 |
| 3 | 79,666 | 0.2 秒 |
| 4 | 3,290,240 | 约 10 秒 |

规则引擎每走一步只需生成一次着法（约 0.1 毫秒），即使批量导入上千盘棋谱也只要几十秒。

必备测试用例：
- 初始局面 perft 1–4；再加几个特殊局面：蹩马腿、塞象眼、炮架、将帅照面、过河兵。
- 将军检测：炮隔子将军、马腿被堵（不算将军）、将帅照面、过河兵横向将军。
- 记谱：见 3.6 节的对照表，包括「前 / 后」的情况。

### 3.3 第 2 层：引擎格式（给 Pikafish）

#### FEN：一行字符串描述一个局面

初始局面：

```
rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1
```

| 部分 | 含义 |
|---|---|
| `rnbakabnr/9/.../RNBAKABNR` | 从**黑方底线（rank 9）**写到**红方底线（rank 0）**，每行从 a 列到 i 列；`/` 分隔行 |
| 字母 | 大写红方、小写黑方：`K`帅 `A`仕 `B`相 `N`马 `R`车 `C`炮 `P`兵 |
| 数字 | 连续空位数量 |
| `w` / `b` | 轮到红方 / 黑方走 |
| `- -` | 象棋不用的占位字段（沿用国际象棋 FEN 格式） |
| `0 1` | 距上次吃子的半回合数、当前回合数 |

**兼容性坑**：有的软件用 `E`（Elephant）表示相、`H`（Horse）表示马，有的用 `r` 表示红方走。导入时统一转换成上面这一种。

#### 着法：ICCS 坐标

`起点列 起点行 终点列 终点行`，例如 `h2e2` = 炮二平五，`h9g7` = 马8进7。棋谱文件里常写成大写加横线（`H2-E2`），导入时统一转成小写、去掉横线。

（若以后换用 Fairy-Stockfish：它的行号是 1–10，同一步写成 `h3e3`，需要在适配层转换。）

#### UCI 协议交互

```
> uci
< id name Pikafish ...
< uciok
> setoption name Threads value 4
> setoption name Hash value 256
> setoption name MultiPV value 3            # 同时给出前 3 个候选着法
> isready
< readyok
> position fen rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1 moves h2e2 h9g7
> go movetime 1000                          # 或 go depth 18 / go nodes 200000
< info depth 18 multipv 1 score cp 30 ... pv h0g2 i9h9 i0h0 ...
< info depth 18 multipv 2 score cp 25 ... pv b0c2 ...
< bestmove h0g2
```

需要解析的字段：`depth`、`multipv`、`score cp X` / `score mate N`、`pv`、`bestmove`。注意：
- **分数是「当前走棋方」视角**。统一转换成**红方视角**再存储，否则胜率曲线会来回跳。
- `score mate 3` 表示 3 步内杀（负数表示被杀），要单独处理，不能当成普通分数。

#### Python 异步适配器

```python
# xiangqi/engine/uci.py
from collections.abc import AsyncIterator, Sequence

@dataclass
class AnalysisLine:
    move: str               # ICCS
    score_cp: int | None    # 红方视角
    mate_in: int | None     # 红方视角
    win_prob: float         # 红方胜率 0..1
    pv: list[str]           # 主要变化（ICCS）
    depth: int

class UciEngine:
    async def start(self, path: str, options: dict[str, str | int]) -> None: ...
    async def analyse(self, fen: str, moves: Sequence[str] = (), *, movetime_ms: int | None = None,
                      depth: int | None = None, nodes: int | None = None,
                      multipv: int = 1) -> list[AnalysisLine]: ...
    def analyse_stream(self, fen: str, moves: Sequence[str] = ()) -> AsyncIterator[AnalysisLine]: ...  # 推给 WebSocket
    async def stop(self) -> None: ...
```

实现要点：用 `asyncio.create_subprocess_exec` 启动引擎，逐行读取 stdout；每个引擎实例配一把 `asyncio.Lock`，保证同一时间只处理一个请求。注意 `python-chess` 库不支持中国象棋，UCI 解析需要自己写（不到 200 行）。

#### 分数 → 胜率

人对「胜率」的感知比「分数」直观，而且在 +800 分的局面里再丢 100 分几乎不影响结果，所以所有评级都基于胜率：

```python
import math

def win_prob(cp_red_view: float, k: float = 200.0) -> float:
    """优先使用引擎直接输出的胜/和/负概率（若支持 UCI_ShowWDL）；
    否则用逻辑函数近似，k 需要用棋谱库里的真实对局结果来标定。"""
    return 1.0 / (1.0 + math.exp(-cp_red_view / k))
```

### 3.4 第 3 层：神经网络张量（可选：自训练模型时才需要）

**MVP 不需要这一层。** 只有在以下情况才需要：
- **大师风格 / 人类风格的 AI**：用棋谱库训练策略网络去预测「大师（或某个水平的人）在这里会怎么走」。这样的对手比「引擎故意随机走错」自然得多，还能告诉你「同水平的人在这里最常犯什么错」。
- 研究、学习 AlphaZero 式训练。

不建议自己训练「最强引擎」：Pikafish 已经远超人类顶尖水平。

#### 输入：把局面变成 `[C, 10, 9]` 的张量

核心思想（AlphaZero 风格）：**每种棋子一张 10×9 的 0/1 平面**。

```
平面 0–6  ：当前走棋方的 帅 仕 相 马 车 炮 兵（有子为 1，否则 0）
平面 7–13 ：对方的       帅 仕 相 马 车 炮 兵
（可选）平面 14+ ：前 N 步的历史局面、重复次数、无吃子步数（归一化成常数平面）
```

关键技巧是**视角归一化**：总是让「轮到走棋的一方」在棋盘下方。黑方走时把棋盘上下翻转、红黑互换。这样网络只需学一种视角，数据利用率翻倍。象棋规则在「上下翻转 + 换色」下完全对称，这样做不改变规则。

```python
import numpy as np

PIECES = "KABNRCP"  # 帅 仕 相 马 车 炮 兵

def encode(fen: str) -> np.ndarray:
    """返回 [14, 10, 9]：0-6 是走棋方的 7 种子，7-13 是对方的。走棋方总在下方（行 0）。"""
    board, side = fen.split()[:2]
    planes = np.zeros((14, 10, 9), dtype=np.float32)
    for i, row in enumerate(board.split("/")):      # 第 0 段是 rank 9（黑方底线）
        rank, file = 9 - i, 0
        for ch in row:
            if ch.isdigit():
                file += int(ch)
                continue
            mine = ch.isupper() == (side == "w")
            r = rank if side == "w" else 9 - rank      # 黑走时上下翻转
            planes[PIECES.index(ch.upper()) + (0 if mine else 7), r, file] = 1.0
            file += 1
    return planes
```

（已验证：初始局面红走和黑走编码出的张量完全相同，说明视角归一化正确。）

#### 输出：策略头 + 价值头

- **价值头**：一个标量 ∈ [-1, 1]（走棋方的期望得分），或胜/和/负三分类。
- **策略头**：给每一个「几何上可能出现的着法」编一个号，网络输出每个编号的概率。

象棋的全部可能着法（从 A 点到 B 点）枚举如下：

| 类别 | 数量 | 说明 |
|---|---|---|
| 直线（同行或同列任意两点） | 1530 | 90 × (8 + 9)，车、炮、帅、兵的走法都是它的子集 |
| 马（日字） | 508 | 不考虑蹩腿的所有日字跳 |
| 相/象（田字，限合法象位） | 32 | 每方 16 |
| 仕/士（九宫斜线） | 16 | 每方 8 |
| **合计（不做视角翻转）** | **2086** | |
| **合计（做视角翻转，只需己方的相、仕）** | **2062** | 1530 + 508 + 16 + 8 |

这些数字已用脚本枚举核对过。也可以用更简单的 `90 × 90 = 8100` 维输出，但大部分位置永远用不到，训练效率低。

使用时：对当前局面的**非法着法做 mask**（把 logits 设为 -∞）再 softmax。做了视角翻转的话，着法编号也要按同样方式翻转。

#### 训练与部署要点

- **数据增强**：棋盘左右对称，左右镜像后数据量 ×2。
- **数据来源**：第 5 节的棋谱库（大师对局）、自己的对局、Pikafish 自对弈。
- **模型规模**：6–10 个残差块 × 64–128 通道的小网络，PyTorch 训练，导出 ONNX，后端用 `onnxruntime` 推理即可（电脑 CPU 足够）。
- **了解即可**：Pikafish 用的是 **NNUE**，输入是「己方帅的位置 × 棋子种类 × 棋子位置」这类稀疏特征。走一步只需增量更新少数特征，所以在 CPU 上极快。我们不需要自己实现它。

### 3.5 第 4 层：给大模型的讲解上下文

#### 原则：大模型只「讲」，不「算」

所有事实，包括合法着法、最佳着法、分数、对方的反击手段、哪个子没保护，**都先由引擎和规则引擎算好**，以结构化数据交给大模型。大模型只做三件事：挑出最重要的事实，用你能听懂的话讲清楚，总结出以后用得上的原则。这一点对 DeepSeek 这类较便宜的模型尤其重要：它们算棋的能力不比贵的模型强，但「看着事实讲道理」完全够用。

#### 输入由 5 部分组成

1. **文字棋盘**：用中文字符画出的棋盘，红黑用不同的字区分，帮助模型建立空间感。
2. **棋子清单**：每个子的位置，避免模型自己数格子数错。
3. **引擎分析**：实际走的着法、前 3 名候选着法、各自胜率、主要变化（全部带中文记谱）。
4. **规则引擎提取的战术事实**（这是防止胡说的关键）：
   - 是否将军；哪些子被攻击；哪些子**无根**（被攻击且无保护）；
   - **这步之后对方的最佳应着是什么、吃掉了什么**（直接取自引擎主变化）；
   - **这步制造的威胁**（见 5.5 节「空着法」技巧）；
   - 子力对比、局面阶段（开局 / 中局 / 残局）。
5. **你的水平**：决定讲解深浅和用词。

#### 完整示例

局面：1. 炮二平五 马8进7，红方第 2 步走了 **炮五进四**（用炮吃中卒），这一步被黑马直接吃回，属于漏着。
（以下局面、着法、攻防关系均已用原型脚本核对；胜率数值仅为示意。）

```json
{
  "player": { "side": "红方", "level": "入门" },
  "phase": "开局",
  "fen": "rnbakab1r/9/1c4nc1/p1p1p1p1p/9/9/P1P1P1P1P/1C2C4/9/RNBAKABNR w - - 2 2",
  "board_text": [
    "  1 2 3 4 5 6 7 8 9   （黑方）",
    "9 車馬象士将士象．車",
    "8 ．．．．．．．．．",
    "7 ．砲．．．．馬砲．",
    "6 卒．卒．卒．卒．卒",
    "5 ．．．．．．．．．",
    "4 ．．．．．．．．．",
    "3 兵．兵．兵．兵．兵",
    "2 ．炮．．炮．．．．",
    "1 ．．．．．．．．．",
    "0 车马相仕帅仕相马车",
    "  九八七六五四三二一   （红方）",
    "图例：红方 帅仕相马车炮兵；黑方 将士象馬車砲卒"
  ],
  "move_played": {
    "cn": "炮五进四", "iccs": "e2e6",
    "win_prob_before": 0.54, "win_prob_after": 0.16, "grade": "漏着"
  },
  "engine_best": [
    { "cn": "马二进三", "iccs": "h0g2", "win_prob": 0.54, "pv_cn": ["马二进三", "车9平8", "车一平二"] },
    { "cn": "马八进七", "iccs": "b0c2", "win_prob": 0.53 },
    { "cn": "兵三进一", "iccs": "g3g4", "win_prob": 0.52 }
  ],
  "opponent_reply": { "cn": "马7进5", "iccs": "g7e6", "effect": "黑马吃掉红炮" },
  "facts": [
    "炮五进四吃掉了黑方中卒（e6）",
    "e6 处于黑方 马(g7) 的攻击范围内，黑马跳过去不蹩腿",
    "红方没有任何棋子能保护 e6：这是一枚无根子",
    "结果：红方用 1 个炮换了 1 个卒，子力净亏"
  ],
  "allowed_moves_cn": ["炮五进四", "马二进三", "马八进七", "兵三进一", "马7进5", "车9平8", "车一平二"]
}
```

#### Prompt 与输出格式

System Prompt（固定不变，便于各家服务的提示词缓存）：

```
你是一位耐心的中国象棋教练，学生的水平在输入的 player.level 中给出。
你会收到一个局面和已经由象棋引擎、规则引擎计算好的事实。
规则：
1. 只能使用输入中给出的事实和着法，不要自己推算变化，不要提到 allowed_moves_cn 之外的任何着法。
2. 先给一句话结论，再解释原因，最后给出一条以后能用上的原则。
3. 用学生能听懂的话，少用术语；必须用术语时顺便解释。
4. 讲解不超过 150 字。
5. 以 JSON 格式输出，字段为 headline、why、better、principle、tags。
```

输出用 pydantic 定义，两种大模型都按这个结构返回：

```python
from pydantic import BaseModel

class Explanation(BaseModel):
    headline: str          # 一句话结论，如「这步炮吃中卒是送子：黑马可以直接把炮吃掉。」
    why: str               # 原因
    better: str            # 更好的下法及理由
    principle: str         # 可迁移的原则，如「吃子前先看落点：对方能不能吃回来？我有没有子保护它？」
    tags: list[str]        # 如 ["无根子", "贪吃", "开局"]，用于错题本归类和推荐练习
```

### 3.6 第 5 层：中文记谱（显示、棋谱导入、大模型共用）

规则：
- 格式：`棋子 + 起点纵线 + 动作 + 数字`，如「炮二平五」「马8进7」。红方用中文数字，黑方用阿拉伯数字。
- 动作：同一行移动 = **平**；向对方方向 = **进**；向己方方向 = **退**。
- 数字：「平」后面是目标纵线；直走子（车、炮、兵、帅）的「进 / 退」后面是**走了几步**；斜走子（马、相、仕）的「进 / 退」后面是**目标纵线**。
- 同一纵线上有两个同种同色子：用「前 / 后」代替纵线号，如「前车进二」（前 = 更靠近对方）。兵卒在同一纵线上有三个及以上时用「前、中、后」或「一、二、三……」。

```python
# xiangqi/core/notation.py
RED_NUM = "零一二三四五六七八九"
RED_NAME = " 帅仕相马车炮兵"      # 下标 = 棋子类型
BLACK_NAME = " 将士象马车炮卒"

def file_no(f: int, red: bool) -> int:
    return 9 - f if red else f + 1

def to_chinese(b: list[int], frm: int, to: int) -> str:
    p = b[frm]
    red, t = p > 0, abs(p)
    num = (lambda n: RED_NUM[n]) if red else str
    ff, fr, tf, tr = file_of(frm), rank_of(frm), file_of(to), rank_of(to)
    if fr == tr:
        action, target = "平", file_no(tf, red)
    else:
        action = "进" if (tr > fr) == red else "退"
        target = file_no(tf, red) if t in (ADVISOR, BISHOP, KNIGHT) else abs(tr - fr)
    name = (RED_NAME if red else BLACK_NAME)[t]
    same = [s for s in range(ff, 90, 9) if b[s] == p]        # 同一纵线上的同种同色子
    if len(same) == 2:                                       # 前 / 后（兵卒 ≥3 个的情况另行处理）
        front = same[-1] if red else same[0]
        head = ("前" if frm == front else "后") + name
    else:
        head = name + num(file_no(ff, red))
    return head + action + num(target)
```

单元测试用例（均已用原型运行通过）：

| 局面 | ICCS | 中文 |
|---|---|---|
| 初始局面 | `h2e2` | 炮二平五 |
| 初始局面 | `h9g7` | 马8进7 |
| 初始局面 | `b0c2` | 马八进七 |
| 初始局面 | `i0i1` | 车一进一 |
| 初始局面 | `h0g2` | 马二进三 |
| 初始局面 | `i9h9` | 车9平8 |
| 初始局面 | `g3g4` | 兵三进一 |
| 红车在 i0、i5（同一纵线） | `i5i7` / `i0h0` | 前车进二 / 后车平二 |
| 黑马在 e6、e7（同一纵线） | `e6d4` / `e7c8` | 前马进4 / 后马退3 |

#### 中文 → ICCS（棋谱导入的关键）

不必单独写解析器：**在当前局面生成全部合法着法，逐个转成中文，和棋谱里的那一步比较**，匹配上的就是它。这样所有特殊情况（前后、蹩腿、多兵）自动正确，而且棋谱里的不合法着法会被立刻发现。

比较前先做**规范化**，因为不同来源的写法五花八门：

| 原文写法 | 统一为 |
|---|---|
| 繁体：車 馬 砲 傌 俥 帥 將 進 | 车 马 炮 马 车 帅 将 进 |
| 全角数字：１２３ | 123 |
| 红黑同字：红方写「象、士、卒」，黑方写「相、仕、兵」 | 按走棋方统一 |
| 黑方用中文数字 / 红方用阿拉伯数字 | 按走棋方统一 |
| 英文 WXF 记法：`C2.5`、`H8+7`（`.` 平、`+` 进、`-` 退） | 可选支持，转成中文后同样匹配 |

### 3.7 第 6 层：存储格式

| 用途 | 格式 |
|---|---|
| 对局 / 棋谱 | `initial_fen` + ICCS 着法列表 + 元数据（棋手、赛事、日期、结果），存 SQLite |
| 导出 | PGN 风格文本（`[Format "ICCS"]`），或中文记谱文本 |
| 局面索引 | Zobrist 64 位哈希：局面检索、错题本去重、讲解缓存的键 |

### 3.8 各层表示一览

| 用途 | 格式 | 示例 |
|---|---|---|
| 规则计算 | `list[int]`（长度 90）+ 走棋方 + 历史哈希 | `board[85] = -1`（黑将在 e9） |
| 引擎通信 | FEN + ICCS | `position fen ... moves h2e2 h9g7` |
| 神经网络 | `float32[14, 10, 9]` + 2062 维策略 | 见 3.4 |
| 大模型 | JSON：文字棋盘 + 事实 + 候选着法（中文） | 见 3.5 |
| 界面显示 | 中文记谱 + 箭头 / 高亮 | 炮二平五 |
| 存储 | 初始 FEN + ICCS 着法列表 | `["h2e2", "h9g7"]` |

---

## 4. 大模型适配层（OpenAI 兼容 / Claude）

### 4.1 统一接口

业务代码只认这一个接口，不关心背后是哪家：

```python
# xiangqi/llm/base.py
from typing import Protocol, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class LLMProvider(Protocol):
    name: str
    async def complete_json(self, system: str, user: str, schema: type[T]) -> T: ...
```

### 4.2 OpenAI 兼容实现（DeepSeek 以及其他兼容服务）

DeepSeek 的接口与 OpenAI 兼容，直接用官方 `openai` SDK，改 `base_url` 即可。同一个实现也能接其他兼容 OpenAI 接口的服务（包括本地部署的模型）。

```python
# xiangqi/llm/openai_compat.py
from openai import AsyncOpenAI

class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str, json_mode: bool = True):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model, self.json_mode = model, json_mode

    async def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        extra = {"response_format": {"type": "json_object"}} if self.json_mode else {}
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            **extra,
        )
        text = resp.choices[0].message.content or ""
        return schema.model_validate_json(extract_json(text))   # 去掉 ```json 包裹，取第一个 {...}
```

注意：
- JSON 模式（`response_format`）通常要求提示词里出现「JSON」字样并给出字段示例，3.5 节的 system prompt 已包含。
- 部分推理类模型不支持 JSON 模式，在配置里设 `json_mode = false`，靠 `extract_json` + pydantic 校验兜底。
- 模型名、价格、上下文长度以服务商文档为准，一律写在配置文件里，不写死在代码中。

### 4.3 Claude 实现

```python
# xiangqi/llm/claude.py
import anthropic

class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

    async def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        resp = await self.client.messages.parse(
            model=self.model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,        # 结构化输出：返回的 JSON 保证符合 pydantic 模型
        )
        if resp.parsed_output is None:
            raise LLMFormatError(resp.stop_reason)
        return resp.parsed_output
```

### 4.4 配置文件

```toml
# config.toml（API Key 只写环境变量名，Key 本身放在 .env 或系统环境变量里，不进 git）
[llm]
provider = "openai_compat"          # "openai_compat" | "claude" | "none"（只用模板讲解）

[llm.openai_compat]
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
model = "deepseek-xxx"              # 按 DeepSeek 文档填写实际模型名
json_mode = true

[llm.claude]
api_key_env = "ANTHROPIC_API_KEY"
model = "claude-opus-5-5"

[engine]
path = "engines/pikafish"           # Windows 下为 pikafish.exe
threads = 4
hash_mb = 256
```

### 4.5 讲解流水线（与具体模型无关）

```
构建上下文（3.5 节 JSON）
   → 查缓存（键 = 局面哈希 + 着法 + 水平 + 模型名），命中直接返回
   → provider.complete_json(...)
   → pydantic 校验结构
   → 着法白名单校验：用正则找出文本里的所有中文着法，必须都在 allowed_moves_cn 中
   → 不通过：把错误原因告诉模型，重试 1 次
   → 仍不通过 / 网络错误 / 没配 Key：使用模板讲解
   → 写入缓存
```

中文着法正则：

```python
CN_MOVE = re.compile(
    r"(?:[前中后][车马炮兵卒相象仕士]|[车马炮相象仕士帅将兵卒][一二三四五六七八九1-9])"
    r"[进退平][一二三四五六七八九1-9]"
)
```

模板讲解示例（不调用大模型时使用）：「这步之后对方可以 {opponent_reply.cn}，{facts[0]}。更好的是 {engine_best[0].cn}。」这样即使完全离线，提示和复盘功能也能用，只是讲解没那么自然。

### 4.6 换模型前先评测

固定 30 个有代表性的局面（开局漏着、中局战术、残局技巧各 10 个）作为评测集。每换一个模型或改一次提示词，就跑一遍并记录：
- **编造着法次数**：自动统计，必须为 0；
- **讲解是否正确、是否易懂**：自己人工打 1–5 分；
- 平均耗时和费用。

### 4.7 控制费用

- 只对**关键时刻**（胜率变化超过阈值的步）调用大模型，普通步只显示评级标签。
- 每次讲解的输入约 1–2k token、输出约 300 token。
- 名局解读、复盘这类不着急的任务放到后台批量处理，结果存库，同一盘棋只解读一次。
- 讲解按局面缓存：同一个错误重复犯，不会重复花钱。

---

## 5. 棋谱模块

### 5.1 支持的格式

| 格式 | 说明 | 计划 |
|---|---|---|
| PGN（ICCS 坐标） | `[Format "ICCS"]`，着法如 `1. H2-E2 H9-G7` | M3 |
| PGN / 纯文本（中文记谱） | `1. 炮二平五 马8进7`，网上最常见 | M3 |
| FEN 局面 | 题目、残局、某个局面 | M3 |
| DhtmlXQ（东萍象棋网的网页棋谱格式） | 文本格式，着法为数字坐标串 | M6（实现时对照真实样例确认坐标方向） |
| XQF（象棋演播室） | 二进制格式，有多个版本，部分版本带简单加密 | M6 |

### 5.2 导入流水线

```
读取文件 → 识别格式 → 解析出元数据 + 初始局面 + 着法序列
   → 用规则引擎逐步回放校验（任一步不合法：记录错误，截断或跳过该局）
   → 转成 ICCS 存库（按「初始局面 + 着法序列」的哈希去重）
   → 为每一步建立局面索引（Zobrist 哈希 → 对局 ID + 步数）
   → 识别开局名称（比对开局局面表）
   → （可选）加入后台队列，用引擎分析整盘
   → 输出导入报告：成功 N 局，失败 M 局及原因
```

### 5.3 棋谱库：浏览、打谱、局面统计

- **搜索**：按棋手、赛事、年份、结果、开局名称；也可以**按局面搜索**，例如「当前局面在库里出现过的所有对局」。
- **打谱**：前进 / 后退 / 跳转（键盘 ← → 键），旁边有引擎评估条和胜率曲线；可以在任意一步**试走**自己的着法，生成分支变化，不改动原谱。
- **局面统计（开局浏览器）**：当前局面下，库中各后续着法的出现次数、红胜 / 和 / 黑胜比例，再加上引擎评分。例如「这里大师们 60% 走马二进三，红方胜率 54%」。

### 5.4 猜着练习（打谱训练的核心）

选一盘大师对局和自己执的一方，系统逐步走对方的着法，轮到你时先自己想、走出你认为最好的一步，然后系统揭晓大师的着法并打分：

| 情况 | 得分 |
|---|---|
| 和大师着法相同 | 3 |
| 不同，但引擎认为不差于大师着法 | 3（显示「你找到了同样好的着法」） |
| 比大师着法差 ≤ 3% 胜率 | 2 |
| 差 3–10% | 1 |
| 差 > 10% | 0，附讲解：为什么大师这样走，你的着法问题在哪 |

细节：
- 大师着法不一定是引擎最佳。若大师着法明显劣于引擎最佳，就标注「此处大师着法也非最佳」，你若走出了更好的着法不扣分。
- 开局前若干步可以跳过（或只在开局训练里练），把时间花在中局和残局。
- 一盘结束后给出：总分 / 满分、与大师的吻合率、平均胜率损失、失分最多的 3 步（加入错题本）。

### 5.5 名局 AI 解读（让 AI 推断着法意图）

1. **引擎整盘分析**：得到胜率曲线，找出转折点（胜率变化最大的几步）。
2. **对每个关键着法推断意图**，给大模型准备三类事实：
   - **威胁检测（空着法技巧）**：假设对方「停一步不走」，看走棋方下一步最想走什么。这就是这步棋制造的威胁。实现方法是把 FEN 里的走棋方翻转后交给引擎分析（若此时对方正被将军，局面不合法，跳过这一项）。
   - **前后特征对比**：哪些子新被攻击、哪些子新受保护，打通了哪条线，子力位置有什么变化。
   - **引擎主变化**：这步之后双方的最佳延续。

   大模型据此总结「这步棋想干什么」，例如「表面上是兑车，实际是为了打通肋道给马让路」。
3. **分阶段总结**：开局（开局名称和双方布局思路）、中局（主要计划和转折点）、残局（胜负关键）。
4. 结果存库，同一盘棋只解读一次，之后打谱时直接显示在对应步上。

### 5.6 从棋谱和自己的对局自动出题

- **出题条件**：某一步的最佳着法胜率比第二名高出 15% 以上（「唯一好棋」）。大师找到了它，可以出题；大师错过了它，也可以出题，答案是引擎着法。
- **自动标签**：规则引擎判断是否将军、是否吃子、是否弃子、是否几步成杀，据此打上「杀法」「弃子」「捉双」等标签。
- **难度**：初始按「引擎需要搜多深才能找到这步」估计，之后按你的做题结果用 Glicko-2 调整。
- 你自己对局中评为「失误」「漏着」的局面，自动进入错题本（见 8.2 节）。

### 5.7 棋谱来源与版权

- 来源：自己收集或购买的棋谱文件、自己的对局、Pikafish 自对弈。
- 网上公开的棋谱一般只允许个人学习使用。本项目只在本机使用，**不把任何第三方棋谱提交进仓库**：`data/` 目录加入 `.gitignore`，仓库里只放几盘自己构造的示例用于测试。

---

## 6. AI 提示系统

### 6.1 对局中的三级渐进提示

不直接给答案，让你先自己想，这样提示才有学习价值：

| 级别 | 内容 | 数据来源 |
|---|---|---|
| L1 方向提示 | 「注意：你有一个子没有保护」「对方有将军的手段」 | 规则引擎特征（不泄露着法） |
| L2 棋子提示 | 高亮应该走的那个棋子 | 引擎最佳着法的起点 |
| L3 着法 + 原因 | 画箭头 + 一句话讲解 | 引擎 + 大模型（或模板） |

每次使用提示都会被记录，计入该局的「独立完成度」。

### 6.2 着法评级（复盘、猜着共用）

用「走这步之前的胜率 − 走这步之后的胜率」（走棋方视角）来衡量：

| 评级 | 胜率下降 | 说明 |
|---|---|---|
| 妙着 | — | 唯一好棋：最佳着法比第二名高出 15% 以上，且走出来了 |
| 好棋 | ≤ 2% | |
| 可以 | 2–5% | |
| 缓着 | 5–10% | |
| 失误 | 10–20% | |
| 漏着 | > 20% | |

阈值是初始值，用一段时间后按自己的感受调整。

### 6.3 复盘报告

- 胜率曲线：失误点标红，点击跳到该局面。
- 准确率：平均每步胜率损失换算成 0–100 分。
- 分阶段表现：开局 / 中局 / 残局各自的准确率。
- 3 个关键时刻：每个都配讲解，并带「再试一次」按钮（从该局面重新走）。
- 推荐练习：按本局错误的标签（如「无根子」「漏看马的攻击」）推荐题目。

---

## 7. 人机对战难度

引擎最强水平远超人类，难度设计的关键是**让它犯「像人一样」的错误**，而不是突然走出离谱的着法。

强度控制手段（可组合使用）：
1. **限制搜索量**：节点数 / 深度 / 时间。简单，但低级别时表现不自然。
2. **引擎自带的强度选项**：引擎若支持 Skill Level / UCI_LimitStrength 之类的选项就直接用（以实际版本为准）。
3. **候选着法加温度随机**：用 MultiPV = N 拿到前 N 个候选着法和胜率，按 `softmax(胜率 / T)` 随机选择，T 越大越弱；再按级别设置「看不见对方威胁」的概率。
4. **（可选）大师风格网络**：见 3.4 节，按棋力段模仿人类着法。

初始参数示例（需实测调整）：

| 级别 | 搜索节点 | MultiPV | 温度 T | 适合 |
|---|---|---|---|---|
| 1 | 1k | 6 | 0.20 | 刚学会规则 |
| 3 | 5k | 5 | 0.10 | 入门 |
| 5 | 30k | 4 | 0.05 | 业余初级 |
| 7 | 200k | 3 | 0.02 | 业余中级 |
| 10 | 不限（按时间） | 1 | 0 | 全力 |

**自适应难度**：为你维护一个个人棋力分（Glicko-2），每个 AI 级别也有对应分数，系统推荐让你胜率在 40–60% 之间的级别。

---

## 8. 自学模块

### 8.1 杀法与战术题库
- 题目格式：`{ fen, solution: ICCS[], tags, rating, source }`。
- 主题：马后炮、卧槽马、双车错、铁门栓、重炮、闷宫、白脸将、天地炮等经典杀法，以及捉双、抽将、牵制等战术。
- 题目主要来自 5.6 节的自动出题，也可以手动录入。

### 8.2 错题本 + 间隔重复
- 复盘、猜着中失分的局面自动加入错题本（存 FEN + 正确着法 + 讲解）。
- 用 FSRS（或更简单的 SM-2）算法安排复习时间：做对了间隔变长，做错了很快再出。
- 首页显示「今日复习 N 题」。

### 8.3 开局训练
- 开局树来自棋谱库统计（5.3 节）加上引擎评分，并标注名称：中炮对屏风马、中炮对反宫马、飞相局、仙人指路、起马局、顺炮、列炮……
- 跟练模式：系统走一方，你需走出主流着法，偏离时提示并解释该开局的意图。

### 8.4 残局训练
- 给定实用残局局面，和全力引擎对下，要求在规定步数内取胜或守和；失败后可看引擎的正确走法。

### 8.5 学习画像
- 棋力分曲线（人机对战 + 做题 + 猜着）。
- 各主题正确率雷达图（杀法、防守、开局、残局、子力判断……）。
- 弱项推荐：正确率最低的主题优先出题。

---

## 9. 技术选型与项目结构

### 9.1 选型

| 层 | 选型 | 说明 |
|---|---|---|
| 语言 | Python ≥ 3.11 | |
| 依赖管理 | uv（或 pip + venv） | `uv run xiangqi` 一条命令启动 |
| 后端框架 | FastAPI + uvicorn | 原生支持 async 和 WebSocket |
| 数据校验 | pydantic v2 | API 数据和大模型输出共用 |
| 数据库 | SQLite + SQLModel（或 SQLAlchemy 2.0） | 单文件，备份就是复制文件 |
| 测试 / 代码风格 | pytest / ruff | |
| 大模型 SDK | `openai`（OpenAI 兼容）、`anthropic`（Claude） | 两个 SDK 的调用方式已核对 |
| 象棋引擎 | Pikafish 原生可执行文件 + NNUE 权重 | 从官方 GitHub Releases 下载，按 CPU 支持的指令集选版本 |
| 前端 | React + TypeScript + Vite，SVG 棋盘，ECharts | Node.js 只在开发和构建时需要，运行时不需要 |
| 可选训练 | PyTorch → ONNX → onnxruntime | 仅 3.4 节需要 |

### 9.2 目录结构

```
game/
├── backend/
│   ├── pyproject.toml
│   ├── xiangqi/
│   │   ├── core/            # 规则引擎（纯 Python，无第三方依赖）
│   │   │   ├── board.py         # 坐标、棋子编码、Position、预计算表
│   │   │   ├── movegen.py       # 着法生成、将军检测、合法着法
│   │   │   ├── rules.py         # 胜负、重复局面、长将
│   │   │   ├── fen.py           # FEN 读写
│   │   │   ├── notation.py      # ICCS ↔ 中文记谱、写法规范化
│   │   │   ├── zobrist.py       # 局面哈希
│   │   │   └── features.py      # 战术特征：无根子、威胁、子力对比
│   │   ├── engine/          # UCI 适配、Pikafish 进程管理、难度控制
│   │   ├── llm/             # base.py、openai_compat.py、claude.py、templates.py、validate.py、prompts/
│   │   ├── games/           # 棋谱解析（pgn / 中文文本 / dhtmlxq / xqf）、导入、索引、开局识别
│   │   ├── training/        # 复盘、猜着、题库、错题本（FSRS）、棋力分
│   │   ├── api/             # FastAPI 路由与 WebSocket
│   │   ├── db/              # 数据表定义
│   │   └── __main__.py      # 启动入口：起服务 + 打开浏览器
│   └── tests/               # perft、将军检测、记谱、棋谱解析
├── frontend/                # React 界面（构建产物由后端提供）
├── engines/                 # Pikafish 可执行文件和权重（.gitignore）
├── data/                    # 数据库、导入的棋谱（.gitignore）
├── config.toml
└── docs/
    └── xiangqi-plan.md
```

### 9.3 API 草案

每次局面变化都返回统一的 `PositionView`：

```json
{
  "fen": "rnbakab1r/9/1c4nc1/p1p1p1p1p/9/9/P1P1P1P1P/1C2C4/9/RNBAKABNR w - - 2 2",
  "turn": "red",
  "legal_moves": ["h0g2", "b0c2", "e2e6", "..."],
  "last_move": "h9g7",
  "in_check": false,
  "result": null,
  "moves_cn": ["炮二平五", "马8进7"]
}
```

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/games` | 新建对局 `{mode: "vs_ai" \| "free", ai_level, user_side, fen?}` |
| POST | `/api/games/{id}/move` | `{move: "h2e2"}` → `PositionView` |
| POST | `/api/games/{id}/ai-move` | AI 走一步 → `{move, position}` |
| POST | `/api/games/{id}/undo` | 悔棋 |
| POST | `/api/games/{id}/hint` | `{level: 1 \| 2 \| 3}` → 提示 |
| POST | `/api/games/{id}/review` | 发起复盘（后台任务） |
| WS | `/ws/analysis` | 发送 `{fen, moves}`，持续收到 `AnalysisLine` |
| POST | `/api/library/import` | 上传棋谱文件 → 导入报告 |
| GET | `/api/library/games` | 搜索：`?player=&event=&opening=&fen=` |
| GET | `/api/library/games/{id}` | 对局详情（含解读） |
| GET | `/api/library/explorer` | `?fen=` → 局面统计 |
| POST | `/api/library/games/{id}/annotate` | AI 解读（后台任务） |
| POST | `/api/guess` | 开始猜着 `{game_id, side}` |
| POST | `/api/guess/{id}/answer` | `{move}` → 得分、大师着法、讲解 |
| GET | `/api/puzzles/next` | `?theme=` → 下一题 |
| POST | `/api/puzzles/{id}/attempt` | 提交答案 |
| GET | `/api/review-queue` | 今日待复习 |

### 9.4 数据表（SQLite）

```
games          (id, kind[library|my_game], event, date, red, black, result, opening,
                initial_fen, moves_iccs, source_file, content_hash UNIQUE, created_at)
position_index (zobrist, game_id, ply)                 -- 局面出现在哪些对局的第几步
move_analysis  (game_id, ply, fen, move, best_move, win_before, win_after, grade, pv)
annotations    (game_id, ply, kind[intent|summary|mistake], content_json, provider, model)
explain_cache  (cache_key PRIMARY KEY, content_json, provider, model, created_at)
puzzles        (id, fen, solution, tags, rating, source_game_id, source_ply)
cards          (id, kind[puzzle|mistake], ref_id, stability, difficulty, due_at, reps, lapses)
guess_sessions (id, game_id, side, current_ply, score, max_score, started_at)
guess_answers  (session_id, ply, user_move, master_move, points, win_loss)
kv             (key PRIMARY KEY, value)                -- 个人棋力分、偏好设置等
```

---

## 10. 里程碑（1 人开发估算）

| 阶段 | 时长 | 交付 | 验收标准 |
|---|---|---|---|
| M0 骨架 | 1 周 | uv 项目、FastAPI、Vite 前端、配置文件、Position / FEN、棋盘静态显示 | FEN 读写往返一致 |
| M1 规则引擎 | 2 周 | 着法生成、将军检测、胜负、Zobrist、重复 / 长将、中文记谱（双向）、本地摆棋走棋 | perft 1–4 通过；将军检测和记谱测试全部通过 |
| M2 人机对战 | 1–2 周 | Pikafish 接入、对弈、10 级难度、悔棋、L2 / L3 提示、实时评估条 | 能和 AI 完整下完一盘 |
| M3 棋谱库 | 2 周 | PGN / 中文文本导入、回放校验、局面索引、浏览搜索、打谱、试走、局面统计 | 导入 1000 盘棋不崩溃，失败对局有原因报告 |
| M4 讲解 + 复盘 | 2 周 | LLMProvider 两种实现、上下文构建、校验、模板兜底、缓存、整盘复盘报告、L1 提示 | 30 局面评测集上编造着法 0 次 |
| M5 棋谱训练 | 2 周 | 猜着练习、名局 AI 解读、自动出题、错题本 | 「打谱 → 猜着 → 错题 → 复习」闭环跑通 |
| M6 扩展 | 2 周 | DhtmlXQ / XQF 导入、开局跟练、残局训练、学习画像 | |
| M7 可选 | 4 周+ | 大师风格神经网络：张量编码、训练、推理 | 着法预测吻合率可量化 |

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| Python 规则引擎速度 | 预计算表（已实测 perft(4) 约 10 秒）；搜索交给引擎；批量导入放后台任务；真有瓶颈再针对热点优化 |
| 棋谱格式杂乱、写法不统一 | 先支持最常见的两种；用规范化表统一写法；每种格式用真实样例文件做回归测试；导入报告列出失败原因 |
| 便宜模型更容易胡说 | 只讲不算；事实预先算好；着法白名单校验 + 重试 + 模板兜底；用评测集比较不同模型 |
| 规则细节（长将、长捉） | MVP 实现长将判负 + 其他重复判和；长捉逐步完善；用特殊局面做回归测试 |
| 引擎太强，低级别不自然 | 候选着法加温度随机 + 按级别「漏看」威胁；之后可用大师风格网络 |
| API Key 泄露 | Key 只放环境变量或本地 `.env`，`.env` 加入 `.gitignore` |
| 棋谱版权 | 仅本机个人使用，不提交、不分发第三方棋谱 |

---

## 12. 还需要确认的问题

1. **手上有没有现成的棋谱文件？是什么格式？**（PGN、XQF、东萍 DhtmlXQ、纯文本中文记谱……）这决定 M3 先写哪个解析器。可以把一两个样例文件放进仓库外的 `data/` 目录给我看格式。
2. **前端用 React 可以吗？** 需要在电脑上装 Node.js，用于开发和构建；运行时只需要 Python。
3. **Python 版本**：方案按 3.11 及以上设计，可以吗？

确认后从 M0 / M1 开始实现：先把 `xiangqi/core`（规则引擎 + 记谱 + perft 测试）做扎实，后面所有功能都依赖它。
