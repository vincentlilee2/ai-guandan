# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS frontend

WORKDIR /build
COPY ui/package.json ui/package-lock.json* ./
# esbuild 需要执行安装脚本
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

COPY ui/ ./
RUN npm run build


# ---------- 阶段 2：运行时 ----------
FROM python:3.11-slim

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 后端源码
COPY main.py ./
COPY backend/ ./backend/

# 前端构建产物（FastAPI 直接 mount ui/dist）
COPY --from=frontend /build/dist ./ui/dist

# 运行期产物目录
RUN mkdir -p history errorPlay

ENV GUANDAN_LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/openapi.json',timeout=4).status==200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
