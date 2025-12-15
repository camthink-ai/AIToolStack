# Overview

[English](README.md) | [中文](README.zh.md)

AI Tool Stack is an end-to-end edge-AI toolkit for [NeoEyes NE301](https://github.com/camthink-ai/ne301), covering data collection, labeling, training, quantization, and deployment. It can be self-hosted and continuously updated.

![Dashboard](img/dashboard.png)
![AI Model Project](img/ai-model-project.png)
![Annotation Workbench](img/Annotation%20tool.png)
![Train model](/img/Train%20model.png)

## Key Features
- **Data ingest & management**: Camera image collection via MQTT, uploading data to project space for unified management.
- **Annotation workbench**: Shortcut-driven labeling, class management, dataset import/export (COCO / YOLO / project annotation ZIP).
- **Training & testing**: YOLO-based training and model testing. Currently supports YOLOv8n, with more models to be added in the future.
- **Quantization & deployment**: One-click NE301 model package export. Deploy your model on devices without coding.

## Requirements
- Docker & docker-compose (required). Please refer to [Docker official installation guide](https://docs.docker.com/get-docker/) and [docker-compose installation documentation](https://docs.docker.com/compose/install/) for installation.
- If you need to generate the NE301 quantization model package, please pull the image in advance: 
  ```
  docker pull camthink/ne301-dev:latest
  ```

> **Related Project**: [NE301 - STM32N6 AI Vision Camera](https://github.com/camthink-ai/ne301) - The target device firmware and development repository.

## Quick Start (Docker)
Clone the repository:
```bash
git clone https://github.com/camthink-ai/AIToolStack.git
cd AIToolStack
```
Deploy with Docker:
```bash
docker-compose build
docker-compose up
```
> **Note: Configuration parameters are now defined in the configuration file.**  
> To modify parameters such as `MQTT_BROKER_HOST`, please edit the environment variables in `docker-compose.yml`.  
> Ensure the address is accessible by NE301 devices (usually use the host machine's actual IP address, not `localhost`).

### Local Development (optional)
Run frontend and backend separately:
```bash
cd frontend && npm install && npm start
cd backend  && pip install -r requirements.txt && uvicorn main:app --reload
```
API routes live in `backend/api/routes.py`  
Frontend config is in `frontend/src/config.ts`.

## AI Model Project Workflow
The recommended workflow for AI Model Project is as follows:

1. **Create a project**  
   Create a new AI project on the page for subsequent data collection, management, and training.

2. **Configure NE301 MQTT (server + topic)**  
   Configure the MQTT broker address and topic for NE301 camera data push, ensuring data can be uploaded to the specified project. See MQTT information on the project page for details.

3. **Capture images or upload manually**  
   - Recommended: Use NE301 to capture on-site images (automatically uploaded via MQTT).  
   - Alternatively: Manually upload local images to the current project to supplement data.

4. **Data annotation and class management**  
   Perform data annotation in the annotation workbench. Create and edit classes, with support for rectangle/polygon and other annotation tools.

5. **Model training**  
   Select annotated data and click the training button. Supports parameter adjustment such as epoch, batch size, model type, etc.

6. **Quantize model / One-click export NE301 package**  
   After training is complete, quantize and export the NE301-compatible model package with one click.

7. **Deploy to NE301 device**  
   Deploy the model package to NE301 through the device management page or manual operation. For detailed guide, see [NE301 Quick Start](https://wiki.camthink.ai/docs/neoeyes-ne301-series/quick-start).
   
**Example topology:**
```
[NE301 Camera] <───> [Router/LAN] <───> [Computer/Server running AI Tool Stack]
```

## Roadmap
- Model hub: standalone quantization and downloadable model library
- Device management: NE101/NE301 connection, data debugging, OTA

## Ports & Environment
- Default ports (docker-compose):  
  - Backend / API: `8000`  
  - Frontend (react-scripts in container): `3000` (proxied/mapped, check logs)  
- Key environment variables (examples):  
  - `API_BASE_URL`: frontend API base (e.g., `http://localhost:8000`)  
  - `MQTT_BROKER_HOST`: MQTT broker host/IP (e.g., `localhost` or broker service name in compose)  
  - `MQTT_BROKER`, `MQTT_TOPIC`: NE301 MQTT settings
  - `DATASETS_ROOT`: backend dataset storage path  
  - Add more as needed in `.env` or compose env overrides.

## Dataset Formats (import/export)
- COCO: single JSON with `images/annotations/categories`.  
- YOLO: ZIP with `images/` + `labels/` (.txt).  
- Project ZIP: `images/` + `annotations/*.json` + optional `classes.json` (id/name/color).  
- Exports: YOLO dataset ZIP; full annotation ZIP (images + annotations + classes.json).

## Troubleshooting
- Build fails (frontend) missing assets: ensure all referenced CSS/TSX exist (`DatasetImportModal.css` etc.).  
- Port conflicts: adjust mapped ports in `docker-compose.yml`.  
- Slow annotation layer on fast navigation: caching added; still slow → reduce image size (JPEG/WebP) or check network/CPU.

## Contributing
- Issues & PRs welcome.  
- Style: follow repo defaults (eslint/prettier for frontend, black/flake8 if configured for backend).  
- Branching: fork & PR or feature branches; please keep changes scoped and include repro steps for bug fixes.

## License
- Add your license of choice (e.g., MIT/Apache-2.0). Add `LICENSE` file and reference it here.








