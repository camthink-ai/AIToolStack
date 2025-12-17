# 概述

[English](README.md) | [中文](README.zh.md)

![Dashboard](img/Topology.png)

AI Tool Stack 是一个致力于 [NeoEyes NE301](https://github.com/camthink-ai/ne301) 的端到端边缘 AI 工具套件，涵盖数据采集、标注、训练、量化和部署。

在社区中拥有众多标注工具中为何还需要AI Tool Stack，CamThink专注模型的边缘部署易用性和可持续性，因此我们与传统的Vision AI工作流中按照先思考场景、标注数据、训练模型再考虑边缘部署的工作流的不同，我们先明确边缘部署的硬件基座，在基于硬件来采集物理世界图像并能够快速部署模型来快速解决碎片化场景的AI落地问题，因此此工具配合设备可以完成设备原始图像数据采集、标注、训练、量化、边缘部署，再到图像采集、数据集丰富、重新训练、重新部署的完整循环，以实现单一场景视觉模型的可落地性，我们关注AI落地的实际价值和投入成本，并期望可以加速这一动作。

此工具的模型训练与量化能力依赖于开源库 [ultralytics](https://github.com/ultralytics/ultralytics)，特此表示感谢！

**如果你需要了解此工具如何配合NE301进行完整的工作流程请详细阅读文档「[NE301 and AI Tool Stack Guide ](https://wiki.camthink.ai/docs/neoeyes-ne301-series/application-guide/ai-tool-stack/)**」

![Dashboard](img/dashboard.png)
![AI Model Project](img/ai-model-project.png)
![Annotation Workbench](img/Annotation%20tool.png)
![Train model](/img/Train%20model.png)
![ModelSpace](/img/ModelSpace.png)
![ModelTest](/img/ModelTest.png)

## 核心功能

### AI 模型项目管理
- **数据采集与管理**：支持相机通过 MQTT 自动采集图像数据，并上传至项目空间，实现数据的统一管理。支持多设备接入，实时查看和筛选采集进度。
- **标注工作台**：提供快捷键驱动的高效标注流程，支持目标检测、分类等多种标注类型。内置类别管理，可灵活增删标签，支持数据集的 COCO / YOLO / 本项目标注 ZIP 格式导入与导出。
- **训练与测试**：内置基于 YOLO 架构的模型训练和测试工具。支持设置训练参数、自定义数据集分配、实时查看训练日志及结果报告。训练与量化功能依赖 [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics) 项目，目前支持 yolov8n，未来将持续新增更多模型与算法支持。
- **量化与部署**：集成 NE301 量化与模型打包工具，可一键导出 NE301 设备适用的模型文件包，无需编码即可部署到边缘 AI 设备。支持自动检查兼容性和推理速度评估。

### 模型空间管理（新功能）
- **模型管理**：每次训练和量化的模型都自动保存为独立版本，并可随时回滚或导出，便于追溯和比较。
- **模型测试**：支持不同模型版本的结果测试，助力挑选最佳模型部署至设备。
- **外部模型量化支持**：支持导入已有的 yolo 模型并量化为 NE301 模型资源，无需重新训练，加速你的边缘部署。

## 环境要求
- Docker & docker-compose（必需）。请参考[Docker 官方安装指南](https://docs.docker.com/get-docker/) 及 [docker-compose 安装文档](https://docs.docker.com/compose/install/) 进行安装。
- 如需生成 NE301 量化模型包，请提前拉取镜像： 
  ```
  docker pull camthink/ne301-dev:latest
  ```

> **相关项目**：[NE301 - STM32N6 AI 视觉相机](https://github.com/camthink-ai/ne301) - 设备固件和开发仓库。


## 快速开始（Docker）

克隆代码仓库：
```bash
git clone https://github.com/camthink-ai/AIToolStack.git
cd AIToolStack
```
Docker 部署项目：
```bash
docker-compose build
docker-compose up
```
> **注意：现在相关参数已在配置文件中定义。**  
如需修改 `MQTT_BROKER_HOST` 等参数，请直接编辑 `docker-compose.yml` 中的环境变量。  
请确保该地址能被 NE301 设备访问（通常需填写宿主机实际 IP，而不是 `localhost`）。

### 本地开发（可选）
分别运行前端和后端：
```bash
cd frontend && npm install && npm start
cd backend  && pip install -r requirements.txt && uvicorn main:app --reload
```
API 路由位于 `backend/api/routes.py`  
前端配置位于 `frontend/src/config.ts`。

## 路线图
- ✅ **模型中心**：已有模型可导入和量化支持，支持简单的模型管理
- 🛠️ **设备管理**：NE301数据调试、AI模型远程更新、采集数据上传项目库并支持检测结果标注丰富数据集

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

## 故障排除
- **构建失败（前端）缺少资源**：确保所有引用的 CSS/TSX 文件存在（`DatasetImportModal.css` 等）。  
- **端口冲突**：在 `docker-compose.yml` 中调整映射端口。  
- **快速导航时标注层缓慢**：已添加缓存；仍然缓慢 → 减小图像尺寸（JPEG/WebP）或检查网络/CPU。

## 贡献
- 欢迎提交 Issues 和 PR。  

## 许可证
- 请添加您选择的许可证（如 MIT/Apache-2.0）。添加 `LICENSE` 文件并在此处引用。

