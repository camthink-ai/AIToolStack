# 概述

[English](README.md) | [中文](README.zh.md)

AI Tool Stack 是一个面向 [NeoEyes NE301](https://github.com/camthink-ai/ne301) 的端到端边缘 AI 工具套件，涵盖数据采集、标注、训练、量化和部署。可自托管并持续更新。

![Dashboard](img/dashboard.png)
![AI Model Project](img/ai-model-project.png)
![Annotation Workbench](img/Annotation%20tool.png)
![Train model](/img/Train%20model.png)

## 核心功能
- **数据采集与管理**：支持相机通过MQTT图像采集数据并上传至项目空间统一管理；
- **标注工作台**：快捷键驱动的标注，类别管理，数据集导入/导出（COCO / YOLO / 本项目标注ZIP）
- **训练与测试**：基于 YOLO 的训练和模型测试，当前支持yolov8n，未来会持续扩展其他模型
- **量化与部署**：一键导出 NE301 模型包，无需编码，即可在设备上部署你的模型

## 环境要求
- Docker & docker-compose（必需）。请参考[Docker 官方安装指南](https://docs.docker.com/get-docker/) 及 [docker-compose 安装文档](https://docs.docker.com/compose/install/) 进行安装。
- 如需生成 NE301 量化模型包，请提前拉取镜像： 
  ```
  docker pull camthink/ne301-dev:latest
  ```

> **相关项目**：[NE301 - STM32N6 AI 视觉相机](https://github.com/camthink-ai/ne301) - 目标设备固件和开发仓库。

## 快速开始（Docker）
克隆代码仓库：
```bash
git clone https://github.com/camthink-ai/AIToolStack.git
cd AIToolStack
```
Docker部署项目
```bash
docker-compose build
docker-compose up
```
> **注意：现在相关参数已在配置文件中定义。**  
如需修改 `MQTT_BROKER_HOST` 等参数，请直接编辑 `docker-compose.yml` 中调整环境变量。  
请确保该地址能被 NE301 设备访问（通常需填写宿主机实际IP，而不是 `localhost`）。

### 本地开发（可选）
分别运行前端和后端：
```bash
cd frontend && npm install && npm start
cd backend  && pip install -r requirements.txt && uvicorn main:app --reload
```
API 路由位于 `backend/api/routes.py`
前端配置位于 `frontend/src/config.ts`。

## AI Model Project 工作流程
AI Model Project 的推荐使用流程如下：

1. **创建项目**  
   在页面上新建一个 AI 项目，用于后续的数据采集、管理和训练。

2. **配置 NE301 MQTT（服务器 + 主题）**  
   NE301相机推送数据的 MQTT broker 地址和主题（topic），确保数据能够上传到指定项目，详见项目页面中的MQTT信息

3. **采集图像或手动上传**  
   - 推荐使用 NE301 采集现场图像（通过 MQTT 自动上传）。  
   - 也可手动上传本地图片到当前项目，便于补充数据。

4. **数据标注与类别管理**  
   在标注工作台进行数据标注。可新建、编辑类别，支持矩形/多边形等多种标注工具。

5. **模型训练**  
   选择标注完成的数据，点击训练按钮。支持调整参数，如 epoch、batch size、模型类型等。

6. **量化模型/一键导出 NE301 包**  
   训练完成后，一键量化并导出适用于 NE301 的模型包。

7. **部署到 NE301 设备**  
   通过设备管理页面或手动操作，将模型包部署到 NE301。详细指南见 [NE301-快速使用](https://wiki.camthink.ai/docs/neoeyes-ne301-series/quick-start)。
   
**示例拓扑结构：**
```
[NE301 相机] <───> [路由器/局域网] <───> [运行 AI Tool Stack 的电脑/服务器]
```

## 路线图
- **模型中心**：独立量化和可下载的模型库
- **设备管理**：NE101/NE301 连接、数据调试、OTA

## 端口与环境变量
- **默认端口（docker-compose）**：  
  - 后端 / API：`8000`  
  - 前端（容器内 react-scripts）：`3000`（代理/映射，查看日志）  
- **关键环境变量（示例）**：  
  - `API_BASE_URL`：前端 API 基础地址（如 `http://localhost:8000`）  
  - `MQTT_BROKER_HOST`：MQTT broker 主机/IP（如 `localhost` 或 compose 中的 broker 服务名）  
  - `MQTT_BROKER`, `MQTT_TOPIC`：NE301 MQTT 设置
  - `DATASETS_ROOT`：后端数据集存储路径  
  - 更多变量可在 `.env` 或 compose 环境变量覆盖中配置。

## 数据集格式（导入/导出）
- **COCO**：单个 JSON 文件，包含 `images/annotations/categories`。  
- **YOLO**：ZIP 文件，包含 `images/` + `labels/` (.txt)。  
- **项目 ZIP**：`images/` + `annotations/*.json` + 可选的 `classes.json`（id/name/color）。  
- **导出**：YOLO 数据集 ZIP；完整标注 ZIP（images + annotations + classes.json）。

## 故障排除
- **构建失败（前端）缺少资源**：确保所有引用的 CSS/TSX 文件存在（`DatasetImportModal.css` 等）。  
- **端口冲突**：在 `docker-compose.yml` 中调整映射端口。  
- **快速导航时标注层缓慢**：已添加缓存；仍然缓慢 → 减小图像尺寸（JPEG/WebP）或检查网络/CPU。

## 贡献
- 欢迎提交 Issues 和 PR。  
- **代码风格**：遵循仓库默认设置（前端使用 eslint/prettier，后端如已配置则使用 black/flake8）。  
- **分支策略**：fork & PR 或功能分支；请保持更改范围明确，并包含错误修复的复现步骤。

## 许可证
- 请添加您选择的许可证（如 MIT/Apache-2.0）。添加 `LICENSE` 文件并在此处引用。

