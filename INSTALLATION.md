# 安装指南

## 系统要求

- Python 3.8+
- Node.js 16+
- MQTT Broker (可选，用于 IoT 集成)

## 安装步骤

### 1. 后端安装

```bash
cd backend

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 前端安装

```bash
cd frontend

# 安装依赖
npm install
```

### 3. 配置

#### 后端配置

复制 `.env.example` 为 `.env` 并修改配置：

```bash
cd backend
cp .env.example .env
```

编辑 `.env` 文件，配置 MQTT Broker 地址（如果需要）：

```env
MQTT_BROKER=localhost
MQTT_PORT=1883
```

#### 前端配置

前端 API 地址在 `frontend/src/config.ts` 中配置，默认指向 `http://localhost:8000`。

### 4. 启动 MQTT Broker（可选）

如果使用 Docker：

```bash
docker run -d -p 1883:1883 -p 9001:9001 eclipse-mosquitto
```

或使用 EMQX：

```bash
docker run -d --name emqx -p 1883:1883 -p 8083:8083 -p 8084:8084 -p 8883:8883 -p 18083:18083 emqx/emqx
```

### 5. 启动服务

#### 后端

```bash
cd backend
source venv/bin/activate  # 或在 Windows 上: venv\Scripts\activate
python main.py
```

后端服务将在 `http://localhost:8000` 启动。

#### 前端

```bash
cd frontend
npm start
```

前端应用将在 `http://localhost:3000` 启动。

### 6. 使用启动脚本

或者使用提供的启动脚本：

```bash
# 终端 1 - 启动后端
chmod +x start_backend.sh
./start_backend.sh

# 终端 2 - 启动前端
chmod +x start_frontend.sh
./start_frontend.sh
```

## 验证安装

1. 访问 `http://localhost:3000` 应该看到项目选择界面
2. 创建新项目
3. 通过 MQTT 上传图像（或手动上传）进行测试

## 故障排除

### 后端无法启动

- 检查 Python 版本：`python3 --version`
- 检查端口 8000 是否被占用
- 查看后端日志输出

### 前端无法连接后端

- 确认后端服务正在运行
- 检查 `frontend/src/config.ts` 中的 API 地址
- 查看浏览器控制台的错误信息

### MQTT 连接失败

- 确认 MQTT Broker 正在运行
- 检查防火墙设置
- 验证 `backend/.env` 中的 MQTT 配置

## 下一步

查看 [README.md](README.md) 了解使用说明和功能特性。

