#!/bin/bash

# 启动前端服务
echo "Starting NeoEyesTool Frontend..."

cd frontend

# 检查 node_modules
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install --legacy-peer-deps
fi

# 启动开发服务器
echo "Starting development server..."
npm start

