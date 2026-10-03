# 象棋自学 · AI 提示 · 人机对战 Web 应用 —— 技术方案

> 版本：v0.1（方案稿）
> 目标：一个可在浏览器里使用、以「自我提升」为核心的中国象棋应用：能和 AI 下棋，下棋时能要提示，下完能复盘并听懂自己错在哪，平时能做题、背开局、练残局。

---

## 0. 先说结论（最重要的三条）

1. **不要让大模型（LLM）直接下棋或直接算棋。**
   LLM 直接读棋盘时，常把子的位置看错，编出不合法的着法，也算不深。正确的分工是：

   | 角色 | 由谁担任 | 负责什么 |
   |---|---|---|
   | 裁判 | 自写的**规则引擎**（TypeScript） | 着法是否合法、将军、胜负、记谱转换、战术特征提取 |
   | 棋手 / 计算器 | 专业**象棋引擎** Pikafish / Fairy-Stockfish | 算最佳着法、评估分数、给出主要变化 |
   | 教练（讲解员） | **LLM**（Claude API） | 把引擎算出的结果「翻译」成人话，解释为什么 |

2. **「棋盘转成数据」不是一个格式，而是分层的几种表示**，每一层喂给不同的「模型」：

   ```
   内部数组 Int8Array(90) ──► 规则引擎（合法性、特征）
          │
          ├──► FEN + ICCS 坐标着法 ──► 象棋引擎（UCI 协议）
          │
          ├──► 张量 [C, 10, 9] + 2086/2062 维策略 ──► 神经网络（可选，自训练时才需要）
          │
          └──► 结构化 JSON + 文字棋盘 + 中文记谱 + 已算好的事实 ──► LLM（只负责讲解）
   ```

   第 3 节会把每一层的格式、示例、代码都写清楚。

3. **先把 MVP 做出来，不需要自己训练模型**：规则引擎 + 开源引擎 + LLM 讲解，已经能做出「人机对战 + 提示 + 复盘讲解」的完整闭环。自训练神经网络放到最后作为可选项（用来做「像人一样下棋」的低级别 AI）。

---

## 1. 产品目标与功能

### 1.1 目标用户
入门到业余中级、想系统提升的个人棋手。核心诉求：**知道自己哪里错了、为什么错、以后怎么避免**。

### 1.2 功能清单

| 模块 | 功能 | 优先级 |
|---|---|---|
| 人机对战 | 执红 / 执黑；10 个难度级别；悔棋；认输 / 求和；可选计时 | P0 |
| AI 提示 | 三级渐进提示（方向 → 该动哪个子 → 具体着法 + 原因） | P0 |
| 对局复盘 | 胜率曲线；每步评级（好棋 / 缓着 / 失误 / 漏着）；关键时刻；LLM 中文讲解 | P0 |
| 分析模式 | 摆任意局面；FEN 导入导出；引擎多线分析 | P1 |
| 杀法 / 战术题库 | 经典杀法、战术组合题；题目难度分；按主题练习 | P1 |
| 错题本 | 自己对局中的失误局面自动入库，按间隔重复算法安排复习 | P1 |
| 开局训练 | 开局树浏览；跟练主流变化；偏离时提示 | P2 |
| 残局训练 | 和引擎下指定残局，要求 N 步内取胜或守和 | P2 |
| 学习画像 | 棋力分（人机 + 做题）；各主题正确率；弱项推荐 | P2 |
| 像人一样的 AI | 按棋力段模仿人类着法的神经网络（自训练） | P3（可选） |

---

## 2. 总体架构

```
┌──────────────────────────── 浏览器 ────────────────────────────┐
│  React UI（SVG 棋盘、复盘面板、题库、错题本）                     │
│        │                                                         │
│        ├── packages/core：规则引擎、FEN、中文记谱、特征提取（TS）   │
│        │                                                         │
│        └── Web Worker：象棋引擎 WASM（对局、快速提示，离线可用）    │
└───────────────┬─────────────────────────────────────────────────┘
                │ HTTPS / JSON
┌───────────────▼──────────────────── 服务端 ─────────────────────┐
│  API 服务（Node.js + Fastify，复用 packages/core）                │
│   ├── 深度分析：Pikafish 原生进程池（UCI 协议）                   │
│   ├── 讲解服务：特征提取 → 组装 Prompt → Claude API → 输出校验     │
│   ├── 题库 / 错题本 / 对局记录 / 用户数据（PostgreSQL，MVP 可 SQLite）│
│   └── 缓存：(FEN, 着法, 级别) → 分析结果 / 讲解文本                │
└──────────────────────────────────────────────────────────────────┘
```

**为什么前后端都有引擎？**
- 浏览器端 WASM：下棋零延迟、不花服务器钱、断网也能玩。
- 服务端原生 Pikafish：复盘时要对整盘棋每一步做深度分析，原生多线程比 WASM 快很多，并且结果可以缓存共享。
- LLM 调用必须走服务端：API Key 不能放在前端。

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

### 3.2 第 1 层：内部表示（给规则引擎）

#### 数据结构

```ts
// 棋子：正数红方，负数黑方，0 为空
export const enum PT { King = 1, Advisor, Bishop, Knight, Rook, Cannon, Pawn } // 帅仕相马车炮兵
export type Piece = number;   // ±1..±7
export type Square = number;  // 0..89

export const sq = (file: number, rank: number): Square => rank * 9 + file;
export const fileOf = (s: Square) => s % 9;
export const rankOf = (s: Square) => (s / 9) | 0;
export const onBoard = (f: number, r: number) => f >= 0 && f < 9 && r >= 0 && r < 10;

export interface Position {
  board: Int8Array;      // 长度 90
  turn: 1 | -1;          // 1 = 红走，-1 = 黑走
  halfmoveClock: number; // 距上次吃子的半回合数（自然限着用）
  keys: bigint[];        // 历史局面的 Zobrist 哈希（重复局面、长将检测用）
}

// 着法打包成一个整数：from(7 bit) | to(7 bit) << 7
export type Move = number;
export const makeMove = (from: Square, to: Square): Move => from | (to << 7);
```

为什么用简单的 `Int8Array(90)` 而不用位棋盘（bitboard）？
- 象棋 90 个点超出 64 位，位棋盘要 128 位；Pikafish 内部就是这么做的，但那是为了每秒搜索上千万个局面。
- 我们的 TS 规则引擎**只负责正确性**（真正的搜索交给 WASM / 原生引擎），每步生成一次着法，简单数组完全够快，也最好调试。

#### 着法生成要点（象棋特有规则）

| 棋子 | 规则 | 实现要点 |
|---|---|---|
| 车 | 直线任意格，不能越子 | 四个方向循环，遇子停止（对方子可吃） |
| 炮 | 不吃子时同车；**吃子必须隔一个子（炮架）** | 循环中记录 `jumped` 状态 |
| 马 | 走日字；**蹩马腿** | 8 个落点各对应一个「马腿」格，马腿有子则不能走 |
| 相/象 | 走田字；**塞象眼**；**不能过河** | 田字中心有子则不能走；红相 rank ≤ 4，黑象 rank ≥ 5 |
| 仕/士 | 斜走一格，限九宫 | 九宫：file 3–5，红 rank 0–2，黑 rank 7–9 |
| 帅/将 | 直走一格，限九宫；**两将不能照面** | 照面检查放在合法性过滤里 |
| 兵/卒 | 过河前只能前进；过河后可左右 | 红 rank ≥ 5 视为已过河，黑 rank ≤ 4 |

示例：马和炮的生成

```ts
// [落点 df, dr, 马腿 lf, lr]
const KNIGHT_STEPS = [
  [ 1,  2, 0,  1], [-1,  2, 0,  1], [ 1, -2, 0, -1], [-1, -2, 0, -1],
  [ 2,  1, 1,  0], [ 2, -1, 1,  0], [-2,  1, -1, 0], [-2, -1, -1, 0],
] as const;

function genKnight(pos: Position, from: Square, out: Move[]) {
  const f = fileOf(from), r = rankOf(from);
  for (const [df, dr, lf, lr] of KNIGHT_STEPS) {
    if (!onBoard(f + df, r + dr)) continue;
    if (pos.board[sq(f + lf, r + lr)] !== 0) continue;       // 蹩马腿
    const to = sq(f + df, r + dr);
    if (pos.board[to] * pos.turn > 0) continue;              // 不能吃己方
    out.push(makeMove(from, to));
  }
}

function genCannon(pos: Position, from: Square, out: Move[]) {
  for (const [df, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    let f = fileOf(from) + df, r = rankOf(from) + dr, jumped = false;
    while (onBoard(f, r)) {
      const p = pos.board[sq(f, r)];
      if (!jumped) {
        if (p === 0) out.push(makeMove(from, sq(f, r)));     // 不吃子时像车
        else jumped = true;                                   // 找到炮架
      } else if (p !== 0) {
        if (p * pos.turn < 0) out.push(makeMove(from, sq(f, r))); // 隔一子吃对方
        break;
      }
      f += df; r += dr;
    }
  }
}
```

#### 合法性 = 伪合法着法 + 走完后己方不被将 + 两将不照面

```ts
function kingsFacing(board: Int8Array): boolean {
  const rk = board.indexOf(PT.King), bk = board.indexOf(-PT.King); // 红帅总在下方，rk < bk
  if (fileOf(rk) !== fileOf(bk)) return false;
  for (let s = rk + 9; s < bk; s += 9) if (board[s] !== 0) return false;
  return true;
}

function legalMoves(pos: Position): Move[] {
  const me = pos.turn;
  return pseudoLegalMoves(pos).filter((m) => {
    const captured = doMove(pos, m);   // 会切换 turn
    const ok = !kingsFacing(pos.board) && !isAttacked(pos, kingSquare(pos, me), -me);
    undoMove(pos, m, captured);
    return ok;
  });
}
```

#### 胜负与特殊规则

- **将死**：被将军且无合法着法 → 负。
- **困毙**：没被将军但无合法着法 → **也判负**（和国际象棋的「逼和」不同，一定要注意）。
- **重复局面**：用 Zobrist 哈希记录历史局面；同一局面出现 3 次进入判定。
- **长将**：一方连续将军造成重复 → 长将方判负。
- **长捉**：连续捉对方无根子造成重复 → 判负。这是规则引擎最复杂的部分（要判断「捉」「有根」「兑子」等），**MVP 先只实现长将判负 + 其他重复判和**，长捉后续迭代。
- **自然限着**：连续若干回合（常用 60 回合）无吃子 → 判和，做成可配置项。

#### 用 perft 测试保证正确性

perft(n) = 从某局面出发走 n 步的所有合法着法序列数。它是检验着法生成器是否正确的标准方法。初始局面的标准值：

| 深度 | 节点数 |
|---|---|
| 1 | 44 |
| 2 | 1,920 |
| 3 | 79,666 |
| 4 | 3,290,240 |

本方案写作时已用一个最小的 Python 原型核对过深度 1–4。除初始局面外，还应加入若干包含蹩马腿、塞象眼、炮架、将帅照面的特殊局面做 perft 回归测试。

### 3.3 第 2 层：引擎格式（给 Pikafish / Fairy-Stockfish）

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

`起点列 起点行 终点列 终点行`，例如 `h2e2` = 炮二平五，`h9g7` = 马8进7。

**另一个兼容性坑**：Pikafish 行号是 0–9（`h2e2`），Fairy-Stockfish 行号是 1–10（同一步写成 `h3e3`，黑马跳写成 `h10g8`）。在**引擎适配层**统一转换，业务代码只认 0–9。

#### UCI 协议交互（Pikafish 使用 UCI）

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

#### 分数 → 胜率

人对「胜率」的感知比「分数」直观得多，而且在 +800 分的局面里再丢 100 分几乎不影响结果。所以所有评级都基于胜率：

```ts
// 若引擎支持 WDL 输出（UCI_ShowWDL），优先直接用引擎给的胜/和/负概率；
// 否则用逻辑函数近似，K 需要用实际对局数据标定
const winProb = (cpRedView: number, K = 200) => 1 / (1 + Math.exp(-cpRedView / K));
```

#### 引擎适配层接口

```ts
interface EngineAdapter {
  init(options: Record<string, string | number>): Promise<void>;
  analyse(req: {
    fen: string;
    moves?: string[];        // ICCS，0–9 行号
    limit: { movetimeMs?: number; depth?: number; nodes?: number };
    multiPV?: number;
  }): Promise<AnalysisLine[]>;
  stop(): void;
}

interface AnalysisLine {
  move: string;              // ICCS
  scoreCp?: number;          // 红方视角
  mateIn?: number;           // 红方视角
  winProb: number;           // 红方胜率 0..1
  pv: string[];              // 主要变化（ICCS）
  depth: number;
}
// 实现：PikafishProcessAdapter（服务端 child_process）、FairyStockfishWasmAdapter（浏览器 Worker）
```

### 3.4 第 3 层：神经网络张量（可选：自训练模型时才需要）

**MVP 不需要这一层。** 只有当你想做以下事情时才需要：
- **像人一样下棋的 AI**：按棋力段（如 1200 分、1500 分）训练策略网络去预测「这个水平的人类会怎么走」。这样的低级别对手比「引擎随机走错」自然得多，还能告诉用户「你这个水平的人在这里最常犯什么错」。
- 研究、学习 AlphaZero 式训练。

不建议自己训练「最强引擎」：Pikafish 已经远超人类顶尖水平。

#### 输入：把局面变成 `[C, 10, 9]` 的张量

核心思想（AlphaZero 风格）：**每种棋子一张 10×9 的 0/1 平面**。

```
平面 0–6  ：当前走棋方的 帅 仕 相 马 车 炮 兵（有子为 1，否则 0）
平面 7–13 ：对方的       帅 仕 相 马 车 炮 兵
（可选）平面 14+ ：前 N 步的历史局面、重复次数、无吃子步数（归一化成常数平面）
```

关键技巧——**视角归一化**：总是让「轮到走棋的一方」在棋盘下方。黑方走时把棋盘上下翻转、红黑互换。这样网络只需学一种视角，数据利用率翻倍。象棋规则在「上下翻转 + 换色」下完全对称，所以这样做不会改变规则。

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

#### 输出：策略头 + 价值头

- **价值头**：一个标量 ∈ [-1, 1]（走棋方的期望得分），或胜/和/负三分类。
- **策略头**：给每一个「几何上可能出现的着法」一个编号，网络输出每个编号的概率。

象棋的全部可能着法（从 A 点到 B 点）枚举：

| 类别 | 数量 | 说明 |
|---|---|---|
| 直线（同行或同列任意两点） | 1530 | 90 × (8 + 9)，车、炮、帅、兵的走法都是它的子集 |
| 马（日字） | 508 | 不考虑蹩腿的所有日字跳 |
| 相/象（田字，限合法象位） | 32 | 每方 16 |
| 仕/士（九宫斜线） | 16 | 每方 8 |
| **合计（不做视角翻转）** | **2086** | |
| **合计（做视角翻转，只需己方的相、仕）** | **2062** | 1530 + 508 + 16 + 8 |

这些数字已用脚本枚举核对过。也可以用更简单的 `90 × 90 = 8100` 维输出，但大部分位置永远不会被用到，训练效率低。

使用时：对当前局面的**非法着法做 mask**（把 logits 设为 -∞）再 softmax。做了视角翻转的话，着法编号也要按同样方式翻转。

#### 训练与部署要点

- **数据增强**：棋盘左右对称，左右镜像后数据量 ×2。
- **数据来源**：① 用户在本平台的对局（需同意）；② Pikafish 低节点数自对弈生成；③ 公开棋谱库（务必确认版权和使用条款）。
- **模型规模**：浏览器端可用 6–10 个残差块 × 64–128 通道的小网络，导出 ONNX 后用 `onnxruntime-web` 在浏览器里推理。
- **了解即可**：Pikafish 用的是 **NNUE**（输入是「己方帅的位置 × 棋子种类 × 棋子位置」这种稀疏特征，走一步只需增量更新少数特征，所以 CPU 上极快）。我们不需要自己实现它，直接用引擎即可。

### 3.5 第 4 层：给 LLM 的讲解上下文（AI 提示的关键）

#### 原则：LLM 只「讲」，不「算」

所有事实——合法着法、最佳着法、分数、对方的反击手段、哪个子没保护——**都先由引擎和规则引擎算好**，作为结构化数据交给 LLM。LLM 的任务只是：挑出最重要的事实，用对方能听懂的话讲清楚，并总结出可迁移的原则。

#### 输入由 5 部分组成

1. **文字棋盘**：用中文字符画出的棋盘，红黑用不同的字区分，帮助模型建立空间感。
2. **棋子清单**：每个子的位置，避免模型自己数格子数错。
3. **引擎分析**：用户实际走的着法、前 3 名候选着法、各自胜率、主要变化（全部带中文记谱）。
4. **规则引擎提取的战术事实**（这是防止胡说的关键）：
   - 是否将军；哪些子被攻击；哪些子**无根**（被攻击且无保护）；
   - **用户这步之后对方的最佳应着是什么、吃掉了什么**（直接从引擎主变化取）；
   - 子力对比、局面阶段（开局 / 中局 / 残局）。
5. **用户水平**：决定讲解深浅和用词。

#### 完整示例

局面：1. 炮二平五 马8进7，红方第 2 步走了 **炮五进四**（用炮吃中卒），这一步被黑马直接吃回，属于漏着。
（以下局面、着法、攻防关系均已用原型脚本核对；胜率数值仅为示意。）

```json
{
  "player": { "side": "红方", "level": "入门（约 1200 分）" },
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

System Prompt（固定不变，可以利用 prompt caching 降低成本）：

```
你是一位耐心的中国象棋教练，学生的水平在输入的 player.level 中给出。
你会收到一个局面和已经由象棋引擎、规则引擎计算好的事实。
规则：
1. 只能使用输入中给出的事实和着法，不要自己推算变化，不要提到 allowed_moves_cn 之外的任何着法。
2. 先给一句话结论，再解释原因，最后给出一条以后能用上的原则。
3. 用学生能听懂的话，少用术语；必须用术语时顺便解释。
4. 讲解不超过 150 字。
```

输出用结构化 JSON（Claude API 的 structured outputs 能保证返回的 JSON 符合 schema）：

```json
{
  "headline": "这步炮吃中卒是送子：黑马可以直接把炮吃掉。",
  "why": "...",
  "better": "...",
  "principle": "吃子前先看落点：对方能不能吃回来？我有没有子保护它？",
  "tags": ["无根子", "贪吃", "开局"]
}
```

服务端调用示意（TypeScript，`@anthropic-ai/sdk` + `zod`）：

```ts
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";

const Explanation = z.object({
  headline: z.string(),
  why: z.string(),
  better: z.string(),
  principle: z.string(),
  tags: z.array(z.string()),
});

const client = new Anthropic(); // 从环境变量 ANTHROPIC_API_KEY 读取

export async function explain(context: object) {
  const res = await client.messages.parse({
    model: "claude-opus-5-5",
    max_tokens: 2000,
    system: [{ type: "text", text: COACH_SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
    messages: [{ role: "user", content: JSON.stringify(context) }],
    output_config: { format: zodOutputFormat(Explanation), effort: "low" },
  });
  return res.parsed_output; // 解析失败时为 null，需要兜底
}
```

#### 防止 LLM 胡说：输出校验

1. 用正则提取讲解文本里出现的所有中文着法：
   ```ts
   const CN_MOVE = /(?:[前中后][车马炮兵卒相象仕士]|[车马炮相象仕士帅将兵卒][一二三四五六七八九1-9])[进退平][一二三四五六七八九1-9]/g;
   ```
2. 每一个都必须在 `allowed_moves_cn` 里，否则重新生成一次；仍不通过则**回落到模板讲解**（例如「这步棋之后对方可以 {opponent_reply}，{facts[0]}」）。
3. 讲解结果按 `(FEN, 着法, 水平)` 做缓存，同一个错误不重复调用 LLM。

#### 模型选择与成本

- 默认使用 `claude-opus-5-5`，短提示用 `effort: "low"`，整盘复盘报告可以调高 effort。
- 如果对成本敏感，可以把「一句话提示」换成更便宜的 `claude-sonnet-5-5` 或 `claude-haiku-4-5`，具体由你决定，建议先用真实局面对比讲解质量再换。
- 只对**关键时刻**（胜率变化超过阈值的步）调用 LLM，普通步只显示评级标签，不调用。

### 3.6 第 5 层：中文记谱（显示和 LLM 共用）

规则：
- 格式：`棋子 + 起点纵线 + 动作 + 数字`，如「炮二平五」「马8进7」。红方用中文数字，黑方用阿拉伯数字。
- 动作：同一行移动 = **平**；向对方方向 = **进**；向己方方向 = **退**。
- 数字：「平」后面是目标纵线；直走子（车、炮、兵、帅）的「进 / 退」后面是**走了几步**；斜走子（马、相、仕）的「进 / 退」后面是**目标纵线**。
- 同一纵线上有两个同种同色子：用「前 / 后」代替纵线号，如「前车进二」（前 = 更靠近对方）。兵卒在同一纵线上有三个及以上时用「前、中、后」或「一、二、三……」。

```ts
const RED_NUM = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"];
const RED_NAME = ["", "帅", "仕", "相", "马", "车", "炮", "兵"];
const BLACK_NAME = ["", "将", "士", "象", "马", "车", "炮", "卒"];
const fileNo = (file: number, red: boolean) => (red ? 9 - file : file + 1);

export function toChinese(board: Int8Array, from: Square, to: Square): string {
  const p = board[from], red = p > 0, type = Math.abs(p);
  const num = (n: number) => (red ? RED_NUM[n] : String(n));
  const [ff, fr, tf, tr] = [fileOf(from), rankOf(from), fileOf(to), rankOf(to)];

  let action: string, target: number;
  if (fr === tr) {
    action = "平"; target = fileNo(tf, red);
  } else {
    action = (red ? tr > fr : tr < fr) ? "进" : "退";
    const diagonal = type === PT.Advisor || type === PT.Bishop || type === PT.Knight;
    target = diagonal ? fileNo(tf, red) : Math.abs(tr - fr);
  }

  const name = (red ? RED_NAME : BLACK_NAME)[type];
  const twins = sameFileTwins(board, from); // 同纵线同种同色子，按「靠近对方」排序；兵卒多子另行处理
  const head = twins.length === 2
    ? (twins[0] === from ? "前" : "后") + name
    : name + num(fileNo(ff, red));
  return head + action + num(target);
}
```

单元测试用例（均已用原型脚本核对）：

| ICCS | 中文 |
|---|---|
| `h2e2` | 炮二平五 |
| `h9g7` | 马8进7 |
| `b0c2` | 马八进七 |
| `i0i1` | 车一进一 |
| `h0g2` | 马二进三 |
| `i9h9` | 车9平8 |
| `g3g4` | 兵三进一 |

另需反向解析（中文 → ICCS），用于导入中文棋谱和校验 LLM 输出：在当前局面的合法着法里逐个生成中文记谱，找到匹配的那一个即可，不必单独写解析器。

### 3.7 第 6 层：存储格式

| 用途 | 格式 |
|---|---|
| 对局记录 | `{ initialFen, moves: ICCS[], result, meta }`（JSON），可导出 PGN 风格文本（`[Format "ICCS"]`） |
| 导入 | FEN、PGN（ICCS / 中文记谱）；可选支持 XQF 等常见棋谱文件 |
| 局面索引 | Zobrist 64 位哈希：错题本去重、开局库查询、讲解缓存的键 |

### 3.8 各层表示一览

| 用途 | 格式 | 示例 |
|---|---|---|
| 规则计算 | `Int8Array(90)` + 走棋方 + 历史哈希 | `board[85] = -1`（黑将在 e9） |
| 引擎通信 | FEN + ICCS | `position fen ... moves h2e2 h9g7` |
| 神经网络 | `float32[14, 10, 9]` + 2062 维策略 | 见 3.4 |
| LLM | JSON：文字棋盘 + 事实 + 候选着法（中文） | 见 3.5 |
| 用户界面 | 中文记谱 + 箭头 / 高亮 | 炮二平五 |
| 存储 | 初始 FEN + ICCS 着法列表 | `{"moves":["h2e2","h9g7"]}` |

---

## 4. AI 提示系统

### 4.1 对局中的三级渐进提示

不直接给答案，让用户先自己想，这样提示才有学习价值：

| 级别 | 内容 | 数据来源 |
|---|---|---|
| L1 方向提示 | 「注意：你有一个子没有保护」「对方有将军的手段」 | 规则引擎特征（不泄露着法） |
| L2 棋子提示 | 高亮应该走的那个棋子 | 引擎最佳着法的起点 |
| L3 着法 + 原因 | 画箭头 + 一句话讲解 | 引擎 + LLM |

每次使用提示都会记录下来，影响该局的「独立完成度」统计。

### 4.2 着法评级（复盘用）

用「走这步之前的胜率 − 走这步之后的胜率」（走棋方视角）来衡量：

| 评级 | 胜率下降 | 说明 |
|---|---|---|
| 妙着 | — | 唯一好棋：最佳着法比第二名高出 15% 以上，且用户走出来了 |
| 好棋 | ≤ 2% | |
| 可以 | 2–5% | |
| 缓着 | 5–10% | |
| 失误 | 10–20% | |
| 漏着 | > 20% | |

以上阈值是初始值，上线后根据用户反馈调整。

### 4.3 复盘报告

- 胜率曲线（横轴步数，纵轴红方胜率），失误点标红，点击跳转到该局面。
- 准确率：平均每步胜率损失换算成 0–100 分。
- 分阶段表现：开局 / 中局 / 残局各自的准确率。
- 3 个关键时刻：胜率变化最大的步，每个配 LLM 讲解 + 「再试一次」按钮（从该局面重新走）。
- 推荐练习：根据本局错误的标签（如「无根子」「漏看马的攻击」）从题库推荐题目。

---

## 5. 人机对战难度

引擎最强水平远超人类，难度设计的关键是**让它犯「像人一样」的错误**，而不是突然走出离谱的着法。

强度控制手段（可组合使用）：
1. **限制搜索量**：节点数 / 深度 / 时间。简单，但低级别时表现不自然。
2. **引擎自带的强度选项**：如果引擎支持 Skill Level / UCI_LimitStrength 之类的选项就直接用（以实际引擎版本为准）。
3. **候选着法加温度随机**：用 MultiPV = N 拿到前 N 个候选和胜率，按 `softmax(胜率 / T)` 随机选择，T 越大越弱；再按级别设置「看不见对方威胁」的概率。
4. **（进阶）人类风格网络**：见 3.4，按棋力段模仿人类着法。

初始参数示例（需实测调整）：

| 级别 | 搜索节点 | MultiPV | 温度 T | 适合 |
|---|---|---|---|---|
| 1 | 1k | 6 | 0.20 | 刚学会规则 |
| 3 | 5k | 5 | 0.10 | 入门 |
| 5 | 30k | 4 | 0.05 | 业余初级 |
| 7 | 200k | 3 | 0.02 | 业余中级 |
| 10 | 不限（按时间） | 1 | 0 | 全力 |

**自适应难度**：为用户维护一个棋力分（Elo / Glicko-2），每个 AI 级别也有对应分数，系统推荐让用户胜率在 40–60% 之间的级别。

---

## 6. 自学模块

### 6.1 杀法与战术题库
- 题目格式：`{ fen, solution: ICCS[], tags: [], rating, source }`。
- 主题：马后炮、卧槽马、双车错、铁门栓、重炮、闷宫、白脸将、天地炮等经典杀法，以及捉双、抽将、牵制等战术。
- **自动出题**：从对局（用户的和自对弈的）中找「唯一胜着」局面，即最佳着法比第二名高出很多，并且后续变化能收束成杀或明显得子。
- 题目难度分用 Glicko-2：用户做对，用户分上升、题目分下降，反之亦然。

### 6.2 错题本 + 间隔重复
- 复盘中评级为「失误」「漏着」的局面自动加入错题本（存 FEN + 正确着法 + 讲解）。
- 用 FSRS（或较简单的 SM-2）算法安排复习时间：做对了间隔变长，做错了很快再出。
- 首页每天推送「今日复习 N 题」。

### 6.3 开局训练
- 开局树：每个节点 = 局面哈希，边 = 着法，附带名称（中炮对屏风马、中炮对反宫马、飞相局、仙人指路、起马局、顺炮、列炮……）和引擎评分。
- 跟练模式：系统走一方，用户需走出主流着法，偏离时提示并解释该开局的意图。

### 6.4 残局训练
- 给定实用残局局面，用户和全力引擎对下，要求在规定步数内取胜或守和；失败后可看引擎正确走法。

### 6.5 学习画像
- 棋力分曲线（人机 + 做题）。
- 各主题正确率雷达图（杀法、防守、开局、残局、子力判断……）。
- 弱项推荐：正确率最低的主题优先出题。

---

## 7. 技术选型与项目结构

### 7.1 选型

| 层 | 选型 | 理由 |
|---|---|---|
| 前端 | React + TypeScript + Vite | 生态成熟 |
| 状态管理 | Zustand | 轻量 |
| 棋盘渲染 | SVG | 矢量缩放清晰，易做动画、箭头、高亮，手机上也清楚 |
| 图表 | ECharts 或 Recharts | 胜率曲线、雷达图 |
| 浏览器引擎 | Fairy-Stockfish WASM（支持 xiangqi，有现成 npm 包）| 免编译即可用；后续可尝试自行用 Emscripten 编译 Pikafish 并实测 |
| 服务端引擎 | Pikafish 原生二进制 | 目前最强的开源象棋引擎之一 |
| 后端 | Node.js + Fastify + TypeScript | 和前端共用 `packages/core`（规则、记谱、校验只写一份） |
| 任务队列 | MVP 用进程内队列；量大后换 BullMQ + Redis | 整盘复盘分析是耗时任务 |
| 数据库 | MVP 用 SQLite，上线换 PostgreSQL（Drizzle 或 Prisma） | |
| LLM | Claude API（`@anthropic-ai/sdk`） | 中文讲解质量好，支持结构化输出、prompt caching |
| 训练（可选） | Python + PyTorch，导出 ONNX → `onnxruntime-web` | 仅第 3.4 节需要 |

**WASM 多线程注意**：多线程 WASM 依赖 `SharedArrayBuffer`，页面必须返回以下响应头，否则只能单线程：

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

### 7.2 目录结构（pnpm monorepo）

```
game/
├── packages/
│   ├── core/          # 规则引擎、FEN、ICCS、中文记谱、Zobrist、战术特征提取（纯 TS，零依赖）
│   └── engine/        # EngineAdapter、UCI 解析、Pikafish 进程池、WASM Worker 封装
├── apps/
│   ├── web/           # React 前端
│   └── server/        # Fastify API、讲解服务、分析队列
├── data/
│   ├── openings/      # 开局树 JSON
│   └── puzzles/       # 题库 JSON
├── ml/                # （可选）Python 训练管线
└── docs/
    └── xiangqi-plan.md
```

### 7.3 API 草案

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/analyse` | `{fen, moves?, multiPV?, depth?}` → 候选着法（ICCS + 中文 + 胜率 + 主变） |
| POST | `/api/explain` | `{fen, move, level}` → 讲解 JSON（带缓存） |
| POST | `/api/games` | 保存对局 |
| POST | `/api/games/:id/review` | 发起整盘复盘（异步任务） |
| GET | `/api/games/:id/review` | 获取复盘结果 |
| GET | `/api/puzzles/next?theme=` | 按用户水平取下一题 |
| POST | `/api/puzzles/:id/attempt` | 提交答案，更新分数 |
| GET | `/api/review-queue` | 今日错题复习列表 |

### 7.4 数据表

```
users            (id, name, rating, created_at)
games            (id, user_id, initial_fen, moves[], result, ai_level, user_side, created_at)
move_analysis    (game_id, ply, fen, move, best_move, win_before, win_after, grade, pv[])
explanations     (cache_key, fen, move, level, content_json, model, created_at)
puzzles          (id, fen, solution[], tags[], rating, source)
user_cards       (user_id, card_id, card_type[puzzle|mistake], stability, difficulty, due_at, reps, lapses)
opening_nodes    (zobrist, move, name, eval_cp, games_count)
```

---

## 8. 里程碑（按 1 人开发估算）

| 阶段 | 时长 | 交付 | 验收标准 |
|---|---|---|---|
| M0 骨架 | 1 周 | monorepo、坐标约定、FEN 读写、棋盘 SVG 静态展示 | FEN 往返转换一致 |
| M1 规则 | 2 周 | 着法生成、合法性、胜负判定、中文记谱、本地双人对弈 | perft 1–4 全部通过；记谱测试通过 |
| M2 人机 | 2 周 | WASM 引擎 Worker、人机对战、10 个难度、L2/L3 提示（箭头） | 手机浏览器上流畅对局 |
| M3 复盘 | 2–3 周 | 服务端 Pikafish、整盘分析、胜率曲线、着法评级 | 一盘 60 步的棋 30 秒内出复盘 |
| M4 讲解 | 2 周 | 特征提取、Prompt、结构化输出、校验、缓存、L1 方向提示 | 抽查 50 条讲解，无编造着法 |
| M5 自学 | 3 周 | 题库、错题本 + 间隔重复、开局跟练、学习画像 | 完整「下棋 → 复盘 → 错题 → 复习」闭环 |
| M6 可选 | 4 周+ | 人类风格网络：张量编码、训练、ONNX 浏览器推理 | 低级别 AI 的着法与同水平人类着法吻合率可量化 |

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 规则细节复杂（长将、长捉） | MVP 实现长将判负 + 重复判和；长捉逐步完善；用大量特殊局面做回归测试 |
| LLM 编造着法或看错局面 | 只讲不算；事实全部预先算好；输出正则校验；失败回落到模板讲解 |
| 引擎太强，低级别不自然 | 候选着法加温度随机 + 级别化「漏看」；后续用人类风格网络 |
| WASM 在手机上慢或内存不足 | 单线程降级、减小 Hash；提示和复盘可走服务端兜底 |
| **许可证**：Pikafish、Fairy-Stockfish 均为 GPL-3.0 | 服务端以独立进程调用；若在浏览器分发 WASM，等同于分发二进制，需随附许可证并提供对应源码 |
| 棋谱版权 | 优先使用自对弈数据和用户授权的对局；第三方棋谱库先确认条款 |
| LLM 成本 | 只讲关键步；按 (FEN, 着法, 水平) 缓存；固定 system prompt 使用 prompt caching；短提示用低 effort |

---

## 10. 需要你确认的问题

1. **使用范围**：只给自己用（单机、无账号），还是要做成多用户网站？这决定 M0 是否需要账号系统和数据库。
2. **设备优先级**：主要在电脑还是手机上用？手机优先的话棋盘交互和 WASM 性能要优先打磨。
3. **后端语言**：方案默认 Node.js（和前端共用规则代码）。如果你更熟悉 Python，也可以用 FastAPI，但规则引擎要在 Python 再实现一份或者编译成共用模块。
4. **LLM 预算**：是否已有 Claude API Key？每月大概能接受多少调用费用？这决定讲解的覆盖范围（只讲漏着，还是每个关键步都讲）。
5. **是否需要人人联网对战**：本方案暂不包含，如需要可在 M5 之后加入。

确认后从 M0 / M1 开始实现：先把 `packages/core`（规则引擎 + 记谱 + perft 测试）做扎实，后面所有功能都依赖它。
