"""Backend main entry point"""
import os
import time
import logging
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
from backend.utils import security_utils

# Configure logging level based on DEBUG setting
log_level = logging.DEBUG if settings.DEBUG else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Set specific logger levels
logging.getLogger("backend.services.mqtt_service").setLevel(log_level)
logging.getLogger("backend.api.routes").setLevel(log_level)

# Hide /docs, /redoc and /openapi.json unless running in DEBUG mode: the
# generated API map is an attacker's recon starting point.
debug_docs = "/docs" if settings.DEBUG else None
debug_redoc = "/redoc" if settings.DEBUG else None
debug_openapi = "/openapi.json" if settings.DEBUG else None

# Create FastAPI application
app = FastAPI(
    title="CamThink AI Tool Stack API",
    description="Provide various AI toolsets to accelerate AI edge deployment",
    version="1.0.0",
    docs_url=debug_docs,
    redoc_url=debug_redoc,
    openapi_url=debug_openapi,
)


# ========== API authentication + per-IP rate limiting (C-1, M-8) ==========

class SecurityMiddleware:
    """Enforces API key auth (when enabled) and a per-IP request rate limit
    on /api endpoints. /health, frontend static files and the key-verification
    endpoint stay reachable without a key."""

    def __init__(self, app):
        self.app = app
        self._request_times: dict = {}

    def _client_ip(self, scope) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _rate_limited(self, ip: str) -> bool:
        limit = settings.RATE_LIMIT_PER_MINUTE
        if limit <= 0:
            return False
        now = time.time()
        window = self._request_times.setdefault(ip, [])
        # Sliding one-minute window; prune old entries lazily
        while window and now - window[0] > 60.0:
            window.pop(0)
        if len(window) >= limit:
            return True
        window.append(now)
        if len(self._request_times) > 10000:
            # Bound memory: drop entries with no recent activity
            self._request_times = {
                k: v for k, v in self._request_times.items()
                if v and now - v[-1] <= 60.0
            }
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_api = path == "/api" or path.startswith("/api/")

        if is_api:
            from starlette.responses import JSONResponse

            # Rate-limit every /api request (including /api/auth/verify so it
            # cannot serve as an unthrottled key-guessing oracle)
            if self._rate_limited(self._client_ip(scope)):
                await JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests, please slow down"},
                )(scope, receive, send)
                return

            if settings.API_AUTH_ENABLED:
                # Exempt only the key verification endpoint (used by the UI
                # login gate) — everything else under /api needs a key.
                if path != "/api/auth/verify":
                    from starlette.requests import Request as _Req
                    fake_request = _Req(scope, receive=receive)
                    presented = security_utils.extract_presented_key(fake_request)
                    if not security_utils.get_configured_api_keys():
                        await JSONResponse(
                            status_code=503,
                            content={
                                "detail": "API authentication is enabled but no API_KEY is configured. "
                                          "Set API_KEY in the environment (openssl rand -hex 32) and restart.",
                                "code": "auth_misconfigured",
                            },
                        )(scope, receive, send)
                        return
                    if not security_utils.is_valid_api_key(presented):
                        await JSONResponse(
                            status_code=401,
                            content={
                                "detail": "Invalid or missing API key",
                                "code": "auth_required",
                            },
                        )(scope, receive, send)
                        return

        await self.app(scope, receive, send)


app.add_middleware(SecurityMiddleware)


# Add request logging middleware
from fastapi import Request

@app.middleware("http")
async def log_requests(request, call_next):
    """Log all incoming requests"""
    print(f"[Request] {request.method} {request.url.path}")
    response = await call_next(request)
    print(f"[Response] {request.method} {request.url.path} -> {response.status_code}")
    return response


# Configure CORS (outermost so preflight OPTIONS bypasses API auth)
# Cross-origin is only allowed for explicitly configured origins; the deployed
# UI is same-origin and needs no CORS at all.
_cors_origins = [o.strip() for o in (settings.CORS_ORIGINS or "").split(",") if o.strip()]
if not _cors_origins:
    # Development defaults (local React dev server)
    _cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,  # never "*": with credentials Starlette would reflect any Origin
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

# Add global exception handler to ensure all errors return JSON.
# Internal details (absolute paths, openssl stderr, ...) are logged but only
# echoed to the client while DEBUG is enabled.
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler to ensure all errors return JSON format"""
    logging.getLogger(__name__).error(
        f"Unhandled error on {request.method} {request.url.path}: {exc}", exc_info=exc
    )
    detail = str(exc) if settings.DEBUG else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={"detail": detail},
    )

# Register API routes (must be registered before static file routes)
app.include_router(routes.router, prefix="/api", tags=["API"])


# ========== WebSocket authentication (C-1, M-2) ==========

async def _authenticate_websocket(websocket: WebSocket) -> bool:
    """Validate API key and Origin for a WebSocket handshake.

    Must be called before websocket.accept(); on failure the socket is
    closed with an appropriate code.
    """
    if not security_utils.is_allowed_websocket_origin(websocket):
        await websocket.close(code=1008, reason="Cross-origin WebSocket connection rejected")
        return False

    if settings.API_AUTH_ENABLED:
        if not security_utils.get_configured_api_keys():
            await websocket.close(code=1011, reason="Server auth misconfigured")
            return False
        presented = security_utils.extract_presented_key(websocket)
        if not security_utils.is_valid_api_key(presented):
            await websocket.close(code=1008, reason="Invalid or missing API key")
            return False
    return True


# Register WebSocket route separately (without /api prefix)
@app.websocket("/ws/projects/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str):
    """WebSocket connection endpoint"""
    if not await _authenticate_websocket(websocket):
        return
    await websocket_manager.connect(websocket, project_id)

    try:
        while True:
            data = await websocket.receive_text()
            # Client messages can be handled here
            # For example: sync annotation operations, real-time collaboration, etc.
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket, project_id)

@app.websocket("/ws/devices")
async def device_websocket_endpoint(websocket: WebSocket):
    """WebSocket connection endpoint for device list updates"""
    if not await _authenticate_websocket(websocket):
        return
    await websocket_manager.connect_device_listener(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            # Client messages can be handled here if needed
    except WebSocketDisconnect:
        websocket_manager.disconnect_device_listener(websocket)

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

        # Path traversal guard: resolve and verify the requested file stays
        # inside the frontend build directory (H-9: %2e%2e%2f reads).
        build_resolved = FRONTEND_BUILD_DIR.resolve()
        file_path = (build_resolved / full_path).resolve()
        if not file_path.is_relative_to(build_resolved):
            raise HTTPException(status_code=403, detail="Access denied: Invalid path")

        # If the requested file exists, return it
        if file_path.is_file():
            return FileResponse(str(file_path))
        # Otherwise return index.html (for frontend routing)
        index_path = build_resolved / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        raise HTTPException(status_code=404, detail="Frontend not found")


@app.on_event("startup")
async def startup_event():
    """Initialize on application startup"""
    print("[Server] Starting CamThink AI Tool Stack backend...")

    if settings.API_AUTH_ENABLED:
        if security_utils.get_configured_api_keys():
            print("[Server] API authentication is ENABLED "
                  f"({len(security_utils.get_configured_api_keys())} key(s) configured)")
        else:
            print("[Server] WARNING: API_AUTH_ENABLED=true but API_KEY is empty - "
                  "all /api and /ws requests will be rejected until a key is configured")
    else:
        print("[Server] WARNING: API authentication is DISABLED - anyone who can reach "
              "port 8000 can use every endpoint. Set API_AUTH_ENABLED=true and API_KEY "
              "in production, and keep the port behind a firewall/reverse proxy.")

    if settings.DEBUG:
        print("[Server] WARNING: DEBUG=true - /docs, /openapi.json and verbose error "
              "responses are exposed. Disable DEBUG in production.")

    # Initialize database
    init_db()
    print("[Server] Database initialized")

    # Initialize NE301 project (auto-download and update if needed)
    try:
        from backend.utils.ne301_init import ensure_ne301_project
        from backend.utils.ne301_update import ensure_ne301_updated

        # Step 1: Ensure project exists
        ne301_path = ensure_ne301_project()

        # Step 2: Check for updates (if enabled)
        if settings.NE301_AUTO_UPDATE:
            print("[Server] Checking for NE301 updates...")
            ne301_path, update_result = ensure_ne301_updated(
                ne301_path=ne301_path,
                timeout=settings.NE301_UPDATE_TIMEOUT
            )

            if update_result.success:
                if update_result.action == "updated":
                    print(f"[Server] NE301 updated: {update_result.old_commit[:8]} -> {update_result.new_commit[:8]}")
                elif update_result.action == "already_latest":
                    print("[Server] NE301 is already up to date")
                elif update_result.action == "skipped":
                    print(f"[Server] NE301 update skipped: {update_result.message}")
            else:
                print(f"[Server] NE301 update failed: {update_result.error}")
                print("[Server] Continuing with existing version...")

        # Update environment variable for subsequent code
        os.environ["NE301_PROJECT_PATH"] = str(ne301_path)
        print(f"[Server] NE301 project ready at: {ne301_path}")

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
