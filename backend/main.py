"""后端主程序入口"""
import os
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from backend.config import settings
from backend.models.database import init_db
from backend.api import routes
from backend.services.mqtt_service import mqtt_service
from backend.services.mqtt_broker import builtin_mqtt_broker
from backend.services.websocket_manager import websocket_manager

# 创建 FastAPI 应用
app = FastAPI(
    title="AI Tool Stack API",
    description="Provide various AI toolsets to accelerate AI edge deployment",
    version="1.0.0"
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由（必须在静态文件路由之前注册）
app.include_router(routes.router, prefix="/api", tags=["API"])

# 单独注册 WebSocket 路由（不使用 /api 前缀）
@app.websocket("/ws/projects/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str):
    """WebSocket 连接端点"""
    await websocket_manager.connect(websocket, project_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            # 可以在这里处理客户端消息
            # 例如：同步标注操作、实时协作等
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket, project_id)

# 静态文件配置（用于 Docker 部署时服务前端构建产物）
# 注意：必须在 API 路由之后注册，避免拦截 API 请求
FRONTEND_BUILD_DIR = Path(__file__).parent.parent / "frontend" / "build"
if FRONTEND_BUILD_DIR.exists():
    # 挂载静态文件目录
    app.mount("/static", StaticFiles(directory=str(FRONTEND_BUILD_DIR / "static")), name="static")
    
    # 处理前端路由（SPA 应用需要）
    # 排除 API、WebSocket 和 health 路径
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """服务前端静态文件或 index.html（用于 React Router）"""
        # 排除 API 和 WebSocket 路径
        if full_path.startswith("api") or full_path.startswith("ws") or full_path == "health":
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")
        
        file_path = FRONTEND_BUILD_DIR / full_path
        # 如果请求的是文件且存在，返回文件
        if file_path.is_file() and file_path.exists():
            return FileResponse(str(file_path))
        # 否则返回 index.html（用于前端路由）
        index_path = FRONTEND_BUILD_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Frontend not found")


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    print("[Server] Starting AI Tool Stack backend...")
    
    # 初始化数据库
    init_db()
    print("[Server] Database initialized")
    
    # 初始化 NE301 项目（自动下载如果不存在）
    try:
        from backend.utils.ne301_init import ensure_ne301_project
        ne301_path = ensure_ne301_project()
        # 更新环境变量，供后续代码使用
        os.environ["NE301_PROJECT_PATH"] = str(ne301_path)
        print(f"[Server] NE301 project initialized at: {ne301_path}")
    except Exception as e:
        print(f"[Server] Failed to initialize NE301 project: {e}")
        print("[Server] NE301 model compilation may not work. Continuing...")
    
    # 启动 MQTT 服务（如果启用）
    if settings.MQTT_ENABLED:
        # 如果使用内置 Broker，先启动内置 Broker
        if settings.MQTT_USE_BUILTIN_BROKER:
            try:
                builtin_mqtt_broker.start()
                print(f"[Server] Built-in MQTT Broker started on port {settings.MQTT_BUILTIN_PORT}")
            except Exception as e:
                print(f"[Server] Failed to start built-in MQTT Broker: {e}")
                print("[Server] Continuing without built-in broker...")
                print("[Server] You can set MQTT_USE_BUILTIN_BROKER=False to use external broker")
        
        # 启动 MQTT 客户端服务
        try:
            mqtt_service.start()
            print("[Server] MQTT service started")
        except Exception as e:
            print(f"[Server] Failed to start MQTT service: {e}")
            print("[Server] Continuing without MQTT service...")
    else:
        print("[Server] MQTT service is disabled")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理"""
    print("[Server] Shutting down...")
    mqtt_service.stop()
    print("[Server] MQTT service stopped")
    
    # 停止内置 Broker
    if settings.MQTT_ENABLED and settings.MQTT_USE_BUILTIN_BROKER:
        try:
            builtin_mqtt_broker.stop()
            print("[Server] Built-in MQTT Broker stopped")
        except Exception as e:
            print(f"[Server] Error stopping built-in broker: {e}")


@app.get("/api")
def api_info():
    """API 信息"""
    return {
        "name": "AI Tool Stack API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "mqtt_enabled": settings.MQTT_ENABLED,
        "mqtt_connected": mqtt_service.is_connected if settings.MQTT_ENABLED else False
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
