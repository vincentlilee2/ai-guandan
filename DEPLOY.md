# 部署指南 · Guandan AI（掼蛋独立版）

本项目为 FastAPI 后端 + React 前端（Vite 构建为静态 `dist`，由 uvicorn 直接托管）。

部署方式：**Docker（推荐，一键拉起）** 或 **裸机（Python + Node）**。两种方式均已在 Ubuntu 24.04 验证。

---

## 一、前置条件

| 组件 | 要求 | 说明 |
|------|------|------|
| Docker | 24+ / docker-compose v2 | `docker compose` 子命令 |
| 或裸机 | Python 3.11+、Node 20+、npm | 不装 Docker 时用手动方式 |
| git | 任意 | 拉取私有仓库需带 PAT |
| 云端 AI | DeepSeek / OpenAI / Gemini 等 API Key | 本项目走云端 AI（默认已配 DeepSeek） |

> 本项目**默认不连 MySQL**（`pymysql` 已拆到 `requirements-mysql.txt`），开箱即跑，无需数据库。
> 只有需要使用 MySQL 持久化对局积分时才装 `requirements-mysql.txt` 并配库。

---

## 二、Docker 部署（推荐）

### 1. 克隆仓库（公开仓库）

```bash
cd /opt
sudo git clone https://github.com/vincentlilee2/ai-guandan.git
sudo chown -R $USER:$USER guandan
cd guandan
```

> 公开仓库直接克隆即可，无需 PAT。

### 2. 配置环境变量

```bash
cp .env.example .env
vim .env
```

**必填项（4 个 BOT 各一套，漏填会 AI 调用失败 → 本地兜底，能跑但变笨）：**

```ini
# 三个 AI 对手（默认值已指向 DeepSeek，替换 API Key 即可）
PARTNERBOT_LLM_API_KEY=sk-xxxx
PARTNERBOT_LLM_BASE_URL=https://api.deepseek.com
PARTNERBOT_LLM_MODEL=deepseek-chat
PARTNERBOT_LLM_TEMPERATURE=0.3

LEFTBOT_LLM_API_KEY=sk-xxxx
LEFTBOT_LLM_BASE_URL=https://api.deepseek.com
LEFTBOT_LLM_MODEL=deepseek-chat
LEFTBOT_LLM_TEMPERATURE=0.3

RIGHTBOT_LLM_API_KEY=sk-xxxx
RIGHTBOT_LLM_BASE_URL=https://api.deepseek.com
RIGHTBOT_LLM_MODEL=deepseek-chat
RIGHTBOT_LLM_TEMPERATURE=0.3

# 复盘教练 / 错误分析（也可复用 DeepSeek）
COACH_OPENAI_API_KEY=sk-xxxx
COACH_MODEL_NAME=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=sk-xxxx
```

**按服务器资源调整（默认 200 并发对局，2vCPU/3.6G 内存请调小）：**

```ini
GAME_MAX_ACTIVE_SESSIONS=20        # 3.6G 内存建议 20~50
GAME_SESSION_TTL_SECONDS=7200      # 空闲对局回收秒数
GAME_CLEANUP_INTERVAL_SECONDS=300
GUANDAN_DEBUG_AI=0                 # 1 = 打印 AI 完整思考过程（调试用）
```

> 也可通过 `docker-compose.yml` 的 `environment` 覆盖，无需改 `.env`。

**功能开关（默认开通会员登录，按需调整）：**

```ini
ENABLE_MEMBER_LOGIN=1    # 会员注册/登录（默认开通；0 = 关闭，隐藏登录入口）
ENABLE_AI_COACH=1        # 1 = 开通复盘 AI 教练
```

> 开源单机版：默认关闭会员登录。如需对接创作人官网会员系统，设 `ENABLE_MEMBER_LOGIN=1` + `MEMBER_SERVER_URL=https://guandan.mgarden.org.cn`（注册/登录/积分/胜率同步到官网）。

### 会员对接官网（guandan.mgarden.org.cn）

会员系统是创作人官网的服务器部署（同一代码、官网开启 `ENABLE_MEMBER_LOGIN=1`）。开源版默认本地单机（账号存本地）；显式开启后才转发官网。

- **官网实例**（创作人服务器，账号权威）：跑「本地模式」——`IS_OFFICIAL_SERVER=1`（官方实例标记，强制本地账号模式，绝不把会员请求转发出去）+ `MEMBER_SERVER_URL=`（留空），账号存服务器 `userdata/`（Docker 挂载卷，重启不丢）。两个防回环保险（官方标记 + 自引用检测）杜绝「远程转发绕一圈打回自己」的递归。
- **本地/各分发实例**：默认本地单机模式（`ENABLE_MEMBER_LOGIN=0`）。如需连官网：设 `ENABLE_MEMBER_LOGIN=1` + `MEMBER_SERVER_URL=https://guandan.mgarden.org.cn`，注册/登录由官网签发 token，本地只保存登录态并转发官网记账。
- **先升级官网再分发**：官网需先更新本代码（含 `/api/member/play-record`、`/api/member/scores` 两个上报接口）并重启，本地实例才能正常上报局数/得分。
- **本地不限制实际玩牌局数**：游客/会员均可无限玩；`/api/auth/me` 返回的 `plays_today`/`total_scores` 为服务器权威记录，仅作展示。
- **Git 安全**：`users.json`/`sessions.json`/`plays.json` 与 `userdata/` 均已加入 `.gitignore`，且远程模式下本地根本不产生注册数据，不会误传账号信息到 GitHub。

### 3. 构建并启动

```bash
docker compose up -d --build
```

- 端口 **8001**（容器内 `0.0.0.0:8001`，宿主机 `8001:8001`）
- `history/` `errorPlay/` 挂载到宿主机，重启不丢
- 会员注册/登录数据（`users.json`/`sessions.json`/`plays.json`）通过 `./userdata` 挂载持久化，重启/重建容器不丢账号
- `restart: unless-stopped` 自动拉起

### 4. 验证

```bash
docker compose ps
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/
# 预期输出: 200
docker compose logs -f        # 看启动日志，确认无 Traceback
```

浏览器打开 `http://<服务器IP>:8001` 即可游玩。

---

## 三、裸机部署（不装 Docker）

```bash
cd /opt && sudo git clone https://github.com/vincentlilee2/ai-guandan.git
cd guandan && sudo chown -R $USER:$USER .

# 1. 后端
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env && vim .env   # 填 API Key（同上）

# 2. 前端构建（uvicorn 直接托管 dist，无需单独前端服务）
cd ui
npm ci
npm run build
cd ..

# 3. 启动（用 systemd 托管，见第四节）
./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001
```

> 注意：Hermes 终端会注入 `PYTHONPATH` 污染 venv，手动跑时需 `env -u PYTHONPATH ./venv/bin/uvicorn ...`。

---

## 四、systemd 托管（裸机，开机自启 + 崩溃重启）

```ini
# /etc/systemd/system/guandan.service
[Unit]
Description=Guandan AI
After=network.target

[Service]
WorkingDirectory=/opt/guandan
ExecStart=/opt/guandan/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001
Environment=PYTHONPATH=
Restart=always
User=<你的用户>

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now guandan
sudo systemctl status guandan
```

---

## 五、nginx 反代到子域名（推荐，配合已有 nginx）

服务器若已用 nginx 托管域名（如 `mgarden.org.cn`），加子域名反代 8001：

```nginx
# /etc/nginx/sites-available/guandan.<你的域名>
server {
    listen 80;
    server_name guandan.<你的域名>;

    # JSON/文本 gzip 压缩（对局 state 响应 141KB → ~20KB，传输+解析都轻）
    gzip on;
    gzip_min_length 1k;
    gzip_comp_level 5;
    gzip_types application/json text/plain text/css application/javascript image/svg+xml;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # 前端使用 /stream (SSE) 实时推送，需放宽超时
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/guandan.<你的域名> /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

> ⚠️ **必须放宽 `proxy_read_timeout`**：前端有 SSE 长轮询（复盘/实时推送），默认 60s 会导致连接中断。
> HTTPS 用 `certbot --nginx -d guandan.<你的域名>` 自动签发。

---

## 六、腾讯云 / 国内环境注意

1. **安全组**：若直接暴露 `:8001`，需在云控制台开放 8001 端口；用 nginx 子域名则只需 80/443（通常已开）。
2. **Docker Hub 镜像拉取慢**：`node:20-alpine` / `python:3.11-slim` 从 Docker Hub 拉可能超时。配置镜像加速：
   ```bash
   # /etc/docker/daemon.json
   { "registry-mirrors": ["https://docker.m.daocloud.io"] }
   sudo systemctl restart docker
   ```
3. **npm 安装慢**：`npm config set registry https://registry.npmmirror.com`
4. **内存**：服务器 3.6G，本项目运行时很轻；但 `GAME_MAX_ACTIVE_SESSIONS` 默认 200 偏高的，按上面改 20~50。

---

## 七、Redis 持久化（可选，重启不丢对局）

默认对局状态全在进程内（memory），服务重启（systemd `Restart=always`、Docker 重建）会清空所有进行中的对局。
可选开启 Redis 后，对局与 meta 会持久化到 Redis，重启后玩家刷新页面即可恢复继续。

### 1. 安装 Redis

```bash
# Ubuntu / 裸机
sudo apt-get update && sudo apt-get install -y redis-server
sudo systemctl enable --now redis-server

# Docker（独立容器，`docker compose` 亦可加一个 redis service）
docker run -d --name redis --restart unless-stopped -p 6379:6379 redis:7-alpine
```

### 2. 配置 .env

```ini
GAME_STORE_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
```

### 3. 说明与限制

- 仍是**单 worker**（uvicorn 不带 `--workers`），Redis 的定位是**重启后对局可恢复**，不是多 worker 横向扩展。
- 实时推送（SSE 事件总线）仍在进程内：重启会断开已订阅的 SSE 连接，客户端会自动重连并收到最新快照。
- 后端启动时若 Redis 连不上（未装 redis-py / ping 失败），会**自动回退到 memory** 并打日志，不影响服务启动。
- 不配置 `GAME_STORE_BACKEND` 时默认走 memory，行为与之前完全一致。

---

## 八、日常运维

```bash
# 查看日志
docker compose logs -f                 # Docker
journalctl -u guandan -f               # systemd

# 更新代码
cd /opt/guandan && git pull
docker compose up -d --build           # Docker 重新构建
# 或裸机: cd ui && npm run build && sudo systemctl restart guandan

# 修改 .env 后
docker compose up -d                   # env_file 自动重载
# 或裸机: sudo systemctl restart guandan

# 完全停止
docker compose down
```

---

## 九、故障排查

| 现象 | 原因 / 处理 |
|------|------|
| 打开页面空白 | `ui/dist` 未生成 → Docker 重新 `up -d --build`；裸机重新 `npm run build` |
| AI 出牌很笨/秒出 | `.env` 里 API Key 漏填 → 走了本地兜底策略，补全 Key 重启 |
| 复盘/实时更新卡住 | nginx `proxy_read_timeout` 太小，按第五节放宽 |
| 端口冲突 | 8001 被占用 → `lsof -i :8001` 查杀，或改 `docker-compose.yml` 映射 |
| 容器不停重启 | `docker compose logs` 看 Traceback；多为 `.env` 格式错误 |

---

## 十、文件清单（仓库已含，无需额外补）

```
main.py                  # FastAPI 入口（托管 ui/dist + /api）
Dockerfile               # 多阶段：Node 构建前端 + Python 运行时
docker-compose.yml       # 一键编排
requirements.txt         # 后端依赖（不含 MySQL）
.env.example             # 环境变量模板
backend/                 # 游戏引擎 + AI 客户端 + 策略
ui/src/                  # 前端源码（构建后输出 ui/dist）
```

> `ui/dist/` 是构建产物，**不进 git**（已在 .gitignore）。Docker 构建阶段会自动 `npm run build` 生成，裸机需手动 build。
