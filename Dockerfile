# 多阶段构建 Dockerfile
# 第一阶段：构建前端 React 应用
FROM node:18-alpine AS frontend-builder

WORKDIR /app/frontend

# 复制前端依赖文件
COPY frontend/package*.json ./

# 安装前端依赖
RUN npm ci --legacy-peer-deps

# 复制前端源代码
COPY frontend/ ./

# 构建前端应用
RUN npm run build

# 第二阶段：构建后端 Python 应用
FROM python:3.10-slim AS backend-builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制后端依赖文件
COPY backend/requirements.txt ./

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 第三阶段：运行阶段
FROM python:3.10-slim

WORKDIR /app

# 安装运行时依赖（包括 git 用于克隆 NE301 项目，以及 Docker CLI 用于编译 NE301 模型）
RUN apt-get update && apt-get install -y \
    libgomp1 \
    curl \
    git \
    ca-certificates \
    gnupg \
    lsb-release \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgthread-2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# 安装 Docker CLI（用于在容器内执行 docker 命令）
# 注意：此安装需要访问 Docker 官方源，如果网络有问题可能需要配置镜像
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version

# 从构建阶段复制 Python 依赖
COPY --from=backend-builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=backend-builder /usr/local/bin /usr/local/bin

# 复制后端代码
COPY backend/ ./backend/

# 从第一阶段复制前端构建产物
COPY --from=frontend-builder /app/frontend/build ./frontend/build

# 创建必要的目录
RUN mkdir -p datasets data backend/data

# 复制初始化脚本
COPY scripts/init-ne301.sh /usr/local/bin/init-ne301.sh
RUN chmod +x /usr/local/bin/init-ne301.sh

# 设置环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# 暴露端口
EXPOSE 8000

# 启动命令（先初始化 NE301，然后启动应用）
CMD ["sh", "-c", "/usr/local/bin/init-ne301.sh && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
