"""Backend main entry point"""
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

# Create FastAPI application
app = FastAPI(
    title="CamThink AI Tool Stack API",
    description="Provide various AI toolsets to accelerate AI edge deployment",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request logging middleware
@app.middleware("http")
async def log_requests(request, call_next):
    """Log all incoming requests"""
    print(f"[Request] {request.method} {request.url.path}")
    response = await call_next(request)
    print(f"[Response] {request.method} {request.url.path} -> {response.status_code}")
    return response

# Register API routes (must be registered before static file routes)
app.include_router(routes.router, prefix="/api", tags=["API"])

# Register WebSocket route separately (without /api prefix)
@app.websocket("/ws/projects/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str):
    """WebSocket connection endpoint"""
    await websocket_manager.connect(websocket, project_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            # Client messages can be handled here
            # For example: sync annotation operations, real-time collaboration, etc.
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket, project_id)

# Register health check endpoint BEFORE catch-all route
# FastAPI matches more specific routes first, so /health will match before /{full_path:path}
@app.get("/health")
def health_check():
    """Health check endpoint for Docker healthchecks"""
    return {
        "status": "healthy",
        "mqtt_enabled": settings.MQTT_ENABLED,
        "mqtt_connected": mqtt_service.is_connected if settings.MQTT_ENABLED else False
    }

# Static file configuration (for serving frontend build artifacts in Docker deployment)
# Note: Must be registered after API routes and health endpoint to avoid intercepting them
FRONTEND_BUILD_DIR = Path(__file__).parent.parent / "frontend" / "build"
if FRONTEND_BUILD_DIR.exists():
    # Mount static file directory
    app.mount("/static", StaticFiles(directory=str(FRONTEND_BUILD_DIR / "static")), name="static")
    
    # Handle frontend routing (required for SPA applications)
    # This catch-all route will only match if /health, /api, /ws don't match first
    # FastAPI matches more specific routes first, so /health should match before this
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve frontend static files or index.html (for React Router)"""
        from fastapi import HTTPException
        
        # Exclude API and WebSocket paths (health is handled by specific route above)
        if (full_path.startswith("api/") or 
            full_path.startswith("ws/") or
            full_path.startswith("api") or 
            full_path.startswith("ws")):
            raise HTTPException(status_code=404, detail="Not found")
        
        file_path = FRONTEND_BUILD_DIR / full_path
        # If the requested file exists, return it
        if file_path.is_file() and file_path.exists():
            return FileResponse(str(file_path))
        # Otherwise return index.html (for frontend routing)
        index_path = FRONTEND_BUILD_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        raise HTTPException(status_code=404, detail="Frontend not found")


@app.on_event("startup")
async def startup_event():
    """Initialize on application startup"""
    print("[Server] Starting CamThink AI Tool Stack backend...")
    
    # Initialize database
    init_db()
    print("[Server] Database initialized")
    
    # Initialize NE301 project (auto-download if not exists)
    try:
        from backend.utils.ne301_init import ensure_ne301_project
        ne301_path = ensure_ne301_project()
        # Update environment variable for subsequent code
        os.environ["NE301_PROJECT_PATH"] = str(ne301_path)
        print(f"[Server] NE301 project initialized at: {ne301_path}")
    except Exception as e:
        print(f"[Server] Failed to initialize NE301 project: {e}")
        print("[Server] NE301 model compilation may not work. Continuing...")
    
    # Start MQTT service (if enabled)
    if settings.MQTT_ENABLED:
        # If using built-in Broker, start it first
        if settings.MQTT_USE_BUILTIN_BROKER:
            try:
                builtin_mqtt_broker.start()
                print(f"[Server] Built-in MQTT Broker started on port {settings.MQTT_BUILTIN_PORT}")
            except Exception as e:
                print(f"[Server] Failed to start built-in MQTT Broker: {e}")
                print("[Server] Continuing without built-in broker...")
                print("[Server] You can set MQTT_USE_BUILTIN_BROKER=False to use external broker")
        
        # Start MQTT client service
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
    """Cleanup on application shutdown"""
    print("[Server] Shutting down...")
    mqtt_service.stop()
    print("[Server] MQTT service stopped")
    
    # Stop built-in Broker
    if settings.MQTT_ENABLED and settings.MQTT_USE_BUILTIN_BROKER:
        try:
            builtin_mqtt_broker.stop()
            print("[Server] Built-in MQTT Broker stopped")
        except Exception as e:
            print(f"[Server] Error stopping built-in broker: {e}")


@app.get("/api")
def api_info():
    """API information"""
    return {
        "name": "CamThink AI Tool Stack API",
        "version": "1.0.0",
        "status": "running"
    }


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
