# 项目结构说明

## 目录结构

```
NeoEyesTool/
├── backend/                      # 后端服务
│   ├── api/                     # API 路由
│   │   ├── __init__.py
│   │   └── routes.py            # FastAPI 路由定义
│   ├── models/                  # 数据模型
│   │   ├── __init__.py
│   │   └── database.py          # SQLAlchemy 模型
│   ├── services/                # 业务逻辑服务
│   │   ├── __init__.py
│   │   ├── mqtt_service.py      # MQTT 订阅服务
│   │   └── websocket_manager.py # WebSocket 连接管理
│   ├── utils/                   # 工具函数
│   │   ├── __init__.py
│   │   └── yolo_export.py       # YOLO 格式导出
│   ├── examples/                # 示例代码
│   │   └── mqtt_client_example.py # MQTT 客户端示例
│   ├── config.py                # 配置文件
│   ├── main.py                  # 后端入口
│   ├── requirements.txt         # Python 依赖
│   └── .env.example             # 环境变量示例
│
├── frontend/                    # 前端应用
│   ├── public/                  # 静态资源
│   │   └── index.html
│   ├── src/
│   │   ├── components/          # React 组件
│   │   │   ├── AnnotationCanvas.tsx    # Canvas 画布组件
│   │   │   ├── AnnotationCanvas.css
│   │   │   ├── AnnotationWorkbench.tsx # 标注工作台
│   │   │   ├── AnnotationWorkbench.css
│   │   │   ├── ControlPanel.tsx        # 控制面板
│   │   │   ├── ControlPanel.css
│   │   │   ├── ProjectSelector.tsx     # 项目选择器
│   │   │   ├── ProjectSelector.css
│   │   │   ├── ToolsBar.tsx            # 工具栏
│   │   │   └── ToolsBar.css
│   │   ├── hooks/               # 自定义 Hooks
│   │   │   └── useWebSocket.ts  # WebSocket Hook
│   │   ├── App.tsx              # 主应用组件
│   │   ├── App.css
│   │   ├── index.tsx            # 入口文件
│   │   ├── index.css
│   │   └── config.ts            # 配置文件
│   ├── package.json             # Node.js 依赖
│   └── tsconfig.json            # TypeScript 配置
│
├── datasets/                    # 数据集存储目录（自动创建）
│   └── {project_id}/
│       ├── raw/                 # 原始图像
│       └── yolo_export/         # YOLO 导出
│
├── data/                        # 数据库文件（自动创建）
│   └── annotator.db            # SQLite 数据库
│
├── README.md                    # 项目说明
├── INSTALLATION.md              # 安装指南
├── USAGE.md                     # 使用指南
├── PROJECT_STRUCTURE.md         # 本文件
├── .gitignore                   # Git 忽略文件
├── start_backend.sh             # 后端启动脚本
└── start_frontend.sh            # 前端启动脚本
```

## 核心模块说明

### 后端模块

#### 1. API 路由 (`backend/api/routes.py`)

- **项目管理**: 创建、查询、删除项目
- **类别管理**: 创建、查询类别
- **图像管理**: 列出图像、获取图像详情
- **标注管理**: 创建、更新、删除标注
- **WebSocket**: 实时通信端点
- **图像文件服务**: 提供图像文件访问
- **YOLO 导出**: 导出项目为 YOLO 格式

#### 2. 数据模型 (`backend/models/database.py`)

- **Project**: 项目表
- **Image**: 图像表
- **Class**: 类别表
- **Annotation**: 标注表

#### 3. MQTT 服务 (`backend/services/mqtt_service.py`)

- 订阅设备上传的图像
- 解析 MQTT 消息
- 存储图像到文件系统
- 写入数据库
- 通过 WebSocket 通知前端

#### 4. WebSocket 管理器 (`backend/services/websocket_manager.py`)

- 管理 WebSocket 连接
- 按项目分组连接
- 广播消息到项目内所有客户端

#### 5. YOLO 导出 (`backend/utils/yolo_export.py`)

- 坐标归一化转换
- 生成 YOLO 格式标注文件
- 支持 bbox 和 polygon 格式

### 前端模块

#### 1. 标注工作台 (`AnnotationWorkbench.tsx`)

- 主工作台组件
- 管理工具状态、图像状态、标注状态
- 快捷键处理
- 撤销/重做功能

#### 2. Canvas 组件 (`AnnotationCanvas.tsx`)

- 图像显示和缩放
- 画布平移
- 标注绘制（矩形框、多边形、关键点）
- 标注编辑
- 十字准星显示

#### 3. 控制面板 (`ControlPanel.tsx`)

- 标注列表显示
- 类别管理
- 文件导航

#### 4. 工具栏 (`ToolsBar.tsx`)

- 工具切换按钮
- 工具快捷键提示

## 数据流

### 图像上传流程

```
IoT 设备 → MQTT Broker → MQTT Service
                              ↓
                        解析消息
                              ↓
                    存储图像到文件系统
                              ↓
                    写入数据库 (Image 表)
                              ↓
                   WebSocket 通知前端
                              ↓
                      前端刷新图像列表
```

### 标注创建流程

```
用户操作 Canvas → 创建标注对象
                        ↓
              发送 POST 请求到 API
                        ↓
              后端保存到数据库
                        ↓
             前端更新本地状态
                        ↓
             重绘画布显示新标注
```

### YOLO 导出流程

```
用户触发导出 → API 查询项目数据
                        ↓
               构建导出数据结构
                        ↓
             坐标转换（归一化）
                        ↓
            生成 YOLO 格式文件
                        ↓
             保存到导出目录
```

## 技术栈

### 后端

- **FastAPI**: Web 框架
- **SQLAlchemy**: ORM
- **Paho MQTT**: MQTT 客户端
- **WebSockets**: 实时通信
- **Pillow**: 图像处理

### 前端

- **React**: UI 框架
- **TypeScript**: 类型安全
- **Canvas API**: 图像绘制
- **WebSocket API**: 实时通信

## 数据库 Schema

### projects

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String (PK) | 项目 ID (UUID) |
| name | String | 项目名称 |
| description | Text | 项目描述 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

### images

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer (PK) | 图像 ID |
| project_id | String (FK) | 项目 ID |
| filename | String | 文件名 |
| path | String | 相对路径 |
| width | Integer | 图像宽度 |
| height | Integer | 图像高度 |
| status | String | 状态 (UNLABELED/LABELED) |
| source | String | 来源 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

### classes

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer (PK) | 类别 ID |
| project_id | String (FK) | 项目 ID |
| name | String | 类别名称 |
| color | String | 颜色 (HEX) |
| shortcut_key | String | 快捷键 |
| created_at | DateTime | 创建时间 |

### annotations

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer (PK) | 标注 ID |
| image_id | Integer (FK) | 图像 ID |
| class_id | Integer (FK) | 类别 ID |
| type | String | 类型 (bbox/polygon/keypoint) |
| data | Text (JSON) | 标注数据 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

## 扩展建议

1. **用户认证**: 添加用户登录和权限管理
2. **批量操作**: 支持批量删除、批量修改类别
3. **标注模板**: 支持预设的标注模板
4. **导入功能**: 支持从其他格式导入标注
5. **协作功能**: 支持多人同时标注同一项目
6. **标注审核**: 添加标注审核流程
7. **统计分析**: 添加标注进度统计和分析

