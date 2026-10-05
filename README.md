# 象棋自学

个人使用的中国象棋自学应用，在电脑浏览器里运行。规划中的功能：人机对战、AI 提示、对局复盘讲解、棋谱打谱与猜着训练。

完整技术方案见 [docs/xiangqi-plan.md](docs/xiangqi-plan.md)。

## 当前进度

- [x] **M0 项目骨架**：Python 后端（FastAPI）+ React 前端，一条命令启动
- [x] **M1 规则引擎**：着法生成、将军 / 将死 / 困毙、长将判负、重复局面、自然限着、FEN、中文记谱双向转换；本地自由对弈界面（双方都由你来走）
- [x] **M2 人机对战**：接入 Pikafish 引擎，10 级难度、两级提示、实时引擎分析（评估条 + 候选着法）
- [x] **M3 棋谱库**：导入棋谱（PGN / 中文记谱 / WXF，可含多局，自动识别 GBK 编码）、检索、打谱、局面统计（开局库）、从棋谱任意一步试走或和 AI 对战；自己下完的对局自动存入棋谱库
- [x] **M4 讲解 + 复盘**：整盘复盘（每步评级、准确率、胜率曲线、3 个关键时刻 +「再试一次」）、边下边分析（走一步分析一步）、大模型讲解（Claude / DeepSeek 等 OpenAI 兼容接口，不配置时用模板讲解）、着法白名单校验、讲解缓存、L1 方向提示、30 局面讲解评测
- [x] **M5 棋谱训练**：猜着练习（跟着大师对局猜着、引擎打分）、名局 AI 解读（关键着法的意图 + 分阶段总结）、自动出题、错题本 + 间隔复习（「今日复习 N 题」）

## 运行

需要：Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 20+（只在构建前端时需要）。

```bash
# 1. 构建前端（第一次运行，或前端代码有改动时）
cd frontend
npm install
npm run build

# 2. 启动，会自动打开浏览器 http://127.0.0.1:8000
cd ../backend
uv run xiangqi
```

不用 uv 也可以：在 `backend/` 下 `python -m venv .venv`，激活后 `pip install -e .`，再运行 `python -m xiangqi`。

端口 8000 被占用时（常见原因：之前启动的程序还开着）会自动换用 8001、8002……，终端里会显示实际地址。

**更新代码**：`git pull` 之后，前端有改动要重新 `npm run build`，后端依赖有改动 `uv run` 会自动安装。

没有安装象棋引擎时只能自由对弈；人机对战、提示和引擎分析需要先按下一节安装引擎。

## 安装象棋引擎（Pikafish）

[Pikafish](https://github.com/official-pikafish/Pikafish) 是开源的中国象棋引擎，棋力远超人类。它的神经网络权重文件（`pikafish.nnue`）允许个人非商业使用。

1. **下载**最新版本（一个 `.7z` 压缩包，内含各系统的可执行文件和 `pikafish.nnue`）：
   - GitHub：<https://github.com/official-pikafish/Pikafish/releases>
   - 国内访问 GitHub 慢时：官网 <https://www.pikafish.com> 提供蓝奏云下载
2. **解压**到仓库的 `engines/` 目录（该目录不会提交到仓库），例如 `engines/Pikafish/`。
   - macOS 解压 `.7z`：用「The Unarchiver」，或 `brew install sevenzip` 后运行 `7zz x Pikafish.xxx.7z -oengines/Pikafish`
3. **macOS 需要先解除隔离**（否则系统会拦截从网上下载的程序）：
   ```bash
   xattr -dr com.apple.quarantine engines/Pikafish
   chmod +x engines/Pikafish/Pikafish-MacOS-* engines/Pikafish/*/pikafish* 2>/dev/null
   ```
   新版压缩包里各系统的可执行文件直接放在根目录（如 `Pikafish-MacOS-universal`），旧版在 `MacOS/` 等子目录里。
4. **自动挑选版本**：压缩包里有针对不同 CPU 的多个版本，下面的命令会逐个试运行，找出能在你电脑上运行的最快版本，并打印要写进配置的内容：
   ```bash
   cd backend
   uv run xiangqi-engine-check ../engines/Pikafish
   ```
5. 把打印出的 `[engine]` 配置写进仓库根目录的 `config.toml`，重新运行 `uv run xiangqi`。

如果提示找不到 `pikafish.nnue`：把它复制到可执行文件所在目录，或在 `config.toml` 的 `[engine]` 中设置 `eval_file = "engines/Pikafish/pikafish.nnue"`。

也可以用 [Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish)（大棋盘版本，支持象棋），在 `[engine]` 中设置 `flavor = "fairy-stockfish"`。

## 使用

- **人机对战**：点「新对局」，选择执红或执黑、难度 1–10（1 级启蒙 … 10 级全力）。难度越低，AI 搜索越浅，并且越常走次优着法。各级参数是初始值，觉得不合适可以在 `backend/xiangqi/engine/difficulty.py` 中调整。
- **提示**（由浅到深）：「方向」只指出要注意什么（如「你的车没有保护」「对方有一步杀的威胁」），不说走法，没装引擎也能用；「动哪个子」只高亮该动的棋子；「怎么走」给出具体着法（绿色箭头）、后续变化和走完后的期望得分。每局使用提示的次数会记录下来。
- **每步分析（走一步分析一步）**：在对弈页勾选「每步分析」，每走一步就马上给这步评级（和复盘的评级方法一样），显示走前走后的期望得分和引擎推荐的走法，可以点「讲解这步」；评为缓着及以下的步在着法列表里标出 ?! ? ??。人机对战只分析你自己的着法（分析 AI 的着法等于提示你对方哪里走错了），自由对弈每步都分析。每步大约要 1–2 秒，不影响 AI 走棋。勾选状态会记住。
- **复盘**：下完后点「复盘这局」（没下完也可以在「着法」卡片里点「复盘」），或在打谱页点「开始复盘」。引擎逐步分析整盘棋（每个局面 0.8 秒，100 步约 80 秒），给出：
  - 每步评级：妙着 / 好棋 / 可以 / 缓着 / 失误 / 漏着（着法列表里标 ! ?! ? ??），按「走这步前后期望得分下降了多少」算，阈值见 `backend/xiangqi/training/review.py`；
  - 准确率（0–100）和开局 / 中局 / 残局各自的准确率、胜率曲线（点击跳到那一步）；
  - 3 个关键时刻（损失最大的步）：自动讲解，「再试一次」从那步之前和 AI 重新下。自己的对局只看自己这一方；
  - 打谱时每一步都显示评级和引擎推荐，可以点「讲解这步」。复盘结果存在棋谱库里，下次打开直接显示。
- **引擎分析**：勾选「显示实时分析」后，棋盘左侧出现评估条（红色部分 = 红方期望得分），右侧列出引擎认为最好的 3 步及后续变化，蓝色箭头是当前最佳着法。下棋练习时建议关闭，复盘或研究局面时再打开。
- **悔棋**：人机对战时一次撤回 AI 和你各一步。快捷键 Ctrl/⌘+Z；按 F 翻转棋盘。
- **棋谱库**（页面顶部「棋谱库」）：
  - 导入：在页面上选择棋谱文件；大文件（几万局）用命令行更快：`cd backend && uv run xiangqi-import <文件>`。不合法的对局会被跳过并说明原因（如「第 99 步 A6-I6 不合法：被将军时没有应将」）；同一局（着法和对局信息都相同）重复导入时自动跳过。
  - 检索：按棋手、赛事、开局、结果筛选；也可以粘贴 FEN，找出走到过这个局面的对局。
  - 打谱：点开一局，用 ← → 键（Home / End 跳到开局 / 终局）或按钮逐步回放；旁边的「局面统计」显示库中走到这个局面的对局数和之后各着法的胜负比例；可以打开引擎分析。
  - 「从这里试走」「从这里和 AI 下」：从棋谱的当前局面开始自由摆走或和 AI 对战。
  - 自己下完的对局会自动存入棋谱库（「我的对局」）；没下完的可以点「保存到棋谱库」。
  - 局面统计只索引每局前 40 步（半回合），再往后的局面几乎每局都不同；可在 `config.toml` 的 `[library]` 中调整。
  - **名局解读**：复盘之后点「AI 解读」，推断转折点上每步棋「想干什么」（包括空着法分析：假如对方停一步，这步棋威胁什么），再分开局、中局、残局总结。解读过的步在打谱时显示在那一步下面。
- **训练**（页面顶部「训练」，有到期的题时显示数字）：
  - **猜着练习**：在打谱页点「猜着练习」，选执红或执黑、开头跳过几步。轮到你时在棋盘上走出你的着法，引擎和大师的着法比较打分：一样或不差 3 分，差 3% 以内 2 分，差 10% 以内 1 分，更差 0 分并附讲解；大师着法也不是最佳时会标出来。猜完后显示总分、和大师的吻合率，失分最多的 3 步进错题本。
  - **错题本 + 今日复习**：自己对局复盘里的失误和漏着、猜着失分最多的步、做错的题都会自动进错题本，打谱或每步分析时也可以点「加入错题本」。复习时从当时的局面找出正确着法：做对了间隔变长（1 天、3 天……），做错了 10 分钟后再出（简化的 SM-2）。对弈页和导航上会显示「今日复习 N 题」。
  - **做题**：每盘复盘过的棋都会自动出题——引擎认为「只有一步好棋」的局面（最佳着法比第二名高出 20% 以上）。题目按规则引擎打上杀法、将军、吃子、弃子、捉双等标签，可以按主题做；优先出没做过、难度接近你等级分（Elo）的题。想多出些题，可以批量复盘棋谱库里的对局（每盘约 1 分钟，Ctrl+C 随时停止）：
    ```bash
    cd backend
    uv run xiangqi-puzzles --games 20                    # 随机挑 20 盘没复盘过的
    uv run xiangqi-puzzles --games 10 --opening 中炮对屏风马
    ```

**开发模式**（改前端代码时页面自动刷新）：

```bash
cd backend && uv run xiangqi --no-browser   # 终端 1：后端
cd frontend && npm run dev                  # 终端 2：打开 Vite 显示的地址
```

配置在仓库根目录的 `config.toml`（端口、引擎、自然限着回合数等）。接口文档：启动后访问 `/docs`。

## 大模型讲解（可选）

讲解的原则是「大模型只讲，不算」：着法、胜率、对方的应着、哪个子没保护，都先由引擎和规则引擎算好，大模型只负责把这些事实讲清楚。讲解里出现的每一步着法都会和允许的着法列表核对，编造了着法就让它重写一次，还不行就换成模板讲解，所以界面上不会出现不存在的着法。

不配置大模型也能用：讲解会用模板拼出来（同样的事实，只是没那么自然）。要用大模型，在 `config.toml` 的 `[llm]` 中设置：

- **Claude**：`provider = "claude"`，默认模型 `claude-opus-5-5`；在仓库根目录新建 `.env` 文件，写一行 `ANTHROPIC_API_KEY=sk-ant-...`（也可以用系统环境变量，或 `ant auth login` 登录）。
- **DeepSeek 等 OpenAI 兼容接口**：`provider = "openai_compat"`，在 `[llm.openai_compat]` 中填 `base_url` 和 `model`（按服务商文档），`.env` 里写 `DEEPSEEK_API_KEY=sk-...`。

`.env` 不会提交到仓库，Key 不要写进 `config.toml`。`level` 设置你的水平（如「入门」「中级」），决定讲解的深浅。同一个局面、同一步棋的讲解会缓存在棋谱库里，不会重复收费；每次复盘只自动讲解 3 个关键时刻，其余的步点「讲解这步」才调用。

**换模型或改提示词之前先评测**：固定的 30 个局面（开局漏着、中局战术、残局技巧，来自 Pikafish 自对弈），统计大模型编造着法的次数（应为 0），并生成报告供你给每条讲解打分：

```bash
cd backend
uv run xiangqi-llm-eval            # 会实际调用大模型；报告写到 data/llm-eval/
uv run xiangqi-llm-eval --limit 5  # 先试 5 个
```

## 测试

```bash
cd backend
uv run pytest              # 全部快速测试
uv run pytest -m slow      # perft 深度 4（约 10 秒）
uv run ruff check .

# 用真实引擎跑完整对局（可选；路径按你解压出来的可执行文件填写）
XIANGQI_TEST_ENGINE=../engines/Pikafish/Pikafish-MacOS-universal uv run pytest tests/test_real_engine.py

cd ../frontend
npm run typecheck
```

## 目录

```
backend/xiangqi/core/   规则引擎（纯 Python，无第三方依赖）
  board.py              坐标、棋子编码、预计算表
  movegen.py            着法生成、将军检测、perft
  position.py           走子 / 悔棋、Zobrist 哈希
  rules.py              胜负判定
  fen.py                FEN 读写、局面合法性检查
  notation.py           ICCS ↔ 中文记谱
  features.py           战术特征：攻击与保护、无根子、子力、局面阶段、一步杀
backend/xiangqi/engine/ 象棋引擎接入
  uci.py                UCI 协议适配（Pikafish / Fairy-Stockfish）
  difficulty.py         难度级别与选着
  service.py            引擎进程管理
  check.py              xiangqi-engine-check：检查 / 挑选引擎版本
backend/xiangqi/library/ 棋谱库
  parse.py              棋谱文件解析（编码、多局切分、PGN 标签、着法）
  importer.py           逐步校验着法，ICCS / 中文 / WXF 统一成 ICCS
  db.py                 SQLite 存储、检索、局面索引与统计
  openings.py           开局识别（自动，仅供参考）
  cli.py                xiangqi-import 命令行导入
backend/xiangqi/llm/    大模型讲解
  context.py            讲解上下文：文字棋盘、棋子清单、引擎候选、战术事实、允许的着法
  claude.py             Claude 适配（anthropic SDK，结构化输出）
  openai_compat.py      OpenAI 兼容接口适配（DeepSeek 等）
  validate.py           着法白名单校验
  templates.py          模板讲解（不调用大模型时）
  service.py            讲解流水线：缓存 → 调用 → 校验 → 重试 → 模板兜底
  evaluate.py           xiangqi-llm-eval 讲解评测（评测集 eval_set.json）
backend/xiangqi/training/ 训练
  store.py              训练数据：猜着记录、题库、错题本、等级分
  guess.py / judge.py   猜着练习的打分；用 searchmoves 在同一次搜索里比较两步棋
  puzzles.py            自动出题、主题标签、难度分与等级分
  srs.py                错题本的间隔复习（简化 SM-2）
  annotate.py           名局解读：转折点、空着法威胁、分阶段总结
  collect.py            复盘之后自动出题、收集错题
  cli.py                xiangqi-puzzles 批量复盘出题
  review.py             整盘复盘：引擎分析、每步评级、准确率、关键时刻
  hints.py              L1 方向提示
backend/xiangqi/api/    FastAPI 接口（对局、提示、实时分析 WebSocket、棋谱库、复盘与讲解、边下边分析、
                        名局解读、猜着练习、训练）
backend/scripts/        开发用脚本（生成讲解评测集）
backend/tests/          测试
frontend/src/           React 界面（SVG 棋盘）
docs/                   技术方案
```

## 棋谱来源

目前找到的可用棋谱（第三方数据，仅供个人学习，不要再分发；下载后放到 `data/` 目录，该目录不会提交到仓库）：

- [CGLemon/chinese-chess-PGN](https://github.com/CGLemon/chinese-chess-PGN)：东萍象棋网棋谱仓库 99,813 局、世界象棋联合会 41,743 局，ICCS 格式 PGN，下载链接（Google Drive）在该仓库的 README 里。
- [Kaggle：Online Chinese Chess (Xiangqi)](https://www.kaggle.com/datasets/boyofans/onlinexiangqi)：playOK 网站的 10,000 局快棋，WXF 记法（CSV 文件，目前不能直接导入）。

下载并导入东萍的 99,813 局（约 100 MB，需要能访问 Google Drive；导入约 3 分钟，棋谱库约 190 MB）：

```bash
uvx gdown --folder https://drive.google.com/drive/folders/12Js9Ld6Yixq4RA96j1PeT2QTUJ0-z0OB -O data/dpxq
cd backend
uv run xiangqi-import ../data/dpxq/ICCS/dpxq-99813games.pgns
```

其中有 32 局包含不合法着法（棋谱记录错误），会被跳过并列出原因。
