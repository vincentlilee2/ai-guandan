# 贡献指南 (CONTRIBUTING)

感谢你对 **Guandan AI** 感兴趣！这是一个单机/小范围自娱的掼蛋 AI 项目，
欢迎 PR 与 Issue。下面几条约定能让你的改动更快被合并。

## 开发环境

```bash
# 后端（Python 3.10+，注意本机 Shell 可能注入 PYTHONPATH，需清空）
python -m venv venv
env -u PYTHONPATH ./venv/bin/pip install -r requirements.txt
# 如需接 MySQL 持久化分数（可选）：
env -u PYTHONPATH ./venv/bin/pip install -r requirements-mysql.txt

# 前端
cd ui && npm install && npm run build

# 一键启动（后端 8002 + 前端热更新 3012）
./start_dev.sh
```

> ⚠️ 本仓库测试/运行都依赖干净的 venv。若你 Shell 里 `PYTHONPATH` 指向了
> Hermes 等外部包，请用 `env -u PYTHONPATH` 前缀运行 pytest / uvicorn，否则
> 会 import 到错误的包。

## 提交前检查

```bash
# 后端测试（无需联网/外部服务，自带 uvicorn 子进程）
env -u PYTHONPATH ./venv/bin/pytest -p no:cacheprovider

# 前端 lint（应保持 0 errors）
cd ui && npx eslint .

# 前端构建
npm run build
```

- 测试全绿、lint 0 errors 是合并前提。
- 新增功能请补充 pytest 用例（纯函数模块 `rules.py` / `scoring.py` 极易测）。

## 代码约定

- **AI 配置命名**：优先用 `*_LLM_*`（如 `PARTNERBOT_LLM_API_KEY`），旧的
  `*_GEMINI_*` 仍向后兼容，请勿在 PR 中强行删除旧名。
- **状态存储**：对局状态走 `GameStore` 抽象（`backend/game_store.py`），不要
  直接加全局字典；多 worker 场景需 Redis 实现时扩展该接口，不要在 `main.py`
  散落存储。
- **实时推送**：前端状态更新优先 SSE（`/api/{id}/stream`）；轮询仅作降级兜底。
- **密钥/隐私**：`.env` 与任何真实 key **不得入库**；示例见 `.env.example`。
  个人知识/笔记请写在自己的 Obsidian，不要提交到本项目。

## 架构边界（改动前请先读）

- 后端 `main.py`：FastAPI 路由层（全 async）。
- `backend/game_engine.py`：掼蛋规则 + 回合驱动（async 版 `trigger_ai_turn_async`）。
- `backend/ai_client.py`：大模型调用（同步 + 异步并存）。
- `backend/rules.py` / `scoring.py`：纯函数，最易单测。
- 前端 `ui/src/App.jsx`：当前仍是单体组件，拆分进行中，**改 UI 请局部小步提交**。

## Issue / PR 模板

- Bug 报告请用模板，附**可复现步骤** + 环境信息。
- 大改动建议先开 Issue 讨论方向，避免返工。

## 许可证

本项目采用 **Copyright (c) 2026 vincentlilee2** 自定的授权条款（见 `LICENSE`）：允许个人非商业使用与分发（Copyleft：分发须保持开源并保留署名），禁止商业用途（SaaS / 付费 / 企业内使用等），商业使用须获书面授权；官方平台数据上传视为例外。提交即表示你同意你的贡献在相同条款下发布。