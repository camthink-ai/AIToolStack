#!/bin/bash
# NE301 项目初始化脚本
# 在容器启动时自动检查并克隆 NE301 项目到主机目录（如果为空）

set -e

NE301_HOST_DIR="/workspace/ne301"  # Docker Compose 挂载的主机目录路径

# 检测是否在 Docker 容器内
if [ -f "/.dockerenv" ]; then
    # 在容器内：检查主机目录（通过挂载）
    if [ -d "$NE301_HOST_DIR" ]; then
        # 主机目录存在（通过 docker-compose volume 挂载）
        echo "[NE301 Init] 检测到主机目录挂载: $NE301_HOST_DIR"
        
        # 检查是否为空目录或不存在关键文件
        if [ ! "$(ls -A $NE301_HOST_DIR 2>/dev/null)" ] || [ ! -d "$NE301_HOST_DIR/Model" ]; then
            echo "[NE301 Init] 主机目录为空或不完整，从 GitHub 克隆..."
            # 如果目录不为空但缺少文件，先清空
            if [ "$(ls -A $NE301_HOST_DIR 2>/dev/null)" ]; then
                rm -rf "$NE301_HOST_DIR"/*
            fi
            git clone https://github.com/camthink-ai/ne301.git "$NE301_HOST_DIR"
            echo "[NE301 Init] 克隆完成"
        else
            echo "[NE301 Init] 主机目录已存在完整项目，跳过克隆"
        fi
    else
        echo "[NE301 Init] 警告：未检测到主机目录挂载 ($NE301_HOST_DIR)"
        echo "[NE301 Init] 请确保 docker-compose.yml 中配置了 ./ne301:/workspace/ne301"
        echo "[NE301 Init] 使用容器内目录作为回退..."
        
        # 回退：使用容器内目录
        NE301_CONTAINER_DIR="/app/ne301"
        if [ ! -d "$NE301_CONTAINER_DIR" ]; then
            echo "[NE301 Init] 克隆到容器内目录..."
            git clone https://github.com/camthink-ai/ne301.git "$NE301_CONTAINER_DIR"
        fi
    fi
else
    # 在主机上：检查项目根目录
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
    NE301_DIR="$PROJECT_ROOT/ne301"
    
    if [ ! -d "$NE301_DIR" ]; then
        echo "[NE301 Init] 在主机上克隆 NE301 项目到: $NE301_DIR"
        git clone https://github.com/camthink-ai/ne301.git "$NE301_DIR"
    else
        echo "[NE301 Init] NE301 项目目录已存在: $NE301_DIR"
    fi
fi

echo "[NE301 Init] 完成"
