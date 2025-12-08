# NeoEyesTool - 图像标注工具与 IoT 集成系统

一个支持 IoT 设备实时图像上传的现代化图像标注工具，专为 YOLO 训练数据准备而设计。

## 功能特性

### 标注工作台
- **三栏式布局**：工具栏 + 画布区 + 控制面板
- **多种标注工具**：矩形框、多边形、关键点
- **高效交互**：键盘快捷键、鼠标操作优化
- **实时预览**：十字准星、拖拽预览、视觉反馈

### IoT 集成
- **MQTT 通信**：支持设备端实时上传图像
- **自动入库**：接收后自动存储并标记状态
- **实时通知**：WebSocket 推送新图像到达

### 数据导出
- **YOLO 格式**：标准 YOLOv5/v8/v10/v11 格式
- **精确转换**：归一化坐标，支持检测和分割任务
- **批量导出**：支持项目级别批量导出

## 技术栈

- **后端**：Python + FastAPI + SQLite
- **前端**：React + TypeScript + Canvas API
- **通信**：MQTT (paho-mqtt) + WebSocket
- **存储**：文件系统 + SQLite 数据库

## 项目结构

```
NeoEyesTool/
├── backend/              # 后端服务
│   ├── api/             # API 路由
│   ├── mqtt/            # MQTT 订阅服务
│   ├── models/          # 数据模型
│   ├── services/        # 业务逻辑
│   └── utils/           # 工具函数
├── frontend/            # 前端应用
│   ├── src/
│   │   ├── components/  # React 组件
│   │   ├── hooks/       # 自定义 Hooks
│   │   └── utils/       # 工具函数
│   └── public/
├── datasets/            # 数据集存储目录
└── docs/               # 文档

```

## 快速开始

### 环境要求

- Python 3.8+
- Node.js 16+
- MQTT Broker (EMQX 或 Mosquitto)

### 安装

```bash
# 后端依赖
cd backend
pip install -r requirements.txt

# 前端依赖
cd frontend
npm install
```

### 配置

1. 配置 MQTT Broker 地址（`backend/config.py`）
2. 配置数据库路径（`backend/config.py`）

### 运行

```bash
# 启动后端服务
cd backend
python main.py

# 启动前端开发服务器
cd frontend
npm start
```

## 使用说明

### MQTT 上传图像

发布消息到 Topic: `annotator/upload/{project_id}`

```json
{
  "req_id": "uuid_v4_string",
  "device_id": "camera_01",
  "timestamp": 1717488000,
  "image": {
    "filename": "img_20240604_001.jpg",
    "format": "jpg",
    "encoding": "base64",
    "data": "base64_encoded_string"
  }
}
```

### 快捷键

- `R`: 矩形框工具
- `P`: 多边形工具
- `V`: 选择/移动工具
- `A` / `←`: 上一张图片
- `D` / `→`: 下一张图片
- `Space + 拖拽`: 平移画布
- `Del`: 删除选中标注
- `Ctrl+Z`: 撤销
- `Ctrl+Shift+Z`: 重做
- `H`: 隐藏/显示标注
- `1-9`: 快速切换类别

## License

MIT

