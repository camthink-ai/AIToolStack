"""后端主程序入口"""
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from backend.config import settings
from backend.models.database import init_db
from backend.api import routes
from backend.services.mqtt_service import mqtt_service
from backend.services.websocket_manager import websocket_manager

# 创建 FastAPI 应用
app = FastAPI(
    title="NeoEyesTool API",
    description="图像标注工具与 IoT 集成系统 API",
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

# 注册 API 路由
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


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    print("[Server] Starting NeoEyesTool backend...")
    
    # 初始化数据库
    init_db()
    print("[Server] Database initialized")
    
    # 启动 MQTT 服务
    mqtt_service.start()
    print("[Server] MQTT service started")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理"""
    print("[Server] Shutting down...")
    mqtt_service.stop()
    print("[Server] MQTT service stopped")


@app.get("/")
def root():
    """根路径"""
    return {
        "name": "NeoEyesTool API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "mqtt_connected": mqtt_service.is_connected
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
