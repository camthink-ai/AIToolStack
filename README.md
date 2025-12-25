# Overview

[English](README.md) | [中文](README.zh.md)

![Topological](img/Topology.png)

**AI Tool Stack** is an end-to-end AI toolset designed for [NeoEyes NE301/NE101](https://github.com/camthink-ai/ne301) and other industry edge devices, covering the entire workflow from data collection, annotation, training, quantization, to deployment, significantly improving the efficiency and reliability of visual model implementation.

Why choose AI Tool Stack? While current mainstream Vision AI toolchains focus more on large-scale cloud training and internet data mining, **CamThink is committed to** making **AI implementation on edge hardware automated, on-demand, iterative, and cost-effective**. We innovatively adopt the philosophy of "**hardware-driven data closed-loop + integrated production deployment**", deeply integrating **device image auto-collection → label annotation → model training/quantization → deployment to edge devices**, supporting continuous model optimization and rapid iteration, greatly solving the high-cost pain points of small-scale specialized visual models in fragmented scenarios.

The model training and quantization capabilities of this tool depend on the open-source library [ultralytics](https://github.com/ultralytics/ultralytics), special thanks!

**If you need to understand how this tool works with NE301 for a complete workflow, please read the documentation in detail: "[NE301 and AI Tool Stack Guide](https://wiki.camthink.ai/docs/neoeyes-ne301-series/application-guide/ai-tool-stack/)**"

<div align="center">

| Dashboard | Project Management | Annotation Workbench |
|:---:|:---:|:---:|
| ![Dashboard](img/dashboard.png) | ![AI Model Project](img/Project.png) | ![Annotation Workbench](img/Annotation%20tool.png) |

| Model Training | Model Space | Model Testing |
|:---:|:---:|:---:|
| ![Train model](/img/Train%20model.png) | ![ModelSpace](/img/ModelSpace.png) | ![ModelTest](/img/ModelTest.png) |

| Device Management | System Settings |
|:---:|:---:|
| ![DeviceManage](/img/DeviceManage.png) | ![SystemSettings](/img/SystemSettings.png) |

</div>

## Core Features

### AI Model Projects
- **Data Collection & Management**: Supports automatic image data collection from cameras via MQTT and upload to project space for unified data management. Supports multi-device access, real-time viewing and filtering of collection progress.
- **Annotation Workbench**: Provides shortcut-driven efficient annotation workflows, supporting multiple annotation types such as object detection and classification. Built-in class management for flexible label addition and deletion, supports dataset import and export in COCO / YOLO / project annotation ZIP formats.
- **Training & Testing**: Built-in YOLO architecture-based model training and testing tools. Supports setting training parameters, custom dataset allocation, real-time viewing of training logs and result reports. Training and quantization features depend on [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics) project. Currently supports yolov8n, with more models and algorithm support to be added in the future.
- **Quantization & Deployment**: Integrated NE301 quantization and model packaging tools, enabling one-click export of model file packages suitable for NE301 devices, deployable to edge AI devices without coding. Supports automatic compatibility checking and inference speed evaluation.
- **Bind Data Sources (New)**: Supports binding CamThink NeoEyes NE101/NE301 to projects, device image data can be seamlessly transmitted to projects for annotation

### Model Space
- **Model Management**: Each trained and quantized model is automatically saved as an independent version and can be rolled back or exported at any time for traceability and comparison.
- **Model Testing**: Supports result testing of different model versions to help select the best model for deployment to devices.
- **External Model Quantization Support**: Supports importing existing YOLO models and quantizing them into NE301 model resources without retraining, accelerating your edge deployment.

### Device Management (New Feature)
- **NE101/NE301 Device Support**: Devices can connect to AIToolStack, detect device online/offline status in real-time, and perform basic information and status management.
- **Device-Project Binding**: Devices can be bound to AI model projects, and images collected by devices are automatically categorized and pushed to corresponding project spaces, facilitating batch training and dataset organization.
- **Device Information & Traceability**: Manage device names, and query device historical collection and reporting data for data traceability and convenient retrieval.

### System Settings (New Feature)
- **Multi-MQTT Broker Management**: The system supports subscribing to and managing multiple external MQTT Brokers simultaneously, meeting multi-scenario data collaboration needs.
- **MQTTS Encryption Protocol Support**: The built-in MQTT Broker now supports MQTTS protocol, users can configure Broker parameters (such as certificates, ports, permissions, etc.) in the interface.
- **MQTT Certificate Management**: New certificate management module, supports import, generation, and one-click switching of multiple sets of MQTT certificates to ensure secure access between devices and servers.

---

## Requirements

- **Docker & docker-compose (Required)**  
  Please refer to [Docker official installation guide](https://docs.docker.com/get-docker/) and [docker-compose guide](https://docs.docker.com/compose/install/) for deployment.
- **NE301 Quantization Model Package Generation**  
  Need to pre-pull the quantization environment image:
  ```
  docker pull camthink/ne301-dev:latest
  ```
- **Recommended Supported Devices**  
  - Deploy AIToolStack on local host or server
  - Use CamThink NeoEyes NE101/NE301 as data collection devices

---

## Quick Start (Docker)

1. Clone the repository
   ```bash
   git clone https://github.com/camthink-ai/AIToolStack.git
   cd AIToolStack
   ```

2. Deploy with Docker
   ```bash
   docker-compose build
   docker-compose up
   ```

   > **Note: Main parameters are defined in the configuration file. To customize `MQTT_BROKER_HOST`, etc., please edit the environment variables in `docker-compose.yml` and ensure the address is accessible by devices (usually use the host machine's actual IP address, not `localhost`).**

### Local Development (Optional)

If you need separate development debugging (recommended for users familiar with frontend/backend development), you can run the frontend and backend separately:

```bash
cd frontend  && npm install && npm start
cd backend   && pip install -r requirements.txt && uvicorn main:app --reload
```

- Backend API routes are in `backend/api/routes.py`
- Frontend service configuration is in `frontend/src/config.ts`

---

## Roadmap

- 🚀 **Auto Annotation**: Auto annotation backend/frontend development has started, implementing model labeled inference + manual review closed-loop
- 🤖 **AI Inference Support**: Frontend collection real-time push to backend, API inference results automatically written back to annotation database (main chain verified)
- 📡 **Device Integration**: Support NE301 OTA remote streaming, remote model package synchronization and switching (v1.2 planned)
- 🔄 **Model Compatibility**: Already compatible with YOLOv8, continuously expanding yolov10/12/Segment/and more
- 🧑‍💻 **Multi-user/Permissions** (Planned): Will support multi-role accounts and resource permission isolation management in the future

---

## Ports & Environment Variables

- **Default Ports (docker-compose)**:
  - API (Backend): `8000`
  - Frontend React: `3000` (in container, proxied to backend)
- **Main Environment Variables (Customizable)**:
  - `API_BASE_URL`: Frontend API address (usually `http://localhost:8000`)
  - `MQTT_BROKER_HOST`: MQTT Broker service hostname or IP (if devices need network access, specify the host machine's actual IP)
  - `MQTT_BROKER`, `MQTT_TOPIC`: NE301 MQTT configuration
  - `DATASETS_ROOT`: Backend dataset directory (recommended to mount for persistence)
  - Other detailed variables can be overridden through `.env` file or docker-compose environment

---

## Contributing & Support

Welcome to participate in project collaboration and feature proposals through Issues and PRs!

## License

This project is licensed under [MIT License](./LICENSE), free to use and develop, please cite the original source when referencing.
