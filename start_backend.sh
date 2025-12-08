#!/bin/bash

# 启动后端服务
echo "Starting NeoEyesTool Backend..."

# 获取脚本所在目录（项目根目录）
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 检查虚拟环境
if [ ! -d "backend/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv backend/venv
fi

# 激活虚拟环境
source backend/venv/bin/activate

# 安装依赖
echo "Installing dependencies..."
pip install -r backend/requirements.txt

# 设置 PYTHONPATH 为项目根目录
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# 启动服务（从项目根目录运行）
echo "Starting server..."
python -m backend.main

