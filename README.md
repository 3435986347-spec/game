# 象棋自学

个人使用的中国象棋自学应用，在电脑浏览器里运行。规划中的功能：人机对战、AI 提示、对局复盘讲解、棋谱打谱与猜着训练。

完整技术方案见 [docs/xiangqi-plan.md](docs/xiangqi-plan.md)。

## 当前进度

- [x] **M0 项目骨架**：Python 后端（FastAPI）+ React 前端，一条命令启动
- [x] **M1 规则引擎**：着法生成、将军 / 将死 / 困毙、长将判负、重复局面、自然限着、FEN、中文记谱双向转换；本地自由对弈界面（双方都由你来走）
- [ ] M2 人机对战（接入 Pikafish 引擎）
- [ ] M3 棋谱库（导入、打谱、局面统计）
- [ ] M4 大模型讲解 + 复盘
- [ ] M5 猜着练习、名局解读、错题本

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

**开发模式**（改前端代码时页面自动刷新）：

```bash
cd backend && uv run xiangqi --no-browser   # 终端 1：后端
cd frontend && npm run dev                  # 终端 2：打开 Vite 显示的地址
```

配置在仓库根目录的 `config.toml`（端口、自然限着回合数等）。接口文档：启动后访问 `/docs`。

## 测试

```bash
cd backend
uv run pytest              # 全部快速测试
uv run pytest -m slow      # perft 深度 4（约 10 秒）
uv run ruff check .

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
backend/xiangqi/api/    FastAPI 接口
backend/tests/          测试
frontend/src/           React 界面（SVG 棋盘）
docs/                   技术方案
```

## 棋谱来源

目前找到的可用棋谱（第三方数据，仅供个人学习，不要再分发；下载后放到 `data/` 目录，该目录不会提交到仓库）：

- [CGLemon/chinese-chess-PGN](https://github.com/CGLemon/chinese-chess-PGN)：东萍象棋网棋谱仓库 99,813 局、世界象棋联合会 41,743 局，ICCS 格式 PGN，下载链接（Google Drive）在该仓库的 README 里。
- [Kaggle：Online Chinese Chess (Xiangqi)](https://www.kaggle.com/datasets/boyofans/onlinexiangqi)：playOK 网站的 10,000 局快棋，WXF 记法。

棋谱导入功能在 M3 阶段实现。
